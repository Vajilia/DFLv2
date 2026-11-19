# V5: attribution of a latch that appears during the "alert did not recover" hold (2026-11-17)

## What failed

The real-host smoke on harness revision `aef65af` (runtime candidate `205f891`, unchanged) failed in the delivery-failure scenario:

`V5.delivery_failure.alert_did_not_recover: the latch changed: None -> <hash>`

The same log showed PAUSE going from absent to present. Everything else in that scenario passed (the failure was recorded, nothing reached the sink while the channel was down, the retry delivered once, nothing was claimed received). The whole of V5 stays **FAILED** for that run; that verdict is not edited by this document.

## Status of the diagnosis: inferred from code, NOT yet confirmed from the saved evidence

The saved evidence archive was not read when this change was written. What follows comes from the pinned source and from the failure text only. The evidence must still be read (section "To confirm").

What the source says:

* The daemon (`tools/broadcast_run.py`, `Guard.step`) trips itself when the monitor's heartbeat is older than `GUARD_STALE_S` (120 s): it writes the latch (`TRIPPED`, kind `monitor_silent`, source `daemon`), appends the same record to `trips.jsonl` and creates PAUSE. That is the daemon's own safety rule.
* V5's monitor-stopped scenarios stop the monitor, wait for the alert (a poll finds it; the timing depends on the poll's phase), then hold for `hold_s` and compare a before/after snapshot. A hold that begins late enough spans the moment 120 s after the monitor's last heartbeat, so the daemon's own latch can appear inside the hold. Whether it does depends on timing, which is why one scenario passed and another failed on the same run.
* The alert tool (`alerts/dfl_alert.py`), from reading its source and unit templates (static; the installed host's effective unit properties are not yet checked against the evidence): it runs `systemctl show` (any other systemctl verb is refused), reads the current boot's manager journal for one unit (`journalctl`), reads its own incident state, records incidents with `safe_write` (atomic, refuses any path outside its alert folder), and runs the owner-configured delivery and ping commands as subprocesses (no argument restriction; in the validation harness the delivery command appends to a sink file). Its `ack` command only updates an incident record. Nothing in it names the guard folder. Its units have no `User=` (so they run as root) and no `ReadWritePaths=`; what stops a write to the guard folder is `ProtectSystem=strict` (everything but the alert's own state folder is read-only to the unit) plus `safe_write`, and the configured delivery command is bounded only by that same sandbox, not by the tool.

The old judge compared the latch hash and could not tell the daemon's protective trip from a forbidden alert action, so it failed any new latch.

## What the judge does now (`tools/vtest/stage_v5.py`)

A latch that was absent before the hold and present after it is accepted only when `independent_daemon_trip` proves all of:

1. The harness itself stopped the monitor in this scenario and knows its last heartbeat (only the monitor-stopped and delivery-failure scenarios pass this; the daemon start-limit and monitor-exit-2 scenarios do not, so a new latch there still fails).
2. No latch existed before.
3. The latch reads kind `monitor_silent`, source `daemon`, detail beginning "no heartbeat from the monitor for ".
4. Its time is inside the hold and not earlier than the last heartbeat plus `GUARD_STALE_S` (minus 2 s clock tolerance).
5. `trips.jsonl` gained exactly one line and it equals the latch record.
6. PAUSE exists.

Still failures, with no exemption: an existing latch altered or removed; a latch with another kind, writer, reason or time; no matching `trips.jsonl` line; any release record; any change to the failed unit (state, result, invocation, restarts, main PID); lost trips lines. A proven trip does not excuse any of these happening at the same time. When a trip is accepted the stage records an `independent_safety_trip` row (info, with the latch and the monitor-down data) so a reviewer sees it was accepted and why.

Tests: `tests/test_vtest_v5.py` (`IndependentSafetyTrip`, and the scenario-level reproduction and controls in `ScenarioGlue`). The scripted reproduction fails on the old judge with the message above. Mutating each clause of the proof makes at least one test fail.

## What this does and does not show

* It changes the harness only. The runtime candidate and the installed alert configuration are untouched; `tools/vtest/PIN` and pin-check are unchanged.
* It does not turn the failed run into a pass and does not combine revisions. The next real-system V5 must run on the new harness revision and, to qualify, as part of the complete current-configuration sequence.
* It does not test the real daemon's self-trip with the real alert package. Only a real-host run does that.

## To confirm (needs the private evidence)

* From the archive: the latch's recorded kind, source and time; the `trips.jsonl` lines; the timeline of the recording and of the hold; the daemon and monitor journals. If any of these contradicts the inference above (for example the source is not `daemon`, or the time falls outside the stale window), this change is wrong and the finding is about the candidate or the alert path, not the harness.
* Preserved/released evidence for the latch (stage recovery may already have moved `TRIPPED`).
