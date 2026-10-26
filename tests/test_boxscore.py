"""Box scores, the structured play table and play-by-play text (Edition 8.47)."""
import json
import os
import random
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import test_engine as TE  # noqa: E402
from dfl.engine.boxscore import box_score, format_box, play_by_play  # noqa: E402
from dfl.engine.game import play_game  # noqa: E402


def game(seed, log=True, decisive=False):
    h, a = TE.squad("C00"), TE.squad("C01")
    return play_game(h, a, random.Random(seed), decisive, log), h, a


class Totals(unittest.TestCase):
    def test_the_line_score_adds_up_to_the_final_score(self):
        for seed in range(80):
            r, h, a = game(seed)
            b = box_score(r, h, a)
            self.assertEqual([sum(b["line"][0]), sum(b["line"][1])], list(r.score), seed)
            self.assertEqual(len(b["line"][0]), max(4, r.periods))
            if b["scoring"]:
                self.assertEqual(b["scoring"][-1]["score"], list(r.score), seed)

    def test_player_lines_add_up_to_the_team_lines(self):
        for seed in range(40):
            r, h, a = game(seed)
            for side, sq in enumerate((h, a)):
                ids = set(sq.cards)
                st = r.stats[side]
                pl = {i: v for i, v in r.players.items() if i in ids}
                s = lambda key: sum(v.get(key, 0) for v in pl.values())
                self.assertEqual(s("rush_yds"), st["rush_yds"], (seed, side))
                self.assertEqual(s("car"), st["rush_att"], (seed, side))
                self.assertEqual(s("pass_yds"), st["pass_yds"], (seed, side))
                self.assertEqual(s("comp"), st["comp"])
                self.assertEqual(s("att"), st["pass_att"])
                self.assertEqual(s("rec_yds"), st["pass_yds"])
                self.assertEqual(s("punts"), st["punts"])
                self.assertEqual(s("fg_att"), st["fg_att"])
                self.assertEqual(s("fg_made"), st["fg_made"])
                self.assertEqual(s("xp_att"), st["pat_att"])
                self.assertEqual(s("int"), st["ints"])
                other = {i: v for i, v in r.players.items() if i not in ids}
                o = lambda key: sum(v.get(key, 0) for v in other.values())
                self.assertEqual(o("def_int"), st["ints"])
                self.assertEqual(o("ff"), st["fumbles_lost"])
                self.assertEqual(o("fr"), st["fumbles_lost"])

    def test_sacks_are_credited_to_the_defense_that_made_them(self):
        for seed in range(40):
            r, h, a = game(seed)
            for side, sq in enumerate((h, a)):
                ids = set(sq.cards)
                made = sum(v.get("sack", 0) for i, v in r.players.items() if i in ids)
                self.assertAlmostEqual(made, r.stats[side]["sacks"], msg=f"seed {seed} side {side}")

    def test_the_team_block_matches_the_stats(self):
        r, h, a = game(7)
        b = box_score(r, h, a)
        for side in (0, 1):
            s, t = r.stats[side], b["team"][side]
            self.assertEqual(t["total_yards"], s["rush_yds"] + s["pass_yds"] - s["sack_yds"])
            self.assertEqual(t["turnovers"], s["ints"] + s["fumbles_lost"])
            self.assertEqual(t["first_downs"], s["first_downs"])
        text = format_box(b)
        self.assertIn("Team statistics", text)
        self.assertIn(r.home, text)

    def test_a_touchdown_run_counts_only_the_yards_to_the_goal_line(self):
        for seed in range(60):
            r, _, _ = game(seed)
            for e in r.table:
                if e["kind"] in ("run", "scramble", "complete"):
                    self.assertLessEqual(e["detail"][0], 100 - e["yl"], e)


class Recording(unittest.TestCase):
    def test_keeping_the_play_table_never_changes_a_result(self):
        for seed in range(30):
            a, _, _ = game(seed, log=True)
            b, _, _ = game(seed, log=False)
            self.assertEqual((a.score, a.stats, a.players, a.injuries), (b.score, b.stats, b.players, b.injuries), seed)

    def test_every_event_has_a_line_of_text_and_the_table_is_ordered(self):
        r, h, a = game(11)
        lines = play_by_play(r, h, a)
        self.assertEqual(len(lines), len(r.table))
        self.assertEqual(len(r.table), len(r.plays))
        self.assertTrue(all(isinstance(x, str) and len(x) > 8 for x in lines))
        self.assertEqual([e["i"] for e in r.table], list(range(len(r.table))))
        kinds = {e["kind"] for e in r.table}
        for k in ("kickoff", "run", "complete", "punt", "touchdown", "extra point"):
            self.assertIn(k, kinds)
        self.assertNotIn("?", " ".join(lines).replace("pass?", ""))          # every id resolved to a name

    def test_the_box_score_is_plain_data_and_hides_ability(self):
        r, h, a = game(5)
        b = box_score(r, h, a)
        text = (json.dumps(b, default=str) + format_box(b) + "\n".join(play_by_play(r, h, a))).lower()
        for hidden in ("attrs", "growth", "traits", "overall", "ovr", "rating"):
            self.assertNotIn(hidden, text)


if __name__ == "__main__":
    unittest.main()
