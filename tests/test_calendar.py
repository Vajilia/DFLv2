import csv
import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl import calendar as C  # noqa: E402


class Calendar(unittest.TestCase):
    def test_season_one_reproduces_the_published_table(self):
        with open(C.SEASON1_CALENDAR, encoding="utf-8", newline="") as f:
            want = list(csv.DictReader(f))
        self.assertEqual(C.season_weeks(2000), want)

    def test_coronation_is_the_last_sunday_of_the_year(self):
        for y in range(2000, 2200):
            d = C.coronation(y)
            self.assertEqual(d.weekday(), 6)
            self.assertEqual(d.year, y)
            self.assertGreaterEqual(d.day, 25)

    def test_every_year_has_the_same_events_in_a_sane_order(self):
        keys0 = None
        for y in range(2000, 2300):
            ev = C.year_events(y)
            dates = [d for d, *_ in ev]
            self.assertEqual(dates, sorted(dates))
            self.assertTrue(all(d.year == y for d in dates), y)
            keys = [k for _, k, *_ in ev]
            self.assertEqual(len(keys), len(set(keys)), y)
            keys0 = keys0 or set(keys)
            self.assertEqual(keys0, set(keys))      # the same events every year (their order may shift with the date)
            at = {k: d for d, k, *_ in ev}
            order = ["offseason_open", "hiring_close", "trading_open", "draft_round_1", "draft_rounds_4_7", "camp_open",
                     "venue_vote", "final_cuts", "waivers_clear", "week_1", "venue_announce", "trade_close", "week_9",
                     "week_19", "wild_card", "divisional", "conference_championships", "ambassador_bowl", "coronation", "year_close"]
            for a, b in zip(order, order[1:]):
                self.assertLessEqual(at[a], at[b], (y, a, b))
            self.assertEqual(at["final_cuts"].weekday(), 1)
            self.assertEqual(at["trade_close"].weekday(), 4)
            self.assertEqual(at["draft_round_1"].weekday(), 3)
            self.assertEqual(at["coronation"], C.coronation(y))
            self.assertEqual((at["coronation"] - at["ambassador_bowl"]).days, 7)

    def test_tour_rounds_fall_on_the_seven_published_calendar_weeks(self):
        ev = {k: d for d, k, *_ in C.year_events(2000)}
        weeks = {r["calendar_week"]: r["saturday"] for r in C.season_weeks(2000)}
        for i, w in enumerate(C.TOUR_CALENDAR_WEEKS, 1):
            self.assertEqual(ev[f"tour_round_{i}"].isoformat(), weeks[str(w)])


if __name__ == "__main__":
    unittest.main()


class TradeWindow(unittest.TestCase):
    def test_ledger_trade_window_matches_the_calendar(self):
        from dfl.ledger import rules as R
        weeks = C.season_weeks(2000)
        paid = [r for r in weeks if r["phase"] in ("regular", "break")]            # the 22 paid weeks, in order
        self.assertEqual(len(paid), R.PAID_WEEKS)
        # trades stay open through the Friday before numbered week 9; that week is paid week TRADE_LAST_PAID_WEEK + 1
        first_closed = paid[R.TRADE_LAST_PAID_WEEK]
        self.assertEqual(first_closed["numbered_week"], "9")
        ev = {k: d for d, k, *_ in C.year_events(2000)}
        self.assertEqual(ev["trade_close"].isoformat(), "2000-09-01")
