# v0.5.0 Dual-Version Validation

Baseline: `eblackrps/xfs-file-cleanup` main commit `7997e4b46a93b8cf73d0b96949054e5eef9d1a2e` (v0.4.3).

Scope: Linux/XFS backup-copy filesystem cleanup, 28 elapsed days by default, v12 legacy plus v13 per-machine filename/metadata layouts. No VBR API access, VBM editing, or immutability overrides.

## Offline checks

- 121 automated regression tests passed with root permissions; includes the 102 original tests and 19 additional dual-version, CLI and synthetic-failure cases.
- 119 tests passed as an unprivileged Linux user; two root-required purge/lock tests skipped as designed.
- 150 randomly generated mixed-version machine identities checked; a recent v13 incremental never authorized deletion of its v12 base full.
- Python 3.8 syntax compatibility confirmed with `ast.parse(feature_version=(3,8))`; actual Python 3.8 interpreter was not available in this environment.
- CLI menu, `--dry-run`, simulated root-only `--purge`, report generation, and non-XFS purge refusal covered in the suite.
- Native Betterleaks 1.9.0 text-scanning gate passed on the public source candidate. The additional disclosure scanner flagged the run identifier variable `token` as a generic secret assignment and the plaintext `.gitignore` file as unreviewed; both manually reviewed as intentional, non-secret content.

## Important limitations

- v13 compatibility has been verified against **synthetic** file and XML examples, not an authentic v13 VBM sample. Veeam's documented v13 new-job per-machine layout is the basis for the parser enhancements.
- No mounted XFS hardened repository deletion test or v13 appliance test was available in this environment; synthetic tests mock the filesystem requirement but exercise real unlink/journal operations on disposable files.
- Do not enable unattended v13 purge in production until there is credible platform-specific integration evidence. Do not run on the Veeam Infrastructure/Software Appliance as root.
- Filesystem consistency checks cannot guarantee exclusion of an independent Veeam writer. A backup-copy idle window is mandatory for every destructive run.
- VBM schema or filename ambiguity must fail closed and appear in the skipped/flagged CSV rather than cause partial-chain deletion.

## References

- https://helpcenter.veeam.com/docs/vbr/userguide/per_vm_backup_files.html?ver=13
- https://helpcenter.veeam.com/docs/vbr/userguide/hardened_repository_immutability.html?ver=13
