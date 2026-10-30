import os
import random
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl import draft as D  # noqa: E402


def clubs(n, prefix="C"):
    return [f"{prefix}{i:02d}" for i in range(n)]


class Order(unittest.TestCase):
    def setup(self):
        ranked = clubs(40)                                   # best first
        playoff = {}
        for i, c in enumerate(ranked[:14]):
            playoff[c] = ("champion" if i == 0 else "runner_up" if i == 1 else "conference" if i < 4 else
                          "divisional" if i < 8 else "wild_card")
        return ranked, playoff, clubs(8, "R")

    def test_shape_and_group_order(self):
        ranked, playoff, ret = self.setup()
        o = D.base_order(ranked, playoff, ret, "s")
        self.assertEqual(len(o), 48)
        self.assertEqual(set(o[:8]), set(ret))                         # returning ambassadors first
        self.assertEqual(o[8:34], list(reversed(ranked[14:])))        # 26 non-playoff clubs, worst record first
        self.assertEqual(o[-1], "C00")                                # champion last
        self.assertEqual(o[-2], "C01")                                # runner-up before
        self.assertEqual(o[34:40], list(reversed(ranked[8:14])))      # wild card losers, worse first
        self.assertEqual(o[40:44], list(reversed(ranked[4:8])))       # divisional losers

    def test_clubs_in_their_first_season_back_pick_at_the_end_of_the_non_playoff_block(self):
        ranked, playoff, ret = self.setup()
        plain = D.base_order(ranked, playoff, ret, "s")
        returned = ["C39", "C30", "C20", "C10"]            # C10 made the playoffs; C39 is the worst record, then C30, C20
        o = D.base_order(ranked, playoff, ret, "s", returned)
        self.assertEqual(len(o), 48)
        self.assertEqual(o[:8], plain[:8])                  # the lottery is untouched
        block = o[8:34]
        self.assertEqual(block[-3:], ["C39", "C30", "C20"])  # the block closes the non-playoff clubs, worst record first
        self.assertEqual(block[:-3], [c for c in reversed(ranked[14:]) if c not in ("C39", "C30", "C20")])
        self.assertEqual(o[34:], plain[34:])                # a returned club that made the playoffs is placed by her exit as usual

    def test_the_returner_block_never_moves_a_club_ahead_of_her_record(self):
        ranked, playoff, ret = self.setup()
        plain = D.base_order(ranked, playoff, ret, "s")
        for r in (["C39"], ["C25", "C26"], ["C14", "C39", "C20"]):
            o = D.base_order(ranked, playoff, ret, "s", r)
            for c in r:
                self.assertGreaterEqual(o.index(c), plain.index(c))

    def test_lottery_is_seeded_and_equal_chance(self):
        ret = clubs(8, "R")
        self.assertEqual(D.lottery(ret, "x"), D.lottery(list(reversed(ret)), "x"))     # input order irrelevant
        first = {}
        for s in range(4000):
            first[D.lottery(ret, f"s{s}")[0]] = first.get(D.lottery(ret, f"s{s}")[0], 0) + 1
        self.assertEqual(len(first), 8)
        for v in first.values():
            self.assertTrue(400 < v < 600, first)                       # about 500 each of 4000

    def test_elimination_rounds_from_a_playoff_log(self):
        log = []
        for conf, base in (("W", "w"), ("E", "e")):
            log += [(conf, f"{base}6", f"{base}3", 10, 20), (conf, f"{base}5", f"{base}4", 10, 20),            # wild card: 3 hosts 6, 4 hosts 5
                    (conf, f"{base}4", f"{base}1", 10, 20), (conf, f"{base}3", f"{base}2", 10, 20),            # divisional: 1 v 4, 2 v 3 (reseeded)
                    (conf, f"{base}2", f"{base}1", 10, 20)]                                                    # conference championship
        e = D.elimination_rounds(log, ("e1", "w1", 7, 14))
        self.assertEqual((e["w6"], e["w5"]), ("wild_card", "wild_card"))
        self.assertEqual((e["w4"], e["w3"]), ("divisional", "divisional"))
        self.assertEqual(e["w2"], "conference")
        self.assertEqual((e["e1"], e["w1"]), ("runner_up", "champion"))
        self.assertEqual(len(e), 12)                                    # six clubs a conference make the playoffs

    def test_slots_cover_seven_rounds(self):
        o = clubs(48)
        s = D.ordinary_slots(2001, o)
        self.assertEqual(len(s), 336)
        self.assertEqual(s[48], (2, 1, "C00"))


class Compensation(unittest.TestCase):
    def mv(self, pid, frm, to, base, top=True):
        return {"pid": pid, "from": frm, "to": to, "base": base, "snap_top_quarter": top}

    def test_net_loss_earns_a_pick_but_churn_does_not(self):
        order = clubs(4, "X")
        moves = [self.mv(1, "X00", "X01", 100), self.mv(2, "X01", "X00", 90),     # X00 lost one, signed one: nothing
                 self.mv(3, "X02", "X03", 120)]                                    # X02 lost one for nothing: a pick
        self.assertEqual(D.compensatory(moves, order), [("X02", 120, 3)])

    def test_threshold_snaps_and_caps(self):
        order = clubs(60, "X")
        self.assertEqual(D.compensatory([self.mv(1, "X00", "X01", 74)], order), [])                 # below the $75 base
        self.assertEqual(D.compensatory([self.mv(1, "X00", "X01", 100, top=False)], order), [])    # not a top-quarter player
        moves = [self.mv(i, "X00", "X01", 100 + i) for i in range(5)]
        got = D.compensatory(moves, order)
        self.assertEqual(len(got), 2)                                       # at most two per club
        self.assertEqual([g[1] for g in got], [104, 103])                   # best value first
        many = [self.mv(i, f"X{i % 60:02d}", "X59", 100 + i) for i in range(60)]
        self.assertLessEqual(len(D.compensatory(many, order)), 48)          # 48 league-wide

    def test_ties_go_to_the_club_that_picks_earlier(self):
        order = ["B", "A"]
        moves = [self.mv(1, "A", "Z", 100), self.mv(2, "B", "Z", 100)]
        self.assertEqual([c for c, *_ in D.compensatory(moves, order)], ["B", "A"])

    def test_full_slots_keys_are_unique_per_club(self):
        order = clubs(48)
        comps = [("C00", 100, 1), ("C00", 90, 2), ("C01", 80, 3)]
        slots = D.full_slots(2001, order, comps)
        keys = [(r, c) for r, _, c in slots]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(slots), 336 + 3)


if __name__ == "__main__":
    unittest.main()
