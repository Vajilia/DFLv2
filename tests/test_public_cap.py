"""The public folder's size limit for recurring operation (tools/publiccap.py; docs/UNATTENDED_OPERATION.md, "The public cap").

The rehearsal's fixed 3 GB (daemon `--max-public-gb 3`, monitor `--max-public-gb 3`) is a timer, not a safeguard, once the broadcast goes on: the public folder never shrinks
and grows about 0.95 GB a season. The recurring cap is per season on air and read from store/carry.json, the same number for the daemon, the monitor and the supervisor's health.

Required here:
  * the number: it moves at the hand-over and only then; a ceiling bounds it; a fixed and a per-season cap together are refused
  * the failure it fixes: a correct two-season broadcast that a fixed cap sized for one season stops in season 2 (daemon exit 70, monitor trip) runs on under the per-season cap
  * the safeguard it keeps: a season that publishes far more than its allowance (a runaway) still stops the daemon and trips the monitor, and the ceiling still bounds a long run
  * the supervisor reports the cap in its health (`public_cap_near` at 85%, `public_cap_exceeded` at 100%) before the daemon or the monitor has to stop anything
"""
import argparse
import contextlib
import io
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
import broadcast_run as BR                              # noqa: E402
import publiccap as PC                                  # noqa: E402
import rehearsal_check as RC                            # noqa: E402
from test_monitor_multiseason import watch_args         # noqa: E402
from test_multiseason import T0, Rig, run               # noqa: E402

GB = 1e9


class Numbers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = self.tmp.name

    def carry(self, seasons, current):
        B.FSYNC = False
        B.put(os.path.join(self.store, 'carry.json'), {'format': S.CARRY_FORMAT, 'seasons': seasons, 'season': current})

    def test_one_season_without_a_carry_record(self):
        self.assertEqual(PC.seasons_on_air(self.store), 1)
        self.assertEqual(PC.cap_bytes(self.store, 1.2, 0.25), int(1.45 * GB))

    def test_the_cap_moves_at_the_hand_over_and_only_then(self):
        self.carry([1], 2)
        self.assertEqual(PC.seasons_on_air(self.store), 2)
        self.assertEqual(PC.cap_bytes(self.store, 1.2, 0.25), int(2.65 * GB))
        self.carry([1, 2, 3], 4)
        self.assertEqual(PC.cap_bytes(self.store, 1.2, 0.25), int(5.05 * GB))

    def test_a_ceiling_bounds_the_cap_however_many_seasons(self):
        self.carry(list(range(1, 15)), 15)
        self.assertEqual(PC.cap_bytes(self.store, 1.2, 0.25, ceiling_gb=12), int(12 * GB))
        self.assertGreater(PC.cap_bytes(self.store, 1.2, 0.25), 12 * GB)

    def test_an_unreadable_carry_record_counts_as_one_season_never_as_none(self):
        Path(self.store, 'carry.json').write_text('{not json')
        self.assertEqual(PC.seasons_on_air(self.store), 1)

    def test_a_fixed_and_a_per_season_cap_together_are_refused(self):
        with self.assertRaises(SystemExit):
            BR.main(['run', '--root', self.store, '--public', self.store + '-p', '--max-public-gb', '3', '--max-public-gb-per-season', '1.2'])
        with self.assertRaises(SystemExit):
            BR.main(['run', '--root', self.store, '--public', self.store + '-p', '--max-public-ceiling-gb', '9'])

    def test_tree_bytes_survives_a_file_renamed_away_while_it_is_being_counted(self):
        p = Path(self.store, 'x')
        p.mkdir()
        (p / 'a').write_bytes(b'1234')
        real = os.scandir

        def racing(path):
            it = real(path)
            (p / 'a').rename(p / 'b')                           # the daemon's atomic publication renames a temporary file into place
            return it
        with mock.patch('os.scandir', racing):
            self.assertIn(PC.tree_bytes(str(p)), (0, 4))
        self.assertEqual(PC.tree_bytes(self.store + '/missing'), 0)


class Daemon(unittest.TestCase):
    """The daemon's decision (BR.current_cap) over a real two-season store."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        B.FSYNC = False
        self.addCleanup(setattr, B, 'FSYNC', True)
        self.r = Rig(self.tmp.name)
        self.r.public = tempfile.mkdtemp()                       # the daemon refuses a public folder inside the private root
        self.addCleanup(lambda: __import__('shutil').rmtree(self.r.public, ignore_errors=True))
        self.r.y1.build(4)
        self.r.y2.build(4)
        open(os.path.join(self.r.store, S.CONTINUOUS), 'w').close()
        self.bc = self.r.started()

    def ns(self, **kw):
        d = dict(max_public_gb=0.0, max_public_gb_per_season=0.0, public_base_gb=0.0, max_public_ceiling_gb=0.0)
        d.update(kw)
        return argparse.Namespace(**d)

    def season_bytes(self):
        run(self.bc, 0, self.r.end1 - 5)
        one = PC.tree_bytes(self.r.public)
        run(self.bc, self.r.end1, self.r.end1 + self.r.y2.plan['total_s'] - 10)
        return one, PC.tree_bytes(self.r.public)

    def test_a_fixed_cap_sized_for_one_season_stops_the_second_and_the_per_season_cap_does_not(self):
        one, two = self.season_bytes()
        self.assertGreater(two, one * 1.5, 'the second season really adds its own files')
        fixed = self.ns(max_public_gb=one * 1.3 / GB)
        self.assertLess(BR.current_cap(fixed, self.r.store), two, 'the fixed number is exceeded by a correct second season: the broadcast would stop (exit 70)')
        per = self.ns(max_public_gb_per_season=one * 1.3 / GB)
        self.assertGreater(BR.current_cap(per, self.r.store), two, 'the per-season number has moved with the hand-over and the same broadcast is within it')

    def test_a_runaway_season_still_exceeds_the_per_season_cap(self):
        one, two = self.season_bytes()
        per = self.ns(max_public_gb_per_season=one * 1.3 / GB)
        junk = Path(self.r.public, 'broadcast', 'junk.bin')
        junk.write_bytes(b'x' * int(one * 1.5))                  # a publisher that duplicates a tree: more than a season's whole allowance
        self.assertGreater(PC.tree_bytes(self.r.public), BR.current_cap(per, self.r.store))

    def test_the_daemon_stops_with_exit_70_at_the_per_season_cap_and_logs_why(self):
        run(self.bc, 0, 600)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = BR.main(['run', '--root', self.tmp.name, '--public', self.r.public, '--tick', '0.1', '--max-public-gb-per-season', '0.000001', '--public-base-gb', '0',
                            '--deadline', str(__import__('time').time() + 30)])
        self.assertEqual(code, 70)
        rec = [json.loads(x) for x in err.getvalue().splitlines() if x.startswith('{')]
        cap = [x for x in rec if x.get('cap_exceeded')][0]
        self.assertEqual((cap['seasons_on_air'], cap['cap_bytes']), (1, 1000))
        self.assertTrue(os.path.exists(os.path.join(self.r.public, 'status.json')), 'nothing is deleted when the cap stops the daemon')

    def test_the_monitor_trips_on_the_fixed_cap_in_season_two_but_not_on_the_per_season_cap(self):
        one, two = self.season_bytes()
        guard = os.path.join(self.tmp.name, 'guard')
        os.makedirs(guard)
        at = self.r.end1 + self.r.y2.plan['total_s'] - 10

        def check(**kw):
            w = RC.Watch(watch_args(self.tmp.name, self.r.public, self.tmp.name, **kw))
            with mock.patch.object(RC.time, 'time', lambda: T0 + at):
                w.full(T0 + at, w.cheap(T0 + at))
            return w

        w = check(max_public_gb=one * 1.3 / GB)
        self.assertTrue(os.path.exists(os.path.join(guard, 'TRIPPED')))
        self.assertEqual(json.load(open(os.path.join(guard, 'TRIPPED')))['kind'], 'public_too_large')
        os.unlink(os.path.join(guard, 'TRIPPED'))
        w = check(max_public_gb=one * 1.3 / GB, max_public_gb_per_season=one * 1.3 / GB, public_base_gb=0.0)
        self.assertFalse(os.path.exists(os.path.join(guard, 'TRIPPED')), 'the per-season cap takes precedence in the monitor too: a correct season 2 is within it')
        self.assertEqual(w.found, 0)
        Path(self.r.public, 'broadcast', 'junk.bin').write_bytes(b'x' * int(one * 1.5))
        check(max_public_gb_per_season=one * 1.3 / GB, public_base_gb=0.0)
        self.assertTrue(os.path.exists(os.path.join(guard, 'TRIPPED')), 'a runaway season still trips the guard')


class SupervisorHealth(unittest.TestCase):
    """The supervisor says it before the daemon or the monitor has to stop anything (it never stops or deletes anything itself)."""

    def setUp(self):
        import test_broadcast_supervisor as TS
        self.TS = TS
        self.tmpd = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpd.cleanup)
        B.FSYNC = False
        self.addCleanup(setattr, B, 'FSYNC', True)
        self.h = TS.Harness(self.tmpd.name)

    def sup(self, **kw):
        kw.setdefault('public_gb_per_season', 0.001)               # 1 MB a season, so a synthetic tree can fill it
        kw.setdefault('public_base_gb', 0.0)
        kw.setdefault('public_cap_every_s', 0)
        return self.h.sup(**kw)

    def fill(self, nbytes):
        Path(self.h.public, 'broadcast').mkdir(exist_ok=True)
        Path(self.h.public, 'broadcast', 'fill.bin').write_bytes(b'x' * nbytes)

    def codes(self, hl):
        return {p['code']: p['severity'] for p in hl['problems']}

    def test_under_the_cap_nothing_is_reported_and_the_numbers_are_in_the_health(self):
        self.fill(100_000)
        hl = self.sup().cycle(self.TS.T0)
        self.assertNotIn('public_cap_near', self.codes(hl))
        pc = hl['capacity']['public_cap']
        self.assertEqual((pc['cap_bytes'], pc['seasons_on_air'], pc['public_bytes']), (1_000_000, 1, 100_000))

    def test_near_the_cap_is_a_warning_and_over_it_is_critical(self):
        self.fill(900_000)
        hl = self.sup().cycle(self.TS.T0)
        self.assertEqual(self.codes(hl).get('public_cap_near'), 'warning')
        self.fill(1_100_000)
        hl = self.sup().cycle(self.TS.T0 + 1)
        self.assertEqual(self.codes(hl).get('public_cap_exceeded'), 'critical')
        self.assertNotIn('public_cap_near', self.codes(hl))
        self.assertTrue(Path(self.h.public, 'broadcast', 'fill.bin').exists(), 'the supervisor reports; it never removes')

    def test_the_cap_follows_the_hand_over(self):
        self.fill(1_100_000)
        B.put(os.path.join(self.h.store, 'carry.json'), {'format': S.CARRY_FORMAT, 'seasons': [1], 'season': 2})
        hl = self.sup().cycle(self.TS.T0)
        self.assertEqual(hl['capacity']['public_cap']['cap_bytes'], 2_000_000)
        self.assertNotIn('public_cap_exceeded', self.codes(hl))

    def test_no_public_folder_or_no_policy_means_no_cap_section_and_no_problem(self):
        hl = self.h.sup(public_gb_per_season=None).cycle(self.TS.T0)
        self.assertIsNone(hl['capacity']['public_cap'])


if __name__ == '__main__':
    unittest.main()
