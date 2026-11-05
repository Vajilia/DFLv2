"""The private builder (tools/broadcast_build.py) on a fresh isolated league: what it makes, where, and what it refuses. Uses the real engine for 13 events
(the offseason, week 1 and Tour round 1), about a minute and a half."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
from dflsite import broadcast as B, season as S

BUILD = os.path.join(ROOT, 'tools', 'broadcast_build.py')


def sh(*a, check=True):
    p = subprocess.run([sys.executable, BUILD] + list(a), capture_output=True, text=True)
    if check and p.returncode:
        raise AssertionError(p.stderr[-800:] or p.stdout[-800:])
    return p


class Builder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.parent = cls.tmp.name
        cls.root = os.path.join(cls.parent, 'season')
        sh('init', '--root', cls.root, '--seed', '4242')
        sh('step', '--root', cls.root, '--events', '13')
        cls.store = os.path.join(cls.root, 'store')
        cls.builds = B.get(os.path.join(cls.store, S.BUILDS))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_everything_is_inside_the_private_root(self):
        self.assertEqual(os.listdir(self.parent), ['season'])
        self.assertEqual(sorted(os.listdir(self.root)), ['build.log.jsonl', 'league', 'store'])

    def test_each_event_has_a_private_release_and_its_own_pointer(self):
        self.assertEqual(sorted(int(k) for k in self.builds['events']), list(range(1, 14)))
        for seq, ev in self.builds['events'].items():
            self.assertTrue(os.path.isdir(os.path.join(self.store, 'feed/releases', ev['release'])))
            ptr = B.get(os.path.join(self.store, ev['pointer']))
            self.assertEqual(ptr['release'], ev['release'])
            self.assertTrue(ev['release'].startswith('e%05d-' % int(seq)))
        self.assertEqual(self.builds['genesis']['events'], 0)

    def test_week_one_games_are_recorded_with_the_published_replay_bytes(self):
        games = self.builds['games']
        self.assertEqual(len(games), 24)
        self.assertEqual({g['key'] for g in games.values()}, {'week_1', 'tour_round_1'})
        for gid, g in list(games.items())[:3]:
            d = os.path.join(self.store, 'games', B.safe(gid))
            raw = open(os.path.join(d, 'replay.json'), 'rb').read()
            pub = open(os.path.join(self.store, 'playback', g['entry']['file']), 'rb').read()
            self.assertEqual(raw, pub)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), g['entry']['sha256'])
            end = B.get(os.path.join(d, 'end.json'))
            self.assertEqual(end['replay'], g['replay'])
            self.assertGreater(g['length_s'], 300)
            self.assertEqual(g['delay_s'], B.DELAY_S)

    def test_the_plan_places_every_built_game_in_a_window(self):
        plan = B.get(os.path.join(self.store, S.PLAN))
        view = S.materialize(plan, self.builds)
        self.assertEqual(len(view.windows), 5)
        self.assertEqual(sum(len(w['games']) for w in view.windows), 24)
        self.assertIsNotNone(view.horizon, 'the rest of the year is not built yet')
        self.assertTrue(all(w['overrun_s'] == 0 for w in view.windows))

    def test_the_league_is_fired_by_the_builder_alone(self):
        sh('init', '--root', os.path.join(self.parent, 'other'), '--seed', '1')
        other = os.path.join(self.parent, 'other')
        sh('step', '--root', other, '--events', '9')
        runner = os.path.join(ROOT, 'tools', 'league_runner.py')
        subprocess.run([sys.executable, runner, os.path.join(other, 'league/league.db'), '--until-events', '11', '--control-dir', os.path.join(other, 'league/ctl')], check=True,
                       capture_output=True)
        p = sh('step', '--root', other, '--events', '1', check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('one event at a time', p.stderr)

    def test_a_second_init_on_the_same_folder_is_refused(self):
        p = sh('init', '--root', self.root, '--seed', '4242', check=False)
        self.assertNotEqual(p.returncode, 0)


if __name__ == '__main__':
    unittest.main()
