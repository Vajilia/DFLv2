#!/usr/bin/env python3
"""Step 6: scheduling and rotation verified on their own, apart from tools/validate.py (which checks one season's tables).

Everything here is derived from the written rules (spec section 3 beds and rotation, section 6 schedule, section 7 Tour) and from the
Season 1 template, not from the code under test. The seasons are generated with the real season loop and its state hand-off.
"""
import collections
import datetime as dt
import os
import random
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl import calendar as C, league  # noqa: E402
from dfl.resolver import PlaceholderResolver  # noqa: E402

TABLES = os.environ.get("DFL_TABLES", os.path.join(ROOT, "data", "season1"))
WEST, EAST = league.WEST, league.EAST
SEASONS = 48                                    # six full eight-season cycles, three conference flips each way


def generate(n, seed=21):
    tpl = league.Template(TABLES)
    state, rng, res, hist = tpl.initial_state(), random.Random(seed), PlaceholderResolver(), {}
    seasons = []
    for s in range(1, n + 1):
        before = state
        r, state = league.play_season(tpl, state, s, res, rng, hist, seed=f"rot{seed}")
        seasons.append((s, before, r))
    return tpl, seasons


def bed(conf, div, n):
    """Written out from spec section 3, independently of schedule_bed_rotation."""
    order = WEST if conf == "Western" else EAST
    i, k, p = order.index(div), (n - 1) % 4, ((n - 1) // 4) % 2
    return 1 + 4 * (p if conf == "Western" else 1 - p) + (i + k) % 4


class Rotation(unittest.TestCase):
    def test_every_division_moves_one_bed_on_inside_its_block_each_season(self):
        for conf, div in league.DIVISIONS:
            for n in range(1, 65):
                a, b = league.bed_of(conf, div, n), league.bed_of(conf, div, n + 1)
                self.assertEqual(league.bed_of(conf, div, n), bed(conf, div, n))
                if n % 4:                                                # inside a block of four seasons
                    self.assertEqual((a - 1) // 4, (b - 1) // 4)         # same block
                    self.assertEqual(b, a + 1 if a % 4 else a - 3)       # one bed on; bed 4 to bed 1 and bed 8 to bed 5
                else:                                                    # the conferences flip every fourth season
                    self.assertNotEqual((a - 1) // 4, (b - 1) // 4)

    def test_wraparound_goes_from_bed_4_to_bed_1_and_8_to_5(self):
        for conf, div in league.DIVISIONS:
            for n in range(1, 41):
                a, b = league.bed_of(conf, div, n), league.bed_of(conf, div, n + 1)
                if a in (4, 8) and n % 4:
                    self.assertEqual(b, a - 3)

    def test_in_eight_seasons_each_division_fills_every_bed_exactly_once(self):
        for conf, div in league.DIVISIONS:
            for start in range(1, 20):
                seen = [league.bed_of(conf, div, n) for n in range(start, start + 8)]
                self.assertEqual(sorted(seen), list(range(1, 9)), (conf, div, start))

    def test_founding_season_matches_the_table_in_section_3(self):
        want = {("Western", d): i + 1 for i, d in enumerate(WEST)} | {("Eastern", d): i + 5 for i, d in enumerate(EAST)}
        self.assertEqual({dv: league.bed_of(*dv, 1) for dv in league.DIVISIONS}, want)
        for n in (5, 6, 7, 8):                                           # after four seasons the conferences have flipped
            self.assertEqual(league.bed_of("Western", "Northwest", n), n)
            self.assertEqual(league.bed_of("Eastern", "Midwest", n), n - 4)


class EverySeason(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tpl, cls.seasons = generate(SEASONS)
        cls.division = cls.tpl.division_of
        cls.s1 = [(g[2]["week"], g[2]["game"], g[2]["kickoff"], g[0], g[1]) for g in cls.tpl.games]     # (week, game, kickoff, away pos, home pos)

    def test_each_season_is_the_season_1_game_list_filled_by_whoever_holds_each_bed_and_seed(self):
        for s, state, r in self.seasons:
            pos = {}
            for dv in league.DIVISIONS:
                for seed, club in state[dv]["seeds"].items():
                    pos[club] = (league.bed_of(*dv, s), seed)
            self.assertEqual(len(r["schedule"]), 360)
            got = [(g["week"], g["game"], g["kickoff"], pos[g["away"]], pos[g["home"]]) for g in r["schedule"]]
            self.assertEqual(got, self.s1, s)

    def test_state_handoff_seeds_come_from_the_final_division_order_and_the_ambassador_returns_fifth(self):
        rs = self.seasons
        for (s, before, r), (_, after, _) in zip(rs, rs[1:]):
            for dv in league.DIVISIONS:
                order = r["outcome"]["division_orders"][dv]
                self.assertEqual([after[dv]["seeds"][i] for i in (1, 2, 3, 4)], order[:4])
                self.assertEqual(after[dv]["seeds"][5], before[dv]["amb"])
                self.assertEqual(after[dv]["amb"], order[4])
                self.assertEqual(sorted(order), sorted(before[dv]["seeds"].values()))        # the five active clubs of that division
            everyone = {c for dv in after.values() for c in list(dv["seeds"].values()) + [dv["amb"]]}
            self.assertEqual(len(everyone), 48)

    def test_each_club_plays_18_games_9_home_9_away_against_the_right_opponents(self):
        for s, state, r in self.seasons:
            active = {c for dv in state.values() for c in dv["seeds"].values()}
            games = collections.defaultdict(list)
            for g in r["schedule"]:
                self.assertNotEqual(g["away"], g["home"])
                games[g["away"]].append(("A", g["home"]))
                games[g["home"]].append(("H", g["away"]))
            self.assertEqual(set(games), active, s)                     # the eight ambassadors play no regular-season game
            for c, gs in games.items():
                dv = self.division[c]
                self.assertEqual(len(gs), 18, (s, c))
                self.assertEqual(sum(1 for h, _ in gs if h == "H"), 9, (s, c))
                opp = collections.Counter(o for _, o in gs)
                rivals = {o for o in active if self.division[o] == dv and o != c}
                self.assertEqual({o for o in opp if o in rivals}, rivals, (s, c))                     # all four rivals ...
                self.assertTrue(all(opp[o] == 2 for o in rivals), (s, c))                              # ... twice
                self.assertEqual({h for h, o in gs if o in rivals}, {"H", "A"})
                for o in rivals:
                    self.assertEqual(sorted(h for h, x in gs if x == o), ["A", "H"], (s, c, o))        # once at home, once away
                others = [o for o in opp if o not in rivals]
                self.assertTrue(all(opp[o] == 1 for o in others), (s, c))                             # no other repeat
                inter = [o for o in others if self.division[o][0] != dv[0]]
                same_conf = [o for o in others if self.division[o][0] == dv[0]]
                self.assertEqual((len(inter), len(same_conf)), (4, 6), (s, c))
                self.assertEqual(len({self.division[o] for o in inter}), 4, (s, c))                   # one from each opposing division
                self.assertEqual(len({self.division[o] for o in same_conf}), 3, (s, c))              # two from each other division of her own

    def test_weeks_byes_dates_and_kickoff_points(self):
        pts = {"Mon": 5, "Sat": 4, "Sun17": 3, "Sun13": 2, "Sun10": 1}
        for s, state, r in self.seasons:
            year = r["year"]
            cal = {int(w["numbered_week"]): w for w in C.season_weeks(year) if w["phase"] == "regular"}
            by_week = collections.defaultdict(list)
            for g in r["schedule"]:
                by_week[int(g["week"])].append(g)
            self.assertEqual(sorted(by_week), list(range(1, 20)))
            for w, gs in by_week.items():
                self.assertEqual(len(gs), 20 if w <= 14 else 16, (s, w))
                clubs = [x for g in gs for x in (g["away"], g["home"])]
                self.assertEqual(len(clubs), len(set(clubs)), (s, w))                  # nobody twice in a week
                sat, sun, mon = (dt.date.fromisoformat(cal[w][k]) for k in ("saturday", "sunday", "monday"))
                ordered = sorted(gs, key=lambda g: int(g["game"]))
                self.assertEqual(dt.date.fromisoformat(ordered[0]["date"]), sat)
                self.assertEqual((ordered[0]["kickoff"], dt.date.fromisoformat(ordered[-1]["date"]), ordered[-1]["kickoff"]), ("17:30", mon, "17:30"))
                for g in ordered[1:-1]:
                    self.assertEqual(dt.date.fromisoformat(g["date"]), sun)
            active = {c for dv in state.values() for c in dv["seeds"].values()}
            self.assertTrue(all(len({x for g in by_week[w] for x in (g["away"], g["home"])}) == 40 for w in range(1, 15)))
            byes = collections.defaultdict(list)
            for w in range(15, 20):
                playing = {x for g in by_week[w] for x in (g["away"], g["home"])}
                for c in active - playing:
                    byes[c].append(w)
            self.assertTrue(all(len(byes[c]) == 1 for c in active), s)                     # exactly one bye each, in weeks 15-19
            self.assertEqual(sorted(collections.Counter(w for c in byes for w in byes[c]).values()), [8] * 5)   # eight clubs a bye week
            seed_of = {c: sd for dv in state.values() for sd, c in dv["seeds"].items()}
            for w in range(15, 20):
                self.assertEqual(len({seed_of[c] for c in byes if byes[c] == [w]}), 1, (s, w))   # one seed group a week (section 6)
            slot = collections.Counter()
            for g in r["schedule"]:
                d = dt.date.fromisoformat(g["date"])
                p = 5 if d.weekday() == 0 else 4 if d.weekday() == 5 else {"17:30": 3, "13:00": 2, "10:00": 1}[g["kickoff"]]
                slot[g["away"]] += p
                slot[g["home"]] += p
            self.assertTrue(all(33 <= v <= 38 for v in slot.values()), (s, sorted(slot.values())))

    def test_teams_table_has_forty_active_and_eight_ambassadors_with_unique_beds(self):
        for s, state, r in self.seasons:
            act = [t for t in r["teams"] if t["is_active"] == "true"]
            self.assertEqual((len(act), len(r["teams"])), (40, 48))
            beds = [t["bed_id"] for t in act]
            self.assertEqual(len(set(beds)), 40)
            for t in act:
                b, _, sd = t["bed_id"].partition("-")
                self.assertEqual(int(b), bed(t["conference"], t["division"], s), (s, t["team_city"]))
                self.assertEqual(sd, t["division_rank"])
            self.assertTrue(all(t["bed_id"] == "" and t["division_rank"] == "6" for t in r["teams"] if t["is_active"] != "true"))
            self.assertEqual(sum(1 for t in r["teams"] if t["club_status"] == "ambassador"), 8)

    def test_the_tour_is_a_round_robin_with_balanced_nominal_homes_every_season(self):
        for s, state, r in self.seasons:
            amb = {dv_state["amb"] for dv_state in state.values()}
            self.assertEqual(len(amb), 8)
            games = r["tour"]
            self.assertEqual(len(games), 28)
            pairs = collections.Counter(frozenset((g["away_nominal"], g["home_nominal"])) for g in games)
            self.assertEqual(len(pairs), 28)
            self.assertEqual({c for p in pairs for c in p}, amb)
            rounds = collections.defaultdict(list)
            for g in games:
                rounds[int(g["tour_round"])].append(g)
            self.assertEqual(sorted(rounds), list(range(1, 8)))
            home = collections.defaultdict(list)
            for i in range(1, 8):
                for g in rounds[i]:
                    home[g["home_nominal"]].append(i)
            for c in amb:
                self.assertIn(len(home[c]), (3, 4), (s, c))
                for a in range(1, 6):
                    self.assertFalse(all(x in home[c] for x in (a, a + 1, a + 2)), (s, c))        # never nominal home in three consecutive rounds


class RotationChangesWhenAndWhereNotWhom(unittest.TestCase):
    """What the bed rotation does (a finding recorded in PROJECT_COMPLETION_PLAN.md): with every club's seed held fixed, the
    18 opponents never change, so rotation moves the order of the games, the kickoff slots and the home/away pattern. Opponents change
    only because seeds change, season to season, with the final standings (equal-seed interconference games, seed-based conference games)."""

    @classmethod
    def setUpClass(cls):
        cls.tpl = league.Template(TABLES)
        cls.state = cls.tpl.initial_state()
        cls.cycle = [league.season_tables(cls.tpl, cls.state, n)[1] for n in range(1, 17)]

    def test_with_seeds_fixed_the_opponents_never_change(self):
        first = None
        for sched in self.cycle:
            opp = collections.defaultdict(collections.Counter)
            for g in sched:
                opp[g["away"]][g["home"]] += 1
                opp[g["home"]][g["away"]] += 1
            first = first or opp
            self.assertEqual(opp, first)

    def test_with_seeds_fixed_home_and_away_balance_exactly_over_each_eight_season_cycle(self):
        for lo in (0, 8):
            home = collections.Counter()
            for sched in self.cycle[lo:lo + 8]:
                for g in sched:
                    home[(g["home"], g["away"])] += 1
            div = self.tpl.division_of
            for (h, a), n in home.items():
                self.assertEqual(n, home[(a, h)], (lo, h, a))                    # same number of home and away games against each opponent
                self.assertEqual(n, 8 if div[h] == div[a] else 4)

    def test_with_seeds_fixed_the_game_order_and_the_home_away_pattern_do_change(self):
        club = "Montana"
        pattern = []
        for sched in self.cycle[:8]:
            mine = sorted((g for g in sched if club in (g["away"], g["home"])), key=lambda g: int(g["week"]))
            pattern.append(tuple((g["home"] == club, g["home"] if g["away"] == club else g["away"]) for g in mine))
        self.assertEqual(len(set(pattern)), 8)                                   # eight different years of fixtures


if __name__ == "__main__":
    unittest.main()
