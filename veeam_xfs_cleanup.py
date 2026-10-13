#!/usr/bin/env python3
"""Veeam v12/v13 Linux XFS backup-copy repository chain cleanup.

Interactive menu, explicit --dry-run, confirmed --scan-purge, and automatic
--purge for scheduled tasks. Every purge includes a new, verified scan.
No VBR access, no immutable flag changes, no VBM editing.
Python >= 3.8, Linux, stdlib only; findmnt/fuser required for purge.
"""
from __future__ import annotations

import argparse
import array
import contextlib
import csv
import datetime as dt
import fcntl
import hashlib
import json
import ntpath
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

BACKUP_EXTS = {".vbk", ".vib", ".vrb", ".vbm"}
PAYLOAD_EXTS = {".vbk", ".vib", ".vrb"}
OTHER_BACKUP_EXTS = {".vlb", ".vlm", ".vsm", ".vsb", ".vbi", ".vab", ".vasm", ".vacm", ".vpm", ".vom"}
# x86_64 Linux FS_IOC_GETFLAGS (linux/fs.h); data is native unsigned long.
FS_IOC_GETFLAGS = 0x80086601
FS_IMMUTABLE_FL = 0x00000010
MAX_VBM_BYTES = 64 * 1024 * 1024
FUSER_BATCH = 40
CONFIRM = "DELETE-EXPIRED-VEEAM-BACKUPS"
UTC = dt.timezone.utc
VEEAM_LOCK = re.compile(r"\.veeam\.\d+\.lock", re.I)

# V12 repositories can retain legacy per-VM names on a V13 backup server.
# The explicit D separator is common in modern per-machine backups; VMware
# identities may be short vm-N values, and Hyper-V IDs may be GUIDs. The
# legacy V12 form placed the timestamp directly after a hex VM identifier.
# These are *positive* filename patterns, not a claim to parse every backup
# format. Unrecognized or ambiguous metadata must stay protected.
DATE_COMPONENT = r"20\d{2}-\d{2}-\d{2}T\d{6}"
VM_SHORT_ID = r"vm-[0-9a-fA-F]{1,16}"
VM_GUID_ID = r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{8,12}"
VM_PATTERN_SEPARATED = re.compile(
    r"^(?P<workload>.+)\.(?P<vmid>(?:" + VM_SHORT_ID + "|" + VM_GUID_ID +
    r"))D(?P<created>" + DATE_COMPONENT + r")(?:_[0-9a-fA-F]{4,8})?$", re.I
)
VM_PATTERN_LEGACY = re.compile(
    r"^(?P<workload>.+)\.vm-(?P<vmid>[0-9a-fA-F]{4,16})(?P<created>" +
    DATE_COMPONENT + r")(?:_[0-9a-fA-F]{4,8})?$", re.I
)
VBM_PATTERN = re.compile(r"^(?P<workload>.+)_[a-fA-F0-9]{4,8}$")
VBM_PER_MACHINE = re.compile(
    r"^(?P<workload>.+)\.(?:" + VM_SHORT_ID + "|" + VM_GUID_ID +
    r")(?:_[0-9a-fA-F]{4,8})?$", re.I
)


def backup_filename_match(stem: str) -> Optional[re.Match]:
    """Return a validated V12/V13 filename match, with explicit D preferred."""
    return VM_PATTERN_SEPARATED.fullmatch(stem) or VM_PATTERN_LEGACY.fullmatch(stem)


@dataclass(frozen=True)
class FileInfo:
    rel: str
    kind: str
    size: int
    allocated: int
    mtime_ns: int
    ctime_ns: int
    dev: int
    ino: int
    nlink: int
    mode: int

    @property
    def parent(self) -> str:
        return str(Path(self.rel).parent)

    @property
    def name(self) -> str:
        return Path(self.rel).name

    def identity(self) -> Tuple[int, ...]:
        return (self.dev, self.ino, self.size, self.mtime_ns,
                self.ctime_ns, self.nlink, self.mode)


@dataclass
class Group:
    directory: str
    name: str
    files: List[str]
    reason: str = ""
    status: str = ""
    logical_bytes: int = 0
    allocated_bytes: int = 0
    oldest_mtime: str = ""
    newest_mtime: str = ""
    immutable_files: int = 0
    group_type: str = "CHAIN"
    chain_start: str = ""
    dependent_paths: List[str] = None
    guard_metadata: List[str] = None


class Refuse(Exception):
    """A refusal is deliberate; do not attempt a partial purge."""


class CriticalPurgeFailure(Exception):
    """Purge cannot continue; some unlinks may already have happened."""


def is_immutability_marker(name: str) -> bool:
    # Veeam uses .veeam.N.lock to store immutability periods. These files
    # persist independently of active writers; their presence is not a busy lock.
    return bool(VEEAM_LOCK.fullmatch(name))


def prohibited_transient(name: str) -> bool:
    lower = name.lower()
    return (Path(name).suffix.lower() in {".tmp", ".partial", ".part"} or
            lower.endswith(".vbm_tmp"))


def file_signature(name: str, st: os.stat_result) -> str:
    return f"{name}\t{st.st_ino}\t{st.st_size}\t{st.st_mtime_ns}\t{st.st_ctime_ns}"


def directory_signature(name: str, st: os.stat_result) -> str:
    # Track file creation/removal in every workload directory. The purge path
    # explicitly refreshes only timestamps changed by its own audited unlinks.
    return ("DIRECTORY\t" + json.dumps(name, ensure_ascii=True) +
            f"\t{st.st_dev}\t{st.st_ino}\t{st.st_mtime_ns}\t{st.st_ctime_ns}")


def acquire_repository_lock(root: Path) -> int:
    """Exclusive, repository-scoped lock, independent of the report directory.

    Purge requires root. Placing the lock in /run/lock prevents another
    invocation using a different report location from bypassing serialization.
    """
    base = Path("/run/lock")
    base_info = base.lstat()
    if not stat.S_ISDIR(base_info.st_mode) or base_info.st_uid != 0:
        raise Refuse("System lock directory /run/lock is not trusted")
    name = "veeam-xfs-cleanup-" + hashlib.sha256(os.fsencode(root)).hexdigest()[:32] + ".lock"
    fd = os.open(base / name, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or
                info.st_nlink != 1 or info.st_mode & 0o077):
            raise Refuse("Repository lock file has untrusted ownership or permissions")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refuse("Another purge process is operating on this repository")
        return fd
    except BaseException:
        os.close(fd)
        raise


def inherited_hazards(directory: str, hazards: Dict[str, Set[str]]) -> Set[str]:
    parts = Path(directory).parts if directory != "." else ()
    ancestry = ["."] + [str(Path(*parts[:i])) for i in range(1, len(parts) + 1)]
    return set().union(*(hazards.get(ancestor, set()) for ancestor in ancestry))


def validate_report_directory(path: Path) -> None:
    """Keep privileged reports away from symlink paths and unsafe owners."""
    probe = Path("/")
    for part in path.parts[1:]:
        probe /= part
        try:
            info = probe.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise Refuse("Report path has a symlink or non-directory component: " + str(probe))
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if info.st_uid != os.geteuid():
        raise Refuse("Report directory is owned by another user: " + str(path))
    # Never chmod an existing user-selected directory. As root, a typo
    # such as --report-dir / or /etc could otherwise change system access
    # controls. Require a purpose-created private report directory instead.
    if info.st_mode & 0o077:
        raise Refuse("Report directory must be private (chmod 700): " + str(path))


@contextlib.contextmanager
def secure_text_report(path: Path):
    """Atomically reserve report filename; create 0600, refuse symlinks."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    # A synced DELETE_INTENT is insufficient if the journal directory entry
    # never reaches stable storage. Persist the name before any unlink.
    try:
        folder_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY |
                            os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            os.fsync(folder_fd)
        finally:
            os.close(folder_fd)
    except BaseException:
        os.close(fd)
        raise
    with os.fdopen(fd, "w", encoding="utf-8", errors="backslashreplace", newline="") as output:
        yield output
        output.flush()
        os.fsync(output.fileno())


def csv_safe(value: str) -> str:
    """Do not let backup filenames become formulas in exported spreadsheets."""
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def audit_event(output, record: dict) -> None:
    try:
        output.write(json.dumps(record, ensure_ascii=True) + "\n")
        output.flush()
        os.fsync(output.fileno())
    except OSError as exc:
        raise CriticalPurgeFailure("Audit log write failed; purge halted; "
                                   "previous file removals may have occurred: " + str(exc)) from exc


def iso_ns(timestamp_ns: int) -> str:
    return dt.datetime.fromtimestamp(timestamp_ns / 1e9, tz=UTC).isoformat(timespec="seconds")


def valid_repo_path(path: Path) -> Path:
    absolute = Path(os.path.abspath(os.path.expanduser(str(path))))
    if len(absolute.parts) < 3 or absolute == Path("/") or not absolute.is_dir():
        raise Refuse("Repository must be an existing, explicitly specified directory (not /).")
    # Refuse symlinks in every path component, including mount aliases.
    test = Path("/")
    for part in absolute.parts[1:]:
        test = test / part
        if test.is_symlink():
            raise Refuse("Repository path contains a symlink: " + str(test))
    return absolute


def ensure_xfs(root: Path) -> str:
    if not shutil.which("findmnt"):
        raise Refuse("findmnt is missing: cannot prove the repository is on XFS.")
    proc = subprocess.run(
        ["findmnt", "-n", "-o", "FSTYPE", "--target", str(root)],
        capture_output=True, text=True, timeout=20, check=False,
    )
    if proc.returncode or proc.stdout.strip() != "xfs":
        raise Refuse("Execute requires a mounted XFS repository; detected: " +
                     (proc.stdout.strip() or proc.stderr.strip() or "unknown"))
    return "xfs"


def snapshot(root: Path) -> Tuple[Dict[str, FileInfo], Dict[str, Set[str]], Dict[str, Set[str]]]:
    """Scan *all* files, not only old ones, to protect mixed active chains.

    Return backup files, per-directory hazards, and auxiliary file signatures.
    Never follow symlinks, and reject crossing mounted filesystems.
    """
    files: Dict[str, FileInfo] = {}
    hazards: Dict[str, Set[str]] = defaultdict(set)
    auxiliary: Dict[str, Set[str]] = defaultdict(set)
    root_dev = root.stat().st_dev

    def walk_error(err: OSError) -> None:
        raise Refuse("Repository scan incomplete: " + str(err))

    for current, dirs, names in os.walk(root, topdown=True, followlinks=False, onerror=walk_error):
        folder = Path(current)
        directory = str(folder.relative_to(root))
        # Include empty folders in the inventory. A later backup-copy job can
        # populate one while an old chain elsewhere in this job is purging.
        auxiliary.setdefault(directory, set())
        keep_dirs = []
        for name in dirs:
            child = folder / name
            st = child.lstat()
            if stat.S_ISLNK(st.st_mode):
                hazards[directory].add("symlink-directory:" + name)
            elif st.st_dev != root_dev:
                hazards[directory].add("foreign-mount:" + name)
            elif not stat.S_ISDIR(st.st_mode):
                hazards[directory].add("non-directory:" + name)
            else:
                auxiliary[directory].add(directory_signature(name, st))
                keep_dirs.append(name)
        dirs[:] = keep_dirs
        for name in names:
            path = folder / name
            st = path.lstat()
            ext = path.suffix.lower()
            if stat.S_ISLNK(st.st_mode):
                hazards[directory].add("symlink-file:" + name)
                continue
            if st.st_dev != root_dev:
                hazards[directory].add("foreign-device:" + name)
                continue
            if not stat.S_ISREG(st.st_mode):
                hazards[directory].add("non-regular-file:" + name)
                continue
            if ext in BACKUP_EXTS:
                rel = str(path.relative_to(root))
                files[rel] = FileInfo(
                    rel=rel, kind=ext[1:], size=st.st_size, allocated=st.st_blocks * 512,
                    mtime_ns=st.st_mtime_ns, ctime_ns=st.st_ctime_ns, dev=st.st_dev,
                    ino=st.st_ino, nlink=st.st_nlink, mode=st.st_mode,
                )
            elif ext in OTHER_BACKUP_EXTS:
                hazards[directory].add("unsupported-backup-type:" + name)
            elif prohibited_transient(name):
                hazards[directory].add("temporary-file:" + name)
            elif is_immutability_marker(name):
                # Not a live job lock; track creation/removal/modification
                # across snapshots and between scan and each unlink instead.
                auxiliary[directory].add(file_signature(name, st))
            else:
                # Ancillary data stays untouched. Record name/metadata changes
                # for the between-scan consistency check.
                auxiliary[directory].add(file_signature(name, st))
    return files, hazards, auxiliary


def pattern_workload(file: FileInfo) -> Optional[str]:
    stem = Path(file.name).stem
    if file.kind in {"vbk", "vib", "vrb"}:
        match = backup_filename_match(stem)
    else:
        match = VBM_PATTERN.fullmatch(stem) or VBM_PER_MACHINE.fullmatch(stem)
    return match.group("workload").casefold() if match else None


def timestamp_from_name(file: FileInfo) -> Optional[int]:
    if file.kind not in {"vbk", "vib", "vrb"}:
        return None
    match = backup_filename_match(Path(file.name).stem)
    if not match:
        return None
    try:
        moment = dt.datetime.strptime(match.group("created"), "%Y-%m-%dT%H%M%S")
        return int(moment.replace(tzinfo=UTC).timestamp() * 1e9)
    except ValueError:
        return None


def metadata_refs(path: Path) -> Tuple[Set[str], Optional[str]]:
    """Get storage names referenced by VBM XML without interpreting VBR job settings.

    FilePath is a Veeam Storage element field. Windows paths are accepted;
    basename references are resolved against repository-wide candidates by
    build_groups, which blocks duplicates and unresolved relationships.
    """
    try:
        size = path.stat().st_size
        if size > MAX_VBM_BYTES:
            return set(), "VBM-too-large"
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            with os.fdopen(fd, "rb") as handle:
                data = handle.read(MAX_VBM_BYTES + 1)
        except BaseException:
            # fdopen takes ownership of fd, even if its read fails.
            raise
        # Also reject UTF-16/UTF-32 forms with interspersed NUL bytes.
        declaration_scan = data.upper().replace(b"\x00", b"")
        if b"<!DOCTYPE" in declaration_scan or b"<!ENTITY" in declaration_scan:
            return set(), "VBM-unsafe-XML-declarations"
        root = ET.fromstring(data)
        if root.tag.rsplit("}", 1)[-1].lower() != "backupmeta":
            return set(), "VBM-unexpected-root"
        result: Set[str] = set()
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1].lower() != "storage":
                continue
            values = [value for key, value in element.attrib.items()
                      if key.rsplit("}", 1)[-1].lower() in ("filepath", "filename")]
            values.extend((child.text or "").strip() for child in element
                          if child.tag.rsplit("}", 1)[-1].lower() in ("filepath", "filename"))
            if not any(values):
                return set(), "VBM-unrecognized-storage-schema"
            for value in values:
                if not value:
                    return set(), "VBM-empty-storage-filename"
                base = ntpath.basename(value.replace("/", "\\"))
                if Path(base).suffix.lower() not in PAYLOAD_EXTS:
                    return set(), "VBM-unsupported-storage-reference"
                result.add(base.casefold())
        return result, None
    except (OSError, ET.ParseError, ValueError, UnicodeError) as exc:
        return set(), "VBM-unreadable:" + type(exc).__name__


def get_immutable(root: Path, rel: str) -> bool:
    """Read the filesystem immutable bit; never change it."""
    path = root / rel
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        flags = array.array("l", [0])
        fcntl.ioctl(fd, FS_IOC_GETFLAGS, flags, True)
        return bool(flags[0] & FS_IMMUTABLE_FL)
    finally:
        os.close(fd)


def payload_parts(file: FileInfo) -> Optional[Tuple[str, str, int]]:
    """Return (workload, VM identifier, timestamp_ns) for known V12/V13 names."""
    if file.kind not in {"vbk", "vib", "vrb"}:
        return None
    match = backup_filename_match(Path(file.name).stem)
    if match is None:
        return None
    created = timestamp_from_name(file)
    if created is None:
        return None
    vmid = match.group("vmid").casefold()
    # Legacy names capture the identifier without the 'vm-' prefix; the
    # explicit-D profile includes it. Normalize both so a V12 full and a V13
    # incremental cannot be mistaken for unrelated chains after an upgrade.
    if vmid.startswith("vm-"):
        vmid = vmid[3:]
    return match.group("workload").casefold(), vmid, created


def build_groups(root: Path, files: Dict[str, FileInfo],
                 hazards: Dict[str, Set[str]], cutoff_ns: int) -> List[Group]:
    """Group by *full-backup boundaries*, not whole VM or directory.

    A later VBK starts an independent forward-incremental chain. VIB/VRB
    attach to the most recent earlier VBK with the same workload and VM ID.
    A VBM is a separate item: never remove shared metadata while any payload
    to which it refers remains. Metadata is never rewritten by this tool.
    Unknown names/metadata are held when a mixed-age directory is present.
    """
    folders: Dict[str, List[str]] = defaultdict(list)
    for rel, f in files.items():
        folders[f.parent].append(rel)
    all_groups: List[Group] = []
    global_vbm_info: Dict[str, Tuple[Set[str], Optional[str]]] = {}

    for directory, members in sorted(folders.items()):
        first_group_index = len(all_groups)
        members.sort()
        directory_hazards = inherited_hazards(directory, hazards)
        by_basename: Dict[str, List[str]] = defaultdict(list)
        dated: Dict[Tuple[str, str], List[Tuple[int, str]]] = defaultdict(list)
        unknown_payloads: List[str] = []
        vbms: List[str] = []
        for rel in members:
            file = files[rel]
            by_basename[file.name.casefold()].append(rel)
            if file.kind == "vbm":
                vbms.append(rel)
            elif (parts := payload_parts(file)) is not None:
                dated[(parts[0], parts[1])].append((parts[2], rel))
            else:
                unknown_payloads.append(rel)
        duplicate_names = [base for base, paths in by_basename.items() if len(paths) != 1]
        # An unrecognized payload could be required by any recognized chain;
        # refusing mixed-age ambiguity avoids deleting an unseen dependency.
        folder_mixed = (any(files[p].mtime_ns >= cutoff_ns for p in members) or
                        any((timestamp_from_name(files[p]) or 0) >= cutoff_ns for p in members))
        generic_ambiguity = bool(unknown_payloads and folder_mixed)
        generic_ambiguity |= bool(duplicate_names)
        vbm_info: Dict[str, Tuple[Set[str], Optional[str]]] = {}
        for rel in vbms:
            vbm_info[rel] = metadata_refs(root / rel)
            global_vbm_info[rel] = vbm_info[rel]
            refs, error = vbm_info[rel]
            known_workload = pattern_workload(files[rel])
            if not known_workload:
                stem = Path(files[rel].name).stem.casefold()
                if any(w == stem for w, _ in dated):
                    known_workload = stem
            # When XML cannot be mapped to an actual workload, mixed-age
            # directories cannot be safely partitioned by chain.
            if (error or not refs) and not known_workload and folder_mixed:
                generic_ambiguity = True
        key_to_groups: Dict[Tuple[str, str], List[Group]] = defaultdict(list)
        path_to_group: Dict[str, Group] = {}
        for key, timed in sorted(dated.items()):
            timed.sort(key=lambda x: (x[0], 0 if files[x[1]].kind == "vbk" else 1, x[1]))
            bins: List[Tuple[Optional[int], List[str]]] = []
            duplicate_full_stamps: Set[int] = set()
            seen_full_stamps: Set[int] = set()
            for stamp, rel in timed:
                if files[rel].kind == "vbk":
                    if stamp in seen_full_stamps:
                        duplicate_full_stamps.add(stamp)
                    seen_full_stamps.add(stamp)
                    bins.append((stamp, [rel]))
                else:
                    if not bins or bins[-1][0] is None:
                        if not bins:
                            bins.append((None, []))
                        bins[-1][1].append(rel)
                    else:
                        bins[-1][1].append(rel)
            for index, (base, paths) in enumerate(bins):
                kind = "CHAIN" if base is not None else "ORPHAN_INCREMENTS"
                if base is None:
                    label = f"{key[0]}.vm-{key[1]} orphan-increments"
                else:
                    label = f"{key[0]}.vm-{key[1]} base-{iso_ns(base)}"
                # Keep labels distinct for same-timestamp fulls.
                label += f" #{index + 1}"
                group = Group(directory=directory, name=label,
                              files=sorted(paths), group_type=kind,
                              chain_start=iso_ns(base) if base is not None else "",
                              dependent_paths=[])
                reasons: List[str] = []
                if base in duplicate_full_stamps:
                    reasons.append("ambiguous-same-timestamp-fulls")
                if kind == "ORPHAN_INCREMENTS":
                    reasons.append("no-visible-base-full")
                group.reason = "; ".join(reasons)
                key_to_groups[key].append(group)
                all_groups.append(group)
                for p in paths:
                    path_to_group[p] = group
        if unknown_payloads:
            unknown = Group(directory=directory, name="unrecognized-files",
                            files=sorted(unknown_payloads), group_type="UNCLASSIFIED",
                            dependent_paths=[], reason="unrecognized-backup-filenames")
            all_groups.append(unknown)
            for p in unknown_payloads:
                path_to_group[p] = unknown
        # Metadata files are managed separately from their payloads. An old
        # VBM can span two independent chains, so it must survive if either
        # chain survives. It may not be edited to remove expired references.
        meta_groups: Dict[str, Group] = {}
        for rel in vbms:
            metadata = Group(directory=directory, name="metadata:" + files[rel].name,
                             files=[rel], group_type="METADATA", dependent_paths=[])
            refs, error = vbm_info[rel]
            named_workload = pattern_workload(files[rel])
            if not named_workload:
                stem = Path(files[rel].name).stem.casefold()
                if any(workload == stem for workload, _ in dated):
                    named_workload = stem
            referenced = set()
            missing = []
            for refname in refs:
                matches = by_basename.get(refname, [])
                if len(matches) == 1 and matches[0] in path_to_group:
                    referenced.add(matches[0])
                elif not matches:
                    missing.append(refname)
            if named_workload:
                # VBM may omit or lag storage references for an active chain.
                # Always protect the metadata while any same-workload payload
                # remains, even if the XML names only older backup files.
                referenced.update(p for p in path_to_group
                                  if (payload_parts(files[p]) or (None,))[0] == named_workload)
            if not refs and not named_workload:
                # A shared, unrecognized VBM without refs cannot prove that
                # any chain is independent. Hold it in mixed directories.
                if folder_mixed:
                    metadata.reason = "unmapped-shared-vbm"
                else:
                    referenced = set(path_to_group)
            metadata.dependent_paths = sorted(referenced)
            notes = []
            if error:
                notes.append("unreadable-vbm:" + error)
            if missing:
                notes.append("vbm-references-missing-storage:" + str(len(missing)))
            if not refs:
                notes.append("vbm-has-no-readable-storage-references")
            metadata.reason = "; ".join([metadata.reason] + notes).strip("; ")
            meta_groups[rel] = metadata
            all_groups.append(metadata)
        # Classify dated payload groups first.
        local_groups = [g for g in all_groups[first_group_index:]
                        if g.group_type != "METADATA"]
        recently_unbased = {key[0] for key, subset in key_to_groups.items()
                            if any(g.group_type == "ORPHAN_INCREMENTS" and
                                   any(files[p].mtime_ns >= cutoff_ns or
                                       (timestamp_from_name(files[p]) or 0) >= cutoff_ns
                                       for p in g.files)
                                   for g in subset)}
        for group in local_groups:
            reasons = [x for x in group.reason.split("; ") if x]
            if directory_hazards:
                reasons.append("unsafe-entries:" + ",".join(sorted(directory_hazards)))
            if any(files[p].kind == "vrb" for p in group.files):
                reasons.append("unsupported-reverse-incremental-layout")
            if generic_ambiguity:
                reasons.append("mixed-age-unrecognized-files-or-unmapped-metadata" if not duplicate_names
                               else "duplicate-case-insensitive-filenames")
            if any(files[p].nlink != 1 for p in group.files):
                reasons.append("hardlinks-present")
            if group.group_type == "UNCLASSIFIED" and folder_mixed:
                reasons.append("unclassified-file-in-mixed-directory")
            if any(files[p].mtime_ns >= cutoff_ns for p in group.files):
                reasons.append("contains-recent-modified-file")
            if any((timestamp_from_name(files[p]) or 0) >= cutoff_ns for p in group.files):
                reasons.append("contains-recent-named-restore-point")
            key_sample = payload_parts(files[group.files[0]]) if group.files else None
            if (key_sample and group.group_type == "CHAIN" and
                    key_sample[0] in recently_unbased):
                reasons.append("recent-unbased-increments-same-workload")
            for meta_rel, meta in meta_groups.items():
                linked = bool(set(group.files) & set(meta.dependent_paths or []))
                if not linked:
                    continue
                _, error = vbm_info[meta_rel]
                refs, error = vbm_info[meta_rel]
                other_live = any(p not in group.files and
                                 (files[p].mtime_ns >= cutoff_ns or
                                  (timestamp_from_name(files[p]) or 0) >= cutoff_ns)
                                 for p in (meta.dependent_paths or []))
                # A retained/current VBM with no usable storage inventory or
                # references to files missing from this directory cannot
                # establish an expired chain's independence. Fail closed.
                missing_refs = any(ref not in by_basename for ref in refs)
                if error:
                    reasons.append("unreadable-associated-vbm:" + files[meta_rel].name)
                elif (not refs or missing_refs) and (
                        files[meta_rel].mtime_ns >= cutoff_ns or other_live):
                    reasons.append("incomplete-active-vbm-storage-map:" + files[meta_rel].name)
                elif files[meta_rel].mtime_ns >= cutoff_ns:
                    # A VBM is regularly updated by the backup copy job. If
                    # another self-contained newer VBK chain exists for this
                    # workload, the older full chain can be retired while the
                    # shared VBM is left intact. The later explicit-ref guard
                    # blocks purging if retaining it would break Storage refs.
                    # Without that newer full, recent VBM activity blocks us.
                    workload = key_sample[0] if key_sample else None
                    newer_independent_full = bool(
                        group.group_type == "CHAIN" and workload and
                        any(other is not group and other.group_type == "CHAIN" and
                            other.chain_start > group.chain_start and
                            any((files[p].mtime_ns >= cutoff_ns or
                                 (timestamp_from_name(files[p]) or 0) >= cutoff_ns)
                                for p in other.files)
                            for (work, _vmid), subset in key_to_groups.items()
                            if work == workload for other in subset))
                    if newer_independent_full:
                        reasons.append("recent-shared-vbm-preserved:" +
                                       files[meta_rel].name)
                    elif any(files[p].mtime_ns >= cutoff_ns for p in group.files):
                        reasons.append("recent-vbm-with-recent-chain:" + files[meta_rel].name)
                    else:
                        reasons.append("recent-associated-vbm:" + files[meta_rel].name)
                else:
                    other = set(meta.dependent_paths or []) - set(group.files)
                    if other:
                        reasons.append("shared-vbm-retained-not-edited:" + files[meta_rel].name)
            group.reason = "; ".join(dict.fromkeys(reasons))
            blockers = [r for r in reasons if r.startswith((
                "unsafe-entries:", "mixed-age-unrecognized", "duplicate-case", "hardlinks-present",
                "unclassified-file-in-mixed", "ambiguous-same", "recent-unbased",
                "unsupported-reverse-incremental-layout",
                "unreadable-associated", "incomplete-active-vbm", "recent-associated"))]
            contains_recent = any(r.startswith("contains-recent") for r in reasons)
            if blockers:
                group.status = "REVIEW"
            elif contains_recent:
                group.status = "RETAIN"
            else:
                group.status = "PURGE_CANDIDATE"
        # Classify metadata after chain decisions: keep if any referenced
        # data file is currently retained/immutable/ambiguous.
        for meta_rel, group in meta_groups.items():
            reasons = [x for x in group.reason.split("; ") if x]
            if directory_hazards:
                reasons.append("unsafe-entries:" + ",".join(sorted(directory_hazards)))
            if generic_ambiguity:
                reasons.append("unmapped-or-ambiguous-directory")
            if files[meta_rel].nlink != 1:
                reasons.append("hardlinks-present")
            if files[meta_rel].mtime_ns >= cutoff_ns:
                reasons.append("recent-metadata")
            if any(path_to_group[p].status != "PURGE_CANDIDATE"
                   for p in (group.dependent_paths or [])):
                reasons.append("required-by-retained-or-blocked-chain")
            if any(r.startswith(("unreadable-vbm", "unmapped-shared-vbm", "unsafe-entries",
                                 "unmapped-or-ambiguous", "hardlinks-present")) for r in reasons):
                group.status = "REVIEW"
            elif "recent-metadata" in reasons or "required-by-retained-or-blocked-chain" in reasons:
                group.status = "RETAIN"
            else:
                group.status = "PURGE_CANDIDATE"
            group.reason = "; ".join(dict.fromkeys(reasons))
        # Only check immutable attributes for old groups otherwise eligible.
        for group in local_groups + list(meta_groups.values()):
            group.logical_bytes = sum(files[p].size for p in group.files)
            group.allocated_bytes = sum(files[p].allocated for p in group.files)
            group.oldest_mtime = iso_ns(min(files[p].mtime_ns for p in group.files))
            group.newest_mtime = iso_ns(max(files[p].mtime_ns for p in group.files))
            if group.status == "PURGE_CANDIDATE":
                try:
                    group.immutable_files = sum(int(get_immutable(root, p)) for p in group.files)
                except OSError as exc:
                    group.status = "REVIEW"
                    group.reason += "; unable-to-check-XFS-flags:" + type(exc).__name__
                else:
                    if group.immutable_files:
                        group.status = "IMMUTABLE"
                        group.reason += "; immutable-files:" + str(group.immutable_files)
        # Re-evaluate metadata if another group turned IMMUTABLE/REVIEW due
        # to file attributes. We must not delete VBM while its payload remains.
        for group in meta_groups.values():
            if (group.status == "PURGE_CANDIDATE" and
                    any(path_to_group[p].status != "PURGE_CANDIDATE"
                        for p in (group.dependent_paths or []))):
                group.status = "RETAIN"
                group.reason += "; required-by-immutable-or-blocked-chain"
        # A retained VBM must not have *newly broken explicit Storage refs*.
        # In particular, an active shared VBM pointing to both old and new
        # VBKs is not safe to leave behind with its old VBK missing. Block
        # that deletion, rather than silently corrupt the retained metadata.
        # Re-run to a fixed point because blocking one chain can retain a VBM
        # previously considered removable, which may block another chain.
        for _ in range(len(local_groups) + len(meta_groups) + 1):
            changed = False
            for meta_rel, meta in meta_groups.items():
                if meta.status == "PURGE_CANDIDATE":
                    if any(path_to_group[p].status != "PURGE_CANDIDATE"
                           for p in (meta.dependent_paths or [])):
                        meta.status = "RETAIN"
                        meta.reason += "; required-by-nonpurge-chain"
                        changed = True
                if meta.status != "PURGE_CANDIDATE":
                    refnames = vbm_info[meta_rel][0]
                    for path in meta.dependent_paths or []:
                        payload = path_to_group[path]
                        if (payload.status == "PURGE_CANDIDATE" and
                                files[path].name.casefold() in refnames):
                            payload.status = "REVIEW"
                            payload.reason += "; retained-vbm-explicitly-references-chain:" + files[meta_rel].name
                            changed = True
            if not changed:
                break

    # A per-machine VBM usually lives next to its payloads, but a shared or
    # job-level VBM may be one directory higher (or elsewhere in the tree).
    # Resolve explicit Storage refs across the complete repository before
    # authorizing the deletion of either a VBM or any referenced backup file.
    group_for = {rel: group for group in all_groups for rel in group.files}
    global_names: Dict[str, List[str]] = defaultdict(list)
    global_workload: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    for rel, item in files.items():
        if "." + item.kind in PAYLOAD_EXTS:
            global_names[item.name.casefold()].append(rel)
            workload = pattern_workload(item)
            if workload:
                parts = Path(item.parent).parts
                global_workload[(parts[0] if parts else ".", workload)].append(rel)
    for meta_rel, (refs, _error) in global_vbm_info.items():
        meta = group_for[meta_rel]
        refs_resolved = set(meta.dependent_paths or [])
        for ref in refs:
            matches = global_names.get(ref, [])
            # The VBM's FilePath can name a different job folder even when an
            # identically named storage file is colocated with this VBM.
            # metadata_refs intentionally strips paths, so a local-name
            # preference is NOT proof of which file the VBM references.
            # Refuse ALL matching candidates unless the reference is unique.
            if len(matches) == 1:
                refs_resolved.add(matches[0])
            elif len(matches) > 1:
                meta.status = "REVIEW"
                meta.reason += "; ambiguous-cross-directory-storage-reference:" + ref
                refs_resolved.update(matches)
                for target in matches:
                    target_group = group_for[target]
                    if target_group.status == "PURGE_CANDIDATE":
                        target_group.status = "REVIEW"
                        target_group.reason += "; ambiguous-cross-directory-metadata-ref: " + meta_rel
        # Per-VM metadata can live in the job directory instead of directly
        # with VBK/VIB files. Same-workload files inside that job may depend
        # on the metadata even if an old VBM has a stale Storage list.
        parts = Path(files[meta_rel].parent).parts
        scope = parts[0] if parts else "."
        named_workload = pattern_workload(files[meta_rel])
        if named_workload:
            refs_resolved.update(global_workload.get((scope, named_workload), []))
        meta.dependent_paths = sorted(refs_resolved)
        # If a VBM has no readable storage references, and current backups
        # exist beneath it, we cannot certify the metadata is abandoned.
        if not refs and meta.status == "PURGE_CANDIDATE":
            path_prefix = meta.directory + os.sep if meta.directory != "." else ""
            any_recent_child = any(
                p != meta_rel and p.startswith(path_prefix) and
                group_for[p].group_type != "METADATA" and
                group_for[p].status != "PURGE_CANDIDATE"
                for p in group_for)
            if any_recent_child:
                meta.status = "REVIEW"
                meta.reason += "; unmapped-vbm-with-active-descendants"

    # An unreadable or incomplete job-level VBM might reference any workload
    # in that backup-copy job. Do not silently delete child backup chains just
    # because an unrecognized metadata filename supplied no usable refs.
    # A VBM with a recognizable, *matching* workload name can be scoped to
    # that workload; otherwise hold the entire job's expired chains.
    for meta_rel, (refs, error) in global_vbm_info.items():
        meta_file = files[meta_rel]
        unresolved = any(not global_names.get(ref) for ref in refs)
        if not error and refs and not unresolved:
            continue
        parts = Path(meta_file.parent).parts
        meta_job = parts[0] if parts else "."
        workload = pattern_workload(meta_file)
        matches_workload = bool(workload and global_workload.get((meta_job, workload)))
        job_payloads = [g for rel, g in group_for.items()
                        if g.group_type != "METADATA" and
                        (meta_job == "." or
                         (Path(files[rel].parent).parts or (".",))[0] == meta_job)]
        job_has_retained_payload = any(g.status != "PURGE_CANDIDATE" for g in job_payloads)
        # An incomplete job-level VBM must not be removed while any child
        # backup remains. A broken/stale Storage list is not evidence that
        # the VBM is detached from those retained backups.
        if meta.status == "PURGE_CANDIDATE" and job_has_retained_payload and not matches_workload:
            meta.status = "REVIEW"
            meta.reason += "; unresolved-metadata-with-retained-job-payload"
        # When the whole job is expired, allow a well-formed but empty VBM
        # to be removed with all its payloads. A valid, recognized per-VM VBM
        # can likewise be scoped to the VM even if another VM is current.
        if (not error and not unresolved and meta.status == "PURGE_CANDIDATE" and
                (matches_workload or not job_has_retained_payload)):
            continue
        for target, target_group in group_for.items():
            if target_group.group_type == "METADATA" or target_group.status != "PURGE_CANDIDATE":
                continue
            target_parts = Path(files[target].parent).parts
            target_job = target_parts[0] if target_parts else "."
            if meta_job != "." and target_job != meta_job:
                continue
            if matches_workload and pattern_workload(files[target]) != workload:
                continue
            target_group.status = "REVIEW"
            target_group.reason += "; unresolved-job-metadata:" + meta_rel

    for _ in range(len(all_groups) + 1):
        changed = False
        for meta_rel, (refs, _error) in global_vbm_info.items():
            meta = group_for[meta_rel]
            if (meta.status == "PURGE_CANDIDATE" and
                    any(group_for[path].status != "PURGE_CANDIDATE"
                        for path in (meta.dependent_paths or []))):
                meta.status = "RETAIN"
                meta.reason += "; referenced-payload-retained-outside-vbm-directory"
                changed = True
            if meta.status != "PURGE_CANDIDATE":
                for target in meta.dependent_paths or []:
                    candidate = group_for[target]
                    # With no refs and retained metadata, don't infer that an
                    # older self-contained chain is absent from its index.
                    if (candidate.status == "PURGE_CANDIDATE" and
                            (files[target].name.casefold() in refs or not refs)):
                        candidate.status = "REVIEW"
                        candidate.reason += "; retained-vbm-references-or-may-index-chain:" + meta_rel
                        changed = True
        if not changed:
            break

    # Guard the metadata that can affect a candidate: colocated/parent
    # metadata, any shared VBM with an explicit reference to its files, and
    # matching per-workload metadata. Index first so huge backup repositories
    # do not need a cartesian (chains x all metadata files) comparison.
    meta_by_dir: Dict[str, Set[str]] = defaultdict(set)
    meta_by_job: Dict[str, Set[str]] = defaultdict(set)
    meta_by_workload: Dict[str, Set[str]] = defaultdict(set)
    meta_by_referenced_name: Dict[str, Set[str]] = defaultdict(set)
    for meta_rel, (refs, _error) in global_vbm_info.items():
        meta_by_dir[files[meta_rel].parent].add(meta_rel)
        meta_parts = Path(files[meta_rel].parent).parts
        meta_by_job[meta_parts[0] if meta_parts else "."].add(meta_rel)
        known_workload = pattern_workload(files[meta_rel])
        if known_workload:
            meta_by_workload[known_workload].add(meta_rel)
        for ref in refs:
            meta_by_referenced_name[ref].add(meta_rel)
    for group in all_groups:
        if group.group_type == "METADATA":
            continue
        parts = Path(group.directory).parts if group.directory != "." else ()
        ancestry = ["."] + [str(Path(*parts[:i])) for i in range(1, len(parts) + 1)]
        related: Set[str] = set(meta_by_job.get(parts[0] if parts else ".", set()))
        for ancestor in ancestry:
            related.update(meta_by_dir.get(ancestor, set()))
        for rel in group.files:
            related.update(meta_by_referenced_name.get(files[rel].name.casefold(), set()))
            workload = pattern_workload(files[rel])
            if workload:
                related.update(meta_by_workload.get(workload, set()))
        group.guard_metadata = sorted(related)
    return all_groups


def checksum(inventory: Dict[str, FileInfo], hazards: Dict[str, Set[str]],
             auxiliary: Dict[str, Set[str]]) -> str:
    signature = {
        "files": [(f.rel, f.identity()) for f in sorted(inventory.values(), key=lambda x: x.rel)],
        "hazards": {k: sorted(v) for k, v in sorted(hazards.items())},
        "other": {k: sorted(v) for k, v in sorted(auxiliary.items())},
    }
    return hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()


def write_report(path: Path, groups: List[Group], files: Dict[str, FileInfo],
                 root: Path, cutoff_ns: int, token: str) -> None:
    """Write preflight findings; subsequent flagged report reflects actual purge."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    group_path = path / f"groups_{token}.csv"
    detail_path = path / f"files_{token}.csv"
    manifest_path = path / f"manifest_{token}.json"
    with secure_text_report(group_path) as out:
        writer = csv.writer(out)
        writer.writerow(["directory", "group", "type", "chain_start_utc", "decision", "file_count",
                         "logical_bytes", "allocated_bytes_estimate", "oldest_modified_utc",
                         "newest_modified_utc", "reason"])
        for group in groups:
            writer.writerow([csv_safe(group.directory), csv_safe(group.name), group.group_type, group.chain_start,
                             group.status, len(group.files), group.logical_bytes,
                             group.allocated_bytes, group.oldest_mtime, group.newest_mtime,
                             csv_safe(group.reason)])
    with secure_text_report(detail_path) as out:
        writer = csv.writer(out)
        writer.writerow(["file_path", "type", "size_bytes", "modified_utc", "age_days",
                         "chain_or_group", "decision", "reason"])
        for group in groups:
            for rel in group.files:
                f = files[rel]
                writer.writerow([str(root / rel), f.kind, f.size, iso_ns(f.mtime_ns),
                                 round((time.time_ns() - f.mtime_ns) / 86400e9, 1),
                                 csv_safe(group.name), group.status, csv_safe(group.reason)])
    manifest = {"root": str(root), "retention_cutoff_utc": iso_ns(cutoff_ns),
                "policy": "backup-copy chain boundary; see retention_cutoff_utc; no VBR, VBM edits or chattr",
                "groups": [asdict(g) for g in groups]}
    with secure_text_report(manifest_path) as out:
        json.dump(manifest, out, indent=2)
        out.write("\n")
    print("Reports: " + str(group_path) + ", " + str(detail_path) + ", " + str(manifest_path))


def write_flagged_report(path: Path, groups: List[Group], files: Dict[str, FileInfo],
                         root: Path, cutoff_ns: int, token: str,
                         outcomes: Optional[Dict[str, str]] = None,
                         executed: bool = False) -> Tuple[Path, int]:
    """Log ALL old backup files still present after a run, even mid-chain.

    In preview mode, proposed candidates are not counted as flagged/blocked:
    they are in groups_*.csv. In purge mode, failed candidate files appear.
    """
    flagged_path = path / f"flagged_not_removed_{token}.csv"
    outcomes = outcomes or {}
    count = 0
    with secure_text_report(flagged_path) as out:
        writer = csv.writer(out)
        writer.writerow(["file_path", "extension", "size_bytes", "modified_utc", "age_days",
                         "chain_or_group", "status", "reason"])
        for group in groups:
            for rel in group.files:
                f = files[rel]
                named_time = timestamp_from_name(f)
                is_old = (f.mtime_ns < cutoff_ns or
                          (named_time is not None and named_time < cutoff_ns))
                if not is_old:
                    continue
                if group.status == "PURGE_CANDIDATE" and not executed:
                    status = "DRY_RUN_CANDIDATE"
                    reason = "eligible-but-not-removed-in-dry-run"
                elif group.status == "PURGE_CANDIDATE":
                    reason = outcomes.get(rel, "")
                    if reason == "DELETED":
                        continue
                    # Re-check the file: a partial purge may leave a subset.
                    try:
                        present = os.path.lexists(root / rel)
                    except OSError:
                        present = True
                    if not present:
                        continue
                    reason = reason or "candidate-not-deleted"
                    status = "FAILED_OR_BLOCKED"
                else:
                    reason = group.reason
                    status = group.status
                writer.writerow([str(root / rel), f.kind, f.size, iso_ns(f.mtime_ns),
                                 round((time.time_ns() - f.mtime_ns) / 86400e9, 1),
                                 csv_safe(group.name), status, csv_safe(reason)])
                count += 1
    print(f"Flagged but not removed: {count} files | {flagged_path}")
    return flagged_path, count


def open_safe_dir(root_fd: int, directory: str) -> int:
    """Anchor deletions to a no-follow directory fd beneath the repo root."""
    fd = os.dup(root_fd)
    try:
        if directory != ".":
            for part in Path(directory).parts:
                if part in ("..", ".", ""):
                    raise Refuse("Invalid directory component: " + directory)
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                  dir_fd=fd)
                os.close(fd)
                fd = next_fd
        return fd
    except Exception:
        os.close(fd)
        raise


def check_identity(folder_fd: int, item: FileInfo) -> None:
    current = os.stat(item.name, dir_fd=folder_fd, follow_symlinks=False)
    observed = (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns,
                current.st_ctime_ns, current.st_nlink, current.st_mode)
    if observed != item.identity() or not stat.S_ISREG(current.st_mode):
        raise Refuse("File changed since scan: " + item.rel)


def check_immutable_at(folder_fd: int, item: FileInfo) -> bool:
    # Operate through the already-verified, symlink-proof directory fd.
    fd = os.open(item.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                 dir_fd=folder_fd)
    try:
        current = os.fstat(fd)
        observed = (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns,
                    current.st_ctime_ns, current.st_nlink, current.st_mode)
        if observed != item.identity() or not stat.S_ISREG(current.st_mode):
            raise Refuse("File changed before immutable check: " + item.rel)
        flags = array.array("l", [0])
        fcntl.ioctl(fd, FS_IOC_GETFLAGS, flags, True)
        return bool(flags[0] & FS_IMMUTABLE_FL)
    finally:
        os.close(fd)


def fuser_check(root: Path, paths: List[str]) -> None:
    if not shutil.which("fuser"):
        raise Refuse("fuser is missing: cannot check whether backup files are open.")
    for start in range(0, len(paths), FUSER_BATCH):
        subset = paths[start:start + FUSER_BATCH]
        args = ["fuser", "-s"] + [str(root / rel) for rel in subset]
        try:
            response = subprocess.run(args, capture_output=True, timeout=60, check=False)
        except subprocess.TimeoutExpired:
            raise Refuse("fuser timed out: unable to establish files are idle")
        if response.returncode == 0:
            raise Refuse("One or more backup files are in use (fuser reported open handles)")
        if response.stderr:
            raise Refuse("fuser emitted an error/warning; cannot prove files are idle: " +
                         response.stderr.decode(errors="replace")[:300])
        if response.returncode != 1:
            raise Refuse("fuser failed: exit " + str(response.returncode) + ": " +
                         response.stderr.decode(errors="replace")[:300])


def assert_no_new_backup_activity(root_fd: int, directory: str,
                                  files: Dict[str, FileInfo],
                                  outcomes: Dict[str, str],
                                  auxiliary: Dict[str, Set[str]],
                                  files_by_directory: Optional[Dict[str, Dict[str, FileInfo]]] = None) -> None:
    """Recheck for backup writers and new storage files immediately before unlink.

    This is a safety interlock, not a replacement for scheduling during a
    stopped backup-copy window. A writer could still start immediately after.
    """
    parts = Path(directory).parts if directory != "." else ()
    ancestors = ["."] + [str(Path(*parts[:i])) for i in range(1, len(parts) + 1)]
    # A job-level VBM can be created after inventory and refer to a child
    # backup. Guard backup filenames in *every ancestor*, not only the child.
    files_by_directory = files_by_directory or {directory: files}
    for ancestor in ancestors:
        fd = open_safe_dir(root_fd, ancestor)
        try:
            expected = files_by_directory.get(ancestor, {})
            expected_names = {item.name for item in expected.values()
                              if outcomes.get(item.rel) != "DELETED"}
            current_auxiliary: Set[str] = set()
            for name in os.listdir(fd):
                if prohibited_transient(name):
                    raise Refuse("Backup temporary file appeared: " + ancestor + "/" + name)
                if Path(name).suffix.lower() in BACKUP_EXTS:
                    if name not in expected_names:
                        raise Refuse("New backup file appeared since scan: " + ancestor + "/" + name)
                    continue
                st = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISDIR(st.st_mode):
                    current_auxiliary.add(directory_signature(name, st))
                    continue
                if not stat.S_ISREG(st.st_mode):
                    raise Refuse("Unexpected repository file type: " + ancestor + "/" + name)
                if Path(name).suffix.lower() in OTHER_BACKUP_EXTS:
                    raise Refuse("New/unsupported backup file present: " + ancestor + "/" + name)
                current_auxiliary.add(file_signature(name, st))
            if current_auxiliary != auxiliary.get(ancestor, set()):
                raise Refuse("Ancillary repository files changed since scan: " + ancestor)
            if ancestor == directory:
                # Retained metadata may change without any new file appearing.
                # Do not act on dependencies read from an out-of-date VBM.
                # VBR can still be writing an existing VIB/VBK without
                # creating a new file or updating the VBM yet. Check every
                # remaining backup file in this workload, not just metadata.
                for item in files.values():
                    if item.parent == directory and outcomes.get(item.rel) != "DELETED":
                        check_identity(fd, item)
        finally:
            os.close(fd)


def assert_repository_directories_unchanged(root_fd: int,
                                            auxiliary: Dict[str, Set[str]]) -> None:
    """Check directory timestamps repository-wide (metadata-only, no reads).

    New files anywhere change their parent directory mtime/ctime. Full
    directory sweeps also cover nested workloads in other backup-copy jobs.
    In-place VBM changes are verified separately by inode identity checks.
    """
    for directory in auxiliary:
        if directory == ".":
            continue
        path = Path(directory)
        parent = str(path.parent)
        fd = open_safe_dir(root_fd, parent)
        try:
            st = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
            if (not stat.S_ISDIR(st.st_mode) or
                    directory_signature(path.name, st) not in auxiliary.get(parent, set())):
                raise Refuse("Backup-copy directory changed since scan: " + directory)
        finally:
            os.close(fd)


def account_for_our_unlink(root_fd: int, directory: str,
                           auxiliary: Dict[str, Set[str]]) -> None:
    """Rebaseline only the directory updated by our audited unlink."""
    if directory == ".":
        return
    path = Path(directory)
    parent = str(path.parent)
    fd = open_safe_dir(root_fd, parent)
    try:
        st = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
        if not stat.S_ISDIR(st.st_mode):
            raise CriticalPurgeFailure("Directory replaced during deletion: " + directory)
        marker_prefix = "DIRECTORY\t" + json.dumps(path.name, ensure_ascii=True) + "\t"
        old_markers = {value for value in auxiliary.get(parent, set())
                       if value.startswith(marker_prefix)}
        if len(old_markers) != 1:
            raise CriticalPurgeFailure("Unable to maintain directory guard: " + directory)
        auxiliary[parent].difference_update(old_markers)
        auxiliary[parent].add(directory_signature(path.name, st))
    finally:
        os.close(fd)


def assert_all_vbm_unchanged(root_fd: int, files: Dict[str, FileInfo],
                             outcomes: Dict[str, str]) -> None:
    """Stop if any indexed VBM, including another job's, changed."""
    for item in files.values():
        if item.kind != "vbm" or outcomes.get(item.rel) == "DELETED":
            continue
        fd = open_safe_dir(root_fd, item.parent)
        try:
            check_identity(fd, item)
        finally:
            os.close(fd)


def assert_all_payload_files_unchanged(root_fd: int,
                                       files_by_directory: Dict[str, Dict[str, FileInfo]],
                                       outcomes: Dict[str, str]) -> None:
    """Verify backup payloads in *all* jobs still match the inventory.

    Directory timestamps only change when directory entries change, not
    when Veeam appends to an existing VBK/VIB. Scan all existing payload
    identities once per candidate group, after fuser, before the first unlink.
    This is a snapshot consistency gate, not an exclusive writer lock.
    """
    for directory, members in files_by_directory.items():
        to_check = [item for item in members.values()
                    if item.kind in {"vbk", "vib", "vrb"} and
                    outcomes.get(item.rel) != "DELETED"]
        if not to_check:
            continue
        fd = open_safe_dir(root_fd, directory)
        try:
            for item in to_check:
                check_identity(fd, item)
        finally:
            os.close(fd)


def assert_metadata_guards(root_fd: int, group: Group,
                           files: Dict[str, FileInfo],
                           outcomes: Dict[str, str]) -> None:
    """Verify all metadata in this job still matches its scan-time identity."""
    for rel in group.guard_metadata or []:
        if outcomes.get(rel) == "DELETED":
            continue
        item = files[rel]
        fd = open_safe_dir(root_fd, item.parent)
        try:
            check_identity(fd, item)
        finally:
            os.close(fd)


def purge(root: Path, groups: List[Group], files: Dict[str, FileInfo],
          report_path: Path, token: str,
          auxiliary: Optional[Dict[str, Set[str]]] = None) -> Tuple[int, int, Dict[str, str]]:
    """Delete independently eligible payload chains, then orphaned metadata."""
    log_path = report_path / f"deletions_{token}.jsonl"
    if auxiliary is None:
        # Direct callers still receive marker-change protection. Production
        # callers use the already verified second snapshot for this baseline.
        auxiliary = snapshot(root)[2]
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    failures, deleted = 0, 0
    outcomes: Dict[str, str] = {}
    by_directory: Dict[str, Dict[str, FileInfo]] = defaultdict(dict)
    for rel, item in files.items():
        by_directory[item.parent][rel] = item
    # Metadata is always last, after *all* payload-chain attempts.
    ordered = sorted(groups, key=lambda g: g.group_type == "METADATA")
    try:
        with secure_text_report(log_path) as audit:
            for group in ordered:
                if group.status != "PURGE_CANDIDATE":
                    continue
                paths = sorted(group.files,
                               key=lambda r: {"vib": 0, "vrb": 0, "vbk": 1, "vbm": 2}[files[r].kind])
                fd = None
                try:
                    fd = open_safe_dir(root_fd, group.directory)
                    # Never delete a VBM if any payload it references still
                    # exists, including a blocked or partially deleted chain.
                    if group.group_type == "METADATA":
                        for ref in group.dependent_paths or []:
                            try:
                                if os.path.lexists(root / ref):
                                    raise Refuse("VBM still references remaining payload: " + ref)
                            except OSError as exc:
                                raise Refuse("Unable to validate VBM reference: " + str(exc))
                    # Preflight every item in a chain before first unlink.
                    for rel in paths:
                        check_identity(fd, files[rel])
                        if check_immutable_at(fd, files[rel]):
                            raise Refuse("Immutable file: " + rel)
                    assert_repository_directories_unchanged(root_fd, auxiliary)
                    assert_metadata_guards(root_fd, group, files, outcomes)
                    # Backup-copy jobs may hold a younger restore point open
                    # while an old chain in the same workload is eligible.
                    # Treat any open workload backup file as active activity.
                    fuser_check(root, sorted(
                        rel for rel in by_directory[group.directory]
                        if outcomes.get(rel) != "DELETED"))
                    # A writer may create a sibling workload or VBM while
                    # fuser is running. Recheck the repository tree afterwards.
                    assert_repository_directories_unchanged(root_fd, auxiliary)
                    # Even a VBM in another job may acquire a new reference;
                    # stop before the first unlink if any metadata changed.
                    assert_all_vbm_unchanged(root_fd, files, outcomes)
                    # An existing sibling VBK/VIB can be modified in place
                    # without changing its parent directory timestamps.
                    assert_all_payload_files_unchanged(root_fd, by_directory, outcomes)
                    for rel in paths:
                        assert_no_new_backup_activity(root_fd, group.directory,
                                                      by_directory[group.directory],
                                                      outcomes, auxiliary, by_directory)
                        assert_repository_directories_unchanged(root_fd, auxiliary)
                        assert_metadata_guards(root_fd, group, files, outcomes)
                        check_identity(fd, files[rel])
                        if check_immutable_at(fd, files[rel]):
                            raise Refuse("Immutable flag changed: " + rel)
                        audit_event(audit, {"time_utc": dt.datetime.now(UTC).isoformat(),
                                            "result": "DELETE_INTENT", "path": str(root / rel),
                                            "group": group.name, "group_type": group.group_type})
                        os.unlink(files[rel].name, dir_fd=fd)
                        # Make the directory mutation durable before claiming
                        # completion in the audit journal.
                        os.fsync(fd)
                        # Our own unlink changes the parent directory's
                        # timestamps. Refresh ONLY this known mutation so a
                        # subsequent file can still detect other writers.
                        account_for_our_unlink(root_fd, group.directory, auxiliary)
                        outcomes[rel] = "DELETED"
                        event = {"time_utc": dt.datetime.now(UTC).isoformat(),
                                 "result": "DELETED", "path": str(root / rel),
                                 "logical_bytes": files[rel].size,
                                 "group": group.name, "group_type": group.group_type}
                        audit_event(audit, event)
                        deleted += 1
                    print(f"DELETED {len(paths)} files: {group.directory}/{group.name}")
                except (Refuse, OSError) as exc:
                    failures += 1
                    for rel in paths:
                        outcomes.setdefault(rel, str(exc))
                    event = {"time_utc": dt.datetime.now(UTC).isoformat(),
                             "result": "BLOCKED_OR_FAILED", "group": group.name,
                             "directory": group.directory, "error": str(exc)}
                    audit_event(audit, event)
                    print("BLOCKED: " + group.directory + "/" + group.name + ": " + str(exc),
                          file=sys.stderr)
                    # A failure could follow a partial unlink in this chain.
                    # Do not attempt unrelated chain deletes after an error.
                    break
                finally:
                    if fd is not None:
                        os.close(fd)
    finally:
        os.close(root_fd)
    print("Audit log: " + str(log_path))
    return deleted, failures, outcomes


def interactive_mode() -> str:
    if not sys.stdin.isatty():
        raise Refuse("Noninteractive launch requires --dry-run or --purge")
    print("\nVeeam v12 / v13 Backup Copy XFS Cleanup")
    print("  1) Dry scan (read-only, reports only)")
    print("  2) Scan and purge expired backup chains")
    print("  3) Exit")
    while True:
        selected = input("Select 1, 2, or 3: ").strip().lower()
        if selected == "1":
            return "dry"
        if selected == "2":
            return "confirmed"
        if selected in {"3", "q", "quit"}:
            return "quit"
        print("Please choose 1, 2, or 3.")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version="veeam-xfs-cleanup 0.5.0")
    parser.add_argument("--repo", type=Path,
                        help="Backup copy repository root (prompted if on a terminal)")
    parser.add_argument("--days", type=int, default=28,
                        help="Remove complete independent chains older than this many days (default: 28)")
    parser.add_argument("--report-dir", type=Path,
                        default=Path.home() / "veeam-xfs-cleanup-reports")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true", help="Preview and write reports only")
    modes.add_argument("--scan-purge", action="store_true", help="Scan and request single purge confirmation")
    modes.add_argument("--purge", action="store_true", help="Rescan and purge without prompts (for cron/jobs)")
    modes.add_argument("--execute", action="store_true", help="Legacy v0.1 alias, requires --confirm")
    parser.add_argument("--confirm", default="", help="Legacy --execute confirmation phrase")
    args = parser.parse_args(argv)
    purge_started = False
    repo_lock_fd = None
    try:
        if not 1 <= args.days <= 36500:
            raise Refuse("--days must be between 1 and 36500")
        mode = ("dry" if args.dry_run else "confirmed" if args.scan_purge else
                "auto" if args.purge else "legacy" if args.execute else interactive_mode())
        if mode == "quit":
            return 0
        if args.repo is None:
            if not sys.stdin.isatty():
                raise Refuse("--repo is required for unattended execution")
            supplied = input("Enter repository root path: ").strip()
            if not supplied:
                raise Refuse("No repository path supplied")
            args.repo = Path(supplied)
        root = valid_repo_path(args.repo)
        report_path = Path(os.path.abspath(os.path.expanduser(str(args.report_dir))))
        resolved_reports = report_path.resolve(strict=False)
        if os.path.commonpath([str(root), str(resolved_reports)]) == str(root):
            raise Refuse("Reports must be stored OUTSIDE the repository")
        will_purge = mode in ("confirmed", "auto", "legacy")
        if will_purge:
            if mode == "legacy" and args.confirm != CONFIRM:
                raise Refuse("Legacy --execute requires --confirm " + CONFIRM)
            if mode == "confirmed":
                if not sys.stdin.isatty():
                    raise Refuse("--scan-purge requires a terminal; use --purge for automation")
            if os.geteuid() != 0:
                raise Refuse("Purge requires root; run with sudo")
            ensure_xfs(root)
            repo_lock_fd = acquire_repository_lock(root)
        validate_report_directory(report_path)
        # Concurrent scheduled runs using the same report directory are refused.
        lock_fd = os.open(report_path / ".veeam_xfs_cleanup.lock",
                          os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Refuse("Another cleanup process is using this report directory")
            cutoff_ns = time.time_ns() - args.days * 86400 * 10**9
            # Microseconds prevent an immediate dry run and purge in the same
            # process/second from colliding with exclusive report filenames.
            token = dt.datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "_" + str(os.getpid())
            inventory, hazards, extra = snapshot(root)
            groups = build_groups(root, inventory, hazards, cutoff_ns)
            write_report(report_path, groups, inventory, root, cutoff_ns, token)
            totals = defaultdict(int)
            for group in groups:
                totals[group.status] += len(group.files)
            print(f"Repository: {root} | expired before UTC: {iso_ns(cutoff_ns)}")
            print("File counts: " + ", ".join(f"{k}={v}" for k, v in sorted(totals.items())))
            amount = sum(g.logical_bytes for g in groups if g.status == "PURGE_CANDIDATE")
            print(f"Candidate logical bytes (not physical freed space): {amount:,}")
            if not will_purge:
                write_flagged_report(report_path, groups, inventory, root, cutoff_ns, token)
                print("DRY SCAN COMPLETE: no files deleted")
                return 0
            if mode == "confirmed":
                print("\nWARNING: This run will permanently delete only eligible backup chains.")
                print("Review groups_*.csv and manifest_*.json before confirming.")
                if input("Type PURGE to proceed: ").strip() != "PURGE":
                    write_flagged_report(report_path, groups, inventory, root, cutoff_ns, token)
                    print("Cancelled, nothing deleted.")
                    return 0
            if any(reason.startswith(("symlink-", "foreign-"))
                   for reasons in hazards.values() for reason in reasons):
                write_flagged_report(report_path, groups, inventory, root, cutoff_ns, token,
                                     outcomes={p: "global-scan-hazard" for p in inventory},
                                     executed=True)
                raise Refuse("Symlink or nested mount found in repository. No deletion allowed")
            if any(reason.startswith("temporary-file:")
                   for reasons in hazards.values() for reason in reasons):
                write_flagged_report(report_path, groups, inventory, root, cutoff_ns, token,
                                     outcomes={p: "repository-has-active-or-transient-files" for p in inventory},
                                     executed=True)
                raise Refuse("Repository has incomplete temporary files; purge requires an idle repository")
            inventory2, hazards2, extra2 = snapshot(root)
            if checksum(inventory, hazards, extra) != checksum(inventory2, hazards2, extra2):
                write_flagged_report(report_path, groups, inventory, root, cutoff_ns, token,
                                     outcomes={p: "repository-changed-during-preflight" for p in inventory},
                                     executed=True)
                raise Refuse("Repository changed during scan/report. No files deleted; rerun")
            second_groups = build_groups(root, inventory2, hazards2, cutoff_ns)
            if [(x.files, x.status, x.dependent_paths) for x in groups] != [
                    (x.files, x.status, x.dependent_paths) for x in second_groups]:
                write_flagged_report(report_path, groups, inventory, root, cutoff_ns, token,
                                     outcomes={p: "eligibility-changed" for p in inventory},
                                     executed=True)
                raise Refuse("Cleanup eligibility changed during verification; rerun")
            ensure_xfs(root)
            purge_started = True
            try:
                deleted, failures, outcomes = purge(root, second_groups, inventory2, report_path, token,
                                                   auxiliary=extra2)
            except CriticalPurgeFailure:
                # The absence of a complete audit must not hide any remaining
                # candidates in the skipped-files report.
                write_flagged_report(report_path, second_groups, inventory2,
                                     root, cutoff_ns, token, executed=True)
                raise
            _, flagged = write_flagged_report(report_path, second_groups, inventory2,
                                             root, cutoff_ns, token, outcomes, executed=True)
            print(f"RESULT: deleted_files={deleted}, failed_groups={failures},"
                  f" flagged_old_files={flagged}")
            return 3 if failures else 4 if flagged else 0
        finally:
            os.close(lock_fd)
    except CriticalPurgeFailure as exc:
        print("PURGE HALTED (partial changes possible): " + str(exc), file=sys.stderr)
        return 3
    except (Refuse, OSError, subprocess.TimeoutExpired, EOFError, KeyboardInterrupt) as exc:
        if purge_started:
            print("PURGE INTERRUPTED (partial changes possible): " + str(exc), file=sys.stderr)
            return 3
        print("REFUSED: " + str(exc), file=sys.stderr)
        return 2
    finally:
        if repo_lock_fd is not None:
            os.close(repo_lock_fd)


if __name__ == "__main__":
    sys.exit(main())
