#!/usr/bin/env python3
"""Paired comparison of the Sacramento cap multiplier on disposable test leagues (docs/SACRAMENTO_CAP_ADVANTAGE_2026-11-22.md, section 5).

The method and the decision rule are fixed in that document before this runs. One execution; no retuning.

  cap_paired_comparison.py run   --out DIR [--pairs 32] [--years 4] [--workers 2] [--base 7770001]
  cap_paired_comparison.py judge --out DIR

Spoiler barrier: only seeds 7770001 to 7770999 are accepted (reserved for testing, never for a league anyone watches). `run` writes one JSON file per
league into DIR, which must be a private folder outside the repository, and prints nothing outcome-bearing. `judge` writes the numbers to
DIR/private_summary.json and prints only the verdict words.
"""
import argparse
import json
import multiprocessing as mp
import os
import random
import sys
import time
from fractions import Fraction as F

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SEED_LOW, SEED_HIGH = 7770001, 7770999
SAC = "Sacramento"
MULT = "1.15"
SCORED = (1, 2, 3, 4)            # league years scored (1-based); a year in which Sacramento is not an active club has no win percentage and is skipped for that pair
BASE_LIMIT = 1000 - 25           # the most a base-cap club may carry (cap less the reserve)
STAGES = ("free_agency_open", "camp_open", "final_cuts")


def _check_seed(seed):
    if not SEED_LOW <= seed <= SEED_HIGH:
        raise SystemExit(f"seed {seed} is outside the block reserved for tests ({SEED_LOW}-{SEED_HIGH}); refusing")


def play(task):
    seed, arm, years, out = task
    _check_seed(seed)
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from dfl.year import Universe
    from dfl.ledger.world import STARTERS
    path = os.path.join(out, f"{seed}_{arm}.json")
    if os.path.exists(path):
        return path
    kw = {"cap_multipliers": {"SAC": MULT}} if arm == "B" else {}
    u = Universe(os.path.join(ROOT, "data", "season1"), seed=seed, engine=True, spatial=False, **kw)
    L, w = u.world.L, u.world
    rows = []
    t0 = time.time()
    for yr in range(1, years + 1):
        row = {"year": yr, "pay": {}}
        u.step_event()
        while u.cursor is not None:
            key = u.step_event()
            if key in STAGES:
                row["pay"][key] = float(L.cap_number(SAC))
            if key == "final_cuts":
                ratings = []
                for pos, n in STARTERS.items():
                    mine = sorted((w.rating[i] for i in L.clubs[SAC].roster if L.players[i].pos == pos and L.players[i].out_weeks == 0), reverse=True)[:n]
                    ratings += mine
                row["strength"] = sum(ratings) / len(ratings)
                row["max_overage"] = float(max(L.overage(c) for c in L.clubs))
                row["sac_limit"] = float(L.cap_of(SAC))
                row["other_limits_base"] = all(L.cap_of(c) == L.cap for c in L.clubs if c != SAC)
        rep = u.reports[-1]
        rec = rep["result"]["outcome"]["records"].get(SAC)      # an ambassador club (one exiled the year before) has no regular-season record
        gp = (rec.w + rec.l + rec.t) if rec else 0
        row["active"] = bool(gp)
        row["win_pct"] = (rec.w + 0.5 * rec.t) / gp if gp else None
        row["title"] = rep["winner"] == SAC
        rows.append(row)
    with open(path, "w") as f:
        json.dump({"seed": seed, "arm": arm, "years": rows, "seconds": round(time.time() - t0, 1)}, f)
    return path


def cmd_run(a):
    os.makedirs(a.out, exist_ok=True)
    if os.path.abspath(a.out).startswith(ROOT + os.sep):
        raise SystemExit("--out must be a private folder outside the repository")
    seeds = [a.base + i for i in range(a.pairs)]
    for s in seeds:
        _check_seed(s)
    tasks = [(s, arm, a.years, a.out) for s in seeds for arm in ("A", "B")]
    with mp.get_context("spawn").Pool(a.workers) as pool:
        done = 0
        for _ in pool.imap_unordered(play, tasks):
            done += 1
            print(f"{done}/{len(tasks)} leagues played", flush=True)


def _boot(vals, rng, n=10000):
    k = len(vals)
    means = sorted(sum(vals[rng.randrange(k)] for _ in range(k)) / k for _ in range(n))
    return means[int(0.05 * n)], means[int(0.95 * n)]


def cmd_judge(a):
    runs = {}
    for fn in os.listdir(a.out):
        if fn.endswith(".json") and fn != "private_summary.json":
            d = json.load(open(os.path.join(a.out, fn)))
            runs[(d["seed"], d["arm"])] = d
    seeds = sorted({s for s, _ in runs if (s, "A") in runs and (s, "B") in runs})
    if len(seeds) < 2:
        raise SystemExit("not enough complete pairs to judge")

    def yrs(d):
        return [y for y in d["years"] if y["year"] in SCORED]

    def mean(xs):
        return sum(xs) / len(xs)
    diffs = {"pay": [], "strength": [], "win_pct": []}
    titles_b = seasons_b = 0
    valid_seeds = valid_pairs = 0
    first_fa_over = 0
    mech = True
    for s in seeds:
        A, B = runs[(s, "A")], runs[(s, "B")]
        diffs["pay"].append(mean([y["pay"]["final_cuts"] for y in yrs(B)]) - mean([y["pay"]["final_cuts"] for y in yrs(A)]))
        diffs["strength"].append(mean([y["strength"] for y in yrs(B)]) - mean([y["strength"] for y in yrs(A)]))
        both = [(ya["win_pct"], yb["win_pct"]) for ya, yb in zip(yrs(A), yrs(B)) if ya["active"] and yb["active"]]
        if both:
            diffs["win_pct"].append(mean([b - a_ for a_, b in both]))
            valid_seeds += 1
            valid_pairs += len(both)
        titles_b += sum(1 for y in yrs(B) if y["title"])
        seasons_b += len(yrs(B))
        first_fa_over += any(v > BASE_LIMIT for v in B["years"][0]["pay"].values())
        mech = mech and all(y["max_overage"] == 0 and y["other_limits_base"] and y["sac_limit"] == 1150 for y in B["years"]) \
            and all(y["sac_limit"] == 1000 for y in A["years"])
    rng = random.Random(20261122)
    if valid_seeds < len(seeds) * 0.75:
        raise SystemExit("too few pairs have a year in which Sacramento is active in both arms to judge")
    ci = {k: _boot(v, rng) for k, v in diffs.items()}
    mean_d = {k: mean(v) for k, v in diffs.items()}
    share_over = first_fa_over / len(seeds)
    title_share = titles_b / seasons_b
    uses = share_over >= 0.75 and ci["pay"][0] > 0
    width = ci["win_pct"][1] - ci["win_pct"][0]
    gain = mean_d["win_pct"]
    if not mech or not uses or width > 0.10:
        verdict = "EVIDENCE INSUFFICIENT"
    elif gain >= 0.10 or title_share > 0.20:
        verdict = "ADJUST DOWN"
    elif ci["win_pct"][1] < 0.01:
        verdict = "ADJUST UP"
    elif gain >= 0.03 and ci["win_pct"][0] > 0:
        verdict = "RETAIN 1.15"
    else:
        verdict = "EVIDENCE INSUFFICIENT"
    summary = {"pairs": len(seeds), "scored_years": list(SCORED), "mechanism_ok": mech, "management_uses_room": uses,
               "share_of_pairs_above_base_limit_in_first_year": share_over, "paired_mean": mean_d, "ci90": ci,
               "valid_seed_pairs": valid_seeds, "valid_year_pairs": valid_pairs, "extra_room_share_filled": mean_d["pay"] / 150, "title_share_B": title_share, "verdict": verdict}
    with open(os.path.join(a.out, "private_summary.json"), "w") as f:
        json.dump(summary, f, indent=1)
    print("VERDICT:", verdict)
    print("mechanism works:", "yes" if mech else "no")
    print("management uses the extra room:", "yes" if uses else "no")
    print("competitive uncertainty remains:", "yes" if title_share <= 0.20 and gain < 0.10 else "no")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--out", required=True)
    r.add_argument("--pairs", type=int, default=32)
    r.add_argument("--years", type=int, default=4)
    r.add_argument("--workers", type=int, default=2)
    r.add_argument("--base", type=int, default=SEED_LOW, help="first seed; the real comparison uses the default, a dry run of the tool uses 7770900")
    r.set_defaults(fn=cmd_run)
    j = sub.add_parser("judge")
    j.add_argument("--out", required=True)
    j.set_defaults(fn=cmd_judge)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
