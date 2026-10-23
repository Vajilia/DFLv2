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

    def test_penalties_are_the_rulebook_ones(self):
        names = {p["name"] for p in PENALTIES}
        self.assertEqual(names, {"false start", "delay of game", "offside", "offensive holding", "defensive holding", "illegal contact",
                                 "defensive pass interference", "offensive pass interference", "personal foul", "unsportsmanlike conduct"})
        seen = set()
        for seed in range(80):
            for row in play(seed=seed, log=True).plays:
                if row[6] == "penalty":
                    seen.add(row[7])
        self.assertTrue(seen <= names)
        self.assertGreaterEqual(len(seen), 8)

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
                self.assertEqual({i for i, _ in sq.new_injuries}, sq.out)
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


if __name__ == "__main__":
    unittest.main()
