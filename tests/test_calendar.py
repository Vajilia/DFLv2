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
            self.assertFalse({"venue_vote", "venue_announce"} & set(keys))      # Edition 8.55: the Coronation has one permanent venue, nothing is voted or announced
            at = {k: d for d, k, *_ in ev}
            order = ["offseason_open", "hiring_close", "trading_open", "draft_round_1", "draft_rounds_4_7", "camp_open",
                     "final_cuts", "waivers_clear", "week_1", "trade_close", "week_9",
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


class YearEnd(unittest.TestCase):
    """Item 14 (owner ruling): the Coronation is the last Sunday of the year, which is 25-31 December, so it can fall up to six days
    before the year closes. Only `year_close` (31 December: results, governance, honours) falls between the Coronation and 1 January;
    the league year turns on 1 January and not 'the next morning' unless the Coronation is itself on 31 December."""

    def test_both_a_31_december_coronation_and_an_earlier_last_sunday_occur(self):
        gaps = {(dt.date(y, 12, 31) - C.coronation(y)).days for y in range(2000, 2300)}
        self.assertEqual(gaps, {0, 1, 2, 3, 4, 5, 6})
        self.assertEqual(C.coronation(2000), dt.date(2000, 12, 31))
        self.assertEqual(C.coronation(2001), dt.date(2001, 12, 30))
        self.assertEqual(C.coronation(2002), dt.date(2002, 12, 29))
        self.assertEqual(C.coronation(2005), dt.date(2005, 12, 25))

    def test_between_the_coronation_and_the_new_year_only_the_year_close_falls(self):
        for y in range(2000, 2300):
            ev = C.year_events(y)
            c = C.coronation(y)
            after = [(d, k) for d, k, *_ in ev if d >= c]
            self.assertEqual([k for _, k in after], ["coronation", "year_close"], y)       # nothing else on or after the Coronation
            self.assertEqual(after[1][0], dt.date(y, 12, 31), y)
            self.assertEqual(ev[-1][1], "year_close")
            nxt = C.year_events(y + 1)
            self.assertEqual((nxt[0][0], nxt[0][1]), (dt.date(y + 1, 1, 1), "offseason_open"), y)    # the league year turns on 1 January
            self.assertEqual(nxt[1][1:2], ("hiring_open",))                                           # leadership window the same day, after the year turns
            self.assertEqual((nxt[1][0], ), (dt.date(y + 1, 1, 1),))

    def test_the_new_year_follows_the_coronation_the_next_morning_only_on_31_december(self):
        next_morning = [y for y in range(2000, 2300) if C.coronation(y) == dt.date(y, 12, 31)]
        later = [y for y in range(2000, 2300) if C.coronation(y) < dt.date(y, 12, 31)]
        self.assertIn(2000, next_morning)
        self.assertIn(2001, later)
        self.assertTrue(later and next_morning)


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
