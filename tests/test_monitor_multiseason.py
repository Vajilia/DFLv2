"""The monitor (tools/rehearsal_check.py) over a broadcast that goes on past one season.

The rehearsal monitor was written for ONE season built completely before the run: its oracle was made once, from store/plan.json and store/builds.json. Recurring operation
breaks both assumptions: the builder is still adding events while the clock runs, and at the hand-over (store/carry.json) the season on air changes. Found by running the
supervisor, the daemon and the monitor together (tests/test_integration_m6.py): without these tests passing the monitor would stop a correct broadcast at the first year
boundary (every file of season 2 is "early or unknown" to a season-1 oracle) and at the first event the builder adds after it started.

Required here:
  * a correct two-season broadcast is never a violation: inside season 1, at the boundary, inside season 2, and at the end
  * a monitor started while the builder is behind keeps up with events built later
  * the earlier season's public records stay EXPECTED: removing one is a violation (`missing_file`), so a historical file cannot vanish unnoticed
  * the guard still works: an early file of the next season, or a future release of the season on air, still trips the broadcast
"""
import argparse
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'tests'))
from dflsite import broadcast as B, season as S         # noqa: E402
import rehearsal_check as RC                            # noqa: E402
import test_multiseason as TM                           # noqa: E402
from test_multiseason import T0, Rig, run               # noqa: E402


def watch_args(root, public, tmp, **kw):
    d = dict(root=root, public=public, url=None, log=os.path.join(tmp, 'watch.log'), evidence=os.path.join(tmp, 'evidence'), every=1.0, full_every=1.0, min_free_gb=0.0,
             max_public_gb=3.0, guard=os.path.join(tmp, 'guard'))
    d.update(kw)
    return argparse.Namespace(**d)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        B.FSYNC = False
        self.addCleanup(setattr, B, 'FSYNC', True)
        self.r = Rig(self.tmp.name)
        os.makedirs(os.path.join(self.tmp.name, 'guard'))
        open(os.path.join(self.r.store, S.CONTINUOUS), 'w').close()

    def monitor(self, **kw):
        return RC.Watch(watch_args(self.tmp.name, self.r.public, self.tmp.name, **kw))

    def check(self, w, at):
        with mock.patch.object(RC.time, 'time', lambda: T0 + at):
            pos = w.cheap(T0 + at)
            w.full(T0 + at, pos)

    def latch(self):
        p = os.path.join(self.tmp.name, 'guard', 'TRIPPED')
        return json.load(open(p)) if os.path.exists(p) else None

    def violations(self):
        p = os.path.join(self.tmp.name, 'watch.log')
        return [json.loads(x) for x in open(p)] if os.path.exists(p) else []


class Following(Base):
    def test_a_correct_two_season_broadcast_is_never_a_violation(self):
        r = self.r
        r.y1.build(4)
        r.y2.build(4)
        bc = r.started()
        w = self.monitor()
        marks = [100, 2000, r.end1 - 50, r.end1 + 30, r.end1 + 1500, r.end1 + r.y2.plan['total_s'] - 100, r.end1 + r.y2.plan['total_s'] + 50]
        t = 0
        for m in marks:
            run(bc, t, m)
            t = m + 5
            self.check(w, m)
            self.assertIsNone(self.latch(), 'tripped at position %s: %s' % (m, self.latch()))
        self.assertEqual(w.found, 0, [v for v in self.violations() if 'violation' in v])
        self.assertEqual(B.get(os.path.join(r.store, S.CARRY))['season'], 902, 'the hand-over really happened')

    def test_a_monitor_started_while_the_builder_is_behind_keeps_up_with_what_is_built_later(self):
        r = self.r
        r.y1.build(2)
        bc = r.started()
        w = self.monitor()
        run(bc, 0, 400)
        self.check(w, 400)
        r.y1.build(4)                                            # the builder goes on while the clock runs
        run(bc, 405, r.end1 - 50)
        self.check(w, r.end1 - 50)
        self.assertIsNone(self.latch(), self.latch())
        r.y2.build(4)
        run(bc, r.end1 - 45, r.end1 + 1500)
        self.check(w, r.end1 + 1500)
        self.assertIsNone(self.latch(), self.latch())
        self.assertEqual(w.found, 0)

    def test_a_monitor_restarted_inside_the_second_season_agrees_with_the_daemon(self):
        r = self.r
        r.y1.build(4)
        r.y2.build(4)
        bc = r.started()
        run(bc, 0, r.end1 + 1500)
        w = self.monitor()                                       # a fresh process after the hand-over: it reads the season on air from store/carry.json
        self.check(w, r.end1 + 1500)
        self.assertIsNone(self.latch(), self.latch())
        self.assertEqual(w.found, 0)


class StillGuarding(Base):
    def setUp(self):
        super().setUp()
        self.r.y1.build(4)
        self.r.y2.build(4)
        self.bc = self.r.started()

    def test_a_historical_file_that_vanishes_is_a_violation(self):
        r = self.r
        run(self.bc, 0, r.end1 + 1500)
        w = self.monitor()
        self.check(w, r.end1 + 1500)
        self.assertEqual(w.found, 0)
        victim = sorted(k for k in r.tree() if k.startswith('broadcast/live/S901') and k.endswith('end.json'))[0]
        os.unlink(r.pub(victim))
        self.check(w, r.end1 + 1500)
        self.assertTrue(any(v.get('violation') == 'missing_file' for v in self.violations()), 'the vanished season-1 file went unnoticed')

    def test_a_next_season_result_published_early_trips_the_broadcast(self):
        r = self.r
        run(self.bc, 0, 3000)
        w = self.monitor()
        self.check(w, 3000)
        self.assertEqual(w.found, 0)
        early = r.pub('playback', 'games', 'S902-early-result.json')
        os.makedirs(os.path.dirname(early), exist_ok=True)
        open(early, 'w').write('{"result": "season 2, too early"}')
        self.check(w, 3000)
        self.assertEqual(self.latch()['kind'], 'early_or_unknown_file')

    def test_a_future_release_of_the_season_on_air_still_trips(self):
        r = self.r
        run(self.bc, 0, r.end1 + 300)
        w = self.monitor()
        self.check(w, r.end1 + 300)
        self.assertEqual(w.found, 0)
        future = [rel for rel in os.listdir(os.path.join(r.store, 'feed', 'releases')) if rel.startswith('e00008')][0]       # season 2's last event, long before its time
        os.makedirs(r.pub('releases', future), exist_ok=True)
        open(r.pub('releases', future, 'manifest.json'), 'w').write('{"secret_marker": "RESULT-OF-EVENT-8"}')
        self.check(w, r.end1 + 300)
        self.assertEqual(self.latch()['kind'], 'early_or_unknown_file')


class ExpectedView(unittest.TestCase):
    """`Expected` is the earlier seasons' files plus the season on air's, without copying them: it must answer exactly as the plain union would."""

    def test_it_answers_like_the_union(self):
        import random
        import broadcast_season_check as SC
        rnd = random.Random(7)
        names = ['f%03d' % i for i in range(300)]
        for _ in range(50):
            cur, past, other = (set(rnd.sample(names, rnd.randint(0, 120))) for _ in range(3))
            e, u = SC.Expected(cur, past), cur | past
            self.assertEqual(set(e), u)
            self.assertEqual(len(e), len(u))
            self.assertEqual(other - e, other - u)
            self.assertEqual(e - other, u - other)
            self.assertEqual(e | other, u | other)
            self.assertEqual(other | e, other | u)
            self.assertTrue(all((n in e) == (n in u) for n in names))
            self.assertEqual(sorted(e - SC.Expected(other, set())), sorted(u - other))

    def test_the_oracle_keeps_one_set_for_all_finished_seasons_not_one_per_season(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        B.FSYNC = False
        self.addCleanup(setattr, B, 'FSYNC', True)
        r = Rig(tmp.name)
        r.y1.build(4)
        r.y2.build(4)
        bc = r.started()
        run(bc, 0, r.end1 + 100)
        import broadcast_season_check as SC
        o = SC.FollowingOracle(r.store)
        self.assertEqual(o._past_seasons, {901})
        a = o.expected(0)[0]
        self.assertIs(a.past, o.expected(10 ** 9)[0].past, 'every call shares the one set of earlier seasons: nothing is copied per call')


if __name__ == '__main__':
    unittest.main()
