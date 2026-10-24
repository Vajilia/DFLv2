"""Per-player production, season analytics, Tiaras and the halls (Edition 8.30)."""
import hashlib
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import test_engine as TE  # noqa: E402
from dfl.engine import production as Pr  # noqa: E402
from dfl.ledger import analytics, honours  # noqa: E402
from dfl.ledger.world import World  # noqa: E402


class Recording(unittest.TestCase):
    def test_recording_changes_no_game_result(self):
        """A digest of 240 games (scores, periods, team and player statistics, injuries) taken before recording existed."""
        rows = []
        for seed in range(120):
            for h, a in (("C00", "C01"), ("C02", "C03")):
                r = TE.play(h, a, seed=seed, decisive=(seed % 3 == 0))
                rows.append([list(r.score), r.periods, r.stats, {k: v for k, v in r.injuries.items()}, r.players])
        got = hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()
        with open(os.path.join(ROOT, "tests", "golden_engine.json")) as fh:
            self.assertEqual(got, json.load(fh)["digest"])

    def test_a_scrimmage_play_is_zero_sum_between_the_sides(self):
        for seed in range(6):
            r = TE.play(seed=seed)
            tot = sum(v[1] for d in r.production.values() for pt, v in d.items() if pt in ("pass", "run"))
            self.assertAlmostEqual(tot, 0.0, places=6, msg=f"seed {seed}")

    def test_the_shares_of_each_side_of_each_play_sum_to_one(self):
        for key, groups in Pr.tables()["shares"].items():
            self.assertAlmostEqual(sum(groups.values()), 1.0, msg=str(key))

    def test_production_is_reproducible_and_every_team_play_is_counted(self):
        a, b = TE.play(seed=4), TE.play(seed=4)
        self.assertEqual(a.production, b.production)
        n = sum(t.get("pass", [0])[0] + t.get("run", [0])[0] for t in a.team_production)
        self.assertGreater(n, 80)

    def test_every_player_on_the_field_has_the_opportunity_and_credit_goes_to_roles(self):
        r = TE.play(seed=9)
        qbs = [pid for pid, d in r.production.items() if d.get("pass", [0])[0] > 10]
        self.assertTrue(qbs)
        # kickers and punters are credited only for kicks
        sq = TE.squad("C00")
        for pos in ("K", "P"):
            pid = sq.available(pos)[0].identity.id
            self.assertTrue(set(r.production.get(pid, {})) <= {"kick", "punt"})


class Models(unittest.TestCase):
    def test_expected_points_rise_toward_the_goal_and_fall_with_each_down(self):
        v = [Pr.ep(y) for y in (1, 25, 50, 75, 99)]
        self.assertEqual(v, sorted(v))
        self.assertLess(Pr.ep(50, 3, 10), Pr.ep(50, 1, 10))
        self.assertGreater(Pr.ep(50, 2, 3), Pr.ep(50, 2, 10))

    def test_win_probability_is_a_probability_that_grows_with_the_lead_and_with_less_time(self):
        self.assertAlmostEqual(Pr.wp(0, 1800), 0.5)
        self.assertLess(Pr.wp(3, 3000), Pr.wp(3, 100))
        self.assertLess(Pr.wp(-7, 600), 0.5)
        self.assertTrue(0 < Pr.wp(-14, 60) < Pr.wp(14, 60) < 1)


def world_with_production(seed=71):
    w = World([f"C{i:02d}" for i in range(1, 9)], seed=seed)
    w.open_year(first=True)
    return w


class Analytics(unittest.TestCase):
    def setUp(self):
        self.w = world_with_production()
        w = self.w
        self.qbs = [p for p in sorted(w.L.players.values(), key=lambda q: q.pid) if p.pos == "QB" and p.where in ("roster", "ps")][:8]

    def fill(self, epas, opps=None, n=600):
        w, y = self.w, self.w.L.year
        prod, team = w.production.setdefault(y, {}), w.team_production.setdefault(y, {})
        clubs = sorted(w.L.clubs)
        for c, v in zip(clubs, (0.1, 0.0, -0.1, 0.05, -0.05, 0.2, -0.2, 0.0)):
            team[c] = {"off": {"pass": [500, 500 * v], "run": [300, 0.0]}, "def": {"pass": [500, 500 * v], "run": [300, 0.0]}}
        for i, p in enumerate(self.qbs):
            opp = (opps or {}).get(i, clubs[i % len(clubs)])
            prod[p.pid] = {opp: {"pass": [n, epas[i], epas[i] / 50]}}
        w.appearances[y] = {p.pid for p in self.qbs}

    def test_better_production_scores_higher_and_scores_are_standardised(self):
        self.fill([50.0 * i for i in range(8)])
        rows = analytics.season_close(self.w, self.w.L.year)
        scores = [rows[p.pid]["score"] for p in self.qbs]
        self.assertEqual(scores, sorted(scores))
        self.assertAlmostEqual(sum(scores) / len(scores), 0.0, places=6)

    def test_a_player_below_the_opportunity_threshold_is_listed_but_not_scored(self):
        self.fill([10.0] * 8)
        y = self.w.L.year
        self.w.production[y][self.qbs[0].pid] = {"C01": {"pass": [3, 1.0, 0.0]}}
        rows = analytics.season_close(self.w, y)
        self.assertNotIn("score", rows[self.qbs[0].pid])
        self.assertIn("score", rows[self.qbs[1].pid])

    def test_the_same_production_against_a_tougher_unit_counts_for_more(self):
        clubs = sorted(self.w.L.clubs)
        self.fill([100.0] * 8, opps={0: clubs[6], 1: clubs[5], **{i: clubs[0] for i in range(2, 8)}})
        # the offence's opponents: the club whose defence allowed least per play is the toughest (-0.2 per play: clubs[6] allowed least)
        rows = analytics.season_close(self.w, self.w.L.year)
        a, b = rows[self.qbs[0].pid], rows[self.qbs[1].pid]
        self.assertEqual(a["epa"], b["epa"])
        self.assertGreater(a["adj"], b["adj"])

    def test_credited_seasons_count_once_and_production_is_dropped(self):
        self.fill([1.0] * 8)
        y = self.w.L.year
        analytics.season_close(self.w, y)
        self.assertTrue(all(y in self.w.credited[p.pid] for p in self.qbs))
        self.assertNotIn(y, self.w.production)
        self.assertNotIn(y, self.w.team_production)


class Honours(unittest.TestCase):
    DIV = {f"C{i:02d}": ("Eastern", "North") if i <= 4 else ("Western", "South") for i in range(1, 9)}

    def setUp(self):
        self.w = world_with_production(73)
        w = self.w
        self.qbs = [p for p in sorted(w.L.players.values(), key=lambda q: q.pid) if p.pos == "QB"][:6]
        for p in self.qbs:
            w.persona[p.pid] = ("QB " + str(p.pid), "QB")

    def give(self, pid, scores, club="C01", seasons=6, tiara=True):
        w = self.w
        for i, s in enumerate(scores):
            w.career.setdefault(pid, {})[2000 + i] = {"club": club, "score": s, "pos": "QB"}
        w.credited[pid] = set(range(2000, 2000 + seasons))
        if tiara:
            w.tiaras[pid] = {2003}

    def test_the_tiara_goes_to_the_final_53_and_the_six_leaders_once(self):
        w = self.w
        got = honours.award_tiara(w, 2005, "C02")
        lead = {w.leaders["C02"][r].identity.id for r in ("ceo", "gm", "hc", "oc", "dc", "stc")}
        self.assertTrue(lead <= got)
        self.assertTrue(set(w.L.clubs["C02"].roster) <= got)
        honours.award_tiara(w, 2005, "C02")
        self.assertEqual(w.tiaras[next(iter(lead))], {2005})

    def test_eligibility_needs_a_tiara_and_five_credited_seasons(self):
        w = self.w
        a, b, c = self.qbs[:3]
        self.give(a.pid, [1] * 6)
        self.give(b.pid, [1] * 4, seasons=4)
        self.give(c.pid, [1] * 6, tiara=False)
        self.assertEqual([honours.eligible(w, p.pid) for p in (a, b, c)], [True, False, False])

    def test_a_register_holds_its_slots_replaces_by_value_and_keeps_ever_membership(self):
        w = self.w
        a, b = self.qbs[:2]
        self.give(a.pid, [3, 3, 3])
        honours.update(w, 2010, self.DIV)
        self.assertEqual(w.hall["reg"][("league",)]["QB"], [a.pid])
        self.assertEqual(w.hall["reg"][("club", "C01")]["QB"], [a.pid])
        self.assertEqual(w.hall["reg"][("division", "Eastern", "North")]["QB"], [a.pid])
        self.give(b.pid, [4, 4, 4])
        honours.update(w, 2011, self.DIV)
        self.assertEqual(w.hall["reg"][("league",)]["QB"], [b.pid])
        self.assertIn(a.pid, w.hall["ever"][("league",)])               # displaced, but a member for ever
        self.assertTrue(any(e["pid"] == a.pid and e["kind"] == "displaced" for e in w.hall["log"]))

    def test_an_equal_challenger_displaces_the_incumbent(self):
        w = self.w
        a, b = self.qbs[:2]
        self.give(a.pid, [3, 3])
        honours.update(w, 2010, self.DIV)
        self.give(b.pid, [3, 3])
        honours.update(w, 2011, self.DIV)
        self.assertEqual(w.hall["reg"][("league",)]["QB"], [b.pid])

    def test_value_counts_only_seasons_inside_the_scope(self):
        w = self.w
        a, b = self.qbs[:2]
        self.give(a.pid, [5, 5], club="C01")
        self.give(b.pid, [4, 4], club="C06")
        w.career[a.pid][2002] = {"club": "C06", "score": -9, "pos": "QB"}
        w.credited[a.pid] |= {2002}
        honours.update(w, 2010, self.DIV)
        self.assertEqual(w.hall["reg"][("club", "C01")]["QB"], [a.pid])
        self.assertEqual(w.hall["reg"][("club", "C06")]["QB"], [b.pid])
        self.assertEqual(w.hall["reg"][("league",)]["QB"], [b.pid])        # a: 5 + 5 - 9 = 1, b: 8

    def test_a_fan_entrant_is_a_non_member_candidate_and_the_choice_is_reproducible(self):
        w = self.w
        for i, p in enumerate(self.qbs):
            self.give(p.pid, [1 + i] * 3)
        honours.update(w, 2010, self.DIV)
        member = w.hall["reg"][("league",)]["QB"][0]
        clubs = sorted(w.L.clubs)
        win = honours.fan_entrant(w, 2010, clubs)
        self.assertNotEqual(win, member)
        self.assertIn(win, w.hall["ever"][("league",)])
        self.assertEqual(w.hall["fan"], [win])
        w2 = world_with_production(73)
        for p in self.qbs:
            w2.persona[p.pid] = w.persona[p.pid]
        for i, p in enumerate(self.qbs):
            Honours.give(type("T", (), {"w": w2})(), p.pid, [1 + i] * 3)
        honours.update(w2, 2010, self.DIV)
        self.assertEqual(honours.fan_entrant(w2, 2010, clubs), win)


if __name__ == "__main__":
    unittest.main()
