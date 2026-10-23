"""Ledger tests: contracts, cap arithmetic, viability gate, rosters, IR, elevations, game day, waivers, trades, cut-down."""
import os
import sys
import unittest
from fractions import Fraction as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl.ledger import gameday, rules as R  # noqa: E402
from dfl.ledger.contracts import Contract, ContractError, rookie_contract  # noqa: E402
from dfl.ledger.ledger import Ledger as _Ledger, Rejected  # noqa: E402
from dfl.ledger.positions import MINIMUMS, POSITIONS, shortfalls  # noqa: E402

MIN = Contract(2000, (5,))


def Ledger(cids, year=2000, drafted=True):
    """A ledger for the tests; by default this year's draft is already done so no rookie reserve muddies the arithmetic."""
    L = _Ledger(cids, year)
    if drafted:
        L.draft_completed = year
    return L


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
            L.sign("A", p.pid, MIN, "roster")                    # one past the ceiling
        L.start_camp(); L.begin_season()
        self.assertEqual(len(L.clubs["A"].roster), R.PRESEASON_CEILING)
        q = L.new_player("WR", 60)
        with self.assertRaises(Rejected):
            L.sign("A", q.pid, MIN, "roster")                    # in season the roster is capped at 53

    def test_practice_squad_size_and_veterans(self):
        L = Ledger(["A"])
        L.start_camp(); L.begin_season()
        for i in range(R.PS_MAX):
            p = L.new_player(POSITIONS[i % 22], 60, seasons=5 if i < R.PS_VETERANS_MAX else 0)
            L.sign("A", p.pid, MIN, "ps")
        with self.assertRaises(Rejected):
            L.sign("A", L.new_player("QB", 60).pid, MIN, "ps")   # 17th
        L2 = Ledger(["A"])
        L2.start_camp(); L2.begin_season()
        for _ in range(R.PS_VETERANS_MAX):
            L2.sign("A", L2.new_player(POSITIONS[_], 60, seasons=5).pid, MIN, "ps")
        with self.assertRaises(Rejected):
            L2.sign("A", L2.new_player("QB", 60, seasons=5).pid, MIN, "ps")   # 7th veteran
        L2.sign("A", L2.new_player("QB", 60, seasons=1).pid, MIN, "ps")      # a second-year player is fine

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
        L.complete_draft()                                        # (an unmade draft would reserve rookie cap room)
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
        L = Ledger(["A", "B"], drafted=False)
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
        ps = [L.new_player(POSITIONS[i % 22], 60) for i in range(24)]       # (positions spread: a block holds only so many jersey numbers)
        for p in ps:
            L.sign("A", p.pid, Contract(2000, (40,)))              # 960
        L.start_camp(); L.begin_season()
        extra = L.new_player("QB", 60)
        with self.assertRaises(Rejected):
            L.sign("A", extra.pid, Contract(L.year, (50,), (), (), L.week))    # 1010 > cap
        L.sign("A", extra.pid, Contract(L.year, (40,), (), (), L.week))        # 1000 is at the cap: fine
        # a release that accelerates a bonus can leave a club over; it is never blocked by the cap
        L2 = Ledger(["A"])
        for p in [L2.new_player(POSITIONS[i % 22], 60) for i in range(24)]:
            L2.sign("A", p.pid, Contract(2000, (40,)))
        q = L2.new_player("QB", 60)
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


class PoolAndReserve(unittest.TestCase):
    def test_four_consecutive_years_in_the_pool_retire_her_and_remove_her(self):
        L = Ledger(["A"])
        p = L.new_player("WR", 60)
        pid = p.pid
        for yr in range(3):
            L.new_year()
            self.assertIn(pid, L.players, f"still listed at opening {yr + 1}")
            self.assertEqual(L.players[pid].pool_years, yr + 1)
        L.new_year()                                              # fourth opening unsigned
        self.assertNotIn(pid, L.players)
        self.assertEqual(L.retired["pool"], 1)

    def test_being_hired_resets_the_clock(self):
        L = Ledger(["A"])
        p = L.new_player("WR", 60, seasons=4)                      # vested: a release goes straight back to the pool
        L.new_year(); L.new_year(); L.new_year()
        self.assertEqual(p.pool_years, 3)
        L.sign("A", p.pid, Contract(L.year, (5,), (), (), L.week))
        self.assertEqual(p.pool_years, 0)
        L.release("A", p.pid)                                     # back in the pool, counting from zero
        for _ in range(3):
            L.new_year()
        self.assertIn(p.pid, L.players)
        L.new_year()
        self.assertNotIn(p.pid, L.players)

    def test_expiring_contract_starts_the_clock_at_zero_and_rostered_players_never_count(self):
        L = Ledger(["A"])
        a = L.new_player("WR", 60)
        b = L.new_player("WR", 60)
        L.sign("A", a.pid, Contract(2000, (5, 5), (), (), 0))
        L.sign("A", b.pid, Contract(2000, (5,), (), (), 0))
        L.new_year()                                              # 2001: b's contract expired, a still under contract
        self.assertEqual((a.where, b.where, a.pool_years, b.pool_years), ("roster", "fa", 0, 0))
        L.new_year()                                              # 2002: a expired too; b has been in the pool one opening
        self.assertEqual((a.pool_years, b.pool_years), (0, 1))

    def test_only_pool_members_can_be_retired(self):
        L = Ledger(["A"])
        p = L.new_player("WR", 60)
        L.sign("A", p.pid, MIN)
        with self.assertRaises(Rejected):
            L.retire(p.pid, "age")

    def test_unmade_picks_reserve_cap_room_at_the_rookie_scale(self):
        L = Ledger(["A"], drafted=False)
        self.assertEqual(L.rookie_reserve("A"), [25, 18, 14, 11, 9, 7, 5])
        self.assertEqual(L.cap_number("A"), 89)
        L.clubs["A"].picks.add((2000, 8, "A"))                   # a compensatory pick reserves the round-7 scale
        self.assertEqual(L.cap_number("A"), 94)
        L.complete_draft()
        self.assertEqual(L.cap_number("A"), 0)

    def test_reserve_leaves_room_and_a_traded_pick_moves_its_reserve(self):
        L = Ledger(["A", "B"], drafted=False)
        for _ in range(60):
            L.sign("A", L.new_player("WR", 60).pid, Contract(2000, (15,)))          # 900 + 89 reserved = 989
        with self.assertRaises(Rejected):
            L.sign("A", L.new_player("WR", 60).pid, Contract(2000, (20,)))          # would leave no room for the class
        L.trade("A", [], "B", [], a_picks=[(2000, 1, "A")])
        self.assertEqual(L.cap_number("A"), 900 + 64)
        self.assertEqual(L.cap_number("B"), 89 + 25)

    def test_signing_the_pick_replaces_its_reserve(self):
        L = Ledger(["A"], drafted=False)
        L.clubs["A"].picks.discard((2000, 1, "A"))
        p = L.new_player("WR", 60)
        L.sign("A", p.pid, rookie_contract(2000, 1, first_round_option=True))
        self.assertEqual(L.cap_number("A"), 89)                  # 64 reserved + 25 signed


class JerseyNumbers(unittest.TestCase):
    def season(self, cids=("A",)):
        L = Ledger(list(cids))
        L.start_camp()
        L.begin_season()
        return L

    def nums(self, L, cid):
        return {L.players[i].pid: L.players[i].number for i in L.clubs[cid].all_pids()}

    def test_every_position_has_a_block_and_a_new_arrival_takes_the_lowest_open(self):
        from dfl.ledger import numbers as N
        self.assertEqual(set(N.PRIMARY), set(POSITIONS))
        L = self.season()
        a = L.new_player("QB", 60); b = L.new_player("QB", 60); k = L.new_player("K", 60); s = L.new_player("SS", 60)
        for p in (a, b, k, s):
            L.sign("A", p.pid, Contract(L.year, (5,), (), (), L.week))
        self.assertEqual((a.number, b.number, k.number, s.number), (10, 11, 1, 40))

    def test_numbers_are_unique_in_a_club_and_a_full_block_rejects_a_position_with_no_overflow(self):
        L = self.season()
        for _ in range(10):
            L.sign("A", L.new_player("CB", 60).pid, Contract(L.year, (5,), (), (), L.week))
        self.assertEqual(sorted(self.nums(L, "A").values()), list(range(20, 30)))
        with self.assertRaises(Rejected):
            L.sign("A", L.new_player("CB", 60).pid, Contract(L.year, (5,), (), (), L.week))     # 11th CB: no number, no overflow

    def test_overflow_only_when_the_primary_block_is_full(self):
        L = self.season()
        for _ in range(10):
            L.sign("A", L.new_player("WR", 60).pid, Contract(L.year, (5,), (), (), L.week))
        self.assertEqual(sorted(self.nums(L, "A").values()), list(range(80, 90)))
        w = L.new_player("WR", 60)
        L.sign("A", w.pid, Contract(L.year, (5,), (), (), L.week))
        self.assertEqual(w.number, 15)                                   # WR overflow
        lb = [L.new_player("MLB", 60) for _ in range(11)]
        for p in lb:
            L.sign("A", p.pid, Contract(L.year, (5,), (), (), L.week))
        self.assertEqual([p.number for p in lb], list(range(50, 60)) + [40])
        de = [L.new_player("DT", 60) for _ in range(11)]
        for p in de:
            L.sign("A", p.pid, Contract(L.year, (5,), (), (), L.week))
        self.assertEqual(de[-1].number, 70)

    def test_a_primary_player_bumps_an_overflow_holder(self):
        L = self.season()
        wrs = [L.new_player("WR", 60, seasons=4) for _ in range(15)]
        for p in wrs:
            L.sign("A", p.pid, Contract(L.year, (5,), (), (), L.week))     # 10 in 80-89, 5 overflow in 15-19
        L.release("A", wrs[3].pid)                                          # vested: straight to free agency, frees 83
        te = L.new_player("TE", 60)
        L.sign("A", te.pid, Contract(L.year, (5,), (), (), L.week))
        self.assertTrue(15 <= te.number <= 19)                              # got a primary number
        self.assertTrue(all(L.players[w.pid].number in range(80, 90) or L.players[w.pid].number in range(15, 20)
                            for w in wrs if w.pid != wrs[3].pid))
        self.assertEqual(len(self.nums(L, "A")), len(set(self.nums(L, "A").values())))

    def test_a_number_is_freed_at_once_and_there_are_no_retired_numbers(self):
        L = self.season()
        a = L.new_player("QB", 60, seasons=4)
        L.sign("A", a.pid, Contract(L.year, (5,), (), (), L.week))
        self.assertEqual(a.number, 10)
        L.release("A", a.pid)
        self.assertIsNone(a.number)
        b = L.new_player("QB", 60)
        L.sign("A", b.pid, Contract(L.year, (5,), (), (), L.week))
        self.assertEqual(b.number, 10)

    def test_same_club_resigning_takes_the_old_number_back_if_free_other_clubs_do_not(self):
        L = self.season(("A", "B"))
        a, b = L.new_player("QB", 60, seasons=4), L.new_player("QB", 60, seasons=4)
        for p in (a, b):
            L.sign("A", p.pid, Contract(L.year, (5,), (), (), L.week))
        self.assertEqual((a.number, b.number), (10, 11))
        L.release("A", a.pid)
        L.release("A", b.pid)
        L.sign("A", b.pid, Contract(L.year, (5,), (), (), L.week))
        self.assertEqual(b.number, 11)                                       # her old number, not the lowest open (10)
        L.release("A", b.pid)
        L.sign("B", b.pid, Contract(L.year, (5,), (), (), L.week))
        self.assertEqual(b.number, 10)                                       # a new club: lowest open
        L.release("B", b.pid)
        L.sign("A", L.new_player("QB", 60).pid, Contract(L.year, (5,), (), (), L.week))      # takes 10 at A
        L.sign("A", b.pid, Contract(L.year, (5,), (), (), L.week))
        self.assertEqual(b.number, 11)                                       # her last club was B (number 10, taken here): lowest open

    def test_trade_keeps_the_number_if_free_else_gives_another(self):
        L = Ledger(["A", "B"], drafted=True)
        L.start_camp(); L.begin_season()
        a = L.new_player("QB", 60); b = L.new_player("QB", 60)
        L.sign("A", a.pid, Contract(L.year, (5,), (), (), L.week))           # A: 10
        L.sign("B", L.new_player("QB", 60).pid, Contract(L.year, (5,), (), (), L.week))      # B: 10
        L.sign("B", b.pid, Contract(L.year, (5,), (), (), L.week))           # B: 11
        L.trade("A", [a.pid], "B", [])                                       # 10 is taken at B
        self.assertEqual(a.number, 12)
        c = L.new_player("HB", 60)
        L.sign("B", c.pid, Contract(L.year, (5,), (), (), L.week))           # 30
        L.trade("B", [c.pid], "A", [])                                       # 30 is free at A: kept
        self.assertEqual(c.number, 30)

    def test_the_final_cut_ends_the_camp_relaxation(self):
        L = Ledger(["A"])
        fill(L, "A")
        qbs = [L.new_player("QB", 60, seasons=4) for _ in range(5)]          # QB block holds 5 and 3 QBs are already there
        L.start_camp()
        for p in qbs:
            L.sign("A", p.pid, Contract(L.year, (5,), (), (), L.week))
        odd = [p for p in qbs if not 10 <= p.number <= 14]
        self.assertTrue(odd)                                                # camp gave some an off-block number
        for p in odd:
            L.release("A", p.pid)
        L.cut_down("A")
        self.assertTrue(all(10 <= L.players[i].number <= 14 for i in L.clubs["A"].roster if L.players[i].pos == "QB"))
        self.assertTrue(all(L.players[i].number is not None for i in L.clubs["A"].all_pids()))

    def test_numbers_stay_with_the_player_through_the_year_turn_while_under_contract(self):
        L = self.season()
        a = L.new_player("CB", 60)
        L.sign("A", a.pid, Contract(L.year, (5, 5), (), (), L.week))
        n = a.number
        L.end_season(); L.new_year()
        self.assertEqual(a.number, n)                                       # under contract: she keeps it (exile or not)
