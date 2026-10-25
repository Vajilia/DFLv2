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
    u = Universe(os.path.join(ROOT, "data", "season1"), seed=a.seed, profile=a.profile)
    seasons, t0 = [], time.time()
    for _ in range(a.years):
        rep = u.play_year()
        w = u.world
        strength = {c: F.club_strength(w, c) for c in rep["result"]["outcome"]["records"]}
        extra = {"roster_mean": sum(w.rating[i] for c in u.L.clubs.values() for i in c.roster) / sum(len(c.roster) for c in u.L.clubs.values())}
        seasons.append(F.season_record(rep["year"], rep, strength, extra))
        print(f"seed {a.seed} {rep['year']} {time.time() - t0:.0f}s", flush=True)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as f:
        keys = ("recall_ballots", "recalls", "term_losses", "elections", "ceo_replaced", "ceo_retirements", "gm_retirements", "ceo_fires_gm", "gm_resignations", "gm_hires",
                "coach_hires", "hires_hc", "hires_oc", "hires_dc", "hires_stc", "coach_fires_hc", "coach_fires_coord", "coach_resignations", "trades", "deals", "refusals", "interviews", "interview_declines", "forced_hires", "rooms")
        term = [[e["sentiment"], int(e["incumbent_won"])] for e in u.world.gov_log if e["kind"] == "election" and e["cause"] == "term"]
        json.dump({"seed": a.seed, "seasons": seasons, "events": {k: u.world.stats.get(k, 0) for k in keys}, "term_elections": term}, f)


def report(a):
    leagues = []
    for p in a.files:
        with open(p) as f:
            leagues.append(json.load(f)["seasons"])
    rows = F.verdicts(leagues, warmup=a.warmup)
    text = (f"Fairness bands: {len(leagues)} league(s), {len(leagues[0])} seasons each, first {a.warmup} dropped as warm-up.\n\n"
            + F.format_table(rows) + "\n")
    if all("games" in lg[0] for lg in leagues):
        pushes = [F.schedule_push(lg, a.warmup) for lg in leagues]
        slope = sum(p[0] for p in pushes) / len(pushes)
        base = F.schedule_baseline(len(leagues[0][-1]["strength"]))
        text += (f"\nSchedule push toward average: opponents' mean strength per point of own strength, slope {slope:.3f} "
                 f"(a schedule that ignores strength gives {base:.3f}, because a club never plays herself), so the push is {slope - base:+.3f} "
                 f"per point; correlation {sum(p[1] for p in pushes) / len(pushes):.3f}; league slopes {', '.join(f'{p[0]:.3f}' for p in pushes)}. "
                 f"Positive push = stronger clubs face tougher schedules.\n")
    if len(leagues[0]) >= 40:
        bands = F.load_bands()
        text += "\nTrend by decade (flags mark a banded figure outside its limits):\n"
        for k, lg in enumerate(leagues, 1):
            text += f"\nLeague {k}\n\n| seasons | win % spread | close games | points/team game | most titles by one club | distinct champions | roster rating | club-strength spread |\n| --- | --- | --- | --- | --- | --- | --- | --- |\n"
            for d in F.decades(lg):
                wf = "*" if not bands["win_pct_sd"][0] <= d["win_pct_sd"] <= bands["win_pct_sd"][1] else ""
                cf = "*" if not bands["close_game_share"][0] <= d["close_game_share"] <= bands["close_game_share"][1] else ""
                text += (f"| {d['seasons']} | {d['win_pct_sd']:.3f}{wf} | {d['close_game_share']:.3f}{cf} | {d['points_per_team_game']:.1f} | "
                         f"{d['most_titles_one_club']} | {d['distinct_champions']} | {d['roster_mean']:.1f} | {d['club_strength_sd']:.2f} |\n")
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
    r.add_argument("--profile", default="tight", choices=("tight", "none", "rigged"), help="parity test bed (see src/dfl/parity.py)")
    p = sub.add_parser("report")
    p.add_argument("files", nargs="+")
    p.add_argument("--warmup", type=int, default=F.WARMUP)
    p.add_argument("--md")
    a = ap.parse_args()
    run(a) if a.cmd == "run" else report(a)


if __name__ == "__main__":
    main()
