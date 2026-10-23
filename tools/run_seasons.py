#!/usr/bin/env python3
"""Multi-season run: python3 tools/run_seasons.py [--tables data/season1] [--out runs/latest] [--seasons 100]
[--seed 1] [--validate-every 1].  Plays N seasons with the placeholder resolver, validates each season's tables
with tools/validate.py, and writes report.md and report.json.  Exit code = number of problems found."""
import argparse
import json
import os
import random
import statistics
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl import league  # noqa: E402
from dfl.resolver import PlaceholderResolver  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", default=os.path.join(ROOT, "data", "season1"))
    ap.add_argument("--out", default=os.path.join(ROOT, "runs", "latest"))
    ap.add_argument("--seasons", type=int, default=100)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--validate-every", type=int, default=1)
    ap.add_argument("--engine", action="store_true", help="play games with the game engine (character-card squads) instead of the placeholder")
    a = ap.parse_args()
    tpl = league.Template(a.tables)
    state = tpl.initial_state()
    rng = random.Random(a.seed)
    if a.engine:
        from dfl.characters import Creator
        from dfl.engine.resolver import EngineResolver
        from dfl.engine.squad import Squad
        clubs = sorted({r["team_city"] for r in tpl.teams}) if hasattr(tpl, "teams") else None
        C = Creator(a.seed)
        pop = C.founding(2001, clubs)
        resolver = EngineResolver({c: Squad(c, pop["clubs"][c]["roster"] + pop["clubs"][c]["ps"]) for c in clubs})
    else:
        resolver = PlaceholderResolver()
    history, problems = {}, []
    wins_sd, ties, exiles, cor_venues, champs = [], [], {}, [], {}
    prev_pct, yoy_x, yoy_y, pct_sd = {}, [], [], []
    cor_home, seed1_titles = 0, 0
    os.makedirs(a.out, exist_ok=True)
    for season in range(1, a.seasons + 1):
        res, state = league.play_season(tpl, state, season, resolver, rng, history, seed=f"run{a.seed}")
        out = res["outcome"]
        if season % a.validate_every == 0:
            d = f"{a.out}/tables/s{season:03d}"
            league.write_tables(tpl, d, res["teams"], res["schedule"], res["tour"])
            p = subprocess.run([sys.executable, "-I", f"{ROOT}/tools/validate.py", d, "--season", str(season)],
                               capture_output=True, text=True)
            if p.returncode:
                problems.append((season, [l for l in p.stdout.splitlines() if l.startswith("FAIL")]))
        rec = out["records"]
        wins_sd.append(statistics.pstdev([r.w for r in rec.values()]))
        pct = {c: r.w / max(1, r.w + r.l + r.t) for c, r in rec.items()}
        pct_sd.append(statistics.pstdev(pct.values()))
        for c, v in pct.items():
            if c in prev_pct:
                yoy_x.append(prev_pct[c]); yoy_y.append(v)
        prev_pct = pct
        ties.append(sum(r.t for r in rec.values()) // 2)
        for c in out["exiled"].values():
            exiles[c] = exiles.get(c, 0) + 1
        cor_venues.append(res["coronation_venue"])
        cor_home += res["coronation_home_club"] is not None
        champ = res["coronation_winner"]
        champs[champ] = champs.get(champ, 0) + 1
        seed1_titles += champ in (out["seeds"]["Western"][0], out["seeds"]["Eastern"][0])
    n = a.seasons
    gaps_ok = all(b - a_ >= 40 for v in set(cor_venues)
                  for a_, b in zip(*(lambda ys: (ys, ys[1:]))([i for i, x in enumerate(cor_venues) if x == v])))
    report = {
        "seasons": n, "seed": a.seed, "validated_seasons": n // a.validate_every, "validator_failures": len(problems),
        "win_pct_sd_mean": round(statistics.mean(pct_sd), 3),
        "win_pct_year_to_year_corr": round(statistics.correlation(yoy_x, yoy_y), 3) if len(yoy_x) > 2 else None,
        "ties_per_season_mean": round(statistics.mean(ties), 2), "wins_sd_mean": round(statistics.mean(wins_sd), 2),
        "exiles_per_club": {"min": min(exiles.values()), "max": max(exiles.values()), "total": sum(exiles.values())},
        "clubs_never_exiled": 48 - len(exiles) if len(exiles) < 48 else 0,
        "champions_distinct": len(champs), "champion_max_titles": max(champs.values()),
        "top_seed_in_final_pct": round(100 * seed1_titles / n, 1),
        "coronation_distinct_venues": len(set(cor_venues)), "coronation_40_year_rule_ok": gaps_ok,
        "coronation_finalist_at_home_pct": round(100 * cor_home / n, 1),
    }
    json.dump(report, open(f"{a.out}/report.json", "w"), indent=2)
    with open(f"{a.out}/report.md", "w") as f:
        f.write(f"# Multi-season run ({'game engine' if a.engine else 'placeholder resolver'})\n\n")
        for k, v in report.items():
            f.write(f"- {k}: {v}\n")
        for s, fl in problems[:10]:
            f.write(f"\nFAIL season {s}: {fl}\n")
    print(open(f"{a.out}/report.md").read())
    return len(problems) + (0 if gaps_ok else 1)


if __name__ == "__main__":
    sys.exit(main())
