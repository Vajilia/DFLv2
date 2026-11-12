# Operational finding: restart-budget exhaustion can leave publication stopped

Found 2026-11-12 on the first systemd validation of candidate 4 (`205f891`): smoke-4, stage V4. **The candidate is unchanged by this note.** Whether to change the limit is the owner's decision (a new candidate); this page records what happens, what would and would not tell a person, and how to recover without clearing a safety latch.

## What happens

`dfl-broadcast-daemon.service` has `StartLimitIntervalSec=3600`, `StartLimitBurst=3`, `StartLimitAction=none` (the monitor: 5 in 3600 s). Every start counts: boot, `systemctl start`, `systemctl restart`, and the automatic restart after a crash (`Restart=on-failure`, `RestartSec=5`). The fourth start inside an hour is refused by systemd ("Start request repeated too quickly") and the unit stays **failed**. Nothing retries. Publication stops until someone resets the unit. This is the documented design (`docs/OOM_INCIDENT_RECORD.md`, the start gate): it stops a crash loop.

Evidence (smoke-4, journal): daemon killed with SIGKILL at 08:29:51 UTC; at 08:29:56 "Scheduled restart job... Start request repeated too quickly... Failed to start". Earlier starts in the same hour were the harness's own restarts, which used the budget. The harness defect (no budget isolation between disturbances) is fixed separately (`9bc3197`); the limit itself is a property of the candidate.

## Would it alert? Verified by reading the code and its tests; not on a real host

* **Detected, if the supervisor runs.** `tools/broadcast_supervisor.py` raises `publication_stalled` (critical) when the clock is running and the daemon's lease has run out or the public heartbeat is older than `stall_s`. `check` then exits 2 (3 if the supervisor itself is dead or stale). Covered by `tests/test_broadcast_supervisor.py` (36 tests pass); a pause or hold is deliberately **not** a stall.
* **Not delivered to a person.** `docs/UNATTENDED_OPERATION.md` section 7: "The supervisor stops at a file and an exit code"; every delivery option is "options only (nothing is sent, created or connected)". The supervisor and check-timer unit files are templates: "nothing was installed, enabled or started". With the check timer alone the only visible result is a failed oneshot in `systemctl --failed` and the journal: nobody is woken. An on-host check also cannot report that the host is gone.
* **Verdict: this state does NOT yet reach a person.** An alert channel (the section 7 minimum is the check timer plus one off-host uptime check on the public `status.json`) has to be chosen, installed and drilled before unattended production: exhaust the budget on a disposable host and confirm the alert arrives. Not done; needs an owner decision on the channel and credentials.

## Recovery that does not clear a safety latch

1. **Read before resetting.** `systemctl status dfl-broadcast-daemon.service` and `journalctl -u dfl-broadcast-daemon.service -n 100`. Three starts in an hour means something is failing; a reset only re-arms the loop. Look at the guard folder: a `TRIPPED` file there is a safety latch.
2. **Reset the systemd counters only:** `systemctl reset-failed dfl-broadcast-daemon.service` (and `dfl-broadcast-monitor.service` if that is failed too), then `systemctl start dfl-broadcast-daemon.service`. `reset-failed` clears systemd's start counters and failed state; it does not touch the guard folder, the store or the public folder. V4 checks this on every step: the latch fingerprint, the public inventory and the trips log are compared before and after each restart and kill (smoke-5 result recorded in the session report).
3. **Expect it to come back held.** The killed run left its marker open; the start gate writes the `unaccounted_stop` latch and starts the daemon **held** (`docs/OOM_INCIDENT_RECORD.md`). That is the fail-safe working. Publication resumes only after a person has read the evidence and run `rehearsal_check.py release`, then `broadcast_run.py resume` (OOM record, "Clearing is as for any latch"). A reset, a restart, a new daemon, a fresh heartbeat and a reboot do not clear a latch, and `release` does not touch the marker. **Do not run `release` or `resume` as part of this recovery unless the latch's evidence has been read and is understood.**
4. Do not delete public files, the marker or the latch to "make it start".

## Decisions for the owner (none made here)

* Alert channel and a drill of this exact state (above): needed before unattended production.
* Whether the limit should change (higher burst, `StartLimitAction=`, an `OnFailure=` notifier, or an automatic recovery that still respects latches): a candidate change, with a new approval.
