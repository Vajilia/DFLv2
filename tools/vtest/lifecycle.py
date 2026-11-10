"""Pure judgments about the candidate's unit lifecycle design: the run marker, the start gate and the stop hook. No I/O, no clock.

The design under test (docs/OOM_INCIDENT_RECORD.md of the pinned candidate):
  ExecStartPre=  broadcast_run.py gate      opens guard/RUN_OPEN before every daemon start; a marker still open means a stop nobody accounted for: latch (kind unaccounted_stop) and start held,
                                            or exit 1 and no daemon if it cannot latch or cannot open the new marker.
  ExecStopPost=  broadcast_run.py incident  after an OOM stop: PAUSE, latch (kind oom_kill, source systemd), evidence, then closes the marker. After a stop systemd says was not an OOM: closes the marker.
Everything here takes plain values (a marker document, the lines of trips.jsonl, timestamps) so that each verdict can be re-derived from the evidence folder.
A row is (check id, status, detail, data[, required]); status is pass / fail / invalid / not_run / info.
"""
HOOK_KINDS = ("oom_kill", "unaccounted_stop")      # latch kinds only the lifecycle hooks write


def new_lines(before, after):
    """The lines appended to an append-only list. None if `after` is not an extension of `before` (a line was lost or rewritten)."""
    before, after = list(before or []), list(after or [])
    if after[:len(before)] != before:
        return None
    return after[len(before):]


def wall_time_of_monotonic_us(enter_us, now_wall, now_mono):
    """systemd reports a unit's start as CLOCK_MONOTONIC microseconds. -> the same instant on the wall clock, or None if systemd gave none (0 or missing)."""
    if not enter_us:
        return None
    return now_wall - (now_mono - enter_us / 1e6)


def marker_view(state_doc):
    """state_doc: (state, doc) as read from guard/RUN_OPEN: state 'absent' | 'open' | 'unreadable'. -> a small dict for the evidence."""
    state, doc = state_doc if state_doc else ("absent", None)
    doc = doc or {}
    return {"state": state, "invocation_id": doc.get("invocation_id"), "boot_id": doc.get("boot_id"), "opened_at": doc.get("opened_at"), "pid": doc.get("pid")}


def judge_marker_reopened(before, after, boot_id, latch_at=None, tol=0.5):
    """After a stop and a restart of the daemon: a NEW marker is open for the new run. `before`/`after`: marker_view dicts. `latch_at`: when the incident's latch was written (None if there was none):
    the gate that opened the new marker must have run after it (the hook finishes before the next start)."""
    problems = []
    if before.get("state") != "open":
        problems.append("the marker was %s before the stop: a running daemon should have an open marker" % before.get("state"))
    if after.get("state") != "open":
        problems.append("no open marker after the restart (%s): the gate did not run before the new daemon, or it could not write" % after.get("state"))
    else:
        if before.get("state") == "open":
            same = (after.get("invocation_id") is not None and after.get("invocation_id") == before.get("invocation_id")) or (after.get("invocation_id") is None and after.get("opened_at") == before.get("opened_at"))
            if same:
                problems.append("the marker is the one from before the stop (same invocation): nothing closed it and nothing reopened it")
        if boot_id and after.get("boot_id") and after["boot_id"] != boot_id:
            problems.append("the new marker carries boot id %s, not this boot's" % after["boot_id"])
        if latch_at is not None and after.get("opened_at") is not None and after["opened_at"] < latch_at - tol:
            problems.append("the new marker (%.1f) was opened BEFORE the incident's latch (%.1f): the gate ran before the stop hook finished" % (after["opened_at"], latch_at))
    return problems


def judge_recorded_by_hook(latch):
    """The OOM must be recorded by ExecStopPost= (kind oom_kill, source systemd). unaccounted_stop means only the gate's fail-safe caught it."""
    if not latch or latch.get("unreadable"):
        return "fail", "no readable latch"
    kind, src = latch.get("kind"), latch.get("source")
    if kind == "oom_kill" and src == "systemd":
        return "pass", "latch kind=oom_kill source=systemd: written by the unit's stop hook"
    if kind == "unaccounted_stop":
        return "fail", "the latch is kind=unaccounted_stop (source %s): the broadcast WAS held, but by the start gate's fail-safe; the stop hook did not record the OOM. DEFECT OF THE CANDIDATE (or of its environment): see the unit's journal for the hook's output" % src
    return "fail", "latch kind=%s source=%s is not the stop hook's record (expected oom_kill / systemd)" % (kind, src)


def judge_before_restart(latch_at, started_wall, tol=0.5):
    """The incident must be on disk before the restarted daemon exists (ExecStopPost= runs before Restart=)."""
    if latch_at is None:
        return "not_run", "no latch time"
    if started_wall is None:
        return "not_run", "INCOMPLETE: systemd gave no start time for the restarted daemon (ActiveEnterTimestampMonotonic)"
    lead = started_wall - latch_at
    if lead >= -tol:
        return "pass", "the latch was written %.1f s before the restarted daemon became active" % lead
    return "fail", "the latch (at %.1f) was written %.1f s AFTER the restarted daemon became active (%.1f): a restarted daemon could have advanced the broadcast first" % (latch_at, -lead, started_wall)


def judge_single_incident(new_trip_lines, expect_kind="oom_kill"):
    """Exactly one incident line for one OOM: if the gate also latched, the hook did not close the marker."""
    if new_trip_lines is None:
        return "fail", "trips.jsonl lost or rewrote an earlier line"
    kinds = [l.get("kind") for l in new_trip_lines]
    if kinds == [expect_kind]:
        return "pass", "exactly one new trips.jsonl line: %s" % expect_kind
    return "fail", "new trips.jsonl lines: %s (expected exactly one, %s)" % (kinds or "none", expect_kind)


def judge_victim(recorded, harness_victims, pre_main_pid):
    """`recorded`: the hook's own account (evidence.json's `evidence` object): victims, victim_status, daemon_main_pid, daemon_was_victim. `harness_victims`: [{"pid","comm"}] the harness read from the kernel
    log for this unit's cgroup, or []/None if it could not. The record must be explicit and must not CONTRADICT what the kernel log shows; 'unknown' is allowed (the service user may not read the log) but never
    a guess. -> (status, detail)"""
    recorded = recorded or {}
    status = recorded.get("victim_status")
    if status not in ("identified", "unknown"):
        return "fail", "the incident record does not say whether the victim was identified (victim_status=%r)" % status
    rpids = sorted(v.get("pid") for v in recorded.get("victims") or [])
    hpids = sorted(v.get("pid") for v in harness_victims or [])
    main = recorded.get("daemon_main_pid")
    if main is not None and pre_main_pid is not None and main != pre_main_pid:
        return "fail", "the record names daemon pid %s, but the daemon's main pid before the stop was %s" % (main, pre_main_pid)
    was = recorded.get("daemon_was_victim")
    if status == "unknown":
        if was is not None:
            return "fail", "victim_status is unknown but daemon_was_victim=%r: a guess" % was
        return "pass", "the record says the victim is UNKNOWN%s (explicit, not guessed)" % ((" (the harness read the kernel log: %s)" % hpids) if hpids else "")
    if not rpids:
        return "fail", "victim_status=identified but no victims are listed"
    if hpids and rpids != hpids:
        return "fail", "the record names victims %s; the kernel log says %s" % (rpids, hpids)
    if was is not None and hpids and main is not None and was != (main in hpids):
        return "fail", "daemon_was_victim=%r contradicts the kernel log (daemon pid %s, victims %s)" % (was, main, hpids)
    return "pass", "victims %s%s; daemon_was_victim=%r" % (rpids, " match the kernel log" if hpids else " (the harness could not read the kernel log to compare)", was)


def evaluate_v2_lifecycle(prefix, latch, evidence_doc, trips_before, trips_after, marker_before, marker_after, daemon_start_wall, harness_victims, pre_main_pid, boot_id):
    """The lifecycle checks of an OOM scenario (V2b, V2c). `prefix` names the scenario ('V2b'). Adds to, never replaces, the incident_record_and_hold row.
    The victim has two witnesses: the production incident record (written by the unprivileged service, which may say 'unknown') and the harness's own kernel-log collection (root, independent of the
    service), which must identify it for the scenario to count; the record is then checked against it."""
    rows = []
    st, detail = judge_recorded_by_hook(latch)
    rows.append((prefix + ".recorded_by_the_stop_hook", st, detail, {"latch": latch}))
    if not latch or latch.get("unreadable"):
        return rows
    st, detail = judge_before_restart(latch.get("at"), daemon_start_wall)
    rows.append((prefix + ".recorded_before_the_restart", st, detail, {"latch_at": latch.get("at"), "daemon_start_wall": daemon_start_wall}))
    nl = new_lines(trips_before, trips_after)
    st, detail = judge_single_incident(nl)
    rows.append((prefix + ".single_incident_line", st, detail, {"new_lines": nl}))
    problems = judge_marker_reopened(marker_view(marker_before) if not isinstance(marker_before, dict) else marker_before,
                                     marker_view(marker_after) if not isinstance(marker_after, dict) else marker_after, boot_id, latch.get("at"))
    rows.append((prefix + ".run_marker_closed_and_reopened", "fail" if problems else "pass",
                 "the stop hook closed the old marker and the gate opened a new one after the latch" if not problems else "; ".join(problems), {"before": marker_before, "after": marker_after}))
    rows.append((prefix + ".kernel_evidence_identifies_victim", "pass" if harness_victims else "not_run",
                 ("the harness's own, independent collection of the kernel log names the victim(s): %s" % sorted(v.get("pid") for v in harness_victims)) if harness_victims else
                 "INCOMPLETE: the harness could not identify the OOM victim from the kernel log it collects as root, so this scenario cannot say who was killed (the production incident record may say 'unknown'; the test's evidence may not)",
                 {"victims": harness_victims}))
    if evidence_doc is None:
        rows.append((prefix + ".victim_recorded_explicitly", "fail", "no evidence.json of the oom_kill incident to read the victim from", None))
    else:
        st, detail = judge_victim((evidence_doc or {}).get("evidence"), harness_victims, pre_main_pid)
        rows.append((prefix + ".victim_recorded_explicitly", st, detail, {"recorded": (evidence_doc or {}).get("evidence"), "harness": harness_victims}))
    return rows


# ---------------------------------------------------------------- V3: no false incident
def evaluate_daemon_negative_control(case, restarted, latch, paused, trips_new, marker_before, marker_after, marker_while_stopped, advances, boot_id):
    """A daemon stop that is NOT an OOM (kill -9, restart, stop then start) must record nothing: no latch, no incident line, no PAUSE, the clock goes on; the marker is closed by the stop hook and reopened by the gate.
    `marker_while_stopped`: marker_view read while the unit was stopped (only for the stop/start case), else None."""
    name = "V3e." + case
    problems = []
    if latch:
        problems.append("a latch appeared (kind=%s source=%s)" % (latch.get("kind"), latch.get("source")))
    if trips_new is None:
        problems.append("trips.jsonl lost or rewrote an earlier line")
    elif trips_new:
        problems.append("new trips.jsonl lines: %s" % [l.get("kind") for l in trips_new])
    if paused:
        problems.append("the daemon is paused (a PAUSE file exists)")
    if not advances:
        problems.append("the broadcast clock did not advance after the restart")
    rows = [(name + ".no_false_incident", "fail" if problems else "pass", "; ".join(problems) if problems else "no latch, no incident line, not paused, clock advances", {"problems": problems})]
    if marker_while_stopped is not None:
        ok = marker_while_stopped.get("state") == "absent"
        rows.append((name + ".marker_closed_by_the_stop_hook", "pass" if ok else "fail", "while the unit was stopped the marker was %s (a stop systemd calls clean must close it)" % marker_while_stopped.get("state"), {"marker": marker_while_stopped}))
    p = judge_marker_reopened(marker_before, marker_after, boot_id)
    rows.append((name + ".marker_cycle", "fail" if p else "pass", "the old marker was closed and a new one opened for the new run" if not p else "; ".join(p), {"before": marker_before, "after": marker_after}))
    return rows


def evaluate_monitor_failures_left_daemon_alone(trips_new, daemon_before, daemon_after, marker_before, marker_after):
    """V3a-V3d fail the MONITOR only. The daemon's own run must be untouched (same main pid, same marker) and the lifecycle hooks must have written no incident."""
    rows = []
    kinds = None if trips_new is None else [l.get("kind") for l in trips_new]
    bad = None if kinds is None else [k for k in kinds if k in HOOK_KINDS]
    rows.append(("V3.no_lifecycle_incident", "fail" if bad is None or bad else "pass",
                 "trips.jsonl lost a line" if bad is None else ("lifecycle hooks wrote: %s" % bad) if bad else "monitor failures wrote only %s; the stop hook and the gate recorded nothing" % (sorted(set(kinds)) or "nothing"), {"kinds": kinds}))
    same = daemon_before.get("MainPID") == daemon_after.get("MainPID") and marker_before.get("invocation_id") == marker_after.get("invocation_id") and marker_before.get("state") == marker_after.get("state") == "open"
    rows.append(("V3.daemon_run_untouched_by_monitor_failures", "pass" if same else "fail",
                 "daemon main pid %s -> %s, marker %s/%s -> %s/%s" % (daemon_before.get("MainPID"), daemon_after.get("MainPID"), marker_before.get("state"), marker_before.get("invocation_id"), marker_after.get("state"), marker_after.get("invocation_id")),
                 {"before": marker_before, "after": marker_after}))
    return rows


# ---------------------------------------------------------------- V4: the start gate under a real fault; reboot
def evaluate_gate_refusal(window, pre_main_pid, marker_before, marker_after, journal, held_pos, held_paused):
    """While the guard folder cannot be written (made immutable by the harness) an OOM happens. Required: no new daemon runs, the broadcast is held (PAUSE, clock stopped), the marker stays open, and the
    journal shows the start gate refusing (the hook's `unrecorded` line and/or the gate's `refused` line). `window`: [{"t", "ActiveState", "MainPID"}] sampled during the fault."""
    rows = []
    ran = sorted({w.get("MainPID") for w in window if w.get("ActiveState") == "active" and w.get("MainPID") not in (0, None, pre_main_pid)})
    rows.append(("V4.immutable_guard.no_daemon_while_storage_broken", "pass" if window and not ran else "fail" if ran else "invalid",
                 "no new daemon became active during the %d samples taken while the guard folder was unwritable" % len(window) if window and not ran else
                 ("a NEW daemon ran while the incident could not be recorded: main pids %s" % ran) if ran else "no samples were taken", {"new_daemons": ran, "samples": len(window)}))
    pos_a, pos_b = held_pos
    moved = pos_a is not None and pos_b is not None and pos_b - pos_a > 1.5
    rows.append(("V4.immutable_guard.held_while_storage_broken", "pass" if held_paused and not moved else "fail", "PAUSE file: %s; broadcast position %s -> %s" % (held_paused, pos_a, pos_b), {"pos": held_pos}))
    open_ = marker_after.get("state") == "open" and marker_after.get("invocation_id") == marker_before.get("invocation_id")
    rows.append(("V4.immutable_guard.marker_left_open", "pass" if open_ else "fail", "the old run's marker is still there (the hook could not account for the stop): %s" % open_, {"before": marker_before, "after": marker_after}))
    j = journal or ""
    hook = '"incident": "unrecorded"' in j or '"incident":"unrecorded"' in j
    gate = '"gate": "refused"' in j or '"gate":"refused"' in j
    rows.append(("V4.immutable_guard.refusal_is_in_the_journal", "pass" if hook or gate else "fail", "journal shows: stop hook unrecorded=%s, start gate refused=%s" % (hook, gate), {"journal_tail": j[-800:]}))
    return rows


def evaluate_gate_recovery(latch, marker_before_fault, marker_after, pre_main_pid, post_main_pid, evidence_doc, hold, boot_id, path):
    """Storage works again; the unit starts (by itself or after a documented reset-failed + start). Required: a latch kind=unaccounted_stop naming the previous run, the daemon held (PAUSE, clock stopped, latch
    unchanged), a new marker. `hold`: {"paused", "pos_a", "pos_b", "dt", "sha_before", "sha_after"}. `path`: how the unit came back ('by itself' / 'after reset-failed and start')."""
    rows = []
    ok = bool(latch) and not latch.get("unreadable") and latch.get("kind") == "unaccounted_stop" and latch.get("source") == "systemd"
    rows.append(("V4.immutable_guard.latched_when_storage_recovered", "pass" if ok else "fail",
                 "latch kind=%s source=%s (expected unaccounted_stop / systemd); the unit came back %s" % ((latch or {}).get("kind"), (latch or {}).get("source"), path), {"latch": latch, "path": path}))
    prev = ((evidence_doc or {}).get("evidence") or {}).get("previous_run") or {}
    named = bool(prev) and prev.get("invocation_id") == marker_before_fault.get("invocation_id") and (evidence_doc["evidence"].get("same_boot") is True)
    rows.append(("V4.immutable_guard.names_the_previous_run", "pass" if named else "fail",
                 "the evidence names the run whose marker was left open (invocation %s) and says same boot: %s" % (marker_before_fault.get("invocation_id"), named), {"previous_run": prev, "same_boot": ((evidence_doc or {}).get("evidence") or {}).get("same_boot")}))
    held_ok = (hold.get("paused") and hold.get("pos_a") is not None and hold.get("pos_b") is not None and hold["pos_b"] - hold["pos_a"] <= 1.0
               and hold.get("sha_before") and hold.get("sha_before") == hold.get("sha_after") and (hold.get("dt") or 0) >= 10 and post_main_pid not in (None, 0, pre_main_pid))
    rows.append(("V4.immutable_guard.started_held", "pass" if held_ok else "fail",
                 "a new daemon (main pid %s -> %s) is paused, its clock stopped, the latch unchanged, for %.0f s" % (pre_main_pid, post_main_pid, hold.get("dt") or 0), {"hold": hold}))
    reopened = marker_after.get("state") == "open" and marker_after.get("invocation_id") not in (None, marker_before_fault.get("invocation_id"))
    rows.append(("V4.immutable_guard.new_marker_open", "pass" if reopened else "fail", "a new marker is open for the new run: %s" % reopened, {"after": marker_after}))
    return rows


def evaluate_reboot_marker(mode, marker_pre, marker_post_boot, boot_pre, boot_now):
    """Judged BEFORE the units are started after a reboot. `mode`: 'clean' (a normal reboot) or 'hard' (power cut / reset, operator's action). The marker the daemon had open before the reboot
    must be CLOSED by a clean shutdown (else every reboot starts held) and STILL THERE after a hard one (else an unclean death would go unnoticed)."""
    changed = bool(boot_pre) and bool(boot_now) and boot_pre != boot_now
    if not changed:
        return [("V4.reboot.host_restarted", "invalid", "the boot id did not change (%s): this is not after a reboot" % boot_now, None)]
    rows = [("V4.reboot.host_restarted", "pass", "boot id changed %s -> %s" % (boot_pre, boot_now), None)]
    if marker_pre.get("state") != "open":
        rows.append(("V4.reboot.marker", "invalid", "the daemon had no open marker before the reboot (%s)" % marker_pre.get("state"), {"pre": marker_pre}))
        return rows
    if mode == "clean":
        ok = marker_post_boot.get("state") == "absent"
        rows.append(("V4.reboot.clean_shutdown_closed_the_marker", "pass" if ok else "fail",
                     "marker after the clean reboot, before any unit started: %s%s" % (marker_post_boot.get("state"), "" if ok else
                     ": ExecStopPost= did not close it at shutdown, so EVERY reboot would start the daemon held (the main way this design could misbehave)"), {"pre": marker_pre, "post": marker_post_boot}))
    else:
        ok = marker_post_boot.get("state") == "open" and marker_post_boot.get("invocation_id") == marker_pre.get("invocation_id")
        rows.append(("V4.reboot.hard_reset_left_the_marker", "pass" if ok else "fail",
                     "marker after the hard reset, before any unit started: %s (must be the run that was open)" % marker_post_boot.get("state"), {"pre": marker_pre, "post": marker_post_boot}))
    return rows


def evaluate_after_reboot_start(mode, latch_pre, latch_now, trips_new, held, marker_after, marker_pre):
    """After the units are started following a reboot. clean: nothing new is recorded. hard: the gate records exactly one unaccounted_stop line naming the previous boot; the pre-reboot latch (if any) stays first."""
    kinds = None if trips_new is None else [l.get("kind") for l in trips_new]
    rows = []
    if mode == "clean":
        ok = kinds == []
        rows.append(("V4.reboot.clean_start_recorded_nothing", "pass" if ok else "fail", "new trips.jsonl lines after the clean reboot: %s (expected none)" % (kinds if kinds is not None else "a line was lost"), {"new": trips_new}))
    else:
        ok = kinds == ["unaccounted_stop"]
        line = (trips_new or [{}])[0] if ok else {}
        rows.append(("V4.reboot.hard_start_recorded_the_stop", "pass" if ok else "fail", "new trips.jsonl lines after the hard reset: %s (expected exactly one unaccounted_stop)" % (kinds if kinds is not None else "a line was lost"), {"new": trips_new}))
        stated = ok and "boot id changed" in (line.get("detail") or "")
        rows.append(("V4.reboot.hard_start_says_the_host_restarted", "pass" if stated else "fail", "the record says the host restarted (boot id changed): %s" % stated, {"detail": line.get("detail")}))
    if latch_pre is not None:
        rows.append(("V4.reboot.first_latch_kept", "pass" if latch_now == latch_pre else "fail", "the latch present before the reboot is byte-identical afterwards: %s" % (latch_now == latch_pre), None))
    reopened = marker_after.get("state") == "open" and marker_after.get("invocation_id") not in (None, marker_pre.get("invocation_id"))
    rows.append(("V4.reboot.new_marker_open", "pass" if reopened else "fail", "the gate opened a new marker for the post-reboot run: %s" % reopened, {"after": marker_after}))
    return rows
