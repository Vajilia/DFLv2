"""Fifth-year option (Edition 8.55, owner ruling L3): a first-round contract is four years carrying the club's option on a fifth at $35. Whether to
exercise it is the club's decision, taken by its general manager before the draft of the player's fourth contract year opens; exercising adds a
fully guaranteed year (a real commitment the viability invariant checks); declining or missing the deadline lets the contract end; every decision is
saved. Expected change from Edition 8.54, where every first-round contract simply had five years."""
import os
import sys
import unittest
from fractions import Fraction as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl.ledger import rules as R  # noqa: E402
from dfl.ledger.contracts import Contract, ContractError, rookie_contract  # noqa: E402
from dfl.ledger.ledger import Ledger as _Ledger, Rejected  # noqa: E402

TABLES = os.path.join(ROOT, "data", "season1")


def ledger_in_fourth_year(cids=("A", "B")):
    """A ledger at the 2003 offseason holding one 2000 first-round pick (player A1) on club A, contract still with its option."""
    L = _Ledger(list(cids), 2000)
    L.draft_completed = 2000
    L.clubs["A"].picks.discard((2000, 1, "A"))
    p = L.new_player("WR", 70, seasons=0)
    L.sign("A", p.pid, rookie_contract(2000, 1, first_round_option=True))
    for _ in range(3):
        L.draft_completed = L.year
        L.new_year()
    assert L.year == 2003 and p.where == "roster"
    return L, p


class Contracts(unittest.TestCase):
    def test_first_round_is_four_years_with_a_pending_option_and_nobody_else_has_one(self):
        k = rookie_contract(2000, 1, first_round_option=True)
        self.assertEqual((k.years, k.end_year, k.option), ((F(25),) * 4, 2003, F(35)))
        self.assertEqual((k.cap_hit(2004), k.release_charges(2003, 0)), (0, {}))      # a pending option is not a covered year: no cap hit, no commitment
        for rnd in range(2, 8):
            self.assertIsNone(rookie_contract(2000, rnd, first_round_option=True).option)
        self.assertIsNone(rookie_contract(2000, 1).option)

    def test_the_option_follows_a_player_through_a_trade_or_claim_and_a_restructure(self):
        k = rookie_contract(2000, 1, first_round_option=True)
        self.assertEqual(k.assumed_by_receiver(2002, 5).option, F(35))
        self.assertEqual(k.restructured(2000, 3, F(10)).option, F(35))

    def test_exercising_adds_one_guaranteed_year_and_declining_just_drops_the_option(self):
        k = rookie_contract(2000, 1, first_round_option=True)
        x = k.with_option_exercised()
        self.assertEqual((x.years, x.guaranteed, x.end_year, x.option, x.shape), ((F(25),) * 4 + (F(35),), (0,) * 4 + (F(35),), 2004, None, "rising"))
        self.assertEqual((x.cap_hit(2004), x.release_charges(2004, 0)), (35, {2004: 35}))      # released at the start of 2004 it still costs $35
        self.assertEqual(k.with_option_exercised(guaranteed=False).guaranteed, (0,) * 5)
        d = k.without_option()
        self.assertEqual((d.years, d.end_year, d.option), (k.years, 2003, None))
        with self.assertRaises(ContractError):
            x.with_option_exercised()                                                          # only once
        with self.assertRaises(ContractError):
            Contract(2000, (25,) * 5, option=35)                                               # no room for a sixth covered year

    def test_the_constants_are_the_rulings(self):
        self.assertEqual((R.ROOKIE_YEARS, R.R1_OPTION, R.R1_OPTION_GUARANTEED), (4, F(35), True))


class LedgerDecision(unittest.TestCase):
    def test_the_option_is_due_in_the_fourth_contract_year_only(self):
        L, p = ledger_in_fourth_year()
        self.assertEqual(L.options_due(), [("A", p.pid)])
        self.assertEqual(L.options_due("B"), [])
        L2 = _Ledger(["A"], 2000)
        L2.draft_completed = 2000
        q = L2.new_player("WR", 70)
        L2.clubs["A"].picks.discard((2000, 1, "A"))
        L2.sign("A", q.pid, rookie_contract(2000, 1, first_round_option=True))
        self.assertEqual(L2.options_due(), [])                                               # year one of four

    def test_exercise_is_recorded_adds_the_year_and_cannot_be_repeated_or_made_by_another_club(self):
        L, p = ledger_in_fourth_year()
        with self.assertRaises(Rejected):
            L.exercise_option("B", p.pid)
        rec = L.exercise_option("A", p.pid, "test")
        self.assertEqual(p.contract.end_year, 2004)
        self.assertEqual((rec["decision"], rec["covers"], rec["guaranteed"], rec["club"], rec["year"]), ("exercised", 2004, "35", "A", 2003))
        self.assertEqual(L.option_log, [rec])
        self.assertEqual(L.cap_number("A", 2004), 35)
        self.assertEqual(L.committed("A")[2004], 35)                                         # counts toward the viability invariant
        self.assertEqual(L.options_due(), [])
        with self.assertRaises(Rejected):
            L.exercise_option("A", p.pid)
        with self.assertRaises(Rejected):
            L.decline_option("A", p.pid)
        L.draft_completed = 2003
        L.new_year()
        self.assertEqual((L.year, p.where, p.contract.end_year), (2004, "roster", 2004))     # still with the club for the fifth year
        L.new_year()
        self.assertEqual(p.where, "fa")                                                      # and free after it

    def test_decline_lets_the_contract_end_a_year_earlier(self):
        L, p = ledger_in_fourth_year()
        rec = L.decline_option("A", p.pid)
        self.assertEqual((rec["decision"], rec["covers"], rec["guaranteed"]), ("declined", None, "0"))
        self.assertIsNone(p.contract.option)
        L.draft_completed = 2003
        L.new_year()
        self.assertEqual((p.where, p.club), ("fa", None))

    def test_the_deadline_closes_the_window_and_an_unmade_decision_lapses_with_a_record(self):
        L, p = ledger_in_fourth_year()
        L.close_options()
        with self.assertRaises(Rejected):
            L.exercise_option("A", p.pid)
        self.assertEqual(L.options_due(), [])
        L.draft_completed = 2003
        L.new_year()
        self.assertEqual(p.where, "fa")
        self.assertEqual([(d["decision"], d["club"], d["year"], d["pid"]) for d in L.option_log], [("lapsed", "A", 2003, p.pid)])

    def test_a_club_that_could_not_carry_the_guaranteed_year_is_refused_and_nothing_changes(self):
        L, p = ledger_in_fourth_year()
        big = L.new_player("QB", 80, seasons=3)
        L.sign("A", big.pid, Contract(2003, (705, 705), (705, 705), (), 0, "steady"))        # $705 owed in 2004 whatever happens: 735 would be the limit with $35 more = 740
        before = (p.contract, L.committed("A")[2004], list(L.option_log))
        with self.assertRaises(Rejected):
            L.exercise_option("A", p.pid)
        self.assertEqual((p.contract, L.committed("A")[2004], L.option_log), before)
        self.assertEqual(L.options_due(), [("A", p.pid)])                                    # still undecided: the club may decline

    def test_a_restructure_that_leaves_the_years_out_of_order_cannot_take_the_option_year(self):
        L, p = ledger_in_fourth_year()
        L.restructure("A", p.pid, F(10))                                                    # the last year's base falls below the earlier ones
        self.assertEqual(p.contract.option, F(35))
        with self.assertRaises(Rejected):
            L.exercise_option("A", p.pid)
        L.decline_option("A", p.pid)


class GmDecider(unittest.TestCase):
    """The general manager's rule, run on three years of the real league loop with the placeholder resolver (the 2000 draft class reaches its fourth
    contract year at the 2003 draft)."""

    @classmethod
    def setUpClass(cls):
        from dfl.year import Universe
        cls.u = Universe(TABLES, seed=3, engine=False, spatial=False)
        while not (cls.u.fired and cls.u.fired[-1][0] == 2003 and cls.u.fired[-1][2] == "draft_round_1"):
            cls.u.step_event()
        cls.L, cls.W = cls.u.world.L, cls.u.world

    def test_every_option_due_was_decided_before_the_first_pick_with_a_saved_record(self):
        log = self.L.option_log
        self.assertGreater(len(log), 30)                                                    # about forty first-round picks of 2000 are still with clubs
        self.assertEqual({d["year"] for d in log}, {2003})
        self.assertEqual({d["decision"] for d in log}, {"exercised", "declined"})
        self.assertEqual(self.L.options_due(), [])
        self.assertEqual(self.L.options_closed, 2003)
        self.assertEqual(len({(d["club"], d["pid"]) for d in log}), len(log))               # once each
        for d in log:
            self.assertEqual(d["option_salary"], "35")

    def test_the_decision_follows_the_clubs_own_price_of_the_player(self):
        for d in self.L.option_log:
            worth = self.W.quote(d["club"], self.L.players[d["pid"]]) if d["pid"] in self.L.players else None
            if worth is None or d["pid"] not in self.L.players:
                continue
            if d["decision"] == "exercised":
                self.assertGreaterEqual(worth, R.R1_OPTION, d)
            elif "below the option salary" in d["note"]:
                self.assertLess(worth, R.R1_OPTION, d)

    def test_exercised_players_carry_a_fifth_guaranteed_year_and_declined_players_do_not(self):
        for d in self.L.option_log:
            p = self.L.players.get(d["pid"])
            if p is None or p.contract is None:
                continue
            if d["decision"] == "exercised":
                self.assertEqual((p.contract.end_year, p.contract.guaranteed[-1], p.contract.option), (2004, 35, None), d)
            else:
                self.assertEqual((p.contract.end_year, p.contract.option), (2003, None), d)

    def test_it_is_deterministic(self):
        from dfl.year import Universe
        v = Universe(TABLES, seed=3, engine=False, spatial=False)
        while not (v.fired and v.fired[-1][0] == 2003 and v.fired[-1][2] == "draft_round_1"):
            v.step_event()
        self.assertEqual(v.world.L.option_log, self.L.option_log)


if __name__ == "__main__":
    unittest.main()
