"""The active viewing target, game groups and additive overruns, independently checked."""
import os
import sys
import unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'tools'))
import broadcast_timetable as T


class Timetable(unittest.TestCase):
    def test_default_is_48_active_hours_with_400_games_and_49_minute_offseason(self):
        params, segments = T.build()
        summary = T.summarize(params, segments)
        self.assertEqual(summary['total_s'], 48 * 3600)
        self.assertEqual(summary['games'], 400)
        self.assertEqual(summary['windows'], 106)
        self.assertEqual(summary['offseason_min'], 49)
        self.assertEqual({s['end_s']-s['start_s'] for s in segments if s['kind']=='window'}, {960})
        self.assertTrue(all(a['end_s'] <= b['start_s'] for a,b in zip(segments, segments[1:])))

    def test_breaks_do_not_change_kickoff_groups_or_postseason_order(self):
        _, padded = T.build()
        _, base = T.build({'target_s': 0})
        for a, b in zip(padded, base):
            self.assertEqual((a['kind'], a['label'], a.get('games'), a.get('tour_games')), (b['kind'], b['label'], b.get('games'), b.get('tour_games')))
        windows = [s for s in padded if s['kind']=='window']
        self.assertEqual([s['games'] for s in windows[-7:]], [2,2,2,2,2,1,1])

    def test_overruns_extend_the_finish_without_shortening_later_breaks(self):
        _, original = T.build()
        changed = T.apply_overruns(original, {0: 1000.1, 2: 1100})
        self.assertEqual(changed[-1]['end_s'] - original[-1]['end_s'], 181)
        self.assertEqual(original[-1]['end_s'], 172800)
        for i, (a,b) in enumerate(zip(original, changed)):
            if a['kind'] != 'window':
                self.assertEqual(a['end_s']-a['start_s'], b['end_s']-b['start_s'])
            if i:
                self.assertEqual(a['start_s'] - original[i-1]['end_s'], b['start_s'] - changed[i-1]['end_s'])

    def test_invalid_overrun_is_refused(self):
        _, segments = T.build()
        for n in (-1, float('inf'), float('nan')):
            with self.assertRaises(ValueError):
                T.apply_overruns(segments, {0:n})
