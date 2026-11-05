"""deploy/rehearsal/quarantine_rehearsal.sh: set the inactive leftovers of a half-finished install aside, reversibly. Scratch tree only, run under umask 027
(the umask the real leftovers were made under). Nothing may be deleted, metadata must survive the move and the restore, and anything active is refused."""
import gzip
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
DEPLOY = Path(os.environ.get('REHEARSAL_DEPLOY_DIR') or Path(__file__).resolve().parents[1] / 'deploy' / 'rehearsal')
COMMIT = 'da101d67dcc49c18726270b7d23a3c2f9b2b0ccf'


def write(path, text='x', mode=0o640, mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(text)
    os.chmod(path, mode)
    if mtime:
        os.utime(path, (mtime, mtime))


def snapshot(root):
    out = {}
    for base, dirs, files in os.walk(root):
        for n in dirs + files:
            p = os.path.join(base, n)
            st = os.lstat(p)
            out[os.path.relpath(p, root)] = (stat.S_IMODE(st.st_mode), st.st_size, int(st.st_mtime), open(p, 'rb').read() if os.path.isfile(p) and not os.path.islink(p) else os.readlink(p) if os.path.islink(p) else None)
    return out


class Quarantine(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        os.chmod(self.d, 0o755)
        self.addCleanup(lambda: subprocess.run(['rm', '-rf', self.d]))
        j = lambda *a: os.path.join(self.d, *a)
        self.reh, self.src, self.sysd, self.etc = j('srv', 'dflv2-rehearsal'), j('opt', 'dflv2-src'), j('systemd'), j('etc-dflv2')
        self.caddy, self.q = j('caddy'), j('quarantine')
        self.bin = j('bin'); os.makedirs(self.bin)
        self.log = j('calls.log')
        for name, body in (('systemctl', 'echo "systemctl $*" >> %s\ncase "$1" in is-active) echo "${UNIT_ACTIVE:-inactive}";; is-enabled) echo "${UNIT_ENABLED:-disabled}";; esac\n' % self.log),):
            Path(self.bin, name).write_text('#!/bin/sh\n' + body)
            os.chmod(os.path.join(self.bin, name), 0o755)
        t = time.time() - 86400
        write(j('srv', 'dflv2-rehearsal', 'private', 'store', 'plan.json'), '{"p":1}', 0o640, t)
        write(j('srv', 'dflv2-rehearsal', 'private', 'store', 'CANARY.txt'), 'canary', 0o600, t - 5)
        write(j('srv', 'dflv2-rehearsal', 'site', 'index.html'), '<script>', 0o644, t - 9)
        write(j('srv', 'dflv2-rehearsal', 'monitor', 'note'), 'n', 0o600, t - 11)
        os.makedirs(j('srv', 'dflv2-rehearsal', 'feed'), mode=0o755)
        os.symlink('private/store/plan.json', j('srv', 'dflv2-rehearsal', 'link'))
        write(j('srv', 'dflv2-rehearsal', '.installed_commit'), COMMIT + '\n', 0o644, t)
        write(j('opt', 'dflv2-src', COMMIT, 'tools', 'broadcast_run.py'), 'code', 0o640, t - 20)
        write(j('opt', 'dflv2-src', 'other-commit', 'keep'), 'keep', 0o644, t)                  # a neighbour that must not move
        write(j('systemd', 'dfl-rehearsal.service'), 'unit', 0o640, t - 30)
        write(j('systemd', 'dfl-rehearsal-monitor.service'), 'unit2', 0o640, t - 31)
        write(j('systemd', 'dfl-feed-export.timer'), 'production timer', 0o644, t)               # production: must not move
        write(j('etc-dflv2', 'rehearsal.env'), 'REHEARSAL_DEADLINE=2000-01-01T00:00:00Z\n', 0o640, t - 40)
        write(j('etc-dflv2', 'league.env'), 'production', 0o644, t)
        write(j('caddy', 'Caddyfile'), 'import conf.d/*.caddy\n', 0o644, t)
        os.makedirs(j('caddy', 'conf.d'))
        write(j('rollback', '2026', 'keep'), 'rollback copy', 0o644, t)
        os.makedirs(j('evidence')); write(j('evidence', 'e.txt'), 'evidence', 0o600, t)
        self.guard = {k: snapshot(j(k)) for k in ('rollback', 'evidence', 'caddy')}
        self.guard['neighbour'] = snapshot(j('opt', 'dflv2-src', 'other-commit'))
        self.before = {'reh': snapshot(self.reh), 'code': snapshot(j('opt', 'dflv2-src', COMMIT)), 'unit': snapshot(j('systemd')), 'etc': snapshot(j('etc-dflv2'))}

    def run_sh(self, *args, **env):
        e = dict(os.environ, PATH=self.bin + os.pathsep + os.environ['PATH'], ALLOW_NON_ROOT='1', REH=self.reh, SRC_ROOT=self.src, CADDY_DIR=self.caddy,
                 SYSTEMD_DIR=self.sysd, ETC_DIR=self.etc, QUARANTINE_ROOT=self.q)
        e.update(env)
        return subprocess.run(['sh', str(DEPLOY / 'quarantine_rehearsal.sh'), *args], capture_output=True, text=True, env=e, preexec_fn=lambda: os.umask(0o027))

    def qdir(self):
        names = sorted(os.listdir(self.q))
        self.assertEqual(len(names), 1, names)
        return os.path.join(self.q, names[0])

    def test_moves_exactly_the_leftovers_keeps_metadata_and_records_original_paths(self):
        r = self.run_sh()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('verified: the moved', r.stdout)
        q = self.qdir()
        self.assertEqual(stat.S_IMODE(os.stat(q).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(self.q).st_mode), 0o700)
        # gone from the original places ...
        for gone in (self.reh, os.path.join(self.src, COMMIT), os.path.join(self.sysd, 'dfl-rehearsal.service'), os.path.join(self.sysd, 'dfl-rehearsal-monitor.service'),
                     os.path.join(self.etc, 'rehearsal.env')):
            self.assertFalse(os.path.lexists(gone), gone)
        # ... present, with identical modes, sizes, times and content, in the quarantine
        self.assertEqual(snapshot(os.path.join(q, 'items' + self.reh)), self.before['reh'])
        self.assertEqual(snapshot(os.path.join(q, 'items' + self.src, COMMIT)), self.before['code'])
        self.assertEqual(open(os.path.join(q, 'items' + self.sysd, 'dfl-rehearsal.service')).read(), 'unit')
        self.assertEqual(open(os.path.join(q, 'items' + self.etc, 'rehearsal.env')).read(), 'REHEARSAL_DEADLINE=2000-01-01T00:00:00Z\n')
        self.assertTrue(os.path.islink(os.path.join(q, 'items' + self.reh, 'link')))
        # recorded: original paths, the listing, the state before and after
        man = open(os.path.join(q, 'manifest.tsv')).read()
        for orig in (self.reh, os.path.join(self.src, COMMIT), os.path.join(self.sysd, 'dfl-rehearsal.service'), os.path.join(self.sysd, 'dfl-rehearsal-monitor.service'),
                     os.path.join(self.etc, 'rehearsal.env')):
            self.assertIn(orig + '\t', man)
        self.assertIn('dfl-rehearsal enabled=disabled active=inactive', open(os.path.join(q, 'state_before.txt')).read())
        self.assertIn('rehearsal snippet: absent', open(os.path.join(q, 'state_after.txt')).read())
        with gzip.open(os.path.join(q, 'listing.tsv.gz'), 'rt') as f:
            self.assertIn('private/store/CANARY.txt', f.read())
        self.assertIn('RESTORE.txt', os.listdir(q))
        # nothing else moved or changed
        self.assertEqual(snapshot(os.path.join(self.d, 'rollback')), self.guard['rollback'])
        self.assertEqual(snapshot(os.path.join(self.d, 'evidence')), self.guard['evidence'])
        self.assertEqual(snapshot(self.caddy), self.guard['caddy'])
        self.assertEqual(snapshot(os.path.join(self.src, 'other-commit')), self.guard['neighbour'])
        self.assertTrue(os.path.exists(os.path.join(self.sysd, 'dfl-feed-export.timer')))
        self.assertTrue(os.path.exists(os.path.join(self.etc, 'league.env')))
        self.assertIn('systemctl daemon-reload', open(self.log).read())
        for word in ('start', 'enable', 'stop', 'disable', 'reload caddy', 'restart'):
            self.assertNotIn('systemctl ' + word, open(self.log).read())

    def test_restore_puts_everything_back_identically(self):
        self.assertEqual(self.run_sh().returncode, 0)
        q = self.qdir()
        r = self.run_sh('--restore', q)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('restore verified', r.stdout)
        self.assertEqual(snapshot(self.reh), self.before['reh'])
        self.assertEqual(snapshot(os.path.join(self.src, COMMIT)), self.before['code'])
        self.assertEqual(snapshot(self.sysd), self.before['unit'])
        self.assertEqual(snapshot(self.etc), self.before['etc'])
        self.assertTrue(os.path.isdir(q))                                              # the quarantine directory itself is kept

    def test_restore_refuses_if_an_original_path_exists_again_and_moves_nothing(self):
        self.assertEqual(self.run_sh().returncode, 0)
        q = self.qdir()
        write(os.path.join(self.etc, 'rehearsal.env'), 'a newer one')
        r = self.run_sh('--restore', q)
        self.assertEqual(r.returncode, 5, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(self.reh))
        self.assertTrue(os.path.exists(os.path.join(q, 'items' + self.reh)))
        self.assertEqual(open(os.path.join(self.etc, 'rehearsal.env')).read(), 'a newer one')

    def test_active_or_enabled_units_and_an_installed_block_are_refused_and_nothing_moves(self):
        for env in ({'UNIT_ACTIVE': 'active'}, {'UNIT_ENABLED': 'enabled'}):
            r = self.run_sh(**env)
            self.assertEqual(r.returncode, 3, (env, r.stdout, r.stderr))
            self.assertEqual(snapshot(self.reh), self.before['reh'])
        write(os.path.join(self.caddy, 'conf.d', 'dflv2-rehearsal.caddy'), 'block')
        r = self.run_sh()
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertEqual(snapshot(self.reh), self.before['reh'])
        self.assertFalse(os.path.exists(self.q))

    def test_production_and_system_paths_are_refused(self):
        for bad in ('/srv/dflv2', '/srv/dflv2/feed', '/var/www/dfl', '/', '/srv/other', os.path.join(self.d, 'srv', '..', 'srv', 'dflv2-rehearsal')):
            r = self.run_sh(REH=bad)
            self.assertEqual(r.returncode, 2, (bad, r.stdout, r.stderr))
        self.assertEqual(snapshot(self.reh), self.before['reh'])

    def test_nothing_to_do_is_not_an_error(self):
        subprocess.run(['rm', '-rf', self.reh, os.path.join(self.src, COMMIT), os.path.join(self.sysd, 'dfl-rehearsal.service'),
                        os.path.join(self.sysd, 'dfl-rehearsal-monitor.service'), os.path.join(self.etc, 'rehearsal.env')])
        r = self.run_sh()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('nothing to set aside', r.stdout)


if __name__ == '__main__':
    unittest.main()
