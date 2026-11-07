#!/usr/bin/env python3
"""The unattended-operation supervisor of the broadcast (M6; docs/UNATTENDED_OPERATION.md).

  broadcast_supervisor.py run   --root R [--public P] [options]     the service: one builder action at a time, ahead of the clock, until stopped
  broadcast_supervisor.py once  --root R [--public P] [options]     one cycle, then print the health document (for a person, a test or a cron job)
  broadcast_supervisor.py check --root R [--max-age-s 180]          read the health file and exit 0 ok, 1 warning, 2 critical or stopped, 3 unknown (missing or stale)

What it does, and all of it. It looks at the store (read-only) and, when something is due, runs ONE existing builder command as a child process:

  * the season on air is not completely built      ->  `broadcast_build.py step --events 1`
  * the season on air is complete and has no successor  ->  `broadcast_build.py next-season`, then `step` until that season is complete
  * the successor is complete                      ->  nothing: never more than one season ahead (the disk is the limit, not the builder)

It never fires the league, never opens the engine and never touches `src/dfl`, `data`, `site` or any league rule: everything it does, the builder does, with the builder's own
exclusive lock (tools/rootlock.py), so two builders cannot run (a second supervisor is refused too: ROOT/supervisor/.lock), a recovery set and a person's manual step are
waited for (`busy` is not a failure), and a kill at any instant leaves what the builder always leaves: the last complete builds.json (the builder resumes from it).

What it does NOT do. It does not control the broadcast: the daemon (src/dflsite/season.py) already holds the clock at the first item that is not built and at the end of a
season whose successor is not on air, and never skips viewing time; the supervisor only measures how far ahead the builder is and says so (`broadcast.holding`, `lead_s`).
It never writes the public folder (it may only read its status.json), never deletes, moves or truncates anything (a test scans this file for such calls), starts and stops
no service, sends nothing and holds no credential. Its only files are under ROOT/supervisor/: `health.json`, `state.json` and `.lock`.

Capacity. Before every builder action it reads free bytes and free inodes of the volume the root is on. A step needs the floor plus a step margin; preparing a season needs the
floor plus a whole season's estimate. If they are not there it builds nothing, changes nothing and reports `capacity_wait` / `capacity_insufficient` (not a failure, no retry
counter); it looks again on every cycle and goes on by itself when room appears. A disk that fills during a step ("No space left on device") is treated the same way.

Health (ROOT/supervisor/health.json, format dfl-supervisor-health 1, rewritten atomically on every cycle) carries `status` (ok, warning, critical), `state` (running, stopped),
`problems` (code, severity, detail, since) and the builder, capacity, broadcast and seasons sections. It is PRIVATE: it names the builder's progress and the horizon, which the public
status.json never does. `check` turns it into an exit code any poller understands; a health file that stopped being written is `unknown`, never `ok`.

Exit codes: 0 stopped by a signal (run) / done (once); 2 refused (no store, bad arguments); 75 another supervisor holds the lock.
"""
import argparse
import calendar
import fcntl
import json
import os
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import broadcast_build as BB  # noqa: E402
import rootlock as RL  # noqa: E402
from dflsite import broadcast as B  # noqa: E402
from dflsite import season as S  # noqa: E402
from dflsite import status as ST  # noqa: E402

FORMAT = "dfl-supervisor-health"
VERSION = 1
GB = 1024 ** 3
EXIT_BUSY = RL.EXIT_BUSY
SEVERITY = {"warning": 1, "critical": 2}
NO_SPACE = ("No space left on device", "Disk quota exceeded", "ENOSPC")
AHEAD = 1                                    # seasons prepared beyond the one on air (a constant: every season held ahead is disk)
HOLD_TOL_S = 1.5


class Refuse(Exception):
    pass


class Busy(Exception):
    pass


def capacity(path):
    """{free_bytes, free_inodes, total_inodes} of the volume `path` is on (what an unprivileged writer can use)."""
    v = os.statvfs(path)
    return {"free_bytes": int(v.f_bavail * v.f_frsize), "free_inodes": int(v.f_favail), "total_inodes": int(v.f_files)}


class Config:
    def __init__(self, root, public=None, builder_cmd=None, capacity_fn=None, floor_gb=8.0, step_margin_gb=0.5, season_gb=2.0, inode_floor=20000, step_inodes=5000,
                 season_inodes=130000, interval=30.0, step_timeout=3600.0, backoff_base=60.0, backoff_max=1800.0, capacity_retry_s=300.0, escalate_after=3,
                 busy_warn_s=900.0, stall_s=300.0, hold_critical_s=1800.0, retry_now=False):
        self.root = os.path.abspath(root)
        self.public = os.path.abspath(public) if public else None
        self.builder_cmd = list(builder_cmd) if builder_cmd else [sys.executable, os.path.join(ROOT, "tools", "broadcast_build.py")]
        self.capacity_fn = capacity_fn or capacity
        self.floor_gb, self.step_margin_gb, self.season_gb = floor_gb, step_margin_gb, season_gb
        self.inode_floor, self.step_inodes, self.season_inodes = inode_floor, step_inodes, season_inodes
        self.interval, self.step_timeout = interval, step_timeout
        self.backoff_base, self.backoff_max, self.capacity_retry_s = backoff_base, backoff_max, capacity_retry_s
        self.escalate_after, self.busy_warn_s, self.stall_s, self.hold_critical_s = escalate_after, busy_warn_s, stall_s, hold_critical_s
        self.retry_now = retry_now


def iso(t):
    return ST.now_iso(t)


def parse_iso(s):
    try:
        return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError):
        return None


class Supervisor:
    def __init__(self, cfg):
        self.cfg = cfg
        self.dir = os.path.join(cfg.root, "supervisor")
        self.store = os.path.join(cfg.root, "store")
        self.health_path = os.path.join(self.dir, "health.json")
        self.state_path = os.path.join(self.dir, "state.json")
        self.started_at = time.time()
        self.cycle_n = 0
        self.stop = False
        self._lock_fd = None
        self._child = None
        self.st = None

    # -------------------------------------------------------------------------------------------------------------------------- the singleton
    def acquire(self):
        os.makedirs(self.dir, exist_ok=True)
        fd = os.open(os.path.join(self.dir, ".lock"), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            who = ""
            try:
                who = os.pread(fd, 200, 0).decode("utf-8", "replace").strip()
            except OSError:
                pass
            os.close(fd)
            raise Busy("another supervisor holds %s (%s); nothing was changed" % (os.path.join(self.dir, ".lock"), who or "no details"))
        info = json.dumps({"pid": os.getpid(), "since": iso(time.time())}).encode()
        os.pwrite(fd, info.ljust(200), 0)                              # a fixed-width record overwritten in place: nothing is truncated
        self._lock_fd = fd

    def release(self):
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    # -------------------------------------------------------------------------------------------------------------------------- persistent state (a convenience: losing it loses only a backoff)
    def load_state(self):
        d = ST.read(self.state_path) or {}
        st = {"consecutive_failures": 0, "last_error": None, "last_action": None, "last_success_at": None, "next_attempt_at": None, "builder_state": "idle", "busy_since": None,
              "problems_since": {}, "capacity_error": False}
        st.update({k: d[k] for k in st if k in d})
        if self.cfg.retry_now:
            st["next_attempt_at"] = None
        return st

    def save_state(self):
        os.makedirs(self.dir, exist_ok=True)
        ST.write_atomic(self.state_path, self.st)

    # -------------------------------------------------------------------------------------------------------------------------- what the store says (read-only)
    def observe(self, now):
        if not os.path.isdir(self.store):
            raise Refuse("%s has no store: initialise it with broadcast_build.py init first" % self.cfg.root)
        P = BB.paths(self.cfg.root)
        carry = None
        cpath = os.path.join(self.store, S.CARRY)
        if os.path.exists(cpath):
            carry = B.get(cpath)
        on_air = int(carry["season"]) if carry else 1
        plan = B.get(os.path.join(P["bdir"], S.PLAN))
        builds = BB.load_builds(P)
        base = int(plan.get("event_base", 0))
        done = max([int(k) for k in builds["events"]] or [base])
        total = len(plan["events"])
        o = {"P": P, "carry": carry, "on_air": on_air, "target": int(P["season_n"]), "plan": plan, "builds": builds, "built": done - base, "total": total,
             "complete": done - base >= total}
        o["continuous"] = os.path.exists(os.path.join(self.store, S.CONTINUOUS))
        return o

    def due(self, o):
        """The one thing the builder should do next, or None."""
        if not o["complete"]:
            return "step"
        if o["target"] <= o["on_air"] + AHEAD - 1:
            return "next-season"
        return None

    def broadcast(self, o, now):
        """What the broadcast clock is doing and how far ahead the prepared content reaches (read-only; never the daemon's business)."""
        out = {"clock_state": None, "position_s": None, "horizon_s": None, "horizon_why": None, "lead_s": None, "holding": False, "season_on_air": o["on_air"], "view_error": None}
        st = None
        try:
            st = B.get(os.path.join(self.store, "clock.json"))
        except (OSError, ValueError):
            pass
        if not st:
            return out, None
        out["clock_state"] = st.get("state")
        pos = B.position(st, now)
        out["position_s"] = None if pos is None else round(pos, 1)
        try:
            bdir = os.path.join(self.store, S.SEASONS, "s%03d" % o["on_air"]) if o["carry"] else self.store
            plan, builds = B.get(os.path.join(bdir, S.PLAN)), B.get(os.path.join(bdir, S.BUILDS))
            ndir = os.path.join(self.store, S.SEASONS, "s%03d" % (o["on_air"] + 1))
            nexists = os.path.exists(os.path.join(ndir, S.PLAN)) and os.path.exists(os.path.join(ndir, S.BUILDS))
            v = S.materialize(plan, builds, o["carry"], hold_at_end=o["carry"] is not None or nexists or o["continuous"])
            out["horizon_s"], out["horizon_why"] = v.horizon, v.horizon_why
            if v.complete:                         # the prepared content goes on into the successor's, as far as that is built
                lead = v.end_s - pos
                if nexists:
                    nv = S.materialize(B.get(os.path.join(ndir, S.PLAN)), B.get(os.path.join(ndir, S.BUILDS)))
                    lead += nv.horizon if nv.horizon is not None else nv.end_s
                out["lead_s"] = round(lead, 1)
            elif v.horizon is not None:
                out["lead_s"] = round(v.horizon - pos, 1)
            if pos is not None and v.horizon is not None and st.get("state") == "running" and pos >= v.horizon - HOLD_TOL_S:
                out["holding"] = True
        except (S.SeasonError, KeyError, ValueError, OSError) as e:
            out["view_error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
        return out, st

    # -------------------------------------------------------------------------------------------------------------------------- capacity
    def capacity_need(self, action):
        c = self.cfg
        step = c.floor_gb * GB + c.step_margin_gb * GB
        inodes = c.inode_floor + c.step_inodes
        if action == "next-season":
            return step + c.season_gb * GB, inodes + c.season_inodes, "a whole season"
        return step, inodes, "one event"

    @staticmethod
    def _same_volume(a, b):
        return os.stat(a).st_dev == os.stat(b).st_dev

    def capacity_check(self, action):
        c = self.cfg
        cap = c.capacity_fn(self.cfg.root)
        need_b, need_i, what = self.capacity_need(action or "step")
        low_b = c.floor_gb * GB + c.step_margin_gb * GB + 2 * c.season_gb * GB
        low_i = c.inode_floor + c.step_inodes + 2 * c.season_inodes
        reasons = []
        if cap["free_bytes"] < need_b:
            reasons.append("free disk %.2f GiB is below the %.2f GiB needed (floor %.2f + %s) to build %s" % (cap["free_bytes"] / GB, need_b / GB, c.floor_gb, "step margin %.2f" % c.step_margin_gb
                                                                                                           if action != "next-season" else "step margin %.2f + season %.2f" % (c.step_margin_gb, c.season_gb), what))
        known = cap.get("total_inodes", 1) != 0
        if known and cap["free_inodes"] < need_i:
            reasons.append("free inodes %d are below the %d needed (floor %d + %s)" % (cap["free_inodes"], need_i, c.inode_floor, what))
        low = cap["free_bytes"] < low_b or (known and cap["free_inodes"] < low_i)
        out = {"free_bytes": cap["free_bytes"], "free_inodes": cap["free_inodes"], "floor_bytes": int(c.floor_gb * GB), "inode_floor": c.inode_floor, "ok": not reasons, "low": bool(low and not reasons),
               "reasons": reasons, "public": None}
        if c.public and os.path.isdir(c.public) and not self._same_volume(c.public, self.cfg.root):
            p = c.capacity_fn(c.public)
            out["public"] = {"free_bytes": p["free_bytes"], "free_inodes": p["free_inodes"], "ok": p["free_bytes"] >= c.floor_gb * GB}
        return out

    # -------------------------------------------------------------------------------------------------------------------------- running the builder
    def run_builder(self, args):
        """-> (returncode, output tail). One child in its own process group, killed with it on a signal or a timeout."""
        cmd = self.cfg.builder_cmd + args
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        self._child = p
        t0 = time.time()
        out = ""
        try:
            while True:
                try:
                    out, _ = p.communicate(timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    if self.stop:
                        self._kill(p)
                        out, _ = p.communicate()
                        return -signal.SIGTERM, "stopped by a signal"
                    if time.time() - t0 > self.cfg.step_timeout:
                        self._kill(p)
                        out, _ = p.communicate()
                        return -signal.SIGKILL, "builder step timed out after %d s" % self.cfg.step_timeout
        finally:
            self._child = None
        return p.returncode, (out or "")[-600:]

    def _kill(self, p):
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass

    def act(self, action, now):
        st = self.st
        args = ["next-season", "--root", self.cfg.root] if action == "next-season" else ["step", "--root", self.cfg.root, "--events", "1"]
        st["last_action"] = action
        rc, out = self.run_builder(args)
        if rc == 0:
            st.update(consecutive_failures=0, last_error=None, last_success_at=now, next_attempt_at=None, busy_since=None, capacity_error=False)
            return "ok"
        if rc == EXIT_BUSY:
            st["busy_since"] = st["busy_since"] or now
            return "busy"
        if self.stop:
            return "stopped"
        st["busy_since"] = None
        text = out.strip().replace("\n", " | ")[-500:]
        if any(k in out for k in NO_SPACE):
            st.update(last_error="%s: the builder reported no space: %s" % (action, text), capacity_error=True, next_attempt_at=now + self.cfg.capacity_retry_s)
            return "capacity"
        n = st["consecutive_failures"] + 1
        delay = min(self.cfg.backoff_max, self.cfg.backoff_base * 2 ** (n - 1))
        st.update(consecutive_failures=n, last_error="%s failed (exit %s): %s" % (action, rc, text), capacity_error=False, next_attempt_at=now + delay)
        return "failed"

    # -------------------------------------------------------------------------------------------------------------------------- one cycle
    def cycle(self, now=None, act=True):
        now = time.time() if now is None else now
        self.cycle_n += 1
        if self.st is None:
            self.st = self.load_state()
        st = self.st
        o = self.observe(now)
        action = self.due(o)
        cap = self.capacity_check(action)
        outcome = None
        if act and action and not self.stop:
            if st["next_attempt_at"] is not None and now < st["next_attempt_at"]:
                outcome = "waiting"
            elif not cap["ok"]:
                outcome = "capacity_wait"
            else:
                outcome = self.act(action, now)
                if outcome == "ok":
                    o = self.observe(now)
                    action = self.due(o)
                    cap = self.capacity_check(action)
        health = self.health(o, action, cap, outcome, now)
        if act:
            self.save_state()
            self.write_health(health)
        return health

    def builder_state(self, action, cap, outcome):
        st = self.st
        if outcome == "busy":
            return "busy"
        if outcome == "capacity" or (action and not cap["ok"]) or (outcome == "waiting" and st["capacity_error"]):
            return "capacity_wait"
        if st["consecutive_failures"] > 0:
            return "failed"
        return "ahead" if action is None else "building"

    def health(self, o, action, cap, outcome, now):
        st, c = self.st, self.cfg
        bstate = self.builder_state(action, cap, outcome)
        st["builder_state"] = bstate
        bc, clock = self.broadcast(o, now)
        found = {}

        def problem(code, severity, detail):
            found[code] = {"code": code, "severity": severity, "detail": detail}

        if st["consecutive_failures"] > 0:
            problem("builder_failed", "critical" if st["consecutive_failures"] >= c.escalate_after else "warning",
                    "%d consecutive failure(s); last: %s; next attempt %s" % (st["consecutive_failures"], st["last_error"], iso(st["next_attempt_at"]) if st["next_attempt_at"] else "now"))
        if st["busy_since"] is not None and now - st["busy_since"] > c.busy_warn_s:
            problem("builder_busy_long", "warning", "the builder lock has been held by someone else for %d s (a manual step, a recovery set or a stuck process)" % (now - st["busy_since"]))
        if bstate == "capacity_wait" or not cap["ok"]:
            detail = "; ".join(cap["reasons"]) or st["last_error"] or "the builder reported no space"
            problem("capacity_insufficient", "critical", "nothing is being built: " + detail)
        elif cap["low"]:
            problem("capacity_low", "warning", "free disk %.2f GiB / %d inodes will not hold the season after the next one: add room before it is needed (nothing is deleted automatically)" % (cap["free_bytes"] / GB, cap["free_inodes"]))
        if cap["public"] and not cap["public"]["ok"]:
            problem("public_capacity_low", "critical", "the public volume has %.2f GiB free, below the %.2f GiB floor" % (cap["public"]["free_bytes"] / GB, c.floor_gb))
        if bc["holding"]:
            problem("broadcast_holding", "warning", "the broadcast is held at the prepared-content boundary (%s); lead %s s" % (bc["horizon_why"], bc["lead_s"]))
        if bc["clock_state"] == "paused" and not bc["holding"]:
            problem("broadcast_paused", "warning", "the broadcast clock is paused (the PAUSE switch or the guard)")
        if clock and clock.get("state") == "running":
            lease = clock.get("lease_until")
            pub = ST.read(os.path.join(c.public, ST.FILE)) if c.public else None
            seen = parse_iso(pub.get("checked_at")) if pub else None
            age = None if seen is None else now - seen
            if (lease is not None and now > lease) or (age is not None and age > c.stall_s):
                problem("publication_stalled", "critical", "the daemon's lease %s and its public heartbeat is %s old: publication has stopped" % (
                    "ran out" if lease is not None and now > lease else "is current", "%d s" % age if age is not None else "unknown"))
        if c.public:
            pub = ST.read(os.path.join(c.public, ST.FILE))
            if pub and isinstance(pub.get("export"), dict) and pub["export"].get("ok") is False and clock and clock.get("state") == "running":
                problem("publication_error", "critical", "the public status says the last publication failed")
        if not o["continuous"]:
            problem("store_not_continuous", "warning", "the store is not marked continuous (broadcast_build.py continuous): the clock would run past the end of a season instead of holding for the next")
        if bc["view_error"]:
            problem("view_error", "warning", "the broadcast view could not be computed: " + bc["view_error"])
        since = st["problems_since"]
        for code in list(since):
            if code not in found:
                del since[code]
        for code, p in found.items():
            since.setdefault(code, now)
            p["since"] = iso(since[code])
            if code == "broadcast_holding" and now - since[code] > c.hold_critical_s:
                p["severity"] = "critical"
        problems = sorted(found.values(), key=lambda p: (-SEVERITY[p["severity"]], p["code"]))
        status = "ok" if not problems else max((p["severity"] for p in problems), key=lambda s: SEVERITY[s])
        ahead = [s for s in range(o["on_air"] + 1, o["target"] + 1)]
        return {"format": FORMAT, "format_version": VERSION, "status": status, "state": "running", "checked_at": now, "checked_at_iso": iso(now), "pid": os.getpid(),
                "started_at": self.started_at, "cycle": self.cycle_n, "problems": problems,
                "builder": {"state": bstate, "season": o["target"], "built": o["built"], "total": o["total"], "last_action": st["last_action"],
                            "consecutive_failures": st["consecutive_failures"], "last_success_at": st["last_success_at"], "last_error": st["last_error"], "next_attempt_at": st["next_attempt_at"]},
                "capacity": cap, "broadcast": bc,
                "seasons": {"on_air": o["on_air"], "target": o["target"], "prepared_ahead": len(ahead), "ahead_complete": bool(ahead) and o["complete"]}}

    def write_health(self, doc):
        os.makedirs(self.dir, exist_ok=True)
        ST.write_atomic(self.health_path, doc)

    def stopped(self, now=None):
        now = time.time() if now is None else now
        try:
            doc = ST.read(self.health_path) or {}
            doc.update(state="stopped", status="critical", checked_at=now, checked_at_iso=iso(now), pid=os.getpid())
            doc["problems"] = [{"code": "supervisor_stopped", "severity": "critical", "detail": "the supervisor was stopped by a signal; nothing is building", "since": iso(now)}] + [
                p for p in doc.get("problems", []) if p.get("code") != "supervisor_stopped"]
            self.write_health(doc)
        except OSError:
            pass


# ------------------------------------------------------------------------------------------------------------------------------ check
def check(path, now=None, max_age_s=180.0):
    """-> (exit code, JSON text): 0 ok, 1 warning, 2 critical or stopped, 3 unknown (no file, unreadable, or not written lately)."""
    now = time.time() if now is None else now
    doc = ST.read(path)
    if doc is None or doc.get("format") != FORMAT:
        return 3, json.dumps({"status": "unknown", "detail": "no readable health file at %s" % path})
    age = now - float(doc.get("checked_at", 0))
    if age > max_age_s:
        return 3, json.dumps({"status": "unknown", "detail": "the health file was last written %d s ago (limit %d s): the supervisor is not running" % (age, max_age_s), "last": doc.get("checked_at_iso")})
    if doc.get("state") == "stopped":
        return 2, json.dumps({"status": "critical", "detail": "the supervisor reports it is stopped", "problems": doc.get("problems", [])})
    code = {"ok": 0, "warning": 1, "critical": 2}.get(doc.get("status"), 3)
    return code, json.dumps({"status": doc.get("status"), "problems": doc.get("problems", []), "builder": doc.get("builder", {}).get("state"), "checked_at": doc.get("checked_at_iso")})


# ------------------------------------------------------------------------------------------------------------------------------ command line
def add_options(p):
    p.add_argument("--root", required=True, help="the builder root (R/league, R/store)")
    p.add_argument("--public", help="the public folder (read only: its status.json is looked at, nothing is written)")
    p.add_argument("--floor-gb", type=float, default=8.0, help="free disk the builder never goes below (keep it above the monitor's floor so building stops before the broadcast does)")
    p.add_argument("--step-margin-gb", type=float, default=0.5, help="room one event needs on top of the floor")
    p.add_argument("--season-gb", type=float, default=2.0, help="room a whole season needs (private store and, later, its public copy: about 2.0 GB measured)")
    p.add_argument("--inode-floor", type=int, default=20000)
    p.add_argument("--step-inodes", type=int, default=5000)
    p.add_argument("--season-inodes", type=int, default=130000, help="about 59,000 public and 60,000 private files a season")
    p.add_argument("--interval", type=float, default=30.0, help="seconds between cycles when there is nothing to build")
    p.add_argument("--step-timeout", type=float, default=3600.0)
    p.add_argument("--backoff-base", type=float, default=60.0)
    p.add_argument("--backoff-max", type=float, default=1800.0)
    p.add_argument("--escalate-after", type=int, default=3, help="consecutive builder failures that make the problem critical")
    p.add_argument("--hold-critical-s", type=float, default=1800.0)
    p.add_argument("--stall-s", type=float, default=300.0)
    p.add_argument("--busy-warn-s", type=float, default=900.0)
    p.add_argument("--retry-now", action="store_true", help="ignore a remembered backoff (after a person fixed the cause)")
    p.add_argument("--builder-cmd", help="JSON list replacing the builder command (tests only)")


def make(a):
    return Supervisor(Config(root=a.root, public=a.public, builder_cmd=json.loads(a.builder_cmd) if a.builder_cmd else None, floor_gb=a.floor_gb, step_margin_gb=a.step_margin_gb,
                             season_gb=a.season_gb, inode_floor=a.inode_floor, step_inodes=a.step_inodes, season_inodes=a.season_inodes, interval=a.interval, step_timeout=a.step_timeout,
                             backoff_base=a.backoff_base, backoff_max=a.backoff_max, escalate_after=a.escalate_after, hold_critical_s=a.hold_critical_s, stall_s=a.stall_s,
                             busy_warn_s=a.busy_warn_s, retry_now=a.retry_now))


def cmd_run(a):
    sup = make(a)
    try:
        sup.acquire()
    except Busy as e:
        print(json.dumps({"busy": True, "detail": str(e)}), file=sys.stderr)
        return EXIT_BUSY

    def on_signal(signum, frame):
        sup.stop = True

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    try:
        while not sup.stop:
            h = sup.cycle()
            if sup.stop:
                break
            if h["builder"]["state"] == "building" and not h["problems"]:
                continue                                              # more to build now: no pause between events
            end = time.time() + a.interval
            while not sup.stop and time.time() < end:
                time.sleep(min(0.2, max(0.0, end - time.time())))
    except Refuse as e:
        print(json.dumps({"refused": str(e)}), file=sys.stderr)
        return 2
    finally:
        sup.stopped()
        sup.release()
    return 0


def cmd_once(a):
    sup = make(a)
    try:
        sup.acquire()
    except Busy as e:
        print(json.dumps({"busy": True, "detail": str(e)}), file=sys.stderr)
        return EXIT_BUSY
    try:
        print(json.dumps(sup.cycle(), sort_keys=True))
    except Refuse as e:
        print(json.dumps({"refused": str(e)}), file=sys.stderr)
        return 2
    finally:
        sup.release()
    return 0


def cmd_check(a):
    code, text = check(os.path.join(os.path.abspath(a.root), "supervisor", "health.json"), max_age_s=a.max_age_s)
    print(text)
    return code


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("run", cmd_run), ("once", cmd_once)):
        p = sub.add_parser(name)
        add_options(p)
        p.set_defaults(fn=fn)
    p = sub.add_parser("check")
    p.add_argument("--root", required=True)
    p.add_argument("--max-age-s", type=float, default=180.0)
    p.set_defaults(fn=cmd_check)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
