"""Independent adversarial regression cases for the v0.5.0 dual-version build."""
import datetime as dt
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import veeam_xfs_cleanup as v


class Fixture(unittest.TestCase):
    """Disposable repository fixture shared by independent review tests."""
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix='xfs-debug-')
        self.addCleanup(t.cleanup)
        self.root = Path(t.name) / 'repo'
        self.job = self.root / 'CopyJob' / 'vm-one'
        self.job.mkdir(parents=True)
        self.old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=100)
        self.recent = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
        self.cutoff = time.time_ns() - 28 * 86400 * 10**9

    def make(self, name, when, contents=b'data'):
        p = self.job / name
        p.write_bytes(contents)
        os.utime(p, (when.timestamp(), when.timestamp()))
        return p

    def statuses(self):
        inv,haz,aux=v.snapshot(self.root)
        with mock.patch.object(v,'get_immutable',return_value=False):
            gr=v.build_groups(self.root,inv,haz,self.cutoff)
        return {rel:g.status for g in gr for rel in g.files}



class IndependentDebugTests(Fixture):
    def test_documented_v13_workload_timestamp_name_parses(self):
        when=self.old.strftime('%Y-%m-%dT%H%M%S')
        f=self.make('Empty VM encryptedD'+when+'.vbk',self.old)
        info=v.snapshot(self.root)[0][str(f.relative_to(self.root))]
        self.assertIsNotNone(v.payload_parts(info), 'Veeam documents workloadDtimestamp.vbk')

    def test_documented_v13_workload_timestamp_increment_retains_base(self):
        stamp=lambda d:d.strftime('%Y-%m-%dT%H%M%S')
        full=self.make('Example VM D'+stamp(self.old)+'.vbk',self.old)
        inc=self.make('Example VM D'+stamp(self.recent)+'.vib',self.recent)
        mapping=self.statuses()
        self.assertEqual(mapping[str(full.relative_to(self.root))],'RETAIN')
        self.assertEqual(mapping[str(inc.relative_to(self.root))],'RETAIN')

    def test_unknown_vbm_storage_tag_is_not_treated_as_empty_metadata(self):
        stamp=self.old.strftime('%Y-%m-%dT%H%M%S')
        old=self.make('vm-one.vm-A1B2D'+stamp+'_ABCD.vbk',self.old)
        meta=self.make('vm-one.vm-A1B2.vbm',self.old,(
            '<BackupMeta><AlternateStorages><BackupStorage Path="'+old.name+'" />'
            '</AlternateStorages></BackupMeta>').encode())
        inv,haz,aux=v.snapshot(self.root)
        refs,error=v.metadata_refs(meta)
        self.assertIsNotNone(error, 'An unknown storage mapping must fail closed')
        mapping=self.statuses()
        self.assertEqual(mapping[str(old.relative_to(self.root))],'REVIEW')
        self.assertEqual(mapping[str(meta.relative_to(self.root))],'REVIEW')

    def test_unsupported_short_guid_tail_not_accepted(self):
        bad='vm-one.450738af-de5f-468f-a2e3-60b4f09D'+self.old.strftime('%Y-%m-%dT%H%M%S')+'.vbk'
        f=self.make(bad,self.old)
        info=v.snapshot(self.root)[0][str(f.relative_to(self.root))]
        self.assertIsNone(v.payload_parts(info))

class AdditionalSafetyTests(Fixture):
    def test_child_filepath_is_understood_and_not_flagged_as_unknown(self):
        stamp=self.old.strftime('%Y-%m-%dT%H%M%S')
        old=self.make('vm-one.vm-A1B2D'+stamp+'_ABCD.vbk',self.old)
        meta=self.make('vm-one.vm-A1B2.vbm',self.old,(
            '<BackupMeta><Storages><Storage><FilePath>'+old.name+'</FilePath>'
            '</Storage></Storages></BackupMeta>').encode())
        refs,error=v.metadata_refs(meta)
        self.assertEqual((refs,error),({old.name.casefold()},None))

    def test_mountinfo_reports_same_device_nested_bind_mount(self):
        fake=(
            '31 23 8:1 / / rw - ext4 /dev/sda rw\n'
            '32 31 8:1 /src /tmp/example\\040repo/child rw - ext4 /dev/sda rw\n'
        )
        with mock.patch.object(v.Path,'read_text',return_value=fake):
            detected=v.nested_mount_points(Path('/tmp/example repo'))
        self.assertEqual(detected,{'/tmp/example repo/child'})

    def test_nested_mount_skipped_even_when_device_matches(self):
        child=self.job/'mount_alias'
        child.mkdir()
        # A same-device bind mount looks like an ordinary directory to stat.
        (child/'foreign.vbk').write_text('do-not-touch')
        with mock.patch.object(v,'nested_mount_points',return_value={str(child)}):
            inv,haz,_=v.snapshot(self.root)
        self.assertTrue(any('foreign-mount' in value for value in haz.get(str(self.job.relative_to(self.root)),set())))
        self.assertFalse(any('foreign.vbk' in p for p in inv))

    def test_new_nested_mount_after_inventory_refuses_purge(self):
        stamp=self.old.strftime('%Y-%m-%dT%H%M%S')
        old=self.make('vm-one.vm-A1B2D'+stamp+'_ABCD.vbk',self.old)
        inv,haz,aux=v.snapshot(self.root)
        with mock.patch.object(v,'get_immutable',return_value=False):
            groups=v.build_groups(self.root,inv,haz,self.cutoff)
        reports=Path(self.root.parent)/'reports'
        reports.mkdir(mode=0o700)
        with mock.patch.object(v,'nested_mount_points',return_value={str(self.job/'new_mount')}), \
             mock.patch.object(v,'check_immutable_at',return_value=False), \
             mock.patch.object(v,'fuser_check',return_value=None):
            with self.assertRaises(v.Refuse):
                v.purge(self.root,groups,inv,reports,'nested-mount',aux)
        self.assertTrue(old.exists())

class DualVersionEndToEnd(Fixture):
    def test_name_only_old_chain_independent_new_full_purge_simulation(self):
        oldstamp=self.old.strftime('%Y-%m-%dT%H%M%S')
        newstamp=self.recent.strftime('%Y-%m-%dT%H%M%S')
        old=self.make('Example VMD'+oldstamp+'.vbk',self.old)
        inc=self.make('Example VMD'+(self.old+dt.timedelta(hours=4)).strftime('%Y-%m-%dT%H%M%S')+'.vib',self.old)
        fresh=self.make('Example VMD'+newstamp+'.vbk',self.recent)
        meta=self.make('Example VM.vbm',self.recent,(
            '<BackupMeta><Storages><Storage FilePath="'+fresh.name+'"/>'
            '</Storages></BackupMeta>').encode())
        inv,haz,extra=v.snapshot(self.root)
        with mock.patch.object(v,'get_immutable',return_value=False):
            gr=v.build_groups(self.root,inv,haz,self.cutoff)
        self.assertEqual(next(g.status for g in gr if str(old.relative_to(self.root)) in g.files),'PURGE_CANDIDATE')
        reports=self.root.parent/'reports'
        reports.mkdir(mode=0o700)
        with mock.patch.object(v,'fuser_check',return_value=None),mock.patch.object(v,'check_immutable_at',return_value=False):
            deleted,failures,_=v.purge(self.root,gr,inv,reports,'nameonly',extra)
        self.assertEqual((deleted,failures),(2,0))
        self.assertFalse(old.exists())
        self.assertFalse(inc.exists())
        self.assertTrue(fresh.exists())
        self.assertTrue(meta.exists())

    def test_expired_legacy_full_is_protected_by_new_name_only_increment(self):
        legacy=self.make('Example VM.vm-1A2B'+self.old.strftime('%Y-%m-%dT%H%M%S')+'_AAAA.vbk',self.old)
        incremental=self.make('Example VMD'+self.recent.strftime('%Y-%m-%dT%H%M%S')+'.vib',self.recent)
        statuses=self.statuses()
        self.assertEqual(statuses[str(legacy.relative_to(self.root))],'REVIEW')
        self.assertEqual(statuses[str(incremental.relative_to(self.root))],'RETAIN')

    def test_threshold_boundary_excludes_exact_time(self):
        stamp=self.old.strftime('%Y-%m-%dT%H%M%S')
        file=self.make('vm-one.vm-A1B2D'+stamp+'_ABCD.vbk',self.old)
        file.stat()
        # The exact cutoff is not expired: strict '<', not '<='.
        mtime=file.stat().st_mtime_ns
        inv,haz,_=v.snapshot(self.root)
        with mock.patch.object(v,'get_immutable',return_value=False):
            groups=v.build_groups(self.root,inv,haz,mtime)
        self.assertEqual(next(g.status for g in groups if str(file.relative_to(self.root)) in g.files),'RETAIN')

    def test_unknown_new_vbm_schema_with_recent_payload_blocks_expired_full(self):
        old=self.make('vm-one.vm-A1B2D'+self.old.strftime('%Y-%m-%dT%H%M%S')+'_ABCD.vbk',self.old)
        recent=self.make('vm-one.vm-A1B2D'+self.recent.strftime('%Y-%m-%dT%H%M%S')+'_BEEF.vbk',self.recent)
        meta=self.make('vm-one.vm-A1B2.vbm',self.recent,(
            '<BackupMeta><BackupStorage Path="'+old.name+'"/></BackupMeta>').encode())
        statuses=self.statuses()
        self.assertEqual(statuses[str(old.relative_to(self.root))],'REVIEW')
        self.assertEqual(statuses[str(recent.relative_to(self.root))],'REVIEW')
        self.assertEqual(statuses[str(meta.relative_to(self.root))],'REVIEW')

class IndependentRandomizedDependencyTests(Fixture):
    def test_randomized_legacy_and_name_only_dependencies(self):
        import random
        rng = random.Random(530105)
        stamp = lambda when: when.strftime('%Y-%m-%dT%H%M%S')
        for n in range(300):
            folder = self.root / 'BackupCopy' / ('workload-' + str(n))
            folder.mkdir(parents=True)
            name = 'workload-' + str(n)
            ident = ''.join(rng.choice('0123456789abcdef') for _ in range(8))
            if n % 3 == 0:
                full_name = name + '.vm-' + ident + stamp(self.old) + '_AABB.vbk'
                inc_name = name + 'D' + stamp(self.recent) + '.vib'
            elif n % 3 == 1:
                full_name = name + 'D' + stamp(self.old) + '.vbk'
                inc_name = name + 'D' + stamp(self.recent) + '.vib'
            else:
                full_name = name + '.vm-' + ident + 'D' + stamp(self.old) + '_AABB.vbk'
                inc_name = name + '.vm-' + ident + 'D' + stamp(self.recent) + '_AABC.vib'
            for filename, date in ((full_name, self.old), (inc_name, self.recent)):
                p = folder / filename
                p.write_bytes(b'fixture')
                os.utime(p, (date.timestamp(), date.timestamp()))
        inv,haz,_ = v.snapshot(self.root)
        with mock.patch.object(v,'get_immutable',return_value=False):
            groups = v.build_groups(self.root, inv, haz, self.cutoff)
        candidates = {rel for g in groups if g.status == 'PURGE_CANDIDATE' for rel in g.files}
        self.assertFalse(any(rel.endswith('.vbk') for rel in candidates),
                         'A recent increment may not authorize deletion of its base')
