#!/usr/bin/env python3
"""Living-league health: play N years with cards, development and the engine, and report what the league looks like.

  python3 -I tools/league_health.py --years 30 --seed 1 --out out/health [--tables data/season1]

Per year it records talent (mean hidden overall of the 53, of the starters by position, of each age band), ages, retirements,
the pool, the spread of club strength, win% spread, ties, exiles and champions. Nothing is tuned from it: it is the measuring stick
for the fairness work (Phase 4) and shows whether the placeholder development data hold the league steady.
"""
import argparse
import csv
import os
import statistics as S
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl.engine.squad import OFF, DEF  # noqa: E402
from dfl.year import Universe  # noqa: E402

STARTERS = OFF + DEF + (("K", 1), ("P", 1))


def club_strength(w, cid):
    """Mean hidden overall of the best starters at each position (what the engine would field when healthy)."""
    by = {}
    for pid in w.L.clubs[cid].roster:
        by.setdefault(w.L.players[pid].pos, []).append(w.rating[pid])
    vals = []
    for pos, n in STARTERS:
        vals += sorted(by.get(pos, []), reverse=True)[:n]
    return S.mean(vals) if vals else 0.0


def snapshot(u, rep):
    w, L = u.world, u.L
    rost = [w.rating[pid] for c in L.clubs.values() for pid in c.roster]
    ages = [L.players[pid].age for c in L.clubs.values() for pid in c.roster]
    band = lambda lo, hi: [w.rating[pid] for c in L.clubs.values() for pid in c.roster if lo <= L.players[pid].age <= hi]
    strength = [club_strength(w, cid) for cid in L.clubs]
    pool = [p for p in L.players.values() if p.where == "fa"]
    out = rep["result"]["outcome"]
    rec = out["records"]
    pct = {c: r.w / max(1, r.w + r.l + r.t) for c, r in rec.items()}
    return {
        "year": rep["year"], "roster_mean": S.mean(rost), "roster_age": S.mean(ages),
        "age_le24": S.mean(band(0, 24) or [0]), "age_25_29": S.mean(band(25, 29) or [0]), "age_30p": S.mean(band(30, 99) or [0]),
        "club_strength_mean": S.mean(strength), "club_strength_sd": S.pstdev(strength),
        "pool_size": len(pool), "pool_mean": S.mean(p_ovr(w, p) for p in pool) if pool else 0,
        "retired_total": sum(L.retired.values()), "players_in_ledger": len(L.players),
        "win_pct_sd": S.pstdev(pct.values()), "points_per_team_game": S.mean(r.pf / max(1, r.gp) for r in rec.values()), "ties": sum(r.t for r in rec.values()) // 2,
        "exiled": len(rep["exiled"]), "winner": rep["winner"],
        "strength_vs_win_corr": corr([club_strength(w, c) for c in sorted(pct)], [pct[c] for c in sorted(pct)]),
    }


def p_ovr(w, p):
    return w.rating[p.pid]


def corr(x, y):
    mx, my = S.mean(x), S.mean(y)
    sx, sy = S.pstdev(x), S.pstdev(y)
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (len(x) * sx * sy) if sx and sy else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", default=os.path.join(ROOT, "data", "season1"))
    ap.add_argument("--years", type=int, default=30)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default=os.path.join(ROOT, "out", "health"))
    a = ap.parse_args()
    u = Universe(a.tables, seed=a.seed)
    os.makedirs(a.out, exist_ok=True)
    rows, t0 = [], time.time()
    for _ in range(a.years):
        rep = u.play_year()
        rows.append(snapshot(u, rep))
        r = rows[-1]
        print(f"{r['year']} roster {r['roster_mean']:.1f} age {r['roster_age']:.1f} strength {r['club_strength_mean']:.1f}±{r['club_strength_sd']:.2f} "
              f"ppg {r['points_per_team_game']:.1f} win%sd {r['win_pct_sd']:.3f} corr {r['strength_vs_win_corr']:.2f} pool {r['pool_size']} retired {r['retired_total']} "
              f"{time.time() - t0:.0f}s", flush=True)
    with open(f"{a.out}/health.csv", "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0]))
        wr.writeheader()
        wr.writerows(rows)
    champs = {}
    for r in rows:
        champs[r["winner"]] = champs.get(r["winner"], 0) + 1
    print(f"distinct champions {len(champs)}, most titles {max(champs.values())}; mean win% sd {S.mean(r['win_pct_sd'] for r in rows):.3f}; "
          f"mean ties/season {S.mean(r['ties'] for r in rows):.1f}; mean exiles/season {S.mean(r['exiled'] for r in rows):.1f}")


if __name__ == "__main__":
    main()
