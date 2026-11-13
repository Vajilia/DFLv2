#!/usr/bin/env python3
"""dfl_alert: failure alerts for the broadcast daemon and its monitor (design: docs/ALERT_DESIGN_2026-11-12.md).

What it does
  * Reads systemd (`systemctl show`, nothing else) and raises an incident when the monitor has failed or stopped, when the daemon has failed or its start budget is exhausted
    (Result=start-limit-hit), or (a warning) when the budget is nearly used. It never asks the monitor anything: it works when the monitor is dead.
  * Writes the incident to its own folder BEFORE trying to deliver it, retries failed deliveries with a growing back-off, and suppresses duplicates (one incident per signature while the
    condition lasts; one reminder per interval while nobody has acknowledged it).
  * Keeps two facts apart: `delivery` (the hand-off command exited 0: attempted_ok, or attempted_failed) and `notification_received` (set only by `ack`, which a person runs).

What it never does
  Release a latch, resume publication, reset a failure, start, stop or restart a unit, or write outside its own alert folder. The only systemctl verb in this file is `show`; every write goes
  through safe_write, which refuses a path outside the alert folder; the unit that runs it is also read-only everywhere else (ProtectSystem=strict). Recovery is for a person, who reads the
  evidence first (the message says so).

Commands: poll | event UNIT | retry | ack ID --by NAME | status | test-delivery (sends one clearly labelled TEST message; run it by hand).
Configuration comes from the environment (an EnvironmentFile, mode 0600, on the host; the repository holds only a template with no secret).
"""
import argparse
import errno
import fcntl
import json
import os
import shlex
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field

BACK_OFF_S = (60, 120, 300, 600, 1800, 3600)
SHOW_PROPS = ("LoadState", "ActiveState", "SubState", "Result", "ExecMainStatus", "NRestarts", "StartLimitBurst")


class Unsafe(Exception):
    pass


@dataclass
class Config:
    alert_dir: str = "/var/lib/dfl-alerts"
    daemon_unit: str = "dfl-broadcast-daemon.service"
    monitor_unit: str = "dfl-broadcast-monitor.service"
    deliver_cmd: list = field(default_factory=list)
    ping_cmd: list = field(default_factory=list)
    heartbeat: str = ""
    heartbeat_max_s: int = 120
    remind_s: int = 3600
    polls_down: int = 2
    host: str = ""

    @classmethod
    def from_env(cls, env):
        def num(k, d):
            try:
                return int(env.get(k, d))
            except ValueError:
                return d
        return cls(alert_dir=env.get("DFL_ALERT_DIR", cls.alert_dir),
                   daemon_unit=env.get("DFL_ALERT_DAEMON_UNIT", cls.daemon_unit),
                   monitor_unit=env.get("DFL_ALERT_MONITOR_UNIT", cls.monitor_unit),
                   deliver_cmd=shlex.split(env.get("DFL_ALERT_DELIVER_CMD", "")),
                   ping_cmd=shlex.split(env.get("DFL_ALERT_PING_CMD", "")),
                   heartbeat=env.get("DFL_ALERT_HEARTBEAT", ""),
                   heartbeat_max_s=num("DFL_ALERT_HEARTBEAT_MAX_S", 120),
                   remind_s=num("DFL_ALERT_REMIND_S", 3600),
                   polls_down=num("DFL_ALERT_POLLS_DOWN", 2),
                   host=env.get("DFL_ALERT_HOST_LABEL", "") or socket.gethostname())


# ------------------------------------------------------------------------------------------------------------------------------- the three places that start a process
def _systemctl_show(argv):
    """The default runner: `systemctl show ...` and nothing else (anything else is refused before a process starts)."""
    if argv[:2] != ["systemctl", "show"]:
        raise Unsafe("this tool only runs `systemctl show`: refused %r" % (argv,))
    r = subprocess.run(argv, capture_output=True, text=True, timeout=20)
    return r.returncode, r.stdout, r.stderr


def deliver_via_command(message, cfg):
    """Hand the message to the owner's delivery command on its standard input (no shell). Exit 0 means the command accepted it: that is a hand-off, not receipt."""
    if not cfg.deliver_cmd:
        return False, "no delivery command configured"
    try:
        r = subprocess.run(cfg.deliver_cmd, input=message, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return False, "%s: %s" % (type(e).__name__, e)
    return r.returncode == 0, "exit %d%s" % (r.returncode, (": " + r.stderr.strip()[-200:]) if r.returncode else "")


def run_ping(cfg):
    """The optional off-host 'I am alive' ping run on every poll (a dead-man's switch: its silence is what an outside service alerts on)."""
    if not cfg.ping_cmd:
        return None
    try:
        r = subprocess.run(cfg.ping_cmd, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as e:
        return False, "%s: %s" % (type(e).__name__, e)
    return r.returncode == 0, "exit %d" % r.returncode


# ------------------------------------------------------------------------------------------------------------------------------------- the only writer
def safe_write(cfg, path, text, mode=0o600):
    """Atomic write of `text`, refused unless the path is inside the alert folder."""
    root = os.path.realpath(cfg.alert_dir)
    target = os.path.realpath(path)
    if target != root and not target.startswith(root + os.sep):
        raise Unsafe("refusing to write outside the alert folder: %s" % path)
    os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
    os.chmod(root, 0o700)
    tmp = "%s.tmp.%d" % (target, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, target)


MEANING = {
    "monitor_failed": ("the monitor has failed (it exits 2 on purpose when it cannot write its safety latch, and the unit is set not to restart it)",
                       "Start the monitor first (reset-failed on that unit, then start), confirm its heartbeat is fresh, and only then follow the documented release and resume if a latch is present."),
    "monitor_not_running": ("the monitor is not running", "Start the monitor and confirm its heartbeat is fresh before anything else."),
    "monitor_heartbeat_stale": ("the monitor is running but its heartbeat has stopped", "Look at the monitor's journal; the daemon pauses itself when the heartbeat stays stale."),
    "daemon_start_limit_hit": ("the daemon's start budget is used up: systemd will not start it again and publication is stopped",
                               "reset-failed clears systemd's counters only and touches no latch; the daemon may then start HELD (the start gate's fail-safe); publication resumes only after a person runs release, then resume."),
    "daemon_failed": ("the daemon has failed and is not being restarted", "Read the unit's journal and the guard folder; a TRIPPED file is a safety latch, not an error to clear."),
    "daemon_not_running": ("the daemon is not running", "Read the unit's journal; start it only after the cause is known."),
    "daemon_budget_low": ("the daemon has restarted nearly as often as its start budget allows (the next crash may be refused)", "Find out why it keeps restarting before it is refused for good."),
    "unit_failed": ("a unit failed", "Read that unit's journal."),
    "unit_missing": ("a unit this tool watches is not installed", "Check the installation."),
}


class Alerter:
    def __init__(self, cfg, runner=None, deliver=None, ping=None, clock=time.time):
        self.cfg = cfg
        self.runner = runner or _systemctl_show
        self.deliver = deliver or deliver_via_command
        self.ping = ping or run_ping
        self.clock = clock
        self._cmds = []
        self.inc_dir = os.path.join(cfg.alert_dir, "incidents")

    # ----------------------------------------------------------------------------------------------------------------------------------------- storage
    def _path(self, iid):
        return os.path.join(self.inc_dir, iid + ".json")

    def _save(self, inc):
        safe_write(self.cfg, self._path(inc["id"]), json.dumps(inc, indent=1, sort_keys=True))

    def incidents(self):
        out = []
        try:
            names = sorted(os.listdir(self.inc_dir))
        except FileNotFoundError:
            return out
        for n in names:
            if n.endswith(".json"):
                with open(os.path.join(self.inc_dir, n)) as f:
                    out.append(json.load(f))
        return out

    def _open_incident(self, signature):
        for inc in self.incidents():
            if inc["signature"] == signature and inc["cleared"] is None:
                return inc
        return None

    def _state(self):
        try:
            with open(os.path.join(self.cfg.alert_dir, "poll_state.json")) as f:
                return json.load(f)
        except (FileNotFoundError, ValueError):
            return {"down": {}}

    def _save_state(self, st):
        safe_write(self.cfg, os.path.join(self.cfg.alert_dir, "poll_state.json"), json.dumps(st, sort_keys=True))

    def _stamp(self, t=None):
        return time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(self.clock() if t is None else t))

    # ---------------------------------------------------------------------------------------------------------------------------------------- reading
    def _show(self, unit):
        argv = ["systemctl", "show", "--no-pager"]
        for p in SHOW_PROPS:
            argv += ["-p", p]
        argv.append(unit)
        self._cmds.append(list(argv))
        rc, out, err = self.runner(argv)
        vals = {}
        for line in out.splitlines():
            k, _, v = line.partition("=")
            vals[k] = v
        return vals

    def _burst(self, v):
        try:
            return int(v.get("StartLimitBurst", ""))
        except ValueError:
            return 0

    def _conditions(self, role, unit, v, down):
        """-> [(event, severity, observed)] for one unit from what systemd says. `down` is the poll counter for 'not running' states (a restart in progress is not an incident)."""
        active = v.get("ActiveState", "")
        res = v.get("Result", "")
        if v.get("LoadState") == "not-found":
            return [("unit_missing", "critical", v)]
        if active == "failed":
            if role == "daemon" and res == "start-limit-hit":
                return [("daemon_start_limit_hit", "critical", v)]
            return [("%s_failed" % role, "critical", v)]
        if active in ("inactive", "deactivating"):
            n = down.get(role, 0) + 1
            down[role] = n
            if n >= self.cfg.polls_down:
                return [("%s_not_running" % role, "critical", v)]
            return []
        down.pop(role, None)
        found = []
        if active == "active":
            if role == "daemon":
                burst = self._burst(v)
                try:
                    n = int(v.get("NRestarts", "0") or 0)
                except ValueError:
                    n = 0
                if burst and n >= burst - 1:
                    found.append(("daemon_budget_low", "warning", v))
            if role == "monitor" and self.cfg.heartbeat:
                try:
                    age = self.clock() - os.stat(self.cfg.heartbeat).st_mtime
                    detail = dict(v, HeartbeatAgeS=str(int(age)))
                except OSError:
                    age, detail = None, dict(v, HeartbeatAgeS="missing")
                if age is None or age > self.cfg.heartbeat_max_s:
                    found.append(("monitor_heartbeat_stale", "critical", detail))
        return found

    # --------------------------------------------------------------------------------------------------------------------------------------- delivery
    def _message(self, inc, kind, n=0):
        what, todo = MEANING.get(inc["event"], MEANING["unit_failed"])
        obs = " ".join("%s=%s" % (k, inc["observed"][k]) for k in sorted(inc["observed"]) if inc["observed"][k] != "")
        head = {"initial": "", "retry": "", "reminder": "REMINDER %d (nobody has acknowledged this): " % n}[kind]
        return ("[DFL %s] %s: %s%s\nWhat: %s.\nSeen %s: %s\nIncident: %s\nDo not reset blindly: read the unit's journal and the guard folder first (a TRIPPED file is a safety latch). %s\n"
                "Acknowledge once you have seen this: dfl_alert.py ack %s --by <your name>\n") % (
            inc["severity"].upper(), self.cfg.host, head, inc["unit"], what, self._stamp(inc["last_seen"]), obs or "(nothing reported)", inc["id"], todo, inc["id"])

    def _attempt(self, inc, kind):
        msg = self._message(inc, kind, n=sum(1 for a in inc["delivery"]["attempts"] if a["kind"] == "reminder") + (1 if kind == "reminder" else 0))
        ok, detail = self.deliver(msg, self.cfg)
        now = self.clock()
        inc["delivery"]["attempts"].append({"at": now, "kind": kind, "ok": bool(ok), "detail": str(detail)[:300]})
        if ok:
            inc["delivery"]["state"] = "attempted_ok"
            inc["delivery"]["next_retry_at"] = None
        else:
            fails = 0
            for a in reversed(inc["delivery"]["attempts"]):
                if a["ok"]:
                    break
                fails += 1
            inc["delivery"]["state"] = "attempted_failed"
            inc["delivery"]["next_retry_at"] = now + BACK_OFF_S[min(fails - 1, len(BACK_OFF_S) - 1)]
        inc["commands_run"] = list(self._cmds)
        self._save(inc)
        return bool(ok)

    def _service(self, inc, summary):
        """An open incident that is still there: retry a failed hand-off when it is due; remind once per interval while nobody has acknowledged it. Nothing else is ever sent."""
        d = inc["delivery"]
        now = self.clock()
        if d["state"] == "attempted_failed" and d["next_retry_at"] is not None and now >= d["next_retry_at"]:
            self._attempt(inc, "retry")
            summary["retried"].append(inc["id"])
        elif d["state"] == "attempted_ok" and inc["notification_received"] is None:
            last_ok = max(a["at"] for a in d["attempts"] if a["ok"])
            if now - last_ok >= self.cfg.remind_s:
                self._attempt(inc, "reminder")
                summary["reminded"].append(inc["id"])

    # ------------------------------------------------------------------------------------------------------------------------------------ incidents
    def _new_id(self, event, unit):
        base = "%s-%s-%s" % (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(self.clock())), event, unit.replace(".service", ""))
        iid, n = base, 1
        while os.path.exists(self._path(iid)):
            n += 1
            iid = "%s-%d" % (base, n)
        return iid

    def _raise(self, event, severity, unit, observed, summary):
        signature = "%s:%s" % (event, unit)
        inc = self._open_incident(signature)
        now = self.clock()
        if inc:
            inc["last_seen"] = now
            inc["observed"] = observed
            inc["commands_run"] = list(self._cmds)
            self._save(inc)
            self._service(inc, summary)
            return signature
        inc = {"id": self._new_id(event, unit), "signature": signature, "event": event, "unit": unit, "severity": severity, "first_seen": now, "last_seen": now,
               "observed": observed, "commands_run": list(self._cmds),
               "delivery": {"state": "pending", "attempts": [], "next_retry_at": None},
               "notification_received": None, "cleared": None}
        self._save(inc)                                                  # on disk before any hand-off
        summary["raised"].append(inc["id"])
        self._attempt(inc, "initial")
        return signature

    def _clear(self, inc, summary):
        now = self.clock()
        inc["cleared"] = {"at": now}
        self._save(inc)
        summary["cleared"].append(inc["id"])
        msg = "[DFL] %s: %s cleared at %s (incident %s). A person acted, or it recovered; nothing was changed by this tool.\n" % (self.cfg.host, inc["event"], self._stamp(now), inc["id"])
        ok, detail = self.deliver(msg, self.cfg)
        inc["cleared"].update({"notified": bool(ok), "detail": str(detail)[:200]})
        self._save(inc)

    def _new_summary(self):
        return {"raised": [], "retried": [], "reminded": [], "cleared": []}

    # ---------------------------------------------------------------------------------------------------------------------------------------- public
    def poll(self):
        self._cmds = []
        summary = self._new_summary()
        st = self._state()
        down = st.setdefault("down", {})
        seen = set()
        for role, unit in (("daemon", self.cfg.daemon_unit), ("monitor", self.cfg.monitor_unit)):
            for event, severity, observed in self._conditions(role, unit, self._show(unit), down):
                seen.add(self._raise(event, severity, unit, observed, summary))
        for inc in self.incidents():
            if inc["cleared"] is None and inc["signature"] not in seen and not inc["signature"].startswith("unit_failed:"):
                self._clear(inc, summary)
        self._save_state(st)
        ping = self.ping(self.cfg)
        safe_write(self.cfg, os.path.join(self.cfg.alert_dir, "poll.json"), json.dumps(
            {"last_poll": self.clock(), "ping": None if ping is None else {"ok": bool(ping[0]), "detail": str(ping[1])}, "open": [i["id"] for i in self.incidents() if i["cleared"] is None]}, sort_keys=True))
        return summary

    def event(self, unit):
        """OnFailure= hook: systemd says `unit` entered the failed state. Whatever systemd then shows, the failure is recorded (a failure must not be lost to a race)."""
        self._cmds = []
        summary = self._new_summary()
        role = "daemon" if unit == self.cfg.daemon_unit else "monitor" if unit == self.cfg.monitor_unit else None
        v = self._show(unit)
        found = self._conditions(role, unit, v, {}) if role else []
        if not found:
            found = [("%s_failed" % role if role else "unit_failed", "critical", v)]
        for event, severity, observed in found:
            self._raise(event, severity, unit, observed, summary)
        return summary

    def retry(self):
        self._cmds = []
        summary = self._new_summary()
        for inc in self.incidents():
            if inc["cleared"] is None:
                self._service(inc, summary)
        return summary

    def ack(self, iid, by, via="cli"):
        try:
            with open(self._path(iid)) as f:
                inc = json.load(f)
        except FileNotFoundError:
            raise KeyError(iid)
        inc["notification_received"] = {"at": self.clock(), "by": by, "via": via}
        self._save(inc)
        return inc


# ------------------------------------------------------------------------------------------------------------------------------------------------------- CLI
def main(argv=None, env=None, runner=None, deliver=None, clock=None):
    ap = argparse.ArgumentParser(prog="dfl_alert", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("poll")
    p = sub.add_parser("event")
    p.add_argument("unit")
    sub.add_parser("retry")
    p = sub.add_parser("ack")
    p.add_argument("incident")
    p.add_argument("--by", required=True)
    p.add_argument("--via", default="cli")
    sub.add_parser("status")
    sub.add_parser("test-delivery")
    a = ap.parse_args(argv)
    env = os.environ if env is None else env
    cfg = Config.from_env(env)
    if not cfg.deliver_cmd and deliver is None:
        # Detection and the record come first and need no channel: a host whose channel is not configured yet must not also be unrecorded. The failed hand-off is visible (exit 1, record says so).
        print("dfl_alert: DFL_ALERT_DELIVER_CMD is not set (see alerts/alert.env.template): incidents are recorded but nothing can be delivered", file=sys.stderr)
        if a.cmd == "test-delivery":
            return 3
    al = Alerter(cfg, runner=runner, deliver=deliver, clock=clock or time.time)
    try:
        wait = float(env.get("DFL_ALERT_LOCK_WAIT_S", "60"))
    except ValueError:
        wait = 60.0
    lock = _take_lock(cfg, wait)
    if lock is None:
        print("dfl_alert: another run holds the lock after %.0f s; nothing was changed (the next poll or event run will do the work)" % wait, file=sys.stderr)
        return 4
    try:
        return _run(a, al, cfg)
    finally:
        os.close(lock)


def _take_lock(cfg, wait_s):
    """One run at a time (OnFailure= and the poll timer can start two together). The lock file lives in the alert folder. -> fd, or None if still held after `wait_s`."""
    os.makedirs(cfg.alert_dir, mode=0o700, exist_ok=True)
    path = os.path.join(cfg.alert_dir, "alert.lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.chmod(path, 0o600)
    end = time.monotonic() + wait_s
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError as e:
            if e.errno not in (errno.EAGAIN, errno.EACCES):
                os.close(fd)
                raise
            if time.monotonic() >= end:
                os.close(fd)
                return None
            time.sleep(0.2)


def _run(a, al, cfg):
    if a.cmd == "ack":
        try:
            al.ack(a.incident, by=a.by, via=a.via)
        except KeyError:
            print("no such incident: %s" % a.incident, file=sys.stderr)
            return 2
        print("acknowledged %s by %s" % (a.incident, a.by))
        return 0
    if a.cmd == "status":
        for inc in al.incidents():
            print("%s  %-9s %-26s delivery=%s received=%s%s" % (inc["id"], inc["severity"], inc["event"], inc["delivery"]["state"],
                                                                 "yes" if inc["notification_received"] else "NO", "  (cleared)" if inc["cleared"] else ""))
        return 0
    if a.cmd == "test-delivery":
        ok, detail = al.deliver("[DFL TEST] %s: this is a test of the alert channel, sent by hand. It is not an incident and nothing is wrong.\n" % cfg.host, cfg)
        print("hand-off %s: %s (this proves the hand-off only; a person must say whether it arrived)" % ("accepted" if ok else "FAILED", detail))
        return 0 if ok else 1
    summary = {"poll": al.poll, "retry": al.retry}[a.cmd]() if a.cmd != "event" else al.event(a.unit)
    print(json.dumps(summary, sort_keys=True))
    failing = [i for i in al.incidents() if i["cleared"] is None and i["delivery"]["state"] == "attempted_failed"]
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
