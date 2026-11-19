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

import attribution as AT
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
        ("V5.never_recovers", "in every scenario: the failed unit, the latch, the release records are unchanged for the hold after the alert; no `release`, `resume`, `reset-failed` or restart came from the alert path; the one allowed change is a latch the daemon PROVABLY wrote itself (its stale-heartbeat rule, kind monitor_silent) with the preserved evidence reproducing it"),
        ("V5.alert_sandbox", "the alert units' effective sandbox (User, ProtectSystem, ReadWritePaths, ...) is preserved, and ProtectSystem=strict with no writable path covering the guard folder is verified"),
        ("V5.attribution_preserved", "in every scenario, at the end of the hold and BEFORE any recovery: the latch file, trips.jsonl, PAUSE, the monitor's heartbeat time, the guard listing and timestamped alert, poll, daemon and monitor journals are saved; a gap fails the scenario and makes the archive incomplete")]
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


def hook_ran(journal, unit):
    """Did the failure hook start the alert instance for `unit` (it has journal entries in the window)? True / False, or None when the alert journal was not read: unknown is not "no"."""
    if not isinstance(journal, dict) or journal.get("state") not in (AT.PRESENT, AT.EMPTY):
        return None
    inst = (journal.get("instances") or {}).get(AT.alert_instance_for(unit))
    if not isinstance(inst, dict) or inst.get("state") not in (AT.PRESENT, AT.EMPTY):
        return None
    return inst["state"] == AT.PRESENT


def evaluate_detection(scn, events, new, sink, t_fault, max_s, alert_journal=None, expect_delivery=True):
    """`new`: the incidents that appeared since the fault was injected. `alert_journal`: the result of collect_alert_journal (or None). -> rows."""
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
    hook = hook_ran(alert_journal, first["unit"])
    if hook is None:
        why = ((alert_journal or {}).get("detail") if isinstance(alert_journal, dict) else None) or "no journal record"
        msg = "undetermined: the alert units' journal could not be read (%s), so whether the failure hook ran is not known; the incident was recorded either way" % str(why)[:160]
    elif hook:
        msg = "the failure hook ran the alert unit for %s (the incident may also have been seen by the poll)" % first["unit"]
    else:
        msg = ("poll only: the journal was read (the unit index listed, the alert instance for %s queried by exact name, exit 0) and it has no entry for it in the window; the poll found the incident. "
               "Whether the hook fires in this case is recorded, not judged" % first["unit"])
    rows.append(row(pre + "detection_path", "info", msg, {"journal_has_hook": hook}, False))
    return rows


TRIP_TOLERANCE_S = 2.0      # clock reads of the harness and of the daemon come from the same host; this only absorbs the sampling gap between a read and a write


def independent_daemon_trip(before, after, monitor_down, preserved=None):
    """Is the latch that appeared during the hold PROVEN to be the daemon's own stale-heartbeat trip? -> (True, proof text) or (False, why not).
    The daemon trips itself (kind monitor_silent, source daemon) once the monitor's heartbeat has been silent for GUARD_STALE_S and pauses; with the monitor stopped for the hold that can happen
    inside the hold. It is accepted only when ALL of these hold: the harness itself stopped the monitor (`monitor_down`, with the last heartbeat it saw); no latch existed before; the latch says
    kind monitor_silent, source daemon, 'no heartbeat from the monitor for N s'; it was written inside the hold and not before the heartbeat could have been stale; trips.jsonl gained exactly one
    line and it is the latch's own record; PAUSE exists; and the PRESERVED evidence (the latch file, trips.jsonl and the monitor's heartbeat time, captured at the end of the hold before any recovery)
    exists, is complete and reproduces what the judge saw. Anything else is unexplained and stays a failure."""
    if not monitor_down or monitor_down.get("last_heartbeat") is None or monitor_down.get("stale_s") is None:
        return False, "no proof: this scenario did not stop the monitor (or its last heartbeat is unknown), so the daemon's stale-heartbeat rule cannot explain a new latch"
    ok, why = AT.preserved_matches(after, preserved)
    if not ok:
        return False, "no proof: " + why
    if before.get("latch_sha") is not None:
        return False, "an existing latch was changed or removed, which no independent safety action explains"
    if after.get("latch_sha") is None:
        return False, "no latch"
    doc = after.get("latch")
    if not isinstance(doc, dict) or doc.get("unreadable"):
        return False, "the new latch could not be read, so its writer is unproven"
    if doc.get("kind") != "monitor_silent" or doc.get("source") != "daemon":
        return False, "the new latch is kind=%r source=%r, not the daemon's monitor_silent trip" % (doc.get("kind"), doc.get("source"))
    if not str(doc.get("detail", "")).startswith("no heartbeat from the monitor for "):
        return False, "the new latch's detail %r is not the daemon's stale-heartbeat message" % (doc.get("detail"),)
    at, tol = doc.get("at"), TRIP_TOLERANCE_S
    if not isinstance(at, (int, float)) or isinstance(at, bool):
        return False, "the new latch has no time"
    if before.get("at") is None or after.get("at") is None or not (before["at"] - tol <= at <= after["at"] + tol):
        return False, "the new latch (at %s) was not written inside the hold (%s .. %s)" % (at, before.get("at"), after.get("at"))
    last_hb = preserved["heartbeat"]["mtime"]                      # the file's own time, read once the monitor was stopped: authoritative over the harness's reading
    earliest = last_hb + monitor_down["stale_s"] - tol
    if at < earliest:
        return False, "the new latch (at %.1f) predates the moment the heartbeat could have been stale (%.1f): it is not the stale-heartbeat rule" % (at, earliest)
    new = (after.get("trip_records") or [])[len(before.get("trip_records") or []):]
    if (after.get("trip_records") or [])[:len(before.get("trip_records") or [])] != (before.get("trip_records") or []) or len(new) != 1 or new[0] != doc:
        return False, "trips.jsonl did not gain exactly the one line that is the latch's own record (new lines: %s)" % (new,)
    if not after.get("paused"):
        return False, "the daemon did not pause with the latch, which its rule always does"
    return True, ("the daemon's own safety rule: latch kind=%s source=%s written at %.1f, %.1f s after the monitor's last heartbeat (%.1f); the harness had stopped the monitor at %.1f; "
                  "trips.jsonl gained exactly that one record; PAUSE exists; the preserved latch file, trips.jsonl and heartbeat time reproduce this" % (doc["kind"], doc["source"], at, at - last_hb, last_hb, monitor_down.get("since", float("nan"))))


def evaluate_never_recovers(scn, before, after, hold_s, monitor_down=None, preserved=None):
    """`monitor_down`: only for a scenario in which the HARNESS stopped the monitor: {"since", "last_heartbeat", "stale_s"}. It lets a latch that the daemon PROVABLY wrote itself (see
    independent_daemon_trip) pass; it never excuses an existing latch changed or removed, a release record, or any change to the failed unit."""
    pre = "V5.%s." % scn
    bad, proof = [], None
    for k in ("ActiveState", "SubState", "Result", "InvocationID", "NRestarts", "MainPID"):
        if before["unit"].get(k) != after["unit"].get(k):
            bad.append("the failed unit's %s changed %r -> %r" % (k, before["unit"].get(k), after["unit"].get(k)))
    if before.get("latch_sha") != after.get("latch_sha"):
        ok, why = independent_daemon_trip(before, after, monitor_down, preserved)
        if ok:
            proof = why
        else:
            bad.append("the latch changed: %s -> %s (%s)" % (before.get("latch_sha"), after.get("latch_sha"), why))
    if after.get("released", 0) != before.get("released", 0):
        bad.append("the number of release records changed %s -> %s" % (before.get("released"), after.get("released")))
    if after.get("trips", 0) < before.get("trips", 0):
        bad.append("trips.jsonl lost lines (%s -> %s)" % (before.get("trips"), after.get("trips")))
    rows = [row(pre + "alert_did_not_recover", "fail" if bad else "pass",
                "; ".join(bad) if bad else "for %d s after the alert went out the failed unit, the latch and the release records were unchanged%s: the alert path restarted, reset and released nothing" %
                (hold_s, " except the daemon's own proven safety trip (see %sindependent_safety_trip)" % pre if proof else ""), {"before": before, "after": after})]
    if proof:
        rows.append(row(pre + "independent_safety_trip", "info", proof, {"latch": after.get("latch"), "monitor_down": monitor_down}, False))
    rows.append(row(pre + "pause_observed", "info", "PAUSE before: %s, after: %s (the daemon pauses itself when the monitor's heartbeat goes stale: its own rule, not the alert path's)" % (before.get("paused"), after.get("paused")), None, False))
    return rows


def evaluate_attribution_capture(scn, cap):
    """REQUIRED row: the evidence that attributes whatever changed in the guard folder during the hold was preserved, complete, before any recovery. A gap fails the scenario."""
    problems = AT.attribution_problems(cap)
    return [row("V5.%s.attribution_preserved" % scn, "fail" if problems else "pass",
                ("the attribution evidence is INCOMPLETE, so nothing about the hold can be accepted: %s" % "; ".join(problems[:6])) if problems else
                "the latch file (%s), trips.jsonl (%s), PAUSE (%s), the heartbeat time, the guard listing and timestamped alert, poll, daemon and monitor journals were captured at the end of the hold, before recovery" %
                (cap["latch"]["state"], cap["trips"]["state"], cap["pause"]["state"]), {"problems": problems, "file": AT.SCENARIOS.get(scn)})]


def evaluate_sandbox(doc, guard):
    """REQUIRED rows: the alert units' effective sandbox was preserved, and it does not let them write the guard folder."""
    problems = AT.sandbox_problems(doc)
    rows = [row("V5.alert_sandbox.preserved", "fail" if problems else "pass", ("the alert units' effective properties are incomplete: %s" % "; ".join(problems[:6])) if problems else
                "effective sandbox properties of the alert unit for each protected unit and of the poll unit were preserved (%s)" % AT.SANDBOX_FILE, {"problems": problems})]
    if not problems:
        bad = AT.sandbox_findings(doc, guard)
        rows.append(row("V5.alert_sandbox.guard_not_writable", "fail" if bad else "pass", "; ".join(bad) if bad else
                        "every alert unit has ProtectSystem=strict and no ReadWritePaths covering the guard folder: the alert path cannot write the latch even if the tool tried",
                        {"users": {n: p.get("User") for n, p in doc["units"].items()}}))
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
    doc, sha = ctx.latch()
    released = [n for n in env.listdir(GUARD + "/evidence/released")] if env.exists(GUARD + "/evidence/released") else []
    trips = ctx.trips()
    return {"unit": {k: sh.get(k) for k in UNIT_PROPS}, "latch_sha": sha, "latch": doc, "released": len(released), "trips": len(trips), "trip_records": trips, "paused": ctx.paused(), "at": ctx.now()}


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


def _journal(ctx, since, *units):
    """A timestamped slice of the journal of the given units, queried by EXACT name: {"state": present|empty|unreadable, "text", "query": {argv, rc, stderr}}. Exit 0 with no entry is a successful empty
    query ("-- No entries --"); any other exit is unreadable, never empty. A unit name given here must not be a glob (see collect_alert_journal)."""
    argv = ["journalctl", "--since", "@%d" % int(since), "--no-pager", "-o", "short-iso-precise"]
    for u in units:
        argv += ["-u", u]
    rc, out, err = ctx.env.run(argv, timeout=60)
    q = {"argv": argv, "rc": rc, "stderr": (err or "")[-200:]}
    if rc != 0:
        return {"state": AT.UNREADABLE, "detail": (err or "rc %s" % rc)[-200:], "query": q}
    return {"state": AT.PRESENT, "text": out, "query": q} if AT.journal_lines(out) else {"state": AT.EMPTY, "query": q}


def collect_alert_journal(ctx, since):
    """The journal of the alert units since `since`, without a wildcard. A glob unit filter (`-u 'dfl-vtest-alert@*'`) makes journalctl exit 1 ("Failed to add filter for units: No data available") when
    the journal holds no unit of that name, which is exactly the case when the monitor was stopped cleanly: the failure hook never ran and only the poll saw it (real VM, 2026-11-19). So: list the unit
    names the journal's own index holds (the fields journalctl itself searches for a glob: journal-wide, not limited by the window, still there after an instance has finished), keep those that are alert
    instances, add the two failure-hook instances that can exist, and query each by EXACT name. Returns {"state": present|empty|unreadable, "text", "detail", "instances": {name: _journal result},
    "enumeration": {"fields": {field: {argv, rc, names}}, "listed": [...]}, "expected": [...]}. 'empty' means every query succeeded and none had an entry; any failure is 'unreadable'."""
    expected = AT.expected_alert_instances()
    fields, held, failed = {}, set(), []
    for f in AT.UNIT_FIELDS:
        argv = ["journalctl", "-F", f, "--no-pager"]
        rc, out, err = ctx.env.run(argv, timeout=60)
        if rc != 0:
            fields[f] = {"argv": argv, "rc": rc, "stderr": (err or "")[-200:]}
            failed.append("field %s: %s" % (f, (err or "rc %s" % rc).strip()[-120:]))
        else:
            names = sorted({l.strip() for l in (out or "").splitlines() if l.strip()})
            fields[f] = {"argv": argv, "rc": rc, "names": names}
            held.update(names)
    res = {"enumeration": {"fields": fields}, "expected": expected, "instances": {}}
    if failed:
        res.update(state=AT.UNREADABLE, detail="the journal's unit index could not be listed (%s)" % "; ".join(failed))
        return res
    if AT.POLL_UNIT not in held:
        res.update(state=AT.UNREADABLE, detail="the journal's unit index does not list the poll unit %s, which runs every minute: the index was not read" % AT.POLL_UNIT)
        return res
    listed = AT.alert_instance_names(held)
    res["enumeration"]["listed"] = listed
    bad = []
    for name in sorted(set(listed) | set(expected)):
        res["instances"][name] = _journal(ctx, since, name)
        if res["instances"][name]["state"] == AT.UNREADABLE:
            bad.append("%s: %s" % (name, res["instances"][name].get("detail", "").strip()[-100:]))
    if bad:
        res.update(state=AT.UNREADABLE, detail="alert instance journal(s) could not be read (%s)" % "; ".join(bad))
        return res
    parts = ["-- instance %s --\n%s" % (n, i["text"]) for n, i in sorted(res["instances"].items()) if i["state"] == AT.PRESENT]
    if parts:
        res.update(state=AT.PRESENT, text="".join(parts))
    else:
        res.update(state=AT.EMPTY)
    return res


def _file_state(env, path):
    if not env.exists(path):
        return {"state": AT.ABSENT}
    text = env.read(path)
    if text is None:
        return {"state": AT.UNREADABLE}
    return {"state": AT.PRESENT, "text": text, "sha256": AT.sha256_text(text), "mtime": env.mtime(path)}


def capture_attribution(ctx, scn, t_fault, monitor_down=None):
    """Everything that attributes a change in the guard folder, read once at the end of the hold and BEFORE any recovery step (the documented recovery releases and moves the latch). Read-only."""
    env = ctx.env
    now = ctx.now()
    lat, trips = _file_state(env, GUARD + "/TRIPPED"), _file_state(env, GUARD + "/trips.jsonl")
    trips["lines"] = len([l for l in trips.get("text", "").splitlines() if l.strip()]) if trips["state"] == AT.PRESENT else 0
    hb_m = env.mtime(GUARD + "/heartbeat")
    hb = {"state": AT.PRESENT if hb_m is not None else AT.UNREADABLE, "mtime": hb_m, "age": None if hb_m is None else now - hb_m}
    try:
        hb["watch_tail"] = ctx.watch_lines()[-5:]
    except Exception:                                                   # a help, not a requirement: the heartbeat file's own time is the evidence
        hb["watch_tail"] = []
    rel = GUARD + "/evidence/released"
    released = {"state": AT.PRESENT, "names": env.listdir(rel)} if env.exists(rel) else {"state": AT.ABSENT}
    listing = [{"name": n, "mtime": env.mtime(GUARD + "/" + n)} for n in env.listdir(GUARD)]
    since = (t_fault or now) - 5
    return {"schema": AT.SCHEMA, "scenario": scn, "captured_at": now, "t_fault": t_fault, "monitor_down": monitor_down, "latch": lat, "trips": trips,
            "pause": {"state": AT.PRESENT if ctx.paused() else AT.ABSENT}, "heartbeat": hb, "released": released, "guard_listing": listing,
            "journals": {"alert": collect_alert_journal(ctx, since), "poll": _journal(ctx, since, C.PREFIX + "alert-poll.service"),
                         "daemon": _journal(ctx, since, UNIT["daemon"]), "monitor": _journal(ctx, since, UNIT["monitor"])},
            "units": {"daemon": {k: ctx.show("daemon", UNIT_PROPS).get(k) for k in UNIT_PROPS}, "monitor": {k: ctx.show("monitor", UNIT_PROPS).get(k) for k in UNIT_PROPS}}}


def capture_sandbox(ctx):
    """The alert units' EFFECTIVE sandbox as systemd reports it (a unit file can say one thing and a drop-in another)."""
    units = {}
    for name in (C.PREFIX + "alert@" + UNIT["daemon"] + ".service", C.PREFIX + "alert@" + UNIT["monitor"] + ".service", C.PREFIX + "alert-poll.service"):
        rc, out, err = ctx.env.sc("show", name, *["-p" + p for p in AT.SANDBOX_PROPS])
        props = {}
        for l in (out or "").splitlines():
            if "=" in l:
                a, b = l.split("=", 1)
                props[a] = b
        units[name] = props if rc == 0 else {"error": (err or "rc %s" % rc)[-200:]}
    return {"schema": AT.SCHEMA, "captured_at": ctx.now(), "guard": GUARD, "units": units}


def sandbox_step(ctx, rec):
    """Capture the alert units' effective sandbox, save it, and judge it (required rows)."""
    try:
        doc = capture_sandbox(ctx)
    except Exception as e:
        doc = {"schema": AT.SCHEMA, "error": "%s: %s" % (type(e).__name__, e)}
    ctx.save(AT.SANDBOX_FILE, doc)
    rec.add_all(evaluate_sandbox(doc, GUARD))
    return doc


def _hold_and_judge(ctx, rec, scn, failed_key, monitor_down=None, t_fault=None):
    hold = ctx.cfg["hold_s"]
    ctx.mark("V5." + scn, phase="hold_start")                          # the evidence manifest requires this scenario's attribution file once this mark exists
    before = snapshot(ctx, failed_key)
    ctx.env.sleep(hold)
    after = snapshot(ctx, failed_key)
    try:
        cap = capture_attribution(ctx, scn, t_fault, monitor_down)     # BEFORE the judge and before any recovery; saved at once, so a failing judge still leaves it
    except Exception as e:                                              # a capture that cannot be made is a failed capture, never a silent one
        cap = {"schema": AT.SCHEMA, "scenario": scn, "error": "%s: %s" % (type(e).__name__, e)}
    ctx.save("attribution_%s.json" % scn, cap)
    ctx.mark("V5." + scn, phase="attribution_captured")
    rec.add_all(evaluate_attribution_capture(scn, cap))
    rec.add_all(evaluate_never_recovers(scn, before, after, hold, monitor_down, cap))
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
    monitor_down = {"since": t0, "last_heartbeat": None, "stale_s": C.GUARD_STALE_S}      # the daemon's own stale-heartbeat rule can fire during the hold
    mid_inc, new = None, []
    try:
        env.sc("stop", UNIT["monitor"])
        hb_age = ctx.heartbeat_age()                      # read AFTER the stop returned: the monitor keeps refreshing it until it exits (9 s on the real host), so this is its final heartbeat
        monitor_down["last_heartbeat"] = None if hb_age is None else ctx.now() - hb_age
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
        rec.add_all(evaluate_detection(scn, events, new, sink_new, t0, cfg["detect_max_s"], collect_alert_journal(ctx, t0 - 2), expect_delivery=not fail_delivery))
        if got:
            _hold_and_judge(ctx, rec, scn, "monitor", monitor_down, t0)
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
            rec.add_all(evaluate_detection(scn, ("daemon_start_limit_hit",), new, read_sink(env)[len(sink0):], t_fault, cfg["detect_max_s"], collect_alert_journal(ctx, t0 - 2)))
            warn = [i for i in new if i.get("event") == "daemon_budget_low"]
            rec.check("V5.%s.budget_warning" % scn, "info", "the 'budget nearly used' warning was %s before the limit was hit (NRestarts is only a heuristic for budget use: recorded, not judged)" % ("raised" if warn else "NOT raised"), {"warnings": [w["id"] for w in warn]}, False)
            if got:
                a = ctx.position()
                _hold_and_judge(ctx, rec, scn, "daemon", None, t_fault)
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
        rec.add_all(evaluate_detection(scn, ("monitor_failed", "monitor_not_running"), new, read_sink(env)[len(sink0):], t0, cfg["detect_max_s"] + 120, collect_alert_journal(ctx, t0 - 2)))
        if got:
            _hold_and_judge(ctx, rec, scn, "monitor", None, t0)
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


def save_alert_journals(ctx):
    """The whole run's alert and poll journals: alert_journal.txt (the instances), alert_journal_timestamped.txt (instances and poll in time order, ISO timestamps) and alert_journal_status.json (what was
    queried and how each query ended). A failed query is written as UNAVAILABLE with its reason, never as an empty file; a successful empty collection says what was queried."""
    env = ctx.env
    res = collect_alert_journal(ctx, 0)
    poll = _journal(ctx, 0, AT.POLL_UNIT)
    units = [AT.POLL_UNIT] + sorted(set(res.get("instances", {})) | set(AT.expected_alert_instances()))
    argv = ["journalctl", "--no-pager", "-o", "short-iso-precise"]
    for u in units:
        argv += ["-u", u]
    rc, out, err = env.run(argv, timeout=120)
    comb = {"argv": argv, "rc": rc, "units": units, "stderr": (err or "")[-200:]}
    if res["state"] == AT.PRESENT:
        legacy = res["text"]
    elif res["state"] == AT.EMPTY:
        legacy = ("(no entries: the failure hook never started an alert instance; %d instance name(s) were queried by exact name, every query succeeded: %s)\n" %
                  (len(res["instances"]), ", ".join(sorted(res["instances"]))))
    else:
        legacy = "UNAVAILABLE: %s\n" % res.get("detail", "unreadable")
    ctx.save("alert_journal.txt", legacy)
    if rc == 0 and AT.journal_lines(out):
        stamped = out
    else:
        stamped = "UNAVAILABLE: %s" % ((err or out or "rc %s" % rc).strip()[-200:] or "no entries")
    ctx.save(AT.JOURNAL_FILE, stamped)
    ctx.save(AT.JOURNAL_STATUS_FILE, {"schema": AT.SCHEMA, "alert": res, "poll": poll, "combined": comb})


def save_evidence(ctx):
    env = ctx.env
    try:
        ctx.save("incidents.json", read_incidents(env))
        ctx.save("sink.log", read_sink(env))
        save_alert_journals(ctx)
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
        sandbox_step(ctx, rec)
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
