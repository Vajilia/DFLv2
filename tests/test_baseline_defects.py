"""Regression tests for two defects of the baseline code, found by a screen of disposable test seeds (docs/BASELINE_DEFECTS_2026-11-22.md).

1. Founding Day could build a cap-illegal club: every founding rookie's salary round is drawn at random with no cap check, so on about 2 seeds
   in 100 a club's all-rookie 53 plus its squad of 16 cannot all be signed and creating the league raises "breaks the cap".
2. Free agency ended a club's whole period the first time the randomly chosen position had no suitable free agent.

Disposable leagues only: seeds in the block reserved for tests (7771000 to 7771999). None may ever be used for a league someone watches.
"""
import os
import sys
import unittest
from fractions import Fraction as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl import neutral  # noqa: E402
from dfl.ledger import rules as R  # noqa: E402
from dfl.ledger.world import TEMPLATE  # noqa: E402
from dfl.year import Universe  # noqa: E402

TABLES = os.path.join(ROOT, "data", "season1")
FAILING_FOUNDING_SEED = 7771413          # raised "sign: breaks the cap for Tampa Bay" on 4a1f26e
# Founding digests produced by the baseline code (4a1f26e) for seeds that founded without trouble: the fix must not move them.
BASELINE_FOUNDING = {7771400: "f53254562c6b90fb400814e81b59b93113a332a5384a5b68ba0eb1ce576452c1",
                     7771401: "b1bfda8c745d0e629da68542251132c70f9fc23f679291ef421610f0f376545e",
                     7771405: "891ef23e0b0d86822741b35296a41dce17fd28be7d5573940742e939fd55325c"}


def fast(seed, **kw):
    return Universe(TABLES, seed=seed, engine=False, spatial=False, **kw)


class TestFounding(unittest.TestCase):
    def test_a_seed_that_used_to_fail_founds_a_legal_league(self):
        u = fast(FAILING_FOUNDING_SEED)
        L = u.world.L
        self.assertEqual(len(L.clubs), 48)
        for cid, c in L.clubs.items():
            self.assertEqual((len(c.roster), len(c.ps)), (53, 16), cid)
            self.assertLessEqual(L.cap_number(cid), L.cap, cid)
            self.assertEqual(L.overage(cid), 0, cid)

    def test_every_founding_contract_is_a_rookie_scale_contract(self):
        u = fast(FAILING_FOUNDING_SEED)
        L = u.world.L
        for p in L.players.values():
            if p.club is None:
                continue
            k = p.contract
            self.assertEqual(len(k.years), R.ROOKIE_YEARS)
            self.assertIn(k.years[0], R.ROOKIE_SCALE)
            self.assertEqual(set(k.years), {k.years[0]})
            self.assertEqual((k.guaranteed, k.bonuses), ((), ()))

    def test_seeds_that_already_founded_are_unchanged(self):
        for seed, digest in BASELINE_FOUNDING.items():
            with self.subTest(seed=seed):
                self.assertEqual(neutral.deep_digest(fast(seed)), digest)

    def test_the_salary_round_is_lowered_only_as_far_as_the_cap_requires(self):
        """Direct test of the rule: the drawn round stands if every signing still to come can be made at the cheapest scale within the cap;
        otherwise the first round at or after it that fits is used."""
        w = fast(7771400).world
        c = "Sacramento"
        w.L.cap_number = lambda cid: F(990)
        base = lambda rnd, after: w._founding_contract(c, rnd, after).years[0]          # noqa: E731
        self.assertEqual(base(1, 0), R.ROOKIE_SCALE[4])            # 990 + 25, 18, 14, 11 break the cap; 9 fits
        self.assertEqual(base(5, 0), R.ROOKIE_SCALE[4])            # a round that fits is kept
        self.assertEqual(base(1, 1), R.ROOKIE_SCALE[6])            # one more signing to come at the minimum: only the cheapest scale leaves room
        self.assertEqual(base(7, 1), R.ROOKIE_SCALE[6])
        w.L.cap_number = lambda cid: F(500)
        for rnd in range(1, 8):
            self.assertEqual(base(rnd, 20), R.ROOKIE_SCALE[rnd - 1])           # plenty of room: the drawn round is used


class _Stop(Exception):
    pass


def before_free_agency(seed):
    """A disposable league stopped at the moment free agency is about to run (the state it would start from)."""
    u = fast(seed)
    w = u.world

    def stop():
        raise _Stop
    w.free_agency = stop
    try:
        for _ in range(400):
            u.step_event()
    except _Stop:
        pass
    del w.free_agency
    return u


class TestFreeAgencyDoesNotEndOnOneEmptyPosition(unittest.TestCase):
    EMPTY, OTHER = "MLB", "CB"

    def run_period(self, seed, empty_first):
        """Every club is made to need two positions, one of which has no suitable free agent, and the first draw is always the empty one (patches on
        this disposable league only). Returns the number of loop signings per club and the number of clubs that could sign."""
        from collections import Counter
        w = before_free_agency(seed).world
        L = w.L
        real_fas = w.fas
        w.fas = lambda pos=None, cid=None: [] if pos == self.EMPTY else real_fas(pos, cid)
        w.need = lambda cid, slots=("roster",): [(F(1), self.EMPTY), (F(1), self.OTHER)]
        w.rng.choice = (lambda seq: self.EMPTY if self.EMPTY in seq else seq[0]) if empty_first else w.rng.choice
        signed = Counter()
        orig = w.try_sign

        def spy(cid, p, *a, **k):
            ok = orig(cid, p, *a, **k)
            if ok and "value" in k:                       # a signing made by the free-agency loop (the minimums rule passes a contract instead)
                signed[cid] += 1
            return ok
        w.try_sign = spy
        able = [cid for cid in L.clubs if len(L.clubs[cid].roster) + len(L.clubs[cid].ps) < R.PRESEASON_CEILING - 9
                and L.cap_number(cid) < L.cap - R.RESERVE - F(90)]
        w.free_agency()
        return signed, able

    def test_a_position_with_no_suitable_candidate_does_not_end_the_clubs_period(self):
        signed, able = self.run_period(7771501, empty_first=True)
        self.assertGreater(len(able), 10, "the fixture must have clubs with room and a payroll under the spending floor")
        stuck = [cid for cid in able if signed[cid] == 0]
        self.assertEqual(stuck, [], f"{len(stuck)} of {len(able)} clubs stopped on the empty position")

    def test_the_period_still_ends_when_no_needed_position_has_a_candidate(self):
        u = before_free_agency(7771502)
        w = u.world
        real_fas = w.fas
        w.fas = lambda pos=None, cid=None: [p for p in real_fas(pos, cid) if w.view(cid, p) <= 60] if cid else real_fas(pos, cid)       # nobody the loop would take
        w.need = lambda cid, slots=("roster",): [(F(1), "MLB"), (F(1), "CB")]
        w.free_agency()                                   # terminates (no endless loop), signing nothing in the loop
        self.assertTrue(all(len(c.roster) + len(c.ps) <= R.PRESEASON_CEILING for c in w.L.clubs.values()))


if __name__ == "__main__":
    unittest.main()
