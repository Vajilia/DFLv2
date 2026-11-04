#!/usr/bin/env python3
"""The proposed 48-hour broadcast timetable of one DFL year (docs/BROADCAST_PLAN.md).

  broadcast_timetable.py [--slot-s N] [--json] [--wall "2026-11-06T18:00:00-08:00"]

Standard library only; reads data/OFFSEASON.csv, data/season1/{CALENDAR,SCHEDULE,TOUR}.csv (the weekday and kickoff of every game are the same every season).
The timetable is in BROADCAST SECONDS from the offseason opening (0) to the end of the Honors show. Pauses and downtime freeze the broadcast clock, so
they never change these numbers (see the plan, "Clock"). Every segment has a fixed length that does not depend on the game being watched, so the schedule
itself reveals nothing about a game (a long game and a short one occupy the same slot).
"""
import argparse
import csv
import datetime as dt
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# every length is in seconds; the measured watching times (tools/broadcast_measure.py) justify SLOT_S
PARAMS = {
    "slot_s": 960,             # one game window; measured: median 667, p99 796 (regular), longest 905.3 at 1x, so 960 holds every measured game at 1x
    "window_gap_s": 300,       # between two windows of one week: scores, "next kickoff" countdown
    "week_gap_s": 600,         # after a week's last window: the week's public results (standings, injuries, rosters) are revealed here
    "break_s": 480,            # an active-club break week without games
    "trade_close_s": 300,
    "playoff_round_gap_s": 600,
    "bowl_ceremony_s": 600,    # after the Ambassador Bowl (Bowl rings)
    "coronation_ceremony_s": 600,
    "honors_s": 1800,          # Honors and year close (tiara, halls, fan entrant)
    "offseason_s": {           # 11 offseason events, ~49 minutes in all
        "offseason_open": 240, "hiring_open": 180, "hiring_close": 180, "trading_open": 180, "free_agency_open": 240, "draft_round_1": 600,
        "draft_rounds_2_3": 360, "draft_rounds_4_7": 360, "camp_open": 180, "final_cuts": 240, "waivers_clear": 180},
    # PROPOSAL (the data has no kickoff times for these): playoff rounds are run in windows of two simultaneous games
    "playoff_windows": {"wild_card": [2, 2], "divisional": [2, 2], "conference_championships": [2]},
    "budget_s": 48 * 3600,
}
WINDOWS = ["Sat 17:30", "Sun 10:00", "Sun 13:00", "Sun 17:30", "Mon 17:30"]
NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _rows(name):
    with open(os.path.join(ROOT, "data", name), encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def window_counts():
    """({numbered_week: {window: games}}, {calendar_week: {window: games}}) from the Season 1 tables."""
    reg, tour = {}, {}
    for r in _rows("season1/SCHEDULE.csv"):
        w = NAMES[dt.date.fromisoformat(r["date"]).weekday()] + " " + r["kickoff"]
        reg.setdefault(int(r["week"]), {}).setdefault(w, 0)
        reg[int(r["week"])][w] += 1
    for r in _rows("season1/TOUR.csv"):
        w = NAMES[dt.date.fromisoformat(r["date"]).weekday()] + " " + r["kickoff"]
        tour.setdefault(int(r["calendar_week"]), {}).setdefault(w, 0)
        tour[int(r["calendar_week"])][w] += 1
    return reg, tour


def build(params=None):
    P = dict(PARAMS)
    P.update(params or {})
    reg, tour = window_counts()
    segs = []
    t = 0

    def add(kind, label, length, **kw):
        nonlocal t
        segs.append(dict(kind=kind, label=label, start_s=t, end_s=t + length, **kw))
        t += length

    for key, n in P["offseason_s"].items():
        add("offseason", key, n, event=key)
    cal = _rows("season1/CALENDAR.csv")
    tour_rounds = {}
    for i, w in enumerate(sorted(tour), 1):
        tour_rounds[w] = i
    for r in cal:
        cw = int(r["calendar_week"])
        if r["phase"] in ("regular", "break"):
            nw = int(r["numbered_week"]) if r["numbered_week"] else None
            counts = {}
            for w in WINDOWS:
                g = (reg.get(nw, {}).get(w, 0) if nw else 0)
                tg = tour.get(cw, {}).get(w, 0)
                if g or tg:
                    counts[w] = (g, tg)
            if not counts:
                add("break", "break (calendar week %d)" % cw, P["break_s"], calendar_week=cw, event="break_%d" % cw)
                continue
            first = True
            for w, (g, tg) in counts.items():
                if not first:
                    t += P["window_gap_s"]
                first = False
                add("window", ("week %d " % nw if nw else "break week %d " % cw) + w, P["slot_s"], calendar_week=cw, week=nw, window=w,
                    games=g, tour_games=tg, tour_round=tour_rounds.get(cw))
            reveal = (["week_%d" % nw] if nw else []) + (["tour_round_%d" % tour_rounds[cw]] if cw in tour_rounds else [])
            add("recap", "week %d results" % nw if nw else "tour round %d results" % tour_rounds.get(cw, 0), P["week_gap_s"], calendar_week=cw, week=nw,
                reveal=reveal)
            if nw == 8:
                add("trade_close", "trading deadline", P["trade_close_s"], event="trade_close")
        else:
            note = r["note"]
            if note == "Ambassador Bowl":
                add("window", "Ambassador Bowl", P["slot_s"], window="Sun", games=1, event="ambassador_bowl")
                add("ceremony", "Bowl ceremony and rings", P["bowl_ceremony_s"], event="ambassador_bowl")
            elif note == "Diamond Coronation":
                add("window", "Diamond Coronation", P["slot_s"], window="Sun", games=1, event="coronation")
                add("ceremony", "Coronation", P["coronation_ceremony_s"], event="coronation")
            else:
                key = {"wild card round": "wild_card", "divisional round": "divisional", "conference championships": "conference_championships"}[note]
                for j, g in enumerate(P["playoff_windows"][key]):
                    if j:
                        t += P["window_gap_s"]
                    add("window", "%s, window %d" % (note, j + 1), P["slot_s"], window="playoff", games=g, event=key)
                add("recap", note + " results", P["playoff_round_gap_s"], reveal=[key])
    add("honors", "Honors and year close", P["honors_s"], event="year_close")
    return P, segs


def summarize(P, segs):
    total = segs[-1]["end_s"]
    by = {}
    for s in segs:
        by.setdefault(s["kind"], 0)
        by[s["kind"]] += s["end_s"] - s["start_s"]
    # gaps between consecutive segments are part of the total but belong to no segment
    seg_sum = sum(by.values())
    return {"total_s": total, "total_h": round(total / 3600, 2), "reserve_h": round((P["budget_s"] - total) / 3600, 2), "segment_seconds": by,
            "gap_seconds": total - seg_sum, "windows": sum(1 for s in segs if s["kind"] == "window"),
            "games": sum(s.get("games", 0) + s.get("tour_games", 0) for s in segs if s["kind"] == "window"),
            "offseason_min": round(by.get("offseason", 0) / 60, 1)}


def hms(s):
    return "%d:%02d:%02d" % (s // 3600, s // 60 % 60, s % 60)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--slot-s", type=int)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--wall", help="ISO time of the offseason opening: print wall-clock times instead of broadcast hours")
    a = ap.parse_args(argv)
    over = {"slot_s": a.slot_s} if a.slot_s else {}
    P, segs = build(over)
    summ = summarize(P, segs)
    if a.json:
        print(json.dumps({"params": P, "summary": summ, "segments": segs}, indent=1))
        return 0
    start = dt.datetime.fromisoformat(a.wall) if a.wall else None
    for s in segs:
        when = hms(s["start_s"])
        if start:
            when = (start + dt.timedelta(seconds=s["start_s"])).strftime("%a %H:%M")
        extra = ""
        if s["kind"] == "window":
            extra = "  games %d%s" % (s.get("games", 0), (" + %d tour" % s["tour_games"]) if s.get("tour_games") else "")
        print("%-9s %-9s %6.1f min  %s%s" % (when, s["kind"], (s["end_s"] - s["start_s"]) / 60, s["label"], extra))
    print(json.dumps(summ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
