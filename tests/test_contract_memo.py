"""The contract release-charge memo (src/dfl/ledger/contracts.py) is derived data: bounded, never written to a snapshot, never able to change an answer.

Proved: every memoized answer equals the plain computation for every year, week and flag combination, including asking out of time order; sweeping all
weeks of all covered years leaves at most (covered years x 4 flag combinations) entries; a pickled or deep-copied contract carries no memo and still
equals the original (and hashes the same); a state written by older code, whose memo is full, loads with the memo empty; the state digest is the same
with a full memo and with none."""
import copy
import importlib.util
import itertools
import os
import pickle
import shutil
import sys
import tempfile
import unittest
from fractions import Fraction as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _diag  # noqa: F401,E402
from dfl import neutral, persist  # noqa: E402
from dfl.ledger import rules as R  # noqa: E402
from dfl.ledger.contracts import Contract  # noqa: E402

FLAGS = list(itertools.product((False, True), (True, False)))
TABLES = os.path.join(ROOT, "data", "season1")
BEFORE = os.path.join(ROOT, "tests", "fixtures", "contracts_before_memo.py")      # src/dfl/ledger/contracts.py as it was before the memo change


def old_contracts_module():
    """The contracts module exactly as it was before the memo change, loaded beside the current one (same package, so its relative import works)."""
    spec = importlib.util.spec_from_file_location("dfl.ledger._contracts_before_memo", BEFORE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def all_contracts(root):
    """Every Contract reachable from `root`, once each."""
    seen, stack, out = set(), [root], []
    while stack:
        o = stack.pop()
        if id(o) in seen or isinstance(o, (str, bytes, int, float, bool, type(None), F)):
            continue
        seen.add(id(o))
        if isinstance(o, Contract):
            out.append(o)
        elif isinstance(o, dict):
            stack.extend(o.keys()); stack.extend(o.values())
        elif isinstance(o, (list, tuple, set, frozenset)):
            stack.extend(o)
        elif hasattr(o, "__dict__"):
            stack.extend(vars(o).values())
    return out


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


class LoadedLeague(unittest.TestCase):
    """A real league saved with a snapshot, closed, and loaded again: the explicit acceptance checks for the cache change."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="dflmemo")
        path = os.path.join(cls.tmp, "l.db")
        r = persist.Runner.create(path, TABLES, seed=1, snapshot_every=5, spatial=False)
        r.run_events(20)                           # past the first game weeks: contracts exist and release charges have been asked
        cls.live = all_contracts(r.u)
        cls.live_with_memo = sum(1 for c in cls.live if c._memo)
        r.close()
        cls.path = path

    def setUp(self):
        # a fresh load for every test: the checks below ask questions of the contracts, which is exactly what fills the caches again
        self.loaded_runner = persist.Runner.open(self.path, heal=False)
        self.loaded = all_contracts(self.loaded_runner.u)

    def tearDown(self):
        self.loaded_runner.close()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_while_the_league_runs_the_caches_are_in_use_and_after_loading_every_one_is_empty(self):
        self.assertGreater(len(self.loaded), 1000)
        self.assertGreater(self.live_with_memo, 0, "the running league used the caches (otherwise this test proves nothing)")
        self.assertEqual(self.loaded_runner.seq, 20)
        full = [c for c in self.loaded if c._memo]
        self.assertEqual(full, [], f"{len(full)} of {len(self.loaded)} contracts came back from the file with a cache")

    def test_the_caches_rebuild_on_the_first_question_and_every_answer_equals_the_plain_computation(self):
        asked = 0
        for c in self.loaded:
            self.assertEqual(c._memo, {})
            for y in range(c.start_year, c.end_year + 1):
                for w in (0, 1, R.PAID_WEEKS // 2, R.PAID_WEEKS):
                    for s, g in FLAGS:
                        self.assertEqual(c.release_charges(y, w, s, g), c._release_charges(y, w, s, g))
                        asked += 1
            self.assertTrue(c._memo, "the first questions after a load fill the cache again")
            self.assertLessEqual(len(c._memo), len(c.years) * len(FLAGS))
        self.assertGreater(asked, 10000)

    def test_every_answer_is_identical_to_the_one_the_code_before_the_change_gives(self):
        old = old_contracts_module()
        self.assertNotIn("__setstate__", vars(old.Contract), "the fixture is the code from before the change (its cache is pickled and unbounded)")
        n = 0
        for c in self.loaded:
            o = old.Contract(c.start_year, c.years, c.guaranteed, c.bonuses, c.start_week, c.shape, c.option)
            for y in range(c.start_year, c.end_year + 1):
                for w in range(0, R.PAID_WEEKS + 1, 3):
                    for s, g in FLAGS:
                        self.assertEqual(c.release_charges(y, w, s, g), o.release_charges(y, w, s, g), (c, y, w, s, g))
                        n += 1
            a, b = c.assumed_by_receiver(c.start_year, 2), o.assumed_by_receiver(c.start_year, 2)      # (two different classes: compare their fields)
            self.assertEqual({k: v for k, v in vars(a).items() if k != "_memo"}, {k: v for k, v in vars(b).items() if k != "_memo"})
        self.assertGreater(n, 50000)

    def test_a_save_after_the_rebuild_still_carries_no_cache_and_the_league_digest_is_unchanged(self):
        before = persist.deep_digest(self.loaded_runner.u)
        for c in self.loaded[:300]:
            c.release_charges(c.start_year, 4)
        self.assertEqual(persist.deep_digest(self.loaded_runner.u), before)
        again = pickle.loads(pickle.dumps(self.loaded_runner.u, protocol=5))
        self.assertEqual([c for c in all_contracts(again) if c._memo], [])
        self.assertEqual(persist.deep_digest(again), before)


def _full(c):
    for y in range(c.start_year, c.end_year + 1):
        for w in range(0, 3):
            for s, g in FLAGS:
                c.release_charges(y, w, s, g)
    return c


if __name__ == "__main__":
    unittest.main()
