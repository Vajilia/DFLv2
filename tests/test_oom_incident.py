"""A kernel OOM kill in the daemon's service cgroup must leave a persistent incident record, and hold the broadcast, BEFORE systemd's restart can bring a daemon back.

Why. The first candidate relied on the monitor polling memory.events. Under real systemd (OOMPolicy=stop, Restart=on-failure) the unit is stopped and restarted within seconds and its
cgroup is recreated with all counters at zero, so a poll can miss the event altogether: the validation run (stage V2b) found no latch and the daemon carried on. Polling a counter that
the restart resets is not a record. The record is now written by the unit's own lifecycle: `ExecStopPost=` runs after the unit stopped and before `Restart=` starts it again, and systemd
tells it why the unit stopped ($SERVICE_RESULT is `oom-kill` when OOMPolicy=stop ended the unit because ANY process of the cgroup, the daemon or a child, was OOM-killed).

Required here (`broadcast_run.py incident` is what ExecStopPost runs; nothing here needs systemd, the systemd parts are listed in docs/OOM_INCIDENT_RECORD.md):
  * an OOM stop (result oom-kill, OR memory.events oom_kill > 0 whatever the result) writes the latch (TRIPPED + trips.jsonl), the evidence folder and PAUSE; the first latch is kept
  * the VICTIM is explicit: named from the kernel log when it can be read (a child is a child, not "the daemon"), otherwise UNKNOWN; it is never assumed to be the daemon
  * a stop that is not an OOM (clean stop, crash, kill -9) records nothing: those restarts must keep working as before
  * if the latch cannot be written the broadcast is held anyway (PAUSE), the failure is loud (exit status 1, a JSON line), and a daemon whose guard folder is not writable holds too
  * the latch survives what restarts the daemon: a fresh daemon process, a fresh heartbeat, a monitor that starts later, a cgroup whose counters were reset, a host restart
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
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
import test_rehearsal_guard as G
from test_rehearsal_guard import Base, run_daemon

UNIT = 'dfl-broadcast-daemon.service'
DAEMON_PID, CHILD_PID, OTHER_PID = 4100, 4364, 777
FIXTURE = ROOT / 'tests' / 'fixtures' / 'cgroup_v2_synthetic'


def kline(pid, comm='python3', memcg='/system.slice/' + UNIT):
    return ('oom-kill:constraint=CONSTRAINT_MEMCG,nodemask=(null),cpuset=/,mems_allowed=0,oom_memcg=%s,task_memcg=%s,task=%s,pid=%d,uid=994' % (memcg, memcg, comm, pid))


def sha(path):
    return hashlib.sha256(open(path, 'rb').read()).hexdigest()


class Incident(Base):
    def setUp(self):
        super().setUp()
        self.cg = os.path.join(self.tmp, 'cgroup', UNIT)
        os.makedirs(self.cg)
        self.klog = os.path.join(self.tmp, 'kernel.log')
        self.evid = os.path.join(self.guard, 'evidence')

    def events(self, oom_kill=0):
        with open(os.path.join(self.cg, 'memory.events'), 'w') as f:
            f.write('low 0\nhigh 0\nmax %d\noom %d\noom_kill %d\n' % (oom_kill, oom_kill, oom_kill))

    def kernel(self, *lines):
        with open(self.klog, 'w') as f:
            f.write('\n'.join(lines) + '\n')

    def record(self, result='oom-kill', main_pid=DAEMON_PID, kernel_log=True, extra=(), env=None):
        """Run what ExecStopPost runs, with the environment systemd gives it. -> (exit status, [JSON lines on stderr])."""
        argv = ['incident', '--root', self.tmp, '--public', self.pub, '--guard', self.guard, '--cgroup', self.cg]
        if kernel_log:
            argv += ['--kernel-log', self.klog]
        e = {'SERVICE_RESULT': result, 'MAINPID': str(main_pid), 'EXIT_CODE': 'killed', 'EXIT_STATUS': 'TERM'}
        e.update(env or {})
        err = io.StringIO()
        with mock.patch.dict(os.environ, e), contextlib.redirect_stderr(err):
            code = BR.main(argv + list(extra))
        return code, [json.loads(l) for l in err.getvalue().splitlines() if l.startswith('{')]

    def latch(self):
        p = os.path.join(self.guard, 'TRIPPED')
        return json.load(open(p)) if os.path.exists(p) else None

    def evidence(self):
        return sorted(d for d in (os.listdir(self.evid) if os.path.isdir(self.evid) else []) if os.path.exists(os.path.join(self.evid, d, 'evidence.json')))


class Record(Incident):
    def test_an_oom_stop_writes_the_latch_the_evidence_and_the_hold(self):
        self.events(1)
        self.kernel(kline(CHILD_PID))
        code, lines = self.record()
        self.assertEqual(code, 0, lines)
        lt = self.latch()
        self.assertIsNotNone(lt, 'no latch: a restarted daemon would carry on')
        self.assertEqual((lt['kind'], lt['source']), ('oom_kill', 'systemd'))
        self.assertGreater(lt['at'], time.time() - 30)
        self.assertEqual(len(open(os.path.join(self.guard, 'trips.jsonl')).read().splitlines()), 1)
        self.assertTrue(os.path.exists(self.pause), 'the hold must not wait for the next daemon to notice the latch')
        ev = self.evidence()
        self.assertEqual(len(ev), 1, ev)
        self.assertIn('oom_kill', ev[0])
        snap = json.load(open(os.path.join(self.evid, ev[0], 'evidence.json')))
        self.assertEqual(snap['kind'], 'oom_kill')
        self.assertEqual(snap['evidence']['service_result'], 'oom-kill')
        self.assertEqual(snap['evidence']['cgroup_events']['oom_kill'], 1)

    def test_the_latch_is_written_before_the_clock_can_move_and_a_restarted_daemon_stays_held(self):
        self.events(1)
        self.record()
        before = sha(os.path.join(self.guard, 'TRIPPED'))
        self.beat()                                                                    # a restarted daemon sees a perfectly healthy monitor
        code, lines = run_daemon(self.daemon_args())
        self.assertTrue(any(l.get('tripped') and l.get('kind') == 'oom_kill' for l in lines), lines)
        self.assertEqual(sha(os.path.join(self.guard, 'TRIPPED')), before)
        self.assertTrue(os.path.exists(self.pause))
        st = B.Clock(self.rig.store, os.path.join(self.rig.store, '_u')).state()
        self.assertEqual(st['state'], 'paused')
        self.assertLess(B.Clock(self.rig.store, os.path.join(self.rig.store, '_u')).position(time.time()), 1.0, 'the clock must not have advanced')

    def test_a_child_that_was_killed_is_named_as_the_victim_and_the_daemon_is_not(self):
        self.events(1)
        self.kernel(kline(CHILD_PID, 'python3'))
        self.record(main_pid=DAEMON_PID)
        oom = json.load(open(os.path.join(self.evid, self.evidence()[0], 'evidence.json')))['evidence']
        self.assertEqual(oom['victim_status'], 'identified')
        self.assertEqual([(v['pid'], v['comm']) for v in oom['victims']], [(CHILD_PID, 'python3')])
        self.assertIs(oom['daemon_was_victim'], False)
        self.assertEqual(oom['daemon_main_pid'], DAEMON_PID)
        self.assertIn('pid %d (python3)' % CHILD_PID, self.latch()['detail'])
        self.assertIn('not the daemon', self.latch()['detail'])

    def test_the_daemon_itself_killed_is_recorded_as_such(self):
        self.events(1)
        self.kernel(kline(DAEMON_PID, 'python3'))
        self.record(main_pid=DAEMON_PID)
        oom = json.load(open(os.path.join(self.evid, self.evidence()[0], 'evidence.json')))['evidence']
        self.assertIs(oom['daemon_was_victim'], True)

    def test_victims_in_other_cgroups_are_not_this_incidents_victims(self):
        self.events(1)
        self.kernel(kline(OTHER_PID, 'nginx', memcg='/system.slice/caddy.service'), kline(CHILD_PID))
        self.record()
        oom = json.load(open(os.path.join(self.evid, self.evidence()[0], 'evidence.json')))['evidence']
        self.assertEqual([v['pid'] for v in oom['victims']], [CHILD_PID])

    def test_an_unreadable_kernel_log_leaves_the_victim_unknown_never_assumed(self):
        self.events(1)
        code, lines = self.record(kernel_log=False, env={'PATH': '/nonexistent'})       # no journalctl: the service user usually cannot read the kernel log either
        self.assertEqual(code, 0, 'an unknown victim must not stop the incident from being recorded')
        oom = json.load(open(os.path.join(self.evid, self.evidence()[0], 'evidence.json')))['evidence']
        self.assertEqual(oom['victim_status'], 'unknown')
        self.assertEqual(oom['victims'], [])
        self.assertIsNone(oom['daemon_was_victim'])
        self.assertIn('victim unknown', self.latch()['detail'])

    def test_a_kernel_log_that_names_nobody_in_this_cgroup_is_unknown_not_the_daemon(self):
        self.events(1)
        self.kernel(kline(OTHER_PID, 'nginx', memcg='/system.slice/caddy.service'))
        self.record()
        oom = json.load(open(os.path.join(self.evid, self.evidence()[0], 'evidence.json')))['evidence']
        self.assertEqual((oom['victim_status'], oom['victims'], oom['daemon_was_victim']), ('unknown', [], None))

    def test_an_oom_counted_in_the_cgroup_is_recorded_whatever_systemd_called_the_stop(self):
        """OOMPolicy=continue (a deployment that lost the setting), or a stop for another reason that raced the kill: the kernel's own counter is evidence too."""
        self.events(1)
        code, lines = self.record(result='success')
        self.assertEqual(code, 0)
        self.assertEqual(self.latch()['kind'], 'oom_kill')

    def test_the_cgroup_being_gone_does_not_hide_an_oom_stop(self):
        code, lines = self.record(extra=['--cgroup', os.path.join(self.tmp, 'no-such-cgroup')])
        self.assertEqual(code, 0)
        self.assertEqual(self.latch()['kind'], 'oom_kill')

    def test_a_stop_that_is_not_an_oom_records_nothing(self):
        """kill -9, a crash, a clean stop and a deploy restart must go on exactly as before: they are not memory incidents."""
        self.events(0)
        for result, code_, status in (('success', 'exited', '0'), ('signal', 'killed', 'KILL'), ('exit-code', 'exited', '1'), ('timeout', 'killed', 'KILL'), ('watchdog', 'killed', 'ABRT')):
            with self.subTest(result=result):
                code, lines = self.record(result=result, env={'EXIT_CODE': code_, 'EXIT_STATUS': status})
                self.assertEqual(code, 0)
                self.assertIsNone(self.latch())
                self.assertFalse(os.path.exists(self.pause))
                self.assertEqual(self.evidence(), [])
                self.assertFalse(os.path.exists(os.path.join(self.guard, 'trips.jsonl')))

    def test_an_earlier_latch_stays_the_first_and_the_incident_is_still_recorded(self):
        BR.write_trip(self.guard, 'memory_high', 'sustained', 'monitor', time.time() - 5)
        first = sha(os.path.join(self.guard, 'TRIPPED'))
        self.events(1)
        code, lines = self.record()
        self.assertEqual(code, 0)
        self.assertEqual(sha(os.path.join(self.guard, 'TRIPPED')), first, 'the FIRST trip is the one kept')
        kinds = [json.loads(l)['kind'] for l in open(os.path.join(self.guard, 'trips.jsonl')).read().splitlines()]
        self.assertEqual(kinds, ['memory_high', 'oom_kill'], 'the OOM is on record even when a latch already existed')
        self.assertTrue(os.path.exists(self.pause))

    def test_two_recordings_of_one_incident_do_not_replace_the_latch(self):
        self.events(1)
        self.record()
        first = sha(os.path.join(self.guard, 'TRIPPED'))
        self.record()
        self.assertEqual(sha(os.path.join(self.guard, 'TRIPPED')), first)


class WhenRecordingFails(Incident):
    def test_if_the_latch_cannot_be_written_the_broadcast_is_held_and_the_failure_is_loud(self):
        self.events(1)
        with mock.patch.object(BR, 'write_trip', side_effect=OSError(28, 'No space left on device')), mock.patch.object(time, 'sleep'):
            code, lines = self.record()
        self.assertNotEqual(code, 0, 'ExecStopPost must fail visibly, not pretend it recorded the incident')
        self.assertTrue(os.path.exists(self.pause), 'no latch: the hold is the last thing left, and it was made')
        self.assertTrue(any(l.get('incident') == 'unrecorded' for l in lines), lines)

    def test_a_transient_failure_is_retried_before_giving_up(self):
        self.events(1)
        real = BR.write_trip
        calls = []

        def flaky(*a, **k):
            calls.append(1)
            if len(calls) < 3:
                raise OSError(5, 'Input/output error')
            return real(*a, **k)
        with mock.patch.object(BR, 'write_trip', flaky), mock.patch.object(time, 'sleep'):
            code, lines = self.record()
        self.assertEqual(code, 0, lines)
        self.assertEqual(self.latch()['kind'], 'oom_kill')
        self.assertEqual(len(calls), 3)

    def test_neither_the_latch_nor_the_hold_can_be_written_exits_nonzero_without_hanging(self):
        self.events(1)
        broken = os.path.join(self.tmp, 'does', 'not', 'exist')
        t0 = time.time()
        with mock.patch.object(time, 'sleep'):
            code, lines = self.record(extra=['--guard', broken, '--root', broken])
        self.assertNotEqual(code, 0)
        self.assertLess(time.time() - t0, 5.0)
        self.assertTrue(any(l.get('incident') == 'unrecorded' for l in lines), lines)

    def test_a_failure_to_write_the_evidence_folder_does_not_lose_the_latch(self):
        self.events(1)
        with mock.patch.object(BR.os, 'makedirs', side_effect=OSError(30, 'Read-only file system')):
            code, lines = self.record()
        self.assertEqual(code, 0, 'the latch is the record; the evidence folder is a help')
        self.assertEqual(self.latch()['kind'], 'oom_kill')
        self.assertTrue(any(l.get('evidence_error') for l in lines), lines)

    def test_the_latch_is_made_durable_including_its_directory_entry(self):
        """A host that loses power right after the incident must still have the latch: the file AND the guard folder's entry for it are flushed, the folder AFTER the file exists."""
        synced = []
        real = os.fsync
        tripped = os.path.join(self.guard, 'TRIPPED')

        def spy(fd):
            try:
                synced.append((os.readlink('/proc/self/fd/%d' % fd), os.path.exists(tripped)))
            except OSError:
                synced.append(('?', False))
            return real(fd)
        self.events(1)
        with mock.patch.object(BR.os, 'fsync', spy):
            self.record()
        self.assertIn((self.guard, True), synced, 'the guard folder must be fsynced after TRIPPED was created: %s' % synced)

    def test_a_daemon_that_cannot_write_its_guard_folder_holds_the_broadcast(self):
        """If it cannot, an incident could not be recorded either: never run in that state."""
        real = os.access
        with mock.patch.object(BR.os, 'access', lambda p, m, *a, **k: False if p == self.guard else real(p, m, *a, **k)):
            code, lines = run_daemon(self.daemon_args())
        self.assertTrue(os.path.exists(self.pause))
        self.assertTrue(any(l.get('error') == 'guard' for l in lines), lines)


class Persistence(Incident):
    def test_a_fresh_heartbeat_does_not_clear_the_latch(self):
        self.events(1)
        self.record()
        before = sha(os.path.join(self.guard, 'TRIPPED'))
        for age in (0.0, 3600.0):
            self.beat(age)
            g = BR.Guard(self.guard, 120.0, 240.0, time.time() - 1000)
            self.assertTrue(g.step(self.rig.store, time.time()))
        self.assertEqual(sha(os.path.join(self.guard, 'TRIPPED')), before)
        self.assertEqual(BR.main(['resume', '--root', self.tmp, '--guard', self.guard]), 3)

    def test_the_latch_holds_across_daemon_monitor_and_host_restarts(self):
        """Each step is a NEW process with nothing shared but the disk: the OOM recorder (as systemd's ExecStopPost), then a daemon, then a daemon again after a 'host restart' (every
        heartbeat and lock left behind is hours old)."""
        self.events(1)
        env = dict(os.environ, SERVICE_RESULT='oom-kill', MAINPID=str(DAEMON_PID), EXIT_CODE='killed', EXIT_STATUS='TERM')
        py = [sys.executable, '-I', str(ROOT / 'tools' / 'broadcast_run.py')]
        r = subprocess.run(py + ['incident', '--root', self.tmp, '--public', self.pub, '--guard', self.guard, '--cgroup', self.cg], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        before = sha(os.path.join(self.guard, 'TRIPPED'))
        for restart in range(2):
            if restart:
                for name in ('heartbeat',):
                    p = os.path.join(self.guard, name)
                    open(p, 'w').write('{}')
                    os.utime(p, (time.time() - 36000, time.time() - 36000))            # the monitor's last heartbeat from before the outage
                if os.path.exists(self.pause):
                    os.unlink(self.pause)                                              # an operator, or the host's tmp cleaner, removed PAUSE: the latch must bring it back
            d = subprocess.run(py + ['run', '--root', self.tmp, '--public', self.pub, '--tick', '0.2', '--guard', self.guard, '--deadline', str(time.time() + 2)],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(d.returncode, 0, d.stderr)
            self.assertIn('"kind": "oom_kill"', d.stderr)
            self.assertEqual(sha(os.path.join(self.guard, 'TRIPPED')), before, 'restart %d' % restart)
            self.assertTrue(os.path.exists(self.pause), 'restart %d' % restart)
            self.assertLess(B.Clock(self.rig.store, os.path.join(self.rig.store, '_u')).position(time.time()), 1.0, 'restart %d: the clock moved' % restart)


class ResetCounter(unittest.TestCase):
    """The restart recreates the daemon's cgroup: oom_kill goes from 1 back to 0. The monitor reads the new cgroup, and none of that touches the latch."""
    def setUp(self):
        self.t = G.MonitorTrips('test_a_correct_tree_trips_nothing_and_logs_heartbeats')
        self.t.setUp()
        self.addCleanup(self.t.doCleanups)
        self.cg = os.path.join(self.t.tmp, 'cgroup', UNIT)
        shutil.copytree(FIXTURE, self.cg)

    def events(self, n):
        with open(os.path.join(self.cg, 'memory.events'), 'w') as f:
            f.write('low 0\nhigh 0\nmax %d\noom %d\noom_kill %d\n' % (n, n, n))

    def test_a_reset_counter_neither_clears_nor_replaces_the_latch(self):
        w = self.t.watch(150, cgroup=self.cg)
        self.events(1)
        w.memory_check(G.T0 + 150)                                                       # the monitor's first look: the old count is only a baseline
        env = {'SERVICE_RESULT': 'oom-kill', 'MAINPID': '4100', 'EXIT_CODE': 'killed', 'EXIT_STATUS': 'TERM'}
        err = io.StringIO()
        with mock.patch.dict(os.environ, env), contextlib.redirect_stderr(err):
            code = BR.main(['incident', '--root', self.t.tmp, '--public', self.t.pub, '--guard', self.t.guard, '--cgroup', self.cg, '--kernel-log', os.devnull])
        self.assertEqual(code, 0, err.getvalue())
        before = sha(os.path.join(self.t.guard, 'TRIPPED'))
        self.assertEqual(self.t.latch()['kind'], 'oom_kill')
        self.events(0)                                                                   # the new cgroup of the restarted daemon
        for at in (160, 170, 180):
            w.memory_check(G.T0 + at)
        self.t.beat()
        self.assertEqual(sha(os.path.join(self.t.guard, 'TRIPPED')), before, 'a counter that went back to zero is not a release')
        self.events(1)                                                                   # and an OOM in the new incarnation is still seen, but the first latch is kept
        w.memory_check(G.T0 + 190)
        self.assertEqual(sha(os.path.join(self.t.guard, 'TRIPPED')), before)
        self.assertEqual(self.t.latch()['kind'], 'oom_kill')


if __name__ == '__main__':
    unittest.main()
