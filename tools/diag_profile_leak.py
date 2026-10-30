#!/usr/bin/env python3
"""Does creating a second league, on another parity profile, change the first one? (See docs/REVIEW_HANDOFF_9211b28.md section 3.2.)

Runs a league on the shipped rules alone for N events and records its state digest after each; then runs an identical league alongside a
second league created after it on the `none` profile (no cap), alternating events, and compares the first league with its isolated run.
Exit 1 if it differs. `--src` points at another checkout's `src` to compare code versions: at commit 9211b28 (and 030b51a) it reports a
difference from event 5 (free-agency opening); with league-owned settings it reports none.

    python3 -I tools/diag_profile_leak.py --events 8
    python3 -I tools/diag_profile_leak.py --events 8 --src /path/to/older/checkout/src --tables /path/to/older/checkout/data/season1
"""
import argparse
import os
import sys

here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ap = argparse.ArgumentParser()
ap.add_argument("--events", type=int, default=8)
ap.add_argument("--src", default=os.path.join(here, "src"))
ap.add_argument("--tables", default=os.path.join(here, "data", "season1"))
a = ap.parse_args()
sys.path.insert(0, a.src)
from dfl import persist  # noqa: E402
from dfl.year import Universe  # noqa: E402

digest = getattr(persist, "journal_digest", None) or persist.quick_digest      # the older name, before save schema 4

alone = Universe(a.tables, seed=1, engine=False, profile="tight")
ref = []
for _ in range(a.events):
    alone.step_event()
    ref.append(digest(alone))
first = Universe(a.tables, seed=1, engine=False, profile="tight")
second = Universe(a.tables, seed=2, engine=False, profile="none")        # created after the first
got = []
for _ in range(a.events):
    first.step_event()
    second.step_event()
    got.append(digest(first))
at = next((i + 1 for i, (x, y) in enumerate(zip(got, ref)) if x != y), None)
print(f"src={a.src}: first league {'identical to its isolated run' if at is None else f'ALTERED by the second league from event {at}'}")
sys.exit(0 if at is None else 1)
