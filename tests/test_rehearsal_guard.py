"""The rehearsal's automatic stop (docs/BROADCAST_REHEARSAL.md, 'The guard'): spoiler-class, resource and monitor-failure violations pause the broadcast, keep the
evidence and need an explicit release and resume; the daemon trips itself when the monitor goes silent; and the verdict counts only a complete, clean run as a pass."""
import contextlib
import functools
import http.server
import io
import json
import os
import socketserver
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'tests'))
from dflsite import broadcast as B, season as S
import broadcast_run as BR
import rehearsal_check as RC
from test_season import Rig, run, T0
from test_rehearsal_check import args


def run_daemon(argv):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = BR.main(argv)
    return code, [json.loads(l) for l in err.getvalue().splitlines() if l.startswith('{')]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__('shutil').rmtree(self.tmp, ignore_errors=True))
        B.FSYNC = False
        self.addCleanup(setattr, B, 'FSYNC', True)
        self.rig = Rig(self.tmp)
        self.pub = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__('shutil').rmtree(self.pub, ignore_errors=True))
        self.rig.public = self.pub
        self.guard = os.path.join(self.tmp, 'monitor')
        os.makedirs(self.guard)
        self.pause = os.path.join(self.rig.store, S.PAUSE)

    def daemon_args(self, *extra):
        return ['run', '--root', self.tmp, '--public', self.pub, '--tick', '0.2', '--guard', self.guard, '--deadline', str(time.time() + 1.5), *extra]

    def beat(self, age=0.0):
        p = os.path.join(self.guard, 'heartbeat')
        open(p, 'w').write('{}')
        os.utime(p, (time.time() - age, time.time() - age))


class DaemonGuard(Base):
    def test_a_latch_pauses_the_daemon_and_resume_is_refused_until_released(self):
        self.beat()
        BR.write_trip(self.guard, 'early_or_unknown_file', 'test', 'monitor', time.time())
        code, lines = run_daemon(self.daemon_args())
        self.assertEqual(code, 0)
        self.assertTrue(any(l.get('tripped') and l.get('kind') == 'early_or_unknown_file' for l in lines), lines)
        self.assertTrue(os.path.exists(self.pause))
        st = B.Clock(self.rig.store, os.path.join(self.rig.store, '_u')).state()
        self.assertEqual(st['state'], 'paused')
        # an operator who removes PAUSE without releasing gets it back at the next tick; `resume --guard` does not even try
        os.unlink(self.pause)
        self.assertEqual(BR.main(['resume', '--root', self.tmp, '--guard', self.guard]), 3)
        self.assertEqual(BR.main(['resume', '--root', self.tmp]), 0)                     # the old command without --guard still works (it is not the safe path)
        code, lines = run_daemon(self.daemon_args())
        self.assertTrue(os.path.exists(self.pause), 'the daemon puts PAUSE back while the latch exists')

    def test_the_daemon_trips_itself_when_the_monitor_is_silent(self):
        code, lines = run_daemon(self.daemon_args('--guard-grace', '0.4'))
        trip = json.load(open(os.path.join(self.guard, 'TRIPPED')))
        self.assertEqual((trip['kind'], trip['source']), ('monitor_silent', 'daemon'))
        self.assertTrue(os.path.exists(self.pause))
        self.assertEqual(len(open(os.path.join(self.guard, 'trips.jsonl')).read().splitlines()), 1)
        self.assertTrue(any(l.get('tripped') for l in lines))

    def test_a_stale_heartbeat_trips_but_a_fresh_one_does_not(self):
        self.beat(age=500)
        run_daemon(self.daemon_args('--guard-stale', '120', '--guard-grace', '0.4'))                  # the monitor does not come back within the grace: the daemon trips itself
        self.assertEqual(json.load(open(os.path.join(self.guard, 'TRIPPED')))['kind'], 'monitor_silent')
        os.unlink(os.path.join(self.guard, 'TRIPPED')); os.unlink(self.pause)
        stop = threading.Event()

        def beater():
            while not stop.is_set():
                self.beat(); time.sleep(0.1)
        t = threading.Thread(target=beater); t.start()
        try:
            run_daemon(self.daemon_args('--guard-stale', '120', '--guard-grace', '0.3'))
        finally:
            stop.set(); t.join()
        self.assertFalse(os.path.exists(os.path.join(self.guard, 'TRIPPED')))

    def test_a_heartbeat_left_by_an_earlier_run_does_not_trip_a_daemon_that_has_just_started(self):
        """After a host outage the guard folder still holds the last heartbeat of the monitor that ran before it: hours old when the daemon starts first (or together with the monitor, as at
        boot). That is "the monitor has not written one yet" (the first-start grace), not "the monitor went silent": tripping at once held the broadcast after every reboot
        (tools/startup_check.py, docs/INTEGRATION_CANDIDATE_2026-11-07.md section 15)."""
        self.beat(age=3600)
        code, lines = run_daemon(self.daemon_args('--guard-stale', '120', '--guard-grace', '600'))
        self.assertFalse(os.path.exists(os.path.join(self.guard, 'TRIPPED')), lines)
        self.assertFalse([l for l in lines if l.get('tripped')], lines)                 # (the run ends at its --deadline, which pauses; that is the only pause)

    def test_the_first_start_grace_is_measured_from_the_daemons_own_start(self):
        started = 1000.0
        g = BR.Guard(self.guard, 120.0, 240.0, started)
        hb = os.path.join(self.guard, 'heartbeat')
        open(hb, 'w').write('{}')
        os.utime(hb, (started - 3600, started - 3600))                                  # left by the previous run
        self.assertFalse(g.step(self.rig.store, started + 5), 'just started: wait for the monitor')
        self.assertFalse(g.step(self.rig.store, started + 239), 'still inside the grace')
        self.assertTrue(g.step(self.rig.store, started + 241), 'no monitor by the end of the grace: the daemon trips itself')
        self.assertEqual(json.load(open(os.path.join(self.guard, 'TRIPPED')))['kind'], 'monitor_silent')

    def test_a_heartbeat_written_after_the_start_still_goes_stale(self):
        started = 1000.0
        g = BR.Guard(self.guard, 120.0, 240.0, started)
        hb = os.path.join(self.guard, 'heartbeat')
        open(hb, 'w').write('{}')
        os.utime(hb, (started + 10, started + 10))                                       # the monitor was alive after the daemon started
        self.assertFalse(g.step(self.rig.store, started + 100))
        self.assertTrue(g.step(self.rig.store, started + 10 + 121), 'it then stopped: the rule is unchanged')

    def test_the_first_start_grace_never_overrides_an_existing_latch(self):
        """The grace only decides whether the MONITOR counts as silent. A trip latch (from the monitor, or from an earlier daemon) holds the broadcast whatever the heartbeat looks like:
        old from before the start, absent, fresh, or inside the grace."""
        started = 1000.0
        for beat_mtime in (started - 3600, None, started + 1):
            for now in (started + 1, started + 100, started + 239, started + 500):
                guard = os.path.join(self.tmp, 'g-%s-%d' % (beat_mtime, now))
                os.makedirs(guard)
                BR.write_trip(guard, 'early_or_unknown_file', 'from an earlier run', 'monitor', started - 7200)        # the latch predates this daemon
                latch = open(os.path.join(guard, 'TRIPPED')).read()
                trips = open(os.path.join(guard, 'trips.jsonl')).read()
                if beat_mtime is not None:
                    hb = os.path.join(guard, 'heartbeat')
                    open(hb, 'w').write('{}')
                    os.utime(hb, (beat_mtime, beat_mtime))
                if os.path.exists(self.pause):
                    os.unlink(self.pause)
                g = BR.Guard(guard, 120.0, 240.0, started)
                self.assertTrue(g.step(self.rig.store, now), (beat_mtime, now))
                self.assertTrue(os.path.exists(self.pause), (beat_mtime, now))
                self.assertEqual(open(os.path.join(guard, 'TRIPPED')).read(), latch, 'the latch is neither replaced nor cleared')
                self.assertEqual(open(os.path.join(guard, 'trips.jsonl')).read(), trips, 'and no second trip is recorded for it')

    def test_a_monitor_that_starts_after_a_latch_does_not_clear_it(self):
        started = 1000.0
        BR.write_trip(self.guard, 'monitor_silent', 'no monitor within the grace', 'daemon', started + 241)
        g = BR.Guard(self.guard, 120.0, 240.0, started)
        self.assertTrue(g.step(self.rig.store, started + 242))
        self.beat()                                                                    # the monitor comes back and writes a fresh heartbeat
        os.utime(os.path.join(self.guard, 'heartbeat'), (started + 250, started + 250))
        self.assertTrue(g.step(self.rig.store, started + 251), 'a heartbeat is not a release')
        self.assertEqual(json.load(open(os.path.join(self.guard, 'TRIPPED')))['kind'], 'monitor_silent')
        self.assertEqual(BR.main(['resume', '--root', self.tmp, '--guard', self.guard]), 3, 'resume is refused while the latch exists')

    def test_a_daemon_started_over_a_latch_and_an_old_heartbeat_holds_at_once(self):
        BR.write_trip(self.guard, 'private_exposed', 'earlier', 'monitor', time.time() - 7200)
        self.beat(age=3600)
        code, lines = run_daemon(self.daemon_args('--guard-stale', '120', '--guard-grace', '600'))
        self.assertTrue(any(l.get('tripped') and l.get('kind') == 'private_exposed' for l in lines), lines)
        self.assertEqual(json.load(open(os.path.join(self.guard, 'TRIPPED')))['kind'], 'private_exposed')
        self.assertEqual(len(open(os.path.join(self.guard, 'trips.jsonl')).read().splitlines()), 1)

    def test_an_unreadable_guard_folder_holds_the_broadcast(self):
        with mock.patch.object(BR.Guard, 'step', side_effect=PermissionError('denied')):
            code, lines = run_daemon(self.daemon_args())
        self.assertTrue(os.path.exists(self.pause))
        self.assertTrue(any(l.get('error') == 'guard' for l in lines))


class MonitorTrips(Base):
    def setUp(self):
        super().setUp()
        self.bc = self.rig.daemon()
        self.bc.clock.start(T0, 0.0, B.LEASE_S, ready_until=0.0)
        self.ev = os.path.join(self.tmp, 'evidence')

    def watch(self, at, **kw):
        run(self.bc, T0, T0 + at)
        kw.setdefault('guard', self.guard)
        a = args(self.tmp, self.pub, self.tmp, evidence=self.ev, **kw)
        return RC.Watch(a)

    def check(self, w, at):
        with mock.patch.object(RC.time, 'time', lambda: T0 + at):
            pos = w.cheap(T0 + at)
            w.full(T0 + at, pos)

    def latch(self):
        p = os.path.join(self.guard, 'TRIPPED')
        return json.load(open(p)) if os.path.exists(p) else None

    def lines(self):
        return [json.loads(x) for x in open(os.path.join(self.tmp, 'watch.log'))]

    def test_a_correct_tree_trips_nothing_and_logs_heartbeats(self):
        w = self.watch(150)
        self.check(w, 150)
        self.assertIsNone(self.latch())
        self.assertEqual(w.found, 0)
        self.assertTrue(any(l.get('heartbeat') for l in self.lines()))
        self.assertFalse(any('violation' in l for l in self.lines()))

    def test_an_early_file_trips_the_broadcast_and_keeps_the_evidence(self):
        w = self.watch(150)
        early = os.path.join(self.pub, 'playback', 'games', 'EARLY-ONE.json')
        os.makedirs(os.path.dirname(early), exist_ok=True)
        open(early, 'w').write('{"result":"too early"}')
        self.check(w, 150)
        self.assertEqual(self.latch()['kind'], 'early_or_unknown_file')
        dirs = [d for d in os.listdir(self.ev) if d.startswith('trip-')]
        self.assertEqual(len(dirs), 1)
        d = os.path.join(self.ev, dirs[0])
        ev = json.load(open(os.path.join(d, 'evidence.json')))
        self.assertIn('playback/games/EARLY-ONE.json', json.dumps(ev))
        import gzip
        self.assertIn('playback/games/EARLY-ONE.json', gzip.open(os.path.join(d, 'public_listing.txt.gz'), 'rt').read())
        self.assertIn('store_clock', ev)
        # the daemon, given the latch, holds; a second violation adds a line but does not replace the first snapshot
        self.check(w, 151)
        self.assertEqual(len([d for d in os.listdir(self.ev) if d.startswith('trip-')]), 1)
        self.assertGreaterEqual(len(open(os.path.join(self.guard, 'trips.jsonl')).read().splitlines()), 2)
        self.assertTrue(os.path.exists(early), 'nothing is deleted')
        code, _ = run_daemon(['run', '--root', self.tmp, '--public', self.pub, '--tick', '0.2', '--guard', self.guard, '--deadline', str(time.time() + 1.0)])
        self.assertTrue(os.path.exists(self.pause))

    def test_resource_violations_trip(self):
        w = self.watch(150, min_free_gb=10 ** 9)                                         # no disk has this much free
        self.check(w, 150)
        self.assertEqual(self.latch()['kind'], 'disk_low')
        os.unlink(os.path.join(self.guard, 'TRIPPED'))
        w = RC.Watch(args(self.tmp, self.pub, self.tmp, evidence=self.ev, guard=self.guard, max_public_gb=1e-9))
        self.check(w, 150)
        self.assertEqual(self.latch()['kind'], 'public_too_large')
        os.unlink(os.path.join(self.guard, 'TRIPPED'))
        cg = os.path.join(self.tmp, 'cg'); os.makedirs(cg)
        open(os.path.join(cg, 'memory.max'), 'w').write('600000000\n')
        open(os.path.join(cg, 'memory.current'), 'w').write('300000000\n')
        w = RC.Watch(args(self.tmp, self.pub, self.tmp, evidence=self.ev, guard=self.guard, cgroup=cg))
        self.check(w, 150)
        self.assertIsNone(self.latch())                                                  # half the limit: fine
        # no memory.stat here, so nothing can be discounted (the conservative reading); ONE high sample is no longer a trip (tests/test_memory_rule.py has the rule), a held one is
        open(os.path.join(cg, 'memory.current'), 'w').write('560000000\n')
        self.check(w, 150)
        self.assertIsNone(self.latch())
        for at in range(160, 230, 10):
            w.memory_check(T0 + at)
        self.assertEqual(self.latch()['kind'], 'memory_high')

    def test_the_first_seconds_after_the_clock_starts_are_not_a_missing_file(self):
        # the live drill showed it: the monitor can read the new clock a moment before the daemon's first tick has published anything
        a = args(self.tmp, self.pub, self.tmp, evidence=self.ev, guard=self.guard)
        w = RC.Watch(a)
        with mock.patch.object(RC.time, 'time', lambda: T0 + 0.2):
            pos = w.cheap(T0 + 0.2)
            w.full(T0 + 0.2, pos)
        self.assertEqual(w.found, 0, self.lines())
        self.assertIsNone(self.latch())

    def test_a_violation_that_is_not_spoiler_resource_or_monitor_is_logged_but_does_not_stop_the_broadcast(self):
        w = self.watch(150)
        w.violation('stale_tmp', 'x')
        w.violation('missing_file', 'x')
        self.assertIsNone(self.latch())
        self.assertEqual(w.found, 2)

    def test_a_crashing_check_stops_the_broadcast(self):
        w = self.watch(150)
        w.stop.clear()

        def boom(*a, **k):
            raise RuntimeError('boom')
        t = None
        with mock.patch.object(RC.signal, 'signal', lambda *a: None), mock.patch.object(w, 'cheap', boom):
            t = threading.Thread(target=w.run); t.start()
            time.sleep(0.8); w.stop.set(); t.join(5)
        self.assertEqual(self.latch()['kind'], 'monitor_error')
        self.assertIn('boom', json.dumps(self.latch()))

    def test_a_monitor_that_cannot_see_the_site_stops_the_broadcast(self):
        w = self.watch(150, url='http://127.0.0.1:1', every=0.1)
        with mock.patch.object(RC.signal, 'signal', lambda *a: None):
            t = threading.Thread(target=w.run); t.start()
            deadline = time.time() + 10
            while time.time() < deadline and not self.latch():
                time.sleep(0.1)
            w.stop.set(); t.join(5)
        self.assertEqual(self.latch()['kind'], 'monitor_blind')

    def test_a_monitor_that_cannot_write_its_latch_exits(self):
        w = self.watch(150)
        with mock.patch.object(BR, 'write_trip', side_effect=OSError('read-only')):
            with self.assertRaises(SystemExit) as cm:
                w.violation('early_or_unknown_file', 'x')
        self.assertEqual(cm.exception.code, 2)

    def test_the_heartbeat_runs_while_the_monitor_works_and_stops_when_it_hangs(self):
        w = self.watch(150)
        hb = os.path.join(self.guard, 'heartbeat')
        with mock.patch.object(RC, 'HEARTBEAT_EVERY_S', 0.05), mock.patch.object(RC, 'HANG_S', 0.4):
            w.beat()
            t = threading.Thread(target=w.beat_loop, daemon=True); t.start()
            for _ in range(8):
                time.sleep(0.1); w.progress()
            self.assertGreater(os.path.getmtime(hb), time.time() - 0.3)                   # alive
            time.sleep(1.0)                                                               # no progress: hung
            self.assertLess(os.path.getmtime(hb), time.time() - 0.5)
            w.stop.set(); t.join(2)


class Release(Base):
    def test_release_needs_a_reason_and_a_live_monitor_and_keeps_the_evidence(self):
        BR.write_trip(self.guard, 'disk_low', 'x', 'monitor', time.time())
        ns = lambda **kw: __import__('argparse').Namespace(guard=self.guard, evidence=os.path.join(self.tmp, 'ev'), **kw)
        self.assertEqual(RC.release(ns(reason='short')), 2)
        self.assertEqual(RC.release(ns(reason='freed disk space, checked listing')), 4)       # no heartbeat
        self.beat()
        self.assertEqual(RC.release(ns(reason='freed disk space, checked listing')), 0)
        self.assertFalse(os.path.exists(os.path.join(self.guard, 'TRIPPED')))
        kept = os.listdir(os.path.join(self.tmp, 'ev', 'released'))
        self.assertEqual(len(kept), 1)
        rec = json.load(open(os.path.join(self.tmp, 'ev', 'released', kept[0])))
        self.assertEqual((rec['kind'], rec['released_reason']), ('disk_low', 'freed disk space, checked listing'))
        self.assertTrue(os.path.exists(os.path.join(self.guard, 'trips.jsonl')), 'the trip history stays')
        self.assertEqual(BR.main(['resume', '--root', self.tmp, '--guard', self.guard]), 0)


class Verdict(Base):
    def setUp(self):
        super().setUp()
        self.log = os.path.join(self.tmp, 'watch.log')

    def finish(self, to):
        bc = self.rig.daemon()
        bc.clock.start(T0, 0.0, B.LEASE_S, ready_until=0.0)
        run(bc, T0, T0 + to)
        return to

    def verdict(self, at, events=3, games=3):
        a = __import__('argparse').Namespace(root=self.tmp, public=self.pub, log=self.log, guard=self.guard, expect_events=events, expect_games=games)
        buf = io.StringIO()
        with mock.patch.object(RC.time, 'time', lambda: T0 + at), contextlib.redirect_stdout(buf):
            code = RC.verdict(a)
        return code, buf.getvalue()

    def heartbeats(self, n=3, extra=()):
        with open(self.log, 'w') as f:
            f.write(json.dumps({'start': True, 'at': T0}) + '\n')
            for i in range(n):
                f.write(json.dumps({'heartbeat': True, 'ok': True, 'at': T0 + i}) + '\n')
            for e in extra:
                f.write(json.dumps(e) + '\n')

    def test_a_complete_clean_run_passes_and_heartbeats_are_not_violations(self):
        end = int(RC.done_at(RC.make_oracle(self.rig.store))) + 5
        self.finish(end)
        self.heartbeats()
        code, out = self.verdict(end)
        self.assertEqual(code, 0, out)
        self.assertIn('RESULT: COMPLETE', out)
        self.assertIn('violation entries in the log: 0', out)

    def test_stopping_before_the_end_is_incomplete_not_a_pass(self):
        self.finish(900)
        self.heartbeats()
        code, out = self.verdict(900)
        self.assertEqual(code, 2, out)
        self.assertIn('INCOMPLETE', out)
        self.assertNotIn('RESULT: COMPLETE', out)

    def test_any_violation_or_trip_fails_even_a_finished_run(self):
        end = int(RC.done_at(RC.make_oracle(self.rig.store))) + 5
        self.finish(end)
        self.heartbeats(extra=[{'violation': 'stale_tmp', 'detail': 'x', 'at': T0}])
        code, out = self.verdict(end)
        self.assertEqual(code, 1, out)
        self.assertIn('FAILED', out)
        self.heartbeats()
        BR.write_trip(self.guard, 'monitor_silent', 'x', 'daemon', T0)
        os.unlink(os.path.join(self.guard, 'TRIPPED'))                                   # even a released trip counts
        code, out = self.verdict(end)
        self.assertEqual(code, 1, out)

    def test_a_planned_monitor_stop_drill_is_excluded_but_nothing_else_can_be(self):
        end = int(RC.done_at(RC.make_oracle(self.rig.store))) + 5
        self.finish(end)
        self.heartbeats()
        ns = lambda drill, reason='drill: stopped the monitor on purpose': __import__('argparse').Namespace(guard=self.guard, evidence=os.path.join(self.tmp, 'ev'), reason=reason, drill=drill)
        self.beat()
        BR.write_trip(self.guard, 'early_or_unknown_file', 'x', 'monitor', time.time())
        self.assertEqual(RC.release(ns(True)), 2)                                        # a spoiler trip is never a drill
        os.unlink(os.path.join(self.guard, 'TRIPPED'))
        os.unlink(os.path.join(self.guard, 'trips.jsonl'))
        BR.write_trip(self.guard, 'monitor_silent', 'x', 'daemon', time.time())
        self.assertEqual(RC.release(ns(True)), 0)
        code, out = self.verdict(end)
        self.assertEqual(code, 0, out)
        self.assertIn('1 planned monitor-stop drill', out)
        # the same trip released without --drill counts against the run
        os.unlink(os.path.join(self.guard, 'drills.jsonl'))
        code, out = self.verdict(end)
        self.assertEqual(code, 1, out)

    def test_a_wrong_season_size_or_a_wrong_file_is_not_a_pass(self):
        end = int(RC.done_at(RC.make_oracle(self.rig.store))) + 5
        self.finish(end)
        self.heartbeats()
        code, out = self.verdict(end, events=47, games=400)
        self.assertNotEqual(code, 0)
        os.unlink(os.path.join(self.pub, 'status.json'))
        code, out = self.verdict(end)
        self.assertNotEqual(code, 0, out)


if __name__ == '__main__':
    unittest.main()
