"""The harness takes an exclusive, no-wait lock before any command that changes the machine or reads evidence that a run is writing.

Found 2026-11-11 on the first real systemd run: smoke-2 was started in the background; a second `vtest.py run --run-id smoke-2` was then typed in the foreground while the first was in V1.
Both processes wrote the same evidence folder and drove the same units and probe folders; the second one's V0 "failed" durability checks that pass on a clean run (smoke-3), and the run's
records were overwritten by the other process. The harness had nothing to stop two commands at once. These tests fail without the lock and say what the lock must do:
refuse at once (never wait), name the holder, release when the holder dies, and cover every command that writes.
"""
import os
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
import render as RN       # noqa: E402
import vtest as V         # noqa: E402

HOLDER = (
    "import sys,time;sys.path.insert(0,%r);import common as C;"
    "C.BASE=%r;l=C.RunLock('holder test');l.__enter__();print('held',flush=True);time.sleep(%s)"
)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__('shutil').rmtree(self.tmp, ignore_errors=True))
        p = mock.patch.object(C, 'BASE', self.tmp + '/srv/dfl-vtest')
        p.start()
        self.addCleanup(p.stop)


class Lock(Base):
    def test_a_second_holder_is_refused_at_once_and_told_who_holds_it(self):
        with C.RunLock('run --run-id smoke-2'):
            t0 = time.time()
            with self.assertRaises(C.Busy) as cm:
                C.RunLock('run --run-id smoke-2').__enter__()
            self.assertLess(time.time() - t0, 1.0, 'the lock must never wait')
            msg = str(cm.exception)
            self.assertIn(str(os.getpid()), msg)
            self.assertIn('run --run-id smoke-2', msg)
            self.assertIn('does not wait', msg)

    def test_the_lock_is_free_again_after_release(self):
        with C.RunLock('first'):
            pass
        with C.RunLock('second'):
            pass

    def test_the_lock_dies_with_its_holder_even_if_the_holder_is_killed(self):
        proc = subprocess.Popen([sys.executable, '-I', '-c', HOLDER % (str(VT), C.BASE, 60)], stdout=subprocess.PIPE, text=True)
        self.addCleanup(proc.kill)
        self.assertEqual(proc.stdout.readline().strip(), 'held')
        with self.assertRaises(C.Busy):
            C.RunLock('while the holder lives').__enter__()
        proc.kill()
        proc.wait()
        with C.RunLock('after the holder was killed'):
            pass

    def test_a_process_in_another_process_is_refused_without_waiting(self):
        proc = subprocess.Popen([sys.executable, '-I', '-c', HOLDER % (str(VT), C.BASE, 30)], stdout=subprocess.PIPE, text=True)
        self.addCleanup(proc.kill)
        self.assertEqual(proc.stdout.readline().strip(), 'held')
        t0 = time.time()
        r = subprocess.run([sys.executable, '-I', '-c', "import sys;sys.path.insert(0,%r);import common as C;C.BASE=%r\ntry:\n C.RunLock('second').__enter__()\nexcept C.Busy as e:\n print('BUSY',e)" % (str(VT), C.BASE)],
                           capture_output=True, text=True)
        self.assertIn('BUSY', r.stdout)
        self.assertLess(time.time() - t0, 5.0)

    def test_the_lock_descriptor_is_not_inherited_by_child_processes(self):
        """A stage's helper processes must not keep the lock alive after the command that holds it has exited."""
        with C.RunLock('x') as lock:
            self.assertFalse(os.get_inheritable(lock.fd))

    def test_the_lock_file_is_inside_the_harness_territory_and_survives_teardown(self):
        path = C.RunLock.path()
        self.assertTrue(C.allowed_path(path), path)
        removed = (RN.SRC_BASE, RN.ETC, RN.ROOT_DIR, RN.WEB_DIR, RN.GUARD, RN.LOADDIR, RN.STATE, RN.PROBE, RN.EVID)
        self.assertFalse([d for d in removed if path.startswith(d + '/')], 'teardown removes this folder, and with it the lock file: %s' % path)


class Commands(Base):
    """Every command that writes, or reads what a run is writing, refuses while the lock is held, before it does anything else."""
    WRITERS = (['install'], ['run', '--smoke', '--run-id', 'smoke-2'], ['teardown', '--yes'], ['start-units'], ['archive', '--run-id', 'smoke-2'])
    READERS = (['pin-check'], ['status'], ['plan'], ['affected', '--recorded', 'HEAD'])

    def refused(self, argv):
        """-> (return code, the body ran)"""
        ran = []
        body = lambda *a, **k: ran.append(1) or 0
        with C.RunLock('another command'), \
                mock.patch.object(V.C, 'pin_check', side_effect=lambda *a, **k: ran.append(1) or (True, [])), \
                mock.patch.object(V.RN, 'install', side_effect=body), mock.patch.object(V.RN, 'teardown', side_effect=body), \
                mock.patch.object(V, 'constants_check', side_effect=lambda *a, **k: ran.append(1) or []), \
                mock.patch.object(V.PF, 'preflight', side_effect=lambda *a, **k: ran.append(1) or []), \
                mock.patch('builtins.print'):
            rc = V.main(argv)
        return rc, bool(ran)

    def test_every_writing_command_refuses_with_its_own_exit_code_and_does_nothing(self):
        for argv in self.WRITERS:
            rc, ran = self.refused(argv)
            self.assertEqual(rc, 5, argv)
            self.assertFalse(ran, '%s did work before checking the lock' % argv)

    def test_the_two_commands_of_the_incident_are_the_same_run_id_twice(self):
        with C.RunLock('run --smoke --run-id smoke-2'):
            with mock.patch('builtins.print') as pr:
                rc = V.main(['run', '--smoke', '--run-id', 'smoke-2'])
        self.assertEqual(rc, 5)
        self.assertIn('run --smoke --run-id smoke-2', ' '.join(str(c) for c in pr.call_args_list))

    def test_read_only_commands_do_not_take_or_need_the_lock(self):
        for argv in self.READERS:
            with C.RunLock('another command'), mock.patch('builtins.print'):
                rc = V.main(argv)
            self.assertNotEqual(rc, 5, argv)

    def test_a_command_releases_the_lock_when_it_ends(self):
        with mock.patch.object(V.RN, 'teardown', return_value=0), mock.patch('builtins.print'):
            self.assertEqual(V.main(['teardown']), 0)
        with C.RunLock('free afterwards'):
            pass


if __name__ == '__main__':
    unittest.main()
