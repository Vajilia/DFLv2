"""The contract release-charge memo (src/dfl/ledger/contracts.py) is derived data: bounded, never written to a snapshot, never able to change an answer.

Proved: every memoized answer equals the plain computation for every year, week and flag combination, including asking out of time order; sweeping all
weeks of all covered years leaves at most (covered years x 4 flag combinations) entries; a pickled or deep-copied contract carries no memo and still
equals the original (and hashes the same); a state written by older code, whose memo is full, loads with the memo empty; the state digest is the same
with a full memo and with none."""
import copy
import itertools
import os
import pickle
import sys
import unittest
from fractions import Fraction as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _diag  # noqa: F401,E402
from dfl import neutral  # noqa: E402
from dfl.ledger import rules as R  # noqa: E402
from dfl.ledger.contracts import Contract  # noqa: E402

FLAGS = list(itertools.product((False, True), (True, False)))


def contracts():
    return [Contract(2000, (F(100), F(110), F(120), F(130), F(140)), (F(50), F(60), F(0), F(0), F(0)), ((F(80), 0, 4),), 3, "rising"),
            Contract(2001, (F(90),), (), (), 0, "steady"),
            Contract(2002, (F(200), F(180), F(160)), (F(200), F(100), F(0)), ((F(60), 0, 3), (F(30), 1, 2)), 10, "declining")]


class Memo(unittest.TestCase):
    def test_every_memoized_answer_equals_the_plain_computation_in_any_order(self):
        weeks = list(range(0, R.PAID_WEEKS + 1))
        for c in contracts():
            asks = [(y, w, s, g) for y in range(c.start_year, c.end_year + 1) for w in weeks for s, g in FLAGS]
            for order in (asks, asks[::-1], asks[::7] + asks[3::5] + asks):          # forward, backward, shuffled-ish with repeats
                for y, w, s, g in order:
                    self.assertEqual(c.release_charges(y, w, s, g), c._release_charges(y, w, s, g), (y, w, s, g))

    def test_the_memo_stays_small_however_many_weeks_are_asked(self):
        for c in contracts():
            for y in range(c.start_year, c.end_year + 1):
                for w in range(0, R.PAID_WEEKS + 1):
                    for s, g in FLAGS:
                        c.release_charges(y, w, s, g)
            self.assertLessEqual(len(c._memo), len(c.years) * len(FLAGS), len(c._memo))

    def test_repeated_questions_within_one_week_are_answered_from_the_memo(self):
        c = contracts()[0]
        c.release_charges(2001, 5)
        calls = []
        orig = c._release_charges
        object.__setattr__(c, "_release_charges", lambda *a, **k: calls.append(a) or orig(*a, **k))
        for _ in range(3):
            c.release_charges(2001, 5)
        self.assertEqual(calls, [])

    def test_a_pickled_or_copied_contract_carries_no_memo_and_still_equals_the_original(self):
        for c in contracts():
            c.release_charges(c.start_year, 7)
            self.assertTrue(c._memo)
            for d in (pickle.loads(pickle.dumps(c, protocol=5)), copy.deepcopy(c), pickle.loads(pickle.dumps(c))):
                self.assertEqual(d._memo, {})
                self.assertEqual(d, c)
                self.assertEqual(hash(d), hash(c))
                self.assertEqual(d.release_charges(c.start_year, 7), c.release_charges(c.start_year, 7))
            self.assertTrue(c._memo, "writing a snapshot must not empty the live memo")
        self.assertLess(len(pickle.dumps(contracts()[0])), len(pickle.dumps(_full(contracts()[0]))) + 1)

    def test_a_state_written_by_older_code_with_a_full_memo_loads_empty(self):
        c = contracts()[0]
        state = dict(c.__dict__)
        state["_memo"] = {(2000, w, False, True): {2000: F(w)} for w in range(40)}
        fresh = Contract.__new__(Contract)
        fresh.__setstate__(state)
        self.assertEqual(fresh._memo, {})
        self.assertEqual(fresh, c)
        self.assertEqual(fresh.release_charges(2000, 5), c._release_charges(2000, 5))

    def test_the_state_digest_does_not_see_the_memo(self):
        c = contracts()[0]
        empty = neutral.deep_digest(c)
        full = _full(contracts()[0])
        self.assertTrue(full._memo)
        self.assertEqual(neutral.deep_digest(full), empty)


def _full(c):
    for y in range(c.start_year, c.end_year + 1):
        for w in range(0, 3):
            for s, g in FLAGS:
                c.release_charges(y, w, s, g)
    return c


if __name__ == "__main__":
    unittest.main()
