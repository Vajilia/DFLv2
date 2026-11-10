# OOM incident record (second candidate)

**Status: implemented and tested locally; not validated under real systemd.** The first candidate (`bfa632b`) is unchanged and is not validated. This change is a second candidate, branch `backend/memory-monitor-candidate2-2026-11-10`, made from `bfa632b`. Nothing here was deployed, installed or run on a host.

## 1. What went wrong

A validation run on a disposable cgroup-v2/systemd machine (stage V2b) killed a process of the daemon's service cgroup with the kernel's OOM killer. systemd (`OOMPolicy=stop`, `Restart=on-failure`) stopped the unit and started a new daemon within seconds. **No latch was written and the broadcast was not held**: the new daemon carried on. The requirement was that a confirmed OOM leaves a persistent incident record and holds the broadcast until a person releases it.

Why: the only recorder was the monitor, which polls `memory.events` of the daemon's cgroup every 10 s. systemd recreates the cgroup on restart, so every counter goes back to zero; an OOM that is stopped and restarted between two polls leaves nothing to see. A transient counter is not a record.

## 2. The change

The record is written by the unit's own lifecycle, which does not depend on polling.

```
kernel OOM-kills a process of the daemon's cgroup (the daemon or a child)
  -> systemd stops the unit (OOMPolicy=stop; result "oom-kill")
  -> ExecStopPost=  broadcast_run.py incident        <- NEW: runs after the stop, BEFORE Restart=
       1. PAUSE (the hold; one empty file, works even if the latch cannot be written)
       2. the latch: guard/TRIPPED + guard/trips.jsonl, kind oom_kill, source systemd  (fsynced, folder entry fsynced; retried)
       3. guard/evidence/trip-<time>-oom_kill/evidence.json
  -> systemd waits RestartSec, starts a new daemon
  -> the new daemon sees TRIPPED, keeps PAUSE, never advances the clock
```

| Piece | Change |
|---|---|
| `tools/broadcast_run.py incident` (new) | The recorder `ExecStopPost=` runs. Reads `$SERVICE_RESULT`, `$MAINPID`, `$EXIT_CODE`, `$EXIT_STATUS` from systemd and the unit cgroup's `memory.events`. |
| `write_trip` | Now also fsyncs the guard folder after creating `TRIPPED` (the file's own fsync does not make its name durable). An `OSError` means the latch was not made. |
| `Guard.step` (daemon) | A guard folder the daemon cannot write holds the broadcast (an incident could not be recorded either). |
| `deploy/supervisor/dfl-broadcast-daemon.service` | `ExecStopPost=` added. `OOMPolicy=stop` was already there. |
| `deploy/rehearsal/dfl-rehearsal.service` | `OOMPolicy=stop` and `ExecStopPost=` added (the same daemon; this template is a repository file, nothing was installed from it). |
| The monitor | **Unchanged.** Its polling rule stays as a second, independent signal. |

**What counts as an OOM stop:** systemd's result is `oom-kill`, **or** the cgroup's own `oom_kill` counter is above zero at stop time, whatever the stop was called. **Anything else records nothing**: a clean stop, a crash, `kill -9`, a deploy restart or a start failure goes on exactly as before.

**The victim is explicit.** The recorder names the victim from the kernel's `oom-kill:` lines for tasks of this cgroup (any depth): `victims` (pid, comm), `victim_status` (`identified` or `unknown`), `daemon_main_pid` and `daemon_was_victim` (`true`, `false`, or `null` when unknown). A child is recorded as a child; an unreadable kernel log gives `unknown`, never "the daemon". The latch's `detail` says which.

**If recording fails:** the latch is retried (0.5 s, 1 s). If it still cannot be written, PAUSE (made first) still holds, the recorder prints `{"incident": "unrecorded", ...}` on stderr and exits 1, so the unit shows as failed in `systemctl`/the journal. If the evidence folder cannot be written, the latch is kept and the failure is reported.

**What clears it:** only `rehearsal_check.py release` followed by `broadcast_run.py resume`, as for any latch. A fresh heartbeat, a monitor that starts later, a new daemon, a reset cgroup counter and a host restart do not.

## 3. What was verified locally (no systemd)

`tests/test_oom_incident.py` (written first; all 21 of its tests, and the new template test, failed on the frozen code) and the extended `tests/test_unit_templates.py`: the recorder's latch, evidence and hold; a child victim named as a child; the daemon as victim; other cgroups' victims ignored; unknown victim never assumed; counter-only OOM; non-OOM stops record nothing; an earlier latch stays first while the OOM is still on record; retry, unrecorded, nothing-writable and evidence-failure paths; the folder fsync after the latch; a daemon with an unwritable guard holds; the latch survives a fresh heartbeat, a reset counter, and separate OS processes standing for the recorder, a restarted daemon and a "host restart" (old heartbeat, PAUSE removed). The templates parse with the tool, set `OOMPolicy=stop`, and the recorder uses the unit's own cgroup, guard and root. A mutation check (eight deliberate breakages of the recorder) was caught by the tests (one test had to be tightened first). The vtest harness's unit renderer accepts the new template unchanged (checked by rendering it; nothing was installed).

## 4. What still needs real systemd (not shown by anything above)

1. **Ordering.** `ExecStopPost=` really finishes before `Restart=` starts the new daemon after an OOM stop.
2. **Environment.** `$SERVICE_RESULT=oom-kill` for a stop caused by `OOMPolicy=stop`, when the victim is a **child** and when it is **the daemon itself**; which of `$MAINPID`, `$EXIT_CODE`, `$EXIT_STATUS` are set then (the recorder tolerates their absence).
3. **The cgroup during stop.** `memory.events` is still readable, with `oom_kill` above zero, while `ExecStopPost=` runs.
4. **The sandbox.** As the service user under `ProtectSystem=strict` and the unit's `ReadWritePaths=`, the recorder can write `PAUSE`, the latch and the evidence folder.
5. **Timing.** The recorder finishes well inside `TimeoutStopSec=30` (it shares it).
6. **The kernel log.** Whether the service user can read it. Expected: **no**, so the victim is `unknown` in production. On the disposable machine the harness runs as root and should identify it. Deciding whether the unit may read the kernel log (for example the `systemd-journal` group) is the owner's call and was not made here.
7. **Failure paths with a real fault.** A read-only or full guard folder: PAUSE still holds, the unit shows failed, a daemon started over it holds.
8. **No false latch.** `kill -9` of the daemon, `systemctl stop/restart`, a crash loop to the start limit, the monitor's death and hang: none writes an incident, and the daemon restarts as before.
9. **Persistence.** The latch across a daemon restart, a monitor restart and a **host reboot** on the real filesystem.

## 5. Known gaps

* If the latch **and** PAUSE both fail and the fault then clears before the restart, the restarted daemon runs unlatched. The failure is loud (failed unit, stderr line) but not enforced. A flag on a tmpfs (`RuntimeDirectory=`) that the daemon refuses to start over would close it; it was not added, to keep this change small.
* The daemon may complete one more tick between the OOM and the unit's stop. The record is written before any **restarted** daemon exists.
* An OOM kill outside the daemon's cgroup is not covered here (the monitor's own death is covered by the existing `monitor_silent` guard).
* On a deployment where `OOMPolicy=continue` is in force, the unit is not stopped, `ExecStopPost=` does not run, and only the monitor's polling remains. Both templates set `OOMPolicy=stop` explicitly.

## 6. Stages to re-run on a real machine

* **V2 (V2a, V2b, V2c)**: V2b's `incident_record_and_hold` must pass; V2c exercises the daemon being the victim.
* **V4 (latch persistence)**: the latch across daemon, monitor and host restarts, including a reboot.
* **V3 (monitor death and hang)**: as a negative control: a daemon or monitor stop that is not an OOM must not latch.
* **V1** is not changed by this fix (the memory rule is untouched); it runs first in the harness and re-checks installation of the new pin.
* The harness pin (`tools/vtest/PIN`) still names the first candidate and was not changed here.
