#!/usr/bin/env python3
"""Full-league soak (Phase 1 exit test on the whole living league): the real calendar, the engine, free agency, the draft, trades,
waivers, retirement, governance and coaching, N league years, 48 clubs. The ledger audits every invariant at each season start and
year end and aborts on any violation.

  python3 -I tools/run_universe_soak.py --years 100 --seed 1 --report out/universe_soak.md
Exit status 0 only if no invariant failed, no club was forced to forfeit by the rules, and every club always had a legal move.
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl.year import Universe  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--play-level", action="store_true", help="play every game with the play-level engine (fast) instead of the league default, the 22-player engine")
    ap.add_argument("--report")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    u = Universe(os.path.join(ROOT, "data", "season1"), seed=a.seed, spatial=not a.play_level)
    digests, t0 = [], time.time()
    for _ in range(a.years):
        rep = u.play_year()
        digests.append(u.L.digest())
        if a.verbose:
            print(f"year {rep['year']} ok  {time.time() - t0:7.1f}s  {digests[-1][:12]}", flush=True)
    s, w = u.world.stats, u.world
    ok = s["forfeits"] == 0 and s["legal_move_failures"] == 0
    rej = dict(sorted(s["rejections"].items(), key=lambda kv: -kv[1]))
    lines = [
        f"# Full-league soak: {a.years} league years, 48 clubs, seed {a.seed}", "",
        f"- result: {'PASS' if ok else 'FAIL'} (invariants audited at every season start and year end; any violation aborts the run)",
        f"- final digest: `{digests[-1]}`", f"- games: {s['games']}, short-handed: {s['short_games']}, emergency lists: {s['emergency_games']}, forfeits: {s['forfeits']}",
        f"- legal-move failures: {s['legal_move_failures']}", f"- trades: {s['trades']} (rejected {s['trades_rejected']}), waiver claims: {s['claims']}, upgrade signings: {s.get('upgrades', 0)}",
        f"- retirements: {dict(u.L.retired)}",
        f"- elevations: {s['elevations']}, IR moves: {s['ir_moves']}, lowest payroll at the cut: {float(s['payroll_min'] or 0):.1f}, draft picks the club could not sign: {s['unsigned_picks']}",
        f"- overage max in season: {float(s['overage_max']):.1f}, before the final cut: {float(s['offseason_overage_max']):.1f}, committed max: {float(s['committed_max']):.1f}, viability margin min: {float(s['margin_min'] or 0):.1f}",
        f"- run time: {time.time() - t0:.0f}s", "", "## Rejections (the gate working)", ""] + [f"- {v:5d}  {k}" for k, v in rej.items()]
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
