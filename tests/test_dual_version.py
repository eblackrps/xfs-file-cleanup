"""Standalone, synthetic v12/v13 compatibility smoke tests."""
import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import veeam_xfs_cleanup as v


class DualVersion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'repo'
        self.workload = self.root / 'BackupCopy' / 'example-vm'
        self.workload.mkdir(parents=True)
        self.old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=100)
        self.recent = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)
        self.cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=28)).timestamp() * 1e9

    def make(self, filename, date, data=b'fixture'):
        path = self.workload / filename
        path.write_bytes(data)
        os.utime(path, (date.timestamp(), date.timestamp()))
        return path

    def groups(self):
        files, hazards, _ = v.snapshot(self.root)
        with mock.patch.object(v, 'get_immutable', return_value=False):
            groups = v.build_groups(self.root, files, hazards, int(self.cutoff))
        return {p: g for g in groups for p in g.files}

    def status(self, mapping, file):
        return mapping[str(file.relative_to(self.root))].status

    def stamp(self, when):
        return when.strftime('%Y-%m-%dT%H%M%S')

    def test_v12_base_plus_v13_increment_never_purges_base(self):
        full = self.make('example-vm.vm-ABCD1234' + self.stamp(self.old) + '_ABCD.vbk', self.old)
        inc = self.make('example-vm.vm-ABCD1234D' + self.stamp(self.recent) + '_ABCE.vib', self.recent)
        groups = self.groups()
        self.assertEqual(self.status(groups, full), 'RETAIN')
        self.assertIs(groups[str(full.relative_to(self.root))], groups[str(inc.relative_to(self.root))])

    def test_v13_guid_two_full_chains(self):
        machine = '450738af-de5f-468f-a2e3-60b4f09d'
        full = self.make('example-vm.' + machine + 'D' + self.stamp(self.old) + '_ABCD.vbk', self.old)
        recent = self.make('example-vm.' + machine + 'D' + self.stamp(self.recent) + '_ABCE.vbk', self.recent)
        groups = self.groups()
        self.assertEqual(self.status(groups, full), 'PURGE_CANDIDATE')
        self.assertEqual(self.status(groups, recent), 'RETAIN')

    def test_recent_vbm_reference_blocks_old_full(self):
        old = self.make('example-vm.vm-1234D' + self.stamp(self.old) + '_ABCD.vbk', self.old)
        recent = self.make('example-vm.vm-1234D' + self.stamp(self.recent) + '_ABCE.vbk', self.recent)
        self.make('example-vm.vm-1234.vbm', self.recent,
                  ('<BackupMeta><Storages><Storage FilePath="' + old.name + '"/>' +
                   '<Storage FilePath="' + recent.name + '"/></Storages></BackupMeta>').encode())
        self.assertEqual(self.status(self.groups(), old), 'REVIEW')

    def test_unrecognized_storage_xml_fails_closed(self):
        old = self.make('example-vm.vm-1234D' + self.stamp(self.old) + '_ABCD.vbk', self.old)
        meta = self.make('example-vm.vm-1234.vbm', self.old,
                         b'<BackupMeta><Storages><Storage><NewField>X</NewField></Storage></Storages></BackupMeta>')
        groups = self.groups()
        self.assertEqual(self.status(groups, old), 'REVIEW')
        self.assertEqual(self.status(groups, meta), 'REVIEW')

    def test_version_flag(self):
        from io import StringIO
        with mock.patch('sys.stdout', new_callable=StringIO) as stream:
            with self.assertRaises(SystemExit) as caught:
                v.main(['--version'])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn('0.5.0', stream.getvalue())


if __name__ == '__main__':
    unittest.main()
