"""The emergency list (spec section 11): filed before every kickoff with coverage for every position, from dressed and medically available
players only; resolved in the game as players are injured, never putting one player in two places; official positions never change."""
import copy
import os
import random
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl.characters import Creator, load_tables  # noqa: E402
from dfl.engine.squad import Squad, OFF, DEF  # noqa: E402
from dfl.ledger import gameday  # noqa: E402
from dfl.ledger.contracts import Contract  # noqa: E402
from dfl.ledger.ledger import Ledger as _Ledger  # noqa: E402
from dfl.ledger.positions import MINIMUMS, POSITIONS  # noqa: E402


def ledger_with(spec, ovr=60):
    L = _Ledger(["A"], 2000)
    L.draft_completed = 2000
    for pos, n in spec.items():
        for _ in range(n):
            p = L.new_player(pos, ovr, seasons=1)
            L.sign("A", p.pid, Contract(L.year, (5,), (), (), L.week), "roster")
    L.start_camp()
    L.begin_season()
    return L


FULL = dict(MINIMUMS, HB=3)


class Filing(unittest.TestCase):
    def test_every_position_has_a_name_before_kickoff(self):
        L = ledger_with(FULL)
        gd = gameday.build(L, "A")
        self.assertEqual(set(gd.emergency), set(POSITIONS))
        self.assertEqual(gd.needed, [])                                     # nobody is missing, yet every position is covered
        for pos in POSITIONS:
            names = gd.emergency[pos]
            self.assertGreaterEqual(len(names), 1, pos)
            self.assertLessEqual(len(names), gameday.EMERGENCY_NAMES)
            self.assertEqual(len(set(names)), len(names), pos)
            for pid in names:
                p = L.players[pid]
                self.assertIn(pid, gd.active)                              # dressed
                self.assertEqual(p.out_weeks, 0)                           # medically available
                self.assertNotEqual(p.pos, pos)                            # a player of another position

    def test_kicker_punter_long_snapper_and_quarterback_are_covered_too(self):
        gd = gameday.build(ledger_with(FULL), "A")
        for pos in ("K", "P", "LS", "QB"):
            self.assertTrue(gd.emergency[pos], pos)

    def test_neighbouring_positions_come_first(self):
        L = ledger_with(FULL)
        gd = gameday.build(L, "A")
        for pos in ("K", "CB", "LT", "MLB"):
            near = gameday.NEIGHBOURS[pos]
            self.assertIn(L.players[gd.emergency[pos][0]].pos, near, pos)

    def test_the_first_names_are_different_players_for_different_positions(self):
        gd = gameday.build(ledger_with(FULL), "A")
        firsts = [gd.emergency[p][0] for p in POSITIONS]
        self.assertEqual(len(set(firsts)), len(firsts))

    def test_an_injured_player_is_never_dressed_so_never_named(self):
        L = ledger_with(FULL)
        hurt = [pid for pid in L.clubs["A"].roster if L.players[pid].pos in ("P", "FS")]
        for pid in hurt:
            L.players[pid].out_weeks = 2
        gd = gameday.build(L, "A")
        named = {pid for names in gd.emergency.values() for pid in names}
        self.assertFalse(named & set(hurt))
        self.assertTrue(set(gd.needed) <= {"P"} or "P" in gd.needed)

    def test_positions_with_nobody_dressed_are_listed_as_needed_and_still_covered(self):
        L = ledger_with({k: v for k, v in FULL.items() if k not in ("K", "LS")})
        gd = gameday.build(L, "A")
        self.assertEqual(sorted(gd.needed), ["K", "LS"])
        self.assertTrue(gd.emergency["K"] and gd.emergency["LS"])
        self.assertNotEqual(gd.emergency["K"][0], gd.emergency["LS"][0])
        self.assertTrue(gd.can_field)

    def test_filing_a_list_does_not_change_anyones_official_position(self):
        L = ledger_with(FULL)
        before = {pid: p.pos for pid, p in L.players.items()}
        gameday.build(L, "A")
        self.assertEqual({pid: p.pos for pid, p in L.players.items()}, before)

    def test_an_empty_dressing_room_files_empty_lists_without_failing(self):
        L = ledger_with({"QB": 5, "WR": 5})
        for pid in L.clubs["A"].roster:
            L.players[pid].out_weeks = 3
        gd = gameday.build(L, "A")
        self.assertTrue(all(v == () for v in gd.emergency.values()))
        self.assertFalse(gd.can_field)


CLUBS = ["C00", "C01"]
POP = Creator(3, load_tables()).founding(2001, CLUBS)


def cards():
    return [copy.deepcopy(c) for c in POP["clubs"]["C00"]["roster"] + POP["clubs"]["C00"]["ps"]]


def squad_with_lists(cs, names=4):
    """A squad whose emergency list is built by the same function the ledger uses, from stand-in player records."""
    class P:
        def __init__(self, c):
            self.pid, self.pos, self.out_weeks = c.identity.id, c.identity.pos, 0
    ps = [P(c) for c in cs]
    return Squad("C00", cs, emergency=gameday.emergency_list(ps, lambda q: 60))


class InTheGame(unittest.TestCase):
    def test_an_empty_position_is_filled_from_the_list_and_nobody_plays_two_places(self):
        rng = random.Random(7)
        for trial in range(300):
            sq = squad_with_lists(cards())
            for c in sq.cards.values():
                if rng.random() < 0.35:
                    sq.out.add(c.identity.id)
            for spec in (OFF, DEF):
                line = sq.lineup(spec)
                ids = [c.identity.id for c in line]
                self.assertEqual(len(ids), len(set(ids)), (trial, spec))          # never one player twice
                self.assertFalse(set(ids) & sq.out, (trial, spec))                # never an injured player
            units = sq.units()
            k, p = units["kicker"], units["punter"]
            if k.identity.id == p.identity.id:                                   # a kicker who also punts (or the reverse) is one
                self.assertIn(k.identity.pos, ("K", "P"))                        # man doing two plays in turn, never two at once, and only
                self.assertTrue(sq.available("K") or sq.available("P"))          # when the club's own kicker or punter is there to be used

    def test_with_no_kicker_the_listed_player_kicks_and_a_second_name_takes_over_when_she_is_hurt(self):
        sq = squad_with_lists(cards())
        for c in sq.cards.values():
            if c.identity.pos == "K":
                sq.out.add(c.identity.id)
        first, second = sq.emergency["K"][0], sq.emergency["K"][1]
        self.assertEqual(sq.units()["kicker"].identity.id, first)
        sq.injure(first, 1)                                                      # hurt during the game
        self.assertEqual(sq.units()["kicker"].identity.id, second)

    def test_kicker_and_punter_both_gone_with_the_same_first_name_gives_two_players(self):
        sq = squad_with_lists(cards())
        for c in sq.cards.values():
            if c.identity.pos in ("K", "P"):
                sq.out.add(c.identity.id)
        sq.emergency["P"] = (sq.emergency["K"][0],) + tuple(sq.emergency["P"])   # force the clash
        u = sq.units()
        self.assertNotEqual(u["kicker"].identity.id, u["punter"].identity.id)

    def test_a_listed_starter_already_on_the_field_is_skipped(self):
        sq = squad_with_lists(cards())
        starter_c = next(c for c in sq.lineup(OFF) if c.identity.pos == "C")
        for c in list(sq.cards.values()):
            if c.identity.pos == "LG":
                sq.out.add(c.identity.id)
        sq.emergency["LG"] = (starter_c.identity.id,) + tuple(sq.emergency["LG"])
        ids = [c.identity.id for c in sq.lineup(OFF)]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids.count(starter_c.identity.id), 1)

    def test_when_every_name_is_exhausted_the_old_fallback_still_fills_the_place(self):
        sq = squad_with_lists(cards(), names=1)
        for c in sq.cards.values():
            if c.identity.pos == "WR":
                sq.out.add(c.identity.id)
        for pid in sq.emergency["WR"]:
            sq.out.add(pid)
        line = sq.lineup(OFF)
        self.assertEqual(len(line), 11)                                          # all 11 offensive places are still filled
        self.assertEqual(len({c.identity.id for c in line}), 11)


if __name__ == "__main__":
    unittest.main()
