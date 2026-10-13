# Veeam v12/v13 XFS Backup-Copy Cleanup - v0.5.0

This **Linux / Python 3** utility inventories and, when requested, removes **complete expired Veeam v12 and v13 backup-copy chains** from a local XFS repository. It was designed for backup-copy retention of **28 elapsed days**, including the cleanup of old files no longer listed in VBR. It does not require access to VBR or another server.

The utility **does not** adjust GFS retention, modify backup metadata, change immutable flags, remove Veeam `.veeam.N.lock` tracking files, or remove directories. It is a filesystem cleanup utility, **not** a Veeam-supported retention operation. Veeam recommends managing registered backups through its supported Delete from Disk/retention functions.

## Latest release

**[v0.5.0 - GitHub Releases](https://github.com/eblackrps/xfs-file-cleanup/releases/tag/v0.5.0)**

- [Download v0.5.0 customer ZIP](https://github.com/eblackrps/xfs-file-cleanup/releases/download/v0.5.0/veeam_xfs_cleanup_v0.5.0_client_with_guide.zip) - Python script, operator guide, documentation and internal checksums
- [Download v0.5.0 operator PDF](https://github.com/eblackrps/xfs-file-cleanup/releases/download/v0.5.0/Veeam_XFS_Backup_Copy_Cleanup_v0.5.0_Operator_Guide.pdf)
- [SHA-256 release checksums](https://github.com/eblackrps/xfs-file-cleanup/releases/download/v0.5.0/veeam_xfs_cleanup_v0.5.0_SHA256SUMS.txt)

The release archive is built reproducibly from the tagged repository source. Run a dry scan and read the flagged-file report before considering deletion. A public release does **not** certify native Veeam v13 metadata or XFS production purge behavior.

## Operator documentation

- [v0.5.0 Operator Guide (PDF)](docs/Veeam_XFS_Backup_Copy_Cleanup_v0.5.0_Operator_Guide.pdf) - complete operating instructions for v12/v13.
- [Operator Guide (Markdown)](docs/OPERATOR_GUIDE.md) - readable source for the same instructions, including installation, retention, reporting, and troubleshooting.
- [Validation evidence](docs/VALIDATION.md) - the tested code and known platform-validation limits.

The downloadable customer package includes this v0.5.0 PDF and a SHA-256 manifest. Earlier v0.4.3 guides do not describe the new filename, VBM, or mount-safety behavior.

## Installation

Copy `veeam_xfs_cleanup.py` to a local directory **outside the XFS repository** (for example `/opt/veeam-xfs-cleanup/`). The script uses Python **3.8+** standard library modules. A scheduled purge also requires **root privileges**, `findmnt`, `fuser` (package `psmisc`), a mounted **XFS** filesystem, and a functioning `/run/lock` directory, and readable `/proc/self/mountinfo` for detecting nested mounts (including same-filesystem bind mounts). The script is intended for manually administered Linux hardened repositories; the immutable-flag ioctl is implemented for x86_64 Linux. Veeam Infrastructure/Software Appliance hardened repositories are **not supported deployment targets** for root-level scheduled deletion by this utility.

## Three operation modes

**Menu (easiest for a person)**

```bash
sudo python3 veeam_xfs_cleanup.py
```

Select **1 — Dry scan** (no deletion) or **2 — Scan and purge** (fresh scan, one `PURGE` confirmation). The script prompts for the repository root. Select **3** to exit.

**Dry scan without menu**

```bash
python3 veeam_xfs_cleanup.py --repo '/mnt/YOUR_VOLUME/backups' --days 28 --dry-run
```

**Scan and purge with a single confirmation**

```bash
sudo python3 veeam_xfs_cleanup.py --repo '/mnt/YOUR_VOLUME/backups' --days 28 --scan-purge
```

**Unattended scheduled purge**

```bash
sudo python3 veeam_xfs_cleanup.py \
  --repo '/mnt/YOUR_VOLUME/backups' \
  --days 28 \
  --report-dir '/var/log/veeam-xfs-cleanup' \
  --purge
```

`--purge` is **destructive and does not prompt**. Every invocation performs its own complete repository inventory and validation. It never replays an earlier CSV, and it refuses a non-XFS filesystem.

Supply the **backup-copy repository root**, typically the folder immediately above job directories such as `Example_Backup_Copy_Job`. Reports must be **outside** the repository. Report directories must be private (`0700`) and owned by the invoking user. The script creates a missing report directory privately; it **refuses existing world-readable report directories rather than changing their permissions**. Don't point it at a directory containing unrelated backup types.

## What is eligible for removal

For recognized v12 or v13 names, a payload chain begins at a full `.vbk`, with subsequent `.vib` files grouped to their applicable full by the recognized Veeam filename pattern. Only a chain for which **every member is older than the configured number of days**, based on both filename restore-point time and file modification time, may be considered. A newer full in the same directory is treated as a separate chain, but a retained metadata file or newer incremental can block removal of the older chain when a dependency is present or cannot be resolved.

The script reads understood `.vbm` XML Storage references and checks metadata in the workload folder, parent job folder, and elsewhere in the inventory. Modern per-machine VBM naming and legacy shared VBM layouts are both considered. Unrecognized or path-bearing alternate XML storage layouts, including extra backup-file references in unexpected Storage attributes, are flagged for review rather than assumed empty. Malformed machine-ID-looking filenames are also held rather than silently falling back to name-only chains. It never edits `.vbm`. Files that are orphaned, expired, or not recognized in VBR can still be considered from repository evidence alone; unverifiable dependencies remain flagged.

Decision statuses:

| Status | Meaning |
|---|---|
| `PURGE_CANDIDATE` | Eligible entire chain or metadata file; execution revalidates immediately before unlink |
| `RETAIN` | Needed by a recent restore point or metadata relationship |
| `IMMUTABLE` | One or more members has an immutable XFS flag; no override attempted |
| `REVIEW` | Invalid, shared, missing, unexpected, recently active, or insufficient evidence; nothing removed |
| `FAILED_OR_BLOCKED` | A proposed deletion was stopped or failed and remains on disk |

A `.veeam.N.lock` file is **an immutability tracking file**, not proof that a job is currently running. The script **leaves those files untouched**. It monitors them for changes, verifies file identities and XFS immutable flags, checks open backup file handles with `fuser`, and refuses temporary/incomplete files. New or changed files detected by the pre-deletion checks stop affected operations. Nested mounts inside the repository are refused; a bind mount may remain on the same device and cannot be detected safely by `st_dev` alone. A repository-scoped lock prevents overlapping runs of this utility even when different report directories are selected. Global directory timestamp checks detect new files and folders across backup-copy jobs during purge, while metadata-identity checks detect changed VBM files. Immediately after the per-workload fuser check, the script also revalidates **every remaining indexed VBK/VIB/VRB in every job** before starting each eligible group, including in-place changes that do not alter directory timestamps. The deletion journal's directory entry is also synced before the first unlink.

**Operational restriction:** Perform purge only while the backup-copy jobs are stopped or are guaranteed not to start. Filesystem checks cannot provide an exclusive lock against the Veeam Data Mover, and an active VBR job could start between two checks. Scheduled purge must run in a backup-copy idle window. This restriction is part of the operating procedure, not a separate customer test.

## Reporting

Reports default to `~/veeam-xfs-cleanup-reports` (usually `/root/veeam-xfs-cleanup-reports` under `sudo`). Use `--report-dir` for scheduled jobs. Reports are restricted to the invoking user (directory 0700, files 0600).

| Output | Purpose |
|---|---|
| `groups_*.csv` | Each chain's decision, timestamps, file count, sizes and reason |
| `files_*.csv` | Full inventory with per-file decision |
| `flagged_not_removed_*.csv` | Old files left in place, including candidates in a dry run, immutable chains, uncertain metadata and deletion errors |
| `manifest_*.json` | Informational snapshot of decisions (never used as an input to purge) |
| `deletions_*.jsonl` | Purge-only append-style journal with durable `DELETE_INTENT`, `DELETED`, and error events |

The program syncs the journal directory entry, writes deletion intent to disk before unlinking a file, and records the completed deletion after the filesystem directory is synced. A host crash can leave an intent without a completion record; inspect the journal in that case. Once a purge encounters a deletion error it halts further deletions and reports what remains.

## Exit codes

- **0:** Dry scan completed, or purge finished with nothing old skipped or failed.
- **2:** Refused **before** starting deletions (invalid configuration, concurrent purge, changed inventory, busy/incomplete repository or non-XFS target).
- **3:** A purge was interrupted or failed; **some files may already have been removed**, so inspect the deletion journal.
- **4:** Purge completed without an execution failure but some older files remain flagged/protected.

Monitor any nonzero exit code in scheduled jobs. A dry scan can return 0 even with candidates; inspect the generated report.

## Example cron entry

Install the script under `/opt/veeam-xfs-cleanup/` and create a protected log directory:

```bash
install -d -m 0700 /var/log/veeam-xfs-cleanup
```

Example root crontab (change the path **and** choose a time when backup-copy jobs are idle):

```cron
15 3 * * * /usr/bin/python3 /opt/veeam-xfs-cleanup/veeam_xfs_cleanup.py --repo '/mnt/YOUR_VOLUME/backups' --days 28 --report-dir /var/log/veeam-xfs-cleanup --purge >> /var/log/veeam-xfs-cleanup/cron.log 2>&1
```

## Full-repository consistency cost

For safety, the script rechecks the identity (device, inode, size, modification and change times, link count, mode) of **every indexed payload file once per candidate chain** immediately before deleting that chain. This rejects detected in-place writes by other backup-copy jobs but makes large cleanup runs more slowly than implementations without repository-wide rechecks. In an offline benchmark, 180,000 identity checks took about 1.7 seconds on the test container; runtime on the customer's repository can be much higher, particularly with numerous workloads or slow storage. Run unattended purge in a backup-copy idle window long enough for the full scan and cleanup; allow it to finish rather than interrupting mid-chain.

These repeated checks are not an exclusive writer lock. A Veeam writer could start immediately after a check, so stopped/guaranteed-idle backup-copy jobs remain a **mandatory prerequisite** for destructive use. No file-only script can prove there are no unrelated Veeam restore-point dependencies that are absent from local XML metadata.

## Safety and support boundaries

The script cannot verify VBR database registration from the repository alone, and it does not rewrite shared VBM metadata. It **intentionally reports and retains** an expired chain when a retained VBM still references that chain, when metadata is incomplete, or when a newer restore point might depend on it. This can leave files older than 28 days on disk rather than silently break retained backups. The `flagged_not_removed` report makes those exceptions visible.

Automated testing uses synthetic v12/v13-style metadata and disposable Linux fixtures. It does not establish that every Veeam v13 build emits these exact names or XML structures. The v0.4.3 release owner reported manual testing on the v12 workflow; those results do not constitute a v13 integration test. XFS and v13 native metadata integration remain unverified on the engineering host. Unknown data is held and reported rather than deleted.

## Veeam v12 and v13 compatibility

The same CLI scans both versions. **No version flag is necessary**: the parser accepts positive per-machine naming patterns found in older v12 backups and modern D-separated Veeam names with short `vm-N` or GUID-like machine identifiers, and the documented name-only `WorkloadDYYYY-MM-DDTHHMMSS.vbk` form. If a machine ID is not encoded in the name, the parser isolates that workload profile and conservatively protects possible cross-format dependencies. A repository upgraded from v12 can still contain old backup file formats; changing the backup server version does not automatically rename existing chains.

New v13 backup-copy jobs use per-machine backups with separate VBM metadata files. Older jobs can retain legacy layouts. This tool can evaluate known layout patterns together, including VBM references to files in another job directory. Unsupported storage extensions, unknown XML Storage fields, reverse-incremental chains, malformed XML, or uncertain dependencies are **not** automatically deleted.

This is not a format converter or VBR metadata repair tool. It does not adjust GFS or immutability. File timestamps and chain membership remain subject to the same 28-day default and fail-closed behavior in both versions.

Official product descriptions:
- https://helpcenter.veeam.com/docs/vbr/userguide/per_vm_backup_files.html?ver=13
- https://helpcenter.veeam.com/docs/vbr/userguide/hardened_repository_immutability.html?ver=13

## v0.5.0 release validation and operational safeguards

The release was manually reviewed and approved for **public GitHub publication** on October 13, 2026. The 146-test offline regression and destructive-path suite is retained in the engineering validation archive, while focused safety regressions are included under `tests/` in this GitHub repository. They use synthetic v12/v13 backup layouts and Linux filesystem fixtures. A real v13 repository sample and an isolated mounted hardened-XFS deletion test have **not** been provided or independently verified. The earlier manual v12 test applies to v0.4.3 and is not a v13 integration test. Public source availability is not Veeam vendor certification or proof that unattended destructive cleanup is safe on an untested v13 repository.

**Mandatory purge conditions:** use the backup-copy-only repository root on a manually configured Linux XFS hardened repository, guarantee backup-copy jobs remain idle for the entire run, keep audit reports outside the repository, and investigate all rows of `flagged_not_removed_*.csv`. The script never overrides immutable flags or rewrites VBMs. If the metadata cannot establish a safe chain boundary, it leaves the old files on disk.

See `RELEASE_NOTES.md`, `CHANGELOG.md` and `docs/VALIDATION.md` for scope, release history and exact offline checks.
