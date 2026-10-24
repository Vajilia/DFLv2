"""The final cut repairs a numbering shortfall instead of stopping the league (found in a long run, Edition 8.29)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from dfl.ledger.ledger import Rejected  # noqa: E402
from dfl.ledger.world import World  # noqa: E402

CLUBS = [f"C{i:02d}" for i in range(1, 9)]


class CampRepair(unittest.TestCase):
    def setUp(self):
        self.w = World(CLUBS, seed=61)
        w = self.w
        w.open_year(first=True)
        w.free_agency_open()
        w.start_draft()
        w.draft_rounds(range(1, 8))
        w.finish_draft()
        w.camp_open()

    def test_a_late_jersey_shortfall_is_repaired_by_shedding_a_surplus_player(self):
        w, L = self.w, self.w.L
        cid = "C01"
        real_cut, real_plan = L.cut_down, L.plan_numbers
        state = {"raised": False}

        def cut(c):
            if c == cid and not state["raised"]:
                state["raised"] = True
                raise Rejected(f"{c}: no legal jersey number for ['DT']")
            return real_cut(c)

        def plan(c):
            new, bad = real_plan(c)
            if c == cid and state["raised"] and not state.get("planned"):
                state["planned"] = True
                dt = next(i for i in L.clubs[c].roster + L.clubs[c].ps if L.players[i].pos == "DT")
                return new, [dt]
            return new, bad
        L.cut_down, L.plan_numbers = cut, plan
        try:
            w.camp(cid)
        finally:
            L.cut_down, L.plan_numbers = real_cut, real_plan
        self.assertEqual(w.stats["numbering_repairs"], 1)
        self.assertLessEqual(len(L.clubs[cid].roster), 53)

    def test_other_cut_down_failures_still_stop_the_league(self):
        w, L = self.w, self.w.L
        real = L.cut_down

        def cut(c):
            raise Rejected(f"{c}: cap not cleared")
        L.cut_down = cut
        try:
            with self.assertRaises(Rejected):
                w.camp("C02")
        finally:
            L.cut_down = real


if __name__ == "__main__":
    unittest.main()
