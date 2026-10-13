# Changelog

## 0.5.0 - Dual-version development candidate (2026-10-13)

- Preserve the v0.4.3 Linux/XFS purge safeguards, interactive menu, unattended mode, reports, audit journal, and 28-day default.
- Support recognized v12 legacy VM-ID filenames alongside newer D-separated per-machine backup files, including short VM IDs and GUID-form identities used by Veeam backups.
- Normalize machine IDs across legacy and modern names so an upgraded repository does not split a full and dependent incremental into unrelated chains.
- Recognize per-machine VBM filenames and fail closed on unrecognized Storage XML elements or unsupported references.
- Add mixed-version, upgraded-chain, VBM, GUID, malformed-format, CLI dry scan, and synthetic purge regressions.
- Confirm that Software/Infrastructure Appliance deployments are outside the supported automated-root-purge scope.
- Native Veeam v13 metadata and mounted hardened-XFS production deletion are not yet independently proven; these remain validation limits, not claims of certification.

## 0.4.3 — Final customer release (2026-10-13)

- Promoted the v0.4.3-rc1 candidate following release-owner-reported manual testing and the completed offline validation.
- Updated only the CLI version string, documentation, and delivery metadata; no cleanup, retention, chain classification, immutability, or purge logic changed.
- Release-owner manual test details and logs were not supplied for independent verification. The Linux/XFS operating safeguards described in the README remain mandatory.

## 0.4.3-rc1 — Final rubber-duck recheck

- Fixed a confirmed stale-inventory deletion gap: appending to an existing VBK/VIB in a different workload or job does **not** change its parent directory's timestamps, so v0.4.2 could proceed with an expired-chain purge after an in-place update.
- Now verify all remaining indexed payload file identities repository-wide after `fuser` and before the first unlink of each candidate group. Existing VBM/global-directory checks remain unchanged.
- Added regression tests for in-place writes in sibling workloads and other backup-copy jobs and a happy-path regression.
- Documented the O(number of candidate groups × number of payload files) pre-unlink safety verification cost and the continuing requirement to run only during Veeam backup-copy idle windows.
- Replaced v0.4.2-rc2; no change to CLI flags, 28-day policy, metadata/no-chattr policy, or CSV output format.

## v0.4.2-rc2 — Full third-pass validation and debug

- Blocks deletion of an old chain beneath an unreadable or incomplete job-level
  VBM, even when metadata is located in an ancestor folder.
- Refuses to infer that same-named backup files in different jobs are equivalent.
  Ambiguous cross-job Storage references now flag every possible candidate.
- Prevents removing an old job-level VBM with unresolved Storage references
  while newer child backups may still depend on its metadata.
- Preserves eligible complete expired chains and independent workload handling.
- Makes report run identifiers microsecond-unique to support immediate
  dry-run followed by purge without CSV filename collisions.
- Confirms the shipped Python file has normal read permissions for customers.
- Adds 13 permanent regression tests (99 total), 1,000 randomized synthetic
  dependency scenarios, complete CLI dry/purge lifecycle, a real `fuser`
  open-file refusal test, and a separate non-root test pass.
- No actual mounted XFS/Veeam v12 customer test was available; release remains
  a tested candidate, not certified production-clean on the target environment.

## v0.4.1-rubberduck — Third adversarial developer review

- Detects new or replaced workload directories, including sibling workloads and nested directories, during cleanup.
- Detects new backup files in other workload/job directories using directory metadata, without repeated full tree scans.
- Detects changed VBMs in sibling workload directories and cross-job metadata before first unlink.
- Refuses newly introduced unsupported backup extensions such as `.vlb` during an ongoing purge.
- Keeps an updated scan-time directory baseline only for the filesystem changes performed by its own audited deletes.
- Syncs the audit/report directory entry before deletion starts to close a journal-name durability gap.
- Adds seven permanent adversarial tests; complete suite now has 86 tests, plus 300 randomized dependency scenarios.
- Earlier all-folder rescanning was replaced with metadata-only checks after it proved too expensive.

## v0.4.0-rc1 — Independent second-pass remediation

- Stopped treating persistent `.veeam.N.lock` immutability markers as active-job locks; now detects their modifications and added/removed markers without deleting them.
- Conservatively handles active VBM XML that is empty, incomplete, or references missing storage.
- Parses Storage `FilePath` values as both XML attributes and child elements.
- Resolves VBM dependencies across workload directories and parent job directories; prevents removal of retained metadata or a referenced old chain.
- Detects current child backup sets when old parent metadata has insufficient information.
- Added a repository-scoped Linux `flock` to exclude overlapping scheduled purges using different report paths.
- Checks all retained backup files in the affected workload, not just candidates, for changes/open file handles during purge.
- Detects between-scan changes in ancillary repository files, immutability markers, and job-level metadata.
- Stops any further deletions on the first per-group purge failure, preserving audit evidence of partial removal.
- Indexed per-directory files and VBM references to avoid repeated full-inventory traversals during purge.
- Removed unused code and extended independent failure and metadata regression coverage.
- Rechecks ancestor job folders for *new* VBM/backup files between scan and unlink, closing a metadata-creation race.
- Refuses unsafe existing report directory permissions without modifying the directory (including system paths specified accidentally).
