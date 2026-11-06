#!/usr/bin/env python3
"""Reproducible real-engine comparison: a broadcast-driven league against a free-running one, through all of season 2 (docs/ENGINE_COMPARISON_REPRODUCTION.md).

  engine_comparison.py --workdir D [--year1-root R1] [--seed 20261105] [--seasons 2] [--daemon-check] [--report FILE]

What it proves: the league that the BUILDER drives one event at a time (feed, replay and live-recording builds in between, `next-season` at the year boundary) ends in the
same state and RNG consequences as a twin that the league runner simply runs, event by event, to the same event: `league_store.py digest` equal (journal and deep hash),
at the end of every season up to --seasons (season 2 = event 94).

Fixture: a FINISHED, completely built first season (`broadcast_build.py status` says season 1, 47 events built, 400 games). Pass it as --year1-root (it is only COPIED),
or leave the option out and the script makes one in D/year1 with `broadcast_build.py init --seed SEED` and `step` (about 35 minutes on one core, 1.1 GB).
D must be new. Needs about 2.5 GB per season built plus 1.1 GB for the twin and, with --daemon-check, about 2 GB more for the public tree (a hard-link copy of the
store, so the check never changes the builder-driven root). Runs on one core per job (the builder and the twin run in parallel, niced).

Exit 0 and a JSON report (also printed) when every digest pair is identical.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
TOOLS = os.path.join(ROOT, "tools")
EVENTS_PER_SEASON = 47


def sh(args, log=None, nice=0, check=True):
    cmd = ([ "nice", "-n", str(nice)] if nice else []) + [PY] + args
    out = open(log, "w") if log else subprocess.PIPE
    p = subprocess.run(cmd, stdout=out, stderr=subprocess.STDOUT if log else subprocess.PIPE, text=True)
    if log:
        out.close()
    if check and p.returncode != 0:
        tail = open(log).read()[-800:] if log else (p.stderr or p.stdout)[-800:]
        raise SystemExit("failed (%d): %s\n%s" % (p.returncode, " ".join(args[:3]), tail))
    return p


def digest(league):
    p = sh([os.path.join(TOOLS, "league_store.py"), "digest", league])
    return json.loads(p.stdout)


def status(root):
    return json.loads(sh([os.path.join(TOOLS, "broadcast_build.py"), "status", "--root", root]).stdout)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--year1-root")
    ap.add_argument("--seed", type=int, default=20261105)
    ap.add_argument("--seasons", type=int, default=2, help="compare at the end of every season up to this one (default 2: event 94)")
    ap.add_argument("--daemon-check", action="store_true", help="afterwards run the daemon in virtual time over the whole of the last season (tools/broadcast_multiseason_check.py --through-end)")
    ap.add_argument("--report")
    a = ap.parse_args(argv)
    if a.seasons < 2:
        raise SystemExit("--seasons must be at least 2 (the point is the year boundary)")
    d = os.path.abspath(a.workdir)
    if os.path.exists(d) and os.listdir(d):
        raise SystemExit("%s must be new" % d)
    os.makedirs(d, exist_ok=True)
    t0 = time.time()
    rep = {"commit": subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(), "seed": a.seed, "steps": [], "digests": {}}

    def step(name, since):
        rep["steps"].append({"step": name, "seconds": round(time.time() - since, 1)})

    # ---- the fixture: a finished, completely built first season
    if a.year1_root:
        y1 = os.path.abspath(a.year1_root)
    else:
        y1 = os.path.join(d, "year1")
        t = time.time()
        sh([os.path.join(TOOLS, "broadcast_build.py"), "init", "--root", y1, "--seed", str(a.seed)])
        sh([os.path.join(TOOLS, "broadcast_build.py"), "step", "--root", y1], log=os.path.join(d, "year1.log"), nice=5)
        step("make year-1 root", t)
    st = status(y1)
    if st["season"] != 1 or st["events_built"] != EVENTS_PER_SEASON:
        raise SystemExit("the year-1 root is not a completely built first season: %s" % st)
    league_events = json.loads(sh([os.path.join(TOOLS, "league_store.py"), "status", os.path.join(y1, "league", "league.db")]).stdout)["events"]
    if league_events != EVENTS_PER_SEASON:
        raise SystemExit("the year-1 league has %d events, expected %d" % (league_events, EVENTS_PER_SEASON))
    rep["year1"] = {"root": y1, "status": st, "digest": digest(os.path.join(y1, "league", "league.db"))}

    # ---- two copies: A is driven by the builder, B by the league runner alone
    t = time.time()
    A, B = os.path.join(d, "A"), os.path.join(d, "B")
    shutil.copytree(y1, A, symlinks=True)
    os.makedirs(B)
    shutil.copytree(os.path.join(y1, "league"), os.path.join(B, "league"), symlinks=True)
    step("copy year-1 root twice", t)
    for season in range(2, a.seasons + 1):
        target = EVENTS_PER_SEASON * season
        t = time.time()
        sh([os.path.join(TOOLS, "broadcast_build.py"), "next-season", "--root", A])
        procs = [
            subprocess.Popen(["nice", "-n", "10", PY, os.path.join(TOOLS, "broadcast_build.py"), "step", "--root", A], stdout=open(os.path.join(d, "A.season%d.log" % season), "w"), stderr=subprocess.STDOUT),
            subprocess.Popen(["nice", "-n", "10", PY, os.path.join(TOOLS, "league_runner.py"), os.path.join(B, "league", "league.db"), "--until-events", str(target),
                              "--control-dir", os.path.join(B, "league", "ctl")], stdout=open(os.path.join(d, "B.season%d.log" % season), "w"), stderr=subprocess.STDOUT)]
        rcs = [p.wait() for p in procs]
        if any(rcs):
            raise SystemExit("a job failed (builder rc=%d, twin rc=%d); see %s" % (rcs[0], rcs[1], d))
        step("season %d: builder-driven and free-running to event %d" % (season, target), t)
        da, db = digest(os.path.join(A, "league", "league.db")), digest(os.path.join(B, "league", "league.db"))
        st = status(A)
        rep["digests"]["season%d_event%d" % (season, target)] = {"builder_driven": da, "free_running": db, "identical": da == db, "builder_status": st}
        if da != db or da.get("events") != target:
            rep["ok"] = False
            print(json.dumps(rep, sort_keys=True))
            return 1
    rep["ok"] = True
    if a.daemon_check:
        t = time.time()
        C = os.path.join(d, "C")
        os.makedirs(C)
        subprocess.run(["cp", "-al", os.path.join(A, "store"), os.path.join(C, "store")], check=True)           # hard links: the check only adds carry.json (a new file) to the copy
        carry = os.path.join(C, "store", "carry.json")
        if os.path.exists(carry):
            os.unlink(carry)
        p = sh([os.path.join(TOOLS, "broadcast_multiseason_check.py"), "--root", C, "--public", os.path.join(d, "public"), "--state", os.path.join(d, "state"), "--step", "20",
                "--past", "600", "--through-end", "--report", os.path.join(d, "daemon_check.json")], check=False)
        rep["daemon_check"] = json.load(open(os.path.join(d, "daemon_check.json"))) if os.path.exists(os.path.join(d, "daemon_check.json")) else {"ok": False, "stderr": p.stderr[-500:]}
        rep["ok"] = rep["ok"] and bool(rep["daemon_check"].get("ok"))
        step("daemon through the whole of the last season (virtual time)", t)
    rep["wall_s"] = round(time.time() - t0, 1)
    if a.report:
        with open(a.report, "w") as f:
            json.dump(rep, f, indent=1, sort_keys=True)
    print(json.dumps(rep, sort_keys=True))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
