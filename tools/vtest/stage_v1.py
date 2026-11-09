"""V1: real cgroup-v2 cache growth. A sustained, healthy workload fills the daemon's cgroup with clean file cache up to its limit. The monitor must not trip, nothing may be killed.

WHAT V1 CAN AND CANNOT MEASURE. The kernel has no value for "the memory it could reclaim": it reports what it did (events, pgscan/pgsteal, pressure), not what it could do.
So V1 does NOT measure the estimate's error and no check here compares the estimate with a true value. It checks, against things that ARE observable:
  * a controlled input: the cache loader's own bytes read and its own resident size are known, so the estimate must stay within a stated bound of (idle footprint + the loader's known
    anonymous size + a margin). A consistency bound, not an error figure;
  * the kernel's reclaim behaviour: the group sat at its limit and the kernel reclaimed (memory.events max, pgscan/pgsteal rose);
  * reclaim probes (kernels with memory.reclaim): ask the kernel to reclaim a quarter of what the estimate calls reclaimable and see that it does. This can show that AT LEAST that much
    was reclaimable at that moment; it says nothing about the rest;
  * pressure counters (memory.pressure), recorded as a DIAGNOSTIC only (docs/MEMORY_MONITOR_VALIDATION_CRITERIA.md): they do not decide the stage, and they cannot be attributed to the
    daemon, because the loader runs inside the same cgroup. Whether the broadcast suffered is judged from the clock, the lease and the publication of live segments, measured directly.

TWO PHASES, in this order, never overlapping (criteria document, section 6):
  publication only   no loader: the daemon publishes the fixture's first window (two games on air) on its own clock. Monitor: no trip. Broadcast: clock stall, lease, publication deadline, errors.
  cache stress       the loader, unchanged. Monitor: every check below. Broadcast: clock stall, lease, errors; no live segment may be published in this phase (`V1.separation`).
"""
import json
import os

import broadcast_probe as P
import evaluate as E
from common import GUARD, LOADDIR, PUBLIC, ROOT_DIR, UNIT, StageStop, jsonl_read

STAGE = "V1"
PLAN = [("V1.baseline", "units up, no latch; sample the daemon's cgroup at idle for baseline_s; start the clock probe"),
        ("V1.dataset", "create 3 x MemoryMax of random files OUTSIDE the unit, written through and dropped from the cache"),
        ("V1.publish", "PHASE 1, no loader: wait until the fixture's first window (two live games) has been on air to its end; judge the monitor (no trip) and the broadcast (stall, lease, deadlines, errors)"),
        ("V1.load", "PHASE 2: start the cache loader INSIDE the daemon's cgroup for duration_s; every 5 s stop on a latch, a dead unit or a dead loader"),
        ("V1.probe", "reclaim probes at evenly spaced instants (kernels with memory.reclaim)"),
        ("V1.evaluate", "judge both phases from the recorded samples; keep the probe, inventories, journals and logs with the evidence; write result.json and the evidence manifest")]

DEFAULTS = {"qualifying": {"baseline_s": 60, "duration_s": 2400, "need_above_s": 1800, "probes": 10, "margin_frac": 0.10, "publish_slack_s": 180},
            "smoke": {"baseline_s": 15, "duration_s": 150, "need_above_s": 60, "probes": 4, "margin_frac": 0.10, "publish_slack_s": 180}}


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
        rows.append(("V1.pressure_diagnostic", "not_run", "no pressure counters on this kernel", None, False))
    else:
        rows.append(("V1.pressure_diagnostic", "info", "DIAGNOSTIC, not gating. memory.pressure of the daemon's cgroup (which also holds the loader, so it cannot be attributed to the daemon): avg60 some peak %.2f%%, full peak %.2f%%; "
                     "the 60 s average lags the load" % (some or 0, full), {"some_avg60_peak": some, "full_avg60_peak": full}, False))
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


def judge_publish_phase(B, samples, clock_log, live, window, t0, t1, reached, latch, viol, journal_text):
    """Phase 1 rows (pure): the monitor did not trip with the daemon publishing, and the broadcast did what the design says. `window` is broadcast_probe.first_window()."""
    rows = [("V1.publish.window_reached", "pass" if reached else "invalid",
             "the clock reached the end of the first window (every game final, position %.0f s)" % window["end_pos"] if reached else
             "the clock did not reach the end of the first window (position %.0f s) in time: the publication phase did not run to completion" % window["end_pos"], None),
            ("V1.publish.no_trip", "pass" if (latch is None and not viol) else "fail",
             "no TRIPPED latch and no violation line while the daemon was publishing" if (latch is None and not viol) else "LATCH %s / %d violation lines" % (latch, len(viol)), {"latch": latch, "violations": viol[:5]})]
    rows += P.clock_rows("V1.publish", B, samples, clock_log, t0, t1)
    rows.append(P.publication_row("V1.publish", B, samples, live, window["segments"], 0.0, t1))        # from the probe's first sample: the whole window must have been observed
    rows.append(P.error_row("V1.publish", journal_text))
    return rows


def judge_stress_phase(B, samples, clock_log, live, window, t0, t1, pos_end, journal_text):
    """Phase 2 broadcast rows (pure): the daemon's clock and lease under the cache stress, errors, and the proof that no publication overlapped the stress."""
    rows = P.clock_rows("V1.cache", B, samples, clock_log, t0, t1)
    during = sorted((name, n) for name, g in live.items() for n, t in g["segments"].items() if t0 <= t <= t1)
    clean = not during and (pos_end is not None and pos_end < window["next_start_s"])
    rows.append(("V1.separation", "pass" if clean else "invalid",
                 "no live segment was published during the cache stress and the clock stayed before the next window (position %.0f s < %s s)" % (pos_end, window["next_start_s"]) if clean else
                 "publication overlapped the cache stress (%d segment files written in it, end position %s, next window at %s s): this phase no longer isolates the cache effect" % (len(during), pos_end, window["next_start_s"]), {"segments_in_phase": len(during)}))
    rows.append(("V1.cache.deadline", "not_run", "no live game is on air in this phase by design (publication is judged in the publication phase)", None, False))
    rows.append(P.error_row("V1.cache", journal_text))
    return rows


def run(ctx):
    import sampler as SM
    cfg, rec, env = ctx.cfg, ctx.rec, ctx.env
    rcm = SM.load_rc(ctx.tree)
    B = P.load_broadcast(ctx.tree)
    store = ROOT_DIR + "/store"
    cg = ctx.cg("daemon")
    ctx.ensure_baseline()
    smp = ctx.sampler("daemon", "daemon_cgroup")
    prb = ctx.probe("broadcast_probe")
    ctx.mark("V1.baseline", seconds=cfg["baseline_s"])
    env.sleep(cfg["baseline_s"])
    base = smp.records()
    mx = ctx.limit_of("daemon")
    if not mx:
        rec.check("V1.limit", "fail", "the daemon's cgroup has no numeric memory.max (is the unit running under its MemoryMax?)")
        rec.stop_if_failed()
    ctx.mark("V1.dataset", bytes=3 * mx)
    ctx.dataset(3 * mx)
    window = P.first_window(json.loads(env.read(store + "/plan.json") or "{}"), json.loads(env.read(store + "/builds.json") or "{}"), B.DELAY_S, B.SEG_S)
    if not window:
        rec.check("V1.window", "invalid", "the installed plan has no live window: nothing can be published, so the publication phase cannot run")
        rec.stop_if_failed()

    # ---- phase 1: publication only (no loader)
    tp0, abort = ctx.now(), None
    ctx.mark("V1.publish", window_start=window["start_s"], until_position=window["end_pos"])
    pos = ctx.position() or 0.0
    reached = False
    while ctx.now() - tp0 < max(0.0, window["end_pos"] - pos) + cfg["publish_slack_s"]:
        p = ctx.position()
        if p is not None and p >= window["end_pos"]:
            reached = True
            break
        lat, _ = ctx.latch()
        if lat:
            abort = "a latch appeared while the daemon was publishing: %s" % lat
        elif not ctx.active("daemon") or not ctx.active("monitor"):
            abort = "daemon or monitor is no longer active"
        if abort:
            break
        env.sleep(5)
    tp1 = ctx.now()
    live_p = P.scan_live(PUBLIC)
    jp = ctx.journal("daemon", tp0)
    ctx.save("segment_inventory_publish.json", live_p)
    ctx.save("journal_daemon_publish.txt", jp)
    lat_p, _ = ctx.latch()
    watch_p = E.summarize_watch(ctx.watch_lines())
    rows_p = judge_publish_phase(B, jsonl_read(prb.log), jsonl_read(store + "/clock_log.jsonl"), live_p, window, tp0, tp1, reached, lat_p, watch_p["violations"], jp)
    if abort:
        rows_p.insert(0, ("V1.aborted", "fail", abort, None))
        for p_ in (prb, smp):
            p_.stop()
        ctx.mark("V1.evaluate")
        rec.add_all(rows_p)
        return rows_p
    quiet = [r for r in smp.records() if tp1 - 30 <= r["t"] <= tp1]
    base = quiet if len(quiet) >= 10 else base                     # the idle footprint right before the load, after the daemon has been publishing

    # ---- phase 2: cache stress
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
    t1 = ctx.now()
    smp.stop()
    prb.stop()
    pos1 = ctx.position()
    after = {k: ctx.show(k) for k in ("daemon", "monitor")}
    load = [r for r in smp.records() if r["t"] >= t0]
    lat, _ = ctx.latch()
    loader = E.loader_summary(ld.records(), ctx.now(), False)
    loader["ended_normally"] = ended
    probes = [p for p in probes if p.get("supported", True)]
    live_c = P.scan_live(PUBLIC)
    ctx.save("segment_inventory_cache.json", live_c)
    jc = ctx.journal("daemon", t0)
    ctx.save("journal_daemon_cache.txt", jc)
    ctx.save("clock_log.jsonl", env.read(store + "/clock_log.jsonl") or "")
    ctx.save("monitor_watch.log", env.read(GUARD + "/watch.log") or "")
    ctx.save("monitor_trips.jsonl", env.read(GUARD + "/trips.jsonl") or "")
    ctx.mark("V1.evaluate")
    rows = rows_p + evaluate_v1(load, base, probes, before, after, lat, loader, E.summarize_watch(ctx.watch_lines()), E.moved(pos0, pos1, 1.5), cfg)
    rows += judge_stress_phase(B, jsonl_read(prb.log), jsonl_read(store + "/clock_log.jsonl"), live_c, window, t0, t1, pos1, jc)
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
