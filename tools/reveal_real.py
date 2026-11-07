#!/usr/bin/env python3
"""Real time across season 2's FIRST RESULTS REVEAL, with supported controls only after each start (docs/INTEGRATION_CANDIDATE_2026-11-07.md, section 17).

  reveal_real.py --source ROOT --work DIR [--lead-s 150] [--tail-s 180] [--limits] [--out FILE]

The first results of a season are released 8,940 s (2 h 29 min) after the season starts, which a real-time test cannot sit through. So the run has three parts, and says which is which:

  SEGMENT A  (real time, supported controls only)   tools/boundary_real.py, unchanged: season 1's end, the hold, the hand-over, the supervisor building season 2 with the real engine.
  JUMP       (FIXTURE PREPARATION, not a supported control)   with every process stopped, the clock is rewritten and the real daemon code is driven in virtual time (in-process, as
             boundary_real.py's stage 1) from where segment A stopped to --lead-s seconds before the first reveal. This is the only place the clock is edited; nothing is running.
  SEGMENT B  (real time, supported controls only)   the daemon, the monitor and the supervisor are started with the unit files' own ExecStart lines in the documented order, and from
             then on the harness only reads. The broadcast crosses the reveal in real time. Nothing edits clock.json or ready_until: the clock's own log is checked.

Observed in segment B, once a second (position, size of both indexes) and every 5 s (tools/index_reconcile.py, by (season, game ID)): the replay library running ahead of the playback
index (the intended reveal delay), the playback index growing in ONE step at the reveal, and the two converging at that moment: every game that was waiting is in both, nothing else
differs, no duplicate. Plus the same file checks as the boundary run (earlier files byte for byte, indexes grow by appending, closure, no early result, a quiet monitor).
Nothing is installed, sent or deleted; SOURCE is only copied; the work folder is scratch.
"""
import argparse
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
import boundary_real as BRL                        # noqa: E402
import broadcast_season_check as SC                # noqa: E402
import index_reconcile as IR                       # noqa: E402
import measure_combined as MC                      # noqa: E402
import public_backup as PBK                        # noqa: E402
import published_bytes as PUB                      # noqa: E402
import rehearsal_check as RC                       # noqa: E402
import startup_check as SU                         # noqa: E402
from dflsite import broadcast as B, season as S    # noqa: E402

log = BRL.log
TOL = IR.TOL_S


def first_reveal(store):
    """The position at which the season on air first releases a game's results (the first playback file), from the monitor's oracle."""
    o = SC.FollowingOracle(store)
    at = [r["at_s"] for r in o._cur.view.reveals if r["dst"].startswith("playback/") and r["dst"] != "playback/index.json"]
    return min(at) if at else None


def jump(root, public, to_pos):
    """FIXTURE PREPARATION with nothing running: rewrite the clock and drive the real daemon code in virtual time to `to_pos`, ending 'now' with a freshly lapsed lease, as stage 1 does."""
    store = os.path.join(root, "store")
    B.FSYNC = False
    bc = S.SeasonBroadcaster(store, public, guard_publication=False)
    st = dict(bc.clock.state())
    pos0 = B.position(st, time.time())
    if to_pos <= pos0:
        raise SystemExit("the clock is already at %.0f s, past %.0f s" % (pos0, to_pos))
    v0 = time.time() - (to_pos - pos0) - 5.0
    st.pop("ready_until", None)
    st.update(state="running", anchor_pos=pos0, anchor_wall=v0, lease_until=v0 + B.LEASE_S, epoch=st["epoch"] + 1)
    bc.clock._write(st, "fixture_jump_start", v0)
    v, ticks = v0, 0
    while bc.clock.position(v) < to_pos and ticks < int((to_pos - pos0) / 20) + 2000:
        bc.tick(v)
        v += 20.0
        ticks += 1
    bc.tick(v)
    st = dict(bc.clock.state())
    pos = B.position(st, v)
    bc.clock._write(dict(st, ready_until=pos + B.DELAY_S), "fixture_jump_end", v)
    B.FSYNC = True
    return {"from_s": round(pos0, 1), "to_s": round(pos, 1), "ticks": ticks, "ended_s_ago": round(time.time() - v, 1)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--hold-start-s", type=float, default=120.0)
    ap.add_argument("--dwell-s", type=float, default=30.0)
    ap.add_argument("--after-s", type=float, default=360.0, help="segment A: seconds of season 2 to broadcast (the supervisor builds events meanwhile)")
    ap.add_argument("--lead-s", type=float, default=150.0, help="segment B starts this long before the first reveal")
    ap.add_argument("--tail-s", type=float, default=180.0, help="segment B runs this long after the first reveal")
    ap.add_argument("--limits", action="store_true")
    ap.add_argument("--cpu", type=int, default=0)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    work = os.path.abspath(a.work)
    report = {"checks": [], "mode": "segment A and B: supported controls only after each start; the jump between them is fixture preparation with nothing running"}
    checks = report["checks"]

    def check(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})
        log("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + str(detail)) if detail else ""))

    def finish():
        report["pass"] = all(c["ok"] for c in checks)
        log("RESULT: %s (%d checks, %d failed)" % ("PASS" if report["pass"] else "FAIL", len(checks), sum(1 for c in checks if not c["ok"])))
        if a.out:
            with open(a.out, "w") as f:
                json.dump(report, f, indent=1, sort_keys=True, default=str)
        return 0 if report["pass"] else 1

    # ---------------------------------------------------------------------------------------------------------------------------------- segment A
    log("SEGMENT A (real time, supported controls): tools/boundary_real.py")
    wa = os.path.join(work, "A")
    os.makedirs(work, exist_ok=True)
    cmd = ["--source", a.source, "--work", wa, "--hold-start-s", str(a.hold_start_s), "--dwell-s", str(a.dwell_s), "--after-s", str(a.after_s), "--cpu", str(a.cpu),
           "--out", os.path.join(work, "segment_A.json")] + (["--limits"] if a.limits else [])
    rc = BRL.main(cmd)
    with open(os.path.join(work, "segment_A.json")) as f:
        ra = json.load(f)
    report["segment_A"] = {"mode": ra["mode"], "checks": len(ra["checks"]), "failed": [c["check"] for c in ra["checks"] if not c["ok"]]}
    check("segment A (the supported-controls boundary run) passed all %d of its checks" % len(ra["checks"]), rc == 0 and ra["pass"], report["segment_A"]["failed"])
    if rc != 0:
        return finish()
    root, public, guard = (os.path.join(wa, n) for n in ("root", "public", "guard"))
    store = os.path.join(root, "store")

    # ---------------------------------------------------------------------------------------------------------------------------------- the jump
    t_rev = first_reveal(store)
    check("season 2's first results reveal was found in its plan", t_rev is not None, t_rev)
    if t_rev is None:
        return finish()
    report["first_reveal_s"] = t_rev
    log("JUMP (fixture preparation, nothing running): to %.0f s, %.0f s before the first reveal at %.0f s" % (t_rev - a.lead_s, a.lead_s, t_rev))
    t0j = time.time()
    report["jump"] = jump(root, public, t_rev - a.lead_s)
    report["jump"]["wall_s"] = round(time.time() - t0j, 1)
    log("jump done: %s" % report["jump"])
    check("the jump reached its target (position %.0f s of %.0f s)" % (report["jump"]["to_s"], t_rev - a.lead_s), report["jump"]["to_s"] >= t_rev - a.lead_s - 1.0, report["jump"])
    if report["jump"]["to_s"] < t_rev - a.lead_s - 1.0:
        return finish()
    pb0 = len(IR._games(os.path.join(public, "playback", "index.json")))
    lib0 = len(IR._games(os.path.join(public, "broadcast", "replays", "index.json")))
    report["indexes_at_B_start"] = {"playback": pb0, "library": lib0}
    log("indexes at the start of segment B: playback %d, replay library %d" % (pb0, lib0))
    snapshot = PUB.snapshot(public)
    clog0 = BRL.clock_log_len(store)

    # ---------------------------------------------------------------------------------------------------------------------------------- segment B
    log("SEGMENT B (real time, supported controls only)")
    os.sched_setaffinity(0, {c for c in range(os.cpu_count()) if c != a.cpu} or {a.cpu})
    mb = lambda v: int(v * 1024 * 1024) if (a.limits and v) else None      # noqa: E731
    g_mon = MC.Group("monitor", parent_limit=int(2 * 1024 ** 3), shares=1024, limit=mb(512))
    g_dae = MC.Group("daemon", shares=1024, limit=mb(192))
    g_sup = MC.Group("supervisor", shares=205, limit=mb(768))
    srv, url = SU.serve(public)
    mon = MC.Proc("monitor", SU.unit_argv("dfl-broadcast-monitor", root, public, guard, url), g_mon, a.cpu, nice=10)
    dae = MC.Proc("daemon", SU.unit_argv("dfl-broadcast-daemon", root, public, guard, url), g_dae, a.cpu, stdout=True)
    sup = MC.Proc("supervisor", SU.unit_argv("dfl-supervisor", root, public, guard, url), g_sup, a.cpu, nice=15)
    series, recs = [], []                                   # per second: (wall, pos, state, library, playback); every 5 s: a reconcile record
    t_end = time.time() + a.lead_s + a.tail_s + 120
    try:
        dae.start()
        time.sleep(3)
        mon.start()
        time.sleep(3)
        sup.start()
        t_procs = time.time()
        last_rec = 0.0
        while time.time() < t_end:
            r = BRL.clock_pos(root)
            if not r:
                time.sleep(1)
                continue
            pos, state, t = r
            lib = len(IR._games(os.path.join(public, "broadcast", "replays", "index.json")))
            pb = len(IR._games(os.path.join(public, "playback", "index.json")))
            series.append((t, pos, state, lib, pb))
            if pos >= t_rev + a.tail_s:
                break
            if any(p.p.poll() is not None for p in (dae, mon, sup)):
                break
            if t - last_rec >= 5.0:
                last_rec = t
                try:
                    rc_ = IR.reconcile(root, public, pos, BRL.clock_pos(root)[0])
                    recs.append({"pos": pos, "ok": rc_["ok"], "counts": rc_["counts"], "duplicates": rc_["duplicates"], "replays": rc_["replays"], "playback": rc_["playback"],
                                 "rows": [{k: x[k] for k in ("season", "gid", "class", "results_due_at_s")} for x in rc_["rows"]]})
                except Exception as e:                       # a failure to look is recorded, and fails the check below
                    recs.append({"pos": pos, "ok": False, "error": "%s: %s" % (type(e).__name__, e)})
            time.sleep(max(0.0, 1.0 - (time.time() - t)))
        real_run_s = time.time() - t_procs
        alive = {p.name: p.p.poll() is None for p in (mon, dae, sup)}
        run_log = BRL.clock_log(store)[clog0:]
        for p in (sup, mon, dae):
            p.stop()
        report["cgroups"] = {g.name: dict(g.pressure(), peak_mb=round((g.peak() or 0) / 1e6, 1)) for g in (g_mon, g_dae, g_sup)}
    finally:
        srv.shutdown()
        for p in (sup, mon, dae):
            try:
                if p.p is not None and p.p.poll() is None:
                    p.stop()
            except Exception:
                pass

    # ---------------------------------------------------------------------------------------------------------------------------------- checks
    log("checking segment B: %d samples, %d reconcile records" % (len(series), len(recs)))
    reasons = sorted({e.get("why") for e in run_log})
    foreign = sorted(set(reasons) - BRL.DAEMON_CLOCK_REASONS)
    report["segment_B"] = {"real_run_s": round(real_run_s, 1), "clock_log_reasons": reasons, "samples": len(series), "reconcile_records": len(recs), "alive_until_stopped": alive}
    check("SEGMENT B, SUPPORTED CONTROLS ONLY: every clock-log entry is one the daemon's own code writes (%s); nothing rewrote clock.json or ready_until" % ", ".join(reasons), not foreign, foreign)
    check("segment B: all three processes ran to the end (none died)", all(alive.values()), alive)
    if not series:
        check("segment B produced samples", False, "none")
        return finish()
    check("segment B started %.0f s before the reveal (first sample at position %.0f s; the reveal is at %.0f s)" % (t_rev - series[0][1], series[0][1], t_rev), abs((t_rev - series[0][1]) - a.lead_s) < 40, series[0][1])
    pre = [x for x in series if x[1] < t_rev - 10]
    post = [x for x in series if x[1] > t_rev + 10]
    check("segment B crossed the reveal in real time (samples: %d before, %d after)" % (len(pre), len(post)), len(pre) >= 60 and len(post) >= 60, (len(pre), len(post)))
    if pre and post:
        pb_before, pb_after = pre[-1][4], post[-1][4]
        steps = [(series[i][1], series[i - 1][4], series[i][4]) for i in range(1, len(series)) if series[i][4] != series[i - 1][4]]
        check("the playback index did not change before the reveal (%d entries the whole time)" % pb_before, all(x[4] == pb_before for x in pre), sorted({x[4] for x in pre}))
        check("the playback index grew in ONE step at the reveal: %s" % steps, len(steps) == 1 and steps[0][2] > steps[0][1], steps)
        if steps:
            check("the growth appeared within 8 s of the reveal's position (at %.1f s, the reveal is %.1f s)" % (steps[0][0], t_rev), abs(steps[0][0] - t_rev) <= 8.0, steps[0][0] - t_rev)
        lib_series = [x[3] for x in series]
        check("the replay library never shrank and the playback index never shrank", all(b >= a_ for a_, b in zip(lib_series, lib_series[1:])) and all(b[4] >= a_[4] for a_, b in zip(series, series[1:])), "")
        r_pre = [r for r in recs if r.get("pos", 0) < t_rev - 10 and "rows" in r]
        r_post = [r for r in recs if r.get("pos", 0) > t_rev + 10 and "rows" in r]
        waiting = {(x["season"], x["gid"]) for x in (r_pre[-1]["rows"] if r_pre else []) if x["class"] == "reveal_delay" and x.get("results_due_at_s") is not None and abs(x["results_due_at_s"] - t_rev) < 0.5}
        check("before the reveal the replay library ran ahead: %d games had ended and were waiting for this reveal (intended reveal delay)" % len(waiting), len(waiting) >= 1, len(waiting))
        still = {(x["season"], x["gid"]) for x in (r_post[-1]["rows"] if r_post else [])}
        check("after the reveal every one of those %d games is in BOTH indexes (none left waiting, none missing)" % len(waiting), bool(r_post) and not (waiting & still), sorted(waiting & still)[:5])
        check("the playback index grew by exactly the number of games that were waiting (%d = %d)" % (pb_after - pb_before, len(waiting)), pb_after - pb_before == len(waiting), (pb_before, pb_after))
        if r_pre and r_post:
            check("the count of games in both indexes rose by that number at the reveal", r_post[-1]["counts"].get("both", 0) - r_pre[-1]["counts"].get("both", 0) >= len(waiting), (r_pre[-1]["counts"], r_post[-1]["counts"]))
        report["reveal"] = {"waiting_before": len(waiting), "playback_before": pb_before, "playback_after": pb_after, "step": steps, "library_before": pre[-1][3], "library_after": post[-1][3]}
    bad = [r for r in recs if not r.get("ok")]
    check("every reconcile record (%d, by season and game ID) found only intended differences and no duplicate" % len(recs), recs and not bad, bad[:2])
    problems = PUB.changed(snapshot, public, limit=50)
    check("every file and every index entry present at the start of segment B is unchanged and in the same place (indexes grow by appending only)", not problems, problems[:5])
    closure_pub = PBK.CLOSURE["public"](PBK.Tree(public))
    closure_store = PBK.CLOSURE["store"](PBK.Tree(store))
    check("reference closure of the public folder", not closure_pub, list(closure_pub)[:5])
    check("reference closure of the private store", not closure_store, list(closure_store)[:5])
    try:
        with open(os.path.join(guard, "watch.log")) as f:
            mlog = [json.loads(x) for x in f if x.strip()]
    except OSError:
        mlog = []
    # the log also holds segment A: only lines written since segment B started count
    mlog = [m for m in mlog if m.get("at", 0) >= t_procs]
    viol = [m for m in mlog if "violation" in m]
    beats = sum(1 for m in mlog if m.get("heartbeat"))
    check("monitor over segment B: no violation, no trip, %d heartbeats" % beats, not viol and not os.path.exists(os.path.join(guard, "TRIPPED")) and beats >= max(2, int(real_run_s / 30 * 0.5)), viol[:3])
    pos_series = [(x[0], x[1]) for x in series]
    back = [(w, p, q) for (w, p), (_, q) in zip(pos_series, pos_series[1:]) if q < p - 0.01]
    fast = max([(p2 - p1) / (w2 - w1) for (w1, p1), (w2, p2) in zip(pos_series, pos_series[1:]) if w2 > w1] or [0.0])
    check("viewers' position never went back and never ran faster than time (max rate %.2f/s)" % fast, not back and fast <= 1.6, back[:3])
    try:
        wn = RC.argparse.Namespace(root=root, public=public, url=None, log=os.path.join(work, "final.log"), evidence=os.path.join(work, "final-evidence"), every=1.0, full_every=1.0,
                                   min_free_gb=0.0, max_public_gb=0.0, max_public_gb_per_season=1.25, public_base_gb=0.25, max_public_ceiling_gb=0.0, guard=os.path.join(work, "final-guard"), canary=None, cgroup=None)
        os.makedirs(wn.guard)
        w = RC.Watch(wn)
        now = time.time()
        pos_final = w.cheap(now)
        w.full(now, pos_final)
        check("no early results: the oracle's final pass over the files finds nothing (position %.0f s)" % pos_final, w.found == 0, w.found)
    except Exception as e:
        check("no early results: the oracle's final pass could run", False, "%s: %s" % (type(e).__name__, e))
    report["series_every_10s"] = [(round(x[0] - series[0][0], 0), round(x[1], 0), x[2], x[3], x[4]) for x in series[::10]]
    report["disk_free_gb_end"] = round(shutil.disk_usage(work).free / 1e9, 1)
    for g in (g_sup, g_dae, g_mon):
        g.remove()
    return finish()


if __name__ == "__main__":
    sys.exit(main())
