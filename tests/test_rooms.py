"""Meeting rooms (Edition 8.29): the contract room, the trade room, the interview."""
import os
import sys
import unittest
from fractions import Fraction as F

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from dfl.ledger import rooms  # noqa: E402
from dfl.ledger import rules as R  # noqa: E402
from dfl.ledger.world import World, ask  # noqa: E402

CLUBS = [f"C{i:02d}" for i in range(1, 13)]


def fresh(seed=31):
    w = World(CLUBS, seed=seed)
    w.open_year(first=True)
    return w


def bargainer(w, cid="C01"):
    return next(p for p in sorted(w.L.players.values(), key=lambda q: q.pid) if p.where == "fa" and ask(p) > R.MIN_SALARY and p.ovr >= 56)


class RoomRules(unittest.TestCase):
    def setUp(self):
        self.w = fresh()

    def test_only_a_party_may_speak_and_a_closed_room_is_closed(self):
        w = self.w
        room = rooms.open_room(w, "interview", ("club:C01", "candidate:5"))
        with self.assertRaises(rooms.RoomError):
            rooms.say(w, room, 1, "club:C09", "invite")
        rooms.say(w, room, 1, "club:C01", "invite")
        rooms.close(w, room, "accepted")
        with self.assertRaises(rooms.RoomError):
            rooms.say(w, room, 2, "candidate:5", "accept")
        with self.assertRaises(rooms.RoomError):
            rooms.close(w, room, "declined")

    def test_the_round_limit_is_enforced_by_kind(self):
        w = self.w
        room = rooms.open_room(w, "trade", ("club:C01", "club:C02"))
        rooms.say(w, room, 3, "club:C01", "propose")
        with self.assertRaises(rooms.RoomError):
            rooms.say(w, room, 4, "club:C02", "reject")
        room = rooms.open_room(w, "interview", ("club:C01", "candidate:5"))
        with self.assertRaises(rooms.RoomError):
            rooms.say(w, room, 3, "club:C01", "invite")

    def test_old_transcripts_are_dropped_after_the_keep_window(self):
        w = self.w
        rooms.open_room(w, "interview", ("club:C01", "candidate:5"))
        w.L.year += 5
        rooms.prune(w)
        self.assertEqual(w.rooms, [])


class ContractRoom(unittest.TestCase):
    def setUp(self):
        self.w = fresh()
        self.p = bargainer(self.w)

    def test_players_at_the_minimum_do_not_bargain_and_open_no_room(self):
        w = self.w
        cheap = next(p for p in w.L.players.values() if p.where == "fa" and ask(p) <= R.MIN_SALARY)
        w._views[("C01", cheap.pid)] = 50.0
        n = len(w.rooms)
        self.assertEqual(w.negotiate("C01", cheap), w.quote("C01", cheap))
        self.assertEqual(len(w.rooms), n)

    def test_a_deal_has_three_rounds_at_most_and_is_inside_the_two_ceilings(self):
        w, p = self.w, self.p
        w._views[("C01", p.pid)] = float(p.ovr) + 8
        price = w.negotiate("C01", p)
        room = w.rooms[-1]
        self.assertEqual(room["kind"], "contract")
        self.assertLessEqual(max(m[0] for m in room["msgs"]), 3)
        self.assertEqual(room["outcome"], "deal" if price is not None else "no_deal")
        if price is not None:
            self.assertLessEqual(price, w.willing("C01", p))

    def test_a_better_negotiator_pays_less_for_the_same_player(self):
        w, p = self.w, self.p
        w._views[("C01", p.pid)] = float(p.ovr) + 10
        out = {}
        for neg in (0, 50, 100):
            w.leaders["C01"]["gm"].attrs["NEG"] = neg
            out[neg] = w.negotiate("C01", p)
        self.assertTrue(out[0] >= out[50] >= out[100])
        self.assertGreater(out[0], out[100])
        d = w.demand(p, "C01")
        self.assertLess(abs(out[50] - d) / d, 0.02)           # at the middle rating the deal is her demand
        self.assertGreaterEqual(out[100], 0.9 * d)           # and no rating takes her below the least she will accept

    def test_a_club_that_cannot_reach_her_least_price_has_no_deal(self):
        w, p = self.w, self.p
        w._views[("C02", p.pid)] = 61.0              # a club that thinks her worth bargaining over, but far below what she asks
        self.assertGreater(w.quote("C02", p), R.MIN_SALARY)
        self.assertIsNone(w.negotiate("C02", p))
        self.assertEqual(w.rooms[-1]["outcome"], "no_deal")

    def test_every_free_agency_signing_above_the_minimum_was_negotiated_in_a_room(self):
        w = self.w
        w.free_agency_open()
        deals = [r for r in w.rooms if r["kind"] == "contract" and r["outcome"] == "deal"]
        self.assertTrue(deals)
        self.assertEqual(len(deals), w.stats["deals"])
        self.assertTrue(all(len(r["msgs"]) <= 4 and max(m[0] for m in r["msgs"]) <= 3 for r in w.rooms))


class TradeRoom(unittest.TestCase):
    def test_each_negotiation_is_a_room_with_at_most_three_rounds_and_a_matching_outcome(self):
        w = fresh(41)
        w.L.open_trading()
        w.free_agency_open()
        for _ in range(3):
            w.trades()
        tr = [r for r in w.rooms if r["kind"] == "trade"]
        self.assertTrue(tr)
        for r in tr:
            self.assertIn(r["outcome"], ("deal", "no_deal"))
            self.assertLessEqual(max(m[0] for m in r["msgs"]), 3)
            self.assertEqual(r["msgs"][0][2], "propose")
        self.assertEqual(sum(r["outcome"] == "deal" for r in tr), w.stats["trades"])


class Interview(unittest.TestCase):
    def setUp(self):
        self.w = fresh(51)
        from dfl.ledger import coaching
        coaching.top_up(self.w)
        self.cands = list(self.w.coach_pool)

    def test_the_answer_is_the_same_every_time_it_is_asked(self):
        w, c = self.w, self.cands[0]
        w.form["C01"] = 0.1
        a = [rooms.interview(w, "C01", c, "hc") for _ in range(4)]
        self.assertEqual(len(set(a)), 1)

    def test_a_candidate_declines_a_losing_club_more_than_a_winning_one_and_never_a_lot(self):
        w = self.w
        c = max(self.cands, key=lambda x: x.traits["AMB"])
        w.form["C01"], w.form["C02"] = 0.0, 0.9
        lo, hi = rooms.decline_chance(w, "C01", c), rooms.decline_chance(w, "C02", c)
        self.assertGreater(lo, hi)
        self.assertEqual(hi, w.tables.rooms["decline_base"])
        self.assertLessEqual(lo, w.tables.rooms["decline_base"] + w.tables.rooms["decline_fortune"])

    def test_a_declined_candidate_stays_in_the_pool_and_the_post_is_filled(self):
        from dfl.ledger import coaching
        w = self.w
        w.tables.rooms["decline_base"] = 0.5
        del w.leaders["C01"]["oc"]
        got = coaching.hire(w, "C01", "oc")
        self.assertIs(w.leaders["C01"]["oc"], got)
        declined = [r for r in w.rooms if r["kind"] == "interview" and r["outcome"] == "declined"]
        self.assertTrue(declined)
        for r in declined:
            cid = int(r["parties"][1].split(":")[1])
            self.assertTrue(any(c.identity.id == cid for c in w.coach_pool))

    def test_an_interview_shrinks_the_club_s_error(self):
        from dfl.ledger import coaching
        w = self.w
        c = self.cands[0]
        true = coaching.fit(w, c, "hc")
        raw, sharp = coaching.view(w, "C01", c, "hc"), coaching.view(w, "C01", c, "hc", w.tables.rooms["interview_factor"])
        self.assertLessEqual(abs(sharp - true), abs(raw - true) + 1e-9)

    def test_a_club_interviews_up_to_the_limit(self):
        from dfl.ledger import coaching
        w = self.w
        del w.leaders["C03"]["hc"]
        n0 = len([r for r in w.rooms if r["kind"] == "interview" and r["outcome"] == "accepted"])
        coaching.hire(w, "C03", "hc")
        acc = len([r for r in w.rooms if r["kind"] == "interview" and r["outcome"] == "accepted"]) - n0
        self.assertEqual(acc, int(w.tables.rooms["interviews_per_hire"]))


if __name__ == "__main__":
    unittest.main()
