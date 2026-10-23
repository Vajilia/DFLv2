"""Parity profiles are switchable test beds and always restorable."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl import draft as D, parity  # noqa: E402
from dfl.ledger import rules as R  # noqa: E402
from dfl.ledger.world import World  # noqa: E402


class Profiles(unittest.TestCase):
    def tearDown(self):
        parity.apply("tight")

    def test_tight_is_the_shipped_rules_and_every_profile_can_be_undone(self):
        shipped = R.CAP
        parity.apply("none")
        self.assertGreater(R.CAP, 100 * shipped)
        parity.apply("tight")
        self.assertEqual(R.CAP, shipped)
        self.assertTrue(D.RETURNER_BLOCK)

    def test_none_and_rigged_switch_off_the_returner_block_and_the_cap(self):
        for name in ("none", "rigged"):
            parity.apply(name)
            self.assertFalse(D.RETURNER_BLOCK)
            ranked = [f"C{i:02d}" for i in range(40)]
            playoff = {c: "wild_card" for c in ranked[:14]}
            ret = [f"R{i}" for i in range(8)]
            self.assertEqual(D.base_order(ranked, playoff, ret, "s", ["C39", "C30"]), D.base_order(ranked, playoff, ret, "s"))
        self.assertEqual(parity.apply("rigged")["priority_clubs"], 2)

    def test_unknown_profile_is_refused(self):
        with self.assertRaises(ValueError):
            parity.apply("loose")

    def test_priority_clubs_shop_first_in_free_agency(self):
        clubs = [f"C{i:02d}" for i in range(1, 9)]
        w = World(clubs, seed=3, priority=["C08"])
        w.open_year(first=True)
        seen = []
        orig = w.fix_minimums
        w.fix_minimums = lambda cid, slot="roster": (seen.append(cid), orig(cid, slot))[1]
        w.free_agency()
        self.assertEqual(seen[0], "C08")
        self.assertEqual(sorted(set(seen)), sorted(clubs))


if __name__ == "__main__":
    unittest.main()
