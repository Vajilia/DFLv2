"""Calibration harness for the game engine: plays many games between founding-league clubs and compares the averages with
data/engine/BANDS.csv.  Usage: calibrate_engine.py [--games N] [--seed S] [--baseline] [--spatial] [--procs P]
--spatial plays every scrimmage snap with the 22-player engine (Edition 8.48); --procs spreads the games over worker processes.
--baseline prints the unit means and spreads of the 48 founding squads (to set data/engine/PARAMS.csv).  Exit code = bands failed."""
import argparse
import csv
import os
import random
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl.characters import Creator  # noqa: E402
from dfl.engine.game import play_game  # noqa: E402
from dfl.engine.squad import Squad  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--games", type=int, default=1000)
ap.add_argument("--seed", type=int, default=1)
ap.add_argument("--baseline", action="store_true")
ap.add_argument("--spatial", action="store_true")
ap.add_argument("--procs", type=int, default=1)
a = ap.parse_args()

clubs = [f"C{i:02d}" for i in range(48)]
C = Creator(a.seed)
pop = C.founding(2001, clubs)
squads = {c: Squad(c, pop["clubs"][c]["roster"] + pop["clubs"][c]["ps"]) for c in clubs}

if a.baseline:
    names = list(next(iter(squads.values())).units()["raw"])
    for n in names:
        xs = [s.units()["raw"][n] for s in squads.values()]
        print(f"mean_{n},{statistics.mean(xs):.1f}   sd_{n},{statistics.pstdev(xs):.2f}")
    sys.exit(0)

rng = random.Random(a.seed)
S = {k: 0.0 for k in ("points", "plays", "pass_att", "comp", "pass_yds", "sacks", "ints", "rush_att", "rush_yds", "fumbles_lost",
                      "third_att", "third_conv", "penalties", "pen_yds", "punts", "fg_att", "fg_made", "td", "first_downs", "top")}
n_team = 0
ot = ties = homewins = 0
home_top = 0.0
margins, inj = [], 0
t0 = time.time()


def one(job):
    h, w, seed = job
    for s in (squads[h], squads[w]):
        s.out.clear(); s.new_injuries.clear(); s._units = None
    r = play_game(squads[h], squads[w], random.Random(seed), spatial=a.spatial)
    return dict(stats=[{k: r.stats[i].get(k, 0) for k in S if k != "points"} for i in (0, 1)], score=list(r.score), periods=r.periods,
                tie=r.tie, top=r.stats[0]["top"], inj=sum(len(v) for v in r.injuries.values()))


jobs = []
for g in range(a.games):
    h, w = rng.sample(clubs, 2)
    jobs.append((h, w, rng.random()))
if a.procs > 1:
    import multiprocessing as mp
    with mp.get_context("fork").Pool(a.procs) as pool:
        results = pool.map(one, jobs, chunksize=4)
else:
    results = map(one, jobs)
for r in results:
    for i in (0, 1):
        S["points"] += r["score"][i]
        for k in S:
            if k != "points":
                S[k] += r["stats"][i][k]
        n_team += 1
    home_top += r["top"]
    ot += r["periods"] > 4
    ties += r["tie"]
    homewins += r["score"][0] > r["score"][1]
    margins.append(r["score"][0] - r["score"][1])
    inj += r["inj"]
per = lambda k: S[k] / n_team
m = {
    "points": per("points"), "plays": per("plays"), "pass_att": per("pass_att"),
    "comp_pct": S["comp"] / max(1, S["pass_att"]), "yds_per_att": S["pass_yds"] / max(1, S["pass_att"]),
    "sack_rate": S["sacks"] / max(1, S["pass_att"] + S["sacks"]), "int": per("ints"), "rush_att": per("rush_att"),
    "ypc": S["rush_yds"] / max(1, S["rush_att"]), "fumbles_lost": per("fumbles_lost"),
    "third_pct": S["third_conv"] / max(1, S["third_att"]), "penalties": per("penalties"), "pen_yds": per("pen_yds"),
    "punts": per("punts"), "fg_att": per("fg_att"), "fg_pct": S["fg_made"] / max(1, S["fg_att"]), "td": per("td"),
    "first_downs": per("first_downs"), "top_share": home_top / max(1.0, S["top"]),
    "ot_rate": ot / a.games, "tie_rate": ties / a.games, "home_win": homewins / max(1, a.games - ties),
    "margin_sd": statistics.pstdev(margins), "injuries": inj / n_team,
}
bands = list(csv.DictReader(open(os.path.join(ROOT, "data", "engine", "BANDS.csv"))))
fails = 0
print(f"{a.games} games, seed {a.seed}, {time.time() - t0:.0f}s ({a.games / (time.time() - t0):.1f} games/s)")
for b in bands:
    v = m[b["metric"]]
    ok = float(b["lo"]) <= v <= float(b["hi"])
    fails += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {b['metric']:13} {v:8.3f}   band {b['lo']} - {b['hi']}")
sys.exit(fails)
