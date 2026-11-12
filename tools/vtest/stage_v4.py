"""V4: a trip latch that already exists survives restarts and prevents resumption, and is cleared only by the documented procedure.

Starts from a REAL latch (the monitor's own memory trip, produced the way V2a does; or the one V2a left, if it is the monitor's memory trip). Then, after each disturbance, the latch's
sha256, size and mtime must be identical, the daemon must still be paused with its clock stopped and its public files unchanged, and `resume` must still be refused.
The full V4 saves the state a machine reboot is judged against (units are not enabled; after the reboot run `run V4 --after-reboot` BEFORE starting anything: the stage first reads the run marker the daemon had open, then starts the
units itself). `--after-hard-reset` is the same after the operator cut the power or reset the machine instead of rebooting it.
The candidate's lifecycle design is exercised here too: every disturbance must close and reopen the daemon's run marker and add no incident line; a fault injected into the guard folder (made immutable) during an OOM must
keep the daemon from running over an unrecorded incident (the start gate refuses), and once the folder works again the daemon must start HELD with an unaccounted_stop latch; a clean reboot must close the marker, a hard
reset must leave it (and the next start must record the unclean stop).
"""
import json
import os

import evaluate as E
import lifecycle as LC
from common import GUARD, ROOT_DIR, UNIT, StageStop, sha256_file, jsonl_read
from scenarios import produce_plateau_latch

STAGE = "V4"
STEPS = ("daemon_restart", "monitor_restart", "both_daemon_first", "both_monitor_first", "daemon_kill9", "monitor_kill9")
PLAN = [("V4.latch", "use or produce a real memory latch; record its sha256, size, mtime and the evidence it came with"),
        ("V4.disturb", "after each of: daemon restart, monitor restart, both (two orders), daemon kill -9, monitor kill -9: latch identical, daemon paused, clock stopped, public files unchanged, resume refused"),
        ("V4.refusals", "resume refused (rc 3); release with a short reason refused (rc 2); release with a stale heartbeat refused (rc 4); the latch intact after each"),
        ("V4.release", "release (rc 0): the latch moves into evidence/released/ and nothing is deleted; resume (rc 0); the clock advances"),
        ("V4.immutable_guard", "SCENARIO (its own; not a stand-in for a full disk): guard folder immutable + a bounded OOM of the daemon: no new daemon runs, PAUSE holds, the marker stays, the journal shows the refusal; the folder works again: the daemon starts HELD with an unaccounted_stop latch naming the previous run; release + resume the documented way"),
        ("V4.second_trip", "produce a second latch: a second evidence folder appears and the first stays in released/"),
        ("V4.after_reboot", "optional, after the full V4 (--after-reboot for a clean reboot or --after-hard-reset for a power cut; `--capture-reboot-state` re-records the state before the second variant): the run marker is closed (clean) or still there (hard) BEFORE any unit starts; then the same preservation checks, and no new incident line (clean) or exactly one unaccounted_stop saying the host restarted (hard)")]
DEFAULTS = {"qualifying": {"settle_s": 25, "watch_s": 15, "stale_wait_s": 40, "overrun_rate": 25e6, "gate_watch_s": 15, "gate_wait_s": 120, "gate_hold_s": 15},
            "smoke": {"settle_s": 20, "watch_s": 10, "stale_wait_s": 40, "overrun_rate": 40e6, "gate_watch_s": 15, "gate_wait_s": 120, "gate_hold_s": 12}}


def fingerprint(path):
    try:
        st = os.stat(path)
        return {"sha256": sha256_file(path), "size": st.st_size, "mtime": st.st_mtime}
    except OSError:
        return None


def evaluate_disturbance(step, fp0, fp1, paused, pos_a, pos_b, inv0, inv1, resume_rc, units_active, trips0, trips1, allow_new=()):
    """`allow_new`: kinds of trips.jsonl lines this disturbance may legitimately add (none for a restart or a kill -9: nothing in them is an incident)."""
    problems = []
    if fp1 is None:
        problems.append("the latch is GONE")
    elif fp1 != fp0:
        problems.append("the latch changed: %s -> %s" % (fp0, fp1))
    if not paused:
        problems.append("no PAUSE file: the daemon is not held")
    if E.moved(pos_a, pos_b, 1.5):
        problems.append("the broadcast clock advanced (%s -> %s)" % (pos_a, pos_b))
    if inv0 != inv1:
        problems.append("immutable public files changed (%d -> %d)" % (len(inv0), len(inv1)))
    if resume_rc != 3:
        problems.append("`resume` returned %s, expected the refusal (3)" % resume_rc)
    if not units_active:
        problems.append("daemon or monitor not active after the disturbance")
    fresh = LC.new_lines(trips0, trips1)
    if fresh is None:
        problems.append("trips.jsonl lost or rewrote an earlier line")
    elif [l.get("kind") for l in fresh if l.get("kind") not in allow_new]:
        problems.append("trips.jsonl gained a line that nothing here should write: %s (a restart or kill -9 is not an incident)" % [l.get("kind") for l in fresh if l.get("kind") not in allow_new])
    return ("V4.disturb.%s" % step, "fail" if problems else "pass", "latch identical, daemon held, clock stopped, public files unchanged, resume refused, no new incident line" if not problems else "; ".join(problems), {"problems": problems, "fp": fp1})


def evaluate_refusals(rc_resume, rc_short, rc_stale, fp0, fps):
    rows = []
    rows.append(("V4.refusals.resume", "pass" if rc_resume == 3 else "fail", "resume with a latch present returned %s (expected 3)" % rc_resume, None))
    rows.append(("V4.refusals.release_short_reason", "pass" if rc_short == 2 else "fail", "release with a 4-character reason returned %s (expected 2)" % rc_short, None))
    rows.append(("V4.refusals.release_stale_heartbeat", "pass" if rc_stale == 4 else "fail", "release while the monitor's heartbeat was stale returned %s (expected 4)" % rc_stale, None))
    rows.append(("V4.refusals.latch_untouched", "pass" if all(f == fp0 for f in fps) else "fail", "latch fingerprint identical after each refusal: %s" % all(f == fp0 for f in fps), None))
    return rows


def new_released_records(before_files, after_files, read):
    """The records evidence/released/ gained since `before_files`, oldest first.

    q3 (2026-11-12): V4 judged the oldest file in evidence/released/, a leftover
    from q2's V2b reset, instead of the record its own release created. The
    folder is never emptied between stages, so only files that were not there
    before the release can be this release's evidence."""
    return [json.loads(read(p)) for rel, p in sorted(after_files.items()) if rel.startswith("evidence/released/") and rel not in before_files]


def evaluate_release(rc_release, rc_resume, latch_after, released, before_files, after_files, orig, advances, held_back):
    rows = []
    rows.append(("V4.release.accepted", "pass" if rc_release == 0 and latch_after is None else "fail", "release rc %s, latch present afterwards: %s" % (rc_release, latch_after is not None), None))
    kept = bool(released) and all(released[0].get(k) == orig.get(k) for k in ("kind", "detail", "source", "at")) and "released_reason" in released[0]
    rows.append(("V4.release.latch_kept_in_evidence", "pass" if kept else "fail", "evidence/released/ holds the latch's fields plus the release reason: %s" % kept, {"released": released[:1]}))
    lost = [p for p in before_files if p not in after_files and not p.endswith("TRIPPED")]
    rows.append(("V4.release.nothing_deleted", "pass" if not lost else "fail", "%d files before, %d after; deleted: %s" % (len(before_files), len(after_files), lost[:5] or "none"), {"deleted": lost[:10]}))
    rows.append(("V4.release.resume_works", "pass" if rc_resume == 0 and advances else "fail", "resume rc %s, clock advances afterwards: %s" % (rc_resume, advances), None))
    rows.append(("V4.release.held_until_resume", "pass" if held_back else "fail", "after release and before resume the daemon was still paused: %s" % held_back, None))
    return rows


def guard_files(env):
    out = {}
    for d, _, ns in os.walk(GUARD):
        for n in ns:
            p = os.path.join(d, n)
            out[os.path.relpath(p, GUARD)] = p
    return out


def marker_row(step, before, after, daemon_restarted, boot_id):
    """The run marker through a disturbance: a daemon stop closes it and the next start opens a new one; a monitor-only disturbance leaves the same one."""
    if daemon_restarted:
        p = LC.judge_marker_reopened(before, after, boot_id)
    else:
        p = [] if (before.get("state") == "open" and after == before) else ["the daemon was not touched but its run marker changed: %s -> %s" % (before, after)]
    return ("V4.marker.%s" % step, "fail" if p else "pass", ("the run marker was closed and a new one opened" if daemon_restarted else "the run marker is the same run's") if not p else "; ".join(p), {"before": before, "after": after})


def _held(ctx, label, fp0, trips0, inv0, rec, marker_before=None, daemon_restarted=None, allow_new=()):
    ctx.env.sleep(ctx.cfg["settle_s"])
    pos_a, ta = ctx.position(), ctx.now()
    ctx.env.sleep(ctx.cfg["watch_s"])
    rc_resume = ctx.env.operator(["/usr/bin/python3", "-I", ctx.tree + "/tools/broadcast_run.py", "resume", "--root", ROOT_DIR, "--guard", GUARD])[0]
    row = evaluate_disturbance(label, fp0, fingerprint(GUARD + "/TRIPPED"), ctx.paused(), pos_a, ctx.position(), inv0, ctx.public_inventory(), rc_resume,
                               ctx.active("daemon") and ctx.active("monitor"), trips0, jsonl_read(GUARD + "/trips.jsonl"), allow_new)
    rec.add_all([row])
    if marker_before is not None:
        rec.add_all([marker_row(label, marker_before, ctx.run_marker(), daemon_restarted, ctx.env.boot_id())])
    ctx.mark("V4.disturb", step=label, status=row[1])
    return row


def _reset_start_budget(ctx, rec, step):
    """Each disturbance is judged on its own, with a full start budget.
    Found 2026-11-12 (smoke-4): the daemon unit allows 3 starts in 3600 s (StartLimitBurst=3, StartLimitAction=none, by design: docs/OOM_INCIDENT_RECORD.md) and V4's own restarts, with those of V2 and V3
    in the same hour, used the budget up, so systemd refused the restart after `kill -9` ("Start request repeated too quickly") and the step failed for a reason that is not what the step tests.
    `systemctl reset-failed` clears the start-limit counters (it is also the documented recovery). What the limit does is NOT hidden: it is recorded here for every step, and the production risk
    (a fourth start within an hour, including the automatic restart after a crash, fails and the unit stays failed until someone resets it) goes in the session report."""
    env = ctx.env
    before = {k: env.sc("show", UNIT[k], "-pNRestarts", "-pActiveState", "-pSubState", "-pResult")[1].split() for k in ("daemon", "monitor")}
    for k in ("daemon", "monitor"):
        env.sc("reset-failed", UNIT[k])
    rec.check("V4.start_budget.%s" % step, "info",
              "start-limit counters of the daemon (3 starts per 3600 s) and the monitor (5 per 3600 s) were reset before this disturbance, so this step is not judged on starts used by earlier steps or stages; "
              "the limit itself is not what this step tests (V3d tests it for the monitor)", {"before": before}, False)


def preservation_state(ctx):
    return fingerprint(GUARD + "/TRIPPED"), jsonl_read(GUARD + "/trips.jsonl"), ctx.public_inventory()


def _immutable_guard_scenario(ctx, rec):
    """SCENARIO: an IMMUTABLE GUARD FOLDER. This is its own scenario and NOT proof of the candidate's behaviour on a full disk: `chattr +i` makes every create, rename and removal in the folder fail with EPERM at once,
    while a full filesystem (ENOSPC) lets some writes succeed and others fail, and can leave a file half written. The full-disk case is covered only by the local sandbox test (tests/test_service_sandbox.py,
    a real 48 KiB tmpfs); it is NOT exercised on the disposable machine.
    Failure injection with the REAL start gate and stop hook: the guard folder is made immutable (chattr +i: nothing can be created in it, not even by root), then a bounded OOM stops the daemon's unit.
    Expected: the stop hook cannot record the incident (PAUSE, in the store, still works) and leaves the run marker; the start gate cannot latch and REFUSES (exit 1), so no new daemon runs. Then the folder is
    made writable again (always, in a finally): the next start must latch unaccounted_stop and start HELD. If the unit hit its start limit meanwhile it is reset the documented way (reset-failed, start).
    The folder is touched by the harness only through Env.immutable. The monitor's heartbeat is written in the same folder and may stall for the few seconds of the fault (it is far inside the 120 s stale limit)."""
    env, cfg = ctx.env, ctx.cfg
    ctx.mark("V4.immutable_guard", phase="baseline")
    ctx.ensure_baseline("vtest V4: baseline before the guard-folder fault")
    pre, mk0, trips0 = ctx.show("daemon"), ctx.run_marker(), ctx.trips()
    mx = ctx.limit_of("daemon")
    t0 = ctx.now()
    ok, why = env.immutable(GUARD, True)
    ctx.mark("V4.immutable_guard", phase="fault_on", ok=ok, detail=why)
    if not ok:
        rec.check("V4.immutable_guard.fault_applied", "invalid", "could not make the guard folder unwritable (%s): the failure-injection case was NOT tested" % why, {"detail": why})
        return
    ld, window, refused, held_pos = None, [], False, (None, None)
    journal = ""
    try:
        ld = ctx.loader("anon", "daemon", "v4_gate_anon", target_bytes=int(1.5 * mx), rate_bps=cfg["overrun_rate"])

        def refusal_seen():
            j = ctx.journal("daemon", t0 - 2)
            return ('"gate": "refused"' in j or '"incident": "unrecorded"' in j) and j
        journal, _ = ctx.wait(refusal_seen, 1.5 * mx / cfg["overrun_rate"] + cfg["gate_wait_s"], every=1.0)
        journal = journal or ctx.journal("daemon", t0 - 2)
        ctx.mark("V4.immutable_guard", phase="refusal_seen", seen=bool(journal))
        pos_a, ta = ctx.position(), ctx.now()
        while ctx.now() - ta < cfg["gate_watch_s"]:
            sh = ctx.show("daemon")
            window.append({"t": round(ctx.now(), 1), "ActiveState": sh.get("ActiveState"), "MainPID": sh.get("MainPID")})
            env.sleep(1)
        held_pos = (pos_a, ctx.position())
        paused = ctx.paused()
        marker_during = ctx.run_marker()
        journal = ctx.journal("daemon", t0 - 2)
    finally:
        if ld is not None:
            ld.stop()
        ok2, why2 = env.immutable(GUARD, False)
        ctx.mark("V4.immutable_guard", phase="fault_off", ok=ok2, detail=why2)
    if not ok2:
        raise StageStop("the guard folder is STILL IMMUTABLE and could not be restored (%s): run `chattr -i %s` by hand" % (why2, GUARD))
    rows = evaluate_gate_refusal_rows(window, pre, mk0, marker_during, journal, held_pos, paused)
    rec.add_all(rows)
    rec.check("V4.immutable_guard.scope", "info", "SCENARIO: immutable guard folder (every create/rename/unlink fails with EPERM). It is not evidence of equivalent behaviour on a full disk (ENOSPC), which only the local tmpfs test covers.", None, False)
    # ---- storage works again: the unit must come back, held
    came, _ = ctx.wait(lambda: ctx.active("daemon") and ctx.show("daemon").get("MainPID") not in (0, pre.get("MainPID")), 45, every=1.0)
    path = "by itself (systemd's own retry)"
    if not came:
        env.sc("reset-failed", UNIT["daemon"])
        env.sc("start", UNIT["daemon"])
        ctx.wait(lambda: ctx.active("daemon") and ctx.show("daemon").get("MainPID") not in (0, pre.get("MainPID")), 60, every=1.0)
        path = "after reset-failed and start (the unit had stopped retrying)"
    post = ctx.show("daemon")
    lat0, sha0 = ctx.latch()
    pos_a, ta = ctx.position(), ctx.now()
    env.sleep(cfg["gate_hold_s"])
    lat1, sha1 = ctx.latch()
    hold = {"paused": ctx.paused(), "pos_a": pos_a, "pos_b": ctx.position(), "dt": ctx.now() - ta, "sha_before": sha0, "sha_after": sha1}
    gate_dirs = sorted(d for d in env.listdir(GUARD + "/evidence") if d.endswith("-unaccounted_stop") and env.exists("%s/evidence/%s/evidence.json" % (GUARD, d)))
    try:
        doc = json.loads(env.read("%s/evidence/%s/evidence.json" % (GUARD, gate_dirs[-1]))) if gate_dirs else None
    except ValueError:
        doc = None
    rec.add_all(LC.evaluate_gate_recovery(lat1, mk0, ctx.run_marker(), pre.get("MainPID"), post.get("MainPID"), doc, hold, env.boot_id(), path))
    rec.add_all([("V4.immutable_guard.trips_since_the_fault", "info", "trips.jsonl lines added since the fault: %s" % [l.get("kind") for l in (LC.new_lines(trips0, ctx.trips()) or [])], {"new": LC.new_lines(trips0, ctx.trips())}, False)])
    rec.stop_if_failed()
    # ---- recovery the documented way
    ctx.wait(lambda: (ctx.heartbeat_age() or 999) < 20, 90)
    r1, r2, out = ctx.release_and_resume("vtest V4: the injected guard-folder fault was observed, releasing the unaccounted_stop latch the documented way")
    p0 = ctx.position()
    env.sleep(15)
    advances = E.moved(p0, ctx.position(), 5.0)
    rec.check("V4.immutable_guard.recoverable_the_documented_way", "pass" if r1 == 0 and r2 == 0 and advances else "fail", "release rc %s, resume rc %s, clock advances afterwards: %s" % (r1, r2, advances), {"output": out})
    rec.stop_if_failed()


def evaluate_gate_refusal_rows(window, pre, mk0, marker_during, journal, held_pos, paused):
    return LC.evaluate_gate_refusal(window, pre.get("MainPID"), mk0, marker_during, journal, held_pos, paused)


def run(ctx):
    cfg, rec, env = ctx.cfg, ctx.rec, ctx.env
    ctx.mark("V4.latch")
    latch, _ = ctx.latch()
    if not (latch and latch.get("kind") == "memory_high" and latch.get("source") == "monitor"):
        ctx.ensure_baseline("vtest V4: reset so that a fresh real memory latch can be produced")
        latch, lsum = produce_plateau_latch(ctx, "v4_first")
        rec.check("V4.latch", "pass" if latch and latch.get("kind") == "memory_high" else "fail", "latch produced by a real plateau: %s" % latch, {"latch": latch, "loader": lsum})
        rec.stop_if_failed()
    else:
        rec.check("V4.latch", "pass", "using the monitor's memory latch already present: %s" % latch, {"latch": latch})
    ctx.start("site", "daemon", "monitor")
    fp0, trips0, inv0 = preservation_state(ctx)
    rec.check("V4.latch_fingerprint", "info", "sha256 %s" % fp0["sha256"], fp0, False)
    orig = json.loads(env.read(GUARD + "/TRIPPED"))
    d, m = UNIT["daemon"], UNIT["monitor"]
    for step in STEPS:
        _reset_start_budget(ctx, rec, step)
        marker_before = ctx.run_marker()
        if step == "daemon_restart":
            env.sc("restart", d)
        elif step == "monitor_restart":
            env.sc("restart", m)
        elif step == "both_daemon_first":
            env.sc("restart", d)
            env.sc("restart", m)
        elif step == "both_monitor_first":
            env.sc("restart", m)
            env.sc("restart", d)
        elif step == "daemon_kill9":
            env.sc("kill", d, "--signal=SIGKILL", "--kill-whom=main")
        elif step == "monitor_kill9":
            env.sc("kill", m, "--signal=SIGKILL", "--kill-whom=main")
        ctx.wait(lambda: ctx.active("daemon") and ctx.active("monitor"), 90)
        row = _held(ctx, step, fp0, trips0, inv0, rec, marker_before, step not in ("monitor_restart", "monitor_kill9"))
        rec.stop_if_failed()
    ctx.mark("V4.refusals")
    # ---- refusals
    py = "/usr/bin/python3"
    rc_resume = env.operator([py, "-I", ctx.tree + "/tools/broadcast_run.py", "resume", "--root", ROOT_DIR, "--guard", GUARD])[0]
    rc_short = env.operator([py, "-I", ctx.tree + "/tools/rehearsal_check.py", "release", "--guard", GUARD, "--reason", "test", "--evidence", GUARD + "/evidence"])[0]
    fps = [fingerprint(GUARD + "/TRIPPED")]
    env.sc("stop", m)
    env.sleep(cfg["stale_wait_s"])
    rc_stale = env.operator([py, "-I", ctx.tree + "/tools/rehearsal_check.py", "release", "--guard", GUARD, "--reason", "vtest: this must be refused because the heartbeat is stale", "--evidence", GUARD + "/evidence"])[0]
    fps.append(fingerprint(GUARD + "/TRIPPED"))
    env.sc("start", m)
    ctx.wait(lambda: (ctx.heartbeat_age() or 999) < 20, 90)
    rec.add_all(evaluate_refusals(rc_resume, rc_short, rc_stale, fp0, fps))
    rec.stop_if_failed()
    ctx.mark("V4.release")
    # ---- the documented release
    before = guard_files(env)
    r_rel = env.operator([py, "-I", ctx.tree + "/tools/rehearsal_check.py", "release", "--guard", GUARD, "--reason", "vtest V4: releasing the latch the documented way, evidence kept", "--evidence", GUARD + "/evidence"])[0]
    env.sleep(5)
    held_back = ctx.paused()
    released = new_released_records(before, guard_files(env), env.read)
    r_res = env.operator([py, "-I", ctx.tree + "/tools/broadcast_run.py", "resume", "--root", ROOT_DIR, "--guard", GUARD])[0]
    p0 = ctx.position()
    env.sleep(15)
    lat_after, _ = ctx.latch()
    rec.add_all(evaluate_release(r_rel, r_res, lat_after, released, before, guard_files(env), orig, E.moved(p0, ctx.position(), 5.0), held_back))
    rec.stop_if_failed()
    _immutable_guard_scenario(ctx, rec)
    ctx.mark("V4.second_trip")
    # ---- a second trip
    n_dirs = len([x for x in env.listdir(GUARD + "/evidence") if x.startswith("trip-")])
    latch2, lsum = produce_plateau_latch(ctx, "v4_second")
    n_dirs2 = len([x for x in env.listdir(GUARD + "/evidence") if x.startswith("trip-")])
    kept = len([1 for rel in guard_files(env) if rel.startswith("evidence/released/")])
    ok = bool(latch2) and latch2.get("at") != orig.get("at") and n_dirs2 == n_dirs + 1 and kept >= 1
    rec.check("V4.second_trip", "pass" if ok else "fail", "second latch at %s (first at %s); trip evidence folders %d -> %d; released files kept: %d" % (latch2 and latch2.get("at"), orig.get("at"), n_dirs, n_dirs2, kept), {"latch2": latch2})
    env.write(PREREBOOT, json.dumps(reboot_state(ctx), sort_keys=True))


PREREBOOT = os.path.join(os.path.dirname(GUARD), "state", "v4_prereboot.json")


def reboot_state(ctx):
    return {"fp": fingerprint(GUARD + "/TRIPPED"), "trips": jsonl_read(GUARD + "/trips.jsonl"), "marker": ctx.run_marker(), "boot_id": ctx.env.boot_id(), "latch": (ctx.latch()[0])}


def capture_reboot_state(ctx):
    """Record the state a reboot or hard reset will be judged against, from the machine as it is now, without repeating V4. Needed for the SECOND of the two reboot variants: after the first one the
    old record describes a machine that no longer exists (another boot, another daemon invocation). Valid only for the state V4 leaves: a latch from the monitor, both services running, the daemon held,
    the run marker open for the running daemon."""
    rec = ctx.rec
    ctx.mark("V4.capture_reboot_state")
    latch, _ = ctx.latch()
    marker = ctx.run_marker()
    problems = []
    if not (latch and latch.get("kind") == "memory_high"):
        problems.append("no memory latch (it is what the reboot must not lose)")
    if not (ctx.active("daemon") and ctx.active("monitor")):
        problems.append("daemon and monitor are not both running")
    if not ctx.paused():
        problems.append("the daemon is not held")
    if marker.get("state") != "open":
        problems.append("the run marker is %s, not open for the running daemon" % marker.get("state"))
    rec.check("V4.capture_reboot_state", "fail" if problems else "pass", "; ".join(problems) or "recorded: latch %s, marker for invocation %s, boot %s" % (latch.get("at"), marker.get("invocation_id"), ctx.env.boot_id()), None)
    rec.stop_if_failed()
    ctx.env.write(PREREBOOT, json.dumps(reboot_state(ctx), sort_keys=True))


def run_after_reboot(ctx, mode="clean"):
    """After a machine reboot (mode 'clean': the operator ran `reboot`) or a hard reset (mode 'hard': power cut or reset). The units are never enabled, so nothing is running: THIS STAGE reads the run marker the
    daemon had open, judges it, and only then starts the units itself (judged before any start, because the gate would reopen it). If the units are already active the marker rows are invalid, not failed.
    Then: the latch survived, the daemon is held, the clock is stopped, and the start recorded nothing (clean) or exactly one unaccounted_stop line saying the host restarted (hard)."""
    rec, env = ctx.rec, ctx.env
    ctx.mark("V4.after_reboot", mode=mode)
    pre = json.loads(env.read(PREREBOOT) or "{}")
    if not pre:
        rec.check("V4.after_reboot", "fail", "no pre-reboot fingerprint: run the full V4 first", None)
        return
    fp0, trips0 = pre["fp"], pre["trips"]
    if ctx.active("daemon") or ctx.active("monitor"):
        rec.check("V4.reboot.marker", "invalid", "the units were already running when this stage began, so the run marker the reboot left could not be read before the gate reopened it; the reboot checks of the marker were NOT made. Run this stage BEFORE start-units.", None)
    else:
        marker_now = ctx.run_marker()
        rec.add_all(LC.evaluate_reboot_marker(mode, pre.get("marker") or {}, marker_now, pre.get("boot_id"), env.boot_id()))
        rec.stop_if_failed()
        ctx.start("site", "daemon", "monitor")
    ctx.wait(lambda: ctx.active("daemon") and ctx.active("monitor"), 120)
    allow = ("unaccounted_stop",) if mode == "hard" else ()
    row = _held(ctx, "after_reboot" if mode == "clean" else "after_hard_reset", fp0, trips0, ctx.public_inventory(), rec, None, None, allow)
    rec.add_all(LC.evaluate_after_reboot_start(mode, pre.get("latch"), ctx.latch()[0], LC.new_lines(trips0, ctx.trips()), None, ctx.run_marker(), pre.get("marker") or {}))
