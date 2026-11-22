#!/usr/bin/env python3
"""Paired comparison of the Sacramento upgrade policy on disposable test leagues (docs/SACRAMENTO_MANAGEMENT_POLICY_2026-11-22.md, section 5).

The method and the decision rule are fixed in that document, and committed, before this runs. One execution; no retuning.

  policy_paired_comparison.py screen --out DIR [--pairs 32] [--base 7770101]
  policy_paired_comparison.py run    --out DIR [--workers 2] [--years 4]
  policy_paired_comparison.py judge  --out DIR

Arms, each from the same creation (same seed): A the default league; B Sacramento cap multiplier 1.15; C Sacramento cap 1.15 plus the upgrade policy.
`screen` keeps the first PAIRS seeds, from BASE upward, whose league can be created (a founding failure of the baseline code, independent of every
option, skips a seed; the skipped seeds are counted). Only seeds 7770101 to 7770999 are accepted: reserved for testing, never for a league anyone
watches. `run` writes one JSON file per league into DIR, which must be a private folder outside the repository, and prints nothing outcome-bearing.
`judge` writes the numbers to DIR/private_summary.json and prints only the verdict words.
"""
import argparse
import json
import multiprocessing as mp
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SEED_LOW, SEED_HIGH = 7770101, 7770999
SAC = "Sacramento"
MULT = "1.15"
ARMS = ("A", "B", "C")
SCORED = (1, 2, 3, 4)            # league years scored (1-based); a year in which Sacramento is not an active club has no win percentage and is skipped for that pair
STAGES = ("free_agency_open", "camp_open", "final_cuts")


def _check_seed(seed):
    if not SEED_LOW <= seed <= SEED_HIGH:
        raise SystemExit(f"seed {seed} is outside the block reserved for tests ({SEED_LOW}-{SEED_HIGH}); refusing")


def _universe(seed, arm):
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from dfl.year import Universe
    kw = {}
    if arm in ("B", "C"):
        kw["cap_multipliers"] = {"SAC": MULT}
    if arm == "C":
        kw["upgrade_policy"] = ["SAC"]
    return Universe(os.path.join(ROOT, "data", "season1"), seed=seed, engine=True, spatial=False, **kw)


def play(task):
    seed, arm, years, out = task
    _check_seed(seed)
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from dfl.ledger.world import STARTERS
    from dfl.ledger import rules as R
    path = os.path.join(out, f"{seed}_{arm}.json")
    if os.path.exists(path):
        return path
    u = _universe(seed, arm)
    L, w = u.world.L, u.world
    rows = []
    t0 = time.time()
    last = {"upgrades": 0, "upgrades_more": 0}
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
                row["strength"] = sum(ratings) / len(ratings)          # a measurement only: it reads the hidden ratings, management never does
                row["max_overage"] = float(max(L.overage(c) for c in L.clubs))
                row["sac_limit"] = float(L.cap_of(SAC))
                row["other_limits_base"] = all(L.cap_of(c) == L.cap for c in L.clubs if c != SAC)
                row["legal_sizes"] = all(len(c.roster) <= R.ROSTER_MAX and len(c.ps) <= R.PS_MAX for c in L.clubs.values())
                for k in last:
                    row[k] = w.stats.get(k, 0) - last[k]
                    last[k] = w.stats.get(k, 0)
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


def cmd_screen(a):
    os.makedirs(a.out, exist_ok=True)
    if os.path.abspath(a.out).startswith(ROOT + os.sep):
        raise SystemExit("--out must be a private folder outside the repository")
    good, skipped, s = [], [], a.base
    while len(good) < a.pairs:
        _check_seed(s)
        try:
            _universe(s, "A")
            good.append(s)
        except Exception as e:                                   # a founding failure of the baseline code; independent of every option
            skipped.append([s, type(e).__name__])
        s += 1
    with open(os.path.join(a.out, "seeds.json"), "w") as f:
        json.dump({"seeds": good, "skipped": skipped}, f)
    print(f"{len(good)} usable seeds, {len(skipped)} skipped for a founding failure")


def cmd_run(a):
    if os.path.abspath(a.out).startswith(ROOT + os.sep):
        raise SystemExit("--out must be a private folder outside the repository")
    seeds = json.load(open(os.path.join(a.out, "seeds.json")))["seeds"]
    tasks = [(s, arm, a.years, a.out) for s in seeds for arm in ARMS]
    with mp.get_context("spawn").Pool(a.workers) as pool:
        done = 0
        for _ in pool.imap_unordered(play, tasks):
            done += 1
            print(f"{done}/{len(tasks)} leagues played", flush=True)


def _boot(vals, rng, n=10000):
    k = len(vals)
    means = sorted(sum(vals[rng.randrange(k)] for _ in range(k)) / k for _ in range(n))
    return means[int(0.05 * n)], means[int(0.95 * n)]


def mean(xs):
    return sum(xs) / len(xs)


def cmd_judge(a):
    runs = {}
    for fn in os.listdir(a.out):
        if fn.endswith(".json") and fn not in ("private_summary.json", "seeds.json"):
            d = json.load(open(os.path.join(a.out, fn)))
            runs[(d["seed"], d["arm"])] = d
    seeds = sorted({s for s, _ in runs if all((s, x) in runs for x in ARMS)})
    if len(seeds) < 2:
        raise SystemExit("not enough complete triples to judge")

    def yrs(d):
        return [y for y in d["years"] if y["year"] in SCORED]
    d_pay, d_str, d_win_ca, d_win_cb = [], [], [], []
    fires = 0
    titles_c = seasons_c = low_c = valid_c = 0
    valid_seeds = 0
    legal = True
    for s in seeds:
        A, B, C = (runs[(s, x)] for x in ARMS)
        d_pay.append(mean([y["pay"]["final_cuts"] for y in yrs(C)]) - mean([y["pay"]["final_cuts"] for y in yrs(B)]))
        d_str.append(mean([y["strength"] for y in yrs(C)]) - mean([y["strength"] for y in yrs(B)]))
        both_ca = [(ya["win_pct"], yc["win_pct"]) for ya, yc in zip(yrs(A), yrs(C)) if ya["active"] and yc["active"]]
        both_cb = [(yb["win_pct"], yc["win_pct"]) for yb, yc in zip(yrs(B), yrs(C)) if yb["active"] and yc["active"]]
        if both_ca:
            d_win_ca.append(mean([c - x for x, c in both_ca]))
            valid_seeds += 1
        if both_cb:
            d_win_cb.append(mean([c - x for x, c in both_cb]))
        fires += sum(y["upgrades_more"] for y in yrs(C)) >= 1
        titles_c += sum(1 for y in yrs(C) if y["title"])
        seasons_c += len(yrs(C))
        low_c += sum(1 for y in yrs(C) if y["active"] and y["win_pct"] <= 0.5)
        valid_c += sum(1 for y in yrs(C) if y["active"])
        legal = legal and all(y["max_overage"] == 0 and y["other_limits_base"] and y["legal_sizes"] and y["sac_limit"] == 1150 for y in C["years"]) \
            and all(y["max_overage"] == 0 and y["legal_sizes"] for y in A["years"] + B["years"])
    rng = random.Random(20261122)
    ci = {"pay_CB": _boot(d_pay, rng), "strength_CB": _boot(d_str, rng), "win_CA": _boot(d_win_ca, rng), "win_CB": _boot(d_win_cb, rng)}
    mn = {"pay_CB": mean(d_pay), "strength_CB": mean(d_str), "win_CA": mean(d_win_ca), "win_CB": mean(d_win_cb)}
    fire_share = fires / len(seeds)
    title_share = titles_c / seasons_c
    low_share = low_c / valid_c
    uses = fire_share >= 0.75 and ci["strength_CB"][0] > 0 and ci["pay_CB"][0] > 0
    fallible = title_share <= 0.20 and low_share >= 0.10
    width = ci["win_CA"][1] - ci["win_CA"][0]
    gain = mn["win_CA"]
    if not legal or valid_seeds < len(seeds) * 0.75 or width > 0.10:
        verdict = "EVIDENCE INSUFFICIENT"
    elif not uses:
        verdict = "INEFFECTIVE"
    elif gain >= 0.10 or title_share > 0.20:
        verdict = "EXCESSIVE"
    elif ci["win_CA"][1] < 0.01:
        verdict = "INEFFECTIVE"
    elif gain >= 0.03 and ci["win_CA"][0] > 0 and fallible:
        verdict = "USEFUL ADVANTAGE"
    else:
        verdict = "EVIDENCE INSUFFICIENT"
    summary = {"triples": len(seeds), "scored_years": list(SCORED), "legal": legal, "policy_fires_share": fire_share, "uses_room_for_real_upgrades": uses,
               "paired_mean": mn, "ci90": ci, "valid_seed_pairs": valid_seeds, "title_share_C": title_share, "low_season_share_C": low_share,
               "fallible": fallible, "win_ci_width": width, "verdict": verdict}
    with open(os.path.join(a.out, "private_summary.json"), "w") as f:
        json.dump(summary, f, indent=1)
    print("VERDICT:", verdict)
    print("rules and roster limits held:", "yes" if legal else "no")
    print("uses the extra room for worthwhile upgrades:", "yes" if uses else "no")
    print("competitive uncertainty remains:", "yes" if fallible else "no")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("screen")
    s.add_argument("--out", required=True)
    s.add_argument("--pairs", type=int, default=32)
    s.add_argument("--base", type=int, default=SEED_LOW)
    s.set_defaults(fn=cmd_screen)
    r = sub.add_parser("run")
    r.add_argument("--out", required=True)
    r.add_argument("--years", type=int, default=4)
    r.add_argument("--workers", type=int, default=2)
    r.set_defaults(fn=cmd_run)
    j = sub.add_parser("judge")
    j.add_argument("--out", required=True)
    j.set_defaults(fn=cmd_judge)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
