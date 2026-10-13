"""Seeded independent chain invariants for the v12/v13 combined parser.

Synthetic only. A held current incremental must never allow deletion of the
only older full for its workload, irrespective of old/new naming style.
"""
import datetime as dt
import os
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import veeam_xfs_cleanup as v


class RandomizedChainInvariants(unittest.TestCase):
    def test_600_mixed_version_dependency_and_new_full_cases(self):
        rng = random.Random(0x505034)
        old = dt.datetime(2023, 8, 11, 17, 7, 18, tzinfo=dt.timezone.utc)
        young = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
        cutoff = int((dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=28)).timestamp() * 1e9)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'
            dependent = []
            new_full = []
            for index in range(600):
                p = root / 'BackupCopy' / f'vm{index}'
                p.mkdir(parents=True)
                workload = f'example-workload-{index}'
                ident = f'{rng.randint(1, 0xFFFF):04X}'
                style = index % 6
                if style < 2:
                    full_name = f'{workload}.vm-{ident}{old:%Y-%m-%dT%H%M%S}_AAAA.vbk'
                elif style < 5:
                    full_name = f'{workload}.vm-{ident}D{old:%Y-%m-%dT%H%M%S}_AAAA.vbk'
                else:
                    full_name = f'{workload}D{old:%Y-%m-%dT%H%M%S}.vbk'
                if style in (0, 2, 3):
                    recent_name = f'{workload}.vm-{ident}D{young:%Y-%m-%dT%H%M%S}_BEEF.vib'
                elif style in (1, 4, 5):
                    recent_name = f'{workload}D{young:%Y-%m-%dT%H%M%S}.vib'
                full = p / full_name
                full.write_bytes(b'full')
                os.utime(full, (old.timestamp(), old.timestamp()))
                inc = p / recent_name
                inc.write_bytes(b'increment')
                os.utime(inc, (young.timestamp(), young.timestamp()))
                dependent.append(str(full.relative_to(root)))
            inv, hazards, _ = v.snapshot(root)
            with mock.patch.object(v, 'get_immutable', return_value=False):
                groups = v.build_groups(root, inv, hazards, cutoff)
            statuses = {file: g.status for g in groups for file in g.files}
            failed = [(f, statuses[f]) for f in dependent if statuses[f] == 'PURGE_CANDIDATE']
            self.assertFalse(failed, f'New dependent increment exposed {len(failed)} old fulls')
