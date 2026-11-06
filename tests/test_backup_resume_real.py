"""REAL-ENGINE version of tests/test_backup_resume.py: a recovery set taken across a real season hand-over, restored with every original deleted, then resumed (OPT-IN).

Needs a finished, completely built first season (`broadcast_build.py status` says season 1, 47 events built; see docs/ENGINE_COMPARISON_REPRODUCTION.md) in the
environment variable DFL_SEASON1_ROOT; it is only COPIED. Without it the whole module is skipped (and the report must say "skipped"). About 6 GB of disk and 12 to 20 minutes.

What happens: the copy gets `next-season` and its first 13 events (the offseason and week 1) built by the real builder; the real daemon runs in virtual time over all of season 1
to a minute before the hand-over; then the recovery set is taken (the public backup with max_passes=1, so it is NOT quiescent) while the daemon crosses the end of the
season INSIDE the public copy; a control copy is made, the originals are deleted, the set is restored; the control and the restored copy then go on from the same instant
after 300 s of downtime: the real builder fires two more events on each (the league digests must be equal), and the daemon runs on, in lock step, to the release of those
events. The public tree of the restored copy equals the control's at every checkpoint (names and sizes) and byte for byte at the end; the viewers' clock never jumps;
nothing of season 2 is public before its release position; every file that was public when the originals were lost is byte-identical afterwards (tests/published_bytes.py).

The one comparison exception, and its limit: the two events built AFTER the restore were built by two builders at two moments, and the builder stamps `generated_at` into each new
release's manifest. So, and only so, for the release folders that did not exist when the originals were lost: `releases/<new>/manifest.json` is compared with its top-level
`generated_at` removed (it must be present in both), and `latest.json` (whose `manifest_sha256` is the digest of that manifest) is compared by its `release` name. Every other file
of a new release, and every file anywhere else, is compared byte for byte, and no previously published file is ever normalised.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'tests'))
import published_bytes as PUB
import public_backup as PBK
import recovery_set as RS
from dflsite import broadcast as B, season as S

SRC = os.environ.get('DFL_SEASON1_ROOT')
BUILD = str(ROOT / 'tools' / 'broadcast_build.py')
STORE_TOOL = str(ROOT / 'tools' / 'league_store.py')
T0 = 5000.0
VOLATILE = ('broadcast/clock.json', 'status.json', 'broadcast/schedule.json')


def sh(*a):
    p = subprocess.run([sys.executable, BUILD] + list(a), capture_output=True, text=True)
    if p.returncode:
        raise AssertionError((p.stderr or p.stdout)[-800:])
    return p.stdout


def digest(league):
    return json.loads(subprocess.run([sys.executable, STORE_TOOL, 'digest', league], capture_output=True, text=True, check=True).stdout)


def listing(public):
    out = {}
    for d, _, names in os.walk(public):
        for n in names:
            if n.startswith('.tmp-'):
                continue
            p = os.path.join(d, n)
            rel = os.path.relpath(p, public).replace(os.sep, '/')
            if rel not in VOLATILE:
                out[rel] = os.path.getsize(p)
    return out


def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for c in iter(lambda: f.read(1 << 20), b''):
            h.update(c)
    return h.hexdigest()


def without_generated_at(path):
    """A release manifest without its top-level `generated_at` (which must be there): the single field that is allowed to differ between two builders."""
    with open(path, 'rb') as f:
        doc = json.loads(f.read())
    assert isinstance(doc, dict) and 'generated_at' in doc, path
    del doc['generated_at']
    return doc


def day(bc, seconds, a, b, step):
    t = a
    while t < b:
        bc.tick(T0 + t)
        t += step
    return t


@unittest.skipUnless(SRC and os.path.isdir(SRC), 'set DFL_SEASON1_ROOT to a finished, completely built first-season root to run the real-engine hand-over test')
class RealHandOver(unittest.TestCase):
    def test_a_set_taken_across_a_real_hand_over_resumes_publication_and_building(self):
        B.FSYNC = False
        self.addCleanup(setattr, B, 'FSYNC', True)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        w = tmp.name
        root, public = os.path.join(w, 'root'), os.path.join(w, 'public')
        st = json.loads(sh('status', '--root', SRC))
        self.assertEqual((st['season'], st['events_built']), (1, 47), 'DFL_SEASON1_ROOT must be a completely built first season')
        shutil.copytree(SRC, root, symlinks=True)
        sh('next-season', '--root', root)
        sh('step', '--root', root, '--events', '13')                          # season 2: the offseason and week 1 (24 games)
        end1 = 172800                                                          # the first season's length on the show clock
        bc = S.SeasonBroadcaster(os.path.join(root, 'store'), public, guard_publication=False)
        bc.clock.start(T0, 0.0, B.LEASE_S)
        t = day(bc, 0, 0, end1 - 120, 20)
        self.assertFalse(os.path.exists(os.path.join(root, 'store', S.CARRY)))
        moved = {'t': t}
        passes = set()

        def pub_hook(event, **info):                                           # inside the public copy, after the cumulative controls: the daemon crosses the end of the season
            if event == 'file' and info['pass_no'] not in passes and info['n'] >= 200:
                passes.add(info['pass_no'])
                moved['t'] = day(bc, 0, moved['t'], moved['t'] + 400, 8)
        res = RS.create(root, public, os.path.join(w, 'set'), fsync=False, pub_hook=pub_hook, max_passes=1)
        self.assertTrue(res['ok'], res)
        self.assertFalse(res['public']['quiescent'], 'the public backup is assembled from several publication states')
        self.assertTrue(os.path.exists(os.path.join(root, 'store', S.CARRY)), 'the hand-over happened while the set was being taken')
        t_crash = moved['t'] - 8
        published = PUB.snapshot(public)                                       # everything public when the originals are lost
        old_releases = {k.split('/')[1] for k in published['files'] if k.startswith('releases/')}
        self.assertTrue(RS.verify(os.path.join(w, 'set'), deep=True)['ok'])
        ctl = os.path.join(w, 'control')
        os.makedirs(ctl)
        shutil.copytree(root, os.path.join(ctl, 'root'), symlinks=True)
        shutil.copytree(public, os.path.join(ctl, 'public'))
        shutil.rmtree(root)
        shutil.rmtree(public)                                                  # the originals are gone
        rr = RS.restore(os.path.join(w, 'set'), os.path.join(w, 'restored'), fsync=False)
        self.assertTrue(rr['ok'], rr)
        a_root, a_pub = os.path.join(ctl, 'root'), os.path.join(ctl, 'public')
        b_root, b_pub = rr['root'], rr['public']
        # building resumes on both: the same two events, the same league
        for r in (a_root, b_root):
            sh('step', '--root', r, '--events', '2')
        da, db = digest(os.path.join(a_root, 'league/league.db')), digest(os.path.join(b_root, 'league/league.db'))
        self.assertEqual(da, db)
        self.assertEqual(da['events'], 47 + 13 + 2)
        plan = B.get(os.path.join(b_root, 'store', 'seasons', 's002', 'plan.json'))
        rel = {}
        for it in plan['items']:
            if it['kind'] == 'block':
                for k in it['events']:
                    rel[k] = it['start_s'] + it['events_at']
        keys = plan['events']
        rel_pos = {plan['event_base'] + i + 1: end1 + rel[k] for i, k in enumerate(keys)}
        until = int(max(rel_pos[48 + 13], rel_pos[48 + 14]) if 48 + 14 in rel_pos else rel_pos[max(rel_pos)]) + 200
        # publication resumes on both, in lock step
        ca = S.SeasonBroadcaster(os.path.join(a_root, 'store'), a_pub, guard_publication=False)
        cb = S.SeasonBroadcaster(os.path.join(b_root, 'store'), b_pub, guard_publication=False)
        start = t_crash + 300
        last_a = last_b = None
        k = start
        n_checks = 0
        while k < until:
            step = 8 if k < end1 + 400 else 20
            ca.tick(T0 + k)
            cb.tick(T0 + k)
            pa, pb = ca.clock.position(T0 + k), cb.clock.position(T0 + k)
            self.assertAlmostEqual(pa, pb, delta=1e-6)
            for last, p in ((last_a, pa), (last_b, pb)):
                if last is not None:
                    self.assertLessEqual(p - last, step + 1e-3, 'the viewers\' clock jumped at %d' % k)
            last_a, last_b = pa, pb
            if (k - start) % 4000 < step:
                la, lb = listing(a_pub), listing(b_pub)
                self.assertEqual(la, lb, 'public trees differ at %d' % k)
                n_checks += 1
                lat = B.get(os.path.join(b_pub, 'latest.json'))['release']
                n = int(lat[1:6])
                if n > 47:
                    self.assertLessEqual(rel_pos[n], pb + 1.5, 'event %d public at clock %.0f before its release position %d' % (n, pb, rel_pos[n]))
            k += step
        self.assertGreater(n_checks, 3)
        la, lb = listing(a_pub), listing(b_pub)
        self.assertEqual(la, lb)
        new_releases = {k.split('/')[1] for k in la if k.startswith('releases/')} - old_releases
        self.assertEqual(len(new_releases), 2, new_releases)                    # the two events built after the restore
        differ = []
        for rel_name in la:                                                    # byte for byte at the end (the exception is stated in the module docstring)
            parts = rel_name.split('/')
            if rel_name == 'latest.json':
                continue                                                       # compared by its release name below
            if len(parts) == 3 and parts[0] == 'releases' and parts[1] in new_releases and parts[2] == 'manifest.json':
                if without_generated_at(os.path.join(a_pub, rel_name)) != without_generated_at(os.path.join(b_pub, rel_name)):
                    differ.append(rel_name + ' (beyond generated_at)')
            elif sha(os.path.join(a_pub, rel_name)) != sha(os.path.join(b_pub, rel_name)):
                differ.append(rel_name)
        self.assertEqual(differ, [])
        self.assertEqual(PUB.changed(published, b_pub), [], 'public before the originals were lost, changed afterwards (restored)')
        self.assertEqual(PUB.changed(published, a_pub), [], 'public before the originals were lost, changed afterwards (control)')
        self.assertEqual(PBK.CLOSURE['public'](PBK.Tree(b_pub)), [])
        self.assertEqual(B.get(os.path.join(b_pub, 'latest.json'))['release'][:6], B.get(os.path.join(a_pub, 'latest.json'))['release'][:6])


if __name__ == '__main__':
    unittest.main()
