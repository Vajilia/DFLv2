"""Parity profiles belong to the league that was created with them: nothing a league does may change another league or the process.

Until Edition 8.54's persistence work a profile rewrote `rules.CAP` and `draft.RETURNER_BLOCK` for the whole process, and a league restored
from a file did not re-apply its profile. The cap now lives on the ledger, the returner rule on the universe and its season run, and the
priority clubs on the world. These tests prove the separation: two differently configured leagues run interleaved produce, event for
event, exactly what each produces alone; restoring one while the other runs changes neither; and a state written before the settings were
league-owned is carried forward with the settings its profile named.
"""
import os
import shutil
import sys
import tempfile
import unittest
from fractions import Fraction as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl import draft as D, neutral, parity, persist  # noqa: E402
from dfl.ledger import rules as R  # noqa: E402
from dfl.ledger.world import World  # noqa: E402
from dfl.year import Universe  # noqa: E402

TABLES = os.path.join(ROOT, "data", "season1")
N = 30            # events: past the draft, free agency, camp and into the season


class Profiles(unittest.TestCase):
    def test_settings_are_returned_and_nothing_else_changes(self):
        before = (R.CAP, R.VIABILITY_MAX_COMMITTED)
        for name in parity.NAMES:
            parity.settings(name)
        self.assertEqual((R.CAP, R.VIABILITY_MAX_COMMITTED), before)             # the module constants are never rewritten
        self.assertEqual(parity.settings("tight"), {"cap": R.CAP, "returner_block": True, "priority_clubs": 0})
        self.assertFalse(hasattr(D, "RETURNER_BLOCK"))                           # the returner rule is no longer a module switch

    def test_none_and_rigged_switch_off_the_returner_block_and_the_cap(self):
        ranked = [f"C{i:02d}" for i in range(40)]
        playoff = {c: "wild_card" for c in ranked[:14]}
        ret = [f"R{i}" for i in range(8)]
        for name in ("none", "rigged"):
            s = parity.settings(name)
            self.assertFalse(s["returner_block"])
            self.assertGreater(s["cap"], 100 * R.CAP)
            self.assertEqual(D.base_order(ranked, playoff, ret, "s", ["C39", "C30"], returner_block=s["returner_block"]),
                             D.base_order(ranked, playoff, ret, "s"))
        self.assertNotEqual(D.base_order(ranked, playoff, ret, "s", ["C39", "C30"], returner_block=True), D.base_order(ranked, playoff, ret, "s"))
        self.assertEqual(parity.settings("rigged")["priority_clubs"], 2)

    def test_unknown_profile_is_refused(self):
        with self.assertRaises(ValueError):
            parity.settings("loose")

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


class LeagueOwned(unittest.TestCase):
    def test_each_league_holds_its_own_settings(self):
        tight, none, rigged = (Universe(TABLES, seed=1, engine=False, profile=p) for p in ("tight", "none", "rigged"))
        self.assertEqual([x.world.L.cap for x in (tight, none, rigged)], [R.CAP, F(10 ** 7), F(10 ** 7)])
        self.assertEqual([x.returner_block for x in (tight, none, rigged)], [True, False, False])
        self.assertEqual([len(x.world.priority) for x in (tight, none, rigged)], [0, 0, 2])
        self.assertEqual(R.CAP, F(1000))                                          # building the others did not touch the first or the module

    def test_the_cap_and_returner_rule_reach_the_places_that_use_them(self):
        u = Universe(TABLES, seed=1, engine=False, profile="none")
        self.assertEqual(u.world.L.limit(u.world.L.year), F(10 ** 7) - R.RESERVE - R.REQUIRED_PLACES * R.MIN_SALARY)
        u.step_event()
        while u.run is None:
            u.step_event()
        self.assertIs(u.run.returner_block, False)                                 # the season run carries the league's own rule


class Interleaved(unittest.TestCase):
    """Two leagues configured differently, stepped alternately in one process, against each one alone."""

    @classmethod
    def setUpClass(cls):
        cls.ref = {}
        for name in ("tight", "rigged"):
            u = Universe(TABLES, seed=1, engine=False, profile=name)
            cls.ref[name] = []
            for _ in range(N):
                u.step_event()
                cls.ref[name].append(persist.journal_digest(u))

    def test_the_profiles_really_play_differently(self):
        a = Universe(TABLES, seed=1, engine=False, profile="tight")
        b = Universe(TABLES, seed=1, engine=False, profile="rigged")
        for _ in range(N):
            a.step_event()
            b.step_event()
        self.assertNotEqual(a.world.L.digest(), b.world.L.digest())                  # the ledgers differ: rigged clubs shop first with no cap
        self.assertNotEqual(a.world.L.cap, b.world.L.cap)

    def test_interleaved_leagues_equal_isolated_runs_event_for_event(self):
        a = Universe(TABLES, seed=1, engine=False, profile="tight")
        b = Universe(TABLES, seed=1, engine=False, profile="rigged")                # created after a, the order that broke the old module switch
        for i in range(N):
            a.step_event()
            b.step_event()
            if i % 3 == 2 or i == N - 1:                                             # a digest is the whole state; every third event is enough to catch a leak
                self.assertEqual(persist.journal_digest(a), self.ref["tight"][i], f"tight league altered by the other at event {i + 1}")
                self.assertEqual(persist.journal_digest(b), self.ref["rigged"][i], f"rigged league altered by the other at event {i + 1}")

    def test_restoring_one_league_while_the_other_runs_changes_neither(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "rigged.db")
            r = persist.Runner.create(path, TABLES, seed=1, snapshot_every=5, engine=False, archive=False, profile="rigged")
            r.run_events(12)
            r.close()
            a = Universe(TABLES, seed=1, engine=False, profile="tight")             # a tight league is built and running in the same process
            for _ in range(6):
                a.step_event()
            r = persist.Runner.open(path)                                            # ...when the rigged one is restored
            self.assertEqual((r.u.world.L.cap, r.u.returner_block, len(r.u.world.priority)), (F(10 ** 7), False, 2))
            self.assertEqual(persist.journal_digest(r.u), self.ref["rigged"][11])
            for j in range(N - 12):                                                  # the two now step alternately: rigged from event 13, tight from event 7
                r.step()
                a.step_event()
            self.assertEqual(persist.journal_digest(r.u), self.ref["rigged"][N - 1], "restored league differs from its isolated run")
            self.assertEqual(persist.journal_digest(a), self.ref["tight"][N - 7], "tight league altered by the other's restore")
            self.assertEqual(R.CAP, F(1000))
            r.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class OlderStates(unittest.TestCase):
    """A state written when the settings were module constants has no `cap` or `returner_block`; its profile name says what they were."""

    def test_a_state_without_the_settings_is_given_the_ones_its_profile_named(self):
        for name in ("tight", "rigged"):
            u = Universe(TABLES, seed=1, engine=False, profile=name)
            while u.run is None:
                u.step_event()
            want = parity.settings(name)
            doc = neutral.encode(u)
            self._strip(doc)
            synthesized = set()
            back = neutral.decode(doc, synthesized)
            self.assertEqual((back.world.L.cap, back.returner_block, back.run.returner_block), (want["cap"], want["returner_block"], want["returner_block"]))
            self.assertTrue({"cap", "returner_block"} <= synthesized, synthesized)
            self.assertEqual(neutral.deep_digest(back), neutral.deep_digest(u))

    @staticmethod
    def _strip(node):
        """Remove the league-owned settings from an encoded state, as an older writer would have left them."""
        if isinstance(node, dict):
            if "s" in node and "$o" in node:
                node["s"] = [kv for kv in node["s"] if not (kv[0] in ("cap", "returner_block") and node["$o"].rsplit(".", 1)[-1] in ("Ledger", "Universe", "SeasonRun"))]
            for v in node.values():
                OlderStates._strip(v)
        elif isinstance(node, list):
            for v in node:
                OlderStates._strip(v)


if __name__ == "__main__":
    unittest.main()
