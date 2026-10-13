# Veeam XFS Backup-Copy Cleanup 0.4.3 — Release Notes

**Status:** Final customer delivery, October 13, 2026.

**Supported scope:** Local Linux x86_64, Python 3.8+, mounted XFS, Veeam v12 backup-copy repository. Does not connect to VBR, change immutability, or rewrite VBM files. Not an official Veeam product or supported replacement for VBR retention.

**Operator modes:**

- Menu: `sudo python3 veeam_xfs_cleanup.py` (dry scan or confirmed scan and purge)
- Preview: `python3 veeam_xfs_cleanup.py --repo '/mnt/YOUR_VOLUME/backups' --days 28 --dry-run`
- Unattended: `sudo python3 veeam_xfs_cleanup.py --repo '/mnt/YOUR_VOLUME/backups' --days 28 --report-dir /var/log/veeam-xfs-cleanup --purge`

**Protection and reporting:** Removes only fully expired eligible backup-copy chains; protects dependencies, ambiguous metadata, active/open files, and immutable files. A clean scan is performed before each purge. Reports include per-file and per-chain findings and `flagged_not_removed_*.csv`; purge operations are written to a durable journal. A blocked or uncertain expired chain can remain on disk by design.

**Required operations:** All backup-copy jobs must be stopped or guaranteed idle for the full purge window. Review nonzero exit codes, journals, and flagged-not-removed reports. Run only against the correct backup-copy-only repository path.

**Release verification:** 102/102 permanent offline regression tests plus adversarial/synthetic workflows passed on the release code path. The requestor reported manual testing completed and authorized finalization. Exact manual test procedure and platform logs were not provided for independent reproduction; do not interpret this note as third-party certification of all real Veeam/XFS cases.

**Changes from v0.4.3-rc1:** CLI version display and release documentation only; deletion logic is unchanged.
