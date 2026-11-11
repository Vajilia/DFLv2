"""The vtest installer, run END TO END on a relocated root, with only the operating system's privileged calls replaced (chown, the user database, systemctl).

Why this exists. Two installer failures reached a real machine because the tests exercised the parts with hand-built inputs, never the whole installer:
  * d24aafe: `vtest.py install` died with KeyError 'ETC' before starting anything (the installer's path dictionary lacked a key the ownership step reads);
  * baca129: the installer never created the probe folder that V0 judges, and its retry advice ("run install again") was refused by its own safety check.
These tests run `render.install` itself: the real pinned-tree extraction (git archive, every file compared with git's blob), the real unit rendering and invariant checks, the real environment file,
the real ownership step, over folders that are the harness's constants moved under a temporary directory. They need git history (the pinned commit): without it they SKIP, and say so.
The same installer is also rehearsed on a clean root with real ownership by tests/rehearse_vtest_install.py (needs root and a machine without systemd).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
VT = ROOT / 'tools' / 'vtest'
sys.path.insert(0, str(VT))

import common as C        # noqa: E402
import perms as P         # noqa: E402
import render as RN       # noqa: E402

PIN = C.read_pin(str(ROOT))
HAVE_PIN = subprocess.run(['git', '-C', str(ROOT), 'cat-file', '-e', PIN + '^{commit}'], capture_output=True).returncode == 0
USER = 'dfl-vtest'
FOLDERS = ('BASE', 'SRC_BASE', 'ETC', 'MARKER', 'UNIT_DIR', 'EXPORT', 'ROOT_DIR', 'WEB_DIR', 'PUBLIC', 'GUARD', 'EVID', 'LOADDIR', 'STATE', 'PROBE')


class Machine(C.Env):
    """The real file system under a temporary root; systemctl, the user database and chown are the only things stood in for."""
    def __init__(self):
        self.sc_calls, self.owned = [], {}

    def user_exists(self, name):
        return name == USER

    def sc(self, verb, *args, timeout=60):
        self.sc_calls.append((verb,) + args)
        return 0, '', ''

    def free_bytes(self, path):
        return 50 * 2**30


class Relocated:
    """Move every folder constant of the harness under `top`, in every module that copied it."""
    def __init__(self, top):
        self.top = top
        self.new = {'BASE': top + '/srv/dfl-vtest', 'SRC_BASE': top + '/opt/dfl-vtest-src', 'ETC': top + '/etc/dfl-vtest', 'MARKER': top + '/etc/dfl-vtest-disposable', 'UNIT_DIR': top + '/etc/systemd/system'}
        b = self.new['BASE']
        self.new.update({'EXPORT': b + '/export', 'ROOT_DIR': b + '/root', 'WEB_DIR': b + '/web', 'PUBLIC': b + '/web/feed', 'GUARD': b + '/guard', 'EVID': b + '/evidence', 'LOADDIR': b + '/load',
                         'STATE': b + '/state', 'PROBE': b + '/probe'})
        self.patches = []

    def __enter__(self):
        for mod in (C, RN):
            for k, v in self.new.items():
                if hasattr(mod, k):
                    p = mock.patch.object(mod, k, v)
                    p.start()
                    self.patches.append(p)
        return self

    def __exit__(self, *a):
        for p in reversed(self.patches):
            p.stop()


def with_fake_privileges(env):
    real = P.apply_layout
    pw = types.SimpleNamespace(pw_uid=995, pw_gid=996)

    def wrapped(user, paths, allowed, **kw):
        return real(user, paths, allowed, chown=lambda p, u, g: env.owned.__setitem__(p, (u, g)), getpw=lambda n: pw)
    return mock.patch.object(P, 'apply_layout', wrapped)


def fake_fixture(env):
    """The generated season is the one slow, privilege-free step; the rehearsal on a clean root runs it for real. Here it only makes the store folder the real one makes, and records the argument."""
    seen = {}

    def build(e, pin, slot=7200):
        seen['root'] = RN.ROOT_DIR
        os.makedirs(RN.ROOT_DIR + '/store', exist_ok=True)
        Path(RN.ROOT_DIR, 'store', 'x').write_text('x')
        return 0, '1.0\n', ''
    return mock.patch.object(RN, 'build_fixture', build), seen


@unittest.skipUnless(HAVE_PIN, 'SKIPPED: the pinned candidate commit %s is not in this checkout (a shallow clone?); CI fetches full history' % PIN[:12])
class InstallEndToEnd(unittest.TestCase):
    def setUp(self):
        self.top = tempfile.mkdtemp()
        self.addCleanup(lambda: subprocess.run(['chmod', '-R', 'u+w', self.top]) and None or shutil.rmtree(self.top, ignore_errors=True))
        self.reloc = Relocated(self.top)
        self.reloc.__enter__()
        self.addCleanup(self.reloc.__exit__)
        self.env = Machine()
        Path(RN.MARKER).parent.mkdir(parents=True, exist_ok=True)
        Path(RN.MARKER).write_text('')
        self.fix, self.seen = fake_fixture(self.env)

    def install(self):
        with with_fake_privileges(self.env), self.fix:
            return RN.install(self.env, str(ROOT), pin=PIN, user=USER)

    def test_the_installer_runs_to_the_end_and_records_what_it_did(self):
        """Fails with KeyError 'ETC' if the installer's path set misses a key the ownership step reads (d24aafe)."""
        log = self.install()
        self.assertEqual(log['pin'], PIN)
        self.assertEqual([s[0] for s in log['steps']], ['extract', 'fixture', 'layout'])
        self.assertTrue(all(s[1] for s in log['steps']), log['steps'])
        rec = Path(RN.STATE, 'install.json')
        self.assertTrue(rec.is_file())
        self.assertIn(PIN, rec.read_text())
        self.assertEqual({v for v, *_ in self.env.sc_calls}, {'daemon-reload'}, 'the installer starts nothing and changes nothing but the unit list')

    def test_every_folder_the_v0_layout_check_judges_exists_afterwards(self):
        """Fails if a folder V0 judges is never created (baca129: the probe folder)."""
        self.install()
        tree = RN.tree_dir(PIN)
        missing = [p for p, _, _ in P.layout(P.layout_paths(tree)) if not os.path.exists(p)]
        self.assertEqual(missing, [])

    def test_the_ownership_step_gave_the_service_user_exactly_its_three_folders(self):
        self.install()
        theirs = {p for p, ug in self.env.owned.items() if ug == (995, 996)}
        want = {RN.ROOT_DIR + '/store', RN.ROOT_DIR + '/store/x', RN.PUBLIC, RN.GUARD}
        self.assertEqual(theirs & {RN.BASE, RN.ROOT_DIR, RN.WEB_DIR, RN.ETC, RN.EVID, RN.STATE, RN.LOADDIR, RN.PROBE, RN.tree_dir(PIN)}, set(), 'the service user owns nothing else the harness keeps')
        self.assertTrue(want <= theirs, want - theirs)
        tree = RN.tree_dir(PIN)
        self.assertEqual(self.env.owned[tree + '/tools/broadcast_run.py'], (0, 0), 'the code is root-owned')

    def test_the_rendered_units_point_only_at_the_relocated_harness_territory(self):
        self.install()
        tree = RN.tree_dir(PIN)
        for k in ('daemon', 'monitor', 'site'):
            text = Path(RN.UNIT_DIR, C.UNIT[k]).read_text()
            self.assertNotRegex(text, r'__[A-Z_]+__', k)
            for prod in ('/srv/dflv2', '/etc/dflv2', '/opt/dflv2-src'):
                self.assertNotIn(prod, text, '%s names a production path' % k)
        daemon = Path(RN.UNIT_DIR, C.UNIT['daemon']).read_text()
        self.assertIn('%s/tools/broadcast_run.py' % tree, daemon)
        self.assertIn('--root %s' % RN.ROOT_DIR, daemon)
        self.assertIn('--public %s' % RN.PUBLIC, daemon)
        self.assertIn('--guard %s' % RN.GUARD, daemon)
        self.assertIn('EnvironmentFile=%s/broadcast.env' % RN.ETC, daemon)
        self.assertTrue(Path(tree, 'tools', 'broadcast_run.py').is_file())

    def test_the_environment_file_uses_only_keys_the_pinned_template_has(self):
        self.install()
        tpl = Path(RN.tree_dir(PIN), RN.ENV_TEMPLATE).read_text()
        have = {l.split('=')[0] for l in tpl.splitlines() if l[:1].isupper() and '=' in l}
        wrote = {l.split('=')[0] for l in Path(RN.ETC, 'broadcast.env').read_text().splitlines() if '=' in l}
        self.assertTrue(wrote <= have, wrote - have)

    def test_a_second_install_is_refused_names_teardown_and_changes_nothing(self):
        """baca129: the advice to 'run install again' was refused by the installer's own pinned-tree check."""
        self.install()
        before = (sorted(os.listdir(RN.SRC_BASE)), Path(RN.STATE, 'install.json').read_text())
        with self.assertRaises(C.Unsafe) as cm, with_fake_privileges(self.env), self.fix:
            RN.install(self.env, str(ROOT), pin=PIN, user=USER)
        self.assertIn('teardown', str(cm.exception))
        self.assertEqual(before, (sorted(os.listdir(RN.SRC_BASE)), Path(RN.STATE, 'install.json').read_text()))

    def test_teardown_dry_run_lists_the_probe_folder_keeps_the_evidence_and_removes_nothing(self):
        self.install()
        lines = []
        RN.teardown(self.env, include_evidence=False, yes=False, echo=lines.append)
        removed = [l for l in lines if l.startswith('remove tree')]
        self.assertTrue(any(l.endswith(RN.PROBE) for l in removed), removed)
        self.assertFalse(any(l.endswith(RN.EVID) for l in removed), removed)
        self.assertTrue(any('dry run' in l for l in lines))
        self.assertTrue(os.path.isdir(RN.PROBE) and os.path.isdir(RN.STATE))

    def test_installer_refuses_without_the_operators_marker(self):
        os.unlink(RN.MARKER)
        with self.assertRaises(C.Unsafe) as cm, with_fake_privileges(self.env), self.fix:
            RN.install(self.env, str(ROOT), pin=PIN, user=USER)
        self.assertIn('disposable', str(cm.exception))
        self.assertFalse(os.path.exists(RN.SRC_BASE + '/' + PIN[:12]), 'refused before extracting anything')


class RelocationIsReal(unittest.TestCase):
    def test_the_test_never_writes_outside_its_temporary_root(self):
        with tempfile.TemporaryDirectory() as top, Relocated(top):
            for k in FOLDERS:
                v = getattr(RN, k, None) or getattr(C, k)
                self.assertTrue(v.startswith(top), '%s = %s' % (k, v))
            self.assertTrue(C.allowed_path(top + '/srv/dfl-vtest/x'))
            self.assertFalse(C.allowed_path('/srv/dfl-vtest/x'), 'the real harness folder must not be allowed while relocated')


if __name__ == '__main__':
    unittest.main()
