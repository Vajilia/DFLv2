"""A killed run must not leave its helper processes behind, and nothing may start or read evidence while one is still writing.

Found 2026-11-11/12 on the first real systemd run (harness defect #4): `smoke-2` was killed, but the sampler and the broadcast probe it had started kept running for about 32 minutes. They wrote into
the smoke-2 evidence folder and read the shared store while smoke-3 ran. Teardown did not stop them either (it removes units and folders only). The run lock (`test_vtest_lock.py`) covers commands, not
the helpers a command leaves when it is killed. These tests fail on the code before the fix and say what the fix must do:

  * a helper the harness starts dies when the process that started it is killed (no reliance on cleanup code that a SIGKILL never runs);
  * a helper that survived anyway can be found from /proc by its script path (never by a loose name), and is stopped only after its command line has been read again;
  * `run` and `archive` refuse while a helper exists (under the lock, any helper is an orphan by definition) and say how to stop it;
  * `stop-helpers` and `teardown --yes` stop them; a dry run lists and does nothing; both hold the run lock like every other writer.
"""
import ast
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
VT = ROOT / 'tools' / 'vtest'
sys.path.insert(0, str(VT))

import common as C        # noqa: E402
import vtest as V         # noqa: E402

SPAWNER = (
    "import sys,time,subprocess;sys.path.insert(0,%r);import common as C\n"
    "p=C.Env().popen([sys.executable,'-c','import time\\nwhile True: time.sleep(1)'])\n"
    "print(p.pid,flush=True)\n"
    "time.sleep(120)\n"
)


def alive(pid):
    try:
        with open('/proc/%d/stat' % pid) as f:
            return f.read().rsplit(')', 1)[1].split()[0] != 'Z'
    except (FileNotFoundError, ProcessLookupError):
        return False


def wait_dead(pid, seconds=6):
    end = time.time() + seconds
    while time.time() < end:
        if not alive(pid):
            return True
        time.sleep(0.1)
    return not alive(pid)


def fake_proc(root, entries):
    """entries: {pid: [argv...]} -> a directory shaped like /proc (cmdline files with NULs)."""
    for pid, argv in entries.items():
        d = Path(root) / str(pid)
        d.mkdir(parents=True)
        (d / 'cmdline').write_bytes(b'\0'.join(a.encode() for a in argv) + b'\0')
    (Path(root) / 'self').mkdir()
    (Path(root) / 'cpuinfo').write_text('not a pid\n')
    return str(root)


class HelpersDieWithTheirParent(unittest.TestCase):
    def test_a_helper_started_by_the_harness_dies_when_its_parent_is_killed_outright(self):
        parent = subprocess.Popen([sys.executable, '-I', '-c', SPAWNER % str(VT)], stdout=subprocess.PIPE, text=True)
        try:
            child = int(parent.stdout.readline())
            self.assertTrue(alive(child))
            parent.kill()                                   # SIGKILL: no handler, no finally, no atexit runs
            parent.wait()
            self.assertTrue(wait_dead(child), 'helper pid %d survived the death of the command that started it' % child)
        finally:
            parent.kill()
            parent.wait()
            try:
                os.kill(child, signal.SIGKILL)
            except Exception:
                pass

    def test_a_helper_started_after_its_parent_is_already_gone_does_not_start(self):
        """The window between fork and the death signal being armed: the child must notice the parent changed and exit."""
        src = ("import sys,os;sys.path.insert(0,%r);import common as C\n"
               "C.die_with_parent(expected_parent=1)\n"          # claims a parent pid that is not ours: as if the real parent had already died
               "print('STILL RUNNING')\n") % str(VT)
        r = subprocess.run([sys.executable, '-I', '-c', src], capture_output=True, text=True, timeout=30)
        self.assertNotIn('STILL RUNNING', r.stdout)
        self.assertNotEqual(r.returncode, 0)


class FindingHelpers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.here = str(VT)

    def proc(self, entries):
        return fake_proc(os.path.join(self.tmp.name, 'proc'), entries)

    def test_finds_exactly_the_harness_helper_scripts_by_their_full_path(self):
        proc = self.proc({
            101: ['/usr/bin/python3', self.here + '/sampler.py', '--cgroup', '/sys/fs/cgroup/x', '--out', '/srv/o.jsonl'],
            102: ['python3', self.here + '/broadcast_probe.py', '--store', '/srv/s'],
            103: [sys.executable, self.here + '/loaders.py', 'anon', '--cgroup', 'x'],
            104: ['/usr/bin/python3', '/some/other/place/sampler.py'],          # another program with the same file name
            105: ['vim', self.here + '/sampler.py'],                              # an editor with the file open
            106: ['/usr/bin/python3', self.here + '/vtest.py', 'status'],        # the harness's own command
            107: ['tail', '-f', self.here + '/broadcast_probe.py'],
        })
        found = C.find_helpers(proc_root=proc, here=self.here)
        self.assertEqual(sorted(h['pid'] for h in found), [101, 102, 103])
        self.assertEqual({h['pid']: h['script'] for h in found}[102], 'broadcast_probe.py')

    def test_ignores_non_pid_entries_and_unreadable_ones_and_itself(self):
        proc = self.proc({os.getpid(): ['python3', self.here + '/sampler.py'], 201: ['python3', self.here + '/sampler.py']})
        os.makedirs(os.path.join(proc, '202'))              # a process that vanished: no cmdline file
        found = C.find_helpers(proc_root=proc, here=self.here)
        self.assertEqual([h['pid'] for h in found], [201])

    def test_nothing_running_is_an_empty_list(self):
        self.assertEqual(C.find_helpers(proc_root=self.proc({1: ['init']}), here=self.here), [])


class StoppingHelpers(unittest.TestCase):
    def test_stops_a_real_orphan_helper_and_leaves_other_processes_alone(self):
        tmp = tempfile.mkdtemp()
        out = os.path.join(tmp, 'o.jsonl')
        # the real sampler script, pointed at a cgroup that does not exist: it records "no cgroup" once a second, forever, like the orphan of the incident
        helper = subprocess.Popen([sys.executable, str(VT / 'sampler.py'), '--cgroup', tmp + '/no-such-cgroup', '--out', out, '--tree', str(ROOT)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        bystander = subprocess.Popen([sys.executable, '-c', 'import time\nwhile True: time.sleep(1)'], start_new_session=True)
        try:
            end = time.time() + 10
            while time.time() < end and not any(h['pid'] == helper.pid for h in C.find_helpers()):
                time.sleep(0.1)
            self.assertIn(helper.pid, [h['pid'] for h in C.find_helpers()], 'the real orphan was not found in the real /proc')
            done = C.stop_helpers(grace=5)
            self.assertIn(helper.pid, [d['pid'] for d in done])
            self.assertTrue(wait_dead(helper.pid) or helper.poll() is not None)
            helper.wait(timeout=5)
            self.assertIsNone(bystander.poll(), 'an unrelated process was stopped')
            self.assertEqual([h for h in C.find_helpers() if h['pid'] == helper.pid], [])
        finally:
            for p in (helper, bystander):
                p.kill()
                p.wait()

    def test_the_command_line_is_read_again_before_every_signal(self):
        """A pid can be reused between the scan and the signal: a process whose command line no longer names a helper script is never signalled."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        here = str(VT)
        proc = fake_proc(os.path.join(tmp.name, 'proc'), {301: ['python3', here + '/sampler.py'], 302: ['python3', here + '/sampler.py']})
        sent = []

        def kill(pid, sig):
            sent.append((pid, sig))
            if pid == 302:
                pass

        # between the scan and the signal, pid 302 becomes an unrelated process
        real_find = C.find_helpers
        calls = {'n': 0}

        def find(proc_root=proc, here=here):
            calls['n'] += 1
            res = real_find(proc_root=proc_root, here=here)
            if calls['n'] == 1:
                (Path(proc) / '302' / 'cmdline').write_bytes(b'nginx\0-g\0daemon off;\0')
            return res

        with mock.patch.object(C, 'find_helpers', side_effect=find), mock.patch.object(C, '_process_gone', return_value=True):
            C.stop_helpers(proc_root=proc, here=here, kill=kill, grace=0.2)
        pids = {p for p, _ in sent}
        self.assertIn(301, pids)
        self.assertNotIn(302, pids)


class CommandsMeetHelpers(unittest.TestCase):
    ORPHAN = [{'pid': 4242, 'script': 'sampler.py', 'cmdline': 'python3 /x/sampler.py'}]

    def setUp(self):
        # Every command takes the run lock, which lives in BASE. Without this the tests wrote to the real /srv/dfl-vtest: they passed as root on a
        # workstation and failed with PermissionError on the CI runner (found 2026-11-13, CI run on 516f696), and as root they touched the real lock file.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch = mock.patch.object(V.C, 'BASE', tmp.name)
        patch.start()
        self.addCleanup(patch.stop)

    def run_cmd(self, argv, after=None):
        """after: what a second scan finds (None: the same orphan)."""
        ran = []
        body = lambda *a, **k: ran.append(1) or 0
        out = []
        scans = mock.Mock(side_effect=None, return_value=self.ORPHAN) if after is None else mock.Mock(side_effect=[self.ORPHAN] + [after] * 5)
        with mock.patch.object(V.C, 'find_helpers', scans), \
                mock.patch.object(V.C, 'pin_check', side_effect=lambda *a, **k: (True, [])), \
                mock.patch.object(V, 'constants_check', return_value=[]), \
                mock.patch.object(V.PF, 'preflight', side_effect=lambda *a, **k: ran.append(1) or []), \
                mock.patch.object(V.EV, 'collect_host', side_effect=body), \
                mock.patch('builtins.print', side_effect=lambda *a, **k: out.append(' '.join(str(x) for x in a))):
            rc = V.main(argv)
        return rc, bool(ran), '\n'.join(out)

    def test_run_refuses_while_a_helper_exists_and_does_no_work(self):
        rc, ran, out = self.run_cmd(['run', '--smoke', '--run-id', 'smoke-9'])
        self.assertEqual(rc, 6)
        self.assertFalse(ran)
        self.assertIn('4242', out)
        self.assertIn('stop-helpers', out)

    def test_archive_refuses_while_a_helper_exists_because_it_is_still_writing_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(V.C, 'EVID', d):
                os.makedirs(os.path.join(d, 'smoke-9'))
                rc, ran, out = self.run_cmd(['archive', '--run-id', 'smoke-9'])
        self.assertEqual(rc, 6)
        self.assertFalse(ran)

    def test_stop_helpers_is_a_dry_run_without_yes(self):
        with mock.patch.object(V.C, 'stop_helpers') as stop:
            rc, _, out = self.run_cmd(['stop-helpers'])
        self.assertEqual(rc, 0)
        stop.assert_not_called()
        self.assertIn('4242', out)
        self.assertIn('--yes', out)

    def test_stop_helpers_with_yes_stops_them(self):
        with mock.patch.object(V.C, 'stop_helpers', return_value=[{'pid': 4242, 'result': 'terminated'}]) as stop:
            rc, _, out = self.run_cmd(['stop-helpers', '--yes'], after=[])
        self.assertEqual(rc, 0)
        stop.assert_called_once()

    def test_stop_helpers_reports_failure_when_a_helper_is_still_there_afterwards(self):
        with mock.patch.object(V.C, 'stop_helpers', return_value=[{'pid': 4242, 'result': 'killed'}]):
            rc, _, out = self.run_cmd(['stop-helpers', '--yes'])          # the scan still finds it afterwards
        self.assertEqual(rc, 1)
        self.assertIn('STILL RUNNING', out)

    def test_teardown_with_yes_stops_helpers_too_and_a_dry_run_only_lists_them(self):
        with mock.patch.object(V.RN, 'teardown', return_value=0), mock.patch.object(V.C, 'stop_helpers', return_value=[]) as stop:
            rc, _, out = self.run_cmd(['teardown', '--yes'])
        self.assertEqual(rc, 0)
        stop.assert_called_once()
        with mock.patch.object(V.RN, 'teardown', return_value=0), mock.patch.object(V.C, 'stop_helpers', return_value=[]) as stop:
            rc, _, out = self.run_cmd(['teardown'])
        stop.assert_not_called()
        self.assertIn('4242', out)

    def test_stop_helpers_takes_the_run_lock_like_every_other_writer(self):
        with C.RunLock('another command'), mock.patch('builtins.print'), mock.patch.object(V.C, 'stop_helpers') as stop:
            rc = V.main(['stop-helpers', '--yes'])
        self.assertEqual(rc, 5)
        stop.assert_not_called()


class EveryWriterIsExclusive(unittest.TestCase):
    """Structural: a new writing command cannot be added without the lock. Read-only commands are listed, not guessed."""
    READ_ONLY = {'cmd_pin_check', 'cmd_preflight', 'cmd_plan', 'cmd_status', 'cmd_affected', 'cmd_selftest'}

    def test_every_other_command_function_is_decorated_exclusive(self):
        tree = ast.parse((VT / 'vtest.py').read_text())
        missing = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith('cmd_') and node.name not in self.READ_ONLY:
                if not any(isinstance(d, ast.Name) and d.id == 'exclusive' for d in node.decorator_list):
                    missing.append(node.name)
        self.assertEqual(missing, [])
        names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        self.assertIn('cmd_stop_helpers', names)


if __name__ == '__main__':
    unittest.main()
