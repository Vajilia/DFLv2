"""deploy/rehearsal/install_rehearsal.sh and uninstall_rehearsal.sh against a scratch tree with recording stand-ins for caddy, systemctl and the address lookups.
Nothing here touches the machine: every path is under a temporary directory. The success path (a complete store) is exercised by hand with a real package
(docs/BROADCAST_REHEARSAL.md, 'Rehearsed here'); these tests cover the refusals and the failure paths, which must change nothing."""
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'tests'))
import build_rehearsal_package as BP
import rehearsal_check as RC
from test_rehearsal_check import serve
from dflsite import broadcast as B
from test_season import Rig

DEPLOY = ROOT / 'deploy' / 'rehearsal'


def stub(path, body):
    Path(path).write_text('#!/bin/sh\n' + body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)


class Deploy(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(lambda: subprocess.run(['rm', '-rf', self.d]))
        B.FSYNC = False
        self.addCleanup(setattr, B, 'FSYNC', True)
        self.bin = os.path.join(self.d, 'bin'); os.makedirs(self.bin)
        self.log = os.path.join(self.d, 'calls.log')
        stub(os.path.join(self.bin, 'caddy'), 'echo "caddy $*" >> %s\nexit ${CADDY_RC:-0}\n' % self.log)
        stub(os.path.join(self.bin, 'systemctl'), 'echo "systemctl $*" >> %s\n' % self.log)
        stub(os.path.join(self.bin, 'useradd'), 'echo "useradd $*" >> %s\n' % self.log)
        stub(os.path.join(self.bin, 'getent'), 'echo "${GETENT_OUT:-10.0.0.5 STREAM x}"\n')
        stub(os.path.join(self.bin, 'ip'), 'echo "2: eth0    inet 10.0.0.5/24 brd 10.0.0.255 scope global eth0"\n')
        stub(os.path.join(self.bin, 'curl'), 'exit 22\n')
        # python3 passes through, except that the pre-flight (proved in test_rehearsal_check) is stood in for: a synthetic three-event store cannot pass its 48-hour test
        stub(os.path.join(self.bin, 'python3'), 'case " $* " in *" preflight "*) [ -n "$REAL_PREFLIGHT" ] || { echo "PASS stand-in pre-flight"; exit 0; };; esac\nexec %s "$@"\n' % sys.executable)
        self.caddy_dir = os.path.join(self.d, 'caddy'); os.makedirs(os.path.join(self.caddy_dir, 'conf.d'))
        open(os.path.join(self.caddy_dir, 'Caddyfile'), 'w').write('import /etc/caddy/conf.d/*.caddy\n')
        self.roll = os.path.join(self.d, 'roll'); os.makedirs(os.path.join(self.roll, '20261105T000000Z'))
        self.reh = os.path.join(self.d, 'srv', 'dflv2-rehearsal')
        self.src = os.path.join(self.d, 'opt', 'dflv2-src')
        self.sysd = os.path.join(self.d, 'systemd'); self.etc = os.path.join(self.d, 'etc-dflv2')
        # a package made from the synthetic year: its store is incomplete on purpose, so the installer's own pre-flight must refuse it
        rig = Rig(self.d)
        out = os.path.join(self.d, 'out'); os.makedirs(out)
        with mock.patch.object(RC, 'preflight', lambda *a, **k: [(True, 'stubbed')]):
            path, self.meta = BP.build('HEAD', self.d, out)
        with tarfile.open(path) as tf:
            tf.extractall(os.path.join(self.d, 'pkg'), filter='data')
        self.pkg = os.path.join(self.d, 'pkg', os.listdir(os.path.join(self.d, 'pkg'))[0])

    def run_sh(self, script, *args, **env):
        e = dict(os.environ, PATH=self.bin + os.pathsep + os.environ['PATH'], ALLOW_NON_ROOT='1', REH=self.reh, SRC_ROOT=self.src, CADDY_DIR=self.caddy_dir,
                 ROLLBACK_ROOT=self.roll, SYSTEMD_DIR=self.sysd, ETC_DIR=self.etc, MIN_FREE_GB='0')
        e.update(env)
        return subprocess.run(['sh', str(DEPLOY / script), *args], capture_output=True, text=True, env=e)

    def calls(self):
        return open(self.log).read() if os.path.exists(self.log) else ''

    def untouched(self):
        self.assertFalse(os.path.exists(self.reh))
        self.assertFalse(os.path.exists(os.path.join(self.caddy_dir, 'conf.d', 'dflv2-rehearsal.caddy')))
        self.assertFalse(os.path.exists(self.sysd))
        self.assertNotIn('reload', self.calls())

    def test_a_damaged_package_is_refused_before_anything_changes(self):
        with open(os.path.join(self.pkg, 'repo', 'tools', 'broadcast_run.py'), 'a') as f:
            f.write('# tampered\n')
        r = self.run_sh('install_rehearsal.sh', self.pkg)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn('SHA256SUMS', r.stderr)
        self.untouched()

    def test_production_and_system_paths_are_refused(self):
        for bad in ('/srv/dflv2/feed', '/srv/dflv2', '/var/www/dfl', '/', os.path.join(self.d, 'srv', 'dflv2-data')):
            r = self.run_sh('install_rehearsal.sh', self.pkg, REH=bad)
            self.assertEqual(r.returncode, 2, (bad, r.stderr))
        self.untouched()

    def test_an_existing_rehearsal_folder_is_never_overwritten(self):
        os.makedirs(self.reh)
        open(os.path.join(self.reh, 'keep'), 'w').write('x')
        r = self.run_sh('install_rehearsal.sh', self.pkg)
        self.assertEqual(r.returncode, 2)
        self.assertIn('already exists', r.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.reh, 'keep')))

    def test_no_recent_rollback_copy_and_wrong_dns_stop_it(self):
        subprocess.run(['rm', '-rf', os.path.join(self.roll, '20261105T000000Z')])
        r = self.run_sh('install_rehearsal.sh', self.pkg)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.untouched()
        os.makedirs(os.path.join(self.roll, '20261105T000001Z'))
        r = self.run_sh('install_rehearsal.sh', self.pkg, GETENT_OUT='203.0.113.9 STREAM x')
        self.assertEqual(r.returncode, 4, r.stderr)
        self.untouched()

    def test_a_failing_preflight_changes_neither_caddy_nor_systemd_and_never_starts_anything(self):
        r = self.run_sh('install_rehearsal.sh', self.pkg, REAL_PREFLIGHT='1')          # the real pre-flight, on a store that cannot pass it
        self.assertEqual(r.returncode, 5, r.stdout + r.stderr)
        self.assertIn('pre-flight failed', r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.caddy_dir, 'conf.d', 'dflv2-rehearsal.caddy')))
        self.assertFalse(os.path.exists(self.sysd))
        calls = self.calls()
        self.assertNotIn('reload', calls)
        self.assertNotIn('start', calls)
        self.assertNotIn('enable', calls)
        # the way back: --purge removes exactly the rehearsal folder and the code this install added
        self.assertTrue(os.path.isdir(self.reh))
        r = self.run_sh('uninstall_rehearsal.sh', '--purge')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(self.reh))
        self.assertFalse(os.path.exists(os.path.join(self.src, self.meta['commit'])))
        self.assertTrue(os.path.isdir(self.caddy_dir))
        self.assertTrue(os.path.isdir(self.roll))

    def web(self):
        web = os.path.join(self.d, 'web')
        os.makedirs(os.path.join(web, 'feed'))
        open(os.path.join(web, 'index.html'), 'w').write('<!doctype html><script src="a.js"></script>')
        return web

    def test_the_real_caddy_rejecting_the_block_on_its_own_stops_everything_before_any_change(self):
        r = self.run_sh('install_rehearsal.sh', self.pkg, CADDY_RC='1')
        self.assertEqual(r.returncode, 6, r.stdout + r.stderr)
        self.assertIn('rejects the rehearsal block on its own', r.stderr)
        self.untouched()
        self.assertNotIn('reload', self.calls())

    def test_a_server_that_follows_the_rules_gets_the_block_kept_and_nothing_is_started(self):
        srv = serve(self.web())
        try:
            r = self.run_sh('install_rehearsal.sh', self.pkg, CHECK_URL='http://127.0.0.1:%d' % srv.server_address[1], CHECK_WAIT_S='0')
        finally:
            srv.shutdown()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('server rules verified live', r.stdout)
        self.assertIn('INSTALLED. Nothing is running', r.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.caddy_dir, 'conf.d', 'dflv2-rehearsal.caddy')))
        self.assertTrue(os.path.exists(os.path.join(self.sysd, 'dfl-rehearsal.service')))
        calls = self.calls()
        self.assertIn('caddy validate', calls)
        self.assertEqual(calls.count('systemctl reload caddy'), 1)
        for word in ('start', 'enable'):
            self.assertNotIn('systemctl ' + word, calls)
        unit = open(os.path.join(self.sysd, 'dfl-rehearsal.service')).read()
        self.assertIn('--guard /srv/dflv2-rehearsal/monitor', unit)
        mon = open(os.path.join(self.sysd, 'dfl-rehearsal-monitor.service')).read()
        for need in ('--guard /srv/dflv2-rehearsal/monitor', '--canary /srv/dflv2-rehearsal/private/store/CANARY.txt', '--min-free-gb 6'):
            self.assertIn(need, mon)
        self.assertTrue(os.path.exists(os.path.join(self.reh, 'private', 'store', 'CANARY.txt')))
        self.assertFalse(os.path.exists(os.path.join(self.caddy_dir, 'conf.d', 'dflv2-rehearsal.caddy.before')))

    def test_a_server_that_breaks_a_rule_gets_the_block_taken_out_again(self):
        srv = serve(self.web(), ['listing'])
        try:
            r = self.run_sh('install_rehearsal.sh', self.pkg, CHECK_URL='http://127.0.0.1:%d' % srv.server_address[1], CHECK_WAIT_S='0')
        finally:
            srv.shutdown()
        self.assertEqual(r.returncode, 7, r.stdout + r.stderr)
        self.assertIn('FAIL', r.stderr)
        self.assertIn('taken out again', r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.caddy_dir, 'conf.d', 'dflv2-rehearsal.caddy')))
        self.assertEqual(self.calls().count('systemctl reload caddy'), 2)               # in, then out
        self.assertTrue(os.path.exists(os.path.join(self.reh, 'monitor', 'install-check.log')))
        self.assertNotIn('systemctl start', self.calls())

    def test_a_site_that_cannot_be_reached_is_the_same_failure(self):
        r = self.run_sh('install_rehearsal.sh', self.pkg, CHECK_URL='http://127.0.0.1:1', CHECK_WAIT_S='0')
        self.assertEqual(r.returncode, 7, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.caddy_dir, 'conf.d', 'dflv2-rehearsal.caddy')))

    def test_too_little_room_for_the_floor_plus_the_cap_stops_the_install(self):
        r = self.run_sh('install_rehearsal.sh', self.pkg, MIN_FREE_GB='100000')
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn('free', r.stderr)

    def test_uninstall_refuses_production_paths_and_keeps_data_without_purge(self):
        for bad in ('/srv/dflv2/feed', '/srv/dflv2', '/var/www', '/', '/tmp/../srv/dflv2-rehearsal', '/srv/other'):
            r = self.run_sh('uninstall_rehearsal.sh', '--purge', REH=bad)
            self.assertEqual(r.returncode, 2, (bad, r.stdout, r.stderr))
        os.makedirs(self.reh)
        open(os.path.join(self.reh, 'data'), 'w').write('x')
        r = self.run_sh('uninstall_rehearsal.sh')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.reh, 'data')))

    def test_uninstall_restores_the_block_if_caddy_rejects_the_configuration_without_it(self):
        snippet = os.path.join(self.caddy_dir, 'conf.d', 'dflv2-rehearsal.caddy')
        open(snippet, 'w').write('rehearsal.example { }\n')
        r = self.run_sh('uninstall_rehearsal.sh', CADDY_RC='1')
        self.assertEqual(r.returncode, 5)
        self.assertTrue(os.path.exists(snippet))


if __name__ == '__main__':
    unittest.main()
