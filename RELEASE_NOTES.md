# Veeam XFS Backup-Copy Cleanup v0.5.0

First formal GitHub release - October 13, 2026

## Overview

v0.5.0 provides a single Linux/Python cleanup tool for supported filesystem layouts produced by Veeam Backup & Replication v12 and v13 backup-copy jobs on conventional XFS hardened repositories. It inventories possible expired chains and can remove independently eligible files only after additional identity, metadata and immutability checks.

The default cutoff is **28 elapsed days**. The tool operates locally, without access to VBR, and never edits VBMs, clears immutable flags, changes GFS retention, removes Veeam immutability tracking files or deletes directories.

## Highlights

- Recognizes legacy v12, newer v13 machine-ID/GUID, and documented name-only timestamped backup filenames.
- Holds mixed-version or ambiguous dependency chains, including unknown VBM Storage references and malformed machine IDs, instead of treating them as safe to delete.
- Detects changes in repository files and metadata, nested/bind mounts, open backup file handles, and overlapping cleanup sessions.
- Provides interactive scan, confirmed cleanup, unattended `--purge`, CSV decisions, flagged-not-removed reports and a durable deletion audit journal.
- Keeps the accepted v0.4.3 cleanup interfaces; all cleanup source code matches the reviewed v0.5.0-rc3 candidate.

## Release assets

- **`veeam_xfs_cleanup_v0.5.0_client_with_guide.zip`** - Python script, README, license, version history, Markdown/PDF operating guide and internal file checksums.
- **`Veeam_XFS_Backup_Copy_Cleanup_v0.5.0_Operator_Guide.pdf`** - standalone searchable PDF.
- **`veeam_xfs_cleanup_v0.5.0_SHA256SUMS.txt`** - checksums for the downloadable ZIP and PDF.

The source, installation commands and operator instructions are available in this repository. The archive is generated from the tag by `scripts/build_release.py` and verified in the GitHub publishing workflow.

## Validation

The v0.5.0 cleanup code completed two separate no-change offline review gates. The engineering suite passed **146/146** Linux root tests, **144** non-root tests with two expected privileged skips, and additional synthetic mixed-version chain scenarios. The publishing workflow runs the focused tests committed in this repository and verifies the release assets after download.

**Important:** The v13 compatibility tests use synthetic file and XML fixtures. Authentic v13 VBM metadata and destructive cleanup on an actual mounted hardened XFS repository were **not** independently validated. Earlier customer manual testing applied to v0.4.3/v12, not native v13 workflows. This is a public source release, **not** Veeam vendor certification or approval for unattended production v13 deletion.

All destructive runs require a guaranteed backup-copy idle window. Veeam Infrastructure/Software Appliance root-level scheduling is outside the supported scope. Review `docs/OPERATOR_GUIDE.md` before use.

## Release identity

- Version: **0.5.0**
- Tag: **v0.5.0**
- Previously reviewed source Git blob: `aca3ac74c244f0edd9fdc73a0811692066e1fd51`
- License: MIT

