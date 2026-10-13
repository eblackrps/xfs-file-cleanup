"""New independent adversarial cases from the final v0.5.0 rubber-duck cycle."""
import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import veeam_xfs_cleanup as v


class FinalAdversarialIssues(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'repo'
        self.folder = self.root / 'BackupCopy' / 'example'
        self.folder.mkdir(parents=True)
        self.old = dt.datetime(2023, 8, 11, tzinfo=dt.timezone.utc)
        self.now = dt.datetime.now(dt.timezone.utc)
        self.cutoff = int((self.now - dt.timedelta(days=28)).timestamp() * 10**9)

    def file(self, filename, when, content=b'x'):
        p = self.folder / filename
        p.write_bytes(content)
        os.utime(p, (when.timestamp(), when.timestamp()))
        return p

    def status_map(self):
        inventory, hazards, _ = v.snapshot(self.root)
        with mock.patch.object(v, 'get_immutable', return_value=False):
            groups = v.build_groups(self.root, inventory, hazards, self.cutoff)
        return {name: g.status for g in groups for name in g.files}

    def test_storage_xml_unknown_path_attribute_must_not_be_ignored(self):
        old = self.file('example.vm-ABCD1234D2023-08-11T170718_AAAA.vbk', self.old)
        current = self.file('example.vm-ABCD1234D2026-10-12T170718_BBBB.vbk', self.now)
        vbm = self.file('example.vm-ABCD1234.vbm', self.now,
            ('<BackupMeta><Storages><Storage FilePath="' + current.name +
             '" PreviousFilePath="' + old.name + '"/>'
             '</Storages></BackupMeta>').encode())
        refs, error = v.metadata_refs(vbm)
        self.assertIsNotNone(error, 'Alternative storage path must fail closed')
        statuses = self.status_map()
        self.assertNotEqual(statuses[str(old.relative_to(self.root))], 'PURGE_CANDIDATE',
                            'A VBM with an unparsed dependency cannot authorize deletion')

    def test_malformed_machine_id_lookalike_fails_closed_even_if_all_old(self):
        malformed = self.file('example.vm-ZZZZ1234D2023-08-11T170718_AAAA.vbk', self.old)
        statuses = self.status_map()
        self.assertNotEqual(statuses[str(malformed.relative_to(self.root))], 'PURGE_CANDIDATE',
                            'Invalid machine IDs cannot silently become name-only purge candidates')

    def test_legitimate_name_only_workload_with_dot_is_still_accepted(self):
        name_only = self.file('my.test.workloadD2023-08-11T170718_AAAA.vbk', self.old)
        statuses = self.status_map()
        self.assertEqual(statuses[str(name_only.relative_to(self.root))], 'PURGE_CANDIDATE')


if __name__ == '__main__':
    unittest.main()

class GuidBoundary(unittest.TestCase):
    def test_truncated_guid_must_not_match_valid_machine_identity(self):
        import veeam_xfs_cleanup as v
        bad = 'example.450738af-de5f-468f-a2e3-1234567890D2023-08-11T170718_AAAA'
        self.assertIsNone(v.backup_filename_match(bad))

    def test_complete_guid_remains_valid(self):
        import veeam_xfs_cleanup as v
        good = 'example.450738af-de5f-468f-a2e3-123456789012D2023-08-11T170718_AAAA'
        self.assertIsNotNone(v.backup_filename_match(good))

class XmlSafetyBoundaries(FinalAdversarialIssues):
    def test_unrecognized_backup_reference_on_known_filepath_child_is_refused(self):
        old = self.file('example.vm-ABCD1234D2023-08-11T170718_AAAA.vbk', self.old)
        current = self.file('example.vm-ABCD1234D2026-10-12T170718_BBBB.vbk', self.now)
        meta = self.file('example.vm-ABCD1234.vbm', self.now,
            ('<BackupMeta><Storage><FilePath PreviousStorage="' + old.name + '">' +
             current.name + '</FilePath></Storage></BackupMeta>').encode())
        self.assertIsNotNone(v.metadata_refs(meta)[1])
        self.assertNotEqual(self.status_map()[str(old.relative_to(self.root))], 'PURGE_CANDIDATE')

    def test_benign_storage_attribute_remains_supported(self):
        old = self.file('example.vm-ABCD1234D2023-08-11T170718_AAAA.vbk', self.old)
        meta = self.file('example.vm-ABCD1234.vbm', self.old,
            ('<BackupMeta><Storage FilePath="' + old.name + '" Seq="1" /></BackupMeta>').encode())
        self.assertEqual(v.metadata_refs(meta), ({old.name.casefold()}, None))

    def test_malformed_id_blocks_other_old_full_in_same_directory(self):
        bad = self.file('example.vm-ZZZZ1234D2023-08-11T170718_AAAA.vib', self.old)
        old = self.file('example.vm-ABCD1234D2023-08-10T170718_AABB.vbk', self.old)
        statuses = self.status_map()
        self.assertEqual(statuses[str(bad.relative_to(self.root))], 'REVIEW')
        self.assertEqual(statuses[str(old.relative_to(self.root))], 'REVIEW')
