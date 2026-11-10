"""The fail-safe for an OOM hook that could not leave a durable incident: a persistent RUN MARKER that is opened before the daemon runs and closed only once the stop is accounted for.

Why. The OOM hook (ExecStopPost=) writes the latch before systemd restarts the daemon. If it CANNOT (the guard folder is unwritable or full), nothing can be written at that moment, and a flag
on a tmpfs would be gone after a reboot. So the dependency is inverted: a marker is written while storage is known to work, at start (ExecStartPre= `broadcast_run.py gate`), and removed only
when the stop has been accounted for: the hook recorded an OOM, or systemd said the stop was not one. A marker still there at the next start means a stop nobody accounted for (the hook failed,
did not finish, or the host died): the gate turns that into a latch (kind unaccounted_stop) before the daemon starts, and REFUSES to start (exit 1) if it cannot. A plain crash or kill -9 is
accounted for (the hook runs, sees a non-OOM result, closes the marker) and does not latch.

This file: the logic, in-process. tests/test_service_sandbox.py runs the same lifecycle as an unprivileged user against real permission faults.
"""
import contextlib
import io
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'tests'))
from dflsite import broadcast as B
import broadcast_run as BR
from test_rehearsal_guard import Base, run_daemon

UNIT = 'dfl-broadcast-daemon.service'


class Lifecycle(Base):
    def setUp(self):
        super().setUp()
        self.marker = os.path.join(self.guard, 'RUN_OPEN')
        self.cg = os.path.join(self.tmp, 'cgroup', UNIT)
        os.makedirs(self.cg)
        self.evid = os.path.join(self.guard, 'evidence')

    def call(self, sub, env=None, extra=()):
        argv = [sub, '--root', self.tmp, '--public', self.pub, '--guard', self.guard] + list(extra)
        if sub == 'incident':
            argv += ['--cgroup', self.cg, '--kernel-log', os.devnull]
        err = io.StringIO()
        with mock.patch.dict(os.environ, env or {}, clear=False), contextlib.redirect_stderr(err):
            code = BR.main(argv)
        return code, [json.loads(l) for l in err.getvalue().splitlines() if l.startswith('{')]

    def gate(self, **kw):
        return self.call('gate', **kw)

    def stop(self, result='success', oom=False, **kw):
        """What ExecStopPost runs, with systemd's environment."""
        with open(os.path.join(self.cg, 'memory.events'), 'w') as f:
            f.write('low 0\nhigh 0\nmax %d\noom %d\noom_kill %d\n' % (oom, oom, oom))
        return self.call('incident', env={'SERVICE_RESULT': result, 'MAINPID': '4100', 'EXIT_CODE': 'exited', 'EXIT_STATUS': '0'}, **kw)

    def latch(self):
        p = os.path.join(self.guard, 'TRIPPED')
        return json.load(open(p)) if os.path.exists(p) else None

    def marker_doc(self):
        return json.load(open(self.marker)) if os.path.exists(self.marker) else None


class Marker(Lifecycle):
    def test_the_gate_opens_a_durable_marker_before_the_daemon_runs(self):
        synced = []
        real = os.fsync

        def spy(fd):
            try:
                synced.append((os.readlink('/proc/self/fd/%d' % fd), os.path.exists(self.marker)))
            except OSError:
                synced.append(('?', False))
            return real(fd)
        with mock.patch.object(BR.os, 'fsync', spy):
            code, lines = self.gate()
        self.assertEqual(code, 0, lines)
        doc = self.marker_doc()
        self.assertIsNotNone(doc, 'no marker: a stop nobody accounts for would be invisible')
        self.assertIn('opened_at', doc)
        self.assertIn('boot_id', doc)
        self.assertIn((self.guard, True), synced, 'the folder entry must be flushed after the marker exists: %s' % synced)
        self.assertIsNone(self.latch())
        self.assertFalse(os.path.exists(self.pause))

    def test_the_marker_is_never_visible_half_written(self):
        """It is made by writing a temporary file and renaming it: a crash between the two leaves the old state, never a torn marker."""
        real = os.replace
        seen = []

        def spy(a, b):
            seen.append((os.path.basename(a), os.path.basename(b), os.path.exists(b)))
            return real(a, b)
        with mock.patch.object(BR.os, 'replace', spy):
            self.gate()
        self.assertEqual([(a != 'RUN_OPEN', b) for a, b, _ in seen], [(True, 'RUN_OPEN')], seen)
        self.assertEqual([n for n in os.listdir(self.guard) if n.startswith('RUN_OPEN') and n != 'RUN_OPEN'], [], 'no temporary file left behind')

    def test_a_stop_that_systemd_calls_clean_or_a_crash_closes_the_marker_and_never_latches(self):
        for result in ('success', 'signal', 'exit-code', 'timeout', 'watchdog', 'core-dump', 'start-limit-hit'):
            with self.subTest(result=result):
                self.assertEqual(self.gate()[0], 0)
                self.assertIsNotNone(self.marker_doc())
                code, lines = self.stop(result=result)
                self.assertEqual(code, 0, lines)
                self.assertIsNone(self.marker_doc(), 'an accounted stop closes the marker')
                self.assertEqual(self.gate()[0], 0)
                self.assertIsNone(self.latch(), 'a restart after %s must carry on, not latch' % result)
                self.assertFalse(os.path.exists(self.pause))
                self.stop(result='success')

    def test_a_recorded_oom_closes_the_marker_because_the_latch_now_carries_it(self):
        self.gate()
        code, lines = self.stop(result='oom-kill', oom=True)
        self.assertEqual(code, 0, lines)
        self.assertEqual(self.latch()['kind'], 'oom_kill')
        self.assertIsNone(self.marker_doc())
        code, lines = self.gate()                                                       # the restart: the latch is already there; the gate adds nothing and the daemon starts held
        self.assertEqual(code, 0, lines)
        self.assertEqual(self.latch()['kind'], 'oom_kill')

    def test_an_oom_that_could_not_be_recorded_leaves_the_marker_open(self):
        self.gate()
        before = self.marker_doc()
        with mock.patch.object(BR, 'write_trip', side_effect=OSError(28, 'No space left on device')), mock.patch.object(time, 'sleep'):
            code, lines = self.stop(result='oom-kill', oom=True)
        self.assertEqual(code, 1)
        self.assertEqual(self.marker_doc(), before, 'the marker is what remembers the stop: it must stay')

    def test_a_non_oom_stop_whose_marker_cannot_be_closed_fails_and_keeps_it(self):
        self.gate()
        with mock.patch.object(BR.os, 'unlink', side_effect=OSError(30, 'Read-only file system')):
            code, lines = self.stop(result='signal')
        self.assertEqual(code, 1)
        self.assertTrue(any(l.get('incident') == 'marker_not_closed' for l in lines), lines)
        self.assertIsNotNone(self.marker_doc())

    def test_a_hook_that_was_not_told_why_the_unit_stopped_does_not_close_the_marker(self):
        """$SERVICE_RESULT is always set by systemd. If it is missing nobody can say the stop was accounted for."""
        self.gate()
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('SERVICE_RESULT', None)
            code, lines = self.call('incident')
        self.assertEqual(code, 0)
        self.assertIsNotNone(self.marker_doc())


class Gate(Lifecycle):
    def left_open(self, boot_id='boot-A', opened=None):
        os.makedirs(self.guard, exist_ok=True)
        with open(self.marker, 'w') as f:
            json.dump({'opened_at': opened or (time.time() - 3600), 'boot_id': boot_id, 'invocation_id': 'inv-1'}, f)

    def test_a_marker_still_there_is_a_stop_nobody_accounted_for_and_it_latches_before_the_daemon_starts(self):
        self.left_open()
        code, lines = self.gate()
        self.assertEqual(code, 0, lines)
        lt = self.latch()
        self.assertEqual((lt['kind'], lt['source']), ('unaccounted_stop', 'systemd'))
        self.assertTrue(os.path.exists(self.pause))
        self.assertEqual(len(open(os.path.join(self.guard, 'trips.jsonl')).read().splitlines()), 1)
        ev = [d for d in os.listdir(self.evid) if os.path.exists(os.path.join(self.evid, d, 'evidence.json'))]
        self.assertEqual(len(ev), 1)
        snap = json.load(open(os.path.join(self.evid, ev[0], 'evidence.json')))['evidence']
        self.assertEqual(snap['previous_run']['invocation_id'], 'inv-1')
        self.assertEqual(self.marker_doc()['opened_at'] > time.time() - 60, True, 'a fresh marker for the run that is about to start')

    def test_the_same_boot_and_a_different_boot_are_told_apart_in_the_record(self):
        for before, now, word in (('boot-A', 'boot-A', 'same boot'), ('boot-A', 'boot-B', 'host restarted')):
            with self.subTest(word=word):
                for n in ('TRIPPED', 'trips.jsonl'):
                    p = os.path.join(self.guard, n)
                    if os.path.exists(p):
                        os.unlink(p)
                self.left_open(boot_id=before)
                with mock.patch.object(BR, 'read_boot_id', lambda: now):
                    self.assertEqual(self.gate()[0], 0)
                self.assertIn(word, self.latch()['detail'])

    def test_if_the_latch_cannot_be_written_the_gate_refuses_to_start_the_daemon(self):
        self.left_open()
        before = self.marker_doc()
        with mock.patch.object(BR, 'write_trip', side_effect=OSError(13, 'Permission denied')), mock.patch.object(time, 'sleep'):
            code, lines = self.gate()
        self.assertEqual(code, 1, 'exit 1 from ExecStartPre= means the daemon does not start')
        self.assertEqual(self.marker_doc(), before, 'and the marker stays, so the next attempt still knows')
        self.assertTrue(any(l.get('gate') == 'refused' for l in lines), lines)

    def test_a_first_start_that_cannot_write_its_marker_is_refused(self):
        """No marker means no way to notice an unaccounted stop later: do not run."""
        with mock.patch.object(BR.os, 'replace', side_effect=OSError(30, 'Read-only file system')):
            code, lines = self.gate()
        self.assertEqual(code, 1)
        self.assertTrue(any(l.get('gate') == 'refused' for l in lines), lines)

    def test_a_marker_that_cannot_be_parsed_still_counts_as_an_unaccounted_stop(self):
        open(self.marker, 'w').write('{"opened_at": 17')
        code, lines = self.gate()
        self.assertEqual(code, 0, lines)
        self.assertEqual(self.latch()['kind'], 'unaccounted_stop')
        self.assertIn('unreadable', self.latch()['detail'])

    def test_an_earlier_latch_stays_the_first_and_the_gate_still_goes_on_to_open_a_marker(self):
        BR.write_trip(self.guard, 'memory_high', 'sustained', 'monitor', time.time() - 9)
        first = open(os.path.join(self.guard, 'TRIPPED')).read()
        self.left_open()
        code, lines = self.gate()
        self.assertEqual(code, 0, lines)
        self.assertEqual(open(os.path.join(self.guard, 'TRIPPED')).read(), first)
        self.assertEqual([json.loads(l)['kind'] for l in open(os.path.join(self.guard, 'trips.jsonl'))], ['memory_high', 'unaccounted_stop'])
        self.assertTrue(os.path.exists(self.pause))

    def test_the_gate_is_idempotent_when_the_unit_is_retried_after_it_refused(self):
        self.left_open()
        with mock.patch.object(BR, 'write_trip', side_effect=OSError(13, 'denied')), mock.patch.object(time, 'sleep'):
            self.assertEqual(self.gate()[0], 1)
        self.assertEqual(self.gate()[0], 0)                                             # storage is back
        self.assertEqual(self.latch()['kind'], 'unaccounted_stop')
        self.assertEqual(self.gate()[0], 0)                                             # and a further start finds a normal marker (the previous run is still "running" in this test: that is the point)
        self.assertEqual(self.latch()['kind'], 'unaccounted_stop')

    def test_a_restarted_daemon_after_the_gate_latched_stays_held(self):
        self.left_open()
        self.gate()
        before = open(os.path.join(self.guard, 'TRIPPED')).read()
        self.beat()
        code, lines = run_daemon(self.daemon_args())
        self.assertTrue(any(l.get('tripped') and l.get('kind') == 'unaccounted_stop' for l in lines), lines)
        self.assertEqual(open(os.path.join(self.guard, 'TRIPPED')).read(), before)
        self.assertLess(B.Clock(self.rig.store, os.path.join(self.rig.store, '_u')).position(time.time()), 1.0)


class WholeOrNothing(Lifecycle):
    def test_a_latch_whose_write_fails_midway_leaves_no_partial_latch(self):
        """A full disk can let the empty file be created and then refuse its contents. A half-written latch cannot be read, released or trusted: the latch is made whole or not at all."""
        real = json.dump

        def torn(obj, f, *a, **k):
            f.write('{"kind": "oom')
            f.flush()
            raise OSError(28, 'No space left on device')
        with mock.patch.object(BR.json, 'dump', torn):
            with self.assertRaises(OSError):
                BR.write_trip(self.guard, 'oom_kill', 'x', 'systemd', time.time())
        self.assertFalse(os.path.exists(os.path.join(self.guard, 'TRIPPED')), 'no partial latch')
        self.assertEqual([n for n in os.listdir(self.guard) if 'TRIPPED' in n], [], 'and no temporary file left behind')
        self.assertTrue(BR.write_trip(self.guard, 'oom_kill', 'x', 'systemd', time.time()), 'the retry makes the whole latch')
        self.assertEqual(json.load(open(os.path.join(self.guard, 'TRIPPED')))['kind'], 'oom_kill')

    def test_two_writers_racing_still_keep_the_first_latch_whole(self):
        a = BR.write_trip(self.guard, 'memory_high', 'first', 'monitor', 1.0)
        b = BR.write_trip(self.guard, 'oom_kill', 'second', 'systemd', 2.0)
        self.assertEqual((a, b), (True, False))
        self.assertEqual(json.load(open(os.path.join(self.guard, 'TRIPPED')))['detail'], 'first')


class FirstStartAndRelease(Lifecycle):
    def test_an_install_with_no_marker_at_all_starts_normally(self):
        self.assertEqual(self.gate()[0], 0)
        self.assertIsNone(self.latch())

    def test_releasing_a_latch_does_not_touch_the_marker(self):
        self.gate()
        before = self.marker_doc()
        BR.write_trip(self.guard, 'memory_high', 'x', 'monitor', time.time())
        os.unlink(os.path.join(self.guard, 'TRIPPED'))                                  # what release does, in effect
        self.assertEqual(self.marker_doc(), before)


if __name__ == '__main__':
    unittest.main()
