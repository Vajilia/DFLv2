"""Ledger tests: contracts, cap arithmetic, viability gate, rosters, IR, elevations, game day, waivers, trades, cut-down."""
import os
import sys
import unittest
from fractions import Fraction as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl.ledger import gameday, rules as R  # noqa: E402
from dfl.ledger.contracts import Contract, ContractError, rookie_contract  # noqa: E402
from dfl.ledger.ledger import Ledger, Rejected  # noqa: E402
from dfl.ledger.positions import MINIMUMS, shortfalls  # noqa: E402

MIN = Contract(2000, (5,))


def fill(L, cid, spec=None, ovr=60, contract=None, slot="roster"):
    """Sign one player per position slot in `spec` ({pos: n}); default the 48 required places (3 HB)."""
    spec = spec or dict(MINIMUMS, HB=3)
    out = []
    for pos, n in spec.items():
        for _ in range(n):
            p = L.new_player(pos, ovr, seasons=1)
            L.sign(cid, p.pid, contract or Contract(L.year, (5,), (), (), L.week), slot)
            out.append(p)
    return out


class ContractMath(unittest.TestCase):
    def test_rules_of_a_contract(self):
        with self.assertRaises(ContractError):
            Contract(2000, (4,))                                  # below the minimum
        with self.assertRaises(ContractError):
            Contract(2000, (5,) * 6)                              # more than five years
        with self.assertRaises(ContractError):
            Contract(2000, (10, 8), shape="rising")               # not rising
        with self.assertRaises(ContractError):
            Contract(2000, (10, 10), bonuses=((10, 0, 3),))       # proration longer than the contract
        Contract(2000, (10, 12, 14), shape="rising")
        Contract(2000, (14, 12, 10), shape="declining")

    def test_cap_hit_is_base_plus_prorated_bonus(self):
        c = Contract(2000, (20, 20, 20, 20, 20), (), ((50, 0, 5),))
        self.assertEqual([c.cap_hit(2000 + i) for i in range(5)], [30] * 5)
        c = Contract(2000, (20, 20), (), ((30, 0, 2),), start_week=11)       # signed mid-season: half the first-year base
        self.assertEqual(c.cap_hit(2000), 10 + 15)
        self.assertEqual(c.cap_hit(2001), 20 + 15)

    def test_release_accelerates_every_uncharged_bonus_onto_this_year(self):
        c = Contract(2000, (20,) * 4, (10, 10, 0, 0), ((40, 0, 4),))
        self.assertEqual(c.release_charges(2001, 11), {2001: 10 + 30})       # earned base 10 (= the guarantee) + bonus years 2-4
        self.assertEqual(c.release_charges(2000, 0), {2000: 10 + 40, 2001: 10})   # guarantee stays in its own year

    def test_split_release_is_even_across_two_years(self):
        c = Contract(2000, (20,) * 4, (), ((40, 0, 4),))
        self.assertEqual(c.release_charges(2000, 0, split=True), {2000: 10 + 15, 2001: 15})
        self.assertEqual(sum(c.release_charges(2000, 0, split=True).values()), sum(c.release_charges(2000, 0).values()))

    def test_receiver_assumes_unpaid_base_and_guarantees_never_the_bonus(self):
        c = Contract(2000, (20, 20, 20), (10, 10, 0), ((30, 0, 3),))
        r = c.assumed_by_receiver(2001, 11)
        self.assertEqual(r.bonuses, ())
        self.assertEqual(r.years, (20, 20))
        self.assertEqual(r.guaranteed, (10, 0))
        self.assertEqual(r.cap_hit(2001), 10)                     # half of the year's base is left

    def test_restructure_converts_base_to_bonus(self):
        c = Contract(2000, (30, 30, 30))
        r = c.restructured(2000, 0, 15)
        self.assertEqual((r.years[0], r.bonuses), (15, ((15, 0, 3),)))
        self.assertEqual(r.cap_hit(2000), 15 + 5)
        with self.assertRaises(ContractError):
            c.restructured(2000, 0, 30)                           # would leave less than the minimum

    def test_rookie_scale(self):
        self.assertEqual(rookie_contract(2000, 1).years, (25,) * 4)
        self.assertEqual(rookie_contract(2000, 7).years, (5,) * 4)
        self.assertEqual(rookie_contract(2000, 1, True).years[-1], 35)


class Rosters(unittest.TestCase):
    def test_preseason_ceiling_then_53_and_16(self):
        L = Ledger(["A"])
        for _ in range(R.PRESEASON_CEILING):
            p = L.new_player("WR", 60)
            L.sign("A", p.pid, MIN, "roster")
        p = L.new_player("WR", 60)
        with self.assertRaises(Rejected):
            L.sign("A", p.pid, MIN, "roster")                    # 76th
        L.start_camp(); L.begin_season()
        self.assertEqual(len(L.clubs["A"].roster), 75)
        q = L.new_player("WR", 60)
        with self.assertRaises(Rejected):
            L.sign("A", q.pid, MIN, "roster")                    # in season the roster is capped at 53

    def test_practice_squad_size_and_veterans(self):
        L = Ledger(["A"])
        L.start_camp(); L.begin_season()
        for i in range(R.PS_MAX):
            p = L.new_player("WR", 60, seasons=5 if i < R.PS_VETERANS_MAX else 0)
            L.sign("A", p.pid, MIN, "ps")
        with self.assertRaises(Rejected):
            L.sign("A", L.new_player("WR", 60).pid, MIN, "ps")   # 17th
        L2 = Ledger(["A"])
        L2.start_camp(); L2.begin_season()
        for _ in range(R.PS_VETERANS_MAX):
            L2.sign("A", L2.new_player("WR", 60, seasons=5).pid, MIN, "ps")
        with self.assertRaises(Rejected):
            L2.sign("A", L2.new_player("WR", 60, seasons=5).pid, MIN, "ps")   # 7th veteran
        L2.sign("A", L2.new_player("WR", 60, seasons=1).pid, MIN, "ps")      # a second-year player is fine

    def test_a_signing_must_start_now_and_a_player_signs_once(self):
        L = Ledger(["A", "B"])
        p = L.new_player("QB", 60)
        with self.assertRaises(Rejected):
            L.sign("A", p.pid, Contract(1999, (5,)))
        L.sign("A", p.pid, MIN)
        with self.assertRaises(Rejected):
            L.sign("B", p.pid, MIN)                               # already under contract

    def test_ir_four_game_minimum_and_eight_returns(self):
        L = Ledger(["A"])
        ps = fill(L, "A", {"WR": 12})
        L.start_camp(); L.begin_season()
        p = ps[0]
        p.out_weeks = 3
        with self.assertRaises(Rejected):
            L.place_on_ir("A", p.pid)
        p.out_weeks = 4
        L.place_on_ir("A", p.pid)
        p.out_weeks = 0
        with self.assertRaises(Rejected):
            L.return_from_ir("A", p.pid)                          # not four games yet
        for _ in range(4):
            L.advance_week()
        L.return_from_ir("A", p.pid)
        self.assertEqual(L.clubs["A"].ir_returns_used, 1)
        L.clubs["A"].ir_returns_used = R.IR_RETURNS_MAX
        q = ps[1]
        q.out_weeks = 4
        L.place_on_ir("A", q.pid)
        q.out_weeks = 0
        for _ in range(4):
            L.advance_week()
        with self.assertRaises(Rejected):
            L.return_from_ir("A", q.pid)                          # ninth return refused


class Viability(unittest.TestCase):
    def heavy(self, L, cid, n, bonus=100):
        """n players each carrying a five-year deal with a big bonus (accelerates onto the cap if released)."""
        out = []
        for i in range(n):
            p = L.new_player("WR", 80)
            L.sign(cid, p.pid, Contract(L.year, (10,) * 5, (), ((bonus, 0, 5),), L.week))
            out.append(p)
        return out

    def test_gate_rejects_commitments_beyond_735_and_rolls_back_whole(self):
        L = Ledger(["A"])
        self.heavy(L, "A", 7)                                     # 7 x 100 = 700 owed if all were cut
        p = L.new_player("WR", 80)
        before = L.digest()
        with self.assertRaises(Rejected):
            L.sign("A", p.pid, Contract(L.year, (10,) * 5, (), ((100, 0, 5),), L.week))   # 800 > 735
        self.assertEqual(L.digest(), before)                      # nothing half-applied
        self.assertEqual(p.where, "fa")
        L.sign("A", p.pid, Contract(L.year, (10,) * 5, (), ((30, 0, 5),), L.week))        # 730 is fine

    def test_future_year_acceleration_is_gated_too(self):
        L = Ledger(["A"])
        # 8 players x 100 bonus prorated over 5 years: if all were released at the start of year 3 the bonus left
        # (3/5) still lands in that year; the committed figure for every year is checked
        with self.assertRaises(Rejected):
            self.heavy(L, "A", 8)

    def test_release_never_raises_what_is_committed(self):
        L = Ledger(["A"])
        ps = self.heavy(L, "A", 6)
        before = L.committed("A")
        for p in ps[:3]:
            L.release("A", p.pid)
        after = L.committed("A")
        self.assertTrue(all(after.get(y, 0) <= v for y, v in before.items()))
        self.assertEqual(max(after.values()), max(before.values()))     # acceleration was already counted

    def test_margin_is_cap_minus_commitments_minus_minimum_roster_minus_reserve(self):
        L = Ledger(["A"])
        self.assertEqual(L.viability_margin("A"), 735)
        self.heavy(L, "A", 2)
        self.assertEqual(L.viability_margin("A"), 735 - 200)

    def test_no_banking(self):
        L = Ledger(["A", "B"])
        fill(L, "B", contract=Contract(2000, (15,) * 2))          # B spends, A does not
        L.new_year()
        self.assertEqual(L.cap_number("A"), 0)
        self.assertFalse(hasattr(L.clubs["A"], "bank"))
        self.assertEqual(L.limit(L.year), R.CAP - R.RESERVE - 240)   # same limit for every club, every year
        self.assertEqual(L.clubs["A"].dead, {})


class Money(unittest.TestCase):
    def test_trade_moves_unpaid_base_not_the_bonus(self):
        L = Ledger(["A", "B"])
        a = L.new_player("QB", 80)
        L.sign("A", a.pid, Contract(2000, (20,) * 3, (), ((30, 0, 3),)))
        b = L.new_player("QB", 70)
        L.sign("B", b.pid, MIN)
        L.trade("A", [a.pid], "B", [b.pid])
        self.assertEqual(L.clubs["A"].dead[2000], 30)             # the whole bonus accelerates onto the sender
        self.assertEqual(a.contract.bonuses, ())
        self.assertEqual(a.club, "B")
        self.assertEqual(L.cap_number("B"), 20 + 5 - 5 + 0 or L.cap_number("B"))   # B holds a (20) and gave up b (5)
        self.assertEqual(L.cap_number("B"), 20)

    def test_trade_window_and_picks(self):
        L = Ledger(["A", "B"])
        a, b = L.new_player("QB", 80), L.new_player("QB", 70)
        L.sign("A", a.pid, MIN); L.sign("B", b.pid, MIN)
        pick = (2000, 1, "A")
        L.trade("A", [], "B", [], a_picks=[pick])
        self.assertIn(pick, L.clubs["B"].picks)
        with self.assertRaises(Rejected):
            L.trade("A", [], "B", [], a_picks=[pick])             # no longer A's
        with self.assertRaises(Rejected):
            L.trade("A", [], "B", [], a_picks=[(2005, 1, "A")])   # beyond the next three drafts
        L.start_camp(); L.begin_season()
        for _ in range(R.TRADE_LAST_PAID_WEEK):
            L.advance_week()
        L.trade("A", [a.pid], "B", [b.pid])                       # the last open week
        L.advance_week()
        with self.assertRaises(Rejected):
            L.trade("B", [a.pid], "A", [b.pid])                   # closed from week 9

    def test_waived_player_claimed_takes_the_guarantee_with_her(self):
        L = Ledger(["A", "B"])
        a = L.new_player("QB", 80)
        L.sign("A", a.pid, Contract(2000, (20,) * 3, (20, 20, 0), ((30, 0, 3),)))
        L.release("A", a.pid)
        self.assertEqual(L.clubs["A"].dead, {2000: 20 + 30, 2001: 20})
        claimed = L.resolve_waivers(["B"], wants=lambda c, p: True)
        self.assertEqual(claimed, [(a.pid, "B")])
        self.assertEqual(L.clubs["A"].dead, {2000: 30, 2001: 0})  # only the bonus stays behind
        self.assertEqual(a.contract.guaranteed[0], 20)
        self.assertEqual(L.cap_number("B"), 20)

    def test_unclaimed_player_becomes_a_free_agent(self):
        L = Ledger(["A", "B"])
        a = L.new_player("QB", 50)
        L.sign("A", a.pid, MIN)
        L.release("A", a.pid)
        L.resolve_waivers(["B"], wants=lambda c, p: False)
        self.assertEqual((a.where, a.club, a.contract), ("fa", None, None))

    def test_split_release_once_per_league_year(self):
        L = Ledger(["A"])
        ps = []
        for _ in range(2):
            p = L.new_player("WR", 70)
            L.sign("A", p.pid, Contract(2000, (10,) * 4, (), ((40, 0, 4),)))
            ps.append(p)
        L.release("A", ps[0].pid, split=True)
        with self.assertRaises(Rejected):
            L.release("A", ps[1].pid, split=True)
        L.release("A", ps[1].pid)                                 # an ordinary release is still allowed

    def test_restructure_is_gated(self):
        L = Ledger(["A"])
        p = L.new_player("WR", 70)
        L.sign("A", p.pid, Contract(2000, (60,) * 5))
        L.restructure("A", p.pid, 40)
        self.assertEqual(L.committed("A")[2000], 40)
        q = L.new_player("WR", 70)
        L.sign("A", q.pid, Contract(2000, (200,) * 5))
        with self.assertRaises(Rejected):
            L.restructure("A", q.pid, 198)                       # would leave 2, below the $5 minimum salary


class CutDownAndCap(unittest.TestCase):
    def club(self, salary=5):
        L = Ledger(["A"])
        fill(L, "A", contract=Contract(2000, (salary,)))
        for _ in range(5):
            L.sign("A", L.new_player("WR", 60).pid, Contract(2000, (salary,)))
        return L

    def test_there_is_no_salary_floor(self):
        L = self.club()
        L.start_camp()
        L.cut_down("A")                                            # 53 x 5 = 265, far under $800: allowed, nothing added
        self.assertEqual(L.cap_number("A"), 53 * 5)
        self.assertFalse(hasattr(R, "FLOOR"))
        self.assertFalse(hasattr(L.clubs["A"], "supplement"))

    def test_cut_refuses_oversize_overage_and_missing_minimums(self):
        L = self.club()
        L.sign("A", L.new_player("WR", 60).pid, MIN)               # 54
        L.start_camp()
        with self.assertRaises(Rejected):
            L.cut_down("A")
        L = Ledger(["A"])
        fill(L, "A", {k: v for k, v in dict(MINIMUMS, HB=3).items() if k != "K"})
        L.start_camp()
        with self.assertRaises(Rejected):
            L.cut_down("A")                                       # no kicker
        L = self.club()
        L.clubs["A"].dead[2000] = F(2000)                          # a club carried over the cap into the cut
        L.start_camp()
        with self.assertRaises(Rejected):
            L.cut_down("A")                                       # the cap must be met at the final cut

    def test_cap_is_enforced_all_year_counting_only_the_top_69_before_the_cut(self):
        L = Ledger(["A"])
        for _ in range(64):
            L.sign("A", L.new_player("WR", 60).pid, Contract(2000, (15,)))     # 960
        for _ in range(11):
            L.sign("A", L.new_player("WR", 60).pid, MIN)                       # 75 players: payroll 1015, counted 985
        self.assertEqual(L.cap_number("A"), 985)
        self.assertGreater(sum(k.cap_hit(2000) for k in L.contracts_of("A")), R.CAP)
        L.start_camp()
        L.release("A", L.clubs["A"].roster[-1])                     # a release is fine
        L.sign("A", L.new_player("WR", 60).pid, Contract(2000, (20,)))          # counted -> 1000: at the cap, allowed
        with self.assertRaises(Rejected):
            L.sign("A", L.new_player("WR", 60).pid, Contract(2000, (25,)))      # counted -> over the cap: refused
        L2 = Ledger(["A"])
        for _ in range(66):
            L2.sign("A", L2.new_player("WR", 60).pid, Contract(2000, (15,)))    # 990
        with self.assertRaises(Rejected):
            L2.sign("A", L2.new_player("WR", 60).pid, Contract(2000, (15,)))    # the 67th hit would be counted: 1005

    def test_begin_season_refuses_a_club_over_the_cap(self):
        L = Ledger(["A", "B"])
        L.sign("A", L.new_player("WR", 60).pid, MIN)
        L.clubs["A"].dead[2000] = F(2000)
        L.start_camp()
        with self.assertRaises(Rejected):
            L.begin_season()

    def test_vested_veteran_release_skips_waivers_but_stays_on_the_books(self):
        L = Ledger(["A", "B"])
        vet = L.new_player("QB", 80, seasons=R.VESTED_SEASONS)
        young = L.new_player("QB", 80, seasons=R.VESTED_SEASONS - 1)
        for p in (vet, young):
            L.sign("A", p.pid, Contract(2000, (20,) * 3, (20, 0, 0), ((30, 0, 3),)))
        L.release("A", vet.pid)
        self.assertEqual((vet.where, vet.club, vet.contract), ("fa", None, None))
        self.assertNotIn(vet.pid, L.waivers)
        self.assertEqual(L.clubs["A"].dead[2000], 20 + 30)         # guarantee and bonus both stay with the releasing club
        L.release("A", young.pid)
        self.assertEqual(young.where, "waivers")

    def test_in_season_signing_that_breaks_the_cap_is_rejected_but_release_is_not_blocked(self):
        L = Ledger(["A"])
        ps = [L.new_player("WR", 60) for _ in range(24)]
        for p in ps:
            L.sign("A", p.pid, Contract(2000, (40,)))              # 960
        L.start_camp(); L.begin_season()
        extra = L.new_player("WR", 60)
        with self.assertRaises(Rejected):
            L.sign("A", extra.pid, Contract(L.year, (50,), (), (), L.week))    # 1010 > cap
        L.sign("A", extra.pid, Contract(L.year, (40,), (), (), L.week))        # 1000 is at the cap: fine
        # a release that accelerates a bonus can leave a club over; it is never blocked by the cap
        L2 = Ledger(["A"])
        for p in [L2.new_player("WR", 60) for _ in range(24)]:
            L2.sign("A", p.pid, Contract(2000, (40,)))
        q = L2.new_player("WR", 60)
        L2.sign("A", q.pid, Contract(2000, (30,), (), ((10, 0, 1),)))          # 960 + 40 = 1000
        L2.start_camp(); L2.begin_season()
        L2.release("A", q.pid)
        self.assertLessEqual(L2.cap_number("A"), 1000)

    def test_practice_squad_salary_counts_against_the_cap(self):
        L = Ledger(["A"])
        L.start_camp(); L.begin_season()
        p = L.new_player("WR", 60, seasons=0)
        L.sign("A", p.pid, Contract(L.year, (12,), (), (), L.week), "ps")
        self.assertEqual(L.cap_number("A"), 12)


class GameDay(unittest.TestCase):
    def test_active_48_and_47_with_fewer_than_eight_ol(self):
        L = Ledger(["A"])
        fill(L, "A", dict(MINIMUMS, HB=3, WR=10, CB=8))
        L.start_camp(); L.begin_season()
        gd = gameday.build(L, "A")
        self.assertEqual((gd.limit, len(gd.active)), (48, 48))
        # take the line down to seven healthy
        for pid in list(L.clubs["A"].roster):
            p = L.players[pid]
            if p.pos == "LT":
                p.out_weeks = 5
        n_ol = sum(1 for pid in L.clubs["A"].roster if L.players[pid].pos in ("LT", "LG", "C", "RG", "RT") and L.players[pid].out_weeks == 0)
        self.assertEqual(n_ol, 7)
        gd = gameday.build(L, "A")
        self.assertEqual((gd.limit, len(gd.active)), (47, 47))

    def test_elevations_two_a_game_three_a_player(self):
        L = Ledger(["A"])
        fill(L, "A", {k: v for k, v in dict(MINIMUMS, HB=3).items() if k != "QB"})   # no quarterback at all
        L.start_camp(); L.begin_season()
        qbs = [L.new_player("QB", 70 - i) for i in range(4)]
        for q in qbs:
            L.sign("A", q.pid, MIN, "ps")
        seen = []
        for g in range(4):
            gd = gameday.build(L, "A")
            seen.append(len(gd.elevated))
            self.assertLessEqual(len(gd.elevated), R.ELEVATIONS_PER_GAME)
        self.assertTrue(all(q.elevations <= R.ELEVATIONS_PER_PLAYER for q in qbs))
        self.assertEqual(max(seen), 2)

    def test_emergency_list_names_a_player_where_nobody_is_left(self):
        L = Ledger(["A"])
        fill(L, "A", {k: v for k, v in dict(MINIMUMS, HB=3).items() if k != "K"})
        L.start_camp(); L.begin_season()
        gd = gameday.build(L, "A")
        self.assertIn("K", gd.short)
        self.assertIn("K", gd.emergency)
        self.assertTrue(gd.can_field)

    def test_fewer_than_eleven_healthy_forfeits(self):
        L = Ledger(["A"])
        fill(L, "A", {"QB": 5, "WR": 5})
        L.start_camp(); L.begin_season()
        for pid in L.clubs["A"].roster[:-4]:
            L.players[pid].out_weeks = 3
        self.assertFalse(gameday.build(L, "A").can_field)


if __name__ == "__main__":
    unittest.main()
