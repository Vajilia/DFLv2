"""Step 5: the season controller and the calendar, checked against Design Document Ed 8.52 section 2 and section 7.

Calendar (pure functions, 400 consecutive years including leap years and every possible Coronation date): the 27-week year, the
Coronation as the last Sunday, the fixed offsets, breaks, byes, Tour rounds, offseason order and weekday rules.
Controller (`Universe.step_event`, three whole years, placeholder resolver so it is fast): each of the 49 events fires once on its calendar
date, the paid-week count and the trade window follow the calendar, every game is scheduled on its own calendar date in its own year,
every club's bye is in the week the calendar assigns to its seed, and a year's totals are the spec's."""
import csv
import datetime as dt
import os
import sys
import unittest
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl import calendar as C  # noqa: E402
from dfl import league  # noqa: E402
from dfl.year import Universe  # noqa: E402

TABLES = os.path.join(ROOT, "data", "season1")
D = dt.date.fromisoformat
BYE_WEEK_OF_SEED = {3: 15, 1: 16, 2: 17, 5: 18, 4: 19}           # the CALENDAR.csv notes: "bye group: seed N"


class CalendarShape(unittest.TestCase):
    def test_every_year_from_2000_to_2399_has_the_section_2_shape(self):
        for y in range(2000, 2400):
            wk = C.season_weeks(y)
            c = C.coronation(y)
            self.assertEqual((c.weekday(), c.year), (6, y))
            self.assertGreater((dt.date(y, 12, 31) - c).days, -1)
            self.assertLess((dt.date(y, 12, 31) - c).days, 7)                    # nothing later in the year is a Sunday
            self.assertEqual(len(wk), 27)
            self.assertEqual([int(r["calendar_week"]) for r in wk], list(range(1, 28)))
            suns = [D(r["sunday"]) for r in wk]
            self.assertTrue(all(s.weekday() == 6 for s in suns), y)
            self.assertTrue(all((b - a).days == 7 for a, b in zip(suns, suns[1:])), y)       # all rungs 7 days apart
            self.assertEqual(suns[-1], c)
            self.assertEqual(sum(1 for r in wk if r["phase"] in ("regular", "break")), 22)
            self.assertEqual([int(r["numbered_week"]) for r in wk if r["numbered_week"]], list(range(1, 20)))
            self.assertEqual([r["phase"] for r in wk[22:]], ["postseason", "postseason", "postseason", "ambassadors", "postseason"])
            rung = {r["note"]: D(r["sunday"]) for r in wk[22:]}
            self.assertEqual((c - rung["wild card round"]).days, 28)
            self.assertEqual((c - rung["divisional round"]).days, 21)
            self.assertEqual((c - rung["conference championships"]).days, 14)
            self.assertEqual((c - rung["Ambassador Bowl"]).days, 7)
            self.assertEqual((c - D(wk[21]["monday"])).days, 34)                     # the final regular-season Monday is C-34
            breaks = [int(r["calendar_week"]) for r in wk if r["phase"] == "break"]
            self.assertEqual(breaks, [5, 11, 17])                                    # after numbered weeks 4, 9 and 14
            self.assertEqual([wk[w - 2]["numbered_week"] for w in breaks], ["4", "9", "14"])
            self.assertEqual({int(r["numbered_week"]): r["note"] for r in wk if r["note"].startswith("bye group")},
                             {n: f"bye group: seed {s}" for s, n in BYE_WEEK_OF_SEED.items()})

    def test_the_tour_rounds_and_the_bowl_are_spaced_as_section_7_says(self):
        for y in range(2000, 2400):
            ev = {k: d for d, k, *_ in C.year_events(y)}
            rounds = [ev[f"tour_round_{i}"] for i in range(1, 8)]
            self.assertEqual([(b - a).days for a, b in zip(rounds, rounds[1:])], [21, 28, 21, 28, 21, 28])
            self.assertTrue(all(r.weekday() == 5 for r in rounds))                    # a round starts on its Saturday
            self.assertGreaterEqual((ev["ambassador_bowl"] - rounds[-1]).days - 2, 14)  # the last round ends at least 14 days before the Bowl
            wk = C.season_weeks(y)
            self.assertEqual([wk[w - 1]["phase"] for w in C.TOUR_CALENDAR_WEEKS], ["regular", "regular", "regular", "break", "regular", "regular", "regular"])

    def test_the_offseason_follows_section_2_in_every_year(self):
        for y in range(2000, 2400):
            ev = {k: d for d, k, *_ in C.year_events(y)}
            self.assertEqual(ev["offseason_open"], dt.date(y, 1, 1))
            self.assertEqual((ev["hiring_open"], ev["hiring_close"]), (dt.date(y, 1, 1), dt.date(y, 1, 31)))
            self.assertEqual((ev["trading_open"], ev["free_agency_open"]), (dt.date(y, 2, 1), dt.date(y, 2, 1)))
            d1 = ev["draft_round_1"]
            self.assertEqual((d1.month, d1.weekday()), (4, 3))                       # a Thursday in April
            self.assertEqual((d1 + dt.timedelta(days=7)).month, 5)                   # and the last one: the next is in May
            self.assertEqual((ev["draft_rounds_2_3"] - d1).days, 1)
            self.assertEqual((ev["draft_rounds_4_7"] - d1).days, 2)
            self.assertEqual((ev["camp_open"], ev["venue_vote"]), (dt.date(y, 6, 1), dt.date(y, 6, 15)))
            w1 = C.week1_saturday(y)
            self.assertEqual((w1 - ev["final_cuts"]).days, 4)                        # the Tuesday before week 1 (a Saturday)
            self.assertEqual((ev["final_cuts"].weekday(), ev["waivers_clear"].weekday()), (1, 2))
            self.assertEqual((ev["waivers_clear"] - ev["final_cuts"]).days, 1)
            self.assertLess(ev["venue_vote"], ev["final_cuts"])                       # the vote precedes the cut and the season
            self.assertLess(ev["waivers_clear"], w1)
            self.assertEqual(ev["year_close"], dt.date(y, 12, 31))
            self.assertEqual(ev["venue_announce"], dt.date(y, 7, 4))

    def test_there_are_49_events_a_year_and_none_share_a_name(self):
        for y in (2000, 2001, 2004, 2100, 2399):
            ev = C.year_events(y)
            self.assertEqual(len(ev), 49)
            self.assertEqual(len({k for _, k, *_ in ev}), 49)
            self.assertEqual([d for d, *_ in ev], sorted(d for d, *_ in ev))
            self.assertTrue(all(dt.date(y, 1, 1) <= d <= dt.date(y, 12, 31) for d, *_ in ev))

    def test_week_one_falls_between_24_june_and_1_july(self):
        firsts = {C.week1_saturday(y) for y in range(2000, 2400)}
        self.assertTrue(all(dt.date(d.year, 6, 24) <= d <= dt.date(d.year, 7, 1) for d in firsts))
        self.assertEqual(C.week1_saturday(2000), dt.date(2000, 7, 1))                 # the published Season 1 table


class SeasonTables(unittest.TestCase):
    """The tables a season is played from (league.season_tables) carry that season's own dates."""

    @classmethod
    def setUpClass(cls):
        cls.tpl = league.Template(TABLES)

    def test_season_one_is_the_published_schedule_unchanged(self):
        _, sched, tour = league.season_tables(self.tpl, self.tpl.initial_state(), 1)
        with open(os.path.join(TABLES, "SCHEDULE.csv"), encoding="utf-8", newline="") as f:
            published = list(csv.DictReader(f))
        self.assertEqual([(r["week"], r["game"], r["date"], r["kickoff"]) for r in sched], [(r["week"], r["game"], r["date"], r["kickoff"]) for r in published])

    def test_every_game_of_every_season_is_dated_in_its_own_calendar_week(self):
        state = self.tpl.initial_state()
        for season in (1, 2, 3, 5, 8, 9, 25):
            year = league.FOUNDING_YEAR + season - 1
            wk = C.season_weeks(year)
            by_numbered = {int(r["numbered_week"]): r for r in wk if r["numbered_week"]}
            by_cal = {int(r["calendar_week"]): r for r in wk}
            _, sched, tour = league.season_tables(self.tpl, state, season)
            for r in sched:
                w = by_numbered[int(r["week"])]
                self.assertIn(r["date"], (w["saturday"], w["sunday"], w["monday"]), (season, r))
                self.assertTrue(r["kickoff"])
            for r in tour:
                w = by_cal[int(r["calendar_week"])]
                self.assertIn(r["date"], (w["saturday"], w["sunday"], w["monday"]), (season, r))
                self.assertEqual(D(r["date"]).year, year)
            self.assertEqual(D(sched[0]["date"]).year, year)

    def test_dates_move_but_everything_else_in_the_schedule_is_the_same_for_the_same_division_state(self):
        state = self.tpl.initial_state()
        _, a, _ = league.season_tables(self.tpl, state, 9)               # season 9 repeats season 1's bed pattern (eight-season cycle)
        _, b, _ = league.season_tables(self.tpl, state, 1)
        key = lambda r: (r["week"], r["game"], r["away"], r["home"], r["kickoff"])
        self.assertEqual([key(r) for r in a], [key(r) for r in b])
        self.assertNotEqual({r["date"] for r in a}, {r["date"] for r in b})


class Controller(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.u = Universe(TABLES, seed=3, engine=False, spatial=False)
        cls.trace = []                                                 # (year, key, paid_week, trade_open, trades so far) after each event
        cls.years = 3
        while len(cls.u.reports) < cls.years:
            key = cls.u.step_event()
            cls.trace.append((cls.u.fired[-1][0], key, cls.u.paid_week, cls.u.L.trade_open(), cls.u.world.stats["trades"]))

    def test_each_year_fires_its_49_events_once_on_their_dates(self):
        for y in (2000, 2001, 2002):
            fired = [(d, k) for yy, d, k in self.u.fired if yy == y]
            self.assertEqual(fired, [(d.isoformat(), k) for d, k, *_ in C.year_events(y)])
            self.assertEqual(len(fired), 49)
        self.assertEqual(len(self.u.fired), 3 * 49)
        self.assertIsNone(self.u.cursor)                               # the last year closed

    def test_results_and_honours_close_on_31_december_whatever_day_the_coronation_fell(self):
        """Item 14: 2000's Coronation is on 31 December itself, 2001's is a day earlier and 2002's two days earlier. In each year only
        the year close follows the Coronation, it runs on 31 December of that year, and the year's Tiaras carry that year."""
        import datetime as dt
        gaps = []
        for y in (2000, 2001, 2002):
            fired = [(d, k) for yy, d, k in self.u.fired if yy == y]
            self.assertEqual([k for _, k in fired[-2:]], ["coronation", "year_close"])
            self.assertEqual(fired[-1][0], dt.date(y, 12, 31).isoformat())
            gaps.append((dt.date(y, 12, 31) - dt.date.fromisoformat(fired[-2][0])).days)
        self.assertEqual(gaps, [0, 1, 2])
        given = set().union(*self.u.world.tiaras.values())
        self.assertEqual(given, {2000, 2001, 2002})                    # each year's Tiaras were awarded at that year's close, none late or twice
        self.assertEqual(self.u.L.year, 2002)                         # the league year has not turned: it turns on 1 January
        self.assertEqual([r["year"] for r in self.u.reports], [2000, 2001, 2002])

    def test_the_paid_week_counter_follows_the_calendar(self):
        for y in (2000, 2001, 2002):
            rows = [t for t in self.trace if t[0] == y]
            paid = 0
            for _, key, got, _, _ in rows:
                if key.startswith(("week_", "break_")):
                    paid += 1
                self.assertEqual(got, paid, (y, key))
            self.assertEqual(paid, 22)

    def test_trades_are_made_only_from_1_february_to_the_friday_before_numbered_week_9(self):
        """The ledger itself keeps the window (owner ruling, Item 14): closed in January, open from `trading_open` (1 February) through
        the Friday before numbered week 9, closed again from `trade_close` to the end of the year. Trading rounds run at `trading_open`
        and in paid weeks 1 to 9 and nowhere else."""
        for y in (2000, 2001, 2002):
            rows = [t for t in self.trace if t[0] == y]
            keys = [t[1] for t in rows]
            i_open, i_close, i_wc = keys.index("trading_open"), keys.index("trade_close"), keys.index("wild_card")
            for t in rows[:i_open]:
                self.assertFalse(t[3], (y, t[1]))                          # January to 31 January (and every closed day): refused by the ledger
            for t in rows[i_open:i_close]:
                self.assertTrue(t[3], (y, t[1]))
            for t in rows[i_close:]:
                self.assertFalse(t[3], (y, t[1]))                          # from the close, through the end of the season, the postseason and December
            self.assertLess(i_close, keys.index("week_9"))
            before = self.trace[self.trace.index(rows[0]) - 1][4] if rows[0] is not self.trace[0] else 0
            for t in rows:
                if t[4] > before:
                    in_window = t[1] == "trading_open" or (t[1].startswith(("week_", "break_")) and t[2] <= 9)
                    self.assertTrue(in_window, (y, t[1], t[2]))
                before = t[4]
        self.assertGreater(self.u.world.stats["trades"], 0)

    def test_a_year_plays_the_spec_number_of_games_in_the_spec_places(self):
        for r in self.u.reports:
            res = r["result"]
            self.assertEqual(len(res["games"]), 360)                       # 40 active clubs x 18 games / 2
            self.assertEqual(len(res["tour_games"]), 28)
            self.assertEqual(len(res["playoff_log"]), 12)                  # 3 + 2 + 1 games a conference
            self.assertEqual(r["bowl"]["neutral"], True)
            self.assertEqual(r["events"], 49)

    def test_every_club_plays_18_games_and_has_its_bye_in_the_week_for_its_seed(self):
        for r in self.u.reports:
            res = r["result"]
            seed = {t["team_city"]: int(t["division_rank"]) for t in res["teams"] if t["club_status"] == "active"}
            weeks = {}
            for g in res["schedule"]:
                for c in (g["away"], g["home"]):
                    weeks.setdefault(c, []).append(int(g["week"]))
            self.assertEqual(set(weeks), set(seed))
            for c, w in weeks.items():
                self.assertEqual(len(w), 18, c)
                self.assertEqual(len(set(w)), 18, c)                       # never twice in one week
                self.assertEqual(sorted(set(range(1, 20)) - set(w)), [BYE_WEEK_OF_SEED[seed[c]]], (r["year"], c))

    def test_ambassadors_play_only_the_tour_seven_games_in_seven_rounds(self):
        for r in self.u.reports:
            res = r["result"]
            amb = {t["team_city"] for t in res["teams"] if t["club_status"] == "ambassador"}
            self.assertEqual(len(amb), 8)
            played = Counter()
            rounds = {}
            for g in res["tour_games"]:
                for c in (g["away"], g["home"]):
                    played[c] += 1
                    rounds.setdefault(c, []).append(int(g["tour_round"]))
            self.assertEqual(set(played), amb)
            self.assertEqual(set(played.values()), {7})
            self.assertTrue(all(sorted(v) == list(range(1, 8)) for v in rounds.values()))
            self.assertFalse(any(g["away"] in amb or g["home"] in amb for g in res["schedule"]))

    def test_every_game_the_controller_played_carries_the_date_of_its_own_year(self):
        for r in self.u.reports:
            y = r["year"]
            wk = C.season_weeks(y)
            by_numbered = {int(w["numbered_week"]): w for w in wk if w["numbered_week"]}
            by_cal = {int(w["calendar_week"]): w for w in wk}
            for g in r["result"]["games"]:
                w = by_numbered[int(g["week"])]
                self.assertIn(g["date"], (w["saturday"], w["sunday"], w["monday"]), (y, g["week"], g["game"]))
            for g in r["result"]["tour_games"]:
                w = by_cal[int(g["calendar_week"])]
                self.assertIn(g["date"], (w["saturday"], w["sunday"], w["monday"]), (y, g["tour_round"], g["game"]))

    def test_the_postseason_and_the_coronation_fall_on_their_sundays(self):
        for y in (2000, 2001, 2002):
            at = {k: D(d) for yy, d, k in self.u.fired if yy == y}
            c = C.coronation(y)
            self.assertEqual(at["coronation"], c)
            self.assertEqual([(c - at[k]).days for k in ("wild_card", "divisional", "conference_championships", "ambassador_bowl")],
                             [28, 21, 14, 7])

    def test_the_next_year_opens_on_the_next_1_january_with_the_next_seasons_calendar(self):
        self.assertEqual([(t[0], t[1]) for t in self.trace if t[1] == "offseason_open"], [(2000, "offseason_open"), (2001, "offseason_open"), (2002, "offseason_open")])
        self.assertEqual([r["year"] for r in self.u.reports], [2000, 2001, 2002])
        self.assertEqual([r["season"] for r in self.u.reports], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
