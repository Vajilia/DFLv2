"""V4: a trip latch that already exists survives restarts and prevents resumption, and is cleared only by the documented procedure.

Starts from a REAL latch (the monitor's own memory trip, produced the way V2a does; or the one V2a left, if it is the monitor's memory trip). Then, after each disturbance, the latch's
sha256, size and mtime must be identical, the daemon must still be paused with its clock stopped and its public files unchanged, and `resume` must still be refused.
`--with-reboot` adds a pause for a machine reboot (units are not enabled, so after the reboot the operator starts them with `vtest.py start-units`, then `run V4 --after-reboot`).
"""
import json
import os

import evaluate as E
from common import GUARD, ROOT_DIR, UNIT, sha256_file, jsonl_read
from scenarios import produce_plateau_latch

STAGE = "V4"
STEPS = ("daemon_restart", "monitor_restart", "both_daemon_first", "both_monitor_first", "daemon_kill9", "monitor_kill9")
PLAN = [("V4.latch", "use or produce a real memory latch; record its sha256, size, mtime and the evidence it came with"),
        ("V4.disturb", "after each of: daemon restart, monitor restart, both (two orders), daemon kill -9, monitor kill -9: latch identical, daemon paused, clock stopped, public files unchanged, resume refused"),
        ("V4.refusals", "resume refused (rc 3); release with a short reason refused (rc 2); release with a stale heartbeat refused (rc 4); the latch intact after each"),
        ("V4.release", "release (rc 0): the latch moves into evidence/released/ and nothing is deleted; resume (rc 0); the clock advances"),
        ("V4.second_trip", "produce a second latch: a second evidence folder appears and the first stays in released/"),
        ("V4.after_reboot", "optional (--with-reboot / --after-reboot): the same preservation checks after a machine reboot")]
DEFAULTS = {"qualifying": {"settle_s": 25, "watch_s": 15, "stale_wait_s": 40}, "smoke": {"settle_s": 20, "watch_s": 10, "stale_wait_s": 40}}


def fingerprint(path):
    try:
        st = os.stat(path)
        return {"sha256": sha256_file(path), "size": st.st_size, "mtime": st.st_mtime}
    except OSError:
        return None


def evaluate_disturbance(step, fp0, fp1, paused, pos_a, pos_b, inv0, inv1, resume_rc, units_active, trips0, trips1):
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
    if trips1[:len(trips0)] != trips0:
        problems.append("trips.jsonl lost or rewrote an earlier line")
    return ("V4.disturb.%s" % step, "fail" if problems else "pass", "latch identical, daemon held, clock stopped, public files unchanged, resume refused" if not problems else "; ".join(problems), {"problems": problems, "fp": fp1})


def evaluate_refusals(rc_resume, rc_short, rc_stale, fp0, fps):
    rows = []
    rows.append(("V4.refusals.resume", "pass" if rc_resume == 3 else "fail", "resume with a latch present returned %s (expected 3)" % rc_resume, None))
    rows.append(("V4.refusals.release_short_reason", "pass" if rc_short == 2 else "fail", "release with a 4-character reason returned %s (expected 2)" % rc_short, None))
    rows.append(("V4.refusals.release_stale_heartbeat", "pass" if rc_stale == 4 else "fail", "release while the monitor's heartbeat was stale returned %s (expected 4)" % rc_stale, None))
    rows.append(("V4.refusals.latch_untouched", "pass" if all(f == fp0 for f in fps) else "fail", "latch fingerprint identical after each refusal: %s" % all(f == fp0 for f in fps), None))
    return rows


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


def _held(ctx, label, fp0, trips0, inv0, rec):
    ctx.env.sleep(ctx.cfg["settle_s"])
    pos_a, ta = ctx.position(), ctx.now()
    ctx.env.sleep(ctx.cfg["watch_s"])
    rc_resume = ctx.env.run(["/usr/bin/python3", "-I", ctx.tree + "/tools/broadcast_run.py", "resume", "--root", ROOT_DIR, "--guard", GUARD])[0]
    row = evaluate_disturbance(label, fp0, fingerprint(GUARD + "/TRIPPED"), ctx.paused(), pos_a, ctx.position(), inv0, ctx.public_inventory(), rc_resume,
                               ctx.active("daemon") and ctx.active("monitor"), trips0, jsonl_read(GUARD + "/trips.jsonl"))
    rec.add_all([row])
    ctx.mark("V4.disturb", step=label, status=row[1])
    return row


def preservation_state(ctx):
    return fingerprint(GUARD + "/TRIPPED"), jsonl_read(GUARD + "/trips.jsonl"), ctx.public_inventory()


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
        row = _held(ctx, step, fp0, trips0, inv0, rec)
        if row[1] != "pass":
            rec.stop_if_failed()
    ctx.mark("V4.refusals")
    # ---- refusals
    py = "/usr/bin/python3"
    rc_resume = env.run([py, "-I", ctx.tree + "/tools/broadcast_run.py", "resume", "--root", ROOT_DIR, "--guard", GUARD])[0]
    rc_short = env.run([py, "-I", ctx.tree + "/tools/rehearsal_check.py", "release", "--guard", GUARD, "--reason", "test", "--evidence", GUARD + "/evidence"])[0]
    fps = [fingerprint(GUARD + "/TRIPPED")]
    env.sc("stop", m)
    env.sleep(cfg["stale_wait_s"])
    rc_stale = env.run([py, "-I", ctx.tree + "/tools/rehearsal_check.py", "release", "--guard", GUARD, "--reason", "vtest: this must be refused because the heartbeat is stale", "--evidence", GUARD + "/evidence"])[0]
    fps.append(fingerprint(GUARD + "/TRIPPED"))
    env.sc("start", m)
    ctx.wait(lambda: (ctx.heartbeat_age() or 999) < 20, 90)
    rec.add_all(evaluate_refusals(rc_resume, rc_short, rc_stale, fp0, fps))
    rec.stop_if_failed()
    ctx.mark("V4.release")
    # ---- the documented release
    before = guard_files(env)
    r_rel = env.run([py, "-I", ctx.tree + "/tools/rehearsal_check.py", "release", "--guard", GUARD, "--reason", "vtest V4: releasing the latch the documented way, evidence kept", "--evidence", GUARD + "/evidence"])[0]
    env.sleep(5)
    held_back = ctx.paused()
    released = [json.loads(env.read(p)) for rel, p in sorted(guard_files(env).items()) if rel.startswith("evidence/released/")]
    r_res = env.run([py, "-I", ctx.tree + "/tools/broadcast_run.py", "resume", "--root", ROOT_DIR, "--guard", GUARD])[0]
    p0 = ctx.position()
    env.sleep(15)
    lat_after, _ = ctx.latch()
    rec.add_all(evaluate_release(r_rel, r_res, lat_after, released, before, guard_files(env), orig, E.moved(p0, ctx.position(), 5.0), held_back))
    rec.stop_if_failed()
    ctx.mark("V4.second_trip")
    # ---- a second trip
    n_dirs = len([x for x in env.listdir(GUARD + "/evidence") if x.startswith("trip-")])
    latch2, lsum = produce_plateau_latch(ctx, "v4_second")
    n_dirs2 = len([x for x in env.listdir(GUARD + "/evidence") if x.startswith("trip-")])
    kept = len([1 for rel in guard_files(env) if rel.startswith("evidence/released/")])
    ok = bool(latch2) and latch2.get("at") != orig.get("at") and n_dirs2 == n_dirs + 1 and kept >= 1
    rec.check("V4.second_trip", "pass" if ok else "fail", "second latch at %s (first at %s); trip evidence folders %d -> %d; released files kept: %d" % (latch2 and latch2.get("at"), orig.get("at"), n_dirs, n_dirs2, kept), {"latch2": latch2})
    state = {"fp": fingerprint(GUARD + "/TRIPPED"), "trips": jsonl_read(GUARD + "/trips.jsonl")}
    env.write(os.path.join(os.path.dirname(GUARD), "state", "v4_prereboot.json"), json.dumps(state, sort_keys=True))


def run_after_reboot(ctx):
    """After a machine reboot the units are not running (they are never enabled). The operator starts them with `vtest.py start-units`; this checks what survived."""
    rec, env = ctx.rec, ctx.env
    ctx.mark("V4.after_reboot")
    pre = json.loads(env.read(os.path.join(os.path.dirname(GUARD), "state", "v4_prereboot.json")) or "{}")
    if not pre:
        rec.check("V4.after_reboot", "fail", "no pre-reboot fingerprint: run the full V4 first", None)
        return
    fp0, trips0 = pre["fp"], pre["trips"]
    ctx.wait(lambda: ctx.active("daemon") and ctx.active("monitor"), 120)
    row = _held(ctx, "after_reboot", fp0, trips0, ctx.public_inventory(), rec)
