"""The fairness-band harness, checked on small made-up leagues whose answers are known."""
import os
import random
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl import fairness as F  # noqa: E402

DIVS = [f"D{i}" for i in range(8)]
CLUBS = [f"{d}-{k}" for d in DIVS for k in range(5)]


def toy_league(seasons=40, seed=1, strength_weight=0.0, rng_games=17):
    """Clubs with fixed strengths; win% = luck plus strength_weight times strength. Division order by win%; last place is exiled and
    returns two seasons later as seed 5 (so it is in the division again, placed by the same results)."""
    rng = random.Random(seed)
    base = {c: rng.gauss(0, 1) for c in CLUBS}
    out, ambassadors, exiled_prev = [], set(), []
    away = {}                                     # club -> season it comes back
    for s in range(seasons):
        active = [c for c in CLUBS if away.get(c, -1) <= s]
        # keep exactly 5 per division active: bring back whoever returns, exile one per division
        recs, orders, exiled, seeds = {}, {}, {}, {"A": [], "B": []}
        by_div = {}
        for c in active:
            by_div.setdefault(c.split("-")[0], []).append(c)
        for d, cs in by_div.items():
            for c in cs:
                p = min(0.95, max(0.05, 0.5 + 0.1 * strength_weight * base[c] + rng.gauss(0, 0.12)))
                w = round(p * rng_games)
                recs[c] = [w, rng_games - w, 0, 20 * rng_games, 20 * rng_games]
            orders[d] = sorted(cs, key=lambda c: (-recs[c][0], c))
        for d, o in orders.items():
            if len(o) >= 5:
                exiled[d] = o[-1]
                away[o[-1]] = s + 2
        ranked = sorted(recs, key=lambda c: (-recs[c][0], c))
        seeds["A"], seeds["B"] = ranked[:7], ranked[7:14]
        champ = ranked[0] if strength_weight else rng.choice(ranked[:14])
        out.append({"year": 2000 + s, "records": recs, "orders": orders, "exiled": exiled, "seeds": seeds, "champion": champ,
                    "draft_order": list(reversed(ranked)), "margins": [rng.choice((3, 7, 10, 14, 21)) for _ in range(300)],
                    "strength": {c: base[c] for c in recs}, "extra": {"roster_mean": 68.0},
                    "games": [[a, b] for a in recs for b in rng.sample(sorted(recs), 3) if a != b]})
    return out


class Bands(unittest.TestCase):
    def test_fifteen_bands_are_loaded_from_data(self):
        b = F.load_bands()
        self.assertEqual(len(b), 15)
        for name, (lo, hi, meaning) in b.items():
            self.assertLessEqual(lo, hi)
            self.assertTrue(meaning)


class Metrics(unittest.TestCase):
    def test_win_pct_counts_a_tie_as_half(self):
        self.assertAlmostEqual(F.win_pct([10, 6, 1, 0, 0]), (10.5) / 17)

    def test_a_luck_league_has_no_repeat_signal_and_a_strength_league_does(self):
        luck = F.league_metrics(toy_league(40, 1, strength_weight=0.0))
        strong = F.league_metrics(toy_league(40, 1, strength_weight=3.0))
        self.assertLess(abs(luck["year_to_year_corr"]), 0.15)
        self.assertGreater(strong["year_to_year_corr"], 0.5)
        self.assertGreater(strong["win_pct_sd"], luck["win_pct_sd"])

    def test_close_game_share_is_counted_from_margins(self):
        lg = toy_league(12, 2)
        m = F.league_metrics(lg, warmup=2)
        want = sum(1 for s in lg[2:] for x in s["margins"] if x <= 8) / sum(len(s["margins"]) for s in lg[2:])
        self.assertAlmostEqual(m["close_game_share"], want)

    def test_one_dynasty_breaks_the_title_concentration_bands(self):
        lg = toy_league(40, 3)
        for s in lg:
            s["champion"] = CLUBS[0]
        m = F.league_metrics(lg)
        self.assertEqual(m["max_titles_in_20"], 20)
        self.assertEqual(m["repeat_champion_rate"], 1.0)
        self.assertEqual(m["best_team_title_odds"] < 0.5, True)

    def test_the_strongest_club_winning_every_year_is_measured_as_odds_one(self):
        lg = toy_league(20, 4)
        for s in lg:
            s["champion"] = max(s["records"], key=lambda c: (s["strength"][c], c))
        self.assertEqual(F.league_metrics(lg, warmup=2)["best_team_title_odds"], 1.0)

    def test_returners_are_followed_two_seasons_after_exile(self):
        lg = toy_league(40, 5)
        m = F.league_metrics(lg)
        self.assertIn("returner_avg_finish", m)
        self.assertTrue(1.0 <= m["returner_avg_finish"] <= 5.0)
        for k in ("returner_win_div", "returner_fifth_again", "returner_playoff_first", "returner_top3_in_3", "stuck_at_bottom", "exile_double_early"):
            self.assertTrue(0.0 <= m[k] <= 1.0, k)

    def test_an_exile_effect_is_seen_when_exile_costs_strength(self):
        lg = toy_league(40, 6, strength_weight=0.0)
        m = F.league_metrics(lg)
        self.assertLess(abs(m["exile_effect"]), 3)             # luck only: no real effect, only noise

    def test_stuck_at_bottom_counts_three_straight_bottom_two_finishes(self):
        lg = toy_league(14, 7)
        club = CLUBS[0]
        for s in lg:                                          # force this club to finish 4th every season
            d = club.split("-")[0]
            if club in s["orders"][d]:
                o = [c for c in s["orders"][d] if c != club]
                o.insert(3, club)
                s["orders"][d] = o
        m = F.league_metrics(lg, warmup=2)
        self.assertGreater(m["stuck_at_bottom"], 0.0)


class SchedulePushAndTrend(unittest.TestCase):
    def test_schedule_push_is_positive_when_strong_clubs_face_strong_clubs(self):
        lg = toy_league(12, 9)
        for s in lg:
            st = s["strength"]
            ranked = sorted(st, key=lambda c: st[c])
            s["games"] = [[ranked[i], ranked[min(len(ranked) - 1, i + k)]] for i in range(len(ranked)) for k in (1, 2, 3)]
        slope, c = F.schedule_push(lg, warmup=2)
        self.assertGreater(c, 0.3)
        # opponents drawn from the strongest clubs only: strong clubs face weak ones -> negative
        for s in lg:
            st = s["strength"]
            ranked = sorted(st, key=lambda c: st[c])
            s["games"] = [[ranked[i], ranked[len(ranked) - 1 - i]] for i in range(len(ranked)) for _ in (1, 2)]
        self.assertLess(F.schedule_push(lg, warmup=2)[1], -0.3)

    def test_a_schedule_that_ignores_strength_shows_only_the_never_play_yourself_slope(self):
        lg = toy_league(30, 12)
        rng = random.Random(5)
        for s in lg:
            cs = sorted(s["strength"])
            s["games"] = [[a, b] for a in cs for b in rng.sample([c for c in cs if c != a], 6)]
        slope = F.schedule_push(lg, warmup=2)[0]
        self.assertAlmostEqual(slope, F.schedule_baseline(40), delta=0.012)

    def test_decades_split_a_century_into_blocks_with_the_headline_figures(self):
        lg = toy_league(40, 10)
        rows = F.decades(lg, 10)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["seasons"], "2000-2009")
        for r in rows:
            self.assertGreaterEqual(r["most_titles_one_club"], 1)
            self.assertTrue(0 < r["close_game_share"] < 1)


class Verdicts(unittest.TestCase):
    def test_pass_fail_and_unclear(self):
        bands = {"x": (0.0, 1.0, "m")}
        mk = lambda vals: [[{"year": i, "v": v} for i in range(1)] for v in vals]
        orig = F.league_metrics
        try:
            for vals, want in (([0.4, 0.5, 0.45, 0.5], "PASS"), ([2.0, 2.1, 2.2, 2.0], "FAIL"), ([0.9, 1.0, 1.2, 1.1], "UNCLEAR")):
                it = iter(vals)
                F.league_metrics = lambda lg, warmup=0, it=it: {"x": next(it)}
                rows = F.verdicts(mk(vals), bands)
                self.assertEqual(rows[0]["verdict"], want, vals)
        finally:
            F.league_metrics = orig

    def test_worst_league_is_judged_on_the_single_worst_value(self):
        orig = F.league_metrics
        try:
            vals = iter([3, 8, 2])
            F.league_metrics = lambda lg, warmup=0: {"worst_league_titles": next(vals)}
            rows = F.verdicts([[1], [1], [1]], {"worst_league_titles": (0, 7, "m")})
            self.assertEqual((rows[0]["mean"], rows[0]["verdict"]), (8, "FAIL"))
        finally:
            F.league_metrics = orig

    def test_the_table_is_readable(self):
        t = F.format_table([{"band": "a", "mean": 0.2, "se": 0.01, "low": 0.1, "high": 0.3, "verdict": "PASS", "n": 3, "meaning": ""}])
        self.assertIn("| a | 0.200 ± 0.020 | 0.1 to 0.3 | PASS |", t)


if __name__ == "__main__":
    unittest.main()
