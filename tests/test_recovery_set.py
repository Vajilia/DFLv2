"""The broadcast recovery set (tools/recovery_set.py): league backup + private store + public folder, taken and restored as a unit.

Uses the real engine and the real builder for 13 events (offseason, week 1: 24 games) once, in a temporary folder (about a minute and a half), then copies it for each test.
The daemon runs in virtual time on the real store. Nothing touches a production directory.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import public_backup as PBK
import recovery_set as RS
from dflsite import broadcast as B, season as S

BUILD = os.path.join(ROOT, 'tools', 'broadcast_build.py')
T0 = 5000.0


def sh(*a, check=True):
    p = subprocess.run([sys.executable, BUILD] + list(a), capture_output=True, text=True)
    if check and p.returncode:
        raise AssertionError(p.stderr[-800:] or p.stdout[-800:])
    return p


def digest(league):
    return json.loads(subprocess.run([sys.executable, os.path.join(ROOT, 'tools/league_store.py'), 'digest', league], capture_output=True, text=True, check=True).stdout)


def publish(root, public, upto, start=0, state_dir=None):
    B.FSYNC = False
    bc = S.SeasonBroadcaster(os.path.join(root, 'store'), public, guard_publication=False)
    if bc.clock.state() is None:
        bc.clock.start(T0, 0.0, B.LEASE_S)
    for t in range(start, upto, 20):
        bc.tick(T0 + t)
    return bc


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.template = os.path.join(cls.tmp.name, 'template')
        sh('init', '--root', os.path.join(cls.template, 'root'), '--seed', '4242')
        sh('step', '--root', os.path.join(cls.template, 'root'), '--events', '13')
        cls.pub_template = os.path.join(cls.template, 'public')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.w = tempfile.mkdtemp(dir=self.tmp.name)
        self.root = os.path.join(self.w, 'root')
        self.public = os.path.join(self.w, 'public')
        shutil.copytree(os.path.join(self.template, 'root'), self.root)
        self.bc = publish(self.root, self.public, 4000)               # virtual: the daemon publishes what is built
        self.assertTrue(os.path.exists(os.path.join(self.public, 'latest.json')))

    def set_path(self, name='set'):
        return os.path.join(self.w, name)


class Set(Fixture):
    def test_create_verify_restore_and_carry_on(self):
        res = RS.create(self.root, self.public, self.set_path(), fsync=False)
        self.assertTrue(res['ok'])
        self.assertEqual(res['league']['events'], 13)
        self.assertEqual(res['store']['built_events'], 13)
        self.assertGreaterEqual(res['public']['public_events'], 1)
        v = RS.verify(self.set_path())
        self.assertTrue(v['ok'], v['problems'])
        # the builder fires event 14 in the ORIGINAL; the restored copy must reach the same league state when it does the same (determinism across the restore)
        out = os.path.join(self.w, 'restored')
        shutil.move(self.root, os.path.join(self.w, 'original'))           # the original is no longer where it was
        shutil.rmtree(self.public)
        rr = RS.restore(self.set_path(), out, fsync=False)
        self.assertTrue(rr['ok'], rr)
        for rel in ('league/league.db', 'league/league.db.archive', 'league/ctl/events.jsonl', 'store/builds.json', 'public/latest.json'):
            self.assertTrue(os.path.exists(os.path.join(out, rel)), rel)
        st = json.loads(sh('status', '--root', out).stdout)
        self.assertEqual((st['events_built'], st['games_built']), (13, 24))
        sh('step', '--root', out, '--events', '1')                                   # the builder carries on from the restored set
        sh('step', '--root', os.path.join(self.w, 'original'), '--events', '1')
        a, b = digest(os.path.join(out, 'league/league.db')), digest(os.path.join(self.w, 'original/league/league.db'))
        self.assertEqual(a, b)
        self.assertEqual(a['events'], 14)
        # the daemon carries on from the restored store and public folder: nothing already public changes, event 14 appears when its time comes
        before = {k: v for k, v in PBK_tree(os.path.join(out, 'public')).items()}
        bc = S.SeasonBroadcaster(os.path.join(out, 'store'), os.path.join(out, 'public'), guard_publication=False)
        for t in range(4020, 60000, 20):
            bc.tick(T0 + t)
        after = PBK_tree(os.path.join(out, 'public'))
        for k, h in before.items():
            if not k.endswith('index.json') and k not in ('latest.json', 'status.json', 'broadcast/schedule.json', 'broadcast/clock.json') and '/live/' not in k:
                self.assertEqual(after.get(k), h, k)
        self.assertEqual(PBK.CLOSURE['public'](PBK.Tree(os.path.join(out, 'public'))), [])
        self.assertEqual(json.load(open(os.path.join(out, 'public/latest.json')))['release'][:6], 'e00014', 'the event built after the restore is published by the restored daemon')

    def test_a_builder_that_moves_during_the_set_makes_it_invalid(self):
        def hook(event, **info):
            if event == 'after_public':
                sh('step', '--root', self.root, '--events', '1')
        with self.assertRaises(RS.Failed) as c:
            RS.create(self.root, self.public, self.set_path(), fsync=False, hook=hook)
        self.assertIn('builder must be idle', str(c.exception))
        self.assertFalse(os.path.exists(self.set_path()))
        v = RS.verify(self.set_path())
        self.assertFalse(v['ok'])

    def test_a_set_that_fails_before_the_commit_is_never_valid(self):
        def hook(event, **info):
            if event == 'after_store':
                raise RuntimeError('killed')
        with self.assertRaises(RuntimeError):
            RS.create(self.root, self.public, self.set_path(), fsync=False, hook=hook)
        self.assertFalse(os.path.exists(self.set_path()))
        self.assertFalse(RS.verify(self.set_path())['ok'])
        self.assertFalse(RS.verify(self.set_path() + '.partial')['ok'])
        self.assertFalse(RS.restore(self.set_path(), os.path.join(self.w, 'x'), fsync=False)['ok'])

    def test_damaged_or_swapped_components_are_reported(self):
        RS.create(self.root, self.public, self.set_path(), fsync=False)
        self.assertTrue(RS.verify(self.set_path())['ok'])
        victim = os.path.join(self.set_path(), 'public', 'files', 'latest.json')
        raw = open(victim, 'rb').read()
        open(victim, 'wb').write(raw + b' ')
        v = RS.verify(self.set_path())
        self.assertFalse(v['ok'])
        self.assertFalse(RS.restore(self.set_path(), os.path.join(self.w, 'x'), fsync=False)['ok'])
        self.assertFalse(os.path.exists(os.path.join(self.w, 'x')))
        open(victim, 'wb').write(raw)
        self.assertTrue(RS.verify(self.set_path())['ok'])
        # another valid public backup (taken later) swapped in: valid on its own, not the one this set recorded
        self.bc.tick(T0 + 4520)
        shutil.rmtree(os.path.join(self.set_path(), 'public'))
        PBK.create(self.public, os.path.join(self.set_path(), 'public'), 'public', fsync=False)
        v = RS.verify(self.set_path())
        self.assertFalse(v['ok'])
        self.assertIn('not the backup this set recorded', ' '.join(v['problems']))
        os.unlink(os.path.join(self.set_path(), RS.DESCRIPTOR))
        self.assertFalse(RS.verify(self.set_path())['ok'])

    def test_a_league_backup_that_is_not_the_one_recorded_is_reported(self):
        RS.create(self.root, self.public, self.set_path(), fsync=False)
        mp = os.path.join(self.set_path(), 'league', 'BACKUP_MANIFEST.json')
        m = json.load(open(mp))
        m['note'] = 'edited'
        json.dump(m, open(mp, 'w'))
        self.assertFalse(RS.verify(self.set_path())['ok'])


def PBK_tree(base):
    out = {}
    for d, _, names in os.walk(base):
        for n in names:
            p = os.path.join(d, n)
            if n.startswith('.tmp-'):
                continue
            out[os.path.relpath(p, base).replace(os.sep, '/')] = hashlib.sha256(open(p, 'rb').read()).hexdigest()
    return out


class Rules(unittest.TestCase):
    def test_each_rule_fires_on_its_own_violation(self):
        ok = dict(league_ev=13, built=13, season_on_air=1, pub_ev=13, pub_season=1, store_quiet=True, pub_ok=True, store_ok=True)
        self.assertEqual(RS.rules(**ok), [])
        self.assertEqual(RS.rules(**dict(ok, league_ev=14)), [])                      # one event fired but not built: allowed
        for change, tag in (({'league_ev': 15}, 'S1'), ({'league_ev': 12}, 'S1'), ({'pub_ev': 14}, 'S2'), ({'pub_season': 2}, 'S3'),
                            ({'store_quiet': False}, 'S4'), ({'pub_ok': False}, 'S4')):
            r = RS.rules(**dict(ok, **change))
            self.assertTrue(r and r[0].startswith(tag), (change, r))


if __name__ == '__main__':
    unittest.main()
