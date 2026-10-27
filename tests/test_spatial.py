"""The spatial play engine: determinism, trace fidelity, outcome shapes, integration with the game and the box score (Edition 8.48)."""
import os
import random
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import test_engine as TE  # noqa: E402
from dfl.engine.boxscore import box_score  # noqa: E402
from dfl.engine.game import play_game  # noqa: E402
from dfl.engine.spatial import trace as TR  # noqa: E402
from dfl.engine.spatial.core import PASSES, RUNS  # noqa: E402
from dfl.engine.spatial.sim import PlaySim  # noqa: E402

KINDS = {"run", "sack", "scramble", "complete", "incomplete", "int"}


def sim(kind, play, cov, seed, trace=False, los=35, down=1, dist=10):
    h, a = TE.squad("C00"), TE.squad("C01")
    return PlaySim(h.units(), a.units(), kind, play, cov, los, 26.67, random.Random(seed), trace=trace, down=down, dist=dist).run()


class Plays(unittest.TestCase):
    def test_same_seed_same_play(self):
        for kind, plays in (("run", RUNS), ("pass", PASSES)):
            for pl in list(plays)[:4]:
                a, b = sim(kind, pl, "cover3", 7), sim(kind, pl, "cover3", 7)
                for k in ("kind", "yards", "secs", "carrier", "target", "tackler"):
                    self.assertEqual(a.get(k), b.get(k), (pl, k))

    def test_trace_never_changes_the_result(self):
        for seed in range(12):
            for kind, pl in (("run", "inside_zone"), ("pass", "quick_slants"), ("pass", "four_verts")):
                a, b = sim(kind, pl, "cover2", seed), sim(kind, pl, "cover2", seed, trace=True)
                self.assertEqual((a["kind"], a["yards"], a["secs"]), (b["kind"], b["yards"], b["secs"]), (seed, pl))

    def test_outcome_shapes(self):
        for seed in range(30):
            for kind, plays in (("run", RUNS), ("pass", PASSES)):
                pl = random.Random(seed).choice(sorted(plays))
                o = sim(kind, pl, random.Random(seed + 1).choice(["cover1", "cover2", "cover3", "cover4", "cover0"]), seed)
                self.assertIn(o["kind"], KINDS)
                self.assertTrue(-25 <= o["yards"] <= 100, o["yards"])
                self.assertTrue(0.5 < o["secs"] < 15, o["secs"])
                if o["kind"] in ("complete", "int"):
                    self.assertIsNotNone(o.get("target"))
                if o["kind"] in ("incomplete", "sack", "scramble", "run"):
                    self.assertNotEqual(o["kind"], "int")

    def test_a_play_takes_about_a_hundredth_of_a_second_per_step_of_cpu(self):
        t = time.time()
        for seed in range(10):
            sim("pass", "curl_flat", "cover3", seed)
        self.assertLess((time.time() - t) / 10, 0.5)


class Trace(unittest.TestCase):
    def test_round_trip(self):
        o = sim("pass", "quick_slants", "cover3", 5, trace=True)
        d = TR.decode(o["trace"])
        n = d["pos"].shape[0]
        self.assertEqual(d["pos"].shape, (n, 22, 2))
        self.assertEqual(d["ball"].shape, (n, 3))
        self.assertAlmostEqual(d["t"][1] - d["t"][0], 0.05)
        self.assertTrue((d["pos"][:, :, 1] > -5).all() and (d["pos"][:, :, 1] < 60).all())
        j = TR.to_json(o["trace"])
        self.assertEqual(len(j["frames"][0]["p"]), 22)
        self.assertEqual(len(j["roles"]), 22)

    def test_players_do_not_teleport(self):
        o = sim("run", "outside_zone", "cover3", 3, trace=True)
        d = TR.decode(o["trace"])
        step = ((d["pos"][1:] - d["pos"][:-1]) ** 2).sum(axis=2) ** 0.5
        self.assertLess(step.max(), 0.05 * 15.0)                  # nobody moves faster than 15 yd/s


class InGames(unittest.TestCase):
    def run_game(self, seed, trace=False):
        h, a = TE.squad("C00"), TE.squad("C01")
        return play_game(h, a, random.Random(seed), False, True, spatial=True, trace=trace), h, a

    def test_a_spatial_game_is_deterministic(self):
        a, _, _ = self.run_game(3)
        b, _, _ = self.run_game(3)
        self.assertEqual((a.score, a.stats), (b.score, b.stats))

    def test_trace_does_not_change_a_game(self):
        a, _, _ = self.run_game(4)
        b, _, _ = self.run_game(4, trace=True)
        self.assertEqual((a.score, a.stats), (b.score, b.stats))
        self.assertTrue(any("trace" in r for r in b.table))
        self.assertFalse(any("trace" in r for r in a.table))

    def test_box_score_is_consistent(self):
        for seed in range(6):
            r, h, a = self.run_game(seed)
            b = box_score(r, h, a)
            self.assertEqual([sum(b["line"][0]), sum(b["line"][1])], list(r.score), seed)
            for side, sq in enumerate((h, a)):
                ids = set(sq.cards)
                pl = {i: v for i, v in r.players.items() if i in ids}
                self.assertEqual(sum(v.get("rush_yds", 0) for v in pl.values()), r.stats[side]["rush_yds"], (seed, side))
                self.assertEqual(sum(v.get("pass_yds", 0) for v in pl.values()), r.stats[side]["pass_yds"], (seed, side))

    def test_snaps_carry_the_call(self):
        r, _, _ = self.run_game(2)
        called = [x for x in r.table if x.get("call")]
        self.assertGreater(len(called), 60)


if __name__ == "__main__":
    unittest.main()
