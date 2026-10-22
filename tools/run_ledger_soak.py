#!/usr/bin/env python3
"""Ledger soak: N league years of 48 autopilot clubs on the placeholder world; audits invariants every year.

  python3 -I tools/run_ledger_soak.py --years 100 --seed 1 --report out/ledger_soak.md
Exit status 0 only if no invariant failed, no club was forced to forfeit by the rules, and every club always had a legal move.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from dfl.ledger.world import World  # noqa: E402


def run(years, seed, clubs=48, reckless=2, verbose=False):
    cids = [f"C{i:02d}" for i in range(1, clubs + 1)]
    w = World(cids, seed=seed, reckless=cids[:reckless])
    digests = []
    t0 = time.time()
    for y in range(years):
        w.step_year(first=(y == 0))
        digests.append(w.L.digest())
        if verbose:
            print(f"year {w.L.year} ok  {time.time() - t0:6.1f}s  {digests[-1][:12]}", flush=True)
    return w, digests, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=12)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--clubs", type=int, default=48)
    ap.add_argument("--reckless", type=int, default=2, help="clubs that load up on bonus-heavy deals")
    ap.add_argument("--report")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    w, digests, secs = run(a.years, a.seed, a.clubs, a.reckless, a.verbose)
    s = w.stats
    rej = dict(sorted(s["rejections"].items(), key=lambda kv: -kv[1]))
    ok = s["forfeits"] == 0 and s["legal_move_failures"] == 0
    lines = [
        f"# Ledger soak: {a.years} league years, {a.clubs} clubs, seed {a.seed}", "",
        f"- result: {'PASS' if ok else 'FAIL'} (invariants audited every year; any violation aborts the run)",
        f"- final digest: `{digests[-1]}`", f"- games: {s['games']}, short-handed: {s['short_games']}, emergency lists: {s['emergency_games']}, forfeits: {s['forfeits']}",
        f"- legal-move failures: {s['legal_move_failures']}", f"- trades: {s['trades']} (rejected {s['trades_rejected']}), waiver claims: {s['claims']}",
        f"- elevations: {s['elevations']}, IR moves: {s['ir_moves']}, floor supplements: {s['floor_supplements']}",
        f"- overage max: {float(s['overage_max']):.1f}, committed max: {float(s['committed_max']):.1f}, viability margin min: {float(s['margin_min'] or 0):.1f}",
        f"- run time: {secs:.0f}s", "", "## Rejections (the gate working)", ""] + [f"- {v:5d}  {k}" for k, v in rej.items()]
    out = "\n".join(lines) + "\n"
    print(out)
    if a.report:
        os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
        with open(a.report, "w") as f:
            f.write(out)
        with open(os.path.splitext(a.report)[0] + ".digests.json", "w") as f:
            json.dump(digests, f)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
