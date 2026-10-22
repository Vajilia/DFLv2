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
        self.assertEqual(S.rank(["A", "B"], rec, t), ["B", "A"])   # falls to SOS (equal), then point differential

    def test_sos_higher_is_better_then_pd_then_sov(self):
        t = {"A": ("W", "N"), "B": ("W", "N")}
        strong, weak, o = "S", "K", None
        t.update({"S": ("E", "N"), "K": ("E", "N")})
        base = dict(w=10, l=8)
        rec = self.mk({
            "A": dict(base, opps=["S"], beaten=[], pf=300, pa=300),
            "B": dict(base, opps=["K"], beaten=[], pf=400, pa=300),
            "S": dict(w=12, l=6), "K": dict(w=6, l=12)})
        self.assertEqual(S.rank(["A", "B"], rec, {"A": ("W", "N"), "B": ("E", "N"), "S": ("E", "N"), "K": ("E", "N")}), ["A", "B"])
        rec["A"].opps = rec["B"].opps = ["S"]
        self.assertEqual(S.rank(["A", "B"], rec, {"A": ("W", "N"), "B": ("E", "N"), "S": ("E", "N"), "K": ("E", "N")}), ["B", "A"])
        rec["A"].pf = rec["B"].pf = 300
        rec["A"].beaten, rec["B"].beaten = ["S"], ["K"]
        self.assertEqual(S.rank(["A", "B"], rec, {"A": ("W", "N"), "B": ("E", "N"), "S": ("E", "N"), "K": ("E", "N")}), ["A", "B"])

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
        # still seed 4 and the 12-6 runners-up are wild cards 5-7.
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
        self.assertEqual({s for s in seeds[4:]}, {"S2", "E2", "C2"})

    def test_bracket_shape_and_home(self):
        self.assertEqual(S.wild_card_pairs(), [(2, 7), (3, 6), (4, 5)])
        self.assertEqual(S.divisional_pairs([1, 4, 5, 7]), [(1, 7), (4, 5)])
        self.assertEqual(S.divisional_pairs([1, 2, 3, 4]), [(1, 4), (2, 3)])
        self.assertEqual(S.host(6, 3), 3)


class Rotation(unittest.TestCase):
    def test_exile_and_seed_handoff(self):
        order = ["a", "b", "c", "d", "e"]
        n = S.next_division_state(order, "amb")
        self.assertEqual(n["seeds"], {1: "a", 2: "b", 3: "c", 4: "d", 5: "amb"})
        self.assertEqual(n["ambassador"], "e")


class Coronation(unittest.TestCase):
    def test_forty_year_window(self):
        venues = [f"V{i:02d}" for i in range(48)]
        hist = {}
        for y in range(2000, 2400):
            el = S.coronation_eligible(hist, venues, y)
            if y >= 2000 + 39:
                self.assertEqual(len(el), 9, y)
            pick = S.coronation_pick(hist, venues, y, "t")
            self.assertEqual(pick, S.coronation_pick(hist, venues, y, "t"))
            hist[y] = pick
        for v in venues:
            yrs = sorted(y for y, x in hist.items() if x == v)
            self.assertTrue(all(b - a >= 40 for a, b in zip(yrs, yrs[1:])), v)
        self.assertEqual(len(set(hist.values())), 48)    # every venue gets its turn within 400 years


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
                self.assertEqual(len(set(seeds)), 7)
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
