#!/usr/bin/env python3
"""Full-year run (Phase 2 exit test): N complete DFL years, ledger and league together.

  python3 -I tools/run_years.py --years 100 --seed 1 --out out/years100 [--validate-every 1]

Every year fires every event of the calendar once, in date order. Each season's tables are written and checked by
tools/validate.py; the ledger is audited every week and at every season boundary. Exit status = number of problems.
"""
import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl import calendar as C  # noqa: E402
from dfl import league  # noqa: E402
from dfl.year import Universe  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", default=os.path.join(ROOT, "data", "season1"))
    ap.add_argument("--years", type=int, default=100)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default=os.path.join(ROOT, "out", "years"))
    ap.add_argument("--validate-every", type=int, default=1)
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    u = Universe(a.tables, seed=a.seed)
    problems, t0 = [], time.time()
    os.makedirs(a.out, exist_ok=True)
    for n in range(1, a.years + 1):
        rep = u.play_year()
        year = rep["year"]
        want = [(d.isoformat(), k) for d, k, *_ in C.year_events(year)]
        got = [(d, k) for y, d, k in u.fired if y == year]
        if got != want:
            problems.append((year, "calendar events did not fire exactly once in order"))
        if n % a.validate_every == 0:
            d = f"{a.out}/tables/s{n:03d}"
            r = rep["result"]
            league.write_tables(u.tpl, d, r["teams"], r["schedule"], r["tour"])
            p = subprocess.run([sys.executable, "-I", f"{ROOT}/tools/validate.py", d, "--season", str(n)], capture_output=True, text=True)
            if p.returncode:
                problems.append((year, [l for l in p.stdout.splitlines() if l.startswith("FAIL")]))
        if a.verbose:
            print(f"{year} ok {time.time() - t0:7.1f}s winner={rep['winner']} comps={rep['comps']} {rep['digest'][:10]}", flush=True)
    s = u.world.stats
    lines = [
        f"# Full-year run: {a.years} years, seed {a.seed}", "",
        f"- result: {'PASS' if not problems and s['forfeits'] == 0 and s['legal_move_failures'] == 0 else 'FAIL'}",
        f"- years: {a.years}; events fired: {len(u.fired)} ({len(u.fired) // a.years} a year, each once, in order)",
        f"- league tables validated: {a.years // a.validate_every}; validator failures: {sum(1 for _, p in problems if isinstance(p, list))}",
        f"- games in the ledger: {s['games']}; forfeits: {s['forfeits']}; short-handed: {s['short_games']}; emergency lists: {s['emergency_games']}",
        f"- legal-move failures: {s['legal_move_failures']}; draft picks made: {s.get('picks_made', 0)}, unsigned: {s['unsigned_picks']}",
        f"- trades: {s['trades']} (refused {s['trades_rejected']}); waiver claims: {s['claims']}; compensatory picks awarded: {sum(r['comps'] for r in u.reports)}",
        f"- Coronation venues: {len({r['venue'] for r in u.reports})} distinct; final digest `{u.reports[-1]['digest']}`",
        f"- run time: {time.time() - t0:.0f}s",
    ]
    if problems:
        lines += ["", "## Problems"] + [f"- {y}: {p}" for y, p in problems]
    out = "\n".join(lines) + "\n"
    print(out)
    with open(f"{a.out}/report.md", "w") as f:
        f.write(out)
    sys.exit(len(problems) + s["forfeits"] + s["legal_move_failures"])


if __name__ == "__main__":
    main()
