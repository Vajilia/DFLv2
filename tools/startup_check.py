#!/usr/bin/env python3
"""Start the recurring broadcast's processes the way the unit files start them, in different orders, and report what each order does (docs/INTEGRATION_CANDIDATE_2026-11-07.md, section 15).

  startup_check.py --source ROOT --work DIR [--scenarios a,b,c] [--run-s 45] [--out FILE]

Each process is started with the EXACT argument list of its ExecStart line in deploy/supervisor/*.service (the flags and the numbers from broadcast.env.template are read from those files,
not retyped), with only the placeholders replaced: the pinned checkout is this checkout, the root, public and guard folders are scratch, the ceiling is a test number, and `--url` points
at a small local server that serves the public folder under /feed (the unit's URL is https://HOST; the monitor needs something to probe). The daemon's `--cgroup` path from the monitor's
unit does not exist here (no systemd), which the monitor treats as "no cgroup files". No memory limit, restart policy or `After=` ordering is applied: this checks the PROCESSES' own start-up
behaviour, not systemd's. Nothing is installed, sent or deleted; SOURCE is only copied; the work folder is scratch.

The fixture is prepared BEFORE anything starts: a copy of a finished real season-1 root, a clock driven (in-process, virtual time) to --position-s and left stopped for --lapsed-s seconds
(the state a host leaves when it was down), and, for the scenario that needs it, a monitor heartbeat file left by the previous run.
"""
import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(REPO, "src"))
sys.path.insert(0, HERE)
from dflsite import broadcast as B, season as S    # noqa: E402

DEPLOY = os.path.join(REPO, "deploy", "supervisor")
PY_PREFIX = "/usr/bin/python3 "


def log(msg):
    print("%s  %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def env_values(ceiling="6"):
    d = {}
    for line in open(os.path.join(DEPLOY, "broadcast.env.template")).read().splitlines():
        m = re.match(r"^([A-Z_]+)=(.*)$", line)
        if m:
            d[m.group(1)] = m.group(2)
    d["PUBLIC_CEILING_GB"] = ceiling                  # the owner's number: a test value here
    return d


def unit_argv(unit, root, public, guard, url):
    """The argument list systemd would run for `unit` (without the interpreter path, with this interpreter), placeholders replaced."""
    line = [l for l in open(os.path.join(DEPLOY, unit + ".service")).read().splitlines() if l.startswith("ExecStart=")][0][len("ExecStart="):]
    for k, v in env_values().items():
        line = line.replace("${%s}" % k, v)
    line = line.replace("/opt/dflv2-src/__COMMIT__", REPO)
    for k, v in (("__ROOT__", root), ("__PUBLIC__", public), ("__GUARD__", guard)):
        line = line.replace(k, v)
    line = line.replace("https://__HOST__", url)
    assert "__" not in line, line
    argv = shlex.split(line)
    assert argv[0] == "/usr/bin/python3", argv
    return [sys.executable] + argv[1:]


class Feed(BaseHTTPRequestHandler):
    public = None

    def do_GET(self):
        p = self.path.split("?")[0]
        rel = p[len("/feed/"):] if p.startswith("/feed/") else None
        f = os.path.join(self.public, rel) if rel and ".." not in rel else None
        if f and os.path.isfile(f):
            data = open(f, "rb").read()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            pointer = rel.endswith(("status.json", "clock.json", "latest.json", "index.json"))             # the headers the real front end sets (deploy/Caddyfile): pointers and clocks never cached
            self.send_header("Cache-Control", "no-store" if pointer else "public, max-age=31536000, immutable")
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(404)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()

    do_HEAD = do_GET

    def log_message(self, *a):
        pass


def serve(public):
    h = type("H", (Feed,), {"public": public})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]


def prepare(source, work, position_s, lapsed_s, heartbeat_age_s):
    """A scratch root + public + guard. The clock is driven in virtual time to position_s and then left stopped for lapsed_s seconds."""
    root, public, guard = (os.path.join(work, n) for n in ("root", "public", "guard"))
    shutil.copytree(source, root, symlinks=True)
    store = os.path.join(root, "store")
    open(os.path.join(store, "continuous"), "w").close()
    os.makedirs(public)
    os.makedirs(guard)
    B.FSYNC = False
    bc = S.SeasonBroadcaster(store, public, guard_publication=False)
    wall_end = time.time() - lapsed_s
    base = wall_end - position_s
    bc.clock.start(base, 0.0, B.LEASE_S)
    t = 0.0
    while t < position_s:
        bc.tick(base + t)
        t += 20.0
    st = bc.clock.state()
    pos = B.position(st, base + t)
    bc.clock._write(dict(st, ready_until=pos + B.DELAY_S), "fixture", base + t)       # fixture only: the daemon always has this key; the 20 s virtual ticks could not keep it
    B.FSYNC = True
    if heartbeat_age_s is not None:                                                  # what a monitor that ran before the host went down left behind
        hb = os.path.join(guard, "heartbeat")
        json.dump({"at": time.time() - heartbeat_age_s, "pid": 1, "progress_at": time.time() - heartbeat_age_s}, open(hb, "w"))
        os.utime(hb, (time.time() - heartbeat_age_s,) * 2)
    return root, public, guard


def position_of(root):
    try:
        st = B.get(os.path.join(root, "store", "clock.json"))
    except (OSError, ValueError):
        return None, None
    return B.position(st, time.time()), st


# name -> (heartbeat left by an earlier run: seconds old or None, [(process, seconds after the scenario starts)])
SCENARIOS = {
    "daemon-first-fresh":           ("a first install: no heartbeat file yet; the daemon starts, the monitor 5 s later", None, [("dfl-broadcast-daemon", 0), ("dfl-broadcast-monitor", 5)]),
    "daemon-first-after-downtime":  ("after a host outage: a heartbeat file from 1 hour ago is still in the guard folder; the daemon starts, the monitor 10 s later", 3600, [("dfl-broadcast-daemon", 0), ("dfl-broadcast-monitor", 10)]),
    "monitor-first-after-downtime": ("the same outage, the monitor started first, the daemon 10 s later", 3600, [("dfl-broadcast-monitor", 0), ("dfl-broadcast-daemon", 10)]),
    "together-after-downtime":      ("the same outage, both started in the same instant (what systemd does at boot: After= orders the starts, a simple service counts as started when it is forked)", 3600, [("dfl-broadcast-daemon", 0), ("dfl-broadcast-monitor", 0.05)]),
}


def run_scenario(name, source, work, run_s, position_s, lapsed_s):
    what, hb_age, order = SCENARIOS[name]
    sw = os.path.join(work, name)
    os.makedirs(sw)
    log("%s: %s" % (name, what))
    root, public, guard = prepare(source, sw, position_s, lapsed_s, hb_age)
    srv, url = serve(public)
    procs, t0 = {}, time.time()
    samples = []
    try:
        for unit, delay in order:
            while time.time() - t0 < delay:
                time.sleep(0.01)
            out = open(os.path.join(sw, unit + ".out"), "w")
            procs[unit] = subprocess.Popen(unit_argv(unit, root, public, guard, url), stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
            samples.append((round(time.time() - t0, 2), "started " + unit))
        pos_series = []
        while time.time() - t0 < run_s:
            time.sleep(1.0)
            pos, st = position_of(root)
            pos_series.append((round(time.time() - t0, 1), None if pos is None else round(pos, 1), None if st is None else st["state"]))
    finally:
        for unit in reversed([u for u, _ in order]):
            p = procs.get(unit)
            if p and p.poll() is None:
                p.send_signal(signal.SIGTERM)
        for p in procs.values():
            try:
                p.wait(20)
            except subprocess.TimeoutExpired:
                p.kill()
        srv.shutdown()
    watch = []
    wl = os.path.join(guard, "watch.log")
    if os.path.exists(wl):
        for line in open(wl):
            try:
                watch.append(json.loads(line))
            except ValueError:
                pass
    tripped = None
    tf = os.path.join(guard, "TRIPPED")
    if os.path.exists(tf):
        try:
            tripped = json.load(open(tf))
        except ValueError:
            tripped = {"kind": "unreadable"}
    clog = []
    cl = os.path.join(root, "store", "clock_log.jsonl")
    if os.path.exists(cl):
        clog = [json.loads(l) for l in open(cl) if l.strip()]
    rep = {
        "scenario": name, "what": what, "order": order,
        "exit_codes": {u: p.returncode for u, p in procs.items()},
        "violations": [{"kind": w["violation"], "detail": w.get("detail", "")[:140], "t": round(w["at"] - t0, 1)} for w in watch if "violation" in w],
        "heartbeats": sum(1 for w in watch if w.get("heartbeat")),
        "tripped": tripped,
        "paused_file": os.path.exists(os.path.join(root, "store", S.PAUSE)),
        "position": pos_series,
        "clock_log_kinds": [c.get("why") or c.get("event") or c.get("kind") for c in clog][-6:],
        "daemon_stderr_tail": open(os.path.join(sw, "dfl-broadcast-daemon.out")).read()[-400:] if "dfl-broadcast-daemon" in procs else "",
    }
    moved = [p for _, p, s in pos_series if p is not None]
    rep["position_advanced_s"] = round(moved[-1] - moved[0], 1) if len(moved) > 1 else None
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--scenarios", default=",".join(SCENARIOS))
    ap.add_argument("--run-s", type=float, default=45.0)
    ap.add_argument("--position-s", type=float, default=1500.0, help="where the fixture's clock stood when the 'host went down'")
    ap.add_argument("--lapsed-s", type=float, default=400.0, help="how long ago the clock was last renewed (the lease is 90 s)")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    work = os.path.abspath(a.work)
    if os.path.exists(work):
        sys.exit("--work must not exist: %s" % work)
    os.makedirs(work)
    reps = []
    for n in a.scenarios.split(","):
        if n not in SCENARIOS:
            sys.exit("unknown scenario %s (have: %s)" % (n, ", ".join(SCENARIOS)))
        r = run_scenario(n, os.path.abspath(a.source), work, a.run_s, a.position_s, a.lapsed_s)
        reps.append(r)
        log("  -> tripped=%s violations=%s position +%ss paused=%s exit=%s" % (r["tripped"] and r["tripped"].get("kind"), sorted({v["kind"] for v in r["violations"]}), r["position_advanced_s"], r["paused_file"], r["exit_codes"]))
    if a.out:
        json.dump(reps, open(a.out, "w"), indent=1, sort_keys=True, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
