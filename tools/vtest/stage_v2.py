"""V2: sustained anonymous-memory pressure, then an OOM. What the monitor does, and what systemd actually does.

The load is a loader process moved INTO the daemon's own cgroup, so the daemon's real MemoryMax, OOMPolicy and Restart settings apply unchanged.
Limit of the method (stated, not hidden): the kernel's victim is the loader (the largest process in the cgroup), not the daemon. systemd reacts to the cgroup's oom_kill event, which
is the same event whoever the victim is; the case where the daemon itself is the victim is not exercised here.

V2a  plateau at 93 % of MemoryMax: the monitor must trip on its sustained rule, with evidence, and the daemon must pause, before any kernel kill.
V2b  growth past the limit: the kernel kills; the whole unit must stop, restart as configured, leave no survivor and no damaged public file. The monitor's response is RECORDED:
       outcome A  a latch (the monitor's oom_event / the daemon's silence trip), the broadcast stays paused for a person;
       outcome B  no latch: systemd restarts the daemon quickly enough that no lease lapses and the broadcast resumes by itself.
     Both are acceptable to this stage; which one happened goes in the result and is for the owner to read (B means a person is not involved).
"""
import json
import os

import evaluate as E
from common import GUARD, MEM_FRAC, MEM_SUSTAIN_S, MON_EVERY_S, UNIT

STAGE = "V2"
PLAN = [("V2a.baseline", "units up, no latch; idle samples of the daemon's cgroup"),
        ("V2a.plateau", "anonymous loader in the daemon's cgroup to 93% of MemoryMax (5 MB/s), held"),
        ("V2a.trip", "wait for the monitor's latch; then observe the daemon's pause for 15 s"),
        ("V2a.release_load", "stop the loader; memory must fall"),
        ("V2a.evaluate", "judge V2a"),
        ("V2b.reset", "clear the latch the documented way (release, resume); record pids, restarts, positions, public files"),
        ("V2b.overrun", "anonymous loader at 25 MB/s to 3 x MemoryMax in the daemon's cgroup"),
        ("V2b.timeline", "record the unit, the loader, the latch every second for up to 150 s after the kill"),
        ("V2b.evaluate", "judge V2b")]

DEFAULTS = {"qualifying": {"baseline_s": 30, "plateau_rate": 5e6, "trip_wait_s": 300, "overrun_rate": 25e6, "timeline_s": 150},
            "smoke": {"baseline_s": 10, "plateau_rate": 10e6, "trip_wait_s": 240, "overrun_rate": 40e6, "timeline_s": 90}}


def evaluate_v2a(recs, latch, evidence, pause, loader, cfg):
    rows = []
    t_cross = E.crossing_time(recs, MEM_FRAC)
    rows.append(("V2a.line_crossed", "pass" if t_cross else "invalid", "the estimated footprint crossed %.0f%% of the limit%s" % (100 * MEM_FRAC, "" if t_cross else " NEVER: the load did not reach the line"), {"t_cross": t_cross}))
    if latch is None:
        rows.append(("V2a.tripped", "fail", "no latch appeared", None))
        return rows
    delay = (latch.get("at", 0) - t_cross) if t_cross else None
    lo, hi = MEM_SUSTAIN_S - 2, MEM_SUSTAIN_S + 2 * MON_EVERY_S + 10
    ok = latch.get("kind") == "memory_high" and latch.get("source") == "monitor" and delay is not None and lo <= delay <= hi
    rows.append(("V2a.tripped_on_the_sustained_rule", "pass" if ok else "fail",
                 "latch kind=%s source=%s, %.0f s after the line was crossed (expected %.0f to %.0f s: the 60 s window plus the monitor's 10 s cadence)" % (latch.get("kind"), latch.get("source"), delay if delay is not None else -1, lo, hi),
                 {"latch": latch, "delay_s": delay}))
    ev = (evidence or {}).get("evidence") or {}
    need = ("current", "max", "anon", "file", "file_dirty", "file_writeback", "file_mapped", "shmem", "kernel", "events", "pressure", "reclaimable_estimate", "pinned_estimate", "pinned_fraction", "rule", "recent")
    missing = [k for k in need if k not in ev]
    good = (not missing and ev.get("reason") == "sustained_unreclaimable" and ev.get("anon", 0) >= 0.5 * max(1, ev.get("current", 1)) and ev.get("pinned_fraction", 0) > MEM_FRAC and len(ev.get("recent", [])) >= 3)
    rows.append(("V2a.evidence_breakdown", "pass" if good else "fail",
                 "evidence has the breakdown, reason=%s, anon %.0f%% of current, pinned fraction %s, %d recent samples%s" % (ev.get("reason"), 100.0 * ev.get("anon", 0) / max(1, ev.get("current", 1)), ev.get("pinned_fraction"), len(ev.get("recent", [])), (" MISSING: %s" % missing) if missing else ""),
                 {"reason": ev.get("reason"), "missing": missing}))
    froze = pause["pause_file"] and not E.moved(pause["pos_a"], pause["pos_b"], 1.5)
    rows.append(("V2a.daemon_paused", "pass" if froze else "fail", "PAUSE file present: %s; broadcast position %s -> %s over %.0f s" % (pause["pause_file"], pause["pos_a"], pause["pos_b"], pause["dt"]), pause))
    kills = E.event_delta(recs, "oom_kill")
    rows.append(("V2a.tripped_before_any_kill", "pass" if kills == 0 and loader["alive_at_trip"] else "fail", "oom_kill increase %d; loader alive at the trip: %s" % (kills, loader["alive_at_trip"]), {"oom_kill_delta": kills}))
    return rows


def evaluate_v2b(kills, journal, pre, post, tl, latch, inv_before, inv_after, partial, procs_new, mon_before, mon_after, old_pids_alive, mem_end, mx, pos_before, pos_after, elapsed, cfg):
    rows = []
    jr = journal.lower()
    saw = ("oom-kill" in jr) or ("oom killer" in jr) or ("out of memory" in jr)
    rows.append(("V2b.kernel_killed", "pass" if (kills >= 1 or saw) else "invalid", "memory.events oom_kill rose by %d; journal mentions the OOM killer: %s (a 1 Hz sampler can miss an event whose cgroup is removed within a second, so the journal counts too)" % (kills, saw), {"oom_kill_delta": kills}))
    rows.append(("V2b.systemd_recognised_the_oom", "pass" if saw else "fail", "the unit's journal records an oom-kill / OOM-killer message" if saw else "the journal has no OOM message for the unit", {"journal_tail": journal[-600:]}))
    restarted = post.get("ActiveState") == "active" and post.get("NRestarts", 0) > pre.get("NRestarts", 0) and post.get("MainPID") != pre.get("MainPID")
    rows.append(("V2b.unit_recovered_as_configured", "pass" if restarted else "fail", "after the kill: ActiveState=%s, NRestarts %s -> %s, MainPID %s -> %s (Restart=on-failure, RestartSec=5)" % (post.get("ActiveState"), pre.get("NRestarts"), post.get("NRestarts"), pre.get("MainPID"), post.get("MainPID")), {"pre": pre, "post": post}))
    stray = [p for p in procs_new if "broadcast_run.py" not in p]
    rows.append(("V2b.no_survivors", "pass" if not old_pids_alive and not stray else "fail", "old loader/daemon processes alive: %s; processes in the new cgroup that are not the daemon: %s" % (old_pids_alive or "none", stray or "none"), {"old_alive": old_pids_alive, "stray": stray}))
    rows.append(("V2b.monitor_stayed_up", "pass" if mon_after.get("ActiveState") == "active" and mon_after.get("NRestarts") == mon_before.get("NRestarts") else "fail", "monitor %s, restarts %s -> %s" % (mon_after.get("ActiveState"), mon_before.get("NRestarts"), mon_after.get("NRestarts")), None))
    if latch:
        rows.append(("V2b.monitor_response", "pass", "OUTCOME A: a latch was written: kind=%s source=%s; the broadcast waits for a person" % (latch.get("kind"), latch.get("source")), {"outcome": "A", "latch": latch}))
    else:
        rows.append(("V2b.monitor_response", "info", "OUTCOME B (OWNER REVIEW): no latch. systemd restarted the daemon fast enough that no trip fired and the broadcast resumed unattended. This is what Restart=on-failure is for, but it means an OOM does not by itself stop the broadcast.", {"outcome": "B"}))
    damaged = {p: (inv_before[p], inv_after.get(p)) for p in inv_before if inv_after.get(p) != inv_before[p]}
    rows.append(("V2b.public_files_intact", "pass" if not damaged and not partial else "fail", "%d immutable public files compared: %d changed or missing, %d partial files" % (len(inv_before), len(damaged), len(partial)), {"changed": dict(list(damaged.items())[:5]), "partial": partial[:5]}))
    ok = pos_before is not None and pos_after is not None and pos_after >= pos_before - 1 and pos_after <= pos_before + elapsed + 10
    rows.append(("V2b.clock_continuity", "pass" if ok else "fail", "broadcast position %s -> %s over %.0f s of wall time (must not go back, must not jump ahead)" % (pos_before, pos_after, elapsed), None))
    rows.append(("V2b.memory_recovered", "pass" if mem_end is not None and mem_end < 0.6 * mx else "fail", "memory.current at the end is %s of the limit (should be well under 60%%)" % ("%.0f%%" % (100.0 * mem_end / mx) if mem_end is not None else "unreadable"), None))
    return rows


def run(ctx):
    import sampler as SM
    cfg, rec, env = ctx.cfg, ctx.rec, ctx.env
    # ---------------- V2a
    ctx.mark("V2a.baseline")
    ctx.ensure_baseline()
    smp = ctx.sampler("daemon", "v2a_cgroup")
    env.sleep(cfg["baseline_s"])
    base = smp.records()
    mx = ctx.limit_of("daemon")
    base_pin = E.median([r["pinned"] for r in E.readable(base)]) or 0
    target = int(0.93 * mx - base_pin)
    t0 = ctx.now()
    ld = ctx.loader("anon", "daemon", "v2a_anon", target_bytes=target, rate_bps=cfg["plateau_rate"])
    ctx.mark("V2a.plateau", target=target, loader_pid=ld.pid())
    deadline = t0 + target / cfg["plateau_rate"] + cfg["trip_wait_s"]

    def trip():
        lat, _ = ctx.latch()
        return lat or (None if ld.alive() and E.event_delta(E.readable(smp.records())[-120:], "oom_kill") == 0 else "STOP")
    ctx.mark("V2a.trip")
    lat, _ = ctx.wait(trip, deadline - t0, every=2.0)
    alive_at_trip = ld.alive()
    latch, sha = (ctx.latch() if lat != "STOP" else (None, None))
    pos_a, ta = ctx.position(), ctx.now()
    env.sleep(15)
    pause = {"pause_file": ctx.paused(), "pos_a": pos_a, "pos_b": ctx.position(), "dt": ctx.now() - ta}
    evid = None
    for d in env.listdir(GUARD + "/evidence"):
        if d.startswith("trip-") and d.endswith("memory_high") and env.exists("%s/evidence/%s/evidence.json" % (GUARD, d)):
            evid = json.loads(env.read("%s/evidence/%s/evidence.json" % (GUARD, d)))
    ld.stop()
    ctx.mark("V2a.release_load")
    env.sleep(20)
    smp.stop()
    recs = [r for r in smp.records() if r["t"] >= t0]
    ctx.mark("V2a.evaluate")
    rows = evaluate_v2a(recs, latch, evid, pause, {"alive_at_trip": alive_at_trip}, cfg)
    last = E.readable(recs)[-1]["sample"]["current"] if E.readable(recs) else None
    rows.append(("V2a.latch_sha256", "info", "sha256 of TRIPPED after V2a: %s (V4 starts from the latch kept)" % sha, {"sha256": sha}, False))
    rec.add_all(rows)
    rec.stop_if_failed()
    # ---------------- V2b
    ctx.mark("V2b.reset")
    ctx.ensure_baseline("vtest: reset after the V2a plateau trip, before the OOM scenario")
    smp = ctx.sampler("daemon", "v2b_cgroup")
    env.sleep(cfg["baseline_s"])
    pre, mon_before = ctx.show("daemon"), ctx.show("monitor")
    inv_before, pos_before, t0 = ctx.public_inventory(), ctx.position(), ctx.now()
    ld = ctx.loader("anon", "daemon", "v2b_anon", target_bytes=3 * mx, rate_bps=cfg["overrun_rate"])
    old_loader_pid = ld.pid()
    ctx.mark("V2b.overrun", loader_pid=old_loader_pid, daemon_pid=pre.get("MainPID"))

    def killed():
        sh = ctx.show("daemon")
        return (sh.get("NRestarts", 0) > pre.get("NRestarts", 0) or sh.get("ActiveState") != "active" or E.event_delta(E.readable(smp.records())[-30:], "oom_kill") > 0) or (not ld.alive())
    v, _ = ctx.wait(killed, 3 * mx / cfg["overrun_rate"] + 120, every=0.5)
    t_kill = ctx.now()
    ctx.mark("V2b.killed_or_timeout", seen=bool(v))
    ctx.mark("V2b.timeline")
    tl = []
    while ctx.now() - t_kill < cfg["timeline_s"]:
        sh = ctx.show("daemon")
        lat, _ = ctx.latch()
        tl.append({"t": round(ctx.now(), 1), "ActiveState": sh.get("ActiveState"), "MainPID": sh.get("MainPID"), "NRestarts": sh.get("NRestarts"), "Result": sh.get("Result"), "loader_alive": ld.alive(), "latch": bool(lat)})
        ctx.mark("V2b.tick", **tl[-1])
        if sh.get("ActiveState") == "active" and sh.get("MainPID") != pre.get("MainPID") and sh.get("NRestarts", 0) > pre.get("NRestarts", 0) and ctx.now() - t_kill > 45:
            break
        env.sleep(1)
    if ld.alive():
        ld.stop()
    smp.stop()
    post, mon_after = ctx.show("daemon"), ctx.show("monitor")
    latch, _ = ctx.latch()
    recs = [r for r in smp.records() if r["t"] >= t0]
    procs_new = []
    for pid in (env.read(ctx.cg("daemon") + "/cgroup.procs") or "").split():
        procs_new.append((env.read("/proc/%s/cmdline" % pid) or "").replace("\0", " "))
    old_alive = [p for p in (old_loader_pid, pre.get("MainPID")) if p and p != post.get("MainPID") and env.exists("/proc/%s" % p)]
    mem_t = (env.read(ctx.cg("daemon") + "/memory.current") or "").strip()
    ctx.mark("V2b.evaluate")
    rows = evaluate_v2b(E.event_delta(recs, "oom_kill"), ctx.journal("daemon", t0 - 2), pre, post, tl, latch, inv_before, ctx.public_inventory(), ctx.partial_files(), procs_new, mon_before, mon_after, old_alive,
                        int(mem_t) if mem_t.isdigit() else None, mx, pos_before, ctx.position(), ctx.now() - t0, cfg)
    rec.add_all(rows)
    return rows
