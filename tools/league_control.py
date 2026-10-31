#!/usr/bin/env python3
"""Pause, resume and look at the league runner. Changes nothing but one marker file.

  league_control.py pause  [--dir DIR] [--reason TEXT]    fire no further events (an event in progress finishes first)
  league_control.py resume [--dir DIR]                    let the runner carry on
  league_control.py status [--dir DIR] [--json]           paused or not, what the runner says, how long ago it last reported

DIR is the runner's control directory (default /srv/dflv2/league). Pausing and resuming are idempotent and safe at any moment, including while no runner is running
(the marker is simply waiting for the next one).
"""
import argparse
import calendar
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))


def main(argv=None):
    from dflsite import status as S
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=("pause", "resume", "status"))
    ap.add_argument("--dir", default="/srv/dflv2/league")
    ap.add_argument("--reason", default="")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    marker = os.path.join(a.dir, "PAUSED")
    if a.cmd == "pause":
        S.write_atomic(marker, {"paused_at": S.now_iso(), "reason": a.reason[:200]})
        print("paused: no further event will start" if not a.json else json.dumps({"paused": True}))
    elif a.cmd == "resume":
        try:
            os.remove(marker)
        except FileNotFoundError:
            pass
        print("resumed: the runner carries on within a few seconds" if not a.json else json.dumps({"paused": False}))
    else:
        rs = S.read(os.path.join(a.dir, S.RUNNER_FILE)) or {}
        age = None
        if rs.get("updated_at"):
            try:
                age = round(time.time() - calendar.timegm(time.strptime(rs["updated_at"], "%Y-%m-%dT%H:%M:%SZ")))
            except ValueError:
                pass
        doc = {"paused_marker": os.path.exists(marker), "runner_state": rs.get("state"), "events": rs.get("seq"), "last_event_at": rs.get("last_event_at"),
               "next_event_due": rs.get("next_event_due"), "pace_seconds": rs.get("pace_seconds"), "last_report_seconds_ago": age,
               "error": (rs.get("error") or {}).get("type")}
        if a.json:
            print(json.dumps(doc, sort_keys=True))
        else:
            print(f"pause marker: {'PRESENT (paused)' if doc['paused_marker'] else 'absent'}")
            print(f"runner: {doc['runner_state'] or 'no report'}, {doc['events']} events, last event {doc['last_event_at']}, next due {doc['next_event_due']}, pace {doc['pace_seconds']} s")
            print(f"last report {age} s ago" + (f"; ERROR {doc['error']}" if doc["error"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
