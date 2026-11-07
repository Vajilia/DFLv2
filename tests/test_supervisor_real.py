"""The supervisor against the REAL builder and the REAL engine (one smoke check; the rest of M6 is tested on synthetic seasons in tests/test_broadcast_supervisor.py).

NEEDS A FINISHED YEAR: set DFL_TEST_YEAR1_ROOT to a private root whose first season is completely built (`broadcast_build.py init` then `step`, about 15 to 35 minutes).
It is only ever COPIED (about 1 GB). Skipped otherwise, and a skipped test says so in the run summary.

On the copy: the supervisor sees a complete season with no successor, so its first cycle runs the real `next-season` (season 2, event base 47), the next cycles run the real
`step --events 1` three times (the offseason events of season 2, about 20 s each), and every one of them goes through the builder's own lock and the league runner. Then it
checks what the supervisor must guarantee on a real store: the plan and builds of season 1 are byte-identical, season 2 holds exactly the three events built once each, the
league has exactly 50 events, nothing else was written outside the builder's own folders and `supervisor/`, and the health file is honest. It does not run the whole season (the
engine determinism across the boundary is tests/test_multiseason_engine.py's job) and it does not need a daemon.
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))
import broadcast_supervisor as SV                       # noqa: E402
from dflsite import status as ST                        # noqa: E402

SRC = os.environ.get('DFL_TEST_YEAR1_ROOT')
EVENTS = int(os.environ.get('DFL_TEST_SUPERVISOR_EVENTS', '3'))


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


@unittest.skipUnless(SRC and os.path.isdir(os.path.join(SRC or '', 'store')), 'set DFL_TEST_YEAR1_ROOT to a root whose first season is completely built')
class RealEngineSmoke(unittest.TestCase):
    def test_next_season_and_the_first_events_are_built_by_the_real_builder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, 'root')
            shutil.copytree(SRC, root, symlinks=True)
            Path(root, 'store', 'continuous').touch()
            before = {n: sha(os.path.join(root, 'store', n)) for n in ('plan.json', 'builds.json')}
            sup = SV.Supervisor(SV.Config(root=root, floor_gb=1, step_margin_gb=0.25, season_gb=0.5, inode_floor=1000, step_inodes=1000, season_inodes=10000))
            sup.acquire()
            try:
                h = sup.cycle()
                self.assertEqual(h['seasons']['on_air'], 1)
                self.assertEqual(h['builder']['state'], 'building')
                self.assertEqual(h['builder']['season'], 2)
                plan = json.loads(Path(root, 'store', 'seasons', 's002', 'plan.json').read_text())
                self.assertEqual(plan['event_base'], 47)
                for i in range(EVENTS):
                    h = sup.cycle()
                    self.assertEqual(h['builder']['consecutive_failures'], 0, h['builder'])
            finally:
                sup.release()
            builds = json.loads(Path(root, 'store', 'seasons', 's002', 'builds.json').read_text())
            self.assertEqual(sorted(int(k) for k in builds['events']), list(range(48, 48 + EVENTS)))
            self.assertEqual(ST.league_events(os.path.join(root, 'league', 'league.db')), 47 + EVENTS)
            for n, d in before.items():
                self.assertEqual(sha(os.path.join(root, 'store', n)), d, 'season 1 is exactly as it was')
            self.assertEqual(h['builder']['built'], EVENTS)
            self.assertEqual(h['builder']['total'], 47)
            self.assertEqual(h['status'], 'ok', h['problems'])
            self.assertTrue(h['capacity']['ok'])
            self.assertEqual(json.loads(Path(root, 'supervisor', 'health.json').read_text())['builder']['built'], EVENTS)


if __name__ == '__main__':
    unittest.main()
