"""tools/build_rehearsal_package.py: what goes in, what is refused, checksums, forced overrun. Uses the synthetic year of test_season and this checkout's own HEAD."""
import json
import os
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
from dflsite import broadcast as B, season as S
from test_season import Rig


def have_git():
    try:
        subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, check=True, capture_output=True)
        return True
    except Exception:
        return False


@unittest.skipUnless(have_git(), 'needs a git checkout')
class Package(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        B.FSYNC = False
        self.addCleanup(setattr, B, 'FSYNC', True)
        self.rig = Rig(self.tmp.name)
        self.out = os.path.join(self.tmp.name, 'out'); os.makedirs(self.out)
        p = mock.patch.object(RC, 'preflight', lambda *a, **k: [(True, 'stubbed')])
        p.start(); self.addCleanup(p.stop)

    def build(self, **kw):
        return BP.build('HEAD', self.tmp.name, self.out, **kw)

    def test_package_contents_and_checksums(self):
        path, meta = self.build(note='test')
        self.assertEqual(len(meta['commit']), 40)
        self.assertEqual(subprocess.run(['sha256sum', '-c', path + '.sha256'], cwd=os.path.dirname(path), capture_output=True).returncode, 0)
        x = os.path.join(self.tmp.name, 'x'); os.makedirs(x)
        with tarfile.open(path) as tf:
            tf.extractall(x, filter='data')
        pkg = os.path.join(x, os.listdir(x)[0])
        self.assertEqual(subprocess.run(['sha256sum', '-c', 'SHA256SUMS', '--quiet'], cwd=pkg, capture_output=True).returncode, 0)
        for need in ('repo/src/dflsite/season.py', 'repo/tools/broadcast_run.py', 'repo/tools/rehearsal_check.py', 'repo/site/index.html', 'repo/deploy/rehearsal/install_rehearsal.sh', 'store.tar.gz', 'PACKAGE.json'):
            self.assertTrue(os.path.exists(os.path.join(pkg, need)), need)
        self.assertFalse(os.path.exists(os.path.join(pkg, 'repo', 'data')))               # no league data, no tests
        self.assertFalse(os.path.exists(os.path.join(pkg, 'repo', 'tests')))
        with tarfile.open(os.path.join(pkg, 'store.tar.gz')) as tf:
            names = tf.getnames()
        self.assertIn('store/plan.json', names)
        self.assertIn('store/builds.json', names)
        self.assertIn('store/CANARY.txt', names)
        with tarfile.open(os.path.join(pkg, 'store.tar.gz')) as tf:
            token = tf.extractfile('store/CANARY.txt').read().decode().strip()
        self.assertRegex(token, r'^DFL-REHEARSAL-CANARY-[0-9a-f]{32}$')
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, 'store', 'CANARY.txt')), 'the source store is not touched')
        self.assertEqual(json.load(open(os.path.join(pkg, 'PACKAGE.json')))['canary']['path'], 'store/CANARY.txt')
        self.assertFalse([n for n in names if n.endswith(('.db', 'clock.json', 'PAUSE', 'daemon.lock'))])
        meta2 = json.load(open(os.path.join(pkg, 'PACKAGE.json')))
        self.assertEqual(meta2['commit'], meta['commit'])
        self.assertIn('league database', meta2['does_not_contain'])

    def test_a_store_that_was_broadcast_or_holds_a_league_file_is_refused(self):
        open(os.path.join(self.tmp.name, 'store', 'clock.json'), 'w').write('{}')
        with self.assertRaises(SystemExit) as cm:
            self.build()
        self.assertIn('never was', str(cm.exception))
        os.unlink(os.path.join(self.tmp.name, 'store', 'clock.json'))
        open(os.path.join(self.tmp.name, 'store', 'league.db'), 'w').write('x')
        with self.assertRaises(SystemExit) as cm:
            self.build()
        self.assertIn('database', str(cm.exception))

    def test_a_failing_preflight_stops_the_build(self):
        with mock.patch.object(RC, 'preflight', lambda *a, **k: [(False, 'the store is incomplete')]):
            with self.assertRaises(SystemExit) as cm:
                self.build()
        self.assertIn('incomplete', str(cm.exception))
        self.assertEqual(os.listdir(self.out), [])

    def test_forced_overrun_is_in_the_packages_copy_only(self):
        original = open(os.path.join(self.tmp.name, 'store', 'plan.json'), 'rb').read()
        path, meta = self.build(force=['w000:100'])
        self.assertEqual(open(os.path.join(self.tmp.name, 'store', 'plan.json'), 'rb').read(), original)       # R is never touched
        self.assertGreater(meta['store']['forced_overruns']['w000']['overrun_s'], 0)
        x = os.path.join(self.tmp.name, 'x'); os.makedirs(x)
        with tarfile.open(path) as tf:
            tf.extractall(x, filter='data')
        pkg = os.path.join(x, os.listdir(x)[0])
        with tarfile.open(os.path.join(pkg, 'store.tar.gz')) as tf:
            plan = json.load(tf.extractfile('store/plan.json'))
        self.assertEqual([i['slot_s'] for i in plan['items'] if i.get('key') == 'w000'], [100])
        self.assertEqual([i['slot_s'] for i in plan['items'] if i.get('key') == 'w001'], [self.rig.slot])

    def test_a_forced_slot_that_does_not_overrun_or_names_nothing_is_refused(self):
        with self.assertRaises(SystemExit):
            self.build(force=['w000:5000'])
        with self.assertRaises(SystemExit):
            self.build(force=['w999:100'])


if __name__ == '__main__':
    unittest.main()
