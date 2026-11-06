"""What the broadcast depends on after a season is handed over (read-only retention evidence; docs/STORAGE_RETENTION.md section 5).

Synthetic two-season store in virtual time (tests/test_multiseason.py). Two facts, both about what a retention action may or may not rely on:

  * Once the next season is on air, the daemon never reads the finished season's PRIVATE per-game caches (store/games/<gid>/, store/playback/games/<file>): moving
    them away changes nothing that is published from then on.
  * The daemon does NOT re-create the finished season's PUBLIC files. A public folder lost after the hand-over stays lost for the earlier seasons while the indexes
    still list them. The public folder is therefore a permanent store that needs its own backup; it cannot be regenerated from the carry record.
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tests'))
from dflsite import broadcast as B, season as S
from test_multiseason import Rig, run


class AfterHandOver(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        B.FSYNC = False
        self.addCleanup(setattr, B, 'FSYNC', True)

    def rig(self):
        r = Rig(tempfile.mkdtemp(dir=self.tmp.name))
        r.y1.build(4)
        r.y2.build(4)
        return r

    def control(self):
        r = self.rig()
        run(r.started(), 0, r.end1 + r.y2.plan['total_s'] + 100)
        return r.tree()

    def test_the_finished_seasons_private_caches_are_not_read_after_the_hand_over(self):
        want = self.control()
        r = self.rig()
        bc = r.started()
        run(bc, 0, r.end1 + 200)
        self.assertTrue(os.path.exists(os.path.join(r.store, S.CARRY)))
        moved = Path(self.tmp.name) / 'moved'
        moved.mkdir()
        n = 0
        for name in os.listdir(os.path.join(r.store, 'games')):
            if name.startswith('S901-'):
                shutil.move(os.path.join(r.store, 'games', name), moved / name)
                n += 1
        pb = os.path.join(r.store, 'playback', 'games')
        for name in os.listdir(pb):
            if name.startswith('S901-'):
                shutil.move(os.path.join(pb, name), moved / ('pb-' + name))
                n += 1
        self.assertGreaterEqual(n, 6)
        run(r.daemon(), r.end1 + 200, r.end1 + r.y2.plan['total_s'] + 100)         # a fresh process, as after a restart
        got = r.tree()
        self.assertEqual(sorted(got), sorted(want))
        for k in want:
            if k != 'broadcast/schedule.json':
                self.assertEqual(got[k], want[k], k)

    def test_a_public_folder_lost_after_the_hand_over_is_not_regenerated_for_earlier_seasons(self):
        want = self.control()
        r = self.rig()
        bc = r.started()
        run(bc, 0, r.end1 + 200)
        shutil.rmtree(r.public)
        run(r.daemon(), r.end1 + 200, r.end1 + r.y2.plan['total_s'] + 100)
        got = r.tree()
        missing = sorted(k for k in want if k not in got)
        self.assertTrue(any(k.startswith('broadcast/live/S901-') for k in missing), 'the first season\'s live recordings are not re-created')
        self.assertTrue(any(k.startswith('releases/') for k in missing), 'the first season\'s releases are not re-created')
        idx = r.js('playback/index.json')['games']
        dangling = [e for e in idx if e['season'] == 901 and not os.path.exists(r.pub('playback', e['file']))]
        self.assertEqual(len(dangling), 3, 'the cumulative index still lists the first season, with no file behind it')
        for k in want:                                           # everything of the season on air is complete again
            if 'S902' in k or k.startswith('releases/e0000') and int(k.split('/')[1][1:6]) > 4:
                self.assertEqual(got.get(k), want[k], k)


if __name__ == '__main__':
    unittest.main()
