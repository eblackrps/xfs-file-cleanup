# v0.5.0-rc3 Dual-Version Debug, Rubber-Duck and Offline Validation

Baseline: `eblackrps/xfs-file-cleanup` main commit `7997e4b46a93b8cf73d0b96949054e5eef9d1a2e` (v0.4.3).

Scope: Linux/XFS backup-copy filesystem cleanup, 28 elapsed days by default, v12 legacy plus v13 per-machine filename/metadata layouts. No VBR API access, VBM editing, or immutability overrides.

## Offline checks

- 146 automated regression tests passed in the initial remediation run with root permissions. New rc3 cases cover hidden VBM XML Storage references, malformed machine IDs, GUID-boundary input, and randomized mixed-version chains.
- Non-root execution is revalidated in the frozen rc3 gate; two root-required purge/lock tests remain intentionally excluded when running without root.
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

## rc3 corrective findings and verified behavior

1. An alternative `Storage` attribute (for example `PreviousFilePath`) could name an older VBK while a recognized `FilePath` named a newer VBK. The old reference was discarded by the parser, which could make the older full purge-eligible. The parser now holds any unknown path-bearing Storage attribute or recognized child attribute; benign numeric/non-path attributes remain accepted.
2. An invalid `vm-` identity containing non-hex characters could fall back to the `WorkloadDtimestamp` filename profile. When the entire directory was old, that malformed backup could be classified as a purge candidate. Malformed identity-like names now make the directory REVIEW instead.
3. The GUID-like terminal component accepted nine, ten, or eleven hexadecimal digits without evidence that those layouts are valid. Only the eight-digit form supported in the accepted regression fixtures and the twelve-digit full form are recognized.

No external VBR or authentic v13 metadata/mounted-XFS deletion tests were supplied. The two separate no-change offline gates passed on the final code candidate. The formal GitHub release uses the same verified Python source and publishes reproducible, checksummed documents and archives; these checks do not establish production deletion safety on an untested v13 repository.
