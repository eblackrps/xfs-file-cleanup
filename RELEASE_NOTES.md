# Veeam XFS Backup-Copy Cleanup v0.5.0 - GitHub Release

**Scope:** one Linux / Python 3 cleanup utility for known Veeam Backup & Replication v12 and v13 backup-copy filesystem layouts on manually configured XFS hardened repositories.

**Preserved operation:** menu-driven dry run, interactive scan and purge, unattended `--purge`, 28-day default, immutable-file protection, VBM guardrails, repository change detection, audit logging, and flagged-not-removed reports.

**New compatibility:** accepts legacy direct-date VM names and modern D-separated short-ID/GUID machine names; recognizes additional per-machine VBM filenames; reconciles machine identities across v12/v13 naming so a new incremental cannot strand its prior full. Unexpected metadata schema elements fail closed.

**Publication approval:** the release owner manually reviewed v0.5.0 and approved public GitHub publication on October 13, 2026. The released code is identical to the reviewed rc3 Python source. Tests use artificial backup files and representative XML layouts, not vendor-supplied real v13 VBM data. The prior manual v12 test applies to v0.4.3 only; v13 production purge remains unverified on an actual hardened XFS repository. Veeam software/infrastructure appliance root-level installs are not supported.

**Destructive-use rule:** ensure no backup-copy jobs can run during the entire purge. Veeam immutable flags are never cleared; uncertain chains stay on disk and are reported.

**Source and history:** https://github.com/eblackrps/xfs-file-cleanup

## v0.5.0-rc2 independent debug additions

- Recognizes Veeam's documented machine-name-only `WorkloadD...` VBK/VIB filenames without losing protection for later increments.
- Rejects unknown XML references in alternative metadata storage layouts while preserving known Storage/FilePath fields.
- Refuses nested same-filesystem bind mounts; the Linux `/proc/self/mountinfo` requirement is documented.
- The deletion engine, menu, unattended `--purge`, immutable-bit handling, and reporting remain the same.
- This remains a candidate: no authenticated Veeam v13 metadata or mounted XFS integration proof is available.

## v0.5.0-rc3 final-debug changes

The independent rc2 review reproduced an ignored alternative VBM Storage attribute reference and a malformed machine-ID filename being accepted as name-only data. Both could make an old file eligible without sufficient dependency evidence. These inputs now fail closed. Full and eight-character abbreviated GUID-style identifiers remain supported; inconsistent intermediate lengths are rejected. No change was made to the 28-day default, interactive menu, unattended mode, XFS immutability handling, or audit journal.

The rc3 code has new permanent tests for the failure cases and randomized v12/v13 incremental dependency checks. All destruction simulation tests use disposable Linux data with mocked XFS prerequisites. The real v13 VBM/XFS acceptance remains outstanding; do not deploy unattended destructive v13 cleanup solely on the basis of these synthetic tests.
