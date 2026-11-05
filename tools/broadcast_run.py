#!/usr/bin/env python3
"""The season broadcast DAEMON, and its pause/resume switch (docs/BROADCAST_PLAN.md, "Season integration").

  broadcast_run.py run    --root R --public P [--tick 1.0] [--lease 90]
  broadcast_run.py pause  --root R
  broadcast_run.py resume --root R
  broadcast_run.py status --root R

`run` is the ONLY writer of the public directory P. It takes an exclusive lock (a second daemon exits 75 and changes nothing), keeps the broadcast clock, and
every second makes P exactly what the clock's position allows (src/dflsite/season.py): place what is due, remove what is not. It reads the private store
(R/store) and writes nothing there except the clock and its log. It never starts the league, the builder or the exporter, and never touches the engine.

* PAUSE / RESUME.  `pause` creates R/store/PAUSE and `resume` removes it; the daemon notices within a tick. The broadcast clock stands still while paused.
* DEATH.  A killed daemon (kill -9, power loss) leaves a clock that stops by itself when its lease and published coverage run out. The next daemon records
  the gap as a pause and carries on from where the viewers' clocks stopped: no catch-up and nothing skipped.
* BUILDER BEHIND.  If the builder has not finished what the next second needs, the clock stands still at that point (and `status.json` says paused).
* PUBLISH FAILURE.  A copy that fails (disk full, permissions) is logged as JSON on stderr, the clock is not allowed past the coverage already published,
  `status.json` reports a failed update, and the daemon retries every tick.
* CAPS (for a rehearsal).  `--deadline` (epoch seconds or ISO-8601, UTC if no zone) ends the run cleanly at that wall-clock time: the daemon creates PAUSE, writes the
  paused status, logs one JSON line `deadline` on stderr and exits 0; the broadcast stays paused until someone runs `resume`. `--max-public-gb` stops the
  daemon (exit 70, one JSON line `cap_exceeded`) if the public folder grows past that size; it is measured every 5 minutes. Neither cap deletes anything.
* SAFETY.  P must be an empty folder, or one this daemon filled before; it must not be inside R, nor R inside it. Never point it at a served production feed.
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
from dflsite import broadcast as B  # noqa: E402
from dflsite import season as S  # noqa: E402


def check_public(root, public):
    root, public = os.path.realpath(root), os.path.realpath(public)
    if public == root or public.startswith(root + os.sep) or root.startswith(public + os.sep):
        raise SystemExit("the public directory and the private root must be separate folders")
    if os.path.isdir(public) and os.listdir(public) and not os.path.exists(os.path.join(public, "broadcast", "clock.json")):
        raise SystemExit("%s is not empty and was not made by this daemon; refusing to write into it" % public)


def parse_deadline(text):
    """Epoch seconds, or ISO-8601 (read as UTC when it has no zone)."""
    import datetime
    try:
        return float(text)
    except ValueError:
        d = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=datetime.timezone.utc)
        return d.timestamp()


def tree_bytes(path):
    total, stack = 0, [path]
    while stack:
        with os.scandir(stack.pop()) as it:
            for e in it:
                if e.is_dir(follow_symlinks=False):
                    stack.append(e.path)
                elif e.is_file(follow_symlinks=False):
                    total += e.stat(follow_symlinks=False).st_size
    return total


CAP_CHECK_S = 300.0


def cmd_run(a):
    store = os.path.join(os.path.abspath(a.root), "store")
    check_public(a.root, a.public)
    os.makedirs(a.public, exist_ok=True)
    lockfd = os.open(os.path.join(store, "daemon.lock"), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(json.dumps({"busy": True}), file=sys.stderr)
        return 75
    try:
        return run_loop(a, store, lockfd)
    finally:
        os.close(lockfd)                                                      # released at once (a process exit would do the same)


def run_loop(a, store, lockfd):
    stop = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(flag=True))
    signal.signal(signal.SIGINT, lambda *_: stop.update(flag=True))
    bc = S.SeasonBroadcaster(store, a.public, a.lease)
    if bc.clock.state() is None:
        t = time.time()
        bc.clock.start(t, 0.0, a.lease, ready_until=0.0)
        bc.tick(t)                                                   # the same instant: a start is not a downtime
    failing = False
    deadline = parse_deadline(a.deadline) if a.deadline else None
    cap = a.max_public_gb * 1e9 if a.max_public_gb else None
    cap_checked = None
    while not stop["flag"]:
        t0 = time.time()
        if deadline is not None and t0 >= deadline:
            open(os.path.join(store, S.PAUSE), "a").close()               # stay paused after the cap: a restart never continues by itself
            try:
                bc.tick(t0)
            except Exception as e:
                print(json.dumps({"error": type(e).__name__, "detail": str(e)[:200], "at": t0}), file=sys.stderr, flush=True)
            print(json.dumps({"deadline": True, "at": t0, "paused": True}), file=sys.stderr, flush=True)
            return 0
        if cap is not None and (cap_checked is None or t0 - cap_checked >= CAP_CHECK_S):
            cap_checked = t0
            size = tree_bytes(a.public)
            if size > cap:
                print(json.dumps({"cap_exceeded": True, "public_bytes": size, "cap_bytes": int(cap), "at": t0}), file=sys.stderr, flush=True)
                return 70
        try:
            r = bc.tick(t0)
            if failing:
                print(json.dumps({"recovered": True, "at": t0}), file=sys.stderr, flush=True)
                failing = False
            if a.verbose:
                print(json.dumps(r), flush=True)
        except Exception as e:                                            # keep trying: the clock already stands within published coverage
            failing = True
            print(json.dumps({"error": type(e).__name__, "detail": str(e)[:200], "at": t0}), file=sys.stderr, flush=True)
        time.sleep(max(0.0, a.tick - (time.time() - t0)))
    return 0


def cmd_pause(a, on=True):
    path = os.path.join(os.path.abspath(a.root), "store", S.PAUSE)
    if on:
        open(path, "a").close()
    elif os.path.exists(path):
        os.unlink(path)
    return 0


def cmd_status(a):
    store = os.path.join(os.path.abspath(a.root), "store")
    c = B.Clock(store, os.path.join(store, "_unused"))
    st = c.state()
    print(json.dumps({"clock": st, "paused_file": os.path.exists(os.path.join(store, S.PAUSE)), "position_now": c.position(time.time()) if st else None}, sort_keys=True))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    p.add_argument("--root", required=True)
    p.add_argument("--public", required=True)
    p.add_argument("--tick", type=float, default=1.0)
    p.add_argument("--lease", type=float, default=B.LEASE_S)
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--deadline", help="end the run (paused, exit 0) at this time: epoch seconds or ISO-8601 (UTC if no zone)")
    p.add_argument("--max-public-gb", type=float, default=0.0, help="exit 70 if the public folder grows past this size (checked every 5 minutes)")
    p.set_defaults(fn=cmd_run)
    for name in ("pause", "resume", "status"):
        p = sub.add_parser(name)
        p.add_argument("--root", required=True)
        p.set_defaults(fn={"pause": cmd_pause, "resume": lambda a: cmd_pause(a, False), "status": cmd_status}[name])
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
