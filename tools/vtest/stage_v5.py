"""V5: the alert drill. Failures of the monitor and of the daemon's start budget are detected without the monitor, recorded, delivered once, and never "fixed" by the alert path.

Runs with the alert package installed beside the candidate (`vtest.py install-alerts`; docs/ALERT_DESIGN_2026-11-12.md). The delivery channel is a file the harness controls: no credential, nothing leaves the machine.
Scenarios, each starting from a healthy baseline and ending back at one: the monitor stopped; the daemon's start budget exhausted (kill -9 until systemd refuses: Result=start-limit-hit); the monitor's own exit 2
(guard folder made immutable while the monitor must write a latch: the q3b mechanism); a monitor failure while the delivery channel is down (the hand-off fails, is recorded, and is retried).
For each: an incident is recorded within the time limit; one message reaches the sink (delivery attempted); the incident is not "received" until an acknowledgement; and after the alert went out the failed unit is
STILL failed for a hold period (the alert path did not restart, reset or release anything). The harness's own recovery then follows the documented order, and the incident clears with one recovery note.
Also recorded, not judged: that the website kept answering while the daemon was failed (website uptime does not prove simulation health), and whether the failure hook or the poll found each failure.
"""
import json
import re

import common as C
import install_alerts as IA
from common import GUARD, UNIT, StageStop

STAGE = "V5"
PLAN = [("V5.installed", "the drop-ins, the poll timer and the release record are in force: OnFailure= names the alert unit on both protected units (read back from systemd)"),
        ("V5.baseline", "healthy machine, timer running: the poll ran recently and no incident was raised (no false alarm)"),
        ("V5.monitor_stopped", "the monitor is stopped: an incident is recorded within the limit, one message reaches the sink, it is not claimed received; the unit stays stopped for the hold; the incident clears after the documented recovery"),
        ("V5.acknowledgement", "only `ack` makes an incident received (run by the harness: this proves the mechanism, not that a person read it)"),
        ("V5.daemon_start_limit", "kill -9 the daemon until systemd refuses (start-limit-hit): the incident is recorded and delivered, the unit stays failed for the hold, the site keeps answering while the simulation is down"),
        ("V5.monitor_exit2", "the guard folder is immutable while the monitor must write a latch: if the monitor exits 2, the incident is recorded and delivered without it; if it does not exit, the check is INVALID, not passed"),
        ("V5.delivery_failure", "the channel is down when the monitor fails: the failure is recorded (attempted_failed), nothing reaches the sink, then the channel returns and the retry delivers once; still not received"),
        ("V5.never_recovers", "in every scenario: the failed unit, the latch, the release records are unchanged for the hold after the alert; no `release`, `resume`, `reset-failed` or restart came from the alert path")]
DEFAULTS = {"qualifying": {"baseline_watch_s": 150, "detect_max_s": 300, "hold_s": 90, "clear_wait_s": 180, "retry_wait_s": 150, "poll_max_age_s": 150, "kill_wait_s": 45, "exit2_wait_s": 420, "overrun_rate": 5e6},
            "smoke": {"baseline_watch_s": 90, "detect_max_s": 300, "hold_s": 60, "clear_wait_s": 180, "retry_wait_s": 150, "poll_max_age_s": 150, "kill_wait_s": 45, "exit2_wait_s": 420, "overrun_rate": 5e6}}
UNIT_PROPS = ("ActiveState", "SubState", "Result", "InvocationID", "NRestarts", "MainPID")


# ------------------------------------------------------------------------------------------------------------------------------------------------ reading
def read_incidents(env):
    out = []
    for n in env.listdir(C.ALERT_STATE + "/incidents"):
        if n.endswith(".json"):
            try:
                out.append(json.loads(env.read(C.ALERT_STATE + "/incidents/" + n) or ""))
            except ValueError:
                pass
    return sorted(out, key=lambda i: (i.get("first_seen") or 0, i.get("id")))


def read_sink(env):
    return env.read(C.ALERT_SINK, "") or ""


def read_poll(env):
    try:
        return json.loads(env.read(C.ALERT_STATE + "/poll.json") or "")
    except ValueError:
        return None


def messages_for(sink, iid):
    """How many messages in the sink are about this incident (each carries one `Incident: <id>` line)."""
    return len(re.findall(r"^Incident: %s$" % re.escape(iid), sink, re.M))


def unescape_unit(text):
    return re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), text or "")


def row(cid, status, detail, data=None, required=True):
    return (cid, status, detail, data, required)


# ------------------------------------------------------------------------------------------------------------------------------------------ evaluators
def evaluate_installed(daemon, monitor, timer, prefix, record_ok):
    rows = []
    for role, show, unit in (("daemon", daemon, UNIT["daemon"]), ("monitor", monitor, UNIT["monitor"])):
        want = "%salert@%s" % (prefix, unit)
        hook = unescape_unit(show.get("OnFailure", ""))
        dropin = "50-dfl-alert.conf" in unescape_unit(show.get("DropInPaths", ""))
        ok = want in hook and dropin
        rows.append(row("V5.installed.%s_dropin" % role, "pass" if ok else "fail",
                        "%s: OnFailure=%r, drop-in listed: %s (want the failure hook %s from the drop-in)" % (unit, hook, dropin, want), {"OnFailure": hook, "DropInPaths": show.get("DropInPaths")}))
    ok = timer.get("ActiveState") == "active"
    rows.append(row("V5.installed.poll_timer", "pass" if ok else "fail", "the poll timer is %s" % timer.get("ActiveState"), timer))
    rows.append(row("V5.installed.release_record", "pass" if record_ok else "fail", "the install record with the exact revisions, hashes and effective settings %s" % ("exists" if record_ok else "is MISSING"), None))
    return rows


def evaluate_baseline(incidents, poll, now, watch_s, max_age_s=150):
    rows = []
    age = None if not poll else now - poll.get("last_poll", 0)
    ok = age is not None and age <= max_age_s
    rows.append(row("V5.baseline.poll_ran", "pass" if ok else "fail", "the poll last ran %s s ago (limit %d): the timer %s" % ("?" if age is None else int(age), max_age_s, "works" if ok else "is NOT running the poll"), poll))
    rows.append(row("V5.baseline.no_false_alarm", "pass" if not incidents else "fail",
                    "incidents raised during %d s of healthy running: %s" % (watch_s, [i.get("event") for i in incidents] or "none"), {"incidents": [i.get("id") for i in incidents]}))
    return rows


def evaluate_detection(scn, events, new, sink, t_fault, max_s, alert_journal="", expect_delivery=True):
    """`new`: the incidents that appeared since the fault was injected. -> rows."""
    pre = "V5.%s." % scn
    match = [i for i in new if i.get("event") in events and (i.get("first_seen") or 0) >= t_fault - 5]
    rows = []
    rows.append(row(pre + "recorded", "pass" if match else "fail", ("incident %s (%s) recorded" % (match[0]["id"], match[0]["event"])) if match else
                    "no incident of kind %s was recorded; incidents present: %s" % (list(events), [i.get("event") for i in new] or "none"), {"incidents": [i.get("id") for i in new]}))
    if not match:
        return rows
    first = match[0]
    late = first["first_seen"] - t_fault
    rows.append(row(pre + "within_time", "pass" if late <= max_s else "fail", "recorded %.0f s after the fault (limit %d s)" % (late, max_s), {"seconds": round(late, 1)}))
    if expect_delivery:
        n_msgs = messages_for(sink, first["id"])
        dup = [i for i in match]
        ok = len(dup) == 1 and n_msgs == 1 and first["delivery"]["state"] == "attempted_ok"
        rows.append(row(pre + "delivered_once", "pass" if ok else "fail",
                        "%d incident(s) for this condition, %d message(s) in the sink for it, hand-off state %s (want exactly one incident, one message, attempted_ok)" % (len(dup), n_msgs, first["delivery"]["state"]),
                        {"messages": n_msgs, "state": first["delivery"]["state"]}))
    rec_ok = all(i.get("notification_received") is None for i in match)
    rows.append(row(pre + "not_claimed_received", "pass" if rec_ok else "fail", "no incident claims to be received before anyone acknowledged it: %s" % rec_ok, None))
    hook = bool(alert_journal) and "failure alert" in alert_journal and first["unit"] in alert_journal
    rows.append(row(pre + "detection_path", "info", ("the failure hook ran the alert unit for %s (the incident may also have been seen by the poll)" % first["unit"]) if hook else
                    "poll only: the alert unit was not started by the failure hook for %s in the journal; the poll found it. Whether the hook fires in this case is recorded, not judged" % first["unit"], {"journal_has_hook": hook}, False))
    return rows


def evaluate_never_recovers(scn, before, after, hold_s):
    pre = "V5.%s." % scn
    bad = []
    for k in ("ActiveState", "SubState", "Result", "InvocationID", "NRestarts", "MainPID"):
        if before["unit"].get(k) != after["unit"].get(k):
            bad.append("the failed unit's %s changed %r -> %r" % (k, before["unit"].get(k), after["unit"].get(k)))
    if before.get("latch_sha") != after.get("latch_sha"):
        bad.append("the latch changed: %s -> %s" % (before.get("latch_sha"), after.get("latch_sha")))
    if after.get("released", 0) != before.get("released", 0):
        bad.append("the number of release records changed %s -> %s" % (before.get("released"), after.get("released")))
    if after.get("trips", 0) < before.get("trips", 0):
        bad.append("trips.jsonl lost lines (%s -> %s)" % (before.get("trips"), after.get("trips")))
    rows = [row(pre + "alert_did_not_recover", "fail" if bad else "pass",
                "; ".join(bad) if bad else "for %d s after the alert went out the failed unit, the latch and the release records were unchanged: the alert path restarted, reset and released nothing" % hold_s, {"before": before, "after": after})]
    rows.append(row(pre + "pause_observed", "info", "PAUSE before: %s, after: %s (the daemon pauses itself when the monitor's heartbeat goes stale: its own rule, not the alert path's)" % (before.get("paused"), after.get("paused")), None, False))
    return rows


def evaluate_retry(scn, mid, sink_mid, final, sink_final):
    pre = "V5.%s." % scn
    rows = []
    attempts = mid.get("delivery", {}).get("attempts", [])
    ok = mid["delivery"]["state"] == "attempted_failed" and bool(attempts) and not attempts[0]["ok"] and messages_for(sink_mid, mid["id"]) == 0
    rows.append(row(pre + "failure_recorded", "pass" if ok else "fail", "with the channel down: hand-off state %s, %d attempt(s), %d message(s) in the sink (want attempted_failed, at least one attempt, none in the sink)" %
                    (mid["delivery"]["state"], len(attempts), messages_for(sink_mid, mid["id"])), {"attempts": attempts}))
    retries = [a for a in final["delivery"]["attempts"] if a["kind"] == "retry" and a["ok"]]
    rows.append(row(pre + "retried_and_delivered", "pass" if final["delivery"]["state"] == "attempted_ok" and retries else "fail",
                    "after the channel returned: hand-off state %s, successful retries %d" % (final["delivery"]["state"], len(retries)), {"attempts": final["delivery"]["attempts"]}))
    n = messages_for(sink_final, final["id"])
    rows.append(row(pre + "delivered_once_after_retry", "pass" if n == 1 else "fail", "%d message(s) in the sink for the incident (want exactly one)" % n, None))
    rows.append(row(pre + "still_not_received", "pass" if final.get("notification_received") is None else "fail", "delivery succeeded but nobody acknowledged: notification_received is %r" % (final.get("notification_received"),), None))
    return rows


def evaluate_ack(scn, before, after):
    ok = before.get("notification_received") is None and bool((after.get("notification_received") or {}).get("by"))
    return [row("V5.%s.received_only_after_ack" % scn, "pass" if ok else "fail",
                ("notification_received was empty until `ack` and is set afterwards, by %s: acknowledged by the harness, not a person, so this proves the mechanism only" % (after.get("notification_received") or {}).get("by")) if ok else
                "before ack: %r, after ack: %r (want empty, then set)" % (before.get("notification_received"), after.get("notification_received")), None)]


def evaluate_monitor_fault(sh):
    """The monitor's state after the exit-2 drill's fault -> rows. The drill needs a monitor that FAILED; whether the status is the designed 2 is a finding about the candidate, recorded for review."""
    if sh.get("ActiveState") != "failed":
        return [row("V5.monitor_exit2.fault_produced", "invalid", "the monitor did not fail (ActiveState=%s, Result=%s): the exit-2 case was NOT tested" % (sh.get("ActiveState"), sh.get("Result")), {"monitor": sh})]
    rows = [row("V5.monitor_exit2.fault_produced", "pass", "the monitor failed with the guard folder unwritable: Result=%s, exit status %s" % (sh.get("Result"), sh.get("ExecMainStatus")), {"monitor": sh})]
    if sh.get("ExecMainStatus") == 2:
        rows.append(row("V5.monitor_exit2.exit_status", "info", "exit status 2: the designed exit when the monitor cannot write its latch", {"monitor": sh}, False))
    else:
        rows.append(row("V5.monitor_exit2.exit_status", "review", "the monitor failed with exit status %s, not the 2 the design predicts (docs/RESTART_BUDGET_FINDING_2026-11-12.md): recorded for review; the alert is judged either way" % sh.get("ExecMainStatus"), {"monitor": sh}, False))
    return rows


def evaluate_site_vs_simulation(http_code, pos_a, pos_b, daemon_active):
    cid = "V5.site_vs_simulation"
    if http_code is None or daemon_active:
        return [row(cid, "invalid", "the demonstration was not made: site answered %r, daemon active %s" % (http_code, daemon_active), None, False)]
    moved = pos_a is not None and pos_b is not None and abs(pos_b - pos_a) > 1.5
    if moved:
        return [row(cid, "invalid", "the status clock advanced (%s -> %s) while the daemon was down, so the frozen-clock demonstration cannot be made here" % (pos_a, pos_b), None, False)]
    return [row(cid, "info", "the website answered HTTP %s while the daemon was failed and the broadcast clock stood still (%s -> %s): website uptime does not prove simulation health" % (http_code, pos_a, pos_b),
                {"http": http_code, "pos": [pos_a, pos_b]}, False)]


# ------------------------------------------------------------------------------------------------------------------------------------------- machine
def snapshot(ctx, key):
    """The state that would show an alert path acting: the failed unit, the latch, the release records, the trips. Read-only."""
    env = ctx.env
    sh = ctx.show(key, UNIT_PROPS)
    _, sha = ctx.latch()
    released = [n for n in env.listdir(GUARD + "/evidence/released")] if env.exists(GUARD + "/evidence/released") else []
    return {"unit": {k: sh.get(k) for k in UNIT_PROPS}, "latch_sha": sha, "released": len(released), "trips": len(ctx.trips()), "paused": ctx.paused()}


def alert_journal(ctx, since):
    rc, out, _ = ctx.env.run(["journalctl", "-u", "%salert@*" % C.PREFIX, "--since", "@%d" % int(since), "--no-pager", "-o", "cat"], timeout=60)
    return out or ""


def _new_since(ctx, old_ids):
    return [i for i in read_incidents(ctx.env) if i["id"] not in old_ids]


def _wait_attempted(ctx, iid, limit=30):
    def done():
        for i in read_incidents(ctx.env):
            if i["id"] == iid and i["delivery"]["attempts"]:
                return i
        return None
    return ctx.wait(done, limit, every=2.0)[0]


def _ack_command(iid):
    env_file, tool = C.ETC + "/alert.env", C.ALERT_BIN + "/dfl_alert.py"
    sh = "set -a; . %s; DFL_ALERT_DIR=%s; DFL_ALERT_DAEMON_UNIT=%s; DFL_ALERT_MONITOR_UNIT=%s; exec /usr/bin/python3 %s ack %s --by vtest-harness --via harness" % (
        env_file, C.ALERT_STATE, UNIT["daemon"], UNIT["monitor"], tool, iid)
    return ["/bin/sh", "-c", sh]


def _fresh_heartbeat(ctx, limit=90):
    return ctx.wait(lambda: (ctx.heartbeat_age() or 999) < 20, limit)[0]


def _back_to_baseline(ctx, rec, scn):
    """The documented order, after the alert has been judged: units running (the monitor first, a fresh heartbeat seen), then release/resume if a latch exists, then `resume` if only a PAUSE is left."""
    try:
        ctx.ensure_baseline("vtest V5 %s: restoring the baseline after the drill, the documented way" % scn)
        if ctx.paused():
            rc = ctx.env.operator(["/usr/bin/python3", "-I", ctx.tree + "/tools/broadcast_run.py", "resume", "--root", C.ROOT_DIR, "--guard", GUARD])[0]
            ctx.wait(lambda: not ctx.paused(), 30)
            ctx.mark("V5." + scn, phase="resume_after_self_pause", rc=rc)
    except StageStop as e:
        rec.check("V5.%s.restore" % scn, "fail", "the machine could not be brought back to the baseline: %s" % e)


def _clears(ctx, rec, scn, match_events, wait_s):
    def cleared():
        for i in read_incidents(ctx.env):
            if i.get("event") in match_events and i.get("cleared"):
                return i
        return None
    inc, _ = ctx.wait(cleared, wait_s, every=5.0)
    rec.check("V5.%s.clears_after_a_person_acts" % scn, "pass" if inc else "fail",
              ("the incident cleared after the documented recovery (recovery note handed off: %s); the alert tool changed nothing" % (inc["cleared"].get("notified"),)) if inc else
              "the incident did not clear within %d s of the recovery" % wait_s, None)


def _hold_and_judge(ctx, rec, scn, failed_key):
    hold = ctx.cfg["hold_s"]
    before = snapshot(ctx, failed_key)
    ctx.env.sleep(hold)
    after = snapshot(ctx, failed_key)
    rec.add_all(evaluate_never_recovers(scn, before, after, hold))
    return before, after


def scenario_monitor_stopped(ctx, rec, fail_delivery=False):
    env, cfg = ctx.env, ctx.cfg
    scn = "delivery_failure" if fail_delivery else "monitor_stopped"
    ctx.mark("V5." + scn, phase="start")
    ctx.ensure_baseline("vtest V5 %s: start from a healthy baseline" % scn)
    old = {i["id"] for i in read_incidents(env)}
    sink0 = read_sink(env)
    if fail_delivery:
        env.write(C.ALERT_MODE, "fail\n", 0o600)
    t0 = ctx.now()
    mid_inc, new = None, []
    try:
        env.sc("stop", UNIT["monitor"])
        ctx.mark("V5." + scn, phase="fault_injected")
        events = ("monitor_not_running", "monitor_failed")

        def seen():
            return [i for i in _new_since(ctx, old) if i.get("event") in events]
        got, waited = ctx.wait(seen, cfg["detect_max_s"], every=5.0)
        ctx.mark("V5." + scn, phase="detected", seen=bool(got), waited=round(waited, 1))
        if got:
            _wait_attempted(ctx, got[0]["id"])
        new = _new_since(ctx, old)
        sink_new = read_sink(env)[len(sink0):]
        rec.add_all(evaluate_detection(scn, events, new, sink_new, t0, cfg["detect_max_s"], alert_journal(ctx, t0 - 2), expect_delivery=not fail_delivery))
        if got:
            _hold_and_judge(ctx, rec, scn, "monitor")
            if fail_delivery:
                mid_inc = [i for i in read_incidents(env) if i["id"] == got[0]["id"]][0]
                sink_mid = read_sink(env)[len(sink0):]
                env.write(C.ALERT_MODE, "ok\n", 0o600)
                done, _ = ctx.wait(lambda: [i for i in read_incidents(env) if i["id"] == got[0]["id"] and i["delivery"]["state"] == "attempted_ok"], cfg["retry_wait_s"], every=5.0)
                final = [i for i in read_incidents(env) if i["id"] == got[0]["id"]][0]
                rec.add_all(evaluate_retry(scn, mid_inc, sink_mid, final, read_sink(env)[len(sink0):]))
            else:
                before = [i for i in read_incidents(env) if i["id"] == got[0]["id"]][0]
                env.run(_ack_command(got[0]["id"]))
                after = [i for i in read_incidents(env) if i["id"] == got[0]["id"]][0]
                rec.add_all(evaluate_ack("acknowledgement", before, after))
    finally:
        env.write(C.ALERT_MODE, "ok\n", 0o600)
        env.sc("reset-failed", UNIT["monitor"])
        env.sc("start", UNIT["monitor"])
        ctx.mark("V5." + scn, phase="monitor_restored_by_harness")
        fresh = _fresh_heartbeat(ctx)
        rec.check("V5.%s.restore_heartbeat" % scn, "pass" if fresh else "fail", "the monitor's heartbeat is fresh again after the documented start: %s" % bool(fresh), None)
        _back_to_baseline(ctx, rec, scn)
    if new:
        _clears(ctx, rec, scn, ("monitor_not_running", "monitor_failed"), cfg["clear_wait_s"])
    rec.stop_if_failed()


def _http_status(port):
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=10) as r:
            return r.status
    except Exception:
        return None


def scenario_daemon_start_limit(ctx, rec):
    env, cfg = ctx.env, ctx.cfg
    scn = "daemon_start_limit"
    ctx.mark("V5." + scn, phase="start")
    ctx.ensure_baseline("vtest V5 %s: start from a healthy baseline (full start budget)" % scn)
    old = {i["id"] for i in read_incidents(env)}
    sink0 = read_sink(env)
    t0 = ctx.now()
    d = UNIT["daemon"]
    kills, limit_hit = [], False
    try:
        for n in range(1, 7):
            sh = ctx.show("daemon", UNIT_PROPS)
            if sh.get("ActiveState") == "failed" or sh.get("Result") == "start-limit-hit":
                limit_hit = True
                break
            env.sc("kill", d, "--signal=SIGKILL", "--kill-whom=main")
            kills.append({"n": n, "t": round(ctx.now() - t0, 1), "NRestarts_before": sh.get("NRestarts")})
            ctx.wait(lambda: ctx.show("daemon", UNIT_PROPS).get("ActiveState") in ("active", "failed"), cfg["kill_wait_s"], every=2.0)
            env.sleep(3)
        sh = ctx.show("daemon", UNIT_PROPS)
        limit_hit = limit_hit or sh.get("Result") == "start-limit-hit"
        ctx.mark("V5." + scn, phase="after_kills", kills=len(kills), limit_hit=limit_hit, result=sh.get("Result"))
        if not limit_hit:
            rec.check("V5.%s.fault_produced" % scn, "invalid", "systemd did not refuse a start after %d kill -9s (Result=%s): the start-limit case was NOT tested" % (len(kills), sh.get("Result")), {"kills": kills})
        else:
            rec.check("V5.%s.fault_produced" % scn, "pass", "the daemon's start budget was exhausted by %d kill -9s: Result=start-limit-hit" % len(kills), {"kills": kills})
            t_fault = t0
            got, waited = ctx.wait(lambda: [i for i in _new_since(ctx, old) if i.get("event") == "daemon_start_limit_hit"], cfg["detect_max_s"], every=5.0)
            if got:
                _wait_attempted(ctx, got[0]["id"])
            new = _new_since(ctx, old)
            rec.add_all(evaluate_detection(scn, ("daemon_start_limit_hit",), new, read_sink(env)[len(sink0):], t_fault, cfg["detect_max_s"], alert_journal(ctx, t0 - 2)))
            warn = [i for i in new if i.get("event") == "daemon_budget_low"]
            rec.check("V5.%s.budget_warning" % scn, "info", "the 'budget nearly used' warning was %s before the limit was hit (NRestarts is only a heuristic for budget use: recorded, not judged)" % ("raised" if warn else "NOT raised"), {"warnings": [w["id"] for w in warn]}, False)
            if got:
                a = ctx.position()
                _hold_and_judge(ctx, rec, scn, "daemon")
                rec.add_all(evaluate_site_vs_simulation(_http_status(C.PORT), a, ctx.position(), ctx.active("daemon")))
    finally:
        env.sc("reset-failed", d)
        env.sc("start", d)
        ctx.mark("V5." + scn, phase="daemon_restored_by_harness")
        _fresh_heartbeat(ctx)
        _back_to_baseline(ctx, rec, scn)
    if limit_hit:
        _clears(ctx, rec, scn, ("daemon_start_limit_hit", "daemon_failed"), cfg["clear_wait_s"])
    rec.stop_if_failed()


def scenario_monitor_exit2(ctx, rec):
    """The q3b mechanism: with the guard folder immutable, a memory trip makes the monitor try to write its latch, fail, and exit 2 (by design). If the monitor does not exit, nothing about this case was tested."""
    env, cfg = ctx.env, ctx.cfg
    scn = "monitor_exit2"
    ctx.mark("V5." + scn, phase="start")
    ctx.ensure_baseline("vtest V5 %s: start from a healthy baseline" % scn)
    old = {i["id"] for i in read_incidents(env)}
    sink0 = read_sink(env)
    mx = ctx.limit_of("daemon")
    smp = ctx.sampler("daemon", "v5_exit2_cgroup")
    env.sleep(10)
    import evaluate as E
    base = E.median([r["pinned"] for r in E.readable(smp.records())]) or 0
    ok, why = env.immutable(GUARD, True)
    ctx.mark("V5." + scn, phase="fault_on", ok=ok, detail=why)
    if not ok:
        smp.stop()
        rec.check("V5.%s.fault_produced" % scn, "invalid", "could not make the guard folder unwritable (%s): the monitor's exit 2 was NOT tested" % why, {"detail": why})
        rec.stop_if_failed()
        return
    t0 = ctx.now()
    ld, failed, new = None, None, []
    try:
        target = int(0.93 * mx - base)
        ld = ctx.loader("anon", "daemon", "v5_exit2_anon", target_bytes=target, rate_bps=cfg["overrun_rate"])
        failed, waited = ctx.wait(lambda: ctx.show("monitor", UNIT_PROPS).get("ActiveState") == "failed", target / cfg["overrun_rate"] + cfg["exit2_wait_s"], every=2.0)
        ctx.mark("V5." + scn, phase="monitor_state", failed=bool(failed), waited=round(waited, 1))
    finally:
        if ld is not None:
            ld.stop()
        smp.stop()
        ok2, why2 = None, None
    sh = ctx.show("monitor", UNIT_PROPS + ("ExecMainStatus",))
    rec.add_all(evaluate_monitor_fault(sh))
    if not failed:
        env.immutable(GUARD, False)
        _back_to_baseline(ctx, rec, scn)
        rec.stop_if_failed()
        return
    try:
        got, _ = ctx.wait(lambda: [i for i in _new_since(ctx, old) if i.get("event") in ("monitor_failed", "monitor_not_running")], cfg["detect_max_s"], every=5.0)
        if got:
            _wait_attempted(ctx, got[0]["id"])
        new = _new_since(ctx, old)
        rec.add_all(evaluate_detection(scn, ("monitor_failed", "monitor_not_running"), new, read_sink(env)[len(sink0):], t0, cfg["detect_max_s"] + 120, alert_journal(ctx, t0 - 2)))
        if got:
            _hold_and_judge(ctx, rec, scn, "monitor")
    finally:
        ok2, why2 = env.immutable(GUARD, False)
        ctx.mark("V5." + scn, phase="fault_off", ok=ok2, detail=why2)
        if not ok2:
            raise StageStop("the guard folder is STILL IMMUTABLE and could not be restored (%s): run `chattr -i %s` by hand" % (why2, GUARD))
        env.sc("reset-failed", UNIT["monitor"])
        env.sc("start", UNIT["monitor"])
        _fresh_heartbeat(ctx)
        _back_to_baseline(ctx, rec, scn)
    if new:
        _clears(ctx, rec, scn, ("monitor_failed", "monitor_not_running"), cfg["clear_wait_s"])
    rec.stop_if_failed()


def save_evidence(ctx):
    env = ctx.env
    try:
        ctx.save("incidents.json", read_incidents(env))
        ctx.save("sink.log", read_sink(env))
        ctx.save("alert_journal.txt", alert_journal(ctx, 0) or "(empty)")
        ctx.save("alerts_install.json", env.read(IA.record_path()) or "UNAVAILABLE: no alert install record")
    except OSError:
        pass


def run(ctx):
    cfg, rec, env = ctx.cfg, ctx.rec, ctx.env
    ctx.mark("V5.installed")
    record = json.loads(env.read(IA.record_path()) or "{}")
    if not record:
        rec.check("V5.installed.release_record", "invalid", "no alert install record: run `vtest.py install-alerts` first (V5 needs the package installed beside the candidate)", None)
        raise StageStop("the alert package is not installed")
    try:
        show = {k: ctx.show(k, ("ActiveState", "OnFailure", "DropInPaths", "FragmentPath")) for k in ("daemon", "monitor")}
        rc, so, _ = env.sc("show", C.PREFIX + "alert-poll.timer", "-pActiveState", "-pSubState")
        timer = dict(l.split("=", 1) for l in (so or "").splitlines() if "=" in l)
        rec.add_all(evaluate_installed(show["daemon"], show["monitor"], timer, C.PREFIX, bool(record.get("effective"))))
        rec.check("V5.installed.revisions", "info", "git: %s; package manifest sha256 %s; tool installed sha256 %s" % (record.get("git"), record.get("package_manifest_sha256"), record.get("tool_installed_sha256")), record.get("git"), False)
        rec.stop_if_failed()
        ctx.ensure_baseline("vtest V5: start from a healthy baseline")
        ctx.mark("V5.baseline")
        t0 = ctx.now()
        old = {i["id"] for i in read_incidents(env)}
        env.sleep(cfg["baseline_watch_s"])
        ctx.wait(lambda: read_poll(env), 90)
        rec.add_all(evaluate_baseline(_new_since(ctx, old), read_poll(env), ctx.now(), cfg["baseline_watch_s"], cfg["poll_max_age_s"]))
        rec.stop_if_failed()
        scenario_monitor_stopped(ctx, rec, fail_delivery=False)
        scenario_daemon_start_limit(ctx, rec)
        scenario_monitor_exit2(ctx, rec)
        scenario_monitor_stopped(ctx, rec, fail_delivery=True)
    finally:
        save_evidence(ctx)
