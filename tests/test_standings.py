#!/usr/bin/env python3
"""Unit and property tests for dfl.standings.   Run: python3 -m unittest discover -s tests"""
import csv
import os
import random
import sys
import unittest
from fractions import Fraction

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl import standings as S  # noqa: E402

TABLES = os.environ.get("DFL_TABLES", os.path.join(ROOT, "data", "season1"))


def G(a, b, sa, sb):
    return {"away": a, "home": b, "away_score": sa, "home_score": sb}


def teams5(conf="W", div="N", prefix=""):
    return {f"{prefix}{x}": (conf, div) for x in "ABCDE"}


class Records(unittest.TestCase):
    def test_tie_is_not_played_for_standings_but_played_for_stats(self):
        t = {"A": ("W", "N"), "B": ("W", "N"), "C": ("W", "S")}
        r = S.tally(t, [G("A", "B", 17, 17), G("A", "C", 20, 10)])
        a, b = r["A"], r["B"]
        self.assertEqual((a.w, a.l, a.t, a.gp), (1, 0, 1, 2))
        self.assertEqual((b.w, b.l, b.t, b.gp), (0, 0, 1, 1))
        self.assertEqual(a.opps, ["C"])            # B is not an opponent played
        self.assertEqual((a.dw, a.dl), (0, 0))      # the tied division game adds no division record
        self.assertEqual((a.pf, a.pa, a.pd), (37, 27, 10))   # but its points count as statistics
        self.assertEqual((b.pf, b.pa), (17, 17))

    def test_win_pct_ignores_ties(self):
        self.assertEqual(S.pct(9, 5), Fraction(9, 14))
        self.assertEqual(S.pct(0, 0), 0)

    def test_wins_rank_before_win_pct(self):
        # owner-confirmed quirk: 10-8 outranks 9-5-4
        t = {"X": ("W", "N"), "Y": ("W", "N")}
        rec = {"X": S.Rec(), "Y": S.Rec()}
        rec["X"].w, rec["X"].l = 10, 8
        rec["Y"].w, rec["Y"].l, rec["Y"].t = 9, 5, 4
        self.assertEqual(S.rank(["Y", "X"], rec, t), ["X", "Y"])


class SeasonDrawPriority(unittest.TestCase):
    """Item 9 (owner ruling): one stable seeded priority per club per season is the final tiebreak, in every scope."""

    def identical(self):
        t = {f"{c}{cf}{d}": (cf, d) for cf in "WE" for d in "NSXY" for c in "ABCDE"}
        return t, {c: S.Rec() for c in t}                 # every club identical: only the draw can separate them

    def test_priority_depends_on_season_and_club_only(self):
        self.assertEqual(S.priority("s", "A"), S.priority("s", "A"))
        self.assertNotEqual(S.priority("s", "A"), S.priority("s", "B"))
        self.assertNotEqual(S.priority("s", "A"), S.priority("t", "A"))

    def test_scope_cannot_reverse_an_identical_tie(self):
        t, rec = self.identical()
        clubs = sorted(t)
        whole = S.rank(clubs, rec, t, "S1")
        pos = {c: i for i, c in enumerate(whole)}
        rng = random.Random(3)
        for _ in range(200):                                # any subset of clubs, in any order, keeps the whole-league order
            sub = rng.sample(clubs, rng.randint(2, 12))
            self.assertEqual(S.rank(sub, rec, t, "S1"), sorted(sub, key=pos.get))

    def test_division_conference_and_wild_card_scopes_agree(self):
        t, rec = self.identical()
        whole = S.rank(sorted(t), rec, t, "S1")
        pos = {c: i for i, c in enumerate(whole)}
        orders = S.division_orders(t, rec, "S1")
        for dv, o in orders.items():
            self.assertEqual(o, sorted(o, key=pos.get))
        for conf in "WE":
            seeds = S.conference_seeds(conf, t, rec, orders, "S1")
            winners = seeds[:4]
            self.assertEqual(winners, sorted(winners, key=pos.get))
            wild = seeds[4:]
            self.assertEqual(wild, sorted(wild, key=pos.get))

    def test_new_season_new_priority(self):
        t, rec = self.identical()
        orders = {S.rank(sorted(t), rec, t, f"S{i}")[0] for i in range(60)}
        self.assertGreater(len(orders), 15)

    def test_standings_best_first_and_draft_is_the_exact_reverse(self):
        from dfl import draft
        t, rec = self.identical()
        for r in rec.values():
            r.w, r.l = 9, 9                                # the rest tied on everything; one clear leader, one clear trailer
        rec["AWN"].w, rec["AWN"].l = 12, 6
        rec["AWS"].w, rec["AWS"].l = 3, 15
        ranked = S.rank(sorted(t), rec, t, "S1")
        self.assertEqual(ranked[0], "AWN")
        self.assertEqual(ranked[-1], "AWS")                 # best first
        order = draft.base_order(ranked, {}, [], "S1")
        self.assertEqual(order, list(reversed(ranked)))    # worst first, and tied clubs in exactly the opposite order
        self.assertEqual(order[0], "AWS")


class Cascade(unittest.TestCase):
    def mk(self, rows):
        """rows: {club: dict(w,l,dw,dl,cw,cl,pf,pa,opps,beaten)}"""
        rec = {}
        for c, d in rows.items():
            r = S.Rec()
            for k, v in d.items():
                setattr(r, k, v)
            rec[c] = r
        return rec

    def test_division_steps_apply_when_all_tied_share_a_division(self):
        t = {"A": ("W", "N"), "B": ("W", "N")}
        rec = self.mk({"A": dict(w=10, l=8, dw=3, dl=5), "B": dict(w=10, l=8, dw=5, dl=3)})
        self.assertEqual(S.rank(["A", "B"], rec, t), ["B", "A"])

    def test_division_steps_skipped_when_group_spans_divisions(self):
        # A and B share a division and B has the better division record, but C is in another division,
        # so for the three-way group the whole cascade skips steps 3-4 and goes to conference record.
        t = {"A": ("W", "N"), "B": ("W", "N"), "C": ("W", "S")}
        rec = self.mk({
            "A": dict(w=10, l=8, dw=2, dl=4, cw=8, cl=4),
            "B": dict(w=10, l=8, dw=4, dl=2, cw=6, cl=6),
            "C": dict(w=10, l=8, dw=0, dl=0, cw=5, cl=7),
        })
        self.assertEqual(S.rank(["A", "B", "C"], rec, t), ["A", "B", "C"])

    def test_conference_steps_skipped_across_conferences(self):
        t = {"A": ("W", "N"), "B": ("E", "N")}
        rec = self.mk({"A": dict(w=10, l=8, cw=9, cl=3, pf=300, pa=290), "B": dict(w=10, l=8, cw=3, cl=9, pf=320, pa=290)})
        self.assertEqual(S.rank(["A", "B"], rec, t), ["B", "A"])   # conference steps skipped: point differential decides

    def test_cascade_order_is_point_differential_then_sos_then_sov_then_draw(self):
        # owner ruling (Edition 8.55): the league-wide cascade is wins, win %, division, conference, POINT DIFFERENTIAL, strength of
        # schedule, strength of victory, seeded draw. Edition 8.54 ranked strength of schedule before point differential.
        T = {"A": ("W", "N"), "B": ("E", "N"), "S": ("E", "N"), "K": ("E", "N")}
        base = dict(w=10, l=8)
        rec = self.mk({
            "A": dict(base, opps=["S"], beaten=[], pf=300, pa=300),
            "B": dict(base, opps=["K"], beaten=[], pf=400, pa=300),
            "S": dict(w=12, l=6), "K": dict(w=6, l=12)})
        self.assertEqual(S.rank(["A", "B"], rec, T), ["B", "A"])        # B has the worse schedule but the better differential
        rec["A"].pf = rec["B"].pf = 300
        self.assertEqual(S.rank(["A", "B"], rec, T), ["A", "B"])        # equal differential: A's opponents were stronger
        rec["A"].opps = rec["B"].opps = ["S"]
        rec["A"].beaten, rec["B"].beaten = ["K"], ["S"]
        self.assertEqual(S.rank(["A", "B"], rec, T), ["B", "A"])        # equal schedule: B beat the stronger opponent
        rec["A"].beaten = ["S"]
        draws = {tuple(S.rank(["A", "B"], rec, T, f"s{i}")) for i in range(60)}
        self.assertEqual(draws, {("A", "B"), ("B", "A")})               # everything equal: only the seeded draw is left

    def test_stages_after_differential_are_in_the_documented_order(self):
        self.assertEqual(S.CASCADE[-4:], ("point_differential", "strength_of_schedule", "strength_of_victory", "seeded_draw"))

    def test_seeded_draw_is_deterministic_and_fair_ish(self):
        t = {c: ("W", "N") for c in "ABCDE"}
        rec = {c: S.Rec() for c in t}
        a = S.rank(list(t), rec, t, "s1")
        self.assertEqual(a, S.rank(list(reversed(list(t))), rec, t, "s1"))
        firsts = {S.rank(list(t), rec, t, f"s{i}")[0] for i in range(200)}
        self.assertEqual(firsts, set(t))

    def test_input_order_does_not_matter(self):
        rng = random.Random(5)
        t = teams5()
        for _ in range(50):
            games = [G(a, b, rng.randint(0, 30), rng.randint(0, 30)) for a in t for b in t if a != b]
            rec = S.tally(t, games)
            base = S.rank(list(t), rec, t, "x")
            for _ in range(5):
                c = list(t)
                rng.shuffle(c)
                self.assertEqual(base, S.rank(c, rec, t, "x"))


def load_schedule():
    with open(os.path.join(TABLES, "SCHEDULE.csv"), encoding="utf-8", newline="") as f:
        sched = list(csv.DictReader(f))
    with open(os.path.join(TABLES, "TEAMS.csv"), encoding="utf-8", newline="") as f:
        teams = {r["team_city"]: (r["conference"], r["division"]) for r in csv.DictReader(f) if r["club_status"] == "active"}
    return teams, sched


class Playoffs(unittest.TestCase):
    def test_division_winner_seeds_above_better_wild_card(self):
        # Division N's best club is 8-10; three other divisions each hold a 12-6 runner-up. The weak winner is
        # still seed 4 and two of the three 12-6 runners-up are the wild cards 5 and 6.
        t, rec = {}, {}

        def add(c, dv, w):
            t[c] = ("W", dv)
            rec[c] = S.Rec()
            rec[c].w, rec[c].l = w, 18 - w
        for i, w in enumerate([8, 4, 4, 3, 3], 1):
            add(f"N{i}", "N", w)
        for dv in "SEC":
            for i, w in enumerate([13, 12, 11, 4, 3], 1):
                add(f"{dv}{i}", dv, w)
        orders = S.division_orders(t, rec, "z")
        seeds = S.conference_seeds("W", t, rec, orders, "z")
        self.assertEqual(seeds[3], "N1")
        self.assertEqual({s[0] for s in seeds[:3]}, {"S", "E", "C"})
        self.assertEqual(len(seeds), 6)
        self.assertTrue(set(seeds[4:]) < {"S2", "E2", "C2"} and len(set(seeds[4:])) == 2)

    def test_last_place_club_cannot_take_a_wild_card(self):
        # Division N's five clubs all finish 10-8, so its last place (n5, worst division record) could outrank
        # other divisions' tied clubs across divisions on conference record. It must still be excluded.
        t, rec = {}, {}

        def add(c, dv, w, cw, dw, l=None):
            t[c] = ("W", dv)
            rec[c] = S.Rec()
            r = rec[c]
            r.w, r.l, r.cw, r.cl, r.dw, r.dl = w, (18 - w) if l is None else l, cw, 12 - cw, dw, 8 - dw
        add("n1", "N", 14, 9, 7)
        for c, dw in zip(("n2", "n3", "n4", "n5"), (4, 3, 2, 1)):
            add(c, "N", 10, 9 if c == "n5" else 4, dw)         # n5: last in division, best conference record
        for dv in "SEC":
            add(f"{dv}1", dv, 13, 8, 6)
            add(f"{dv}2", dv, 10, 5, 4)
            add(f"{dv}3", dv, 10, 3, 3)
            add(f"{dv}4", dv, 6, 3, 2)
            add(f"{dv}5", dv, 5, 3, 1)
        orders = S.division_orders(t, rec, "z")
        self.assertEqual(orders[("W", "N")][-1], "n5")
        seeds = S.conference_seeds("W", t, rec, orders, "z")
        self.assertNotIn("n5", seeds)
        self.assertEqual(len(seeds), 6)
        for dv in "NSEC":
            self.assertNotIn(orders[("W", dv)][-1], seeds)

    def test_bracket_shape_and_home(self):
        # six clubs a conference; seeds 1 and 2 have a bye; 3 hosts 6 and 4 hosts 5
        self.assertEqual(S.wild_card_pairs(), [(3, 6), (4, 5)])
        # divisional round reseeded: 1 hosts the lowest surviving seed, 2 hosts the other survivor
        for left, want in (([3, 4], [(1, 4), (2, 3)]), ([3, 5], [(1, 5), (2, 3)]), ([6, 4], [(1, 6), (2, 4)]), ([6, 5], [(1, 6), (2, 5)])):
            self.assertEqual(S.divisional_pairs([1, 2] + left), want)
        with self.assertRaises(AssertionError):
            S.divisional_pairs([1, 3, 4, 5])                    # seed 2 always survives to this round
        self.assertEqual(S.host(6, 3), 3)
        self.assertEqual(S.host(2, 5), 2)                       # the higher remaining seed hosts the conference championship


class Rotation(unittest.TestCase):
    def test_exile_and_seed_handoff(self):
        order = ["a", "b", "c", "d", "e"]
        n = S.next_division_state(order, "amb")
        self.assertEqual(n["seeds"], {1: "a", 2: "b", 3: "c", 4: "d", 5: "amb"})
        self.assertEqual(n["ambassador"], "e")


def WG(week, away, home, sa, sh):
    return {"week": week, "away": away, "home": home, "away_score": sa, "home_score": sh}


class CoronationDesignation(unittest.TestCase):
    """Week 4 designation (Edition 8.55): the conferences, pooled over the opening four weeks' interconference games, are two aggregate
    teams ranked by the cascade without its division and conference steps; the winner's finalist is the Coronation's nominal home."""

    T = {"a": ("W", "x"), "b": ("W", "x"), "e": ("W", "x"), "c": ("E", "y"), "d": ("E", "y"), "f": ("E", "y")}

    def balanced(self):
        """One win each, point differential 0, equal strength of schedule (2/3 each); the Western opponents' victories were over the stronger club."""
        return [WG(1, "a", "c", 10, 7), WG(2, "d", "b", 10, 7), WG(3, "c", "f", 14, 3), WG(3, "a", "e", 14, 3)]

    def test_wins_decide_first(self):
        g = [WG(1, "a", "c", 10, 7), WG(2, "b", "d", 24, 3), WG(3, "e", "f", 20, 0), WG(4, "c", "a", 9, 3)]    # West wins 3 of the 4 pooled games
        r = S.conference_designation(self.T, g, "s")
        self.assertEqual((r["home_conference"], r["decided_by"]), ("W", "wins"))
        self.assertEqual((r["conferences"]["W"]["w"], r["conferences"]["W"]["l"], r["interconference_games"]), (3, 1, 4))

    def test_win_percentage_cannot_separate_two_conferences_whose_wins_tie(self):
        # every pooled game is one conference's win and the other's loss, so equal wins imply equal losses and equal percentages
        r = S.conference_designation(self.T, self.balanced(), "s")
        self.assertEqual(r["conferences"]["W"]["win_pct"], r["conferences"]["E"]["win_pct"])
        self.assertNotEqual(r["decided_by"], "win_pct")

    def test_point_differential_decides_when_wins_tie(self):
        g = [WG(1, "a", "c", 20, 7), WG(2, "d", "b", 10, 7)]
        r = S.conference_designation(self.T, g, "s")
        self.assertEqual((r["home_conference"], r["decided_by"], r["conferences"]["W"]["pd"]), ("W", "point_differential", 10))
        self.assertEqual(r["conferences"]["E"]["pd"], -10)

    def test_strength_of_schedule_aggregation(self):
        # wins tie 1-1 and the differential is 0. West's opponents (one entry per decided pooled game) are c and d, East's are a and b, each
        # with her whole record through Week 4: c 1-1, d 1-0 against a 2-0, b 0-1 (the games of week 3 are inside the conferences).
        g = self.balanced()[:3]                                            # no a>e: a is 1-0
        r = S.conference_designation(self.T, g, "s")
        self.assertEqual(r["conferences"]["W"]["sos"], "2/3")             # (1+1) wins over (2+1) decisions
        self.assertEqual(r["conferences"]["E"]["sos"], "1/2")             # a 1-0 and b 0-1
        self.assertEqual((r["home_conference"], r["decided_by"]), ("W", "strength_of_schedule"))

    def test_strength_of_victory_aggregation(self):
        r = S.conference_designation(self.T, self.balanced(), "s")
        self.assertEqual((r["conferences"]["W"]["sos"], r["conferences"]["E"]["sos"]), ("2/3", "2/3"))
        self.assertEqual((r["conferences"]["W"]["sov"], r["conferences"]["E"]["sov"]), ("1/2", "0"))     # West beat c (1-1); East beat b (0-1)
        self.assertEqual((r["home_conference"], r["decided_by"]), ("W", "strength_of_victory"))

    def test_seeded_draw_when_everything_ties_and_it_is_reproducible(self):
        none = S.conference_designation(self.T, [], "s1")
        self.assertEqual((none["decided_by"], none["interconference_games"]), ("seeded_draw", 0))
        self.assertEqual(none, S.conference_designation(self.T, [], "s1"))
        seen = {S.conference_designation(self.T, [], f"s{i}")["home_conference"] for i in range(80)}
        self.assertEqual(seen, {"W", "E"})                                # either conference can win the draw, seed by seed

    def test_only_the_first_four_weeks_and_only_interconference_games_count(self):
        base = S.conference_designation(self.T, self.balanced(), "s")
        later = self.balanced() + [WG(5, "c", "a", 40, 0), WG(9, "d", "b", 40, 0), WG(18, "f", "e", 40, 0)]    # East wins, everything after Week 4
        inner = self.balanced() + [WG(2, "a", "b", 50, 0), WG(4, "c", "d", 50, 0)]                            # inside conferences: not pooled
        self.assertEqual(S.conference_designation(self.T, later, "s"), base)
        got = S.conference_designation(self.T, inner, "s")
        self.assertEqual(got["interconference_games"], 2)
        for c in ("W", "E"):                                              # the pooled totals are untouched (opponents' records may move)
            for f in ("w", "l", "t", "pf", "pa", "pd"):
                self.assertEqual(got["conferences"][c][f], base["conferences"][c][f])

    def test_a_tie_is_not_played_for_wins_losses_or_opponents_but_its_points_count(self):
        g = [WG(1, "a", "c", 17, 17), WG(2, "d", "b", 9, 6)]
        r = S.conference_designation(self.T, g, "s")
        w, e = r["conferences"]["W"], r["conferences"]["E"]
        self.assertEqual((w["w"], w["l"], w["t"], e["w"], e["l"], e["t"]), (0, 1, 1, 1, 0, 1))
        self.assertEqual((w["pf"], w["pa"], e["pf"], e["pa"]), (23, 26, 26, 23))
        self.assertEqual(w["sos"], "1")                                   # only the decided game counts: opponent d is 1-0
        self.assertEqual((r["home_conference"], r["decided_by"]), ("E", "wins"))

    def test_game_order_does_not_matter(self):
        rng = random.Random(8)
        g = self.balanced()
        for _ in range(10):
            rng.shuffle(g)
            self.assertEqual(S.conference_designation(self.T, g, "s"), S.conference_designation(self.T, self.balanced(), "s"))

    def test_real_schedule_pools_eighty_games_and_the_two_conferences_mirror_each_other(self):
        teams, sched = load_schedule()
        rng = random.Random(4)
        pooled = [r for r in sched if int(r["week"]) <= 4 and teams[r["away"]][0] != teams[r["home"]][0]]
        self.assertEqual(len(pooled), 80)
        seen = set()
        for n in range(60):
            games = S.random_results(sched, rng, {c: rng.gauss(0, 1) for c in teams}, tie_rate=0.03)
            r = S.conference_designation(teams, games, f"s{n}")
            w, e = r["conferences"]["Western"], r["conferences"]["Eastern"]
            self.assertEqual((r["interconference_games"], w["w"], w["l"], w["pd"]), (80, e["l"], e["w"], -e["pd"]))
            self.assertEqual((w["w"] + w["l"] + w["t"], w["t"]), (80, e["t"]))
            later = [dict(g, away_score=g["home_score"], home_score=g["away_score"]) if int(g["week"]) > 4 else g for g in games]
            self.assertEqual(S.conference_designation(teams, later, f"s{n}"), r)          # nothing after Week 4 can move it
            seen.add(r["home_conference"])
        self.assertEqual(seen, {"Western", "Eastern"})


class RealSchedule(unittest.TestCase):
    def test_simulated_seasons_on_the_real_schedule(self):
        teams, sched = load_schedule()
        self.assertEqual((len(teams), len(sched)), (40, 360))
        rng = random.Random(2026)
        ties_seen = 0
        for n in range(150):
            strength = {c: rng.gauss(0, 1) for c in teams}
            games = S.random_results(sched, rng, strength, tie_rate=0.02)
            out = S.season_outcome(teams, games, f"s{n}")
            rec = out["records"]
            self.assertEqual({r.gp for r in rec.values()}, {18})               # ties still count as played
            self.assertEqual(sum(r.w for r in rec.values()), sum(r.l for r in rec.values()))
            self.assertEqual(sum(r.pf for r in rec.values()), sum(r.pa for r in rec.values()))
            self.assertEqual(sum(r.pd for r in rec.values()), 0)
            ties_seen += sum(r.t for r in rec.values())
            self.assertEqual(len(out["exiled"]), 8)
            self.assertEqual(len(set(out["exiled"].values())), 8)
            for dv, o in out["division_orders"].items():
                self.assertEqual(sorted(o), sorted(c for c, d in teams.items() if d == dv))
                self.assertEqual(o[-1], out["exiled"][dv])
            for conf, seeds in out["seeds"].items():
                self.assertEqual(len(set(seeds)), 6)
                self.assertTrue(all(teams[c][0] == conf for c in seeds))
            # shuffling the game list changes nothing
            g2 = list(games)
            rng.shuffle(g2)
            self.assertEqual(S.season_outcome(teams, g2, f"s{n}")["division_orders"], out["division_orders"])
        self.assertGreater(ties_seen, 0)

    def test_win_loss_totals_exclude_ties(self):
        teams, sched = load_schedule()
        rng = random.Random(1)
        games = S.random_results(sched, rng, None, tie_rate=0.2)
        rec = S.tally(teams, games)
        tie_games = sum(1 for g in games if g["away_score"] == g["home_score"])
        self.assertEqual(sum(r.w for r in rec.values()), 360 - tie_games)
        self.assertEqual(sum(r.t for r in rec.values()), 2 * tie_games)


if __name__ == "__main__":
    unittest.main(verbosity=1)
