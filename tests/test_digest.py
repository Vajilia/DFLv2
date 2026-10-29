#!/usr/bin/env python3
"""The exhaustive state digest (`neutral.deep_digest`) must see every value, every random generator's state included, and must not
depend on where Python happens to put temporary objects in memory.

History: until this test existed the digest cached results by object address and let the temporary tuples made by
`Random.getstate()` be freed, so a later temporary could reuse the address and inherit the earlier generator's digest. Different
generator states then digested alike, and whether two equal states digested alike depended on the allocator. That produced one
unexplained `StateMismatch` in a full test run (see PROJECT_COMPLETION_PLAN.md section 15).
"""
import copy
import gc
import os
import random
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl import neutral  # noqa: E402

TABLES = os.path.join(ROOT, "data", "season1")


class Plain:
    def __init__(self, **kw):
        self.__dict__.update(kw)


Plain.__module__ = "dfl.testplain"


class Sees(unittest.TestCase):
    def test_different_random_states_digest_differently(self):
        a = [random.Random(1), random.Random(2)]
        b = [random.Random(1), random.Random(3)]
        self.assertNotEqual(neutral.deep_digest(a), neutral.deep_digest(b))

    def test_every_one_of_many_generators_is_covered(self):
        rngs = [random.Random(i) for i in range(40)]
        base = neutral.deep_digest(rngs)
        for i in range(40):
            st = rngs[i].getstate()
            rngs[i].random()
            self.assertNotEqual(neutral.deep_digest(rngs), base, i)
            rngs[i].setstate(st)
        self.assertEqual(neutral.deep_digest(rngs), base)

    def test_generators_inside_objects_dicts_and_sets_are_covered(self):
        def build(k):
            return {"a": Plain(r=random.Random(1), n=1), "b": [Plain(r=random.Random(k), n=2)], "c": (random.Random(5), random.Random(6))}
        self.assertEqual(neutral.deep_digest(build(2)), neutral.deep_digest(build(2)))
        self.assertNotEqual(neutral.deep_digest(build(2)), neutral.deep_digest(build(3)))

    def test_every_generator_of_a_real_league_is_covered(self):
        from dfl.year import Universe
        u = Universe(TABLES, seed=1, engine=False)
        u.step_event()
        found, seen = [], set()

        def walk(o, path):
            if id(o) in seen:
                return
            seen.add(id(o))
            if isinstance(o, random.Random):
                found.append((path, o))
            elif isinstance(o, dict):
                for k, v in o.items():
                    walk(v, f"{path}[{k!r}]")
            elif isinstance(o, (list, tuple)):
                for i, v in enumerate(o):
                    walk(v, f"{path}[{i}]")
            elif hasattr(o, "__dict__"):
                for k, v in vars(o).items():
                    walk(v, f"{path}.{k}")
        walk(u, "u")
        self.assertGreaterEqual(len(found), 6)
        base = neutral.deep_digest(u)
        for path, r in found:
            st = r.getstate()
            r.random()
            self.assertNotEqual(neutral.deep_digest(u), base, f"{path}: its state is not covered by the digest")
            r.setstate(st)


class DoesNotDependOnMemoryLayout(unittest.TestCase):
    def test_an_equal_copy_digests_equal_whatever_the_allocator_is_doing(self):
        def build():
            return {"clubs": [{"cid": i, "rng": random.Random(i), "picks": {(2000, 1, i)}} for i in range(60)],
                    "world": {"rng": random.Random(99), "dev": random.Random(98), "soul": random.Random(97)}}
        original = build()
        want = neutral.deep_digest(original)
        doc = neutral.encode(original)
        rng = random.Random(7)
        keep = []
        bad = 0
        for i in range(300):
            junk = [tuple(range(rng.choice([2, 3, 3, 3, 4, 625, 626]))) for _ in range(rng.randint(50, 3000))]
            rng.shuffle(junk)
            keep.append(junk[:rng.randint(0, len(junk) // 2)])
            del junk
            if len(keep) > 6:
                keep.pop(rng.randrange(len(keep)))
            if i % 17 == 0:
                gc.collect()
            copy_ = neutral.decode(copy.deepcopy(doc))
            bad += neutral.deep_digest(copy_) != want
        self.assertEqual(bad, 0, f"{bad} of 300 equal copies digested differently")

    def test_repeated_digests_of_one_league_state_agree_under_churn(self):
        from dfl.year import Universe
        u = Universe(TABLES, seed=1, engine=False)
        for _ in range(3):
            u.step_event()
        doc = neutral.encode(u)
        want = neutral.deep_digest(u)
        rng = random.Random(11)
        keep = []
        for i in range(12):
            junk = [tuple(range(rng.choice([2, 3, 4, 625, 626]))) for _ in range(rng.randint(100, 6000))]
            rng.shuffle(junk)
            keep.append(junk[:len(junk) // 2])
            del junk
            self.assertEqual(neutral.deep_digest(neutral.decode(doc)), want, i)


if __name__ == "__main__":
    unittest.main()
