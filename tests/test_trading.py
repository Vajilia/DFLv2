"""Trades, stage 1 (Edition 8.26): the public list, private valuation, the block, the three-round protocol, the ledger check and the log."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from dfl.ledger import trading  # noqa: E402
from dfl.ledger.world import World  # noqa: E402

CLUBS = [f"C{i:02d}" for i in range(1, 13)]


def fresh(seed):
    w = World(CLUBS, seed=seed)
    w.open_year(first=True)
    w.free_agency_open()
    return w


class PublicList(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = fresh(101)

    def test_only_public_facts(self):
        row = trading.public_list(self.w, limit=1)[0]
        self.assertEqual(set(row), {"pid", "club", "pos", "age", "seasons", "slot", "cap_hit", "years_left", "out_weeks", "market"})

    def test_it_covers_every_rostered_player_and_filters_and_sorts(self):
        w = self.w
        allrows = trading.public_list(w)
        self.assertEqual(len(allrows), sum(len(c.roster) + len(c.ir) for c in w.L.clubs.values()))
        qbs = trading.public_list(w, pos="QB")
        self.assertTrue(qbs and all(r["pos"] == "QB" for r in qbs))
        one = trading.public_list(w, club="C03")
        self.assertTrue(all(r["club"] == "C03" for r in one))
        by_age = trading.public_list(w, sort="age", limit=5)
        self.assertEqual([r["age"] for r in by_age], sorted((r["age"] for r in by_age), reverse=True))
        young = trading.public_list(w, max_age=23)
        self.assertTrue(all(r["age"] <= 23 for r in young))


class Valuation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = fresh(102)

    def test_two_clubs_value_the_same_player_differently(self):
        w = self.w
        pid = w.L.clubs["C01"].roster[0]
        vals = {trading.value(w, c, pid, +1) for c in CLUBS[1:]}
        self.assertGreater(len(vals), 3)

    def test_a_greedy_or_skilled_negotiator_asks_for_a_bigger_margin(self):
        w = self.w
        gm = w.leaders["C01"]["gm"]
        base = trading.margin(w, "C01")
        gm.traits["GRE"] = 100
        greedy = trading.margin(w, "C01")
        gm.traits["GRE"] = 50
        gm.attrs["NEG"] = 99
        skilled = trading.margin(w, "C01")
        gm.attrs["NEG"] = 50
        self.assertGreater(greedy, base)
        self.assertGreater(skilled, base)

    def test_picks_are_worth_less_the_later_the_round_and_the_further_away(self):
        w = self.w
        y = w.L.draft_completed + 1
        self.assertGreater(trading.pick_value(w, (y, 1, "C01")), trading.pick_value(w, (y, 2, "C01")))
        self.assertGreater(trading.pick_value(w, (y, 1, "C01")), trading.pick_value(w, (y + 2, 1, "C01")))


class Block(unittest.TestCase):
    def test_the_block_is_small_healthy_and_only_players_the_club_values_little(self):
        w = fresh(103)
        T = w.tables.trading
        for cid in CLUBS:
            b = trading.block(w, cid)
            self.assertLessEqual(len(b), int(T["block_size"]))
            have = trading._have(w, cid)
            for pid in b:
                self.assertEqual(w.L.players[pid].club, cid)
                self.assertEqual(w.L.players[pid].out_weeks, 0)
                self.assertLessEqual(trading.value(w, cid, pid, -1, have), T["block_max_value"])


class Protocol(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = fresh(104)
        cls.made = sum(cls.w.trades() for _ in range(40))

    def test_deals_happen(self):
        self.assertGreater(self.made, 0)
        self.assertEqual(self.w.stats["trades"], self.made)

    def test_every_accepted_deal_moved_exactly_the_players_and_picks_named_and_nothing_illegal_was_proposed(self):
        w = fresh(107)
        saw = 0
        for _ in range(30):
            n0 = len(w.trade_log)
            w.trades()
            for r in w.trade_log[n0:]:
                self.assertNotEqual(r["kind"], "illegal")
                if r["kind"] == "accept":
                    saw += 1
                    for pid in r["a_gives"]:
                        self.assertEqual(w.L.players[pid].club, r["b"])
                    for pid in r["b_gives"]:
                        self.assertEqual(w.L.players[pid].club, r["a"])
                    for pk in r["a_picks"]:
                        self.assertIn(tuple(pk), w.L.clubs[r["b"]].picks)
        self.assertGreater(saw, 0)

    def test_a_full_year_with_trading_passes_every_audit_with_no_forfeit(self):
        w = World(CLUBS, seed=108)
        for y in range(2):
            w.step_year(first=(y == 0))          # step_year audits at the season start and the year end
        self.assertGreater(w.stats["trades"], 0)
        self.assertEqual(w.stats["forfeits"], 0)

    def test_at_most_three_rounds_and_three_players_and_three_picks_a_side(self):
        w = self.w
        T = w.tables.trading
        for r in w.trade_log:
            self.assertLessEqual(len(r["a_gives"]), int(T["max_players"]))
            self.assertLessEqual(len(r["b_gives"]), int(T["max_players"]))
            self.assertLessEqual(len(r["a_picks"]) + len(r["b_picks"]), int(T["max_picks"]))
            if "round" in r:
                self.assertLessEqual(r["round"], 3)

    def test_the_log_reads_as_whole_negotiations(self):
        w = self.w
        kinds = [r["kind"] for r in w.trade_log]
        for prev, nxt in zip(kinds, kinds[1:]):
            if prev == "counter":
                self.assertIn(nxt, {"accept", "reject", "illegal"})        # a counter is always answered
            if nxt in ("accept", "reject", "counter"):
                self.assertIn(prev, {"propose", "counter"})                # and an answer always follows a proposal or a counter
        self.assertEqual(kinds.count("accept"), self.made)

    def test_a_player_is_in_at_most_one_deal_per_run(self):
        w = self.w
        w2 = fresh(105)
        for _ in range(30):
            n0 = len(w2.trade_log)
            w2.trades()
            moved = []
            for r in w2.trade_log[n0:]:
                if r["kind"] == "accept":
                    moved += r["a_gives"] + r["b_gives"]
            self.assertEqual(len(moved), len(set(moved)))

    def test_it_is_repeatable(self):
        def run():
            w = fresh(106)
            for _ in range(15):
                w.trades()
            return w.trade_log
        self.assertEqual(run(), run())

    def test_no_cash_or_cap_space_ever_moves(self):
        for r in self.w.trade_log:
            self.assertEqual(set(r) - {"year", "week", "kind", "a", "b", "a_gives", "b_gives", "a_picks", "b_picks", "round", "score_a", "score_b", "reason"}, set())


if __name__ == "__main__":
    unittest.main()


class PublicPrice(unittest.TestCase):
    """Trades, stage 2 (Edition 8.34): the market price comes from public evidence, never from a player's own hidden rating."""

    def setUp(self):
        self.w = fresh(131)
        self.p = next(p for p in (self.w.L.players[i] for i in self.w.L.clubs["C03"].roster) if p.pos == "QB")

    def test_her_own_hidden_rating_does_not_move_her_public_estimate(self):
        w, p = self.w, self.p
        before = trading.public_ovr(w, p)
        w.rating[p.pid] += 25
        w.pub_prior.clear()
        self.assertEqual(trading.public_ovr(w, p), before)

    def test_with_no_production_the_estimate_is_the_going_level_for_her_position_and_age(self):
        w, p = self.w, self.p
        w.career.pop(p.pid, None)
        self.assertEqual(trading.public_ovr(w, p), trading._prior(w, p.pos, p.age, p.pid))

    def test_better_scores_raise_it_and_more_seasons_count_for_more(self):
        w, p = self.w, self.p
        base = trading.public_ovr(w, p)
        w.career[p.pid] = {1990: {"score": 1.0}}
        one = trading.public_ovr(w, p)
        w.career[p.pid] = {1990: {"score": 1.0}, 1991: {"score": 1.0}, 1992: {"score": 1.0}}
        three = trading.public_ovr(w, p)
        w.career[p.pid] = {1990: {"score": -1.0}, 1991: {"score": -1.0}, 1992: {"score": -1.0}}
        bad = trading.public_ovr(w, p)
        self.assertGreater(one, base)
        self.assertGreater(three, one)
        self.assertLess(bad, base)
        T = w.tables.trading
        self.assertAlmostEqual(three - base, T["pub_slope"] * 3 / (3 + T["pub_k"]), places=6)

    def test_only_the_latest_seasons_count_and_unscored_seasons_are_skipped(self):
        w, p = self.w, self.p
        T = w.tables.trading
        w.career[p.pid] = {1980: {"score": 5.0}, 1990: {"score": 1.0}, 1991: {"pos": "QB"}, 1992: {"score": 1.0}, 1993: {"score": 1.0}}
        got = trading.public_ovr(w, p) - trading._prior(w, p.pos, p.age, p.pid)
        self.assertAlmostEqual(got, T["pub_slope"] * 1.0 * 2 / (2 + T["pub_k"]), places=6)       # 1991 unscored; 1980 outside the three latest records

    def test_the_listed_market_price_and_the_negotiators_estimate_both_use_it(self):
        w, p = self.w, self.p
        w.career[p.pid] = {1990: {"score": 2.0}}
        row = next(r for r in trading.public_list(w, club="C03") if r["pid"] == p.pid)
        self.assertEqual(row["market"], trading.public_market(w, p))
        years = max(1, min(trading.years_left(w, p), int(w.tables.trading["years_cap"])))
        self.assertAlmostEqual(trading._public_value(w, p.pid), (trading.public_market(w, p) - float(p.contract.cap_hit(w.L.year))) * years)
