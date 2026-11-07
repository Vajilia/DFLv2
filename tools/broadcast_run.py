#!/usr/bin/env python3
"""The season broadcast DAEMON, and its pause/resume switch (docs/BROADCAST_PLAN.md, "Season integration").

  broadcast_run.py run    --root R --public P [--tick 1.0] [--lease 90] [--deadline T] [--max-public-gb N] [--guard DIR]
  broadcast_run.py pause  --root R
  broadcast_run.py resume --root R [--guard DIR]
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
  For RECURRING operation use `--max-public-gb-per-season G [--public-base-gb B] [--max-public-ceiling-gb C]` instead (never both): the limit is B + G x the seasons that have
  been on air (store/carry.json), at most C; see tools/publiccap.py for why a fixed number cannot work past season 3.
* GUARD (for a rehearsal).  `--guard DIR` (the monitor's folder) makes the daemon obey the monitor. If DIR/TRIPPED exists the daemon creates PAUSE at once and keeps it
  (the clock stands; the process stays up so status.json says paused); `resume --guard DIR` refuses while TRIPPED exists. If the monitor stops proving it is alive
  (DIR/heartbeat not refreshed for --guard-stale seconds, or never written within --guard-grace seconds of the daemon's start) the daemon trips itself
  (writes DIR/TRIPPED with kind monitor_silent, appends DIR/trips.jsonl) and pauses. A trip never deletes anything and is cleared only by
  `rehearsal_check.py release` followed by `resume`. See docs/BROADCAST_REHEARSAL.md.
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
sys.path.insert(0, os.path.join(ROOT, "tools"))
import publiccap as PC  # noqa: E402


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


tree_bytes = PC.tree_bytes                                                          # kept under its old name (tests, tools)


def current_cap(a, store):
    """The public folder's limit in bytes right now, or None. `--max-public-gb` is the rehearsal's fixed number; `--max-public-gb-per-season` (tools/publiccap.py) is the
    recurring one: base + per-season x seasons on air, read from store/carry.json, bounded by the ceiling. The two are never combined (check_arguments)."""
    if getattr(a, "max_public_gb_per_season", 0.0):
        return PC.cap_bytes(store, a.max_public_gb_per_season, a.public_base_gb, a.max_public_ceiling_gb or None)
    return int(a.max_public_gb * 1e9) if a.max_public_gb else None


CAP_CHECK_S = 300.0
GUARD_STALE_S = 120.0                     # the monitor refreshes its heartbeat every 5 s while it makes progress
GUARD_GRACE_S = 240.0                     # time allowed after the daemon starts for the monitor's first heartbeat (a heartbeat file older than the daemon's start does not count as one)


def trip_file(guard):
    return os.path.join(guard, "TRIPPED")


def write_trip(guard, kind, detail, source, now):
    """Create DIR/TRIPPED if it does not exist (the FIRST trip is the one kept) and append every trip to DIR/trips.jsonl. Returns True if this call made the latch."""
    rec = {"kind": kind, "detail": detail, "source": source, "at": now}
    try:
        with open(os.path.join(guard, "trips.jsonl"), "a") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass
    try:
        fd = os.open(trip_file(guard), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as f:
        json.dump(rec, f, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    return True


class Guard:
    def __init__(self, guard, stale, grace, started):
        self.dir, self.stale, self.grace, self.started = guard, stale, grace, started
        self.said = False

    def step(self, store, now):
        """Called before every tick. Returns True if the broadcast must be held (PAUSE exists)."""
        pause = os.path.join(store, S.PAUSE)
        if not os.path.exists(trip_file(self.dir)):
            hb = os.path.join(self.dir, "heartbeat")
            try:
                mtime = os.path.getmtime(hb)
                age = now - mtime
            except OSError:
                mtime = age = None
            if age is not None and mtime < self.started:
                # Written by a monitor run that ended before this daemon started (the folder outlives a host outage): hours old at a boot. That is "the monitor has not written one
                # yet", the same as a first install, and gets the same grace measured from the daemon's own start; it is not "the monitor went silent" (tools/startup_check.py).
                age = None
            if (age is None and now - self.started > self.grace) or (age is not None and age > self.stale):
                detail = ("no heartbeat from the monitor for %.0f s" % age) if age is not None else ("the monitor wrote no new heartbeat in %.0f s since this daemon started" % (now - self.started))
                write_trip(self.dir, "monitor_silent", detail, "daemon", now)
        if os.path.exists(trip_file(self.dir)):
            if not os.path.exists(pause):
                open(pause, "a").close()
            if not self.said:
                self.said = True
                try:
                    rec = json.load(open(trip_file(self.dir)))
                except (OSError, ValueError):
                    rec = {"kind": "unreadable"}
                print(json.dumps({"tripped": True, "kind": rec.get("kind"), "detail": rec.get("detail"), "at": now, "paused": True}), file=sys.stderr, flush=True)
            return True
        self.said = False
        return False


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
    cap_on = bool(a.max_public_gb or a.max_public_gb_per_season)
    cap_checked = None
    guard = Guard(a.guard, a.guard_stale, a.guard_grace, time.time()) if a.guard else None
    while not stop["flag"]:
        t0 = time.time()
        if guard is not None:
            try:
                guard.step(store, t0)                                        # before the tick: a held broadcast never advances past this second
            except OSError as e:
                print(json.dumps({"error": "guard", "detail": str(e)[:200], "at": t0}), file=sys.stderr, flush=True)
                open(os.path.join(store, S.PAUSE), "a").close()              # cannot read the monitor's folder: hold, never run unwatched
        if deadline is not None and t0 >= deadline:
            open(os.path.join(store, S.PAUSE), "a").close()               # stay paused after the cap: a restart never continues by itself
            try:
                bc.tick(t0)
            except Exception as e:
                print(json.dumps({"error": type(e).__name__, "detail": str(e)[:200], "at": t0}), file=sys.stderr, flush=True)
            print(json.dumps({"deadline": True, "at": t0, "paused": True}), file=sys.stderr, flush=True)
            return 0
        if cap_on and (cap_checked is None or t0 - cap_checked >= CAP_CHECK_S):
            cap_checked = t0
            size = tree_bytes(a.public)
            cap = current_cap(a, store)                                       # read AFTER the measurement: a hand-over during the count only raises it
            if size > cap:
                print(json.dumps({"cap_exceeded": True, "public_bytes": size, "cap_bytes": int(cap), "seasons_on_air": PC.seasons_on_air(store), "at": t0}), file=sys.stderr, flush=True)
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
    if not on and getattr(a, "guard", None) and os.path.exists(trip_file(a.guard)):
        print("refusing to resume: %s exists (the monitor or the daemon tripped). Read it, keep the evidence, then run  rehearsal_check.py release --guard %s --reason ..." % (trip_file(a.guard), a.guard), file=sys.stderr)
        return 3
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
    p.add_argument("--max-public-gb", type=float, default=0.0, help="exit 70 if the public folder grows past this size (checked every 5 minutes): the REHEARSAL's fixed cap")
    PC.add_arguments(p)
    p.add_argument("--guard", help="the monitor's folder: obey DIR/TRIPPED and trip when DIR/heartbeat goes stale (rehearsal)")
    p.add_argument("--guard-stale", type=float, default=GUARD_STALE_S)
    p.add_argument("--guard-grace", type=float, default=GUARD_GRACE_S)
    p.set_defaults(fn=cmd_run)
    for name in ("pause", "resume", "status"):
        p = sub.add_parser(name)
        p.add_argument("--root", required=True)
        if name == "resume":
            p.add_argument("--guard", help="refuse to resume while DIR/TRIPPED exists")
        p.set_defaults(fn={"pause": cmd_pause, "resume": lambda a: cmd_pause(a, False), "status": cmd_status}[name])
    a = ap.parse_args(argv)
    if a.cmd == "run":
        PC.check_arguments(a, ap)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
