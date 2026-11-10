"""The OOM-incident lifecycle (gate, hook, daemon) run the way the service runs it: as an UNPRIVILEGED user, as separate processes, with `python3 -I`, against real filesystem faults.

What the unit's sandbox does (User=dflv2, ReadWritePaths= the store, the public folder and the guard folder) is modelled here with plain Unix ownership: the daemon's folders belong to an
unprivileged user, the program tree is read-only to it, and a "fault" is the guard and store folders turning root-owned and read-only (every write fails with EACCES, as the kernel does it),
plus a genuinely full filesystem (ENOSPC) in a private mount namespace. NOT modelled, and still unverified: systemd itself (ordering of ExecStopPost= and Restart=, the environment systemd
passes, ProtectSystem=/PrivateTmp=, TimeoutStopSec=). See docs/OOM_INCIDENT_RECORD.md.

Skipped when not root, when `setpriv` is missing or when the nobody user cannot be assumed.
"""
import json
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'tests'))
from dflsite import broadcast as B
from test_rehearsal_guard import Base

try:
    NOBODY = pwd.getpwnam('nobody')
    UID, GID = NOBODY.pw_uid, NOBODY.pw_gid
except KeyError:
    UID = GID = None


def sandbox_available():
    if os.geteuid() != 0 or UID is None or not shutil.which('setpriv'):
        return False
    r = subprocess.run(['setpriv', '--reuid', str(UID), '--regid', str(GID), '--clear-groups', 'id', '-u'], capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() == str(UID)


@unittest.skipUnless(sandbox_available(), 'needs root and setpriv to run as an unprivileged user')
class AsService(Base):
    def setUp(self):
        super().setUp()
        os.chmod(self.tmp, 0o755)
        self.src = tempfile.mkdtemp(prefix='dflsrc')                                    # /opt/dflv2-src/<commit>: root-owned, read-only to the service user
        self.addCleanup(shutil.rmtree, self.src, True)
        for d in ('src', 'tools'):
            shutil.copytree(ROOT / d, os.path.join(self.src, d), ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        subprocess.run(['chmod', '-R', 'a+rX,go-w', self.src], check=True)
        os.chmod(self.src, 0o755)
        self.cg = os.path.join(self.tmp, 'cgroup', 'dfl-broadcast-daemon.service')
        os.makedirs(self.cg)
        with open(os.path.join(self.cg, 'memory.events'), 'w') as f:
            f.write('low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\n')
        self.marker = os.path.join(self.guard, 'RUN_OPEN')
        self.give_to_service()
        self.healthy()

    def give_to_service(self):
        for dirpath, dirs, files in os.walk(self.tmp):
            for n in dirs + files:
                p = os.path.join(dirpath, n)
                if not os.path.islink(p):
                    os.chown(p, UID, GID)
        os.chown(self.tmp, UID, GID)
        os.chown(self.pub, UID, GID)
        os.chmod(self.pub, 0o755)

    def healthy(self):
        for d in (self.guard, self.rig.store):
            os.chown(d, UID, GID)
            os.chmod(d, 0o755)

    def fault(self):
        """The guard folder and the store stop accepting writes from the service user (permission denied, as the kernel says it)."""
        for d in (self.guard, self.rig.store):
            os.chown(d, 0, 0)
            os.chmod(d, 0o555)

    def service(self, sub, env=None, extra=(), timeout=90, public=True):
        env = dict(env or {})
        cmd = ['setpriv', '--reuid', str(UID), '--regid', str(GID), '--clear-groups', 'env', '-i', 'PATH=/usr/bin:/bin', 'HOME=/nonexistent'] + ['%s=%s' % kv for kv in env.items()]
        cmd += ['python3', '-I', os.path.join(self.src, 'tools', 'broadcast_run.py'), sub, '--root', self.tmp] + (['--public', self.pub] if public else []) + ['--guard', self.guard] + list(extra)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        lines = [json.loads(l) for l in r.stderr.splitlines() if l.startswith('{')]
        return r.returncode, lines, r.stderr

    def gate(self):
        return self.service('gate')

    def stop(self, result='success', oom=False):
        with open(os.path.join(self.cg, 'memory.events'), 'w') as f:
            f.write('low 0\nhigh 0\nmax %d\noom %d\noom_kill %d\n' % (oom, oom, oom))
        os.chown(os.path.join(self.cg, 'memory.events'), UID, GID)
        return self.service('incident', env={'SERVICE_RESULT': result, 'MAINPID': '4100', 'EXIT_CODE': 'killed', 'EXIT_STATUS': 'KILL', 'INVOCATION_ID': 'inv-test'},
                            extra=['--cgroup', self.cg, '--kernel-log', os.devnull])

    def daemon(self, seconds=3):
        return self.service('run', extra=['--tick', '0.2', '--deadline', str(time.time() + seconds)])

    def latch(self):
        p = os.path.join(self.guard, 'TRIPPED')
        return json.load(open(p)) if os.path.exists(p) else None

    def clock_position(self):
        return B.Clock(self.rig.store, os.path.join(self.rig.store, '_u')).position(time.time())


class HealthyCycle(AsService):
    def test_start_crash_restart_and_oom_as_the_service_user(self):
        code, lines, err = self.gate()
        self.assertEqual(code, 0, err)
        self.assertEqual(os.stat(self.marker).st_uid, UID, 'written by the service user, not root')
        code, lines, err = self.stop(result='signal')                                   # kill -9 of the daemon: the hook runs, sees a non-OOM result, closes the marker
        self.assertEqual(code, 0, err)
        self.assertFalse(os.path.exists(self.marker))
        self.assertEqual(self.gate()[0], 0)
        self.assertIsNone(self.latch(), 'a crash is not an incident')
        code, lines, err = self.stop(result='oom-kill', oom=True)
        self.assertEqual(code, 0, err)
        lt = self.latch()
        self.assertEqual(lt['kind'], 'oom_kill')
        self.assertEqual(os.stat(os.path.join(self.guard, 'TRIPPED')).st_uid, UID)
        self.assertTrue(os.path.exists(self.pause))
        self.assertFalse(os.path.exists(self.marker))
        self.assertEqual(self.gate()[0], 0)
        code, lines, err = self.daemon()
        self.assertEqual(code, 0, err)
        self.assertLess(self.clock_position(), 1.0, 'a restarted daemon must not advance')


class BothWritesFail(AsService):
    """The hook cannot establish ANY durable incident: no latch, no PAUSE. The next start must not run the daemon."""
    def setUp(self):
        super().setUp()
        self.assertEqual(self.gate()[0], 0)                                             # the run was opened while storage worked
        self.fault()
        self.hook = self.stop(result='oom-kill', oom=True)

    def test_the_hook_fails_loudly_and_leaves_nothing_but_the_marker(self):
        code, lines, err = self.hook
        self.assertEqual(code, 1, err)
        self.assertTrue(any(l.get('incident') == 'unrecorded' and l.get('held') is False for l in lines), lines)
        self.assertIsNone(self.latch())
        self.assertFalse(os.path.exists(self.pause))
        self.assertTrue(os.path.exists(self.marker), 'the marker, written while storage worked, is all that remembers the stop')

    def test_while_storage_is_still_broken_the_gate_refuses_so_the_daemon_never_starts(self):
        code, lines, err = self.gate()
        self.assertEqual(code, 1, 'ExecStartPre= failing means systemd does not run the daemon')
        self.assertTrue(any(l.get('gate') == 'refused' for l in lines), lines)
        self.assertIsNone(self.latch())
        for _ in range(3):                                                              # systemd retries (Restart=on-failure): every attempt is refused
            self.assertEqual(self.gate()[0], 1)

    def test_storage_recovering_before_the_restart_turns_the_unaccounted_stop_into_a_latch(self):
        self.healthy()
        code, lines, err = self.gate()
        self.assertEqual(code, 0, err)
        lt = self.latch()
        self.assertEqual(lt['kind'], 'unaccounted_stop')
        self.assertTrue(os.path.exists(self.pause))
        before = open(os.path.join(self.guard, 'TRIPPED')).read()
        code, lines, err = self.daemon()
        self.assertEqual(code, 0, err)
        self.assertEqual(open(os.path.join(self.guard, 'TRIPPED')).read(), before)
        self.assertLess(self.clock_position(), 1.0)

    def test_a_host_restart_while_storage_is_broken_changes_nothing_then_recovery_still_latches(self):
        """After a reboot every flag in memory or on a tmpfs is gone, and PAUSE could never be written. Only the marker is left, on disk."""
        self.assertEqual(self.gate()[0], 1)                                             # first boot attempt: still broken, refused
        self.healthy()
        code, lines, err = self.gate()                                                  # later: storage is back, nothing else survived
        self.assertEqual(code, 0, err)
        self.assertEqual(self.latch()['kind'], 'unaccounted_stop')
        self.assertTrue(os.path.exists(self.pause))

    def test_resume_is_refused_afterwards_as_for_any_latch(self):
        self.healthy()
        self.gate()
        rc, lines, err = self.service('resume', public=False)
        self.assertEqual(rc, 3, 'resume is refused while the latch exists: only release, then resume, clears it')


class NonOomStopThatCouldNotBeClosed(AsService):
    def test_a_crash_whose_marker_could_not_be_closed_is_treated_as_unaccounted_after_recovery(self):
        self.assertEqual(self.gate()[0], 0)
        self.fault()
        code, lines, err = self.stop(result='signal')
        self.assertEqual(code, 1, err)
        self.assertTrue(any(l.get('incident') == 'marker_not_closed' for l in lines), lines)
        self.assertEqual(self.gate()[0], 1)
        self.healthy()
        self.assertEqual(self.gate()[0], 0)
        self.assertEqual(self.latch()['kind'], 'unaccounted_stop', 'ambiguous, so held: a person looks at it and releases it')


@unittest.skipUnless(sandbox_available() and shutil.which('unshare') and subprocess.run(['unshare', '-m', 'sh', '-c', 'mount -t tmpfs -o size=64k tmpfs /mnt'], capture_output=True).returncode == 0,
                     'needs a private mount namespace and tmpfs')
class FullFilesystem(AsService):
    def test_a_full_guard_filesystem_fails_the_hook_and_recovery_latches_at_the_next_start(self):
        """ENOSPC for real: the guard folder is a 48 KiB tmpfs in a private mount namespace."""
        guard_real = self.guard
        script = textwrap.dedent('''
            set -u
            G=%(g)s
            mount -t tmpfs -o size=48k tmpfs "$G" && chown %(uid)d:%(gid)d "$G" || { echo '{"setup":"failed"}'; exit 9; }
            svc() { setpriv --reuid %(uid)d --regid %(gid)d --clear-groups env -i PATH=/usr/bin:/bin HOME=/nonexistent "$@"; }
            run() { sub=$1; shift; svc "$@" python3 -I %(src)s/tools/broadcast_run.py $sub --root %(tmp)s --public %(pub)s --guard "$G" %(cg)s; }
            run gate; echo "{\\"step\\":\\"gate1\\",\\"rc\\":$?}"
            dd if=/dev/zero of="$G/filler" bs=1k count=100 2>/dev/null
            svc SERVICE_RESULT=oom-kill MAINPID=4100 python3 -I %(src)s/tools/broadcast_run.py incident --root %(tmp)s --public %(pub)s --guard "$G" --cgroup %(cgdir)s --kernel-log /dev/null 2>/tmp/hook.err
            echo "{\\"step\\":\\"hook\\",\\"rc\\":$?,\\"latch\\":$( [ -s "$G/TRIPPED" ] && echo true || echo false ),\\"empty_latch_file\\":$( [ -e "$G/TRIPPED" ] && [ ! -s "$G/TRIPPED" ] && echo true || echo false ),\\"marker\\":$( [ -e "$G/RUN_OPEN" ] && echo true || echo false ),\\"pause\\":$( [ -e %(pause)s ] && echo true || echo false )}"
            run gate; echo "{\\"step\\":\\"gate_full\\",\\"rc\\":$?}"
            rm -f "$G/filler"
            run gate; echo "{\\"step\\":\\"gate_recovered\\",\\"rc\\":$?,\\"latch\\":\\"$(grep -o 'unaccounted_stop' "$G/TRIPPED" | head -1)\\"}"
        ''') % {'g': guard_real, 'uid': UID, 'gid': GID, 'src': self.src, 'tmp': self.tmp, 'pub': self.pub, 'cg': '', 'cgdir': self.cg, 'pause': self.pause}
        r = subprocess.run(['unshare', '-m', 'sh', '-c', script], capture_output=True, text=True, timeout=180)
        steps = {}
        for l in r.stdout.splitlines():
            if l.startswith('{'):
                d = json.loads(l)
                steps[d.get('step', 'setup')] = d
        self.assertNotIn('setup', steps, r.stdout + r.stderr)
        self.assertEqual(steps['gate1']['rc'], 0, r.stdout + r.stderr)
        h = steps['hook']
        self.assertEqual(h['rc'], 1, r.stdout + r.stderr)
        self.assertFalse(h['empty_latch_file'], 'a latch whose write failed must not be left behind as an empty file')
        self.assertTrue(h['marker'])
        self.assertTrue(h['pause'], 'the store is on another filesystem: the hold itself could still be made')
        self.assertEqual(steps['gate_full']['rc'], 1, 'full: the refusal holds')
        self.assertEqual(steps['gate_recovered']['rc'], 0)
        self.assertEqual(steps['gate_recovered']['latch'], 'unaccounted_stop')


if __name__ == '__main__':
    unittest.main()
