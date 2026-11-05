"""The season plan (tools/broadcast_plan.py): every game in a window, every event revealed once, in the engine's own order."""
import os
import sys
import unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import broadcast_plan as BP
from dflsite import season as S
from dfl import calendar as C


class Plan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = BP.build_plan(season=1)

    def test_events_are_the_engines_events_in_firing_order_each_revealed_once(self):
        self.assertEqual(self.plan['events'], [k for _, k, _, _ in C.year_events(2000)])
        self.assertEqual(len(self.plan['events']), 47)

    def test_timeline_is_ordered_and_is_48_active_hours(self):
        items = self.plan['items']
        self.assertEqual(self.plan['total_s'], 48 * 3600)
        for a, b in zip(items, items[1:]):
            end = a['start_s'] + a['slot_s'] if a['kind'] == 'window' else a['end_s']
            self.assertLessEqual(end, b['start_s'])

    def test_every_regular_and_tour_game_has_a_window_and_counts_match(self):
        counts = {}
        for g, k in self.plan['games'].items():
            counts[k] = counts.get(k, 0) + 1
        wins = [i for i in self.plan['items'] if i['kind'] == 'window']
        self.assertEqual(len(wins), 106)
        post = set(sum(self.plan['rounds'].values(), [])) | set(self.plan['single'].values())
        for w in wins:
            if w['key'] not in post:
                self.assertEqual(counts[w['key']], w['expected'], w['label'])
        self.assertEqual(sum(w['expected'] for w in wins), 400)
        self.assertEqual(len(self.plan['games']), 388)

    def test_simultaneous_games_share_one_window_and_calendar_order_is_kept(self):
        # week 1: the first Saturday window holds one regular and one Tour game that kick off together
        self.assertEqual(self.plan['games']['reg:w01:g01'], self.plan['games']['tour:r1:g01'])
        keys = [i['key'] for i in self.plan['items'] if i['kind'] == 'window']
        self.assertEqual(keys, sorted(keys))

    def test_postseason_rounds_use_pairs_by_conference(self):
        gids = ['S001/po:Eastern:wild_card:s6v3', 'S001/po:Eastern:wild_card:s5v4', 'S001/po:Western:wild_card:s5v4', 'S001/po:Western:wild_card:s6v3']
        got = S.assign_round(self.plan, 'wild_card', gids)
        east = {got[g] for g in gids if ':Eastern:' in g}
        west = {got[g] for g in gids if ':Western:' in g}
        self.assertEqual(len(east), 1)
        self.assertEqual(len(west), 1)
        self.assertNotEqual(east, west)
        with self.assertRaises(S.SeasonError):
            S.assign_round(self.plan, 'wild_card', gids[:3])

    def test_playoff_and_ceremony_windows_are_gated_and_regular_ones_are_not(self):
        gated = [i['label'] for i in self.plan['items'] if i['kind'] == 'window' and i['gated']]
        self.assertEqual(len(gated), 7)
        self.assertTrue(all('week' not in g for g in gated))

    def test_a_game_of_another_season_is_refused(self):
        with self.assertRaises(S.SeasonError):
            S.window_of(self.plan, 'S002/reg:w01:g01')
        self.assertEqual(S.window_of(self.plan, 'S001/bowl'), self.plan['single']['ambassador_bowl'])
        self.assertIsNone(S.window_of(self.plan, 'S001/po:Eastern:divisional:s1v4'))

    def test_honors_parts_are_revealed_inside_the_honors_show(self):
        show = [i for i in self.plan['items'] if i['kind'] == 'block' and i['role'] == 'honors'][0]
        self.assertEqual([p['part'] for p in show['parts']], ['tiara', 'halls', 'fan_entrant'])
        for p in show['parts']:
            self.assertLess(p['at'], show['end_s'] - show['start_s'])
        self.assertLess(show['events_at'], show['end_s'] - show['start_s'])


if __name__ == '__main__':
    unittest.main()
