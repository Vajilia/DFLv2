# Failure alerts: design (2026-11-12)

**Status: design only.** Nothing here is implemented, installed, enabled or connected. No credential is needed to read it and none appears in it. It answers two open findings from the first systemd validation of candidate 4 (`205f891`): `docs/RESTART_BUDGET_FINDING_2026-11-12.md` (the daemon's start budget runs out and nothing tells a person) and the monitor finding in `docs/LAUNCH_ROADMAP_EVIDENCE.md` section 3 (the monitor exits 2 by design when it cannot write its latch, stays stopped, and nothing tells a person).

## What must alert

| Event | How it shows in systemd | Consequence if nobody knows |
|---|---|---|
| **Monitor terminated** (exit 2 after a failed latch write, or any other stop that leaves it inactive) | `dfl-broadcast-monitor.service` enters `failed` (`Result=exit-code`; `RestartPreventExitStatus=1 2` stops the automatic restart), or stays `inactive` after a stop | The monitor's heartbeat goes silent, so the daemon pauses itself after its stale limit. Publication stops, and with it every protection the monitor gives, until a person starts the monitor. |
| **Daemon start budget exhausted** (`StartLimitBurst=3` in 3600 s, `StartLimitAction=none`) | `dfl-broadcast-daemon.service` enters `failed` with `Result=start-limit-hit` | The daemon is not running and will not be started again by systemd. Publication is stopped. |
| **Budget nearly used** (warning) | `NRestarts` in the current window is at 2 of 3 | The next crash will be refused. |

## Constraints

1. **Must work when the monitor is dead.** The alert path may not depend on the monitor, the daemon, the supervisor, the guard folder, the store or the public folder being healthy. It depends on systemd, one small script and the delivery channel.
2. **Recovery preserves safety latches.** The alert path **never** writes to the guard folder, the evidence folders, the store or the public folder, and never runs `release`, `resume`, `reset-failed` or `start`. A person does those, after reading the evidence. There is no automatic recovery in this design: an automatic monitor restart would flap while storage is still broken, and an automatic release would defeat the latch.
3. **No secret in the repository.** The delivery command and its credential live in a root-only (0600) environment file on the host, chosen and supplied by the owner. The repository holds only the script and unit templates.
4. **Not part of the candidate.** The candidate's unit files, daemon and monitor stay byte-for-byte as pinned. Everything below is a separate package added beside them (see "Pin and validation").
5. **Host death is out of reach of any on-host alert.** The off-host uptime check on the public `status.json` (`docs/UNATTENDED_OPERATION.md` section 7) stays part of the minimum.

## Design

**Layer A: event-driven, on the host.** A systemd drop-in for each protected unit (`/etc/systemd/system/<unit>.d/alert.conf`, outside the candidate's unit files) adds `OnFailure=dfl-alert@%n.service`. The template unit `dfl-alert@.service` (`Type=oneshot`, `ProtectSystem=strict`, `ReadWritePaths=` only its own alert folder, no access to the guard, store or public folders) runs `dfl-alert <unit>`, which:

1. reads, never writes: `systemctl show -p Result,ExecMainStatus,NRestarts,ActiveEnterTimestamp <unit>` and the last 20 journal lines of the unit;
2. **writes a record first**, always, in its own root-only folder (`/var/lib/dfl-alerts/<UTC time>-<unit>.json`): which unit, which of the two events above, the values read, the recovery text below; the record exists even if delivery then fails;
3. calls the owner's delivery command from the environment file with a short text (severity, unit, one-line meaning, "read the evidence before resetting");
4. marks the record `delivered` or `delivery_failed`, and exits non-zero on `delivery_failed` so `systemctl --failed` shows it;
5. rate-limits: at most one alert per unit and event per 10 minutes (a failing restart loop must not flood the channel).

**Layer B: a one-minute poll, because events can be missed.** `OnFailure=` does not fire when a unit is simply `inactive` (stopped by hand and never started again, not started after a reboot), and a lost delivery needs a retry. `dfl-alert-poll.timer` runs `dfl-alert --poll` every minute. It reads `systemctl show` for the daemon and monitor, and the age of the guard folder's `heartbeat` file (stat only), and:

* raises *monitor not running* if the monitor is not `active` for more than two polls, and *daemon budget exhausted* if `Result=start-limit-hit`;
* raises the *budget nearly used* warning at 2 of 3;
* retries every record marked `delivery_failed`;
* reports the existence of a `TRIPPED` latch **as information only** (it is the safety latch working; it is not an error to be cleared).

**Layer C: off-host.** One external check of the public `status.json` (unchanged from `docs/UNATTENDED_OPERATION.md` section 7).

## What the alert text tells the person

1. *What stopped and why* (the values read above).
2. *Do not reset blindly.* Read `systemctl status` and `journalctl` for the unit, and look at the guard folder: a `TRIPPED` file is a safety latch.
3. *Monitor:* start the monitor first (`systemctl reset-failed dfl-broadcast-monitor.service`, then `systemctl start`), confirm its heartbeat is fresh, and only then follow the documented release and resume if a latch is present.
4. *Daemon:* `systemctl reset-failed dfl-broadcast-daemon.service` clears systemd's counters only and touches no latch; the daemon may come back **held** (the start gate's fail-safe); publication resumes only after a person runs `release` then `resume`.
5. Never delete public files, the run marker or the latch to make something start.

## Assumptions that must be checked on a real systemd, not argued

* That `OnFailure=` fires for the monitor's exit-2 failure and for `start-limit-hit`, on the systemd version of the target host, and **how often it fires during an auto-restart loop** (the rate limit and the poll are written to be correct either way).
* That the alert unit runs with the monitor dead, the guard folder immutable, and the daemon held.
* That the alert path leaves the guard folder, evidence, store and public folder byte-identical.
* That a failed delivery leaves a record and is retried.

## Pin and validation

* **No new candidate pin.** The alert package adds files and drop-ins; it does not edit the candidate's unit files, daemon or monitor. The pin gate stays green. If the owner later chooses to put `OnFailure=` or a different `StartLimit*` into the candidate's own unit files, that **is** a candidate change (new pin, full V0-V4 cycle, `docs/CANDIDATE_HISTORY.md`).
* **New stage V5 (alert drill), harness side:** kill the monitor the way q3b did (exit 2), exhaust the daemon's budget, and show for each that a record appears, that delivery reaches a sink the harness controls (a local file: no credential on the disposable machine), that the guard folder, evidence, store and public folder are byte-identical before and after, and that the alert path never ran `release`, `resume`, `reset-failed` or `start`. Tests come first (the script's dedupe, no-write and no-recovery properties are unit-testable without systemd).
* **Interference check:** with the drop-ins installed, V3 and V4 are repeated, because the alert unit must not change what they judge.
* **Delivery to the real channel** is a separate owner drill (one clearly labelled test message); it needs the owner's channel and credential and is not part of the disposable-machine session.

## Decisions for the owner

* The delivery channel (e-mail, phone push, chat) and who holds its credential.
* Whether the monitor's exit-2 behaviour should also change (for example a supervised restart that waits for storage to recover, never releasing a latch): a candidate change, deliberately not proposed here, because alerts remove the silence without altering what the candidate does.
