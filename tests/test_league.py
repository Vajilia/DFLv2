"""Season loop tests: rotation matches the written spec, determinism, tables validate, state hand-off is sound."""
import os
import random
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl import league, standings  # noqa: E402
from dfl.resolver import PlaceholderResolver  # noqa: E402

TABLES = os.environ.get("DFL_TABLES", os.path.join(ROOT, "data", "season1"))


def spec_bed(conf, div, n):
    """Section 3 of the design document, written out independently of schedule_bed_rotation."""
    i = (league.WEST if conf == "Western" else league.EAST).index(div)
    k, p = (n - 1) % 4, ((n - 1) // 4) % 2
    return 1 + 4 * (p if conf == "Western" else 1 - p) + (i + k) % 4


def run(seasons, seed):
    tpl = league.Template(TABLES)
    state, rng, res, hist, out = tpl.initial_state(), random.Random(seed), PlaceholderResolver(), {}, []
    for s in range(1, seasons + 1):
        r, state = league.play_season(tpl, state, s, res, rng, hist, seed=f"t{seed}")
        out.append(r)
    return tpl, out, state


class Rotation(unittest.TestCase):
    def test_bed_module_matches_section_3_for_64_seasons(self):
        for n in range(1, 65):
            for conf, div in league.DIVISIONS:
                self.assertEqual(league.bed_of(conf, div, n), spec_bed(conf, div, n), (n, conf, div))

    def test_every_season_fills_each_bed_once_and_repeats_every_eight(self):
        for n in range(1, 25):
            beds = sorted(league.bed_of(c, d, n) for c, d in league.DIVISIONS)
            self.assertEqual(beds, list(range(1, 9)))
            for c, d in league.DIVISIONS:
                self.assertEqual(league.bed_of(c, d, n), league.bed_of(c, d, n + 8))

    def test_conferences_flip_every_fourth_season(self):
        for c, d in league.DIVISIONS:
            west = c == "Western"
            self.assertEqual(league.bed_of(c, d, 1) <= 4, west)
            self.assertEqual(league.bed_of(c, d, 4) <= 4, west)
            self.assertEqual(league.bed_of(c, d, 5) <= 4, not west)


class Loop(unittest.TestCase):
    def test_season_one_reproduces_the_template(self):
        tpl = league.Template(TABLES)
        teams, sched, tour = league.season_tables(tpl, tpl.initial_state(), 1)
        self.assertEqual(sched, tpl.sched)
        self.assertEqual(tour, tpl.tour)
        self.assertEqual(teams, tpl.teams)

    def test_deterministic_given_seed(self):
        _, a, _ = run(6, 7)
        _, b, _ = run(6, 7)
        _, c, _ = run(6, 8)
        key = lambda rs: [(r["coronation_venue"], r["coronation_winner"], r["outcome"]["exiled"]) for r in rs]
        self.assertEqual(key(a), key(b))
        self.assertNotEqual(key(a), key(c))

    def test_state_handoff_each_season(self):
        tpl = league.Template(TABLES)
        state, rng, res, hist = tpl.initial_state(), random.Random(3), PlaceholderResolver(), {}
        for s in range(1, 25):
            r, new = league.play_season(tpl, state, s, res, rng, hist)
            for dv in league.DIVISIONS:
                order = r["outcome"]["division_orders"][dv]
                self.assertEqual([new[dv]["seeds"][i] for i in (1, 2, 3, 4)], order[:4])
                self.assertEqual(new[dv]["amb"], order[4])                      # last place is exiled
                self.assertEqual(new[dv]["seeds"][5], state[dv]["amb"])         # old ambassador returns as seed 5
            clubs = {c for dv in new.values() for c in list(dv["seeds"].values()) + [dv["amb"]]}
            self.assertEqual(len(clubs), 48)
            state = new

    def test_all_games_and_playoffs_well_formed(self):
        _, rs, _ = run(8, 2)
        for r in rs:
            self.assertEqual(len(r["games"]), 360)
            for conf, seeds in r["outcome"]["seeds"].items():
                self.assertEqual(len(seeds), 7)
            self.assertEqual(len(r["playoff_log"]), 2 * 6)          # 3 + 2 + 1 games per conference
            self.assertTrue(all(a != b for _, _, _, a, b in r["playoff_log"]))   # no tied playoff games

    def test_tables_validate_for_20_seasons(self):
        tpl, rs, _ = run(20, 5)
        with tempfile.TemporaryDirectory() as tmp:
            for r in rs:
                d = f"{tmp}/s{r['season']}"
                league.write_tables(tpl, d, r["teams"], r["schedule"], r["tour"], year=r["year"])
                p = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "tools", "validate.py"), d, "--season", str(r["season"])],
                                   capture_output=True, text=True)
                self.assertEqual(p.returncode, 0, p.stdout[-800:])

    def test_coronation_window_over_120_seasons(self):
        _, rs, _ = run(120, 4)
        yrs = {}
        for r in rs:
            yrs.setdefault(r["coronation_venue"], []).append(r["year"])
        for v, ys in yrs.items():
            self.assertTrue(all(b - a >= 40 for a, b in zip(ys, ys[1:])), v)


if __name__ == "__main__":
    unittest.main()


class TourBowlAndDraftOrder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tpl, cls.res, cls.state = run(6, 3)

    def test_tour_is_a_single_round_robin_among_the_eight_ambassadors(self):
        for r in self.res:
            tg = r["tour_games"]
            self.assertEqual(len(tg), 28)
            amb = {t["team_city"] for t in r["teams"] if t["club_status"] == "ambassador"}
            self.assertEqual(len(amb), 8)
            pairs = {frozenset((g["away"], g["home"])) for g in tg}
            self.assertEqual(len(pairs), 28)
            self.assertTrue(all(g["away"] in amb and g["home"] in amb for g in tg))
            played = {}
            for g in tg:
                for c in (g["away"], g["home"]):
                    played[c] = played.get(c, 0) + 1
            self.assertEqual(set(played.values()), {7})

    def test_eight_distinct_venues_one_per_round_plus_the_bowl(self):
        pool = {r["venue_id"] for r in league._read(f"{TABLES}/INTL_VENUES.csv") if r["active"] == "true"}
        for r in self.res:
            v = r["venues"]
            self.assertEqual(len(v), 8)
            self.assertEqual(len(set(v.values())), 8)
            self.assertTrue(set(v.values()) <= pool)
            for g in r["tour_games"]:
                self.assertEqual(g["venue"], v[g["venue_slot"]])
            self.assertEqual(r["bowl"]["venue"], v["BOWL"])

    def test_the_bowl_is_played_without_a_tie_between_the_two_all_star_sides(self):
        for r in self.res:
            b = r["bowl"]
            self.assertEqual({b["away"], b["home"]}, {"Western All-Stars", "Eastern All-Stars"})      # which is nominal home is a seeded draw
            self.assertNotEqual(b["away_score"], b["home_score"])
            amb = {t["team_city"]: t["conference"] for t in r["teams"] if t["club_status"] == "ambassador"}
            self.assertEqual({conf: amb[c] for conf, c in b["staff_clubs"].items()}, {"Western": "Western", "Eastern": "Eastern"})

    def test_draft_order_has_all_48_clubs_with_returning_ambassadors_first(self):
        for r in self.res:
            o = r["draft_order"]
            amb = {t["team_city"] for t in r["teams"] if t["club_status"] == "ambassador"}
            self.assertEqual(len(o), 48)
            self.assertEqual(len(set(o)), 48)
            self.assertEqual(set(o[:8]), amb)
            self.assertEqual(o[-1], r["coronation_winner"])
            self.assertEqual({o[-2]}, {c for c in (r["champions"]["Western"], r["champions"]["Eastern"])} - {r["coronation_winner"]})

    def test_exiled_clubs_pick_in_the_worst_group(self):
        for r in self.res:
            o = r["draft_order"]
            exiled = set(r["outcome"]["exiled"].values())
            non_playoff_end = 8 + 26
            self.assertTrue(exiled <= set(o[8:non_playoff_end]))
