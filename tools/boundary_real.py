#!/usr/bin/env python3
"""One REAL-ENGINE season-boundary check with the supervisor, the daemon and the monitor running together as separate processes (docs/INTEGRATION_CANDIDATE_2026-11-07.md, section 10).

  boundary_real.py --source ROOT --work DIR [--hold-start-s 360] [--dwell-s 90] [--after-s 300] [--limits] [--cpu 0] [--accelerated-tail-s 0] [--out FILE]

TWO KINDS OF RUN. The default uses SUPPORTED CONTROLS ONLY: stage 1 below prepares the fixture before any process starts; from then on the harness only starts, waits for and stops the
processes, and a check reads the clock's own log (store/clock_log.jsonl) to prove that nothing but the daemon's code wrote clock.json during the real-time run (no rewrite of `ready_until`).
`--accelerated-tail-s N` ALSO runs stage 5, which rewrites clock.json after the processes are stopped and drives the daemon code in virtual time N more seconds into season 2 (12000 reaches the
first results, which real time cannot in a reasonable run). Everything it shows is labelled [accelerated] and is not a supported control. The processes are started with the exact ExecStart
argument lists of deploy/supervisor/*.service (tools/startup_check.py), in the documented order: daemon, monitor 3 s later, supervisor later.

SOURCE is a private root whose first season is completely built (47 events; it is only COPIED). A season is 48 hours of show clock, so the first 47 hours 54 minutes are NOT run in
real time: stage 1 drives the real daemon code in-process over virtual time (the same way tests/test_backup_resume_real.py and tools/engine_comparison.py do) up to --hold-start-s
before the end of season 1. That stage is a stand-in for two days of broadcasting; the monitor does not watch it. Everything after it is real time and separate processes:

  stage 2   monitor + daemon start (the daemon finds a clock that stopped, records the downtime, and plays on). Season 2 has NOT been prepared, so the daemon reaches the end of
            season 1 and HOLDS there (a safe hold with the real builder absent). It stays held for --dwell-s; the position must not move, nothing of season 2 may appear.
  stage 3   the supervisor starts. It prepares season 2 (`next-season`) and builds its events with the REAL engine. The daemon hands over as soon as season 2 is named, and the
            broadcast goes on --after-s seconds into season 2.
  stage 4   everything is stopped (SIGTERM) and checked from the files: every season-1 public file is byte for byte what it was at the hold (tests/published_bytes.py) and keeps
            its owner, mode, inode and modification time; the cumulative indexes keep their earlier entries as an exact prefix and name season 1 still; the public folder and the
            private store pass the backup tool's reference closure; the monitor logged no violation and never tripped; the viewers' position (sampled every second from the
            public clock) never went back, never ran faster than time and never passed the end of season 1 before the hand-over; no season-2 file is public that the plan does not
            release yet (the monitor's independent oracle, once more, at the end).

With --limits the three processes run in memory cgroups of the proposed unit limits (daemon 192M, monitor 512M, supervisor and builder 768M) inside a 2 GB parent, and the report
says whether any group hit its limit or killed anything. Nothing here installs, deploys, sends or deletes anything; the work folder is scratch.
"""
import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
import measure_combined as MC                      # noqa: E402
import public_backup as PBK                        # noqa: E402
import published_bytes as PUB                      # noqa: E402
import rehearsal_check as RC                       # noqa: E402
import startup_check as SU                         # noqa: E402
from dflsite import broadcast as B, season as S    # noqa: E402

PY = sys.executable


def log(msg):
    print("%s  %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def meta(base, skip=()):
    out = {}
    for d, _, names in os.walk(base):
        for n in names:
            if n.startswith(".tmp-"):
                continue
            p = os.path.join(d, n)
            rel = os.path.relpath(p, base).replace(os.sep, "/")
            if rel in skip:
                continue
            try:
                st = os.lstat(p)
            except FileNotFoundError:
                continue
            out[rel] = (st.st_size, st.st_mode, st.st_uid, st.st_ino, st.st_mtime_ns)
    return out


def clock_pos(root):
    try:
        st = B.get(os.path.join(root, "store", "clock.json"))
    except (OSError, ValueError):
        return None
    t = time.time()
    return B.position(st, t), st["state"], t                    # the instant the position was computed for: samples are stamped with it, so a slow read cannot look like a fast clock


DAEMON_CLOCK_REASONS = {"start", "pause", "resume", "downtime", "coverage_wait", "publish_failure"}       # every `why` that src/dflsite writes to store/clock_log.jsonl


def clock_log(store):
    try:
        with open(os.path.join(store, "clock_log.jsonl")) as f:
            return [json.loads(x) for x in f if x.strip()]
    except (OSError, ValueError):
        return []


def clock_log_len(store):
    return len(clock_log(store))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--hold-start-s", type=float, default=360.0, help="how long before the end of season 1 the real-time stages start")
    ap.add_argument("--dwell-s", type=float, default=90.0, help="how long the daemon stands at the end of season 1 before the supervisor is started")
    ap.add_argument("--after-s", type=float, default=300.0, help="seconds of season 2 to broadcast after the hand-over")
    ap.add_argument("--handover-timeout-s", type=float, default=1800.0)
    ap.add_argument("--accelerated-tail-s", type=float, default=0.0, help="ACCELERATED TEST, off by default: after the real-time stages, rewrite clock.json (drops ready_until) and drive the real daemon code in virtual time this many more seconds into season 2, so its first results are published and the cumulative indexes GROW. 12000 reaches them. Not a supported control: the default run never does it")
    ap.add_argument("--limits", action="store_true", help="run each process in a memory cgroup at the proposed unit limit")
    ap.add_argument("--cpu", type=int, default=0)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    work = os.path.abspath(a.work)
    if os.path.exists(work):
        sys.exit("--work must not exist: %s" % work)
    os.makedirs(work)
    root, public, guard = (os.path.join(work, n) for n in ("root", "public", "guard"))
    store = os.path.join(root, "store")
    report = {"checks": [], "limits": bool(a.limits), "mode": "accelerated tail (clock.json rewritten after the real-time run)" if a.accelerated_tail_s > 0 else "supported controls only (no clock edit after the processes start)"}
    checks = report["checks"]

    def check(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})
        log("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + str(detail)) if detail else ""))

    t0 = time.time()
    shutil.copytree(a.source, root, symlinks=True)
    open(os.path.join(store, "continuous"), "w").close()
    os.makedirs(public)
    os.makedirs(guard)
    report["copy_s"] = round(time.time() - t0, 1)
    plan1 = B.get(os.path.join(store, "plan.json"))
    end1 = float(plan1["total_s"])
    report["season1"] = {"season": plan1["season"], "length_s": end1}
    log("season %s is %.0f s long; copy took %.0f s" % (plan1["season"], end1, report["copy_s"]))

    # ------------------------------------------------------------------------------------------------------------------------------ stage 1: virtual time
    B.FSYNC = False
    bc = S.SeasonBroadcaster(store, public, guard_publication=False)
    ff_end = end1 - a.hold_start_s
    base = time.time() - ff_end
    bc.clock.start(base, 0.0, B.LEASE_S)
    t = 0.0
    t1 = time.time()
    while t < ff_end:
        bc.tick(base + t)
        t += 20.0
    wall_end = base + t
    st = bc.clock.state()
    pos = B.position(st, wall_end)
    st = dict(st, ready_until=pos + B.DELAY_S)                          # the production daemon always has this key; the 20 s virtual ticks could not keep it
    bc.clock._write(st, "fast_forward_end", wall_end)
    B.FSYNC = True
    report["stage1"] = {"virtual_to_s": ff_end, "wall_s": round(time.time() - t1, 1), "position_s": pos, "public_files": sum(len(f) for _, _, f in os.walk(public))}
    log("stage 1 done: position %.0f s of %.0f, %d public files, %.0f s" % (pos, end1, report["stage1"]["public_files"], report["stage1"]["wall_s"]))
    check("stage 1: season 1 not yet handed over", not os.path.exists(os.path.join(store, "carry.json")))
    del bc

    # ------------------------------------------------------------------------------------------------------------------------------ processes
    os.sched_setaffinity(0, {c for c in range(os.cpu_count()) if c != a.cpu} or {a.cpu})
    mb = lambda v: int(v * 1024 * 1024) if (a.limits and v) else None      # noqa: E731
    g_mon = MC.Group("monitor", parent_limit=int(2 * 1024 ** 3), shares=1024, limit=mb(512))
    g_dae = MC.Group("daemon", shares=1024, limit=mb(192))
    g_sup = MC.Group("supervisor", shares=205, limit=mb(768))
    tools = lambda n: os.path.join(HERE, n)       # noqa: E731
    srv, url = SU.serve(public)                   # the documented monitor probes a URL: a small local server stands in for the front end (tools/startup_check.py)
    # the EXACT ExecStart argument lists of deploy/supervisor/*.service (placeholders replaced, the ceiling a test number: the owner's is not chosen), not flags retyped here
    mon = MC.Proc("monitor", SU.unit_argv("dfl-broadcast-monitor", root, public, guard, url), g_mon, a.cpu, nice=10)
    dae = MC.Proc("daemon", SU.unit_argv("dfl-broadcast-daemon", root, public, guard, url), g_dae, a.cpu, stdout=True)
    sup = MC.Proc("supervisor", SU.unit_argv("dfl-supervisor", root, public, guard, url), g_sup, a.cpu, nice=15)
    samples = []                     # (wall, position, state)
    stop = threading.Event()

    def sampler():
        while not stop.wait(1.0):
            r = clock_pos(root)
            if r:
                samples.append((r[2], r[0], r[1]))

    th = threading.Thread(target=sampler, daemon=True)
    hold_snapshot = hold_meta = None
    carry_seen = None
    real_run_s = 0.0
    try:
        clog0 = clock_log_len(store)
        t_procs = time.time()
        dae.start()                                      # the documented order: the daemon first, the monitor 3 s later (docs/INTEGRATION_CANDIDATE_2026-11-07.md, section 15)
        time.sleep(3)
        mon.start()
        th.start()
        # stage 2: reach the end of season 1 and stand there
        deadline = time.time() + a.hold_start_s + 600
        while time.time() < deadline:
            r = clock_pos(root)
            if r and r[0] >= end1 - 0.5:
                break
            time.sleep(1)
        else:
            check("stage 2: the daemon reached the end of season 1", False, "position %s" % (clock_pos(root),))
            raise SystemExit(1)
        t_hold = time.time()
        names_at_arrival = set(meta(public))
        log("stage 2: at the end of season 1 after %.0f s of real time; standing for %.0f s" % (t_hold - t1 - report["stage1"]["wall_s"], a.dwell_s))
        time.sleep(a.dwell_s)
        r = clock_pos(root)
        check("stage 2: the position stands still at the end of season 1 while season 2 is not prepared", r and abs(r[0] - end1) < 1.0 and not os.path.exists(os.path.join(store, "carry.json")), r)
        check("stage 2: the daemon is alive and the monitor is not tripped", dae.p.poll() is None and mon.p.poll() is None and not os.path.exists(os.path.join(guard, "TRIPPED")))
        new_names = sorted(set(meta(public)) - names_at_arrival)
        check("stage 2: during the hold no file appeared in the public folder", not new_names, new_names[:5])
        hold_snapshot = PUB.snapshot(public)
        hold_meta = meta(public)
        report["hold"] = {"files": len(hold_meta), "indexes": {k: len(v) for k, v in hold_snapshot["indexes"].items()}}
        log("hold snapshot: %d files, indexes %s" % (len(hold_meta), report["hold"]["indexes"]))

        # stage 3: the supervisor
        sup.start()
        t_sup = time.time()
        log("stage 3: supervisor started")
        deadline = time.time() + a.handover_timeout_s
        while time.time() < deadline:
            if os.path.exists(os.path.join(store, "carry.json")):
                carry_seen = time.time()
                break
            if any(p.p.poll() is not None for p in (dae, mon, sup)):
                break
            time.sleep(2)
        check("stage 3: the daemon handed over to season 2", carry_seen is not None, "after %.0f s" % ((carry_seen or time.time()) - t_sup))
        report["handover_after_supervisor_s"] = round((carry_seen or time.time()) - t_sup, 1)
        if carry_seen:
            deadline = time.time() + a.after_s + 120
            while time.time() < deadline:
                r = clock_pos(root)
                if r and r[0] >= end1 + a.after_s:
                    break
                if any(p.p.poll() is not None for p in (dae, mon, sup)):
                    break
                time.sleep(2)
        r = clock_pos(root)
        check("stage 3: the broadcast is %.0f s into season 2" % a.after_s, r and r[0] >= end1 + a.after_s - 2, r)
        report["supervisor_health"] = None
        try:
            with open(os.path.join(root, "supervisor", "health.json")) as f:
                report["supervisor_health"] = json.load(f)
        except (OSError, ValueError):
            pass
        stop.set()
        th.join(timeout=3)
        cpu_s = {p.name: round(MC.tree_cpu(p.p.pid), 1) for p in (mon, dae, sup) if p.p is not None and p.p.poll() is None}
        alive = {p.name: p.p.poll() is None for p in (mon, dae, sup)}
        rus = {}
        for p in (sup, mon, dae):
            ru = p.stop()
            rus[p.name] = round(ru.ru_maxrss / 1024.0, 1) if ru else None
        real_run_s = time.time() - t_procs
        run_log = clock_log(store)[clog0:]
        foreign = sorted({e.get("why") for e in run_log} - DAEMON_CLOCK_REASONS)
        report["clock_log_during_real_time_run"] = {"entries": len(run_log), "reasons": sorted({e.get("why") for e in run_log}), "not_made_by_the_daemon": foreign}
        check("SUPPORTED CONTROLS ONLY during the real-time run: every clock-log entry is one the daemon's own code writes (%s); nothing rewrote clock.json or ready_until" % ", ".join(sorted({e.get("why") for e in run_log})), not foreign, foreign)
        report["processes_alive_until_stopped"] = alive
        report["peak_rss_mb"] = rus
        report["cpu_seconds"] = cpu_s
        report["cgroups"] = {g.name: dict(g.pressure(), peak_mb=round((g.peak() or 0) / 1e6, 1)) for g in (g_mon, g_dae, g_sup)}
        check("all three processes ran to the end (none died)", all(alive.values()), alive)
    finally:
        stop.set()
        srv.shutdown()
        for p in (sup, mon, dae):
            try:
                if p.p is not None and p.p.poll() is None:
                    p.stop()
            except Exception:
                pass

    # ------------------------------------------------------------------------------------------------------------------------------ stage 5: virtual time again
    vtail = {}
    final_now = None
    if a.accelerated_tail_s > 0 and carry_seen is not None:
        B.FSYNC = False
        bc = S.SeasonBroadcaster(store, public, guard_publication=False)
        vnow = time.time()
        st = dict(bc.clock.state())
        st.pop("ready_until", None)                                     # 20 s virtual ticks cannot keep the 3 s publication horizon of a real-time daemon (as in stage 1)
        st["state"] = "running"
        st["lease_until"] = max(st["lease_until"], vnow + 90)
        bc.clock._write(st, "virtual_tail_start", vnow)
        target = end1 + a.after_s + a.accelerated_tail_s
        vpos = []
        guard_t = 0
        while True:
            bc.tick(vnow)
            p_ = bc.clock.position(vnow)
            vpos.append(p_)
            if p_ >= target or guard_t > 20000:
                break
            vnow += 20.0
            guard_t += 1
        final_now = vnow
        B.FSYNC = True
        vtail = {"virtual_seconds": round(20.0 * guard_t, 1), "position_s": vpos[-1], "ticks": guard_t,
                 "went_back": sum(1 for x, y in zip(vpos, vpos[1:]) if y < x - 0.01), "max_step_s": round(max([y - x for x, y in zip(vpos, vpos[1:])] or [0]), 1)}
        report["stage5"] = vtail
        log("stage 5 (ACCELERATED, not a supported control): virtual time to position %.0f s (season 2 + %.0f s), %d ticks" % (vpos[-1], vpos[-1] - end1, guard_t))
        check("stage 5 [accelerated]: the broadcast reached the target position (the virtual clock is NOT gated by publication coverage here, so this says nothing about holds)", vpos[-1] >= target, vpos[-1])
        check("stage 5 [accelerated]: the position never went back", vtail["went_back"] == 0)
        check("stage 5 [accelerated]: the position never advanced faster than the 20 s tick (max step %.1f s)" % vtail["max_step_s"], vtail["max_step_s"] <= 20.5)

    # ------------------------------------------------------------------------------------------------------------------------------ stage 4: from the files
    log("stage 4: checking the files")
    if hold_snapshot is not None:
        problems = PUB.changed(hold_snapshot, public, limit=50)
        check("historical files: every season-1 public file has its bytes; indexes keep their earlier entries as a prefix", not problems, problems[:5])
        now_meta = meta(public)
        exempt = lambda rel: rel in PUB.CONTROLS or PUB.LIVE_MANIFEST.match(rel) or rel in PUB.APPEND_ONLY      # noqa: E731
        diff = [rel for rel, m in hold_meta.items() if not exempt(rel) and now_meta.get(rel) != m]
        check("historical files: owner, mode, inode, size and modification time unchanged (controls, indexes and live manifests excepted)", not diff, diff[:5])
        exempt_changed = sorted(rel for rel, m in hold_meta.items() if exempt(rel) and now_meta.get(rel) != m)
        report["exempt_files_replaced_by_design"] = exempt_changed
        snap_now = PUB.snapshot(public)
        grew = {k: (report["hold"]["indexes"][k], len(v)) for k, v in snap_now["indexes"].items() if k in report["hold"]["indexes"]}
        report["indexes_before_after"] = grew
        check("cumulative indexes: season 1's entries are still there and the indexes did not shrink", all(b >= a_ for a_, b in grew.values()), grew)
        if vtail:
            check("cumulative indexes [accelerated tail only]: season 2's first results were appended (the playback index grew past season 1's 400 games)", grew.get("playback/index.json", (0, 0))[1] > grew.get("playback/index.json", (0, 0))[0], grew)
        check("cumulative indexes: every entry that was there at the hold is byte-for-byte unchanged and in the same place (an exact prefix)", not PUB.changed(hold_snapshot, public), "")
    closure_pub = PBK.CLOSURE["public"](PBK.Tree(public))
    closure_store = PBK.CLOSURE["store"](PBK.Tree(store))
    check("reference closure of the public folder (every index entry has its file, every release matches its manifest)", not closure_pub, list(closure_pub)[:5])
    check("reference closure of the private store", not closure_store, list(closure_store)[:5])
    carry = None
    try:
        carry = B.get(os.path.join(store, "carry.json"))
    except (OSError, ValueError):
        pass
    check("carry.json names season 1 as finished and season 2 on air", bool(carry) and carry.get("seasons") == [plan1["season"]] and carry.get("season") == plan1["season"] + 1, carry and {k: carry[k] for k in ("season", "seasons") if k in carry})
    try:
        with open(os.path.join(guard, "watch.log")) as f:
            mlog = [json.loads(x) for x in f if x.strip()]
    except OSError:
        mlog = []
    viol = [m for m in mlog if "violation" in m]
    report["monitor"] = {"heartbeats": sum(1 for m in mlog if m.get("heartbeat")), "violations": viol[:10], "violation_count": len(viol)}
    check("monitor: no violation over the whole real-time run, no trip", not viol and not os.path.exists(os.path.join(guard, "TRIPPED")), viol[:3])
    need_beats = max(2, int(real_run_s / 30.0 * 0.5))                       # one heartbeat per 30 s full pass (the unit's --full-every): at least half of them
    check("monitor: it was checking (%d heartbeats seen over %.0f s of real time; at least %d expected)" % (report["monitor"]["heartbeats"], real_run_s, need_beats), report["monitor"]["heartbeats"] >= need_beats, report["monitor"]["heartbeats"])
    # the viewers' position
    pos_series = [(w, p) for w, p, _ in samples]
    back = [(w, p, q) for (w, p), (_, q) in zip(pos_series, pos_series[1:]) if q < p - 0.01]
    fast = 0.0
    for (w1, p1), (w2, p2) in zip(pos_series, pos_series[1:]):
        if w2 > w1:
            fast = max(fast, (p2 - p1) / (w2 - w1))
    before_hand = [p for w, p in pos_series if carry_seen is None or w < carry_seen]
    report["viewer_position"] = {"samples": len(pos_series), "max_rate": round(fast, 3), "went_back": len(back), "max_before_handover": max(before_hand) if before_hand else None}
    check("viewers' position never went back", not back, back[:3])
    check("viewers' position never ran faster than time (max rate %.2f/s)" % fast, fast <= 1.6, fast)
    check("viewers' position never passed the end of season 1 before the hand-over", bool(before_hand) and max(before_hand) <= end1 + 1.0, max(before_hand) if before_hand else None)
    # no early results, independently of the live monitor: the same oracle, once more, over the final files
    try:
        wa = RC.argparse.Namespace(root=root, public=public, url=None, log=os.path.join(work, "final.log"), evidence=os.path.join(work, "final-evidence"), every=1.0, full_every=1.0,
                                   min_free_gb=0.0, max_public_gb=0.0, max_public_gb_per_season=1.25, public_base_gb=0.25, max_public_ceiling_gb=0.0, guard=os.path.join(work, "final-guard"), canary=None, cgroup=None)
        os.makedirs(wa.guard)
        w = RC.Watch(wa)
        now = final_now if final_now is not None else time.time()
        from unittest import mock
        with mock.patch.object(RC.time, "time", lambda: now):
            pos_final = w.cheap(now)
            w.full(now, pos_final)
        check("no early results: the oracle's final pass over the files finds nothing (position %.0f s, season 2 on air)" % pos_final, w.found == 0, w.found)
    except Exception as e:                                             # a failure to run the check is a failure of the check
        check("no early results: the oracle's final pass could run", False, "%s: %s" % (type(e).__name__, e))
    plan2 = None
    try:
        plan2 = B.get(os.path.join(store, "seasons", "s002", "plan.json"))
        builds2 = B.get(os.path.join(store, "seasons", "s002", "builds.json"))
        report["season2"] = {"events_built": len(builds2["events"]), "season": plan2["season"]}
    except (OSError, ValueError):
        pass
    report["disk_free_gb_end"] = round(shutil.disk_usage(work).free / 1e9, 1)
    report["public_gb_end"] = round(sum(m[0] for m in meta(public).values()) / 1e9, 3)
    report["pass"] = all(c["ok"] for c in checks)
    for g in (g_sup, g_dae, g_mon):
        g.remove()
    log("RESULT: %s (%d checks, %d failed)" % ("PASS" if report["pass"] else "FAIL", len(checks), sum(1 for c in checks if not c["ok"])))
    if a.out:
        with open(a.out, "w") as f:
            json.dump(report, f, indent=1, sort_keys=True, default=str)
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
