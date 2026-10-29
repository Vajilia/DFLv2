"""The integrated year: every calendar event fires once, in order, with the ledger and the league in step."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl import calendar as C  # noqa: E402
from dfl.year import Universe  # noqa: E402

TABLES = os.path.join(ROOT, "data", "season1")


class Year(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.u = Universe(TABLES, seed=5, spatial=False)
        for _ in range(3):
            cls.u.play_year()

    def test_every_event_fires_once_in_calendar_order(self):
        for y in (2000, 2001, 2002):
            fired = [(d, k) for yy, d, k in self.u.fired if yy == y]
            want = [(d.isoformat(), k) for d, k, *_ in C.year_events(y)]
            self.assertEqual(fired, want)
            self.assertEqual(len({k for _, k in fired}), len(fired))

    def test_each_season_ends_with_a_winner_a_bowl_and_a_draft_order(self):
        for r in self.u.reports:
            self.assertIn(r["winner"], self.u.clubs)
            self.assertEqual(len(r["result"]["draft_order"]), 48)
            self.assertNotEqual(r["bowl"]["away_score"], r["bowl"]["home_score"])
            self.assertEqual(len(r["result"]["tour_games"]), 28)

    def test_the_next_draft_follows_the_last_seasons_order(self):
        L = self.u.L
        last = self.u.reports[-1]["result"]["draft_order"]
        self.assertEqual(len(last), 48)
        self.assertEqual(self.u.draft_order, last)
        self.assertEqual(L.draft_completed, 2002)

    def test_ledger_is_clean_after_each_year(self):
        w = self.u.world
        self.assertEqual(w.stats["forfeits"], 0)
        self.assertEqual(w.stats["legal_move_failures"], 0)
        self.assertEqual(w.stats["unsigned_picks"], 0)
        self.assertEqual(w.L.year, 2002)
        self.assertEqual(w.L.phase, "post")
        self.assertEqual(w.L.week, 22)

    def test_ambassadors_play_the_tour_weeks_and_active_clubs_their_games(self):
        # the ledger played 22 paid weeks; games = 18 per active club + 7 per ambassador per year
        per_year = 40 * 18 + 8 * 7
        self.assertEqual(self.u.world.stats["games"], 3 * per_year)
        # and the ambassadors played in the Tour weeks (including the break week), not the weeks next to them
        by = self.u.world.stats["games_by_week"]
        rows = {}
        for r in self.u.tpl.sched:
            rows[int(r["week"])] = rows.get(int(r["week"]), 0) + 1
        for r in C.season_weeks(2000):
            w = int(r["calendar_week"])
            if w > 22:
                continue
            fixtures = rows.get(int(r["numbered_week"]), 0) if r["numbered_week"] else 0
            want = 3 * (2 * fixtures + (8 if w in C.TOUR_CALENDAR_WEEKS else 0))
            self.assertEqual(by.get(w, 0), want, w)


if __name__ == "__main__":
    unittest.main()
