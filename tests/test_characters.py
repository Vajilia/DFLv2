"""Character creator tests: determinism, class quotas, hidden-ability guard, immutability, storage, populations."""
import dataclasses
import os
import statistics
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl.characters import Card, Creator, load_tables  # noqa: E402
from dfl.characters.creator import apportion, LEADER_ROLES  # noqa: E402
from dfl.ledger.positions import POSITIONS  # noqa: E402

T = load_tables()


class Generation(unittest.TestCase):
    def test_same_seed_same_cards_and_a_card_ignores_how_many_came_before(self):
        a, b = Creator(7, T), Creator(7, T)
        self.assertEqual([c.dumps() for c in a.draft_class(2001)[:50]], [c.dumps() for c in b.draft_class(2001)[:50]])
        c = Creator(7, T)
        c.next_id = 400
        d = Creator(7, T)
        for _ in range(399):
            d.player("QB", 22, 2001)
        self.assertEqual(c.player("QB", 22, 2001).attrs, d.player("QB", 22, 2001).attrs)       # id 400 both times
        self.assertNotEqual(Creator(8, T).player("QB", 22, 2001).attrs, Creator(7, T).player("QB", 22, 2001).attrs)

    def test_draft_class_is_672_with_at_least_12_at_every_position(self):
        cl = Creator(1, T).draft_class(2001)
        self.assertEqual(len(cl), 672)
        n = {}
        for c in cl:
            n[c.identity.pos] = n.get(c.identity.pos, 0) + 1
        self.assertEqual(set(n), set(POSITIONS))
        self.assertGreaterEqual(min(n.values()), 12)
        self.assertGreater(n["WR"], n["K"])                                  # shares follow how many each club carries

    def test_apportion_is_exact(self):
        out = apportion(672, {"a": 3, "b": 1, "c": 1}, floor=12)
        self.assertEqual(sum(out.values()), 672)
        self.assertTrue(all(v >= 12 for v in out.values()))

    def test_rookies_are_adults_and_values_are_in_range(self):
        for c in Creator(2, T).draft_class(2002):
            self.assertTrue(21 <= c.age(2002) <= 23)
            self.assertTrue(all(1 <= v <= 99 for v in c.attrs.values()))
            self.assertTrue(all(0 <= v <= 100 for v in c.traits.values()))
            self.assertTrue(140 <= c.identity.height_cm <= 200 and 45 <= c.identity.weight_kg <= 160)

    def test_attributes_correlate_through_shared_talent(self):
        cl = [c for c in Creator(3, T).draft_class(2001) if c.identity.pos == "WR"]
        xs = [c.attrs["HND"] for c in cl]
        ys = [c.attrs["RTE"] for c in cl]
        mx, my = statistics.mean(xs), statistics.mean(ys)
        cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / len(xs)
        self.assertGreater(cov / (statistics.pstdev(xs) * statistics.pstdev(ys)), 0.2)

    def test_classes_differ_in_strength_and_quality_moves_talent(self):
        C = Creator(4, T)
        qs = [C.class_quality(y) for y in range(2001, 2041)]
        self.assertGreater(max(qs) - min(qs), 0.3)
        weak, strong = Creator(4, T).draft_class(2001, quality=-0.6), Creator(4, T).draft_class(2001, quality=0.6)
        m = lambda cl: statistics.mean(c.true_overall(T) for c in cl)
        self.assertGreater(m(strong), m(weak) + 3)

    def test_positions_have_their_own_attribute_sets_and_bodies(self):
        C = Creator(5, T)
        qb, ol, k = C.player("QB", 25, 2001), C.player("LT", 25, 2001), C.player("K", 25, 2001)
        self.assertIn("THA", qb.attrs)
        self.assertNotIn("THA", ol.attrs)
        self.assertIn("PBK", ol.attrs)
        self.assertIn("KAC", k.attrs)
        bodies = [C.player("LT", 25, 2001).identity.weight_kg for _ in range(40)], [C.player("CB", 25, 2001).identity.weight_kg for _ in range(40)]
        self.assertGreater(statistics.mean(bodies[0]), statistics.mean(bodies[1]) + 10)

    def test_names_are_unique_while_listed_and_reusable_after_release(self):
        C = Creator(6, T)
        cards = [C.player("WR", 22, 2001) for _ in range(3000)]
        names = [c.identity.name for c in cards]
        self.assertEqual(len(names), len(set(names)))
        C.release_name(cards[0])
        self.assertNotIn(cards[0].identity.name, C.taken)

    def test_leaders_have_role_attributes_and_adult_ages(self):
        C = Creator(8, T)
        for role in LEADER_ROLES:
            c = C.leader(role, 2001)
            self.assertEqual(c.identity.role, role)
            self.assertTrue(33 <= c.age(2001) <= 66)
            self.assertNotIn("SPD", c.attrs)
            self.assertTrue(c.attrs)
        self.assertIn("EVL", C.leader("gm", 2001).attrs)


class CardRules(unittest.TestCase):
    def setUp(self):
        self.card = Creator(9, T).player("QB", 23, 2001)

    def test_identity_is_frozen_so_position_is_permanent(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            self.card.identity.pos = "WR"
        with self.assertRaises(dataclasses.FrozenInstanceError):
            self.card.identity.look = ()

    def test_public_view_hides_ability_growth_and_traits(self):
        pub = self.card.public(2001)
        self.assertEqual(set(pub), {"id", "name", "role", "pos", "age", "height_cm", "weight_kg", "look"})
        text = str(pub).lower()
        for hidden in ("attrs", "growth", "traits", "ovr", "overall", "thp"):
            self.assertNotIn(hidden, text)
        self.assertFalse(hasattr(self.card, "ovr"))
        self.assertNotIn("ovr", self.card.dumps())

    def test_storage_round_trips_exactly(self):
        c = self.card
        c.remember({"y": 2001, "what": "drafted"})
        c.relations[12] = {"kind": "teammate"}
        back = Card.loads(c.dumps())
        self.assertEqual(back, c)
        self.assertEqual(back.dumps(), c.dumps())

    def test_memory_holds_32_and_overflow_is_refused(self):
        for i in range(32):
            self.card.remember({"i": i})
        with self.assertRaises(OverflowError):
            self.card.remember({"i": 33})

    def test_creator_state_round_trips(self):
        C = Creator(10, T)
        C.draft_class(2001)
        s = C.state()
        D = Creator(10, T)
        D.restore(s)
        self.assertEqual(D.player("QB", 22, 2002).dumps(), C.player("QB", 22, 2002).dumps())


class Populations(unittest.TestCase):
    def test_founding_league_fills_every_club(self):
        clubs = [f"C{i:02d}" for i in range(48)]
        pop = Creator(11, T).founding(2001, clubs)
        self.assertEqual(sum(len(v["roster"]) for v in pop["clubs"].values()), 48 * 53)
        self.assertEqual(sum(len(v["ps"]) for v in pop["clubs"].values()), 48 * 16)
        self.assertTrue(all([c.identity.role for c in v["leaders"]] == list(LEADER_ROLES) for v in pop["clubs"].values()))
        pool = {}
        for c in pop["pool"]:
            pool[c.identity.pos] = pool.get(c.identity.pos, 0) + 1
        self.assertGreaterEqual(min(pool.values()), 8)
        ids = [c.identity.id for v in pop["clubs"].values() for c in v["roster"] + v["ps"] + v["leaders"]] + [c.identity.id for c in pop["pool"]]
        self.assertEqual(len(ids), len(set(ids)))
        vets = [c.age(2001) for v in pop["clubs"].values() for c in v["roster"]]
        self.assertGreater(statistics.mean(vets), 24)


if __name__ == "__main__":
    unittest.main()
