# v0.5.0-rc2 Dual-Version Debug, Rubber-Duck and Offline Validation

Baseline: `eblackrps/xfs-file-cleanup` main commit `7997e4b46a93b8cf73d0b96949054e5eef9d1a2e` (v0.4.3).

Scope: Linux/XFS backup-copy filesystem cleanup, 28 elapsed days by default, v12 legacy plus v13 per-machine filename/metadata layouts. No VBR API access, VBM editing, or immutability overrides.

## Offline checks

- 134 automated regression tests passed with root permissions; includes 102 original tests and dual-version, name-only filename, unknown XML schema, nested-mount, CLI and failure-injection cases.
- 132 tests passed as an unprivileged Linux user; two root-required purge/lock tests skipped as designed.
- 150 existing plus 300 new randomly generated mixed-version name/dependency scenarios checked; recent v13 incrementals never authorized deletion of older potentially dependent fulls.
- Python 3.8 syntax compatibility confirmed with `ast.parse(feature_version=(3,8))`; actual Python 3.8 interpreter was not available in this environment.
- CLI menu, `--dry-run`, simulated root-only `--purge`, end-to-end name-only chain deletion/retention, report generation, mount-aware refusal, and non-XFS purge refusal covered in the suite.
- The native Betterleaks 1.9.0 credential preflight on the engineering archive returned PASS with zero findings. Public disclosure rule hits require separate manual disposition and exact-final-package re-verification.

## Important limitations

- v13 compatibility has been verified against **synthetic** file and XML examples, not an authentic v13 VBM sample. Veeam's documented v13 new-job per-machine layout is the basis for the parser enhancements.
- No mounted XFS hardened repository deletion test or v13 appliance test was available in this environment; synthetic tests mock the filesystem requirement but exercise real unlink/journal operations on disposable files.
- Do not enable unattended v13 purge in production until there is credible platform-specific integration evidence. Do not run on the Veeam Infrastructure/Software Appliance as root.
- Filesystem consistency checks cannot guarantee exclusion of an independent Veeam writer. A backup-copy idle window is mandatory for every destructive run.
- VBM schema or filename ambiguity must fail closed and appear in the skipped/flagged CSV rather than cause partial-chain deletion.

## References

- https://helpcenter.veeam.com/docs/vbr/userguide/per_vm_backup_files.html?ver=13
- https://helpcenter.veeam.com/docs/vbr/userguide/hardened_repository_immutability.html?ver=13

## Rubber-duck assumptions and reproduced defects

1. **Assumption:** Every v13 per-machine VBK encodes `vm-N` or a GUID-like ID. **Reproduced break:** Veeam also documents `WorkloadDYYYY-MM-DDTHHMMSS` names; old and current files could remain unclassified rather than forming one chain. **Correction:** recognize name-only D-separated patterns, isolate their identity, and protect cross-format unbased increments.
2. **Assumption:** An XML VBM with no recognized `<Storage>` records means it has no backup dependencies. **Reproduced break:** an alternative XML element held a `.vbk` path but was read as an empty map. **Correction:** fail closed on unrecognized backup-file paths, while retaining known child `Storage/FilePath` parsing.
3. **Assumption:** Matching Linux device IDs ensures there are no mounted aliases within the repository. **Break:** same-device bind mounts share `st_dev`. **Correction:** parse `/proc/self/mountinfo`, exclude mounted children from inventory, and refuse any nested mount before/throughout destructive operations.

## Release gate and operational limitations

- A passing development/review suite is not equivalent to a real Veeam v13 production certification.
- No Veeam appliance mode; no VBR database or job-control integration; no immutable-bit bypass.
- Deletion must run only in a guaranteed backup-copy idle window.
- Unknown/unverifiable dependencies remain in `flagged_not_removed_*.csv`.
- Reports are created outside the repository and audits are journaled before unlink.
