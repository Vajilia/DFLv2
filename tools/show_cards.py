"""Print sample character cards (developer view) and summary statistics for a draft class.  Usage: show_cards.py [--seed N] [--year Y]"""
import argparse
import os
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from dfl.characters import Creator  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--seed", type=int, default=1)
ap.add_argument("--year", type=int, default=2001)
a = ap.parse_args()
C = Creator(a.seed)
cl = C.draft_class(a.year)
print(f"Draft class {a.year}: {len(cl)} prospects, quality shift {C.class_quality(a.year):+.2f}")
by = {}
for c in cl:
    by.setdefault(c.identity.pos, []).append(c)
print(f"{'pos':5}{'n':>4}{'ovr mean':>10}{'best':>6}{'age':>6}{'ht':>5}{'kg':>5}")
for p in sorted(by):
    g = by[p]
    o = [c.true_overall(C.t) for c in g]
    print(f"{p:5}{len(g):>4}{statistics.mean(o):>10.1f}{max(o):>6.0f}{statistics.mean(c.age(a.year) for c in g):>6.1f}"
      f"{statistics.mean(c.identity.height_cm for c in g):>5.0f}{statistics.mean(c.identity.weight_kg for c in g):>5.0f}")
print()
for pos in ("QB", "LT", "CB", "K"):
    print(max(by[pos], key=lambda c: c.true_overall(C.t)).describe(a.year, hidden=True), "\n")
for role in ("ceo", "gm", "coach"):
    print(C.leader(role, a.year).describe(a.year, hidden=True), "\n")
