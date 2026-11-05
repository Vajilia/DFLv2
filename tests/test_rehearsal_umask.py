"""The rehearsal installer must not depend on the caller's umask, and must prove access AS THE REAL SERVICE USERS (root can read everything, so a root-only check proves nothing).

Background: the first live install ran from a shell with umask 027. The installer inherited it: the Caddy snippet, the installed code directory and part of the viewer came out readable by
root only. `caddy validate` (run as root) passed, then Caddy's own reload (run as the `caddy` user) could not open the snippet, and the install stopped (exit 6, production untouched).

Every test here runs the installer under umask 027 (the whole Deploy suite re-runs under it as well) and, where it matters, checks access with `runuser` as two real unprivileged users that
exist on any Debian/Ubuntu machine: www-data stands in for Caddy's user and daemon for the service user. They need root; without it these tests are skipped (and say so).
"""
import grp
import os
import pwd
import shutil
import stat
import subprocess
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_rehearsal_deploy as TD
from test_rehearsal_check import serve

CADDY_U, SVC_U = 'www-data', 'daemon'


def have_users():
    try:
        pwd.getpwnam(CADDY_U); pwd.getpwnam(SVC_U)
    except KeyError:
        return False
    return os.geteuid() == 0 and shutil.which('runuser') is not None


REAL = unittest.skipUnless(have_users(), 'needs root, runuser and the users www-data and daemon')


def as_user(user, *cmd):
    return subprocess.run(['runuser', '-u', user, '--', *cmd], capture_output=True, text=True)


def narrow(path):
    """What the umask-027 shell did to the first live install: directories 750, files 640, nothing for 'other'."""
    for base, dirs, files in os.walk(path):
        os.chmod(base, 0o750)
        for f in files:
            p = os.path.join(base, f)
            if not os.path.islink(p):
                os.chmod(p, 0o640)


class Umask027(TD.Deploy):
    """All the Deploy tests again, with the caller's umask at 027 (the installer must override it)."""

    def setUp(self):
        super().setUp()
        for p in (self.d, self.bin, self.caddy_dir, os.path.join(self.caddy_dir, 'conf.d'), self.roll):
            os.chmod(p, 0o755)                              # the test's own scaffolding must be reachable by the unprivileged users; mkdtemp makes it 0700
        os.chmod(os.path.join(self.caddy_dir, 'Caddyfile'), 0o644)
        for n in os.listdir(self.bin):
            os.chmod(os.path.join(self.bin, n), 0o755)      # the stand-ins must be runnable by the unprivileged users too
        # the package as the live Droplet saw it: made and unpacked under umask 027
        narrow(self.pkg)
        self.hold = os.umask(0o027)
        self.addCleanup(os.umask, self.hold)

    def run_sh(self, script, *args, **env):
        e = dict(os.environ, PATH=self.bin + os.pathsep + os.environ['PATH'], ALLOW_NON_ROOT='1', REH=self.reh, SRC_ROOT=self.src, CADDY_DIR=self.caddy_dir,
                 ROLLBACK_ROOT=self.roll, SYSTEMD_DIR=self.sysd, ETC_DIR=self.etc, MIN_FREE_GB='0')
        e.update(env)
        return subprocess.run(['sh', str(TD.DEPLOY / script), *args], capture_output=True, text=True, env=e, preexec_fn=lambda: os.umask(0o027))

    def real(self, **env):
        base = dict(CADDY_USER=CADDY_U, SERVICE_USER=SVC_U)
        base.update(env)
        return base

    def install_ok(self, **env):
        srv = serve(self.web())
        try:
            return self.run_sh('install_rehearsal.sh', self.pkg, CHECK_URL='http://127.0.0.1:%d' % srv.server_address[1], CHECK_WAIT_S='0', **self.real(**env))
        finally:
            srv.shutdown()

    def web(self):
        web = super().web()
        for p in (web, os.path.join(web, 'feed')):
            os.chmod(p, 0o755)
        os.chmod(os.path.join(web, 'index.html'), 0o644)
        return web

    # ---- the success path, judged by the real readers
    @REAL
    def test_installed_under_umask_027_is_readable_by_the_real_service_users_and_private_stays_private(self):
        r = self.install_ok()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        commit = self.meta['commit']
        snippet = os.path.join(self.caddy_dir, 'conf.d', 'dflv2-rehearsal.caddy')
        # Caddy's user: the block, the whole viewer (including a directory that was 750 in the package), the feed folder
        self.assertEqual(as_user(CADDY_U, 'test', '-r', snippet).returncode, 0)
        self.assertEqual(as_user(CADDY_U, 'test', '-r', os.path.join(self.reh, 'site', 'index.html')).returncode, 0)
        found = as_user(CADDY_U, 'find', os.path.join(self.reh, 'site'), '(', '-type', 'd', '(', '!', '-readable', '-o', '!', '-executable', ')', '-o', '-type', 'f', '!', '-readable', ')')
        self.assertEqual(found.stdout.strip(), '', found.stderr)
        self.assertEqual(as_user(CADDY_U, 'test', '-x', os.path.join(self.reh, 'feed')).returncode, 0)
        # ... and nothing private
        for hidden in (os.path.join(self.reh, 'private'), os.path.join(self.reh, 'monitor'), os.path.join(self.reh, 'private', 'store', 'CANARY.txt'),
                       os.path.join(self.reh, 'private', 'store', 'builds.json')):
            self.assertNotEqual(as_user(CADDY_U, 'test', '-r', hidden).returncode, 0, hidden)
            self.assertNotEqual(as_user(CADDY_U, 'ls', os.path.dirname(hidden) if os.path.isfile(hidden) else hidden).returncode, 0, hidden)
        # the service user: all of the code, and write access where the daemon and the monitor write
        code = os.path.join(self.src, commit)
        self.assertEqual(as_user(SVC_U, 'test', '-r', os.path.join(code, 'tools', 'broadcast_run.py')).returncode, 0)
        found = as_user(SVC_U, 'find', code, '(', '-type', 'd', '(', '!', '-readable', '-o', '!', '-executable', ')', '-o', '-type', 'f', '!', '-readable', ')')
        self.assertEqual(found.stdout.strip(), '', found.stderr)
        for w in ('feed', 'private', 'monitor'):
            self.assertEqual(as_user(SVC_U, 'test', '-w', os.path.join(self.reh, w)).returncode, 0, w)
        # modes are explicit, not inherited
        mode = lambda p: stat.S_IMODE(os.stat(p).st_mode)
        self.assertEqual(mode(self.reh), 0o755)
        self.assertEqual(mode(os.path.join(self.reh, 'feed')), 0o755)
        self.assertEqual(mode(os.path.join(self.reh, 'private')), 0o700)
        self.assertEqual(mode(os.path.join(self.reh, 'monitor')), 0o700)
        self.assertEqual(mode(snippet), 0o644)
        self.assertEqual(mode(os.path.join(self.sysd, 'dfl-rehearsal.service')), 0o644)
        self.assertEqual(mode(os.path.join(self.etc, 'rehearsal.env')), 0o644)
        self.assertEqual(mode(os.path.join(self.reh, 'site', 'index.html')), 0o644)
        # the private tree was NOT recursively changed: its inside keeps exactly the modes the package carried, only the owner changed
        self.assertEqual(pwd.getpwuid(os.stat(os.path.join(self.reh, 'private')).st_uid).pw_name, SVC_U)
        import tarfile
        with tarfile.open(os.path.join(self.pkg, 'store.tar.gz')) as tf:
            carried = {m.name.rstrip('/'): stat.S_IMODE(m.mode) for m in tf.getmembers()}
        checked = 0
        for name, m in carried.items():
            p = os.path.join(self.reh, 'private', name)
            if os.path.lexists(p) and name not in ('.', ''):
                self.assertEqual(mode(p), m, name)
                checked += 1
        self.assertGreater(checked, 3)
        self.assertIn('access: %s can read the block' % CADDY_U, r.stdout)        # the installer said it had checked, as that user

    # ---- existing unreadable directories (what an earlier umask-027 run leaves behind) are caught, named, and change nothing
    @REAL
    def test_an_existing_code_directory_left_at_750_stops_it_before_anything_else_is_created(self):
        code = os.path.join(self.src, self.meta['commit'])
        os.makedirs(code)
        open(os.path.join(code, 'x'), 'w').write('x')
        os.chmod(code, 0o750)
        r = self.run_sh('install_rehearsal.sh', self.pkg, **self.real())
        self.assertEqual(r.returncode, 8, r.stdout + r.stderr)
        self.assertIn(SVC_U + ' cannot', r.stderr)
        self.assertIn(code, r.stderr)
        self.untouched()

    @REAL
    def test_an_existing_unreadable_parent_of_the_code_is_named(self):
        os.makedirs(self.src)
        os.chmod(os.path.dirname(self.src), 0o755)                                    # /opt itself is fine ...
        os.chmod(self.src, 0o750)                                                     # ... /opt/dflv2-src is as an earlier umask-027 run left it
        r = self.run_sh('install_rehearsal.sh', self.pkg, **self.real())
        self.assertEqual(r.returncode, 8, r.stdout + r.stderr)
        self.assertIn('cannot enter ' + self.src, r.stderr)
        self.untouched()

    @REAL
    def test_an_existing_unreadable_conf_directory_stops_before_caddy_is_reloaded_and_the_block_is_taken_out(self):
        conf = os.path.join(self.caddy_dir, 'conf.d')
        os.chmod(conf, 0o700)
        r = self.run_sh('install_rehearsal.sh', self.pkg, **self.real())
        self.assertEqual(r.returncode, 8, r.stdout + r.stderr)
        self.assertIn('NOT reloaded', r.stderr)
        self.assertIn('cannot enter ' + conf, r.stderr)
        self.assertFalse(os.path.exists(os.path.join(conf, 'dflv2-rehearsal.caddy')))
        self.assertNotIn('systemctl reload', self.calls())
        self.assertNotIn('systemctl start', self.calls())

    @REAL
    def test_the_caddy_user_failing_to_adapt_the_configuration_is_the_same_stop(self):
        # an unreadable Caddyfile itself (mode 600, root only) is found the same way
        os.chmod(os.path.join(self.caddy_dir, 'Caddyfile'), 0o600)
        r = self.run_sh('install_rehearsal.sh', self.pkg, **self.real())
        self.assertEqual(r.returncode, 8, r.stdout + r.stderr)
        self.assertNotIn('systemctl reload', self.calls())
        self.assertFalse(os.path.exists(os.path.join(self.caddy_dir, 'conf.d', 'dflv2-rehearsal.caddy')))

    # ---- private data must stay out of the web server's reach
    @REAL
    def test_if_the_web_servers_user_could_read_private_data_the_install_stops(self):
        r = self.run_sh('install_rehearsal.sh', self.pkg, **self.real(CADDY_USER=SVC_U))        # same user for both: private/ is reachable by "Caddy"
        self.assertEqual(r.returncode, 8, r.stdout + r.stderr)
        self.assertIn('private data must never be reachable', r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.caddy_dir, 'conf.d', 'dflv2-rehearsal.caddy')))
        self.assertNotIn('systemctl reload', self.calls())

    # ---- the checks cannot be skipped by accident on a real install
    @REAL
    def test_a_real_install_that_cannot_check_a_user_refuses_instead_of_skipping(self):
        r = self.run_sh('install_rehearsal.sh', self.pkg, **self.real(CADDY_USER='no-such-user-xyz', ALLOW_NON_ROOT=''))
        self.assertEqual(r.returncode, 8, r.stdout + r.stderr)
        self.assertIn('cannot check access as the user', r.stderr)
        self.assertNotIn('systemctl reload', self.calls())


if __name__ == '__main__':
    unittest.main()
