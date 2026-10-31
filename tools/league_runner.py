#!/usr/bin/env python3
"""The one simulation runner for a development league: it fires the league's events one at a time, forever or up to a limit, at a set pace.

  league_runner.py LEAGUE_FILE [--pace SECONDS] [--until-events N] [--control-dir DIR] [--poll SECONDS]

* ONE RUNNER. It takes an exclusive lock (`runner.lock` in the control directory). A second runner exits with status 75 and changes nothing. The operating system
  drops the lock when the holder dies, however it dies.
* PAUSE / RESUME. While the file `PAUSED` exists in the control directory the runner fires nothing; remove it (tools/league_control.py resume) and it carries on at
  once. An event that has started always finishes (an event is one atomic commit); the pause takes effect before the next one. The runner can also be stopped with
  SIGTERM: it finishes the event in progress and exits cleanly.
* PACE. `--pace` is the least number of seconds from the start of one event to the start of the next (0 = as fast as the machine allows). The runner does not
  invent a pace of its own: the service file states it, and the owner approves it.
* RECOVERY. The league file is the only state. `Runner.open` heals an interrupted commit, so a runner killed at any instant (even kill -9, or power loss) is
  restarted by systemd and carries on from the last committed event: no event fires twice, no result changes (tests: tests/test_league_runner.py, which compares the
  chain of a killed-and-restarted league with one that was never interrupted).
* STATUS. Every few seconds it rewrites `runner_status.json` (atomically) so that anything that looks can tell running from paused from stopped from failed, and
  how long ago it last did anything.
It never reads or writes the public feed. A different program (tools/export_public_feed.py) publishes what has been committed.
"""
import argparse
import fcntl
import json
import os
import signal
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

PAUSED = "PAUSED"
LOCK = "runner.lock"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("league")
    ap.add_argument("--pace", type=float, default=0.0, help="least seconds from the start of one event to the start of the next (default 0: no waiting)")
    ap.add_argument("--until-events", type=int, help="stop (exit 0) once the league has committed this many events in all")
    ap.add_argument("--control-dir", help="where PAUSED, runner.lock and runner_status.json live (default: the league file's folder)")
    ap.add_argument("--poll", type=float, default=5.0, help="how often, while waiting or paused, it looks at the pause file and refreshes its status")
    ap.add_argument("--die-inside", type=int, help="(tests) exit abruptly in the middle of the Kth commit")
    a = ap.parse_args(argv)
    from dflsite import status as S
    ctl = os.path.abspath(a.control_dir or os.path.dirname(os.path.abspath(a.league)))
    os.makedirs(ctl, exist_ok=True)
    lockfd = os.open(os.path.join(ctl, LOCK), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(json.dumps({"busy": True, "detail": "another runner holds the lock"}), file=sys.stderr)
        return 75
    status_path = os.path.join(ctl, S.RUNNER_FILE)
    pause_path = os.path.join(ctl, PAUSED)
    started = S.now_iso()
    st = {"format": "dfl-runner-status", "state": "running", "pid": os.getpid(), "started_at": started, "updated_at": started, "seq": None, "last_event": None,
          "last_event_at": None, "pace_seconds": a.pace, "next_event_due": None, "paused_since": None, "error": None, "until_events": a.until_events}
    stop = {"flag": False}

    def put(**kw):
        st.update(kw)
        st["updated_at"] = S.now_iso()
        S.write_atomic(status_path, st)

    def on_term(signum, frame):
        stop["flag"] = True
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)
    r = None
    try:
        from dfl import persist
        counter = {"n": 0}

        def hook(point):
            if a.die_inside and point == "journal_written":
                counter["n"] += 1
                if counter["n"] == a.die_inside:
                    os._exit(9)
        r = persist.Runner.open(a.league, crash_hook=hook)
        put(seq=r.seq)
        last_start = None
        while not stop["flag"]:
            if a.until_events is not None and r.seq >= a.until_events:
                break
            if os.path.exists(pause_path):
                if st["state"] != "paused":
                    put(state="paused", paused_since=S.now_iso(), next_event_due=None)
                else:
                    put()
                _sleep(a.poll, stop, pause_path, want_paused=True)
                continue
            if st["state"] == "paused":
                put(state="running", paused_since=None)
            if last_start is not None and a.pace > 0:
                due = last_start + a.pace
                if time.time() < due:
                    put(next_event_due=S.now_iso(due))
                    _sleep(min(a.poll, due - time.time()), stop, pause_path, want_paused=False)
                    continue
            last_start = time.time()
            t0 = time.time()
            key = r.step()
            took = time.time() - t0
            put(seq=r.seq, last_event=str(key), last_event_at=S.now_iso(), last_event_seconds=round(took, 2), next_event_due=S.now_iso(last_start + a.pace) if a.pace > 0 else None)
        put(state="stopped", next_event_due=None)
        return 0
    except BaseException as e:                                             # an error is reported, then the process ends; systemd decides about restarting
        if isinstance(e, SystemExit):
            raise
        try:
            put(state="error", error={"type": type(e).__name__, "message": str(e)[:300], "at": S.now_iso()}, next_event_due=None)
        except OSError:
            pass
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}), file=sys.stderr, flush=True)
        return 1
    finally:
        if r is not None:
            try:
                r.close()
            except Exception:
                pass


def _sleep(seconds, stop, pause_path, want_paused):
    """Sleep up to `seconds`, in short slices, waking early on a stop request or when the pause file's presence changes."""
    end = time.time() + max(0.0, seconds)
    while time.time() < end and not stop["flag"]:
        if os.path.exists(pause_path) != want_paused:
            return
        time.sleep(min(0.2, max(0.0, end - time.time())))


if __name__ == "__main__":
    sys.exit(main())
