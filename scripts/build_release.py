#!/usr/bin/env python3
"""Build a deterministic customer ZIP and checksums from the checked-out source."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_FILES = (
    'veeam_xfs_cleanup.py',
    'README.md',
    'LICENSE',
    'CHANGELOG.md',
    'RELEASE_NOTES.md',
    'docs/OPERATOR_GUIDE.md',
    'docs/VALIDATION.md',
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build() -> None:
    out = subprocess.check_output(
        [sys.executable, str(ROOT / 'veeam_xfs_cleanup.py'), '--version'],
        text=True, timeout=12,
    ).strip()
    if not out.startswith('veeam-xfs-cleanup '):
        raise RuntimeError('Unexpected script version output: ' + out)
    version = out.split(' ', 1)[1]
    if not version or not all(c in '0123456789.' for c in version):
        raise RuntimeError('Invalid semantic version: ' + version)
    folder = 'veeam_xfs_cleanup_v' + version
    doc_pdf = f'docs/Veeam_XFS_Backup_Copy_Cleanup_v{version}_Operator_Guide.pdf'
    target = ROOT / 'dist'
    target.mkdir(mode=0o700, exist_ok=True)
    zip_path = target / (folder + '_client_with_guide.zip')
    pdf_dest = target / Path(doc_pdf).name
    manifest_path = target / ('veeam_xfs_cleanup_v' + version + '_SHA256SUMS.txt')
    payloads = {}
    for name in (*PACKAGE_FILES, doc_pdf):
        file_path = ROOT / name
        if not file_path.is_file() or file_path.is_symlink():
            raise RuntimeError('Missing or unsafe required file: ' + name)
        payloads[name] = file_path.read_bytes()
    if not payloads[doc_pdf].startswith(b'%PDF-'):
        raise RuntimeError('Operator guide is not a PDF')
    checksum_text = ''.join(
        sha256(payloads[name]) + '  ' + name + '\n' for name in (*PACKAGE_FILES, doc_pdf)
    )
    payloads['SHA256SUMS.txt'] = checksum_text.encode('utf-8')
    # Explicit ZIP metadata avoids timestamps and machine-specific paths.
    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9, allowZip64=True) as zf:
        for name, contents in sorted(payloads.items()):
            info = zipfile.ZipInfo(folder + '/' + name, (2026, 10, 13, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            zf.writestr(info, contents, compress_type=zipfile.ZIP_DEFLATED,
                        compresslevel=9)
    shutil.copyfile(ROOT / doc_pdf, pdf_dest)
    with zipfile.ZipFile(zip_path, 'r') as zf:
        assert zf.testzip() is None, 'ZIP CRC failure'
        for name, contents in payloads.items():
            assert zf.read(folder + '/' + name) == contents, 'ZIP payload changed: ' + name
    releases = (zip_path, pdf_dest)
    manifest_path.write_text(''.join(
        sha256(path.read_bytes()) + '  ' + path.name + '\n'
        for path in releases
    ), encoding='utf-8')
    for path in (*releases, manifest_path):
        print(f'{path.name}\t{path.stat().st_size}\t{sha256(path.read_bytes())}')


if __name__ == '__main__':
    build()
