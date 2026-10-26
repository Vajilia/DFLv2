"""Calibrate the penalty catalogue (data/engine/PENALTIES.csv) against the NFL's accepted penalties per game by type.

Plays founding-league games, counts accepted penalties by name, and scales each type's draw rate by target / observed (damped).
Usage: calibrate_penalties.py [--games N] [--seed S] [--iterations I] [--write]
Without --write it only reports. Exit code 1 if any type with at least 0.05 a game is more than 15 percent off the NFL.
"""
import argparse
import csv
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl.characters import Creator  # noqa: E402
from dfl.engine import game as G  # noqa: E402
from dfl.engine.params import PENALTIES  # noqa: E402
from dfl.engine.squad import Squad  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--games", type=int, default=3000)
ap.add_argument("--seed", type=int, default=1)
ap.add_argument("--iterations", type=int, default=1)
ap.add_argument("--write", action="store_true")
a = ap.parse_args()

clubs = [f"C{i:02d}" for i in range(48)]
pop = Creator(a.seed).founding(2001, clubs)
squads = {c: Squad(c, pop["clubs"][c]["roster"] + pop["clubs"][c]["ps"]) for c in clubs}
by_name = {p["name"]: p for p in PENALTIES}


def measure(n, seed):
    rng = random.Random(seed)
    cnt, yds, teams = {}, {}, 0
    for _ in range(n):
        h, w = rng.sample(clubs, 2)
        for s in (squads[h], squads[w]):
            s.out.clear(); s.new_injuries.clear(); s.ejected.clear(); s._units = None
        r = G.play_game(squads[h], squads[w], random.Random(rng.random()))
        for d in r.pen_types:
            for name, (c, y) in d.items():
                cnt[name] = cnt.get(name, 0) + c
                yds[name] = yds.get(name, 0) + y
        teams += 2
    return {k: v / n for k, v in cnt.items()}, {k: v / n for k, v in yds.items()}, teams


for it in range(a.iterations):
    per, yd, _ = measure(a.games, a.seed * 1000 + it)
    if it < a.iterations - 1 or a.write:
        for p in PENALTIES:
            obs = per.get(p["name"], 0.0)
            if p["nfl"] > 0:
                f = p["nfl"] / obs if obs > 0 else 2.0
                p["rate"] = round(p["rate"] * (1 + 0.8 * (min(3.0, max(0.33, f)) - 1)), 6)
    print(f"iteration {it + 1}: {sum(per.values()) / 2:.2f} penalties and {sum(yd.values()) / 2:.1f} yards per team per game")

per, yd, _ = measure(a.games, a.seed * 1000 + 99)
bad = 0
print(f"{'penalty':36s} {'nfl':>7s} {'engine':>7s} {'ratio':>6s} {'yds/pen':>8s}")
for p in PENALTIES:
    obs = per.get(p["name"], 0.0)
    ratio = obs / p["nfl"] if p["nfl"] else 0
    flag = ""
    if p["nfl"] >= 0.05 and abs(ratio - 1) > 0.15:
        flag = "  <--"
        bad += 1
    ypp = yd.get(p["name"], 0) / (per[p["name"]]) if per.get(p["name"]) else 0
    print(f"{p['name']:36s} {p['nfl']:7.3f} {obs:7.3f} {ratio:6.2f} {ypp:8.1f}{flag}")
tot_n = sum(per.values()) / 2
tot_y = sum(yd.values()) / 2
print(f"total per team: {tot_n:.2f} penalties (NFL 5.94), {tot_y:.1f} yards (NFL 49.0)")

if a.write:
    import io
    path = os.path.join(ROOT, "data", "engine", "PENALTIES.csv")
    lines = open(path, newline="").read().split("\n")
    out, header = [], None
    for ln in lines:
        if not ln or ln.startswith("#"):
            out.append(ln)
            continue
        row = next(csv.reader([ln]))
        if header is None:
            header = row
            out.append(ln)
            continue
        d = dict(zip(header, row))
        d["rate"] = repr(by_name[d["name"]]["rate"])
        s = io.StringIO()
        csv.writer(s, lineterminator="").writerow([d[h] for h in header])
        out.append(s.getvalue())
    open(path, "w", newline="").write("\n".join(out))
    print("rates written")
sys.exit(1 if bad else 0)
