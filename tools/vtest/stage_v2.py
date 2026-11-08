"""V2: sustained anonymous-memory pressure, then an OOM. What the monitor does, and what systemd actually does.

The load is a loader process moved INTO the daemon's own cgroup, so the daemon's real MemoryMax, OOMPolicy and Restart settings apply unchanged.
Limit of the method (stated, not hidden): the injected load is the largest process in the cgroup, so in the natural overrun (V2b) the kernel's victim is normally the load, not the daemon. systemd reacts
to the cgroup's oom_kill event whoever the victim is, so the unit is stopped and restarted either way, but that does NOT show that the daemon's own death is recovered from. Both scenarios read the kernel
log and REPORT the victim. V2c is the scenario that makes the daemon the victim, without making anything unkillable: it RAISES oom_score_adj to 1000 for the processes already inside the daemon's
disposable cgroup (Env.oom_adjust: raise only, 0..1000, only pids of that cgroup, nothing persistent), then applies a bounded load. The summary check V2.daemon_oom_exercised passes ONLY with kernel
evidence that the daemon itself was killed; no evidence (log unreadable, other victim, selection not applied) leaves it INCOMPLETE (not_run), and an incomplete stage blocks the full run.

V2a  plateau at 93 % of MemoryMax: the monitor must trip on its sustained rule, with evidence, and the daemon must pause, before any kernel kill.
V2b/V2c  growth past the limit: the kernel kills; the whole unit must stop, restart as configured, leave no survivor and no damaged public file. REQUIRED: a persistent incident record (the latch and
     its evidence) and a hold: after the restart the daemon stays paused, its clock does not advance, and the latch is unchanged. An OOM that leaves no latch FAILS the stage: a quick restart must
     not erase or bypass the protection. If that happens it is a defect of the candidate, to be fixed in a new, separately tested candidate; the harness does not fix it.
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
        ("V2b.hold", "after the recovery, 15 s: the latch is unchanged, the daemon still paused, the clock stopped; read the kernel log for the OOM victim"),
        ("V2b.evaluate", "judge V2b"),
        ("V2c.reset", "daemon-victim scenario: clear the latch the documented way; record pids, restarts, positions, public files"),
        ("V2c.prefer", "raise oom_score_adj to 1000 (raise only, never lower) of the processes already in the daemon's cgroup; read it back; the injected load is untouched"),
        ("V2c.overrun", "bounded anonymous load (1.5 x MemoryMax at 25 MB/s, finite wait, always stopped) in the daemon's cgroup"),
        ("V2c.timeline", "record the unit, the loader, the latch every second for up to 150 s after the kill"),
        ("V2c.hold", "after the recovery, 15 s: latch unchanged, daemon paused, clock stopped; read the kernel log for the victim"),
        ("V2c.evaluate", "judge V2c, then the summary check V2.daemon_oom_exercised (kernel evidence that the daemon itself was the victim, else incomplete)")]

DEFAULTS = {"qualifying": {"baseline_s": 30, "plateau_rate": 5e6, "trip_wait_s": 300, "overrun_rate": 25e6, "timeline_s": 150, "v2c_factor": 1.5},
            "smoke": {"baseline_s": 10, "plateau_rate": 10e6, "trip_wait_s": 240, "overrun_rate": 40e6, "timeline_s": 90, "v2c_factor": 1.5}}


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


def evaluate_v2b(kills, journal, pre, post, tl, latch, inv_before, inv_after, partial, procs_new, mon_before, mon_after, old_pids_alive, mem_end, mx, pos_before, pos_after, elapsed, cfg,
                 victims=None, daemon_pids=(), loader_pid=None, incident=None, t0=0.0):
    """`victims`: the OOM killer's victims from the kernel log (evaluate.parse_oom_victims); None or [] means the victim is UNKNOWN. `daemon_pids`: pids in the daemon's cgroup before the load was added.
    `incident`: what was observed after the recovery: {"sha_before","sha_after","paused","pos_a","pos_b","dt","new_evidence": [trip folders with evidence.json created since t0]}; None means nothing was observed."""
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
    # --- the persistent incident record and the hold (a REQUIRED outcome, not an observation)
    oom = kills >= 1 or saw
    inc = incident or {}
    problems = []
    if not oom:
        pass
    elif not latch:
        problems.append("no latch (TRIPPED) exists after the OOM: systemd restarted the daemon and nothing recorded or held the incident")
    elif latch.get("unreadable"):
        problems.append("the latch exists but cannot be parsed")
    else:
        if (latch.get("at") or 0) < t0 - 1:
            problems.append("the latch predates the OOM (at=%s, scenario started %.0f)" % (latch.get("at"), t0))
        if not inc.get("new_evidence"):
            problems.append("no new evidence folder (with evidence.json) was written for the incident")
        if not inc:
            problems.append("the hold was not observed")
        else:
            if not inc.get("paused"):
                problems.append("the daemon is not paused after the restart (no PAUSE file)")
            if inc.get("pos_a") is None or inc.get("pos_b") is None or inc["pos_b"] - inc["pos_a"] > 1.0:
                problems.append("the broadcast clock advanced during the hold (%s -> %s)" % (inc.get("pos_a"), inc.get("pos_b")))
            if not inc.get("sha_before") or inc.get("sha_before") != inc.get("sha_after"):
                problems.append("the latch changed or vanished during the hold")
            if (inc.get("dt") or 0) < 10:
                problems.append("the hold was observed for under 10 s")
    if not oom:
        rows.append(("V2b.incident_record_and_hold", "invalid", "no confirmed OOM, so the requirement could not be tested", None))
    elif problems:
        rows.append(("V2b.incident_record_and_hold", "fail", "DEFECT OF THE CANDIDATE (not of the harness): a confirmed OOM must leave a persistent incident record and hold the broadcast for review, and a quick restart must not bypass it. " + "; ".join(problems),
                     {"problems": problems, "latch": latch, "incident": inc}))
    else:
        rows.append(("V2b.incident_record_and_hold", "pass", "the OOM left a latch (kind=%s source=%s), new evidence %s, and the daemon stayed paused with the clock stopped and the latch unchanged for %.0f s" % (latch.get("kind"), latch.get("source"), inc.get("new_evidence"), inc.get("dt") or 0),
                     {"latch": latch, "incident": inc}))
    # --- who the OOM killer killed
    cls = E.classify_victims(victims or [], daemon_pids, loader_pid)
    desc = lambda l: ", ".join("pid %s (%s)" % (v["pid"], v["comm"]) for v in l) or "none"
    if not victims:
        rows.append(("V2b.victim_reported", "not_run", "INCOMPLETE: the kernel log could not be read or names no victim; who was killed is UNKNOWN", {"victims": []}))
        daemon_row = ("V2b.daemon_oom_exercised", "not_run", "INCOMPLETE: the victim is unknown, so it is not shown that the daemon's own termination and recovery were exercised", None, False)
    else:
        rows.append(("V2b.victim_reported", "pass", "OOM victims: daemon %s; injected load %s; other %s" % (desc(cls["daemon"]), desc(cls["loader"]), desc(cls["other"])), {"victims": victims, "classes": cls}))
        if cls["daemon"]:
            daemon_row = ("V2b.daemon_oom_exercised", "pass", "the kernel killed the daemon itself (%s); the recovery rows above judge what followed" % desc(cls["daemon"]), {"classes": cls}, False)
        else:
            daemon_row = ("V2b.daemon_oom_exercised", "not_run", "INCOMPLETE: the kernel killed only %s, not the daemon. systemd's OOMPolicy=stop then stopped and restarted the unit, which is not the same as recovering from the daemon's own death. Daemon-OOM validation is NOT done." % (
                "the injected load (%s)" % desc(cls["loader"]) if cls["loader"] else "other processes (%s)" % desc(cls["other"])), {"classes": cls}, False)
    rows.append(daemon_row)
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
    # ---------------- V2b: the natural overrun (the victim is normally the injected load), then V2c: the daemon is made the preferred victim
    rows_b = _overrun(ctx, "V2b", SCEN["V2b"], prefer=False, factor=3.0)
    rec.add_all(rows_b)
    rec.stop_if_failed()
    rows_c = _overrun(ctx, "V2c", SCEN["V2c"], prefer=True, factor=cfg.get("v2c_factor", 1.5))
    rec.add_all(rows_c)
    rec.stop_if_failed()
    summary = daemon_oom_summary(rows_b, rows_c)
    rec.add_all([summary])
    return rows_b + rows_c + [summary]


def _overrun(ctx, tag, m, prefer, factor):
    """One OOM scenario in the daemon's own cgroup. prefer=False: the injected load grows to `factor` x MemoryMax and the kernel picks whom it likes. prefer=True (V2c): first the processes already in the daemon's
    cgroup get oom_score_adj 1000 (a test-only, raise-only change confined to that group, see Env.oom_adjust), then a BOUNDED load (factor x MemoryMax, finite wait, always stopped) pushes the group over its limit."""
    cfg, env = ctx.cfg, ctx.env
    ctx.mark(m["reset"])
    ctx.ensure_baseline("vtest: reset before the %s OOM scenario" % tag)
    smp = ctx.sampler("daemon", tag.lower() + "_cgroup")
    env.sleep(cfg["baseline_s"])
    mx = ctx.limit_of("daemon")
    pre, mon_before = ctx.show("daemon"), ctx.show("monitor")
    inv_before, pos_before, t0 = ctx.public_inventory(), ctx.position(), ctx.now()
    daemon_pids = ctx.daemon_pids()
    evid_before = {d for d in env.listdir(GUARD + "/evidence")} if env.exists(GUARD + "/evidence") else set()
    adj_set = None
    if prefer:
        adj_set = ctx.prefer_daemon_as_oom_victim()
        ctx.mark(m["prefer"], applied={str(k): v for k, v in adj_set.items()})
    ld = ctx.loader("anon", "daemon", tag.lower() + "_anon", target_bytes=int(factor * mx), rate_bps=cfg["overrun_rate"])
    old_loader_pid = ld.pid()
    ctx.mark(m["overrun"], loader_pid=old_loader_pid, daemon_pid=pre.get("MainPID"), daemon_pids=daemon_pids, target_bytes=int(factor * mx))

    def killed():
        sh = ctx.show("daemon")
        return (sh.get("NRestarts", 0) > pre.get("NRestarts", 0) or sh.get("ActiveState") != "active" or E.event_delta(E.readable(smp.records())[-30:], "oom_kill") > 0) or (not ld.alive())
    v, _ = ctx.wait(killed, factor * mx / cfg["overrun_rate"] + 120, every=0.5)       # finite: the load's own size over its rate, plus 2 minutes
    t_kill = ctx.now()
    ctx.mark(m["killed"], seen=bool(v))
    ctx.mark(m["timeline"])
    tl = []
    while ctx.now() - t_kill < cfg["timeline_s"]:
        sh = ctx.show("daemon")
        lat, _ = ctx.latch()
        tl.append({"t": round(ctx.now(), 1), "ActiveState": sh.get("ActiveState"), "MainPID": sh.get("MainPID"), "NRestarts": sh.get("NRestarts"), "Result": sh.get("Result"), "loader_alive": ld.alive(), "latch": bool(lat)})
        ctx.mark(m["tick"], **tl[-1])
        if sh.get("ActiveState") == "active" and sh.get("MainPID") != pre.get("MainPID") and sh.get("NRestarts", 0) > pre.get("NRestarts", 0) and ctx.now() - t_kill > 45:
            break
        env.sleep(1)
    if ld.alive():
        ld.stop()                                                   # the bounded load is always stopped
    smp.stop()
    post, mon_after = ctx.show("daemon"), ctx.show("monitor")
    recs = [r for r in smp.records() if r["t"] >= t0]
    new_pids = ctx.daemon_pids()
    procs_new = [(env.read("/proc/%d/cmdline" % pid) or "").replace("\0", " ") for pid in new_pids]
    adj_after = ctx.oom_adj_of(new_pids)
    old_alive = [p for p in (old_loader_pid, pre.get("MainPID")) if p and p != post.get("MainPID") and env.exists("/proc/%s" % p)]
    mem_t = (env.read(ctx.cg("daemon") + "/memory.current") or "").strip()
    ctx.mark(m["hold"])
    lat0, sha0 = ctx.latch()
    pos_a, ta = ctx.position(), ctx.now()
    env.sleep(15)
    lat1, sha1 = ctx.latch()
    new_evidence = sorted(d for d in (env.listdir(GUARD + "/evidence") if env.exists(GUARD + "/evidence") else []) if d not in evid_before and env.exists("%s/evidence/%s/evidence.json" % (GUARD, d)))
    incident = {"sha_before": sha0, "sha_after": sha1, "paused": ctx.paused(), "pos_a": pos_a, "pos_b": ctx.position(), "dt": ctx.now() - ta, "new_evidence": new_evidence}
    victims = E.parse_oom_victims(ctx.kernel_log(t0 - 2), UNIT["daemon"])
    ctx.mark(m["evaluate"])
    rows = evaluate_v2b(E.event_delta(recs, "oom_kill"), ctx.journal("daemon", t0 - 2), pre, post, tl, lat1, inv_before, ctx.public_inventory(), ctx.partial_files(), procs_new, mon_before, mon_after, old_alive,
                        int(mem_t) if mem_t.isdigit() else None, mx, pos_before, ctx.position(), ctx.now() - t0, cfg,
                        victims=victims, daemon_pids=daemon_pids, loader_pid=old_loader_pid, incident=incident, t0=t0)
    rows = rename_rows(rows, "V2b", tag)
    if prefer:
        rows += evaluate_v2c_extra(daemon_pids, adj_set, adj_after)
    return rows


SCEN = {"V2b": dict(reset="V2b.reset", overrun="V2b.overrun", killed="V2b.killed_or_timeout", timeline="V2b.timeline", tick="V2b.tick", hold="V2b.hold", evaluate="V2b.evaluate"),
        "V2c": dict(reset="V2c.reset", prefer="V2c.prefer", overrun="V2c.overrun", killed="V2c.killed_or_timeout", timeline="V2c.timeline", tick="V2c.tick", hold="V2c.hold", evaluate="V2c.evaluate")}


def rename_rows(rows, old, new):
    return [(new + r[0][len(old):],) + tuple(r[1:]) if r[0].startswith(old + ".") else r for r in rows]


def evaluate_v2c_extra(daemon_pids, adj_set, adj_after):
    """The checks only the daemon-victim scenario has. adj_set: {pid: {"ok","readback"}} from Ctx.prefer_daemon_as_oom_victim; adj_after: {pid: oom_score_adj or None} of the processes in the cgroup after the recovery."""
    rows = []
    applied = bool(daemon_pids) and bool(adj_set) and set(adj_set) == set(daemon_pids) and all(v.get("ok") and v.get("readback") == 1000 for v in adj_set.values())
    rows.append(("V2c.victim_selection_applied", "pass" if applied else "invalid",
                 "oom_score_adj raised to 1000 and read back for every process in the daemon's cgroup: %s" % (sorted(adj_set or {}) if applied else
                 "NOT applied (%s): the scenario did not test what it was meant to" % (adj_set or "no processes in the cgroup")), {"applied": adj_set}))
    vals = list((adj_after or {}).values())
    if not vals or any(v is None for v in vals):
        rows.append(("V2c.adjustment_not_persistent", "not_run", "INCOMPLETE: the new daemon's oom_score_adj could not be read, so it is not shown that the change ended with the old process", {"after": adj_after}))
    else:
        rows.append(("V2c.adjustment_not_persistent", "pass" if all(v != 1000 for v in vals) else "fail",
                     "oom_score_adj of the processes in the cgroup after the restart: %s (must not still be 1000)" % sorted(set(vals)), {"after": adj_after}))
    return rows


def daemon_oom_summary(rows_b, rows_c):
    """THE check that daemon-OOM termination and recovery were exercised. Passes only with kernel evidence that the daemon itself (a pid that was in its cgroup before the load) was the OOM victim, in the natural
    scenario or in the daemon-victim scenario (whose selection step must also have been applied). Anything else, including no readable kernel log, is INCOMPLETE (not_run), never pass."""
    by = {}
    for r in list(rows_b) + list(rows_c):
        by[r[0]] = r
    reasons, evidence = [], None
    for tag in ("V2b", "V2c"):
        row = by.get(tag + ".daemon_oom_exercised")
        if row is None:
            reasons.append("%s: not run" % tag)
        elif row[1] != "pass":
            reasons.append("%s: %s" % (tag, row[2]))
        elif tag == "V2c" and (by.get("V2c.victim_selection_applied") or ("", ""))[1] != "pass":
            reasons.append("V2c: victim selection was not shown to be applied")
        elif evidence is None:
            evidence = (tag, row)
    if evidence:
        return ("V2.daemon_oom_exercised", "pass", "the kernel killed the daemon itself (%s): %s" % (evidence[0], evidence[1][2]), {"scenario": evidence[0], "classes": (evidence[1][3] or {}).get("classes")})
    return ("V2.daemon_oom_exercised", "not_run", "INCOMPLETE: no kernel evidence that the daemon itself was the OOM victim. Daemon-OOM termination and recovery are NOT validated. " + " | ".join(reasons), {"reasons": reasons})
