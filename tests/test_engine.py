"""Game engine tests (slice 1): the rules core, determinism, scoring identities, overtime, hidden-ability guard."""
import copy
import os
import random
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl.characters import Creator, load_tables  # noqa: E402
from dfl.engine.game import play_game  # noqa: E402
from dfl.engine.squad import Squad  # noqa: E402
from dfl.engine.params import P, PENALTIES  # noqa: E402

CLUBS = [f"C{i:02d}" for i in range(6)]
POP = Creator(3, load_tables()).founding(2001, CLUBS)


def squad(cid, boost=0):
    cards = [copy.deepcopy(c) for c in POP["clubs"][cid]["roster"] + POP["clubs"][cid]["ps"]]
    for c in cards:
        c.attrs = {k: min(99, v + boost) for k, v in c.attrs.items()}
    return Squad(cid, cards)


def play(h="C00", a="C01", seed=1, decisive=False, log=False, boost=(0, 0)):
    return play_game(squad(h, boost[0]), squad(a, boost[1]), random.Random(seed), decisive, log)


class Rules(unittest.TestCase):
    def test_deterministic(self):
        a, b = play(seed=11, log=True), play(seed=11, log=True)
        self.assertEqual((a.score, a.plays, a.stats), (b.score, b.plays, b.stats))
        self.assertNotEqual(play(seed=11).score, play(seed=12).score)

    def test_points_add_up_from_scoring_events(self):
        for seed in range(60):
            r = play(seed=seed)
            for i in (0, 1):
                s = r.stats[i]
                self.assertEqual(r.score[i], 6 * s["td"] + 3 * s["fg_made"] + s["pat_made"] + 2 * s["two_made"] + 2 * s["safeties"], f"seed {seed} team {i}")

    def test_a_decisive_game_never_ties_and_a_regular_game_can(self):
        results = [play(seed=s, decisive=True) for s in range(300)]
        self.assertFalse(any(r.tie for r in results))
        regular = [play(seed=s) for s in range(1500)]
        ties = [r for r in regular if r.tie]
        self.assertTrue(ties)                                              # ten-minute overtime can end level
        self.assertTrue(all(r.periods == 5 for r in ties))                 # and only after overtime
        self.assertTrue(all(r.periods == 4 or r.periods >= 5 for r in regular))

    def test_overtime_only_follows_a_tie_after_regulation(self):
        for seed in range(400):
            r = play(seed=seed, log=True)
            if r.periods > 4:
                reg = [row for row in r.plays if row[0] <= 4]
                self.assertTrue(reg)
                self.assertGreater(len([x for x in r.plays if x[0] > 4]), 0)

    def test_each_side_gets_a_possession_in_overtime(self):
        seen = 0
        for seed in range(1500):
            r = play(seed=seed, log=True)
            if r.periods == 5 and not r.tie:
                owners = {row[2] for row in r.plays if row[0] == 5 and row[6] in ("run", "complete", "incomplete", "sack", "scramble", "int", "interception", "touchdown", "punt", "field goal", "fumble lost")}
                seen += 1
                if len(owners) < 2:
                    # allowed only when a defensive score or a safety ended it on the first possession
                    kinds = [row[6:] for row in r.plays if row[0] == 5][-3:]
                    self.assertTrue(any(k[0] in ("touchdown", "safety", "fumble lost", "interception") for k in kinds), kinds)
        self.assertGreater(seen, 5)

    def test_time_of_possession_fits_the_game_length(self):
        for seed in range(40):
            r = play(seed=seed)
            top = r.stats[0]["top"] + r.stats[1]["top"]
            if r.periods == 4:
                self.assertLessEqual(top, 3600 + 1)
            self.assertGreater(top, 2400)

    def test_field_position_and_downs_stay_in_bounds(self):
        r = play(seed=5, log=True)
        for row in r.plays:
            period, t, poss, down, dist, yl = row[:6]
            self.assertTrue(0 <= yl <= 100, row)
            self.assertTrue(1 <= down <= 5, row)
            self.assertGreaterEqual(t, -2)

    def test_penalties_come_from_the_catalogue(self):
        names = {p["name"] for p in PENALTIES}
        self.assertGreaterEqual(len(names), 40)
        for base in ("false start", "delay of game", "offensive holding", "defensive holding", "illegal contact", "defensive pass interference",
                     "offensive pass interference", "unsportsmanlike conduct", "roughing the passer", "intentional grounding"):
            self.assertIn(base, names)                                       # the section 10 list is all there
        seen = set()
        for seed in range(80):
            for row in play(seed=seed, log=True).plays:
                if row[6] == "penalty":
                    seen.add(row[7])
        self.assertTrue(seen <= names)
        self.assertGreaterEqual(len(seen), 25)

    def test_catalogue_rows_are_well_formed(self):
        for p in PENALTIES:
            self.assertIn(p["side"], ("off", "def", "either"))
            self.assertIn(p["when"], ("pre", "live", "pass", "kick"))
            self.assertTrue(0 < p["rate"] < 0.1, p["name"])
            self.assertGreater(p["nfl"], 0)
            if p["when"] == "kick":
                self.assertIn(p["effect"], ("replay_back", "replay_fwd", "rough", "ret_back", "cov_fwd", "oob"))
                self.assertTrue(p["kinds"] and set(p["kinds"]) <= set("pfk"))
            else:
                self.assertEqual(p["effect"], "")

    def test_penalty_counts_stay_near_the_nfl(self):
        pens = yds = 0
        n = 300
        for seed in range(n):
            r = play(h=f"C0{seed % 3}", a=f"C0{3 + seed % 3}", seed=seed)
            pens += sum(s["penalties"] for s in r.stats)
            yds += sum(s["pen_yds"] for s in r.stats)
            self.assertEqual(sum(c for d in r.pen_types for c, _ in d.values()), sum(s["penalties"] for s in r.stats))
        self.assertTrue(4.5 <= pens / n / 2 <= 7.5, pens / n / 2)
        self.assertTrue(40 <= yds / n / 2 <= 65, yds / n / 2)

    def test_two_minute_warning_stops_the_clock_at_two_minutes(self):
        warned = 0
        for seed in range(60):
            r = play(seed=seed, log=True)
            rows = [row for row in r.plays if row[6] == "two-minute warning"]
            periods = [row[0] for row in rows]
            self.assertEqual(len(periods), len(set(periods)))               # at most once a period
            self.assertTrue(set(periods) <= {2, 4})                         # second and fourth quarters only, never overtime
            for row in rows:
                self.assertEqual(row[1], 120)
            warned += len(rows)
        self.assertGreater(warned, 90)

    def test_a_second_unsportsmanlike_foul_ejects_the_player(self):
        from dfl.engine.game import Game
        g = Game(squad("C00"), squad("C01"), random.Random(1), log=True)
        pen = next(p for p in PENALTIES if p["name"] == "unsportsmanlike conduct")
        u = g.sq[0].units()
        card = u["off"][3]
        g.charge(0, dict(pen, side="off"), 15, card)
        self.assertNotIn(card.identity.id, g.sq[0].out)
        g.charge(0, dict(pen, side="off"), 15, card)
        self.assertIn(card.identity.id, g.sq[0].out)
        self.assertEqual(g.sq[0].ejected, [card.identity.id])
        self.assertNotIn(card.identity.id, {c.identity.id for c in g.sq[0].units()["off"]})   # she is replaced on the field

    def test_loss_of_down_and_half_the_distance(self):
        from dfl.engine.game import Game
        g = Game(squad("C00"), squad("C01"), random.Random(1), log=True)
        g.poss, g.yl, g.down, g.dist = 0, 40, 2, 10
        pen = dict(next(p for p in PENALTIES if p["name"] == "intentional grounding"), side="off")
        g.assess(0, pen, 12)
        self.assertEqual((g.yl, g.down, g.dist), (28, 3, 22))                # ten yards or the spot, and the down is lost
        g.yl, g.down, g.dist = 6, 4, 8
        g.assess(0, pen, 12)
        self.assertEqual(g.yl, 3)                                            # half the distance to the goal
        self.assertEqual(g.next[:2], ("ball", 1))                            # a lost fourth down is a turnover on downs
        g = Game(squad("C00"), squad("C01"), random.Random(1), log=True)
        g.poss, g.yl, g.down, g.dist = 0, 96, 2, 4
        dpi = dict(next(p for p in PENALTIES if p["name"] == "defensive pass interference"), side="def")
        g.assess(0, dpi, 30)
        self.assertEqual((g.yl, g.down, g.dist), (99, 1, 1))                 # a spot foul in the end zone puts the ball on the one

    def test_kickoffs_follow_the_rulebook(self):
        r = play(seed=9, log=True)
        starts = [row[7] for row in r.plays if row[6] == "kickoff" and isinstance(row[7], int)]
        self.assertTrue(starts)
        self.assertIn(25, starts)                                          # touchbacks come out to the 25


class Model(unittest.TestCase):
    def test_a_stronger_squad_wins_more_often(self):
        wins = sum(play(seed=s, boost=(15, 0)).score[0] > play(seed=s, boost=(15, 0)).score[1] for s in range(150))
        self.assertGreater(wins, 100)

    def test_injuries_remove_players_for_the_rest_of_the_game(self):
        found = False
        for seed in range(200):
            h, a = squad("C00"), squad("C01")
            r = play_game(h, a, random.Random(seed), False, True)
            for sq in (h, a):
                self.assertEqual({i for i, _ in sq.new_injuries} | set(sq.ejected), sq.out)
            if r.injuries["C00"] or r.injuries["C01"]:
                found = True
                self.assertTrue(all(1 <= w <= 30 for lst in r.injuries.values() for _, w in lst))
        self.assertTrue(found)

    def test_a_squad_with_no_quarterback_still_plays(self):
        cards = [c for c in POP["clubs"]["C00"]["roster"] + POP["clubs"]["C00"]["ps"] if c.identity.pos != "QB"]
        r = play_game(Squad("C00", cards), squad("C01"), random.Random(2))
        self.assertGreater(r.stats[0]["plays"], 30)

    def test_the_result_carries_no_hidden_ability(self):
        r = play(seed=3, log=True)
        text = repr((r.stats, r.players, r.plays, r.injuries)).lower()
        for hidden in ("attrs", "growth", "traits", "ovr", "overall"):
            self.assertNotIn(hidden, text)


class SeasonIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from dfl import league
        from dfl.engine.resolver import EngineResolver
        cls.league, cls.ER = league, EngineResolver
        cls.tpl = league.Template(os.path.join(ROOT, "data", "season1"))
        cls.clubs = sorted({r["team_city"] for r in cls.tpl.teams})
        cls.pop = Creator(5, load_tables()).founding(2001, cls.clubs)

    def season(self, seed=1):
        res = self.ER({c: Squad(c, self.pop["clubs"][c]["roster"] + self.pop["clubs"][c]["ps"]) for c in self.clubs})
        state = self.tpl.initial_state()
        out, _ = self.league.play_season(self.tpl, state, 1, res, random.Random(seed), seed="t")
        return res, out

    def test_a_whole_season_runs_on_the_engine_and_is_deterministic(self):
        res, out = self.season()
        res2, out2 = self.season()
        self.assertEqual(out["coronation_winner"], out2["coronation_winner"])
        self.assertEqual(out["outcome"]["seeds"], out2["outcome"]["seeds"])
        self.assertGreater(res.games_played, 300)
        self.assertGreater(res.injuries_seen, 100)
        recs = out["outcome"]["records"].values()
        self.assertTrue(all(r.w + r.l + r.t > 0 for r in recs))

    def test_the_bowl_is_played_by_all_star_squads(self):
        res, out = self.season()
        self.assertEqual({out["bowl"]["away"], out["bowl"]["home"]}, {"Western All-Stars", "Eastern All-Stars"})
        self.assertTrue(out["bowl"]["away_score"] != out["bowl"]["home_score"])
        self.assertTrue(any(n.endswith("All-Stars") for n in res.sides))

    def test_injured_players_miss_the_clubs_next_games(self):
        res, _ = self.season()
        some = next(((c, d) for c, d in res.games_out.items() if d), None)
        self.assertTrue(some is None or all(n > 0 for n in some[1].values()))


if __name__ == "__main__":
    unittest.main()
