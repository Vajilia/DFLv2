"""The replay library and the playback index, reconciled by (season, game ID) (tools/index_reconcile.py).

Required here:
  * the replay library is allowed to hold a game the playback index does not (the broadcast has ended, its results are not released yet): that gap is INTENDED, it is found at the
    positions between a game's end and its block's release, it closes at the release, and at the end of the season the two indexes list exactly the same games
  * a game is never in the playback index before it is in the library, never in either early, and never listed twice
  * each failure the tool names is found when it is made: a duplicate, a game missing from either index (from the library it shows as results public for a game that is not replayable),
    a result released early, an entry nobody planned
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'tests'))
from dflsite import broadcast as B           # noqa: E402
import index_reconcile as IR                  # noqa: E402
from test_season import Rig, run, T0          # noqa: E402


class Reconcile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        B.FSYNC = False
        self.addCleanup(setattr, B, 'FSYNC', True)
        self.pub = tempfile.mkdtemp()
        self.rig = Rig(self.tmp.name)
        self.rig.public = self.pub
        self.bc = self.rig.daemon()
        self.bc.clock.start(T0, 0.0, B.LEASE_S, ready_until=0.0)
        self.at = 0.0

    def advance(self, to):
        run(self.bc, T0 + self.at, T0 + to, step=1)
        self.at = to + 1

    def rec(self, at):
        self.advance(at)
        return IR.reconcile(self.tmp.name, self.pub, at, at, 0.0)

    def test_the_gap_exists_between_a_games_end_and_its_release_and_is_always_the_intended_kind(self):
        seen_delay, seen_closed = False, False
        r1 = self.rig.t['r1']
        for at in list(range(100, int(r1) + 700, 100)):
            rep = self.rec(at)
            self.assertTrue(rep['ok'], (at, rep['counts'], rep['rows'], rep['duplicates']))
            self.assertLessEqual(rep['unique_playback'], rep['unique_replays'], at)
            if rep['counts'].get('reveal_delay'):
                seen_delay = True
                self.assertLess(at, r1 + 5, 'a gap after the block released would be an omission')
                for row in rep['rows']:
                    self.assertGreater(row['gap_s'], 0)
            elif seen_delay and at > r1 + 5:
                seen_closed = True
        self.assertTrue(seen_delay, 'the rig must show the intended lag at least once, or this test proves nothing')
        self.assertTrue(seen_closed)

    def test_at_the_end_of_the_season_the_two_indexes_list_the_same_games(self):
        rep = self.rec(int(self.rig.plan['total_s']) - 1)
        self.assertEqual((rep['unique_replays'], rep['unique_playback'], rep['replays'], rep['playback']), (3, 3, 3, 3))
        self.assertEqual(rep['counts'], {'both': 3})
        self.assertTrue(rep['ok'])

    def test_a_duplicate_entry_is_found(self):
        self.rec(int(self.rig.plan['total_s']) - 1)
        p = os.path.join(self.pub, 'playback', 'index.json')
        idx = B.get(p)
        idx['games'].append(dict(idx['games'][0]))
        B.put(p, idx)
        rep = IR.reconcile(self.tmp.name, self.pub, self.at - 1, self.at - 1, 0.0)
        self.assertFalse(rep['ok'])
        self.assertEqual([d[0] for d in rep['duplicates']], ['playback'])

    def test_a_game_missing_from_the_library_or_from_the_playback_index_is_found(self):
        end = int(self.rig.plan['total_s']) - 1
        self.rec(end)
        for rel, kind in (('broadcast/replays/index.json', 'playback_without_replay'), ('playback/index.json', 'omission_playback')):
            p = os.path.join(self.pub, rel)
            idx = B.get(p)
            gone = idx['games'].pop()
            B.put(p, idx)
            rep = IR.reconcile(self.tmp.name, self.pub, end, end, 0.0)
            self.assertFalse(rep['ok'], rel)
            self.assertIn(kind, rep['counts'], (rel, rep['counts']))
            idx['games'].append(gone)
            B.put(p, idx)

    def test_a_result_released_early_and_a_game_nobody_planned_are_found(self):
        rep = self.rec(300)                                                           # the first window is on air; nothing is released
        self.assertTrue(rep['ok'], rep['counts'])
        self.advance(int(self.rig.plan['total_s']) - 1)
        idx = B.get(os.path.join(self.pub, 'playback', 'index.json'))
        self.setUp()
        self.advance(300)
        B.put(os.path.join(self.pub, 'playback', 'index.json'), idx)
        rep = IR.reconcile(self.tmp.name, self.pub, 300, 300, 0.0)
        self.assertFalse(rep['ok'])
        self.assertTrue({'early_playback', 'playback_without_replay'} & set(rep['counts']), rep['counts'])
        self.setUp()
        self.advance(int(self.rig.plan['total_s']) - 1)
        p = os.path.join(self.pub, 'broadcast', 'replays', 'index.json')
        idx = B.get(p)
        idx['games'].append(dict(idx['games'][0], gid='S901/reg:w99:g99'))
        B.put(p, idx)
        rep = IR.reconcile(self.tmp.name, self.pub, self.at - 1, self.at - 1, 0.0)
        self.assertIn('unknown', rep['counts'])
        self.assertFalse(rep['ok'])


if __name__ == '__main__':
    unittest.main()
