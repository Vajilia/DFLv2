# OOM incident record

**Status: implemented and tested locally; not validated under real systemd.** Three candidates exist, none of them validated:

| Candidate | Commit | State |
|---|---|---|
| First | `bfa632b` | Failed validation stage V2b: no latch after an OOM, the restarted daemon carried on. Unchanged. |
| Second | `47ed870` | Records the OOM from `ExecStopPost=`. Had a known bypass (section 3). Unchanged. |
| Third (this one) | branch `backend/memory-monitor-candidate3-2026-11-10` | Closes that bypass with a run marker and a start gate. |

Nothing here was deployed, installed or run on a host with systemd.

## 1. What went wrong in the first candidate

A validation run on a disposable cgroup-v2/systemd machine (stage V2b) killed a process of the daemon's service cgroup with the kernel's OOM killer. systemd (`OOMPolicy=stop`, `Restart=on-failure`) stopped the unit and started a new daemon within seconds. **No latch was written and the broadcast was not held.** The only recorder was the monitor, which polls `memory.events` every 10 s; systemd recreates the cgroup on restart, so every counter goes back to zero and an OOM stopped and restarted between two polls leaves nothing to see. A transient counter is not a record.

## 2. The second candidate: record the OOM in the unit's lifecycle

```
kernel OOM-kills a process of the daemon's cgroup (the daemon or a child)
  -> systemd stops the unit (OOMPolicy=stop; result "oom-kill")
  -> ExecStopPost=  broadcast_run.py incident        runs after the stop, BEFORE Restart=
       1. PAUSE (the hold: one empty file)   2. the latch: guard/TRIPPED + trips.jsonl, kind oom_kill, source systemd   3. an evidence folder
  -> systemd starts a new daemon, which sees TRIPPED, keeps PAUSE and never advances the clock
```
It counts as an OOM stop when systemd's result is `oom-kill` **or** the cgroup's own `oom_kill` counter is above zero. The victim is named from the kernel log when the service user can read it (`victims`, `victim_status`, `daemon_was_victim`); otherwise it is recorded as **unknown**, never assumed to be the daemon. A stop that is not an OOM (clean stop, crash, `kill -9`, deploy restart) records no incident.

## 3. The bypass that remained, and how it is closed

If the hook **could not write** (the guard folder unwritable or full), nothing was left: no latch, and often no `PAUSE`. A flag on a tmpfs is not an answer (gone after a reboot, and written at the same moment storage is failing). The restarted daemon then carried on.

The fix inverts the dependency: **write the memory while storage is known to work, and make forgetting impossible to miss.**

* **Run marker** (`guard/RUN_OPEN`, JSON with `opened_at`, `boot_id`, `invocation_id`). `ExecStartPre= broadcast_run.py gate` writes it before the daemon starts (temporary file, flush, rename, flush the folder).
* **Closing it.** `ExecStopPost=` removes it only when the stop is *accounted for*: an OOM that was recorded (the latch now carries it), or a stop systemd says was not an OOM (`$SERVICE_RESULT` is set and is not `oom-kill`, and the cgroup counter shows none).
* **Not closing it.** If the OOM could not be recorded, if the marker cannot be removed, or if systemd gave no result, the marker stays and the hook exits 1.
* **The gate.** At the next start, a marker still there means a stop nobody accounted for. The gate writes the latch (kind `unaccounted_stop`, with `PAUSE` and an evidence folder naming the previous run and whether the boot id changed), then opens a fresh marker, and the daemon starts **held**. If it cannot write the latch, or cannot write the new marker, it **exits 1: ExecStartPre= failing means systemd does not start the daemon.** systemd retries (`Restart=on-failure`); every attempt is refused while storage is broken, and the first attempt after it recovers writes the latch. After `StartLimitBurst=3` refusals in an hour the unit stays failed until someone resets it.
* The latch is now made **whole or not at all** (temporary file, flushed, linked into place): a full disk can no longer leave an empty `TRIPPED` that cannot be read or released.
* `RequiresMountsFor=` (in `[Unit]`) orders the unit before the unmount of what it writes to, so that a clean shutdown closes the marker.

| Event | Hook | Next start |
|---|---|---|
| OOM, recorded | latch + hold + evidence; marker closed | starts held |
| OOM, hook cannot write anything | exit 1; marker stays | gate refuses while storage is broken; latches `unaccounted_stop` once it works |
| `kill -9`, crash, `systemctl restart`, deploy | marker closed | starts normally, no latch |
| the same, but the hook cannot close the marker | exit 1; marker stays | held (ambiguous, so held) |
| clean host shutdown / reboot | marker closed (if ExecStopPost= ran: unverified) | starts normally |
| host dies uncleanly (power loss, panic, reset) | did not run | **held** (`unaccounted_stop`, `boot id changed`) |
| first ever start | | marker opened, starts normally |

**A behaviour change to decide on.** An unclean host death can no longer be told apart from "an OOM whose hook failed to record it", so it now starts **held** and needs `release` then `resume`, where before the daemon carried on. This is the cost of a fail-safe that does not depend on storage working at the moment of the incident. A plain daemon crash is unaffected (the hook runs and closes the marker).

Clearing is as for any latch: `rehearsal_check.py release`, then `broadcast_run.py resume`. A fresh heartbeat, a new daemon, a restarted monitor, a reset cgroup counter and a reboot do not clear it, and `release` does not touch the marker.

## 4. What was verified locally

* `tests/test_run_marker.py` (logic) and `tests/test_service_sandbox.py` were written first; they failed on the second candidate.
* **As an unprivileged user** (`nobody` via `setpriv`, `python3 -I`, a root-owned read-only program tree, separate processes standing for the gate, the hook and the daemon) against **real permission faults** (the guard and store folders turn root-owned and read-only: every write fails with `EACCES` from the kernel): the hook fails loudly and leaves the marker; the gate refuses while storage is broken, however often it is retried; storage recovering before the restart gives a latch and a held daemon whose clock does not move; a host restart while broken changes nothing and recovery still latches; a crash whose marker cannot be closed is held after recovery; a healthy cycle (start, crash, restart, OOM, restart) latches only on the OOM.
* **A genuinely full filesystem** (`ENOSPC`: the guard folder is a 48 KiB tmpfs in a private mount namespace). This showed that the earlier latch write could leave an empty `TRIPPED`; the whole-or-nothing write fixed it.
* `systemd-analyze verify` (systemd 255, the same series as the target OS) **parses** both unit templates without ignoring a setting. It caught `RequiresMountsFor=` placed in `[Service]` instead of `[Unit]`. It does not run anything.
* A mutation check: nine deliberate breakages of the gate, hook and latch were each caught by the tests (one was not a real mutant and was redone).
* The vtest harness's unit renderer accepts the new template (see the harness notes).

## 5. Still unverified (needs real systemd)

1. `ExecStartPre=` runs before every start including `Restart=` restarts, and its failure stops the start; the retry/`StartLimitBurst` behaviour described in section 3.
2. `ExecStopPost=` finishes before the next start after an OOM stop (so the latch precedes any restarted daemon), and runs with `$SERVICE_RESULT`, `$MAINPID`, `$EXIT_CODE`, `$EXIT_STATUS` set as assumed, for a child victim and for the daemon as victim.
3. **A clean reboot closes the marker.** If `ExecStopPost=` does not complete at shutdown (ordering, timeout), every reboot would start held: the most likely way this design misbehaves, and what stage V4's reboot case must show.
4. The cgroup's `memory.events` is readable while the hook runs.
5. As the service user under `ProtectSystem=strict` and `ReadWritePaths=`, the gate and hook can write `RUN_OPEN`, the latch, `PAUSE` and the evidence folder.
6. Timing: the hook finishes inside `TimeoutStopSec=30`.
7. The kernel log is probably unreadable by the service user, so a production victim is `unknown`; granting log access is a deployment decision not made here.
8. A real unclean power-off: the marker survives it and the next start is held.

## 6. Limits

* An unclean host death holds the broadcast (section 3).
* A daemon started outside the unit (without the gate) is not covered.
* The marker and the latch live in the guard folder: erasing the folder erases both.
* One more tick may complete between the OOM and the unit's stop; the record precedes any restarted daemon.
* An OOM outside the daemon's cgroup is not covered here (the monitor's own death is covered by `monitor_silent`).
* If the gate and the hook both fail and the unit then exhausts its start limit, the unit stays failed: safe, but it needs a person.

## 7. Stages to re-run on a real machine

See `docs/MEMORY_MONITOR_VALIDATION_PLAN.md` and the harness README: **V2** (V2a, V2b, V2c), **V3** (no false latch; monitor death and hang), **V4** (latch persistence, now including the unaccounted-stop gate and a real reboot). V1 is not changed by this fix but runs first in the harness.
