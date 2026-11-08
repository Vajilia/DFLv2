#!/usr/bin/env python3
"""Real memory-cgroup workloads for the monitor's memory rule (docs/MEMORY_MONITOR_FIX_2026-11-08.md).

  memory_workloads.py --out DIR [--limit-mb 256] [--scenarios a,b,...] [--duration S]

Each scenario starts real processes inside a REAL memory cgroup with a hard limit (the stand-in for the unit's MemoryMax), samples the group once a second with the monitor's own reader
(tools/rehearsal_check.py read_cgroup_memory) and judges every sample two ways:

  old rule   memory.current above 90 % of the limit on ONE sample (what stopped the first rehearsal)
  new rule   MemoryJudge, live, at 1 Hz with the production window (60 s, 3 samples); AND replayed from the same trace at the monitor's real cadence (--every 10: one sample in ten,
             at each of the ten possible phases), because the monitor is not run at 1 Hz

WHAT THIS HOST IS. cgroup v1 and no systemd (the Droplet is cgroup v2). The kernel's memory accounting, page cache, dirty/writeback pages and OOM killer are real; four things differ:
the file names (read_cgroup_memory maps v1 onto the v2 names), failcnt stands in for memory.events `max`, the pressure file is the whole host's (nothing else is running here), and
dirty-page throttling is global in v1 but per group in v2 (so a dirty flood is HARSHER here than on the Droplet). Nothing here ran on the Droplet, which is not touched.

Scenarios (the limit L is --limit-mb, the line is 0.9 L):
  healthy_cache         a reader streams 3 L of files over and over: memory.current goes to the limit, nothing unreclaimable grows     expect: old trips, new does not
  anon_spike            anonymous memory to 0.93 L for 15 s, then freed                                                              expect: old trips, new does not
  anon_plus_cache       0.55 L anonymous + the cache reader                                                                          expect: old trips, new does not
  anon_plateau          anonymous memory to 0.93 L and held (a leak that stopped, or a daemon that really is that big)               expect: both trip; new after the window
  anon_slow_leak        0.85 L, then +0.3 MB/s: the group crosses the line, and the kernel would kill it ~2 minutes later             expect: new trips BEFORE any kernel kill
  anon_fast_growth      anonymous memory at 15 MB/s past the limit                                                                   expect: the kernel kills it; new trips on the OOM event
  dirty_flood           a writer that never syncs                                                                                    recorded: peak dirty+writeback, verdict follows the numbers
  anon_plus_dirty       0.80 L anonymous + the dirty writer                                                                          recorded: dirty pages are counted as pinned, verdict follows the numbers
Nothing outside the scratch folder and the scenario cgroups is touched; files and cgroups are removed at the end of each scenario.
"""
import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "src"))
import measure_combined as MC          # noqa: E402  (the v1 group helper the other measurements use)
import rehearsal_check as RC           # noqa: E402

MB = 1 << 20

SCENARIOS = {
    "healthy_cache": dict(workers=[("cache", {})], expect=dict(old=True, new=False)),
    "anon_spike": dict(workers=[("anon", {"steps": [["grow", 0.93, 40], ["hold", 15], ["free"], ["hold", 600]]})], expect=dict(old=True, new=False)),
    "anon_plus_cache": dict(workers=[("anon", {"steps": [["grow", 0.55, 20], ["hold", 600]]}), ("cache", {})], expect=dict(old=True, new=False)),
    "anon_plateau": dict(workers=[("anon", {"steps": [["grow", 0.93, 10], ["hold", 600]]})], expect=dict(old=True, new=True, reason="sustained_unreclaimable")),
    "anon_slow_leak": dict(workers=[("anon", {"steps": [["grow", 0.85, 20], ["leak", 0.3, 600]]})], expect=dict(old=True, new=True, reason="sustained_unreclaimable", before_kill=True), duration=240),
    "anon_fast_growth": dict(workers=[("anon", {"steps": [["grow", 2.0, 15], ["hold", 600]]})], expect=dict(old=True, new=True, reason="oom_event"), duration=60),
    "dirty_flood": dict(workers=[("dirty", {})], expect=None),
    "anon_plus_dirty": dict(workers=[("anon", {"steps": [["grow", 0.80, 20], ["hold", 600]]}), ("dirty", {})], expect=None),
}


# ---- the workers (run inside the group) ---------------------------------------------------------------------------------------------------------------------------------
def worker(kind, spec, limit, workdir, cgmem):
    if kind == "anon":
        held = []

        def rss():                                                                   # the GROUP's anonymous memory as the kernel charges it (the interpreters' own included): the target is TOTAL anonymous memory
            with open(os.path.join(cgmem, "memory.stat")) as f:
                for ln in f:
                    if ln.startswith("rss "):
                        return int(ln.split()[1])
            return 0
        for step in spec["steps"]:
            if step[0] == "grow":
                target, rate = int(step[1] * limit), step[2]
                while rss() < target:
                    held.append(bytes([1]) * MB)
                    time.sleep(1.0 / rate)
            elif step[0] == "leak":
                end, rate = time.time() + step[2], step[1]
                while time.time() < end:
                    held.append(bytes([2]) * MB)
                    time.sleep(1.0 / rate)
            elif step[0] == "hold":
                time.sleep(step[1])
            elif step[0] == "free":
                held.clear()
        return
    if kind == "cache":
        files = [os.path.join(workdir, "cache_%d.bin" % i) for i in range(3)]
        buf = bytes([3]) * MB
        for fn in files:                                                              # written and flushed, then evicted, so that READING them is what charges this group
            with open(fn, "wb") as f:
                for _ in range(limit // MB):
                    f.write(buf)
                f.flush()
                os.fsync(f.fileno())
                os.posix_fadvise(f.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
        while True:
            for fn in files:
                with open(fn, "rb") as f:
                    while f.read(MB):
                        time.sleep(0.002)
    if kind == "dirty":
        buf = bytes([4]) * MB
        n = 0
        while True:
            fn = os.path.join(workdir, "dirty_%d.bin" % (n % 2))
            with open(fn, "wb") as f:                                                  # never fsynced: the pages are dirty until the kernel decides to write them
                for _ in range(3 * limit // MB):
                    f.write(buf)
            n += 1
            os.unlink(fn)


# ---- the scenario runner -----------------------------------------------------------------------------------------------------------------------------------------------
def old_rule_first(trace, limit_frac=0.9):
    for r in trace:
        if r["s"]["max"] and r["s"]["current"] > limit_frac * r["s"]["max"]:
            return r["t"]
    return None


def replay(trace, phase, every):
    """The new rule on one sample in `every`, starting at `phase`: -> (time of the first trip, reason) or (None, None). Time is the trace's own seconds."""
    j = RC.MemoryJudge()
    for r in trace[phase::every]:
        v = j.observe(r["s"], r["t"])
        if v:
            return r["t"], v["reason"]
    return None, None


def run_scenario(name, limit, duration, work):
    spec = SCENARIOS[name]
    duration = spec.get("duration", duration)
    wd = os.path.join(work, name)
    os.makedirs(wd, exist_ok=True)
    g = MC.Group("memrule_" + name, limit=limit)
    if not g.ok:
        raise SystemExit("cannot make a cgroup v1 memory group here")
    try:
        MC.cg_write(os.path.join(g.mem, "memory.failcnt"), 0)
    except OSError:
        pass
    procs = []
    for kind, wspec in spec["workers"]:
        procs.append(subprocess.Popen([sys.executable, os.path.abspath(__file__), "--worker", kind, json.dumps(wspec), str(limit), wd, g.mem], preexec_fn=g.enter, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    live = RC.MemoryJudge()
    trace, live_trip, t0 = [], None, time.time()
    killed_at = None
    try:
        while time.time() - t0 < duration:
            t = time.time() - t0
            s = RC.read_cgroup_memory(g.mem)
            est = RC.memory_estimates(s)
            trace.append({"t": round(t, 2), "s": s, "reclaimable": est["reclaimable"], "pinned": est["pinned"]})
            v = live.observe(s, t)
            if v and live_trip is None:
                live_trip = {"t": round(t, 2), "reason": v["reason"], "detail": v["detail"]}
            if killed_at is None and any(p.poll() == -signal.SIGKILL for p in procs):
                killed_at = round(t, 2)
            if live_trip and killed_at is None and duration > 60 and t > live_trip["t"] + 8 and name not in ("anon_fast_growth",):
                pass                                                                 # keep sampling: the point is what happens AFTER the trip (nothing stops the workload here)
            time.sleep(max(0.0, 1.0 - ((time.time() - t0) - t)))
    finally:
        exits = []
        for p in procs:
            if p.poll() is None:
                p.kill()
            exits.append(p.wait())
        time.sleep(0.5)
        final_oc = (MC.cg_read(os.path.join(g.mem, "memory.oom_control")) or "").replace("\n", "; ")
        peak = g.peak()
        g.remove()
        shutil.rmtree(wd, ignore_errors=True)
    old_t = old_rule_first(trace)
    phases = [replay(trace, ph, 10) for ph in range(10)]
    trips = [p for p in phases if p[0] is not None]
    last = trace[-1]["s"]
    peak_dirty = max(r["s"]["file_dirty"] + r["s"]["file_writeback"] for r in trace)
    out = {
        "scenario": name, "limit_bytes": limit, "line_bytes": int(0.9 * limit), "duration_s": round(trace[-1]["t"], 1), "samples": len(trace), "exit_codes": exits,
        "killed_by_kernel_at_s": killed_at, "oom_kill_count_end": last["events"]["oom_kill"], "failcnt_end": last["events"]["max"], "oom_control_end": final_oc, "peak_usage_bytes": peak,
        "peak_current": max(r["s"]["current"] for r in trace), "peak_pinned": max(r["pinned"] for r in trace), "peak_anon": max(r["s"]["anon"] for r in trace),
        "peak_file": max(r["s"]["file"] for r in trace), "peak_dirty_plus_writeback": peak_dirty, "peak_kernel": max(r["s"]["kernel"] for r in trace),
        "peak_some_avg10": max(((r["s"].get("pressure") or {}).get("some") or {}).get("avg10", 0.0) for r in trace),
        "peak_full_avg10": max(((r["s"].get("pressure") or {}).get("full") or {}).get("avg10", 0.0) for r in trace),
        "old_rule_first_trip_s": old_t,
        "new_rule_live_1hz": live_trip,
        "new_rule_at_10s_cadence": {"phases_tripped": len(trips), "first_trip_s": [p[0] for p in phases], "reasons": sorted({p[1] for p in trips if p[1]})},
        "expect": spec["expect"],
    }
    checks = []
    exp = spec["expect"]
    if exp:
        checks.append(("old rule %s" % ("trips" if exp["old"] else "does not trip"), (old_t is not None) == exp["old"]))
        checks.append(("new rule (live, 1 Hz) %s" % ("trips" if exp["new"] else "does not trip"), (live_trip is not None) == exp["new"]))
        checks.append(("new rule (10 s cadence, all ten phases) %s" % ("trips" if exp["new"] else "does not trip"), len(trips) == (10 if exp["new"] else 0)))
        if exp.get("reason"):
            checks.append(("new rule's reason is %s" % exp["reason"], bool(live_trip) and live_trip["reason"] == exp["reason"] and out["new_rule_at_10s_cadence"]["reasons"] == [exp["reason"]]))
        if exp.get("before_kill"):
            # the harness lets the leak run on (nothing stops the workload), so the kernel does kill it eventually: what matters is that the trip came FIRST, at every sampling phase
            firsts = [t for t in out["new_rule_at_10s_cadence"]["first_trip_s"] if t is not None]
            checks.append(("new rule trips before the kernel's kill (live %s s, 10 s cadence %s..%s s, kill %s s)" % (live_trip and live_trip["t"], min(firsts, default=None), max(firsts, default=None), killed_at),
                           live_trip is not None and killed_at is not None and live_trip["t"] < killed_at and len(firsts) == 10 and max(firsts) < killed_at))
        if exp.get("reason") == "oom_event":
            checks.append(("the kernel really killed the workload (oom_kill rose, exit -9)", out["oom_kill_count_end"] >= 1 and -signal.SIGKILL in exits))
        if not exp["new"]:
            checks.append(("nothing was killed", out["oom_kill_count_end"] == 0 and killed_at is None))
    else:
        # recorded scenarios: the verdict has to follow the numbers (pinned above the line on enough consecutive seconds <=> a trip)
        run, best = 0, 0
        for r in trace:
            run = run + 1 if r["pinned"] > 0.9 * limit else 0
            best = max(best, run)
        checks.append(("the live verdict follows the data (longest run over the line %d s, trip %s)" % (best, "yes" if live_trip else "no"), (best >= 60) == (live_trip is not None and live_trip["reason"] == "sustained_unreclaimable") or (live_trip is not None and live_trip["reason"] == "oom_event")))
        checks.append(("dirty and writeback pages were counted as pinned", all(r["pinned"] >= min(r["s"]["current"], r["s"]["file_dirty"] + r["s"]["file_writeback"]) for r in trace)))
    out["checks"] = [{"check": c, "ok": bool(ok)} for c, ok in checks]
    out["ok"] = all(ok for _, ok in checks)
    return out, trace


def main(argv=None):
    if argv is None and len(sys.argv) > 1 and sys.argv[1] == "--worker":
        a = sys.argv
        worker(a[2], json.loads(a[3]), int(a[4]), a[5], a[6])
        return 0
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit-mb", type=int, default=256)
    ap.add_argument("--duration", type=int, default=150)
    ap.add_argument("--scenarios", default=",".join(SCENARIOS))
    ap.add_argument("--note", default=None, help="stored with each scenario of this run (for a re-run: why)")
    ap.add_argument("--work", default=None, help="scratch folder for the workloads' files (default: <out>/_work, removed at the end)")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    work = a.work or os.path.join(a.out, "_work")
    limit = a.limit_mb * MB
    report = {"host": {"kernel": os.uname().release, "cgroup": "v1 (memory controller), no systemd"}, "limit_bytes": limit, "rule": {"frac": RC.MEM_PINNED_FRAC, "sustain_s": RC.MEM_SUSTAIN_S, "min_samples": RC.MEM_MIN_SAMPLES},
              "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "scenarios": []}
    prior = os.path.join(a.out, "report.json")
    kept = {}
    if os.path.exists(prior):                                                          # a re-run of some scenarios keeps the others' results (and says which were re-run)
        with open(prior) as f:
            old_report = json.load(f)
        kept = {s["scenario"]: s for s in old_report.get("scenarios", [])}
        report["started_at"] = old_report.get("started_at", report["started_at"])
    for name in a.scenarios.split(","):
        print("== %s" % name, flush=True)
        res, trace = run_scenario(name, limit, a.duration, work)
        if a.note:
            res["note"] = a.note
        res["run_started_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        kept[name] = res
        with open(os.path.join(a.out, "trace_%s.json" % name), "w") as f:
            json.dump([{"t": r["t"], "current": r["s"]["current"], "pinned": r["pinned"], "reclaimable": r["reclaimable"], "anon": r["s"]["anon"], "file": r["s"]["file"], "dirty": r["s"]["file_dirty"],
                        "writeback": r["s"]["file_writeback"], "mapped": r["s"]["file_mapped"], "shmem": r["s"]["shmem"], "kernel": r["s"]["kernel"], "events": r["s"]["events"],
                        "some_avg10": ((r["s"].get("pressure") or {}).get("some") or {}).get("avg10"), "full_avg10": ((r["s"].get("pressure") or {}).get("full") or {}).get("avg10")} for r in trace], f)
        print("   old first trip %s | new live %s | 10 s cadence %d/10 | killed %s | peak current %.0f%% pinned %.0f%% dirty+wb %.0f%% | %s"
              % (res["old_rule_first_trip_s"], (res["new_rule_live_1hz"] or {}).get("reason"), res["new_rule_at_10s_cadence"]["phases_tripped"], res["killed_by_kernel_at_s"],
                 100 * res["peak_current"] / limit, 100 * res["peak_pinned"] / limit, 100 * res["peak_dirty_plus_writeback"] / limit, "OK" if res["ok"] else "CHECK FAILED"), flush=True)
        for c in res["checks"]:
            print("     %s %s" % ("ok  " if c["ok"] else "FAIL", c["check"]), flush=True)
    report["scenarios"] = [kept[n] for n in SCENARIOS if n in kept]
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    report["ok"] = all(s["ok"] for s in report["scenarios"])
    with open(os.path.join(a.out, "report.json"), "w") as f:
        json.dump(report, f, indent=1, sort_keys=True)
    shutil.rmtree(work, ignore_errors=True)
    print("RESULT: %s" % ("all scenarios behaved as required" if report["ok"] else "SOME CHECK FAILED"))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
