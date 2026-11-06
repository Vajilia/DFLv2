#!/usr/bin/env python3
"""The season PLAN: the 48-hour timetable (tools/broadcast_timetable.py) turned into what the broadcast daemon and the private builder both need.

  broadcast_plan.py [--season N] [--json]

A plan is one league year of broadcast, in NOMINAL broadcast seconds (no overrun applied yet; the daemon shifts items at run time, src/dflsite/season.py):

  items   in timetable order. A WINDOW is a kickoff group (every game of it kicks off together). A BLOCK is everything else: offseason events, break weeks,
          results recaps, ceremonies, the Honors show. A block REVEALS league events (their feed release, replay files) and honours parts at fixed offsets.
  games   which window every regular-season and Tour game belongs to (from data/season1/SCHEDULE.csv and TOUR.csv, the same every season)
  rounds  which windows each postseason round uses; its games are assigned by conference when the round is built
  events  the 47 events of a year in firing order, each one revealed by exactly one block
  season, event_base   the season number and how many league events came before it (0 for the first season): the league's event numbers of this season are
          event_base+1 .. event_base+47, so the builds of a later season carry the league's own (global) event numbers

Nothing here depends on a game's result or length: a plan is the same for every league. It is data for the daemon, not a promise about a result.
Standard library only (the check against the engine's own event calendar lives in the tests).
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import broadcast_timetable as T  # noqa: E402

FORMAT = "dfl-broadcast-plan"
VERSION = 1
GATED = ("wild_card", "divisional", "conference_championships", "ambassador_bowl", "coronation")     # windows whose participants are announced late
HONORS_PARTS = (("tiara", 0), ("halls", 600), ("fan_entrant", 1200))        # offsets inside the Honors show (seconds)
HONORS_EVENT_AT = 1500
RINGS_AT = 0


class PlanError(Exception):
    pass


def window_name(date_iso, kickoff):
    import datetime as dt
    return T.NAMES[dt.date.fromisoformat(date_iso).weekday()] + " " + kickoff


def build_plan(params=None, season=1, event_base=0):
    P, segs = T.build(params)
    items, wcount = [], 0
    cal_windows = {}       # (calendar_week, 'Sat 17:30') -> key       (numbered regular weeks and break weeks with Tour games)
    round_windows = {}     # postseason round -> [keys]
    single = {}            # 'ambassador_bowl' / 'coronation' -> key
    for s in segs:
        if s["kind"] == "window":
            key = "w%03d" % wcount
            wcount += 1
            it = {"kind": "window", "key": key, "label": s["label"], "start_s": s["start_s"], "slot_s": s["end_s"] - s["start_s"], "expected": s.get("games", 0) + s.get("tour_games", 0),
                  "gated": s.get("event") in GATED, "event": s.get("event")}
            if s.get("calendar_week") is not None and s.get("window") in T.WINDOWS:
                cal_windows[(s["calendar_week"], s["window"])] = key
                it["week"], it["calendar_week"], it["window"] = s.get("week"), s["calendar_week"], s["window"]
            elif s.get("event") in ("wild_card", "divisional", "conference_championships"):
                round_windows.setdefault(s["event"], []).append(key)
            elif s.get("event") in ("ambassador_bowl", "coronation"):
                single[s["event"]] = key
            else:
                raise PlanError("a window with no place in the calendar: " + s["label"])
            it["gated"] = it["event"] in GATED
            items.append(it)
            continue
        b = {"kind": "block", "role": s["kind"], "label": s["label"], "start_s": s["start_s"], "end_s": s["end_s"], "events": [], "events_at": 0, "parts": []}
        if s["kind"] == "recap":
            b["events"] = list(s["reveal"])
            if s.get("week") is None and s.get("calendar_week") is not None and s["reveal"][0].startswith("tour_round_"):
                b["events"] = ["break_%d" % s["calendar_week"]] + b["events"]     # a break week that carries a Tour round: its break event is revealed with it
            b["events_at"] = 0
        elif s["kind"] in ("offseason", "break", "trade_close"):
            b["events"] = [s["event"]]
            b["events_at"] = s["end_s"] - s["start_s"]
        elif s["kind"] == "ceremony":
            b["events"] = [s["event"]]
            b["events_at"] = 0
            if s["event"] == "ambassador_bowl":
                b["parts"] = [{"part": "rings", "at": RINGS_AT, "event": "ambassador_bowl"}]
        elif s["kind"] == "honors":
            b["events"] = [s["event"]]
            b["events_at"] = HONORS_EVENT_AT
            b["parts"] = [{"part": p, "at": at, "event": "year_close"} for p, at in HONORS_PARTS]
        else:
            raise PlanError("unknown segment kind " + s["kind"])
        items.append(b)
    games = {}
    reg, tour = _rows("season1/SCHEDULE.csv"), _rows("season1/TOUR.csv")
    cw_of_week = {int(r["numbered_week"]): int(r["calendar_week"]) for r in _rows("season1/CALENDAR.csv") if r["numbered_week"]}
    for r in reg:
        wk = int(r["week"])
        name = window_name(r["date"], r["kickoff"])
        games["reg:w%02d:g%02d" % (wk, int(r["game"]))] = _need(cal_windows, (cw_of_week[wk], name), "regular week %d %s" % (wk, name))
    for r in tour:
        name = window_name(r["date"], r["kickoff"])
        games["tour:r%d:g%02d" % (int(r["tour_round"]), int(r["game"]))] = _need(cal_windows, (int(r["calendar_week"]), name), "tour round %s %s" % (r["tour_round"], name))
    per_window = {}
    for g, k in games.items():
        per_window[k] = per_window.get(k, 0) + 1
    for it in items:
        if it["kind"] == "window" and it["key"] in per_window and per_window[it["key"]] != it["expected"]:
            raise PlanError("%s expects %d games but the season tables give it %d" % (it["label"], it["expected"], per_window[it["key"]]))
    events = []
    for it in items:
        if it["kind"] == "block":
            events += it["events"]
    if len(events) != len(set(events)):
        raise PlanError("an event is revealed twice")
    plan = {"format": FORMAT, "format_version": VERSION, "season": season, "event_base": event_base, "total_s": items[-1]["end_s"], "params": {"slot_s": P["slot_s"], "target_s": P["target_s"]},
            "items": items, "games": games, "rounds": round_windows, "single": single,
            "round_sizes": {k: [it["expected"] for it in items if it["kind"] == "window" and it["key"] in v] for k, v in round_windows.items()}, "events": events}
    return plan


def _need(table, key, what):
    if key not in table:
        raise PlanError("no timetable window for " + what)
    return table[key]


def _rows(name):
    return T._rows(name)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--season", type=int, default=1)
    ap.add_argument("--event-base", type=int, default=0, help="league events that came before this season (47 a season)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    plan = build_plan(season=a.season, event_base=a.event_base)
    if a.json:
        print(json.dumps(plan, indent=1, sort_keys=True))
    else:
        wins = [i for i in plan["items"] if i["kind"] == "window"]
        print("items %d, windows %d, games %d + 12 postseason, events %d, total %d s" % (len(plan["items"]), len(wins), len(plan["games"]), len(plan["events"]), plan["total_s"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
