# Veeam XFS Backup-Copy Cleanup v0.5.0 - Development Candidate

**Scope:** one Linux / Python 3 cleanup utility for known Veeam Backup & Replication v12 and v13 backup-copy filesystem layouts on manually configured XFS hardened repositories.

**Preserved operation:** menu-driven dry run, interactive scan and purge, unattended `--purge`, 28-day default, immutable-file protection, VBM guardrails, repository change detection, audit logging, and flagged-not-removed reports.

**New compatibility:** accepts legacy direct-date VM names and modern D-separated short-ID/GUID machine names; recognizes additional per-machine VBM filenames; reconciles machine identities across v12/v13 naming so a new incremental cannot strand its prior full. Unexpected metadata schema elements fail closed.

**Release boundary:** tests use artificial backup files and representative XML layouts, not vendor-supplied real v13 VBM data. The previous customer's manual v12 test applies to v0.4.3 only. This version must not be described as production-certified for new v13 environments without platform and metadata acceptance evidence. Veeam software/infrastructure appliance root-level installs are not supported.

**Destructive-use rule:** ensure no backup-copy jobs can run during the entire purge. Veeam immutable flags are never cleared; uncertain chains stay on disk and are reported.

**Source and history:** https://github.com/eblackrps/xfs-file-cleanup
