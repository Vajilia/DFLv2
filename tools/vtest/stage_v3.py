"""V3: the monitor dies or hangs. The daemon must pause safely by itself.

The daemon trips itself (kind monitor_silent, source daemon) when the monitor's heartbeat file is older than the guard's stale limit (120 s) and pauses. Four ways to lose the monitor:
  V3a  kill -9: systemd restarts it (RestartSec=10) well inside the stale limit, so NOTHING must trip;
  V3b  stopped and left stopped: the daemon must trip and pause;
  V3c  hung (SIGSTOP; the process exists but makes no progress): same outcome as V3b;
  V3d  killed by the kernel for its own memory, repeatedly, until systemd's start limit leaves the unit failed: one OOM must not trip; the failed unit must.
  V3e  the NEGATIVE CONTROL for the daemon's lifecycle hooks: a daemon stop that is not an OOM (kill -9, restart, stop then start) must record nothing (no latch, no incident line, no PAUSE; the clock goes on)
       while the run marker is closed by the stop hook and reopened by the start gate. V3a-V3d also check that the monitor's failures left the daemon's own run untouched (same process, same marker) and
       wrote no oom_kill / unaccounted_stop line.
"Safely" means: a valid latch is written atomically, the broadcast clock stops, no public file changes afterwards, nothing is deleted, and the documented release + resume works.
"""
import json

import evaluate as E
import lifecycle as LC
from common import GUARD, GUARD_STALE_S, UNIT

STAGE = "V3"
PLAN = [("V3a.kill9", "systemctl kill --signal=SIGKILL the monitor; watch 150 s: restarted, heartbeat gap under the stale limit, no latch, clock still advancing"),
        ("V3b.stop", "systemctl stop the monitor and leave it; wait for the daemon's own trip; check the pause; start the monitor; release; resume"),
        ("V3c.hang", "SIGSTOP the monitor; same as V3b; SIGCONT afterwards; check the latch persisted"),
        ("V3d.monitor_oom", "loader in the monitor's cgroup past its MemoryMax, repeatedly, until the start limit fails the unit; the daemon must trip; recover the documented way"),
        ("V3.monitor_failures_summary", "after V3a-V3d: the daemon's run (main pid, run marker) is untouched and no oom_kill / unaccounted_stop line was written"),
        ("V3e.daemon_kill9", "negative control: kill -9 the daemon; no latch, no incident line, not paused, clock advances; run marker closed and reopened"),
        ("V3e.daemon_restart", "negative control: systemctl restart the daemon; same checks"),
        ("V3e.daemon_stop_start", "negative control: stop the daemon (the marker must be closed while it is stopped), start it; same checks")]
DEFAULTS = {"qualifying": {"watch_s": 150, "trip_wait_s": 240, "oom_rate": 100e6, "oom_iterations": 7},
            "smoke": {"watch_s": 60, "trip_wait_s": 240, "oom_rate": 100e6, "oom_iterations": 7}}


def evaluate_v3a(pre, post, peak_hb_age, latch, paused, moved, observed_s):
    restarted = post.get("NRestarts", 0) == pre.get("NRestarts", 0) + 1 and post.get("ActiveState") == "active"
    return [("V3a.systemd_restarted_the_monitor", "pass" if restarted else "fail", "NRestarts %s -> %s, ActiveState=%s" % (pre.get("NRestarts"), post.get("NRestarts"), post.get("ActiveState")), {"pre": pre, "post": post}),
            ("V3a.heartbeat_gap_below_stale_limit", "pass" if peak_hb_age is not None and peak_hb_age < 0.85 * GUARD_STALE_S else "fail", "largest heartbeat age seen %.0f s (stale limit %.0f s)" % (peak_hb_age if peak_hb_age is not None else -1, GUARD_STALE_S), {"peak_age_s": peak_hb_age}),
            ("V3a.nothing_tripped", "pass" if latch is None and not paused and moved else "fail", "latch %s, PAUSE file %s, clock advanced %s over %.0f s" % (latch, paused, moved, observed_s), {"latch": latch})]


def evaluate_silent(name, latch, hb_final_mtime, paused, pos_a, pos_b, inv_a, inv_b, recovery, stale=GUARD_STALE_S):
    rows = []
    if latch is None or latch.get("unreadable"):
        return [(name + ".daemon_tripped", "fail", "no valid latch appeared (latch=%s)" % latch, None)]
    delay = latch.get("at", 0) - hb_final_mtime
    ok = latch.get("kind") == "monitor_silent" and latch.get("source") == "daemon" and stale - 2 <= delay <= stale + 20
    rows.append((name + ".daemon_tripped", "pass" if ok else "fail", "latch kind=%s source=%s, %.0f s after the last heartbeat (stale limit %.0f s)" % (latch.get("kind"), latch.get("source"), delay, stale), {"latch": latch, "delay_s": delay}))
    froze = paused and not E.moved(pos_a, pos_b, 1.5)
    rows.append((name + ".paused_safely", "pass" if froze and inv_a == inv_b else "fail", "PAUSE file %s; position %s -> %s; immutable public files unchanged: %s (%d files)" % (paused, pos_a, pos_b, inv_a == inv_b, len(inv_a)), None))
    rows.append((name + ".recoverable_the_documented_way", "pass" if recovery["release_rc"] == 0 and recovery["resume_rc"] == 0 and recovery["clock_advances"] else "fail",
                 "release rc %s, resume rc %s, clock advances afterwards: %s" % (recovery["release_rc"], recovery["resume_rc"], recovery["clock_advances"]), recovery))
    return rows


def evaluate_v3d(iters, latch_before_last, final, latch, hb_final_mtime, paused, pos_a, pos_b, inv_a, inv_b, recovery):
    rows = []
    first = iters[0] if iters else {}
    rows.append(("V3d.one_oom_restarts_without_a_trip", "pass" if first.get("restarted") and not first.get("latch") else "fail",
                 "after the first OOM kill: restarted=%s, latch=%s, restart took %.0f s" % (first.get("restarted"), first.get("latch"), first.get("restart_s") or -1), {"first": first}))
    failed = final.get("ActiveState") == "failed"
    rows.append(("V3d.start_limit_left_the_unit_failed", "pass" if failed and len(iters) <= 6 else "fail", "after %d OOM kills the monitor unit is %s (StartLimitBurst=5 in 3600 s)" % (len(iters), final.get("ActiveState")), {"iterations": len(iters), "final": final}))
    rows += evaluate_silent("V3d", latch, hb_final_mtime, paused, pos_a, pos_b, inv_a, inv_b, recovery)
    return rows


def _daemon_case(ctx, case, act):
    """One non-OOM stop of the daemon, from a clean baseline (no latch). `act` does the stop and the start and returns the marker seen while stopped (or None). -> rows."""
    env = ctx.env
    ctx.ensure_baseline("vtest: baseline before V3e.%s" % case)
    pre, mk0, trips0 = ctx.show("daemon"), ctx.run_marker(), ctx.trips()
    ctx.mark("V3e.act", case=case, pid=pre.get("MainPID"))
    mk_stopped = act()
    ctx.wait(lambda: ctx.active("daemon") and ctx.show("daemon").get("MainPID") not in (0, pre.get("MainPID")), 90)
    env.sleep(20)
    p0 = ctx.position()
    env.sleep(15)
    latch, _ = ctx.latch()
    return LC.evaluate_daemon_negative_control(case, True, latch, ctx.paused(), LC.new_lines(trips0, ctx.trips()), mk0, ctx.run_marker(), mk_stopped, E.moved(p0, ctx.position(), 5.0), env.boot_id())


def _recover(ctx, why):
    """Documented recovery: monitor back, fresh heartbeat, release, resume, then see the clock move."""
    ctx.env.sc("reset-failed", UNIT["monitor"])
    ctx.env.sc("start", UNIT["monitor"])
    ctx.wait(lambda: (ctx.heartbeat_age() or 999) < 20, 90)
    r1, r2, out = ctx.release_and_resume(why)
    p0 = ctx.position()
    ctx.env.sleep(15)
    return {"release_rc": r1, "resume_rc": r2, "clock_advances": E.moved(p0, ctx.position(), 5.0), "output": out}


def _silent(ctx, name, silence, restore=None):
    """Silence the monitor, wait for the daemon's trip, observe the pause. -> rows (and the recovery already run)."""
    rec = ctx.rec
    ctx.ensure_baseline("vtest: reset before %s" % name)
    silence()
    ctx.mark(name + ".silenced")
    lat, _ = ctx.wait(lambda: ctx.latch()[0], ctx.cfg["trip_wait_s"], every=2.0)
    latch, _ = ctx.latch()
    hb = _mtime(GUARD + "/heartbeat")
    pos_a, inv_a = ctx.position(), ctx.public_inventory()
    ctx.env.sleep(20)
    paused, pos_b, inv_b = ctx.paused(), ctx.position(), ctx.public_inventory()
    if restore:
        restore()
    recovery = _recover(ctx, "vtest %s: the planned monitor failure was observed, recovering the documented way" % name)
    return evaluate_silent(name, latch, hb, paused, pos_a, pos_b, inv_a, inv_b, recovery)


def _mtime(p):
    import os
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0.0


def run(ctx):
    cfg, rec, env = ctx.cfg, ctx.rec, ctx.env
    # ---- a: kill -9
    ctx.ensure_baseline()
    daemon0, marker0, trips_v3 = ctx.show("daemon"), ctx.run_marker(), ctx.trips()
    pre, pos0, t0 = ctx.show("monitor"), ctx.position(), ctx.now()
    env.sc("kill", UNIT["monitor"], "--signal=SIGKILL", "--kill-whom=main")
    ctx.mark("V3a.kill9", pid=pre.get("MainPID"))
    peak = 0.0
    while ctx.now() - t0 < cfg["watch_s"]:
        a = ctx.heartbeat_age()
        peak = max(peak, a if a is not None else 999)
        env.sleep(1)
    latch, _ = ctx.latch()
    rec.add_all(evaluate_v3a(pre, ctx.show("monitor"), peak, latch, ctx.paused(), E.moved(pos0, ctx.position(), 5.0), ctx.now() - t0))
    rec.stop_if_failed()
    # ---- b: stopped
    ctx.mark("V3b.stop")
    rec.add_all(_silent(ctx, "V3b", lambda: env.sc("stop", UNIT["monitor"])))
    rec.stop_if_failed()
    # ---- c: hung
    ctx.mark("V3c.hang")
    rec.add_all(_silent(ctx, "V3c", lambda: env.sc("kill", UNIT["monitor"], "--signal=SIGSTOP", "--kill-whom=main"), restore=lambda: env.sc("kill", UNIT["monitor"], "--signal=SIGCONT", "--kill-whom=main")))
    rec.stop_if_failed()
    # ---- d: the monitor is OOM-killed until systemd gives up
    ctx.mark("V3d.monitor_oom")
    ctx.ensure_baseline("vtest: reset before V3d")
    mx = ctx.limit_of("monitor")
    iters, t_start = [], ctx.now()
    for i in range(cfg["oom_iterations"]):
        pre = ctx.show("monitor")
        if pre.get("ActiveState") != "active":
            break
        t_i = ctx.now()
        ld = ctx.loader("anon", "monitor", "v3d_anon_%d" % i, target_bytes=mx + (128 << 20), rate_bps=cfg["oom_rate"])
        ctx.wait(lambda: ctx.show("monitor").get("NRestarts", 0) > pre.get("NRestarts", 0) or ctx.show("monitor").get("ActiveState") in ("failed", "inactive"), 90, every=0.5)
        ld.stop()
        sh = ctx.show("monitor")
        back, secs = ctx.wait(lambda: ctx.show("monitor").get("ActiveState") in ("active", "failed"), 60, every=1.0)
        sh2 = ctx.show("monitor")
        lat, _ = ctx.latch()
        iters.append({"i": i, "after_kill": sh, "restarted": sh2.get("ActiveState") == "active" and sh2.get("NRestarts", 0) > pre.get("NRestarts", 0), "restart_s": secs, "latch": bool(lat), "state": sh2.get("ActiveState")})
        ctx.mark("V3d.iteration", **iters[-1])
        if sh2.get("ActiveState") == "failed":
            break
        env.sleep(3)
    final = ctx.show("monitor")
    lat, _ = ctx.wait(lambda: ctx.latch()[0], cfg["trip_wait_s"], every=2.0)
    latch, _ = ctx.latch()
    hb = _mtime(GUARD + "/heartbeat")
    pos_a, inv_a = ctx.position(), ctx.public_inventory()
    env.sleep(20)
    paused, pos_b, inv_b = ctx.paused(), ctx.position(), ctx.public_inventory()
    recovery = _recover(ctx, "vtest V3d: the monitor was OOM-killed until systemd gave up; recovering the documented way")
    rec.add_all(evaluate_v3d(iters, None, final, latch, hb, paused, pos_a, pos_b, inv_a, inv_b, recovery))
    rec.stop_if_failed()
    ctx.mark("V3.monitor_failures_summary")
    rec.add_all(LC.evaluate_monitor_failures_left_daemon_alone(LC.new_lines(trips_v3, ctx.trips()), daemon0, ctx.show("daemon"), marker0, ctx.run_marker()))
    rec.stop_if_failed()
    # ---- e: the negative control for the daemon's lifecycle hooks
    def kill9():
        env.sc("kill", UNIT["daemon"], "--signal=SIGKILL", "--kill-whom=main")

    def restart():
        env.sc("restart", UNIT["daemon"])

    def stop_start():
        env.sc("stop", UNIT["daemon"])
        ctx.wait(lambda: not ctx.active("daemon"), 60)
        mk = ctx.run_marker()
        env.sc("start", UNIT["daemon"])
        return mk
    ctx.mark("V3e.daemon_kill9")
    rec.add_all(_daemon_case(ctx, "daemon_kill9", kill9))
    rec.stop_if_failed()
    ctx.mark("V3e.daemon_restart")
    rec.add_all(_daemon_case(ctx, "daemon_restart", restart))
    rec.stop_if_failed()
    ctx.mark("V3e.daemon_stop_start")
    rec.add_all(_daemon_case(ctx, "daemon_stop_start", stop_start))
