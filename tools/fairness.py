#!/usr/bin/env python3
"""Fairness bands on a living league (Phase 4).

  python3 -I tools/fairness.py run --seed 1 --years 36 --out out/fair/seed1.json     # one league: season records
  python3 -I tools/fairness.py report out/fair/seed*.json [--warmup 8] [--md out/fair/report.md]

`run` plays a whole league (cards, development, draft, free agency, the engine) and writes compact season records. `report` reads one
or more leagues and prints every band with its measured value, error bar and verdict (PASS / FAIL / UNCLEAR). Leagues are independent
seeds, so they can be run side by side and the report rerun as more arrive.
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl import fairness as F  # noqa: E402


def run(a):
    from dfl.year import Universe
    u = Universe(os.path.join(ROOT, "data", "season1"), seed=a.seed)
    seasons, t0 = [], time.time()
    for _ in range(a.years):
        rep = u.play_year()
        w = u.world
        strength = {c: F.club_strength(w, c) for c in rep["result"]["outcome"]["records"]}
        seasons.append(F.season_record(rep["year"], rep, strength))
        print(f"seed {a.seed} {rep['year']} {time.time() - t0:.0f}s", flush=True)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as f:
        json.dump({"seed": a.seed, "seasons": seasons}, f)


def report(a):
    leagues = []
    for p in a.files:
        with open(p) as f:
            leagues.append(json.load(f)["seasons"])
    rows = F.verdicts(leagues, warmup=a.warmup)
    text = (f"Fairness bands: {len(leagues)} league(s), {len(leagues[0])} seasons each, first {a.warmup} dropped as warm-up.\n\n"
            + F.format_table(rows) + "\n")
    print(text)
    if a.md:
        with open(a.md, "w") as f:
            f.write(text)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--seed", type=int, default=1)
    r.add_argument("--years", type=int, default=36)
    r.add_argument("--out", required=True)
    p = sub.add_parser("report")
    p.add_argument("files", nargs="+")
    p.add_argument("--warmup", type=int, default=F.WARMUP)
    p.add_argument("--md")
    a = ap.parse_args()
    run(a) if a.cmd == "run" else report(a)


if __name__ == "__main__":
    main()
