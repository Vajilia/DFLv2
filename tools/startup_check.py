#!/usr/bin/env python3
"""Start the recurring broadcast's processes the way the unit files start them, in different orders, and report what each order does (docs/INTEGRATION_CANDIDATE_2026-11-07.md, section 15).

  startup_check.py --source ROOT --work DIR [--scenarios a,b,c] [--out FILE]

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


D, M = "dfl-broadcast-daemon", "dfl-broadcast-monitor"
# name -> what it is, the age of the heartbeat file an earlier run left (None: no file), how long the clock has been stopped, how long to watch, and which process starts when (seconds after the scenario starts)
SCENARIOS = {
    "daemon-first-fresh":           dict(what="a first install: no heartbeat file yet; the daemon starts, the monitor 5 s later", hb=None, lapsed=400.0, run_s=45.0, order=[(D, 0), (M, 5)], expect="clean"),
    "daemon-first-after-downtime":  dict(what="after a host outage: a heartbeat file from 1 hour ago is still in the guard folder; the daemon starts, the monitor 10 s later", hb=3600, lapsed=400.0, run_s=45.0, order=[(D, 0), (M, 10)], expect="clean"),
    "monitor-first-after-downtime": dict(what="the same outage, the monitor started first, the daemon 10 s later", hb=3600, lapsed=400.0, run_s=45.0, order=[(M, 0), (D, 10)], expect="monitor-first"),
    "together-after-downtime":      dict(what="the same outage, both started in the same instant (what systemd does at boot: After= orders the starts, a simple service counts as started when it is forked)", hb=3600, lapsed=400.0, run_s=45.0, order=[(D, 0), (M, 0.05)], expect="clean"),
    "daemon-restart-monitor-back-within-grace": dict(what="the daemon is restarted 10 s after its lease lapsed while the monitor is down (its last heartbeat is 10 minutes old); the monitor is back 100 s later, inside the 240 s grace", hb=600, lapsed=100.0, run_s=285.0, order=[(D, 0), (M, 100)], expect="recovers"),
    "daemon-restart-dead-monitor":  dict(what="the same restart and the monitor never comes back: the daemon must trip itself by 240 s; a monitor started at 255 s, after the trip, must not clear it", hb=600, lapsed=100.0, run_s=310.0, order=[(D, 0), (M, 255)], expect="safe-trip"),
}


def run_scenario(name, source, work, position_s):
    sc = SCENARIOS[name]
    what, hb_age, order, run_s, lapsed_s = sc["what"], sc["hb"], sc["order"], sc["run_s"], sc["lapsed"]
    sw = os.path.join(work, name)
    os.makedirs(sw)
    log("%s: %s" % (name, what))
    root, public, guard = prepare(source, sw, position_s, lapsed_s, hb_age)
    srv, url = serve(public)
    procs, t0 = {}, time.time()
    samples = []
    pos_series, latch_series = [], []
    done = threading.Event()

    def sampler():                                      # from the first second, whatever the start schedule: a trip is seen when it happens, not when the next process starts
        while not done.wait(1.0):
            pos, st = position_of(root)
            now_t = round(time.time() - t0, 1)
            pos_series.append((now_t, None if pos is None else round(pos, 1), None if st is None else st["state"]))
            tl = os.path.join(guard, "trips.jsonl")
            try:
                n_trips = len(open(tl).read().splitlines()) if os.path.exists(tl) else 0
            except OSError:
                n_trips = 0
            latch_series.append((now_t, os.path.exists(os.path.join(guard, "TRIPPED")), n_trips))

    threading.Thread(target=sampler, daemon=True).start()
    try:
        for unit, delay in order:
            while time.time() - t0 < delay:
                time.sleep(0.01)
            out = open(os.path.join(sw, unit + ".out"), "w")
            procs[unit] = subprocess.Popen(unit_argv(unit, root, public, guard, url), stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
            samples.append((round(time.time() - t0, 2), "started " + unit))
        while time.time() - t0 < run_s:
            time.sleep(0.5)
        done.set()
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
        "scenario": name, "what": what, "order": order, "t0": t0,
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
    rep["started_at_s"] = dict(samples_started(samples))
    rep["latch_series"] = latch_series
    rep["trips_jsonl"] = [l for l in open(os.path.join(guard, "trips.jsonl")).read().splitlines()] if os.path.exists(os.path.join(guard, "trips.jsonl")) else []
    rep["clock_log"] = clog
    rep["clock_final"] = position_of(root)[1]
    rep["evidence_dir"] = keep_evidence(sw, root, guard)
    rep["checks"] = [{"check": n, "ok": bool(ok), "detail": d} for n, ok, d in evaluate(name, rep)]
    return rep


def samples_started(samples):
    for t, what in samples:
        if what.startswith("started "):
            yield what[len("started "):], t


def keep_evidence(sw, root, guard):
    """Copy what a person would need to understand the run (the latch, the trip log, the heartbeat, the monitor's log and evidence, the clock and its log, the daemon's output) next to the scratch work."""
    ev = os.path.join(sw, "evidence-kept")
    os.makedirs(ev)
    for src, name in ((os.path.join(guard, "TRIPPED"), "TRIPPED"), (os.path.join(guard, "trips.jsonl"), "trips.jsonl"), (os.path.join(guard, "heartbeat"), "heartbeat"),
                      (os.path.join(guard, "watch.log"), "watch.log"), (os.path.join(root, "store", "clock.json"), "clock.json"), (os.path.join(root, "store", "clock_log.jsonl"), "clock_log.jsonl"),
                      (os.path.join(sw, "dfl-broadcast-daemon.out"), "daemon.out"), (os.path.join(sw, "dfl-broadcast-monitor.out"), "monitor.out")):
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(ev, name))
    if os.path.isdir(os.path.join(guard, "evidence")):
        shutil.copytree(os.path.join(guard, "evidence"), os.path.join(ev, "monitor-evidence"))
    open(os.path.join(ev, "PAUSE-present"), "w").write(str(os.path.exists(os.path.join(root, "store", S.PAUSE))) + "\n")
    return ev


def evaluate(name, rep):
    """The expectation of each scenario as checks. Returns [(check, ok, detail)]."""
    sc = SCENARIOS[name]
    kinds = {v["kind"] for v in rep["violations"]}
    tr = rep["tripped"]
    run_s = sc["run_s"]
    out = []
    pos = rep["position"]

    def adv(t_from, t_to):
        a = [p for t, p, _ in pos if p is not None and t_from <= t <= t_to]
        return round(a[-1] - a[0], 1) if len(a) > 1 else None

    exp = sc["expect"]
    if exp in ("clean", "monitor-first", "recovers"):
        out.append(("no trip", tr is None, tr))
        allowed = {"daemon_silent"} if exp == "monitor-first" else set()
        out.append(("monitor reported nothing beyond %s" % (sorted(allowed) or "nothing"), kinds <= allowed, sorted(kinds)))
        out.append(("the broadcast kept going (position advanced %s s in the last %.0f s)" % (adv(run_s - 25, run_s), 20), (adv(run_s - 25, run_s) or 0) >= 20, adv(run_s - 25, run_s)))
    if exp == "recovers":
        out.append(("the monitor ran (heartbeats %s)" % rep["heartbeats"], rep["heartbeats"] >= 5, rep["heartbeats"]))
        out.append(("it was still unlatched after the grace would have ended (240 s): no latch in any sample", not any(l[1] for l in rep["latch_series"]), None))
        out.append(("the daemon did not stop on its own: clock running at the end", (rep["clock_final"] or {}).get("state") == "running", (rep["clock_final"] or {}).get("state")))
    if exp == "safe-trip":
        d0 = rep["started_at_s"].get("dfl-broadcast-daemon", 0.0)
        t_trip = (tr["at"] - (rep["t0"] + d0)) if tr else None
        out.append(("tripped by the daemon itself, kind monitor_silent", bool(tr) and tr.get("kind") == "monitor_silent" and tr.get("source") == "daemon", tr))
        out.append(("tripped at 240 s, not before (%s s after the daemon was started)" % (None if t_trip is None else round(t_trip, 1)), t_trip is not None and 239.0 <= t_trip <= 247.0, t_trip))
        first = next((t for t, ex, _ in rep["latch_series"] if ex), None)
        out.append(("no latch in any 1 s sample before 238 s; first seen at %s s" % first, first is not None and 238.0 <= first <= 247.0, first))
        ran = adv(5, 238)
        out.append(("until then the broadcast ran (position advanced %s s between 5 s and 238 s: the grace is a window of operation, not a hold)" % ran, ran is not None and ran >= 200, ran))
        paused_at = next((t for t, _, st in pos if st == "paused"), None)
        out.append(("the clock was seen paused at %s s, within 240 to 247 s" % paused_at, paused_at is not None and 239.0 <= paused_at <= 247.0, paused_at))
        frozen = adv(first + 3 if first else run_s, run_s)
        out.append(("the position stopped: it advanced %s s from 3 s after the trip to the end (the late monitor started at 255 s)" % frozen, frozen is not None and abs(frozen) < 0.5, frozen))
        out.append(("the clock is paused and PAUSE is present", (rep["clock_final"] or {}).get("state") == "paused" and rep["paused_file"], (rep["clock_final"] or {}).get("state")))
        out.append(("exactly one trip recorded, and still one after the late monitor ran", len(rep["trips_jsonl"]) == 1 and all(n <= 1 for _, _, n in rep["latch_series"]), len(rep["trips_jsonl"])))
        out.append(("the late monitor started and was alive (heartbeats %s) and did NOT clear the latch" % rep["heartbeats"], rep["heartbeats"] >= 1 and tr is not None, rep["heartbeats"]))
        out.append(("the latch file stayed in place to the end (every sample after the first sighting)", all(ex for t, ex, _ in rep["latch_series"] if first and t >= first), None))
        out.append(("the daemon process stayed up (paused) until it was stopped: exit status 0", rep["exit_codes"].get("dfl-broadcast-daemon") == 0, rep["exit_codes"]))
        out.append(("evidence kept: TRIPPED, trips.jsonl and the clock log were copied", all(os.path.exists(os.path.join(rep["evidence_dir"], n)) for n in ("TRIPPED", "trips.jsonl", "clock_log.jsonl")), rep["evidence_dir"]))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--scenarios", default=",".join(SCENARIOS))
    ap.add_argument("--position-s", type=float, default=1500.0, help="where the fixture's clock stood when the 'host went down'")
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
        r = run_scenario(n, os.path.abspath(a.source), work, a.position_s)
        reps.append(r)
        log("  -> tripped=%s violations=%s position +%ss paused=%s exit=%s" % (r["tripped"] and r["tripped"].get("kind"), sorted({v["kind"] for v in r["violations"]}), r["position_advanced_s"], r["paused_file"], r["exit_codes"]))
        for c in r["checks"]:
            log("     %s  %s%s" % ("PASS" if c["ok"] else "FAIL", c["check"], "" if c["ok"] or c["detail"] is None else "  -- %s" % (c["detail"],)))
    failed = [(r["scenario"], c["check"]) for r in reps for c in r["checks"] if not c["ok"]]
    log("RESULT: %s (%d checks, %d failed)" % ("PASS" if not failed else "FAIL", sum(len(r["checks"]) for r in reps), len(failed)))
    if a.out:
        json.dump(reps, open(a.out, "w"), indent=1, sort_keys=True, default=str)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
