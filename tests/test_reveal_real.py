"""The machinery of tools/reveal_real.py on the small test season (the real run takes 25 minutes and is not part of the suite).

Required here:
  * the first results reveal is found from the plan (the position of the first playback release)
  * the fixture jump moves the clock and the public tree to the target in virtual time with nothing running, ends with a lapsed lease and the production `ready_until`, and leaves
    the two indexes in the state the reveal check expects: the replay library ahead of the playback index by the games that have ended and are waiting
  * after the jump, real-time-style ticks cross the reveal and the playback index catches up in one step, with no difference left (the same check the long run makes, in miniature)
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'tests'))
from dflsite import broadcast as B           # noqa: E402
import index_reconcile as IR                  # noqa: E402
import reveal_real as RR                      # noqa: E402
from test_season import Rig, run, T0          # noqa: E402


class RevealMachinery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        B.FSYNC = False
        self.addCleanup(setattr, B, 'FSYNC', True)
        self.pub = tempfile.mkdtemp()
        self.rig = Rig(self.tmp.name)
        self.rig.public = self.pub
        self.bc = self.rig.daemon()
        self.bc.clock.start(time.time() - 600, 0.0, B.LEASE_S, ready_until=0.0)
        self.store = os.path.join(self.tmp.name, 'store')

    def test_the_first_reveal_is_the_first_playback_release(self):
        self.assertEqual(RR.first_reveal(self.store), self.rig.t['r1'])

    def test_the_jump_lands_before_the_reveal_with_the_library_ahead_and_a_crossing_closes_the_gap(self):
        t_rev = RR.first_reveal(self.store)
        info = RR.jump(self.tmp.name, self.pub, t_rev - 150)
        self.assertGreaterEqual(info['to_s'], t_rev - 151)
        self.assertLess(info['to_s'], t_rev)
        st = B.Clock(self.store, os.path.join(self.store, '_u')).state()
        self.assertEqual(st['state'], 'running')
        self.assertIn('ready_until', st)
        before = IR.reconcile(self.tmp.name, self.pub, info['to_s'], info['to_s'], 0.0)
        self.assertTrue(before['ok'], before['counts'])
        self.assertEqual(before['counts'].get('reveal_delay'), 2, 'the two games of the first window ended and wait for the recap: ' + str(before['counts']))
        self.assertEqual(before['playback'], 0)
        # cross the reveal the way the daemon does: second by second
        bc = self.rig.daemon()
        now = time.time()
        t = 0
        while t < 200:
            bc.tick(now + t)
            t += 1
        pos = bc.clock.position(now + t)
        after = IR.reconcile(self.tmp.name, self.pub, pos, pos, 0.0)
        self.assertTrue(after['ok'], after['counts'])
        self.assertGreaterEqual(pos, t_rev)
        self.assertEqual(after['counts'].get('reveal_delay'), None)
        self.assertEqual(after['playback'], 2)


if __name__ == '__main__':
    unittest.main()
