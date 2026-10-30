"""Step 7, part 3: rules of section 11 and 12 that need a scenario of their own (the random driver in test_step7_oracle covers money and lists)."""
import os
import sys
import unittest
from fractions import Fraction as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import ledger_oracle as O  # noqa: E402
from dfl.ledger.contracts import Contract  # noqa: E402
from dfl.ledger.ledger import Ledger, Rejected  # noqa: E402


def league(n=3):
    L = Ledger([f"C{i}" for i in range(n)], 2001)
    L.draft_completed = L.year
    return L


def put(L, cid, pos="QB", ovr=70, seasons=1, contract=None, slot="roster"):
    p = L.new_player(pos, ovr, age=25, seasons=seasons)
    L.sign(cid, p.pid, contract or Contract(L.year, (10,), (), (), L.week), slot)
    return p


class Midseason(unittest.TestCase):
    def test_a_midseason_deal_is_one_year_and_pays_the_weeks_that_remain(self):
        L = league()
        L.phase, L.week = "season", 6
        p = L.new_player("QB", 60)
        with self.assertRaises(Rejected):
            L.sign("C0", p.pid, Contract(L.year, (10, 10), (), (), 6))
        self.assertEqual(p.where, "fa")
        L.sign("C0", p.pid, Contract(L.year, (22,), (), (), 6))
        self.assertEqual(L.players[p.pid].contract.cap_hit(L.year), F(22) * 16 / 22)
        self.assertEqual(O.cap_hit(L.players[p.pid].contract, L.year), F(16))

    def test_a_multi_year_deal_is_fine_before_the_first_game(self):
        L = league()
        L.phase, L.week = "season", 0
        p = L.new_player("QB", 60)
        L.sign("C0", p.pid, Contract(L.year, (10, 10), (), (), 0))

    def test_a_claim_or_trade_keeps_the_years_that_were_already_agreed(self):
        L = league()
        p = put(L, "C0", contract=Contract(L.year, (10, 12, 14), (), (), 0, "rising"), seasons=1)
        L.phase, L.week = "season", 5
        L.release("C0", p.pid)
        L.resolve_waivers(["C1", "C2"], wants=lambda c, q: c == "C1")
        self.assertEqual(L.players[p.pid].club, "C1")
        k = L.players[p.pid].contract
        self.assertEqual((k.start_week, k.years), (5, (10, 12, 14)))


class YearTurn(unittest.TestCase):
    def test_a_player_still_on_waivers_when_the_year_turns_becomes_a_free_agent(self):
        L = league()
        p = put(L, "C0", seasons=1, contract=Contract(L.year, (10, 10), (), (), 0))
        L.release("C0", p.pid)
        self.assertEqual(L.players[p.pid].where, "waivers")
        L.new_year()
        self.assertEqual((L.players[p.pid].where, L.players[p.pid].club, L.players[p.pid].contract), ("fa", None, None))
        self.assertEqual(L.waivers, {})
        self.assertEqual(O.structural_problems(L), [])


class Waivers(unittest.TestCase):
    def test_four_credited_seasons_skip_the_wire_three_do_not(self):
        L = league()
        vet, rookie = put(L, "C0", seasons=4), put(L, "C0", seasons=3, pos="WR")
        L.release("C0", vet.pid)
        L.release("C0", rookie.pid)
        self.assertEqual(L.players[vet.pid].where, "fa")
        self.assertEqual(L.players[rookie.pid].where, "waivers")

    def test_the_guarantee_goes_with_the_claimant_and_the_bonus_stays(self):
        L = league()
        k = Contract(L.year, (20, 20), (20, 10), ((30, 0, 2),), 0)
        p = put(L, "C0", seasons=1, contract=k)
        L.release("C0", p.pid)
        dead_before = dict(L.clubs["C0"].dead)
        L.resolve_waivers(["C1", "C0"], wants=lambda c, q: c == "C1")
        taken = L.players[p.pid].contract
        self.assertEqual((taken.bonuses, taken.years, taken.guaranteed), ((), (F(20), F(20)), (F(20), F(10))))
        self.assertEqual(L.clubs["C0"].dead.get(L.year, 0), dead_before.get(L.year, 0) - F(20))      # relief = her guaranteed base; the bonus stays dead
        self.assertEqual(O.money_problems(L) + O.structural_problems(L), [])


class InjuredReserve(unittest.TestCase):
    def test_injured_reserve_gives_no_cap_relief(self):
        L = league()
        p = put(L, "C0", contract=Contract(L.year, (30,), (), (), 0))
        before = L.cap_number("C0")
        L.players[p.pid].out_weeks = 6
        L.place_on_ir("C0", p.pid)
        self.assertEqual(L.cap_number("C0"), before)
        self.assertEqual(L.cap_number("C0"), O.cap_number(L, "C0"))


class SplitReleaseL2(unittest.TestCase):
    """Owner ruling L2 (Edition 8.55): the release year's own bonus portion stays in that year; only the FUTURE portions are split 50/50 between
    that year and the next. The independent oracle states the same reading (tests/ledger_oracle.py, READING A)."""

    def test_this_years_portion_stays_and_only_the_future_portions_divide(self):
        c = Contract(2000, (20,) * 4, (), ((40, 0, 4),))                      # a $40 bonus: $10 charged each year
        self.assertEqual(c.release_charges(2000, 0, split=True), {2000: 10 + 15, 2001: 15})      # $10 stays; the later $30 divides 15 / 15
        # released at the start of 2001: this year's $10 stays, the later $20 (2002 and 2003) divides 10 / 10 between 2001 and 2002
        self.assertEqual(c.release_charges(2001, 0, split=True), {2001: 10 + 10, 2002: 10})
        self.assertEqual(c.release_charges(2001, 0), {2001: 30})                          # without the split all of it lands in 2001

    def test_the_alternative_reading_would_have_split_the_whole_uncharged_bonus(self):
        c = Contract(2000, (20,) * 4, (), ((40, 0, 4),))
        whole = F(40) / 2                                                             # the rejected reading: half of everything uncharged, this year's portion included
        got = c.release_charges(2000, 0, split=True)
        self.assertNotEqual(got[2000] - 10, whole)                                    # the built (approved) reading charges the release year $5 more
        self.assertEqual(got[2000] - 10, F(15))
        self.assertEqual(sum(got.values()), sum(c.release_charges(2000, 0).values()))     # the same money in total, only its timing differs

    def test_the_oracle_agrees_on_a_grid_of_contracts_and_weeks(self):
        for years in ((20,) * 4, (15, 20, 25), (30,) * 2):
            for bonus in (0, 10, 40):
                n = len(years)
                c = Contract(2000, years, (), ((bonus, 0, n),) if bonus else (), 0, "steady" if len(set(years)) == 1 else "rising")
                for y in range(2000, 2000 + n):
                    for week in (0, 5, 11, 22):
                        self.assertEqual(c.release_charges(y, week, split=True), O.dead_if_released(c, y, week, True, True), (years, bonus, y, week))
                        if y == 2000 + n - 1:                                         # no later portion: split changes nothing
                            self.assertEqual(c.release_charges(y, week, split=True), c.release_charges(y, week, split=False))


if __name__ == "__main__":
    unittest.main()
