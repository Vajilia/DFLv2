"""V1: real cgroup-v2 cache growth. A sustained, healthy workload fills the daemon's cgroup with clean file cache up to its limit. The monitor must not trip, nothing may be killed.

WHAT V1 CAN AND CANNOT MEASURE. The kernel has no value for "the memory it could reclaim": it reports what it did (events, pgscan/pgsteal, pressure), not what it could do.
So V1 does NOT measure the estimate's error and no check here compares the estimate with a true value. It checks, against things that ARE observable:
  * a controlled input: the cache loader's own bytes read and its own resident size are known, so the estimate must stay within a stated bound of (idle footprint + the loader's known
    anonymous size + a margin). A consistency bound, not an error figure;
  * the kernel's reclaim behaviour: the group sat at its limit and the kernel reclaimed (memory.events max, pgscan/pgsteal rose);
  * reclaim probes (kernels with memory.reclaim): ask the kernel to reclaim a quarter of what the estimate calls reclaimable and see that it does. This can show that AT LEAST that much
    was reclaimable at that moment; it says nothing about the rest;
  * pressure counters (memory.pressure) and whether the workload and the daemon survived.
"""
import os

import evaluate as E
from common import GUARD, LOADDIR, UNIT, StageStop

STAGE = "V1"
PLAN = [("V1.baseline", "units up, no latch; sample the daemon's cgroup at idle for baseline_s"),
        ("V1.dataset", "create 3 x MemoryMax of random files OUTSIDE the unit, written through and dropped from the cache"),
        ("V1.load", "start the cache loader INSIDE the daemon's cgroup for duration_s; every 5 s stop on a latch, a dead unit or a dead loader"),
        ("V1.probe", "reclaim probes at evenly spaced instants (kernels with memory.reclaim)"),
        ("V1.evaluate", "judge from the recorded samples; write result.json and the evidence manifest")]

DEFAULTS = {"qualifying": {"baseline_s": 60, "duration_s": 2400, "need_above_s": 1800, "probes": 10, "psi_full_max": 5.0, "margin_frac": 0.10},
            "smoke": {"baseline_s": 15, "duration_s": 150, "need_above_s": 60, "probes": 4, "psi_full_max": 5.0, "margin_frac": 0.10}}


def evaluate_v1(load, base, probes, before, after, latch, loader, watch, position_moved, cfg):
    """-> list of (id, status, detail, data[, required]). Pure."""
    rows = []
    mx = E.limit(load) or E.limit(base)
    above = E.seconds_at_or_above(load, 0.90)
    peak_cur = E.peak(load, "current") or 0
    dmax = E.event_delta(load, "max")
    scan = E.stat_delta(load, "pgscan") + E.stat_delta(load, "pgsteal")
    real = bool(mx) and above >= cfg["need_above_s"] and peak_cur >= 0.95 * mx and dmax > 0
    rows.append(("V1.workload_was_real", "pass" if real else "invalid",
                 "memory.current at or above 90%% of the limit for %.0f s (need %d), peak %.1f%% of the limit, memory.events max rose by %d. Without these the stage did not test what it is for." % (above, cfg["need_above_s"], 100.0 * peak_cur / mx if mx else 0, dmax),
                 {"above_90_s": above, "peak_current": peak_cur, "limit": mx, "events_max_delta": dmax}))
    rows.append(("V1.kernel_reclaimed", "pass" if scan > 0 else "invalid", "pgscan+pgsteal rose by %d pages: the kernel was reclaiming the cache" % scan, {"pgscan_pgsteal_delta": scan}))
    viol, lat = watch["violations"], latch
    rows.append(("V1.no_trip", "pass" if (lat is None and not viol) else "fail",
                 "no TRIPPED latch and no violation lines in the monitor's log" if (lat is None and not viol) else "LATCH %s / %d violation lines: %s" % (lat, len(viol), [v.get("violation") for v in viol][:5]), {"latch": lat, "violations": viol[:5]}))
    surv = []
    for k in ("daemon", "monitor"):
        if after[k].get("ActiveState") != "active":
            surv.append("%s unit is %s" % (k, after[k].get("ActiveState")))
        if after[k].get("NRestarts") != before[k].get("NRestarts"):
            surv.append("%s restarted (%s -> %s)" % (k, before[k].get("NRestarts"), after[k].get("NRestarts")))
    kills = E.event_delta(load, "oom_kill") + E.event_delta(load, "oom")
    if kills:
        surv.append("memory.events oom/oom_kill rose by %d" % kills)
    if not loader["alive"] and not loader.get("ended_normally"):
        surv.append("the loader is not alive")
    if loader.get("bytes_read_last_60s", 0) <= 0:
        surv.append("the loader made no progress in its last 60 s")
    if not position_moved:
        surv.append("the broadcast clock did not advance")
    rows.append(("V1.survival", "pass" if not surv else "fail", "daemon, monitor and loader alive, no restart, no OOM event, clock advancing" if not surv else "; ".join(surv), {"problems": surv, "loader": loader}))
    full, some = E.pressure_peak(load, "full", "avg60"), E.pressure_peak(load, "some", "avg60")
    if full is None:
        rows.append(("V1.pressure", "not_run", "no pressure counters on this kernel", None, False))
    else:
        rows.append(("V1.pressure", "pass" if full < cfg["psi_full_max"] else "fail", "memory.pressure avg60: some peak %.2f%%, full peak %.2f%% (gate: full below %.1f%%, a chosen bound, not a kernel constant)" % (some or 0, full, cfg["psi_full_max"]), {"some_avg60_peak": some, "full_avg60_peak": full}))
    base_pin = E.median([r["pinned"] for r in E.readable(base)]) or 0
    bound = base_pin + loader.get("rss_peak", 0) + cfg["margin_frac"] * (mx or 0)
    pk = E.peak(load, "pinned") or 0
    rows.append(("V1.estimate_consistent_with_known_input", "pass" if pk <= bound else "fail",
                 "peak estimated footprint %.1f MB <= idle %.1f MB + loader's own resident %.1f MB + margin %.1f MB (a consistency bound against a controlled input, NOT an error measurement)"
                 % (pk / 1e6, base_pin / 1e6, loader.get("rss_peak", 0) / 1e6, cfg["margin_frac"] * (mx or 0) / 1e6), {"peak_pinned": pk, "baseline_pinned": base_pin, "loader_rss_peak": loader.get("rss_peak"), "bound": bound}))
    if not probes:
        rows.append(("V1.reclaim_probes", "not_run", "no probes were made (the kernel has no memory.reclaim, or none were due)", None, False))
    else:
        good = [p for p in probes if p["ok"]]
        rows.append(("V1.reclaim_probes", "pass" if len(good) >= 0.8 * len(probes) else "fail",
                     "%d of %d probes: the kernel reclaimed the requested quarter of the estimate's `reclaimable` (shows at least that much was reclaimable; says nothing about the rest)" % (len(good), len(probes)), {"probes": probes}))
    hb = watch["heartbeats"]
    okmem = hb > 0 and watch["mem_lines"] >= 0.9 * hb and watch["mem_consistent"] >= 0.99 * max(1, watch["mem_lines"])
    rows.append(("V1.heartbeat_records_breakdown", "pass" if okmem else "fail", "%d heartbeats, %d with a `mem` object, %d consistent (anon+file+kernel = current within 1 MiB)" % (hb, watch["mem_lines"], watch["mem_consistent"]), watch["mem_lines"] and {"heartbeats": hb, "mem_lines": watch["mem_lines"], "consistent": watch["mem_consistent"]}))
    return rows


def run(ctx):
    import sampler as SM
    cfg, rec, env = ctx.cfg, ctx.rec, ctx.env
    rcm = SM.load_rc(ctx.tree)
    cg = ctx.cg("daemon")
    ctx.ensure_baseline()
    smp = ctx.sampler("daemon", "daemon_cgroup")
    ctx.mark("V1.baseline", seconds=cfg["baseline_s"])
    env.sleep(cfg["baseline_s"])
    base = smp.records()
    mx = ctx.limit_of("daemon")
    if not mx:
        rec.check("V1.limit", "fail", "the daemon's cgroup has no numeric memory.max (is the unit running under its MemoryMax?)")
        rec.stop_if_failed()
    ctx.mark("V1.dataset", bytes=3 * mx)
    ctx.dataset(3 * mx)
    before = {k: ctx.show(k) for k in ("daemon", "monitor")}
    pos0, t0 = ctx.position(), ctx.now()
    n = cfg["probes"]
    due = [t0 + 5 + (cfg["duration_s"] - 10) * (0.2 + 0.7 * i / max(1, n - 1)) for i in range(n)]
    ld = ctx.loader("cache", "daemon", "cache_loader", dir=LOADDIR, seconds=cfg["duration_s"])
    ctx.mark("V1.load", duration=cfg["duration_s"], loader_pid=ld.pid())
    probes, abort = [], None
    while ctx.now() - t0 < cfg["duration_s"] + 90 and ld.alive():
        lat, _ = ctx.latch()
        if lat:
            abort = "a latch appeared during a healthy workload: %s" % lat
        elif not ctx.active("daemon") or not ctx.active("monitor"):
            abort = "daemon or monitor is no longer active"
        if abort:
            break
        while due and ctx.now() >= due[0]:
            due.pop(0)
            probes.append(probe(ctx, rcm, cg))
        env.sleep(5)
    ended = ld.alive() is False and ld.p.returncode == 0
    if ld.alive():
        ld.stop()
    env.sleep(2)
    smp.stop()
    pos1 = ctx.position()
    after = {k: ctx.show(k) for k in ("daemon", "monitor")}
    load = [r for r in smp.records() if r["t"] >= t0]
    lat, _ = ctx.latch()
    loader = E.loader_summary(ld.records(), ctx.now(), False)
    loader["ended_normally"] = ended
    probes = [p for p in probes if p.get("supported", True)]
    ctx.mark("V1.evaluate")
    rows = evaluate_v1(load, base, probes, before, after, lat, loader, E.summarize_watch(ctx.watch_lines()), E.moved(pos0, pos1, 1.5), cfg)
    if abort:
        rows.insert(0, ("V1.aborted", "fail", abort, None))
    rec.add_all(rows)
    return rows


def probe(ctx, rcm, cg):
    import sampler as SM
    ctx.mark("V1.probe")
    snap = SM.snapshot(rcm, cg, ctx.now())
    if not snap.get("readable"):
        return {"supported": True, "ok": False, "note": "cgroup unreadable"}
    est = snap["reclaimable"]
    req = max(8 << 20, int(0.25 * est))
    req -= req % 4096
    before = snap["sample"]["current"]
    ok, err = ctx.env.cg_write(cg + "/memory.reclaim", str(req))
    ctx.env.sleep(0.5)
    after_txt = (ctx.env.read(cg + "/memory.current") or "0").strip()
    after = int(after_txt) if after_txt.isdigit() else None
    if not ok and err in (2, 22):                       # ENOENT / EINVAL: no memory.reclaim on this kernel
        return {"supported": False}
    return {"supported": True, "t": ctx.now(), "requested": req, "ok": ok, "errno": err, "current_before": before, "current_after": after, "estimate_reclaimable": est}
