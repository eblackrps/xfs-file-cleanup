# Veeam v12 XFS Backup-Copy Cleanup — v0.4.3

This **Linux / Python 3** utility inventories and, when requested, removes **complete expired Veeam v12 backup-copy chains** from a local XFS repository. It was designed for backup-copy retention of **28 elapsed days**, including the cleanup of old files no longer listed in VBR. It does not require access to VBR or another server.

The utility **does not** adjust GFS retention, modify backup metadata, change immutable flags, remove Veeam `.veeam.N.lock` tracking files, or remove directories. It is a filesystem cleanup utility, **not** a Veeam-supported retention operation. Veeam recommends managing registered backups through its supported Delete from Disk/retention functions.

## Installation

Copy `veeam_xfs_cleanup.py` to a local directory **outside the XFS repository** (for example `/opt/veeam-xfs-cleanup/`). The script uses Python **3.8+** standard library modules. A scheduled purge also requires **root privileges**, `findmnt`, `fuser` (package `psmisc`), a mounted **XFS** filesystem, and a functioning `/run/lock` directory. The script is intended for Linux hosts; the immutable-flag ioctl is implemented for x86_64 Linux.

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

A payload chain begins at a full `.vbk`, with subsequent `.vib` files grouped to their applicable full by the recognized Veeam filename pattern. Only a chain for which **every member is older than the configured number of days**, based on both filename restore-point time and file modification time, may be considered. A newer full in the same directory is treated as a separate chain, but a retained metadata file or newer incremental can block removal of the older chain when a dependency is present or cannot be resolved.

The script reads `.vbm` XML Storage references and checks metadata in the workload folder, parent job folder, and elsewhere in the inventory. It never edits `.vbm`. Files that are orphaned, expired, or not recognized in VBR can still be considered from repository evidence alone; unverifiable dependencies remain flagged.

Decision statuses:

| Status | Meaning |
|---|---|
| `PURGE_CANDIDATE` | Eligible entire chain or metadata file; execution revalidates immediately before unlink |
| `RETAIN` | Needed by a recent restore point or metadata relationship |
| `IMMUTABLE` | One or more members has an immutable XFS flag; no override attempted |
| `REVIEW` | Invalid, shared, missing, unexpected, recently active, or insufficient evidence; nothing removed |
| `FAILED_OR_BLOCKED` | A proposed deletion was stopped or failed and remains on disk |

A `.veeam.N.lock` file is **an immutability tracking file**, not proof that a job is currently running. The script **leaves those files untouched**. It monitors them for changes, verifies file identities and XFS immutable flags, checks open backup file handles with `fuser`, and refuses temporary/incomplete files. New or changed files detected by the pre-deletion checks stop affected operations. A repository-scoped lock prevents overlapping runs of this utility even when different report directories are selected. Global directory timestamp checks detect new files and folders across backup-copy jobs during purge, while metadata-identity checks detect changed VBM files. Immediately after the per-workload fuser check, the script also revalidates **every remaining indexed VBK/VIB/VRB in every job** before starting each eligible group, including in-place changes that do not alter directory timestamps. The deletion journal's directory entry is also synced before the first unlink.

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

For safety, the script rechecks the identity (device, inode, size, modification and change times, link count, mode) of **every indexed payload file once per candidate chain** immediately before deleting that chain. This rejects detected in-place writes by other backup-copy jobs but makes large cleanup runs slower than v0.4.2. In an offline benchmark, 180,000 identity checks took about 1.7 seconds on the test container; runtime on the customer's repository can be much higher, particularly with numerous workloads or slow storage. Run unattended purge in a backup-copy idle window long enough for the full scan and cleanup; allow it to finish rather than interrupting mid-chain.

These repeated checks are not an exclusive writer lock. A Veeam writer could start immediately after a check, so stopped/guaranteed-idle backup-copy jobs remain a **mandatory prerequisite** for destructive use. No file-only script can prove there are no unrelated Veeam restore-point dependencies that are absent from local XML metadata.

## Safety and support boundaries

The script cannot verify VBR database registration from the repository alone, and it does not rewrite shared VBM metadata. It **intentionally reports and retains** an expired chain when a retained VBM still references that chain, when metadata is incomplete, or when a newer restore point might depend on it. This can leave files older than 28 days on disk rather than silently break retained backups. The `flagged_not_removed` report makes those exceptions visible.

Automated testing used synthetic Veeam v12-style XML and disposable Linux fixtures; actual XFS immutability behavior and the exact customer metadata were not independently reproduced by the engineering test host. The release owner reports manual testing completed. Fail-closed behavior and the mandatory backup-copy idle window remain part of the operating procedure.

## Release acceptance and operational safeguards

**Release:** 0.4.3 (customer delivery). The release owner reported completion of manual testing and requested final publication on October 13, 2026. The manual test environment, commands, and logs were not provided for independent verification; that acceptance is a user report, not a claim of vendor certification. The packaged program matches the independently reviewed v0.4.3-rc1 cleanup logic; only the CLI version string was updated. Our automated Linux test suite was rerun on the final package.

**Mandatory purge conditions:** select the correct *backup-copy-only* repository root, schedule a guaranteed backup-copy idle window for the entire run, retain the generated audit files, and investigate all rows of `flagged_not_removed_*.csv`. XFS immutability is never overridden. Metadata ambiguity or an in-use/protected chain may leave expired files behind intentionally. No filesystem-only script can guarantee exclusion of an independent Veeam writer, and this utility is not a substitute for Veeam-supported retention of registered backups.

See `RELEASE_NOTES.md` for delivery status and `CHANGELOG.md` for the engineering history.
