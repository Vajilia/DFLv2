#!/usr/bin/env python3
"""Measure how long the published replays take to watch, to choose broadcast speeds and slot lengths from data (docs/BROADCAST_PLAN.md).

  broadcast_measure.py --playback PLAYBACK_DIR [--events EVENTS_JSONL] [--json]

Read-only; standard library only. For every game in PLAYBACK_DIR/index.json it reads the game file and adds up the time the viewer (site/assets/playback.js)
spends on it at speed 1: a play with a recorded trace takes `trace.t_end + HOLD_S`, a play without one (kickoff, punt, penalty, timeout, score card ...) takes
`CARD_S`. HOLD_S and CARD_S are the viewer's own constants and are repeated here only to measure; the broadcast timetable stores its own values.
It reports, per kind of game and per kickoff window, the distribution (min, median, 90th, 99th percentile, max) of watching time, the number of plays,
how many have movement, and what the untraced plays are. With --events it also reports how long the league took to compute each kind of event.
"""
import argparse
import collections
import json
import math
import os
import sys

HOLD_S = 1.2        # site/assets/playback.js: pause after a traced play ends
CARD_S = 2.6        # site/assets/playback.js: how long an event card without movement stays up


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def dist(xs):
    return {"n": len(xs), "min": round(min(xs), 1), "p50": round(pct(xs, .5), 1), "p90": round(pct(xs, .9), 1), "p99": round(pct(xs, .99), 1), "max": round(max(xs), 1),
            "mean": round(sum(xs) / len(xs), 1)} if xs else {"n": 0}


def measure_game(g):
    traced = untraced = 0
    seconds = 0.0
    kinds = collections.Counter()
    for p in g["plays"]:
        tr = p.get("trace")
        if tr:
            traced += 1
            seconds += float(tr["t_end"]) + HOLD_S
        else:
            untraced += 1
            seconds += CARD_S
            kinds[p.get("kind", "?")] += 1
    return {"plays": len(g["plays"]), "traced": traced, "untraced": untraced, "seconds": seconds, "untraced_kinds": kinds,
            "trace_seconds": sum(float(p["trace"]["t_end"]) for p in g["plays"] if p.get("trace"))}


def window_of(gid, sched, tour):
    """The kickoff window of a game id: 'Sat 17:30', 'Sun 10:00' ... from the Season 1 tables (the weekday and kickoff of every game are the same every season)."""
    s = gid.split("/", 1)[1]
    if s.startswith("reg:"):
        _, w, gm = s.split(":")
        return sched.get((int(w[1:]), int(gm[1:])))
    if s.startswith("tour:"):
        _, r, gm = s.split(":")
        return tour.get((int(r[1:]), int(gm[1:])))
    return None


def load_tables(root):
    import csv
    import datetime as dt
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    sched, tour = {}, {}
    try:
        with open(os.path.join(root, "data", "season1", "SCHEDULE.csv"), encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                sched[(int(r["week"]), int(r["game"]))] = names[dt.date.fromisoformat(r["date"]).weekday()] + " " + r["kickoff"]
        with open(os.path.join(root, "data", "season1", "TOUR.csv"), encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                tour[(int(r["tour_round"]), int(r["game"]))] = names[dt.date.fromisoformat(r["date"]).weekday()] + " " + r["kickoff"]
    except OSError:
        pass
    return sched, tour


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--playback", required=True)
    ap.add_argument("--events")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--table", action="store_true", help="plain-text tables instead of JSON")
    a = ap.parse_args(argv)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sched, tour = load_tables(root)
    with open(os.path.join(a.playback, "index.json"), encoding="utf-8") as f:
        index = json.load(f)
    by_kind, by_window, by_season_kind = collections.defaultdict(list), collections.defaultdict(list), collections.defaultdict(list)
    plays_by_kind, traced_by_kind = collections.defaultdict(list), collections.defaultdict(list)
    cards = collections.Counter()
    tracesec_by_kind = collections.defaultdict(list)
    longest = []
    for e in index["games"]:
        with open(os.path.join(a.playback, e["file"]), encoding="utf-8") as f:
            g = json.load(f)
        m = measure_game(g)
        k = e["game_kind"]
        by_kind[k].append(m["seconds"])
        by_season_kind[(e["season"], k)].append(m["seconds"])
        plays_by_kind[k].append(m["plays"])
        traced_by_kind[k].append(m["traced"] / max(1, m["plays"]))
        tracesec_by_kind[k].append(m["trace_seconds"])
        cards.update(m["untraced_kinds"])
        w = window_of(e["gid"], sched, tour)
        if w:
            by_window[w].append(m["seconds"])
        longest.append((m["seconds"], e["gid"], m["plays"]))
    out = {"games": len(index["games"]), "constants": {"HOLD_S": HOLD_S, "CARD_S": CARD_S},
           "watching_seconds_at_1x_by_kind": {k: dist(v) for k, v in sorted(by_kind.items())},
           "plays_by_kind": {k: dist(v) for k, v in sorted(plays_by_kind.items())},
           "share_of_plays_with_movement_by_kind": {k: round(sum(v) / len(v), 3) for k, v in sorted(traced_by_kind.items())},
           "recorded_movement_seconds_by_kind": {k: dist(v) for k, v in sorted(tracesec_by_kind.items())},
           "watching_seconds_at_1x_by_window": {k: dist(v) for k, v in sorted(by_window.items())},
           "untraced_play_kinds": dict(cards.most_common()),
           "longest": [{"gid": g, "plays": n, "seconds": round(s, 1)} for s, g, n in sorted(longest, reverse=True)[:5]],
           "shortest": [{"gid": g, "plays": n, "seconds": round(s, 1)} for s, g, n in sorted(longest)[:3]]}
    if a.events:
        ev = collections.defaultdict(list)
        with open(a.events, encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                key = str(r["key"])
                for p in ("week_", "tour_round_", "break_"):
                    if key.startswith(p):
                        key = p.rstrip("_")
                ev[key].append(float(r["seconds"]))
        out["league_compute_seconds_by_event_kind"] = {k: dist(v) for k, v in sorted(ev.items())}
    if a.table:
        def row(name, d):
            return "%-26s n=%-4d min %7.1f  p50 %7.1f  p90 %7.1f  p99 %7.1f  max %7.1f  mean %7.1f" % (name, d["n"], d["min"], d["p50"], d["p90"], d["p99"], d["max"], d["mean"]) if d["n"] else name
        print("games", out["games"], "HOLD_S", HOLD_S, "CARD_S", CARD_S)
        for title in ("watching_seconds_at_1x_by_kind", "watching_seconds_at_1x_by_window", "plays_by_kind", "recorded_movement_seconds_by_kind"):
            print("--", title)
            for k, d in out[title].items():
                print(row(k, d))
        print("-- share of plays with movement", out["share_of_plays_with_movement_by_kind"])
        print("-- untraced play kinds", out["untraced_play_kinds"])
        print("-- longest", out["longest"], "shortest", out["shortest"])
        if "league_compute_seconds_by_event_kind" in out:
            print("-- league compute seconds by event kind")
            for k, d in out["league_compute_seconds_by_event_kind"].items():
                print(row(k, d))
        return 0
    print(json.dumps(out, indent=None if not a.json else 1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
