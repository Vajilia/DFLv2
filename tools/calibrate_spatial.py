"""Play-level calibration of the spatial engine (Design Document section 10, Edition 8.48).

Plays N snaps of one kind against random founding-league squads and prints the rates the engine must reproduce, beside the NFL's.
Usage: calibrate_spatial.py [--kind run|pass|both] [--n N] [--seed S] [--set name=value ...] [--procs P]
`--set` overrides a data/engine/spatial/PHYSICS.csv tunable for this run only (for scans). Exit code 1 if any rate is outside its band.
"""
import argparse
import multiprocessing as mp
import os
import random
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

RUN_COV = {"cover3": 0.34, "cover1": 0.18, "cover2": 0.16, "cover4": 0.14, "cover0": 0.02, "cover1_blitz": 0.08, "cover3_blitz": 0.08}
# name: (nfl, low, high). Rates are per play unless noted.
RUN_T = {"ypc": (4.35, 4.0, 4.7), "loss": (0.105, 0.07, 0.14), "stuff1": (0.27, 0.2, 0.34), "p10": (0.11, 0.08, 0.14), "p20": (0.032, 0.015, 0.05),
         "fumble": (0.0125, 0.006, 0.02), "secs": (4.6, 3.9, 5.4),
         "h_0": (0.08, 0.04, 0.12), "h_1_2": (0.21, 0.15, 0.27), "h_3_4": (0.19, 0.13, 0.25), "h_5_9": (0.24, 0.17, 0.31)}
PASS_T = {"sack": (0.065, 0.045, 0.085), "scramble": (0.02, 0.008, 0.035), "comp_pct": (0.645, 0.61, 0.68), "int_pct": (0.023, 0.015, 0.032),
          "ypa": (7.0, 6.2, 7.8), "ypc": (10.9, 9.5, 12.0), "pd": (0.085, 0.05, 0.12), "throwaway": (0.03, 0.01, 0.05), "secs": (4.9, 4.1, 5.7),
          "air": (8.0, 6.5, 9.5), "yac": (5.2, 4.0, 6.5),
          "conv3": (0.37, 0.30, 0.44), "d_behind": (0.12, 0.06, 0.18), "d_0_5": (0.27, 0.19, 0.35), "d_5_10": (0.27, 0.19, 0.35), "d_10_15": (0.15, 0.09, 0.21),
          "d_15_20": (0.09, 0.04, 0.14), "d_20": (0.10, 0.05, 0.15)}     # the depth shares are of throws at a receiver, by yards past the line


def setup(overrides):
    from dfl.characters import Creator
    from dfl.engine.squad import Squad
    from dfl.engine.spatial import core
    for k, v in overrides.items():
        core.S[k] = v
    clubs = [f"C{i:02d}" for i in range(48)]
    pop = Creator(1).founding(2001, clubs)
    return clubs, {c: Squad(c, pop["clubs"][c]["roster"] + pop["clubs"][c]["ps"]) for c in clubs}


DETAIL = False


def pick(rng, table):
    r = rng.random() * sum(table.values())
    for k, w in table.items():
        r -= w
        if r <= 0:
            return k
    return k


def situations(clubs, sq, rng, kind, n):
    """(down, distance, yards from own goal line) of n real scrimmage snaps of the given kind, taken from play-level games, so that the
    spatial engine is measured in the spots where games put it (third and long, the red zone, backed up) and not only on first and ten."""
    from dfl.engine.game import play_game
    out = []
    want = ("run", "scramble") if kind == "run" else ("complete", "incomplete", "sack", "interception")
    while len(out) < n:
        a, b = rng.sample(clubs, 2)
        r = play_game(sq[a], sq[b], random.Random(rng.random()), False, True)
        for x in r.table:
            if x["kind"] in want and x.get("down") and x.get("dist"):
                out.append((x["down"], x["dist"], x["yl"], x["off"]))
    rng.shuffle(out)
    return out[:n]


def work(args):
    kind, n, seed, overrides = args
    from dfl.engine.spatial import calls
    from dfl.engine.spatial.sim import PlaySim
    clubs, sq = setup(overrides)
    rng = random.Random(seed)
    out = []
    for down, dist, yl, _ in situations(clubs, sq, rng, kind, n):
        a, b = rng.sample(clubs, 2)
        if kind == "run":
            play = calls.run_call(rng, down, dist, yl)
        else:
            play = calls.pass_call(rng, down, dist, yl)
        cov = calls.coverage_call(rng, down, dist, yl, kind == "pass")
        los = float(min(99, max(1, yl)))
        o = PlaySim(sq[a].units(), sq[b].units(), kind, play, cov, los, 26.67 + rng.choice((-3, 0, 3)), random.Random(rng.random()),
                    down=down, dist=dist).run()
        o["los"] = los
        o["down"], o["dist"] = down, dist
        out.append({"play": play, "cov": cov, "down": down, "dist": dist} | {k: o[k] for k in ("kind", "yards", "turnover", "secs", "td", "end", "los") + (("air",) if "air" in o else ()) + (("aim",) if "aim" in o else ()) + (("yac",) if "yac" in o else ())} |
                   {"fumble": bool(o.get("fumble")) or o["turnover"] == "fumble", "pd": "pd" in o, "thrown_away": o["end"] == "incomplete" and o["kind"] == "incomplete" and o.get("target") is None})
    return out


def run(kind, n, seed, overrides, procs):
    chunks = [(kind, n // procs, seed * 1000 + i, overrides) for i in range(procs)]
    if procs > 1:
        with mp.Pool(procs) as pool:
            res = [r for part in pool.map(work, chunks) for r in part]
    else:
        res = work(chunks[0])
    return res


def report(kind, res):
    n = len(res)
    m = {}
    if kind == "run":
        ys = [r["yards"] for r in res]
        m["ypc"] = sum(ys) / n
        m["loss"] = sum(y < 0 for y in ys) / n
        m["stuff1"] = sum(y <= 1 for y in ys) / n
        m["p10"] = sum(y >= 10 for y in ys) / n
        m["p20"] = sum(y >= 20 for y in ys) / n
        m["fumble"] = sum(r["fumble"] for r in res) / n
        m["secs"] = sum(r["secs"] for r in res) / n
        tgt = RUN_T
        hist = Counter("<0" if y < 0 else "0" if y == 0 else "1-2" if y <= 2 else "3-4" if y <= 4 else "5-9" if y <= 9 else "10-19" if y <= 19 else "20+" for y in ys)
        for key, lo_, hi_ in (("h_0", 0, 1), ("h_1_2", 1, 3), ("h_3_4", 3, 5), ("h_5_9", 5, 10)):
            m[key] = sum(lo_ <= y < hi_ for y in ys) / n
        extra = "  ".join(f"{k}:{hist[k] / n:.3f}" for k in ("<0", "0", "1-2", "3-4", "5-9", "10-19", "20+"))
    else:
        att = [r for r in res if r["kind"] in ("complete", "incomplete", "int")]
        comp = [r for r in att if r["kind"] == "complete"]
        m["sack"] = sum(r["kind"] == "sack" for r in res) / n
        m["scramble"] = sum(r["kind"] == "scramble" for r in res) / n
        m["comp_pct"] = len(comp) / max(1, len(att))
        m["int_pct"] = sum(r["kind"] == "int" for r in att) / max(1, len(att))
        m["ypa"] = sum(r["yards"] for r in comp) / max(1, len(att))
        m["ypc"] = sum(r["yards"] for r in comp) / max(1, len(comp))
        m["pd"] = sum(r["pd"] for r in att) / max(1, len(att))
        m["throwaway"] = sum(r["thrown_away"] for r in att) / max(1, len(att))
        m["secs"] = sum(r["secs"] for r in res) / n
        aims = [r["aim"] for r in att if "aim" in r]
        m["air"] = sum(aims) / max(1, len(aims))
        t3 = [r for r in res if r["down"] == 3]
        m["conv3"] = sum(r["yards"] >= r["dist"] and r["kind"] in ("complete", "scramble") or r["td"] for r in t3) / max(1, len(t3))
        for key, lo_, hi_ in (("d_behind", -99, 0), ("d_0_5", 0, 5), ("d_5_10", 5, 10), ("d_10_15", 10, 15), ("d_15_20", 15, 20), ("d_20", 20, 99)):
            m[key] = sum(lo_ <= x < hi_ for x in aims) / max(1, len(aims))
        m["yac"] = sum(r["yac"] for r in comp if "yac" in r) / max(1, len(comp))
        tgt = PASS_T
        extra = f"attempts {len(att) / n:.3f} of snaps"
    bad = 0
    if DETAIL:
        for key in ("play", "cov"):
            groups = {}
            for r in res:
                groups.setdefault(r[key], []).append(r)
            print(f"  by {key}:")
            for g, rs in sorted(groups.items()):
                a = [r for r in rs if r["kind"] in ("complete", "incomplete", "int")]
                c = [r for r in a if r["kind"] == "complete"]
                if kind == "run":
                    print(f"    {g:14s} n={len(rs):4d} ypc {sum(r['yards'] for r in rs) / len(rs):5.2f}  loss {sum(r['yards'] < 0 for r in rs) / len(rs):.3f}")
                else:
                    print(f"    {g:14s} n={len(rs):4d} comp {len(c) / max(1, len(a)):.3f} int {sum(r['kind'] == 'int' for r in a) / max(1, len(a)):.3f} "
                          f"sack {sum(r['kind'] == 'sack' for r in rs) / len(rs):.3f} ypc {sum(r['yards'] for r in c) / max(1, len(c)):5.1f} "
                          f"ypa {sum(r['yards'] for r in c) / max(1, len(a)):4.1f}")
    if DETAIL and kind == "pass":
        print("  by depth of the throw (NFL completion % in brackets):")
        att = [r for r in res if r["kind"] in ("complete", "incomplete", "int") and "aim" in r]
        for lo, hi, nc in ((-99, 0, 0.80), (0, 5, 0.76), (5, 10, 0.66), (10, 15, 0.57), (15, 20, 0.45), (20, 99, 0.33)):
            g = [r for r in att if lo <= r["aim"] < hi]
            if g:
                c = [r for r in g if r["kind"] == "complete"]
                print(f"    {lo:4d}..{hi:3d}  share {len(g) / len(att):.2f}  comp {len(c) / len(g):.2f} [{nc}]  int {sum(r['kind'] == 'int' for r in g) / len(g):.3f}  "
                      f"yards/comp {sum(r['yards'] for r in c) / max(1, len(c)):5.1f}")
    print(f"--- {kind} ({n} snaps)  {extra}")
    for k, (nfl, lo, hi) in tgt.items():
        v = m.get(k, float("nan"))
        flag = "" if lo <= v <= hi else "   <--"
        bad += flag != ""
        print(f"  {k:10s} nfl {nfl:7.3f}   engine {v:7.3f}   band {lo}-{hi}{flag}")
    report.last = m
    return bad


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default="both")
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--procs", type=int, default=max(1, min(8, os.cpu_count() or 1)))
    ap.add_argument("--set", nargs="*", default=[])
    ap.add_argument("--detail", action="store_true")
    a = ap.parse_args()
    ov = {k: float(v) for k, v in (s.split("=") for s in a.set)}
    DETAIL = a.detail
    bad = 0
    for kind in (["run", "pass"] if a.kind == "both" else [a.kind]):
        bad += report(kind, run(kind, a.n, a.seed, ov, a.procs))
    sys.exit(1 if bad else 0)
