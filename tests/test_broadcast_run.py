"""The daemon's rehearsal caps (tools/broadcast_run.py): --deadline pauses and exits 0, --max-public-gb exits 70, both leave everything in place."""
import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'tests'))
from dflsite import broadcast as B, season as S, status as ST
import broadcast_run as R
from test_season import Rig


def run_daemon(argv):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = R.main(argv)
    return code, [json.loads(l) for l in err.getvalue().splitlines() if l.startswith('{')]


class Caps(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.rig = Rig(self.tmp)
        self.root = self.tmp
        self.pub = tempfile.mkdtemp()                       # outside the private root, as the daemon requires
        self.rig.public = self.pub
        self.args = ['run', '--root', self.root, '--public', self.pub, '--tick', '0.2']

    def test_deadline_pauses_and_exits_zero_and_stays_paused(self):
        code, lines = run_daemon(self.args + ['--deadline', str(time.time() + 1.5)])
        self.assertEqual(code, 0)
        self.assertTrue(any(l.get('deadline') for l in lines), lines)
        self.assertTrue(os.path.exists(os.path.join(self.rig.store, S.PAUSE)))
        self.assertEqual(ST.read(os.path.join(self.rig.public, 'status.json'))['runner']['state'], 'paused')
        pos = B.Clock(self.rig.store, os.path.join(self.rig.store, '_u')).state()
        self.assertEqual(pos['state'], 'paused')
        # a restart with a deadline already past does not continue: it pauses again at once
        code2, lines2 = run_daemon(self.args + ['--deadline', str(time.time() - 5)])
        self.assertEqual(code2, 0)
        self.assertTrue(any(l.get('deadline') for l in lines2))
        self.assertTrue(os.path.exists(os.path.join(self.rig.public, 'broadcast', 'clock.json')))     # nothing was deleted

    def test_deadline_accepts_iso_text(self):
        self.assertAlmostEqual(R.parse_deadline('2026-11-07T06:00:00Z'), 1794031200.0)
        self.assertAlmostEqual(R.parse_deadline('2026-11-07T06:00:00'), 1794031200.0)
        self.assertAlmostEqual(R.parse_deadline('2026-11-06T22:00:00-08:00'), 1794031200.0)
        self.assertEqual(R.parse_deadline('1794031200'), 1794031200.0)

    def test_public_size_cap_exits_70_and_leaves_the_folder(self):
        os.makedirs(self.rig.public, exist_ok=True)
        with open(os.path.join(self.rig.public, 'broadcast_marker.bin'), 'wb') as f:
            f.write(b'x' * 2_000_000)
        os.makedirs(os.path.join(self.rig.public, 'broadcast'), exist_ok=True)
        open(os.path.join(self.rig.public, 'broadcast', 'clock.json'), 'w').write('{}')          # makes it a folder the daemon may own
        code, lines = run_daemon(self.args + ['--max-public-gb', '0.001'])
        self.assertEqual(code, 70)
        cap = [l for l in lines if l.get('cap_exceeded')]
        self.assertEqual(len(cap), 1)
        self.assertGreater(cap[0]['public_bytes'], cap[0]['cap_bytes'])
        self.assertTrue(os.path.exists(os.path.join(self.rig.public, 'broadcast_marker.bin')))

    def test_tree_bytes_counts_files_not_links(self):
        d = os.path.join(self.tmp, 'tb'); os.makedirs(os.path.join(d, 'a/b'))
        open(os.path.join(d, 'a/b/f'), 'wb').write(b'12345'); open(os.path.join(d, 'g'), 'wb').write(b'12')
        os.symlink(os.path.join(d, 'g'), os.path.join(d, 'link'))
        self.assertEqual(R.tree_bytes(d), 7)


if __name__ == '__main__':
    unittest.main()
