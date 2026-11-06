"""tools/storage_inventory.py: the read-only retention inventory (docs/STORAGE_RETENTION.md) over a synthetic three-season store run through the daemon in virtual time.

It must (1) change nothing, (2) call removable only what is behind a hand-over AND has a public copy of the same size, (3) never name anything of the season on air or of a
season still being built, (4) never name the league, the archive, the plans, the published replays or the indexes, and (5) contain no code that deletes or rewrites."""
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT))
from dflsite import broadcast as B, season as S
from tests import test_multiseason as M

spec = importlib.util.spec_from_file_location('storage_inventory', str(ROOT / 'tools/storage_inventory.py'))
INV = importlib.util.module_from_spec(spec)
spec.loader.exec_module(INV)


def snapshot(*roots):
    out = {}
    for root in roots:
        for d, _, names in os.walk(root):
            for n in names:
                p = os.path.join(d, n)
                st = os.stat(p)
                out[p] = (st.st_size, st.st_mtime_ns, hashlib.sha256(open(p, 'rb').read()).hexdigest())
    return out


class Inventory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        B.FSYNC = False
        tmp = cls.tmp.name
        cls.root = os.path.join(tmp, 'root')
        os.makedirs(os.path.join(cls.root, 'league'))
        open(os.path.join(cls.root, 'league', 'league.db'), 'wb').write(b'L' * 1000)
        open(os.path.join(cls.root, 'league', 'league.db.archive'), 'wb').write(b'A' * 5000)
        cls.public = os.path.join(tmp, 'public')
        store = os.path.join(cls.root, 'store')
        cls.y = [M.Year(store, 901, 0, True), M.Year(store, 902, 4, False), M.Year(store, 903, 8, False)]
        for y in cls.y:
            y.build(4)
        cls.end1 = cls.y[0].plan['total_s']
        bc = S.SeasonBroadcaster(store, cls.public, guard_publication=False)
        bc.clock.start(M.T0, 0.0, B.LEASE_S)
        t = 0
        cls.end_all = cls.end1 + cls.y[1].plan['total_s'] + 600            # a little into season 903: seasons 901 and 902 are behind the clock
        while t <= cls.end_all:
            bc.tick(M.T0 + t)
            t += 5
        cls.store = store
        cls.carry = B.get(os.path.join(store, S.CARRY))

    @classmethod
    def tearDownClass(cls):
        B.FSYNC = True
        cls.tmp.cleanup()

    def rows(self, **kw):
        return INV.inventory(self.root, self.public, **kw)

    def test_the_run_reached_season_three(self):
        self.assertEqual(self.carry['season'], 903)
        self.assertEqual(self.carry['seasons'], [901, 902])

    def test_it_changes_nothing(self):
        before = snapshot(self.root, self.public)
        INV.inventory(self.root, self.public)
        INV.summarize(INV.inventory(self.root, self.public))
        self.assertEqual(snapshot(self.root, self.public), before)

    def test_only_seasons_behind_the_carry_record_are_candidates(self):
        cands = [r for r in self.rows() if r['class'] == 'cache_after_handover']
        self.assertTrue(cands)
        self.assertEqual({r['season'] for r in cands}, {901, 902})
        for r in cands:
            self.assertNotIn('903', os.path.basename(r['path']))

    def test_every_candidate_has_a_public_copy_of_the_same_size(self):
        for r in self.rows():
            if r['class'] != 'cache_after_handover' or '/games/' not in '/' + r['path']:
                continue
            slug = os.path.basename(r['path'])
            if r['path'].startswith('store/games/'):
                end = json.loads(open(os.path.join(self.store, 'games', slug, 'end.json'), 'rb').read())
                pub = os.path.join(self.public, 'broadcast', 'replays', end['replay'])
                self.assertTrue(os.path.isfile(pub), slug)
                self.assertEqual(os.path.getsize(pub), os.path.getsize(os.path.join(self.store, 'games', slug, 'replay.json')))

    def test_the_league_the_archive_and_the_published_replays_are_never_candidates(self):
        for r in self.rows():
            if r['class'] in ('cache_after_handover', 'old_live', 'duplicate_public'):
                self.assertNotIn('league', r['path'])
                self.assertFalse(r['path'].startswith('public:playback/'), r['path'])
                self.assertNotIn('index.json', r['path'])
                self.assertNotIn('latest.json', r['path'])
                self.assertNotIn('plan.json', r['path'])
                self.assertNotIn('builds.json', r['path'])
        perm = {r['path'] for r in self.rows() if r['class'] == 'permanent'}
        self.assertIn('league/league.db', perm)
        self.assertIn('league/league.db.archive', perm)
        self.assertTrue(any(p.startswith('public:playback/games/') for p in perm))

    def test_season_on_air_is_never_a_candidate(self):
        for r in self.rows():
            if r['class'] in ('cache_after_handover', 'old_live'):
                self.assertNotEqual(r['season'], 903, r)

    def test_without_a_public_folder_nothing_that_needs_a_public_copy_is_removable(self):
        rows = INV.inventory(self.root, None)
        self.assertEqual([r for r in rows if r['class'] in ('cache_after_handover', 'old_live', 'duplicate_public')], [])

    def test_old_live_follows_the_keep_setting(self):
        rows = self.rows(keep_live=2)
        old = {r['season'] for r in rows if r['class'] == 'old_live'}
        self.assertEqual(old, {901}, 'season 901 is older than the newest two (903, 902)')
        rows = self.rows(keep_live=1)
        self.assertEqual({r['season'] for r in rows if r['class'] == 'old_live'}, {901, 902})
        rows = self.rows(keep_live=9)
        self.assertEqual([r for r in rows if r['class'] == 'old_live'], [])

    def test_replay_twins_are_listed_as_duplicates(self):
        dup = [r for r in self.rows() if r['class'] == 'duplicate_public']
        self.assertGreaterEqual(len(dup), 6)
        for r in dup:
            twin = os.path.join(self.public, 'playback', 'games', os.path.basename(r['path']))
            self.assertTrue(os.path.isfile(twin))

    def test_the_summary_adds_up(self):
        rows = self.rows()
        s = INV.summarize(rows)
        self.assertEqual(s['total_bytes'], sum(r['bytes'] for r in rows))
        self.assertEqual(s['reclaimable_after_handover_bytes'], sum(r['bytes'] for r in rows if r['class'] == 'cache_after_handover'))
        self.assertFalse(s['deletes_anything'])
        games = sum(r['bytes'] for r in rows if r['path'].startswith('store/games/'))
        self.assertEqual(games, INV.size_of(os.path.join(self.store, 'games'))[0])

    def test_the_program_contains_nothing_that_removes_or_rewrites(self):
        text = (ROOT / 'tools/storage_inventory.py').read_text()
        code = re.sub(r'"""[\s\S]*?"""', '', text)
        for word in ('rmtree', 'unlink', 'os.remove', 'os.rename', 'os.replace', 'shutil.move', 'os.link', 'os.symlink', 'os.truncate', 'os.makedirs', 'subprocess', 'os.system'):
            self.assertNotIn(word, code, word)
        self.assertNotRegex(code, r"open\([^)]*['\"][wax+]b?['\"]")
        for o in re.findall(r'open\(([^)]*)\)', code):
            self.assertIn('"rb"', o, 'every open() reads: ' + o)


if __name__ == '__main__':
    unittest.main()
