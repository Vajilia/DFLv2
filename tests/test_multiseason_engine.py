"""Real-engine determinism across the year boundary. NEEDS A FINISHED YEAR: set DFL_TEST_YEAR1_ROOT to a private root whose first season is completely built
(`broadcast_build.py init` then `step`, about 35 minutes); it is only ever COPIED. Skipped otherwise (a skipped test says so in the run summary).

The copy is continued two ways from the same seed and compared:  A: `next-season`, then the builder fires the league one event at a time with the feed, replay and live
recording builds in between;  B: the league runner alone.  The exhaustive league digest of both must be identical (journal, state and RNG consequences), and the builder must
have taken only the second season's games (not the first season's 400 again). EVENTS defaults to 13 (the offseason, draft, week 1 and Tour round 1; about 2 minutes)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
from dflsite import broadcast as B, season as S

SRC = os.environ.get('DFL_TEST_YEAR1_ROOT')
EVENTS = int(os.environ.get('DFL_TEST_EVENTS', '13'))
PY = sys.executable


def tool(*a):
    p = subprocess.run([PY] + [os.path.join(ROOT, 'tools', a[0])] + list(a[1:]), capture_output=True, text=True)
    if p.returncode:
        raise AssertionError((p.stderr or p.stdout)[-800:])
    return p.stdout


@unittest.skipUnless(SRC and os.path.isdir(os.path.join(SRC or '', 'store')), 'set DFL_TEST_YEAR1_ROOT to a root whose first season is completely built')
class EngineBoundary(unittest.TestCase):
    def test_builder_driven_second_season_equals_free_running_league(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = os.path.join(tmp, 'a'), os.path.join(tmp, 'b')
            shutil.copytree(SRC, a, symlinks=True)
            shutil.copytree(SRC, b, symlinks=True)
            self.assertEqual(json.loads(tool('broadcast_build.py', 'status', '--root', a))['events_built'], 47)
            nxt = json.loads(tool('broadcast_build.py', 'next-season', '--root', a))
            self.assertEqual((nxt['season'], nxt['event_base']), (2, 47))
            tool('broadcast_build.py', 'step', '--root', a, '--events', str(EVENTS))
            st = json.loads(tool('broadcast_build.py', 'status', '--root', a))
            self.assertEqual((st['season'], st['events_built']), (2, EVENTS))
            builds = B.get(os.path.join(a, 'store', S.SEASONS, 's002', S.BUILDS))
            self.assertEqual(sorted(int(k) for k in builds['events']), list(range(48, 48 + EVENTS)))
            self.assertTrue(all(g.startswith('S002/') for g in builds['games']), 'only the second season\'s games')
            tool('league_runner.py', os.path.join(b, 'league', 'league.db'), '--until-events', str(47 + EVENTS), '--control-dir', os.path.join(b, 'league', 'ctl'))
            da = tool('league_store.py', 'digest', os.path.join(a, 'league', 'league.db'))
            db = tool('league_store.py', 'digest', os.path.join(b, 'league', 'league.db'))
            self.assertEqual(da, db, 'the builder-driven league and the free-running league differ after the year boundary')


if __name__ == '__main__':
    unittest.main()
