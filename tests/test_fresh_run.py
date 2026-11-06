"""deploy/rehearsal/fresh_run.sh: set a failed rehearsal aside whole and prepare a clean run, against a scratch tree with recording stand-ins for systemctl and journalctl.
Nothing here touches the machine: every path is under a temporary directory. The package is the synthetic three-event one of test_rehearsal_deploy (its pre-flight is stood in for)."""
import hashlib
import json
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'tests'))
import build_rehearsal_package as BP
import rehearsal_check as RC
from dflsite import broadcast as B
from test_season import Rig

SCRIPT = ROOT / 'deploy' / 'rehearsal' / 'fresh_run.sh'


def stub(path, body):
    Path(path).write_text('#!/bin/sh\n' + body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)


def tree(path):
    out = {}
    for d, _, names in os.walk(path):
        for n in names:
            p = os.path.join(d, n)
            out[os.path.relpath(p, path)] = hashlib.sha256(open(p, 'rb').read()).hexdigest()
    return out


class FreshRun(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(lambda: subprocess.run(['chmod', '-R', 'u+w', self.d]) or subprocess.run(['rm', '-rf', self.d]))
        B.FSYNC = False
        self.addCleanup(setattr, B, 'FSYNC', True)
        self.bin = os.path.join(self.d, 'bin'); os.makedirs(self.bin)
        self.log = os.path.join(self.d, 'calls.log')
        self.reh = os.path.join(self.d, 'srv', 'dflv2-rehearsal')
        self.src = os.path.join(self.d, 'opt', 'dflv2-src')
        self.sysd = os.path.join(self.d, 'systemd'); self.etc = os.path.join(self.d, 'etc-dflv2')
        self.keep = os.path.join(self.d, 'srv', 'failed-runs')                       # same filesystem as REH, as on the Droplet
        stub(os.path.join(self.bin, 'systemctl'),
             'echo "systemctl $*" >> %s\ncase "$1" in\n is-active) [ -f %s/ACTIVE ] && [ "$3" = "${ACTIVE_UNIT:-$3}" ] && exit 0; exit 3;;\n'
             ' start) if [ "$2" = dfl-rehearsal ]; then mkdir -p %s/private/store; echo \'{"state":"running"}\' > %s/private/store/clock.json; fi;;\nesac\nexit 0\n'
             % (self.log, self.d, self.reh, self.reh))
        stub(os.path.join(self.bin, 'journalctl'), 'echo "journal of the failed run"\n')
        stub(os.path.join(self.bin, 'python3'), 'case " $* " in *" preflight "*) echo "PASS stand-in pre-flight"; exit 0;; esac\nexec %s "$@"\n' % sys.executable)
        # a FAILED run in place: private store with a clock and a latch, a public folder with files, a monitor folder with a trip and evidence
        for rel, body in (('private/store/clock.json', '{"state":"paused"}'), ('private/store/PAUSE', ''), ('private/store/CANARY.txt', 'OLD-CANARY'),
                          ('feed/latest.json', '{"release":"e00011"}'), ('feed/broadcast/live/G/.tmp-1-s0001.json', '{}'), ('monitor/TRIPPED', '{"kind":"monitor_error"}'),
                          ('monitor/evidence/trip-1-monitor_error/evidence.json', '{"traceback":"FileNotFoundError"}'), ('monitor/watch.log', '{"violation":"monitor_error"}\n'),
                          ('site/index.html', '<html>')):
            p = os.path.join(self.reh, rel); os.makedirs(os.path.dirname(p), exist_ok=True); open(p, 'w').write(body)
        os.makedirs(os.path.join(self.src, 'oldcommit', 'tools')); open(os.path.join(self.src, 'oldcommit', 'tools', 'rehearsal_check.py'), 'w').write('old')
        os.makedirs(self.sysd); os.makedirs(self.etc)
        open(os.path.join(self.etc, 'rehearsal.env'), 'w').write('REHEARSAL_DEADLINE=2026-11-08T14:18:10Z\n')
        open(os.path.join(self.sysd, 'dfl-rehearsal.service'), 'w').write('OLD UNIT /opt/dflv2-src/oldcommit/\n')
        rig = Rig(self.d)
        out = os.path.join(self.d, 'out'); os.makedirs(out)
        with mock.patch.object(RC, 'preflight', lambda *a, **k: [(True, 'stubbed')]):
            path, self.meta = BP.build('HEAD', self.d, out)
        with tarfile.open(path) as tf:
            tf.extractall(os.path.join(self.d, 'pkg'), filter='data')
        self.pkg = os.path.join(self.d, 'pkg', os.listdir(os.path.join(self.d, 'pkg'))[0])
        self.commit = json.load(open(os.path.join(self.pkg, 'PACKAGE.json')))['commit']

    def sh(self, *args, **env):
        e = dict(os.environ, PATH=self.bin + os.pathsep + os.environ['PATH'], ALLOW_NON_ROOT='1', REH=self.reh, SRC_ROOT=self.src, SYSTEMD_DIR=self.sysd, ETC_DIR=self.etc,
                 KEEP_ROOT=self.keep, CONF_D=os.path.join(self.d, 'caddy'), MIN_FREE_GB='0', STAMP='20261106T170000Z')
        e.update(env)
        return subprocess.run(['sh', str(SCRIPT), *args], capture_output=True, text=True, env=e)

    def calls(self):
        return open(self.log).read() if os.path.exists(self.log) else ''

    def active(self, on=True):
        p = os.path.join(self.d, 'ACTIVE')
        if on:
            open(p, 'w').close()
        elif os.path.exists(p):
            os.unlink(p)

    # ------------------------------------------------------------------------------------------------------------------------------------------ preserve
    def test_the_script_is_valid_shell(self):
        self.assertEqual(subprocess.run(['sh', '-n', str(SCRIPT)]).returncode, 0)

    def test_preserve_without_apply_changes_nothing(self):
        before = tree(self.reh)
        r = self.sh('preserve')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(tree(self.reh), before)
        self.assertFalse(os.path.exists(self.keep + '/20261106T170000Z'))

    def test_preserve_refuses_while_a_unit_is_active_and_changes_nothing(self):
        self.active()
        before = tree(self.reh)
        r = self.sh('preserve', '--apply')
        self.assertEqual(r.returncode, 2)
        self.assertIn('is active', r.stderr)
        self.assertEqual(tree(self.reh), before)

    def test_preserve_moves_the_failed_run_whole_and_nothing_is_lost(self):
        before = {d: tree(os.path.join(self.reh, d)) for d in ('private', 'feed', 'monitor')}
        site, oldcode = tree(os.path.join(self.reh, 'site')), tree(os.path.join(self.src, 'oldcommit'))
        r = self.sh('preserve', '--apply')
        self.assertEqual(r.returncode, 0, r.stderr)
        k = os.path.join(self.keep, '20261106T170000Z')
        for d in ('private', 'feed', 'monitor'):
            self.assertEqual(tree(os.path.join(k, d)), before[d], d)                 # byte for byte, same files
            self.assertFalse(os.path.exists(os.path.join(self.reh, d)), 'moved, not copied')
        self.assertEqual(tree(os.path.join(self.reh, 'site')), site)
        self.assertEqual(tree(os.path.join(self.src, 'oldcommit')), oldcode)
        self.assertIn('monitor_error', open(os.path.join(k, 'FAILED_RUN.txt')).read())
        self.assertIn('FAILED', open(os.path.join(k, 'FAILED_RUN.txt')).read())
        self.assertTrue(os.path.exists(os.path.join(k, 'config', 'rehearsal.env')))
        self.assertIn('2026-11-08T14:18:10Z', open(os.path.join(k, 'config', 'rehearsal.env')).read())
        self.assertTrue(os.path.exists(os.path.join(k, 'journal.txt')))
        ok = subprocess.run(['sha256sum', '-c', '--quiet', 'MANIFEST.sha256'], cwd=k, capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertTrue(all(not (os.stat(os.path.join(dd, f)).st_mode & stat.S_IWUSR) for dd, _, fs in os.walk(k) for f in fs), 'read-only')
        self.assertEqual(self.sh('preserve', '--apply').returncode, 2)                    # nothing left to set aside; and never over an existing target

    def test_preserve_never_overwrites_an_existing_target(self):
        os.makedirs(os.path.join(self.keep, '20261106T170000Z'))
        before = tree(self.reh)
        r = self.sh('preserve', '--apply')
        self.assertEqual(r.returncode, 2)
        self.assertEqual(tree(self.reh), before)

    # ------------------------------------------------------------------------------------------------------------------------------------------ prepare
    def test_prepare_refuses_over_a_run_that_has_not_been_preserved(self):
        before = tree(self.reh)
        r = self.sh('prepare', self.pkg, '--apply')
        self.assertEqual(r.returncode, 2)
        self.assertIn('preserve', r.stderr)
        self.assertEqual(tree(self.reh), before)
        self.assertFalse(os.path.exists(os.path.join(self.src, self.commit)))

    def test_prepare_refuses_a_damaged_package_and_an_existing_code_directory(self):
        self.assertEqual(self.sh('preserve', '--apply').returncode, 0)
        open(os.path.join(self.pkg, 'store.tar.gz'), 'ab').write(b'x')
        r = self.sh('prepare', self.pkg, '--apply')
        self.assertEqual(r.returncode, 2)
        self.assertIn('SHA256SUMS', r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.reh, 'private')))

    def prepared(self):
        self.assertEqual(self.sh('preserve', '--apply').returncode, 0)
        r = self.sh('prepare', self.pkg, '--apply')
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)

    def test_prepare_builds_a_clean_run_with_independent_code_and_a_past_deadline(self):
        oldcode = tree(os.path.join(self.src, 'oldcommit'))
        self.prepared()
        new = os.path.join(self.src, self.commit)
        self.assertTrue(os.path.isfile(os.path.join(new, 'tools', 'rehearsal_check.py')))
        pkg_inodes = {os.stat(os.path.join(dd, f)).st_ino for dd, _, fs in os.walk(os.path.join(self.pkg, 'repo')) for f in fs}
        for dd, _, fs in os.walk(new):
            for f in fs:
                st = os.stat(os.path.join(dd, f))
                self.assertEqual(st.st_nlink, 1, 'a hard link in the new code directory: ' + f)
                self.assertNotIn(st.st_ino, pkg_inodes)
        self.assertEqual(tree(os.path.join(self.src, 'oldcommit')), oldcode)
        self.assertEqual(os.listdir(os.path.join(self.reh, 'feed')), [])
        self.assertEqual(os.listdir(os.path.join(self.reh, 'monitor')), [])
        store = os.path.join(self.reh, 'private', 'store')
        self.assertFalse(os.path.exists(os.path.join(store, 'clock.json')))
        self.assertFalse(os.path.exists(os.path.join(store, 'PAUSE')))
        self.assertTrue(os.path.exists(os.path.join(store, 'CANARY.txt')))
        self.assertNotEqual(open(os.path.join(store, 'CANARY.txt')).read(), 'OLD-CANARY', 'a new canary')
        self.assertEqual(open(os.path.join(self.etc, 'rehearsal.env')).read().strip(), 'REHEARSAL_DEADLINE=2000-01-01T00:00:00Z')
        for u in ('dfl-rehearsal.service', 'dfl-rehearsal-monitor.service'):
            text = open(os.path.join(self.sysd, u)).read()
            self.assertIn('/opt/dflv2-src/%s/' % self.commit, text)
            self.assertNotIn('oldcommit', text)
        self.assertNotIn('systemctl start', self.calls())
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.reh, 'private')).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.reh, 'monitor')).st_mode), 0o700)

    # ------------------------------------------------------------------------------------------------------------------------------------------ start
    def test_start_refuses_a_run_that_was_not_prepared(self):
        r = self.sh('start', '--apply')
        self.assertEqual(r.returncode, 2)
        self.assertEqual(open(os.path.join(self.etc, 'rehearsal.env')).read().strip(), 'REHEARSAL_DEADLINE=2026-11-08T14:18:10Z')
        self.assertNotIn('systemctl start', self.calls())

    def test_start_without_apply_changes_nothing(self):
        self.prepared()
        r = self.sh('start')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(open(os.path.join(self.etc, 'rehearsal.env')).read().strip(), 'REHEARSAL_DEADLINE=2000-01-01T00:00:00Z')
        self.assertNotIn('systemctl start', self.calls())

    def test_start_computes_the_deadline_when_it_runs_and_starts_the_monitor_first(self):
        self.prepared()
        t0 = time.time()
        r = self.sh('start', '--apply')
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        line = open(os.path.join(self.etc, 'rehearsal.env')).read().strip()
        self.assertTrue(line.startswith('REHEARSAL_DEADLINE='))
        deadline = time.mktime(time.strptime(line.split('=')[1], '%Y-%m-%dT%H:%M:%SZ')) - time.timezone
        self.assertAlmostEqual(deadline - t0, 54 * 3600, delta=15)
        starts = [l for l in self.calls().splitlines() if l.startswith('systemctl start')]
        self.assertEqual(starts, ['systemctl start dfl-rehearsal-monitor', 'systemctl start dfl-rehearsal'])

    def test_start_refuses_when_the_public_folder_is_not_empty_or_a_clock_exists(self):
        self.prepared()
        open(os.path.join(self.reh, 'feed', 'latest.json'), 'w').write('{}')
        self.assertEqual(self.sh('start', '--apply').returncode, 2)
        os.unlink(os.path.join(self.reh, 'feed', 'latest.json'))
        open(os.path.join(self.reh, 'private', 'store', 'clock.json'), 'w').write('{}')
        self.assertEqual(self.sh('start', '--apply').returncode, 2)
        self.assertEqual(open(os.path.join(self.etc, 'rehearsal.env')).read().strip(), 'REHEARSAL_DEADLINE=2000-01-01T00:00:00Z')


if __name__ == '__main__':
    unittest.main()
