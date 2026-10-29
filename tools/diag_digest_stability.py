#!/usr/bin/env python3
"""Bounded stability check of the exhaustive state digest (see PROJECT_COMPLETION_PLAN.md section 15).

Builds a league (4 events), encodes it once, then N times: fragments the allocator with junk tuples, decodes the same document and
compares the copy's `neutral.deep_digest` with the original's. Every copy is equal by construction, so any difference is the digest being
unstable, not the state. `--src` points at another checkout's `src` (for example a copy of an older commit) to compare code versions.

    python3 -I tools/diag_digest_stability.py --iterations 200 --seed 4
"""
import argparse
import collections
import os
import random
import sys

here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ap = argparse.ArgumentParser()
ap.add_argument("--iterations", type=int, default=100)
ap.add_argument("--seed", type=int, default=1)
ap.add_argument("--src", default=os.path.join(here, "src"))
ap.add_argument("--tables", default=os.path.join(here, "data", "season1"))
a = ap.parse_args()
sys.path.insert(0, a.src)
from dfl import neutral  # noqa: E402
from dfl.year import Universe  # noqa: E402

u = Universe(a.tables, seed=1, engine=False)
for _ in range(4):
    u.step_event()
doc = neutral.encode(u)
want = neutral.deep_digest(u)
rng = random.Random(a.seed)
keep, seen = [], collections.Counter()
for _ in range(a.iterations):
    junk = [tuple(range(rng.choice([2, 3, 3, 3, 4, 625, 626]))) for _ in range(rng.randint(100, 6000))]
    rng.shuffle(junk)
    keep.append(junk[:rng.randint(0, len(junk) // 2)])
    del junk
    if len(keep) > 6:
        keep.pop(rng.randrange(len(keep)))
    seen["equal" if neutral.deep_digest(neutral.decode(doc)) == want else "DIFFERENT"] += 1
print(dict(seen))
sys.exit(1 if seen["DIFFERENT"] else 0)
