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
* OOM INCIDENT (`incident`, run by the unit's ExecStopPost=).  A kernel OOM kill of ANY process in the daemon's service cgroup (the daemon or a child) makes systemd stop the unit
  (OOMPolicy=stop, result `oom-kill`) and restart it a few seconds later in a NEW cgroup whose counters are zero: a monitor polling memory.events can miss it. ExecStopPost= runs after
  the stop and before the restart, so the recorder creates PAUSE, then the latch (DIR/TRIPPED kind `oom_kill` source `systemd`, trips.jsonl) with an evidence folder, before any restarted
  daemon exists. The victim is named from the kernel log when the service user can read it and is otherwise reported as UNKNOWN, never assumed to be the daemon. If the latch cannot
  be written (retried), PAUSE still holds, the recorder exits 1 (the stop shows as failed) and says so on stderr; a daemon whose guard folder is not writable holds too. A stop
  that is not an OOM (clean stop, crash, kill -9) records nothing. See docs/OOM_INCIDENT_RECORD.md.
* SAFETY.  P must be an empty folder, or one this daemon filled before; it must not be inside R, nor R inside it. Never point it at a served production feed.
"""
import argparse
import errno
import fcntl
import json
import os
import signal
import subprocess
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


def _fsync_dir(path):
    """Flush a folder's entries (a file's own fsync does not make its NAME durable)."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_trip(guard, kind, detail, source, now):
    """Create DIR/TRIPPED if it does not exist (the FIRST trip is the one kept) and append every trip to DIR/trips.jsonl. Returns True if this call made the latch. The latch and its
    folder entry are flushed to disk before this returns; an OSError means the latch was NOT made."""
    rec = {"kind": kind, "detail": detail, "source": source, "at": now}
    try:
        with open(os.path.join(guard, "trips.jsonl"), "a") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        _fsync_dir(guard)
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
    _fsync_dir(guard)
    return True


class Guard:
    def __init__(self, guard, stale, grace, started):
        self.dir, self.stale, self.grace, self.started = guard, stale, grace, started
        self.said = False

    def step(self, store, now):
        """Called before every tick. Returns True if the broadcast must be held (PAUSE exists)."""
        pause = os.path.join(store, S.PAUSE)
        if not os.access(self.dir, os.W_OK | os.X_OK):
            raise PermissionError(errno.EACCES, "the guard folder is not writable, so an incident could not be recorded", self.dir)       # run_loop holds on any OSError
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


INCIDENT_KIND = "oom_kill"
INCIDENT_TRIES = 3                         # the latch is retried (0.5 s, 1 s) inside the stop timeout before the recorder gives up


def _keyvals(text):
    out = {}
    for ln in (text or "").splitlines():
        parts = ln.split()
        if len(parts) == 2 and parts[1].isdigit():
            out[parts[0]] = int(parts[1])
    return out


def cgroup_events(cgroup):
    """memory.events of the unit's cgroup (it still exists while ExecStopPost runs); None if it cannot be read."""
    try:
        with open(os.path.join(cgroup, "memory.events")) as f:
            return _keyvals(f.read())
    except OSError:
        return None


def parse_oom_victims(text, unit):
    """The kernel's own `oom-kill:` lines -> [{pid, comm, task_memcg, oom_memcg}] for tasks of the cgroup named `unit` (any depth below it). Other cgroups' victims are not this incident's."""
    out = []
    for ln in (text or "").splitlines():
        if "oom-kill:" not in ln:
            continue
        kv = dict(p.split("=", 1) for p in ln.split("oom-kill:", 1)[1].replace(" ", ",").split(",") if "=" in p)
        memcg = kv.get("task_memcg", "")
        if unit and unit not in memcg.split("/"):
            continue
        try:
            pid = int(kv["pid"])
        except (KeyError, ValueError):
            continue
        out.append({"pid": pid, "comm": kv.get("task", "?"), "task_memcg": memcg, "oom_memcg": kv.get("oom_memcg")})
    return out


def read_kernel_log(path=None, timeout=5.0):
    """Recent kernel messages, or None. The service user usually cannot read them; that leaves the victim UNKNOWN, which is recorded as such."""
    try:
        if path:
            with open(path) as f:
                return f.read()
        r = subprocess.run(["journalctl", "-k", "-o", "cat", "--no-pager", "-n", "1000"], capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def is_oom_stop(env, events):
    """systemd says the unit was stopped for an OOM kill (result `oom-kill`), or the cgroup's own counter shows one whatever the stop was called."""
    return env.get("SERVICE_RESULT") == "oom-kill" or (events or {}).get("oom_kill", 0) > 0


def describe_incident(oom):
    if oom["victim_status"] == "identified":
        who = "victim " + ", ".join("pid %d (%s)" % (v["pid"], v["comm"]) for v in oom["victims"])
        who += "; the daemon itself was among them" if oom["daemon_was_victim"] else "; not the daemon (main pid %s)" % oom["daemon_main_pid"]
    else:
        who = "victim unknown (the kernel log could not be read or names nobody in this cgroup)"
    return ("the kernel OOM-killed in the daemon's service cgroup (systemd result %s, main process %s %s, memory.events oom_kill %s): %s. The unit is restarted by systemd; "
            "the broadcast stays held until this latch is released." % (oom["service_result"] or "unknown", oom["exit_code"] or "?", oom["exit_status"] or "?", (oom["cgroup_events"] or {}).get("oom_kill", "unreadable"), who))


def cmd_incident(a):
    now = time.time()
    env = os.environ
    events = cgroup_events(a.cgroup) if a.cgroup else None
    if not is_oom_stop(env, events):
        return 0
    main_pid = int(env["MAINPID"]) if env.get("MAINPID", "").isdigit() and int(env["MAINPID"]) > 0 else None
    log = read_kernel_log(a.kernel_log)
    victims = parse_oom_victims(log, os.path.basename(a.cgroup.rstrip("/")) if a.cgroup else None)
    oom = {"service_result": env.get("SERVICE_RESULT"), "exit_code": env.get("EXIT_CODE"), "exit_status": env.get("EXIT_STATUS"), "daemon_main_pid": main_pid, "cgroup_events": events,
           "kernel_log_read": log is not None, "victims": victims, "victim_status": "identified" if victims else "unknown",
           "daemon_was_victim": (main_pid in [v["pid"] for v in victims]) if (victims and main_pid) else None}
    detail = describe_incident(oom)
    held = False
    try:                                                                        # the hold first: one empty file, and it works even if the latch cannot be written
        open(os.path.join(os.path.abspath(a.root), "store", S.PAUSE), "a").close()
        held = True
    except OSError:
        pass
    err = None
    for i in range(INCIDENT_TRIES):
        try:
            write_trip(a.guard, INCIDENT_KIND, detail, "systemd", now)
            err = None
            break
        except OSError as e:
            err = e
            if i + 1 < INCIDENT_TRIES:
                time.sleep(0.5 * (i + 1))
    if err is not None:
        print(json.dumps({"incident": "unrecorded", "held": held, "error": str(err)[:200], "detail": detail, "at": now}, sort_keys=True), file=sys.stderr, flush=True)
        return 1
    out = {"incident": "recorded", "kind": INCIDENT_KIND, "held": held, "victim_status": oom["victim_status"], "at": now}
    try:                                                                        # the evidence folder is a help: its failure never loses the latch
        d = os.path.join(a.evidence or os.path.join(a.guard, "evidence"), "trip-%s-%s" % (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now)), INCIDENT_KIND))
        os.makedirs(d, exist_ok=True)
        snap = {"kind": INCIDENT_KIND, "detail": detail, "evidence": oom, "at": now, "pause_file": held}
        for name, path in (("store_clock", os.path.join(os.path.abspath(a.root), "store", "clock.json")), ("public_status", os.path.join(a.public, "status.json") if a.public else None)):
            try:
                snap[name] = B.get(path) if path else None
            except Exception as e:
                snap[name] = "unreadable: %s" % e
        with open(os.path.join(d, "evidence.json"), "w") as f:
            json.dump(snap, f, indent=1, sort_keys=True, default=str)
            f.flush()
            os.fsync(f.fileno())
        out["evidence_dir"] = d
    except Exception as e:
        out["evidence_error"] = str(e)[:200]
    print(json.dumps(out, sort_keys=True), file=sys.stderr, flush=True)
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
    p = sub.add_parser("incident", help="run by the unit's ExecStopPost=: record an OOM stop as a latch before systemd restarts the daemon (reads $SERVICE_RESULT, $MAINPID, $EXIT_CODE, $EXIT_STATUS)")
    p.add_argument("--root", required=True)
    p.add_argument("--public", help="the public folder (its status.json goes into the evidence)")
    p.add_argument("--guard", required=True, help="the monitor's folder: where the latch is written")
    p.add_argument("--evidence", help="evidence folder (default DIR/evidence)")
    p.add_argument("--cgroup", help="the unit's cgroup folder: its memory.events is read while it still exists, and its name picks the victims out of the kernel log")
    p.add_argument("--kernel-log", help="read kernel messages from this file instead of journalctl (tests)")
    p.set_defaults(fn=cmd_incident)
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
