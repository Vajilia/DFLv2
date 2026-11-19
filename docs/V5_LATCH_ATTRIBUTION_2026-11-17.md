# V5: attribution of a latch that appears during the "alert did not recover" hold (2026-11-17)

## What failed

The real-host smoke on harness revision `aef65af` (runtime candidate `205f891`, unchanged) failed in the delivery-failure scenario:

`V5.delivery_failure.alert_did_not_recover: the latch changed: None -> <hash>`

The same log showed PAUSE going from absent to present. Everything else in that scenario passed (the failure was recorded, nothing reached the sink while the channel was down, the retry delivered once, nothing was claimed received). The whole of V5 stays **FAILED** for that run; that verdict is not edited by this document.

## Status of the diagnosis: supported by the preserved evidence, with two named gaps

The preserved private evidence of the failed run was read on 2026-11-18 (archive integrity and its V5 manifest were verified; the archive itself is kept private and is not described further here). Times below are seconds relative to the moment the harness began stopping the monitor in the failed scenario ("T").

**What the evidence shows**

* The daemon's own journal contains the daemon's one-time trip notice (printed from the latch file's contents by `Guard.step`): kind `monitor_silent`, detail "no heartbeat from the monitor for 120 s", paused. It appears about T+124 s.
* The monitor's journal shows the stop requested at T and completed at T+9 s, and no later start until the harness's documented recovery. The latch therefore appeared about 120 s after the monitor's last possible heartbeat, as the stale-heartbeat rule predicts, while the monitor was stopped.
* That moment lies inside the 60 s hold (the alert was recorded about T+79 s; the 60 s hold ran from shortly after that to about T+139 s). In the earlier monitor-stopped scenario of the same run the alert was recorded within about 5 s and the hold ended about 65 s after T, so no latch could appear: the outcome depended on poll timing, as described below.
* Failing check's snapshots: latch absent -> present, PAUSE absent -> present, trips lines 13 -> 14 (exactly one added), release records 3 -> 3, and the failed unit's state, result, invocation, restarts and PID unchanged.
* Every command the alert tool recorded across all 11 incidents of the run is `systemctl show` or `journalctl --boot=0`; none is anything else. The installed alert units run with `ProtectSystem=strict` and a state folder of their own only; the guard folder is not inside it.
* The string "no heartbeat from the monitor for N s" is produced in exactly one place in the source, the daemon's `Guard.step`, in the same call that records source `daemon`.

**Gaps (not shown by the preserved evidence)**

* The latch file itself (its `source` and `at` fields) and the content of the new `trips.jsonl` line were not preserved; only the count change is. The latch's kind and detail are known only through the daemon's journal notice. The judge therefore requires source, time and the matching trips line on the next real run, and a latch that does not meet them will fail honestly.
* The alert units' journal in the preserved evidence carries no timestamps and omits the poll service, and the install record does not list `ReadWritePaths=`. The no-write conclusion rests on the recorded commands, the sandbox properties above and the source, not on a per-run audit of the hold window.

This is attribution evidence, not a proof that the alert path could not have acted. The original verdict (V5 FAILED on `aef65af`) is unchanged.

The reasoning from the pinned source:

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

## Still to confirm

* On the next real run: the latch's recorded `source` and `at`, and the matching `trips.jsonl` line, preserved in the evidence this time (the judge now requires them).
* Preserved/released evidence for the latch of the failed run if it exists (stage recovery may already have moved `TRIPPED`).

## Evidence preserved from now on (harness change, same record)

The first failure could be explained only from the daemon's journal because the latch file, the trips line, the heartbeat time, timestamped alert and poll journals and the alert units' effective
sandbox were not kept, and recovery had already moved the latch. V5 now preserves them automatically, before any recovery step, and the archive is incomplete without them:

* `V5/attribution_<scenario>.json` for each hold (monitor stopped, daemon start limit, monitor exit 2, delivery failure), captured at the end of the hold and saved before the judge runs and before any
  recovery: the raw latch file and its hash and time, the raw `trips.jsonl`, whether PAUSE exists, the monitor's heartbeat file time and age (and the watch log's tail), the guard folder listing and the
  released-evidence listing, timestamped (`-o short-iso-precise`) journals of the alert units, the poll unit, the daemon and the monitor since just before the fault, and the two units' state.
* `V5/alert_sandbox.json`, captured once before the drills: the effective `User`, `ProtectSystem`, `ReadWritePaths`, `ReadOnlyPaths`, `InaccessiblePaths`, `StateDirectory` and related properties of the alert
  unit for each protected unit and of the poll unit. Two new required rows judge it: it must be recorded in full, and every unit must have `ProtectSystem=strict` with no writable path covering the guard folder.
* `V5/alert_journal_timestamped.txt`: the whole run's alert and poll journal with precise timestamps (the earlier `alert_journal.txt` has none and omits the poll). How these journals are collected was
  corrected on 2026-11-19, see the last section; `V5/alert_journal_status.json` now records each query's status.

How a gap cannot become a pass:

* Each scenario has a REQUIRED row `attribution_preserved`; a missing, unreadable or hollow capture (for example the poll journal empty, no heartbeat time, the latch file not read, the capture itself raising)
  fails the scenario and stops the stage. The capture is saved before the judge, so a failing judge still leaves it.
* A latch that appears during a hold is accepted as the daemon's own only if the preserved evidence is complete AND reproduces the snapshot: the preserved latch file must hash to the judged latch and say the
  same, the preserved `trips.jsonl` must be the records the judge counted, PAUSE must be preserved, and the preserved heartbeat file time (not the harness's own reading) fixes the earliest legitimate moment.
* The evidence manifest requires all of the above. A scenario whose hold began (its timeline mark) owes its file even if the stage stopped there; a scenario never reached is `NOT_PRODUCED`; a V5 that
  passed must have all four. A file that exists but is hollow (the same rules as above, applied to the content) is reported MISSING with the reason, so `vtest.py archive` exits 3 and cleanup is blocked.

Tests: `tests/test_vtest_v5.py` (judge, capture, sandbox rows, scenario-level reproduction and the missing-evidence cases), `tests/test_vtest_evidence.py` (`V5AttributionEvidence`), and the retest-scope
classification of the new harness module in `tests/test_alert_release_config.py`. Mutating each rule makes at least one test fail.

## The alert-journal collection failure (2026-11-19, harness `aaddb43`)

**What failed.** The first real V5-only smoke on the revision that added the attribution capture (runtime candidate unchanged) stopped in its first scenario at
`V5.monitor_stopped.attribution_preserved`: "the alert journal was not read (state 'unreadable')", with the command's own message `Failed to add filter for units: No data available`. The run exited 2, the archive
exited 3 (incomplete evidence) and the host was kept until both archives were copied off and verified. **This run stays FAILED.** It is not a pass, a partial pass or evidence about the candidate. In that
scenario every other row passed (installed, baseline, incident recorded about a minute after the stop, delivered once, not claimed received, nothing recovered, clean recovery); the other three hold scenarios
were never reached. No latch had appeared when the capture was taken (the monitor's last heartbeat was then about 120 s old, just at the daemon's stale limit), so this run says nothing new about the
daemon's own trip.

**Demonstrated cause.** The collector read the alert units' journal with a wildcard unit filter (`journalctl -u '<prefix>alert@*'`). Reproduced with the same journalctl version as the VM (systemd 255.4) against
a private journald:

| query on a journal that holds the poll unit's entries but no alert instance | exit | output |
|---|---|---|
| `-u 'pattern@*'` alone | 1 | `Failed to add filter for units: No data available` |
| `-u 'pattern@*'` plus any exact `-u` (even a unit that never existed) | 0 | normal |
| exact `-u NAME` with no entries | 0 | `-- No entries --` |
| `-u 'pattern@*'` once a matching unit name is in the journal | 0 | normal |

A glob is expanded against the unit names the journal itself holds (the fields `_SYSTEMD_UNIT`, `OBJECT_SYSTEMD_UNIT`, `COREDUMP_UNIT`, `UNIT`; journal-wide, unaffected by `--since`, and still present after an
instance has finished); if every `-u` given is a glob and none matches, the command fails. In the failed run the monitor had been stopped cleanly. A clean stop is not a unit failure, so `OnFailure=` never started
an alert instance (the full boot journal of the host has no alert instance at all) and the poll found the incident, so nothing existed for the glob to match. This also explains why the earlier real smoke runs
(where a monitor was made to fail and the hook did run) read the same query without trouble, and why the whole-run timestamped query of the failed run worked: it named the poll unit as well. The only thing missing
was a unit name for the glob to match; nothing was wrong with the host, the alert package or the candidate.

A second defect: the helper turned any error into an empty string. The detection-path row ("poll only: the hook did not start the alert unit") was therefore decided from an unreadable query. For this run the
statement happens to be true (the full boot journal confirms it) but the harness had no evidence for it.

**Correction (harness only).** The collector (`collect_alert_journal`, `tools/vtest/stage_v5.py`) never sends a wildcard. It lists the unit names the journal's own index holds (`journalctl -F` on the four fields
above), keeps the alert instances, adds the two failure-hook instances that can exist, and queries each by exact name. Each query's argv, exit status and stderr are kept next to its text, and the text keeps
ISO timestamps and the unit's name. Rules (`attribution.alert_journal_problems`), applied by the stage, by the manifest and by the new whole-run status file:

* `empty` is accepted only as the proved result of successful queries: all four index listings exited 0, the index holds the poll unit (it runs every minute; `-F` also exits 0, empty, for a field that does
  not exist, so an index without the poll proves nothing), every alert instance the index lists and both hook instances were queried by exact name with exit 0, and none had an entry in the window.
* Any failed listing or query, an instance listed but not queried, a state that disagrees with the instances, or a missing query status is a failure of the scenario row (required), an incomplete archive and no
  basis for accepting a latch. The poll, daemon and monitor journals must also record an exit-0 query.
* An instance that finished before the capture is found: the index is journal-wide and not limited by the window or by whether the unit still exists.
* The detection-path row says "poll only" only when the journal was read; an unreadable journal is "undetermined".
* `alert_journal.txt` says `UNAVAILABLE: <reason>` for a failed query (never the old `(empty)` placeholder), and says what was queried when the collection was successfully empty.
  `alert_journal_status.json` (new, required, content-checked) records the whole-run queries and their status.

The failed run's own capture is judged incomplete under these rules (the alert journal unreadable; the other journals carry no query status): the archive of that run could not become complete under the corrected harness.

**Verification done.** Failing regression tests first (the scripted machine's journal now behaves like the real command, so the old collector reproduces the exact message at scenario level), then the fix:
`tests/test_vtest_v5_journal.py` (new) plus the extended V5 and evidence suites; mutating each rule of the collector, the validators and the manifest wiring makes a test fail (one equivalent mutant survives:
the manifest already treats any `UNAVAILABLE:` file as missing). The collector was also run against real journalctl and a private journald, for a poll-only journal, a journal with indexed instances, and a
journal that lacks the poll unit.

**Limitations.** The private journald could not attribute messages to units by cgroup, so unit entries were seeded through the plain `UNIT=` field: the exit statuses, the index listing and the glob-free
behavior are real, but an instance's entry text being matched by an exact `-u` filter, and the `-F` listing of a long-lived real journal, are confirmed only by the earlier real runs and the source, not by this
reproduction. The alert path's inability to write the latch still rests on the recorded commands, the sandbox properties and the source, and the daemon's own stale-heartbeat trip has still not been preserved
and attributed on a real host. The 2026-11-17 failure stays unexplained by direct evidence beyond what is written above.

**Smallest justified real-system retest.** One V5-only smoke on the corrected revision, whose job is to produce the missing evidence: it must reach and judge all four hold scenarios, and a complete archive. A
qualifying run is a separate, later decision.
