# Validation criteria: what counts as passing, fixed before any further run

**Status.** Written and committed BEFORE the harness changes that implement it and before any further run. It supersedes the V1 pressure gate of `docs/MEMORY_MONITOR_VALIDATION_PLAN.md` (section 1, row V1: "pressure stays low"). Nothing below was chosen by looking at samples from an earlier run; every number is a constant of the pinned candidate (`bfa632b0e8961d9d576bdcded86abea90a3cc675`) or is marked as a sample-size or resolution choice.

**The earlier result is not rewritten.** The first smoke run (`smoke1`, harness `24a805d`) failed V1 on a memory-pressure gate of 5% ("full" average over 60 s). That failure stays what it is: a recorded failure of that gate on that run. Its evidence is private to the owner and is not in this repository. A run made under this document is a new run with a new id; it does not replace, re-grade or "fix" `smoke1`.

## 1. Why the gate is replaced

The 5% figure was a bound the harness chose; it was not derived from broadcast behaviour, and the memory-pressure file it read belongs to the daemon's cgroup, which also holds the injected loader. Pressure there cannot be attributed to the daemon, and it says nothing by itself about whether viewers were affected. What viewers see is decided by the broadcast clock, the publication of live segments, and the daemon's lease. Those can be measured directly, and the pinned design says what they must do.

## 2. Three kinds of check, kept apart

| Kind | Question | Decides pass/fail |
|---|---|---|
| **Monitor correctness (M)** | Does the monitor and its rule behave correctly (no false trip, no kill, estimates consistent with a known input, probes, breakdown)? | yes |
| **Broadcast performance (B)** | Did the broadcast do what the design says while the machine was under that condition? | yes |
| **Diagnostics (D)** | What did the kernel report? (memory pressure, reclaim counters) | no: recorded, never gating |

A V1 result is `pass` only if every M and B check passes. A D row is recorded with status `info`.

## 3. Design constants the criteria use

All from the pinned tree. `vtest.py pin-check` compares the harness's copy of each with the tree's and fails on any difference.

| Constant | Value | Where in the pinned tree | Meaning |
|---|---|---|---|
| `SEG_S` | 5 s | `src/dflsite/broadcast.py:31` | a live segment holds the plays that start in these seconds of game time; segment n is released when the game is n x `SEG_S` seconds in |
| `DELAY_S` | 3 s | `broadcast.py:32` | viewers show the game this far behind the released data; this is also the publication horizon the clock may run ahead of the last tick (`broadcast.py:494`, `season.py:_tick`: `ready_until = pos + DELAY_S`) |
| `LEASE_S` | 90 s | `broadcast.py:34` | the clock stands still this long after the daemon's last heartbeat |
| lease renewal | when under `LEASE_S - 5` remains | `broadcast.py:149,157` | renewed about every 5 s |
| daemon tick | 1.0 s | `tools/broadcast_run.py` (`--tick`, default) | |

Consequences of the design that the criteria rest on:

* **A late tick is invisible up to `DELAY_S`, and a stall beyond it.** After each tick the clock may advance only to `ready_until = position + DELAY_S` (`clock_limit`, `broadcast.py:76`). If the next tick is later than that, the clock stands still until it arrives. The viewer sees a freeze.
* **A segment is needed by viewers at position `start + n x SEG_S + DELAY_S`.** A viewer shows game time `position - start - DELAY_S`, and shows the plays that start at or before it. A segment published after that position has been reached is an underrun: the viewer has nothing to show for the next plays.
* **A lapsed lease stops the clock and is recorded.** The daemon writes the gap as a pause (`clock_log.jsonl`, why `downtime`, or `publish_failure` after a failed publication).

## 4. Monitor-correctness criteria (M)

Unchanged from the existing V1 checks, which are kept as they are (ids and logic in `tools/vtest/stage_v1.py`): `V1.workload_was_real`, `V1.kernel_reclaimed`, `V1.no_trip`, `V1.survival`, `V1.estimate_consistent_with_known_input`, `V1.reclaim_probes`, `V1.heartbeat_records_breakdown`. One addition: `V1.no_trip` also applies to the publication-only phase (section 6), so a false trip under representative publication activity is a failure too.

## 5. Broadcast-performance criteria (B), measured directly

Measured from the daemon's own files, once a second, outside the daemon's cgroup; no change to the code under test.

| Id | Criterion | Rationale |
|---|---|---|
| **B1 clock stall** | No sample at which the clock is running, not paused, and has stood still for more than one sampling interval (1 s) beyond its own limit: i.e. `now - clock_limit <= 1 s` at every sample. | A stall is what a late tick or a lapsed lease looks like to a viewer. 1 s is the resolution of the observation, not a tolerance on the design. |
| **B2 lease** | `lease_until > now` at every sample, and no `downtime` or `publish_failure` entry in `clock_log.jsonl` during the phase. | A lapse stops the clock for every viewer; the design records it. No other margin is claimed: the observed margin is reported (see D). |
| **B3 publication deadline** | Every live segment of every game on air is published no later than the wall time at which the clock reaches `start + n x SEG_S + DELAY_S` (plus the 1 s observation resolution); every game of the window ends with all its segments and state `final`. | This is the viewer's underrun condition, per segment, from file modification times and the clock's own position. Only applicable while games are on air (section 6, phase P); in the stress phase it is reported `not_run`, never silently passed. |
| **B4 no publication error** | No `error` line from the daemon in its journal for the phase. | The daemon logs a publication failure as JSON on stderr (`broadcast_run.py`, PUBLISH FAILURE). |

Each B check also records what it measured (worst gap, minimum lease margin, the latest segment lateness) in `result.json`. Those figures are reported to the owner; they are not thresholds.

## 6. Cache stress kept apart from publication activity

V1 now has two phases, run in this order on the same machine, never overlapping:

* **Phase P, publication only.** No cache loader. The daemon publishes the fixture's first window: two games, 134 segments each (the fixture's games are 670.45 s long and kick off at position 200 s). Applies M `V1.no_trip` and B1-B4. This is the representative-activity phase: the daemon doing what it does in production, publishing live segments on its clock, with nothing else on the machine but the monitor and the loopback site server.
* **Phase C, cache stress.** The existing clean-cache loader, unchanged, for `duration_s`. No live segment is being published (checked: `V1.separation` requires that no segment file was written during the phase and the clock stayed before the fixture's next window). Applies all M checks and B1, B2, B4.

Why they are separate: in phase C the cgroup holds an unthrottled read loop that hits every page it touches; the measured effect is of that load on the daemon's tick, lease and the monitor's rule. Mixing it with publication in one run would make a failure unattributable. A combined phase is not in this revision; it is listed under section 8.

A tracked consequence: the earlier smoke run's whole V1 stage lasted 178 s, and the clock starts at 0 when the daemon starts, so it ended before the fixture's first window (position 200 s). It therefore cannot have published a live segment, and could not have shown any publication effect either way. Phase P exists so that this cannot happen again, and so that a run which never reaches a live window is reported `invalid`, not `pass`.

## 7. Diagnostics (D), recorded and not gating

* Cgroup memory pressure (`some` and `full`, `avg10`/`avg60` and the cumulative stall) for each phase, and what share of the 60 s average it had reached at the end (the average lags the load).
* Reclaim counters, the loader's read rate, the lease margin distribution, the tick-age distribution.
* Host-wide pressure is not sampled (not needed for any B check); the cgroup pressure includes the loader and cannot be attributed to the daemon, and the report says so.

Pressure becomes a gate only if a later run shows that it predicts a B failure and the owner decides so in writing.

## 8. What this revision does not do

* No combined cache-stress-and-publication phase (it needs the fixture's third game or a different fixture; the fixture is the pinned tree's and is not changed here).
* No per-process stall accounting, no host pressure, no new probes in the daemon.
* V2 to V4 are unchanged. They remain untested; the candidate is not validated by anything in this document.
* Sample sizes (phase P covers 268 segments; phase C runs `duration_s`) are choices that set how much evidence there is. Because the B criteria are zero-tolerance counts against design constants, a longer run can only add failures, never remove one.

## 9. Evidence manifest and archive completeness

`vtest.py archive --run-id ID` writes `EVIDENCE_MANIFEST.json` and `.txt` listing every item a run must leave, with its size and SHA-256, or the explicit status `MISSING` with the reason, or `NOT_APPLICABLE` with the reason. Required items: the run record and result summary; per stage the result, check manifest, timeline and samples (cgroup samples, loader log, clock and publication probe, monitor log, trip log); the unit journals (daemon, monitor, site) for the run's time span; the kernel log and the OOM lines extracted from it; the systemd unit states and the failed-unit list; machine facts; and the harness command output. It then builds one compressed archive, reads it back, and checks every `present` item inside it against the manifest. The command exits non-zero, and prints that the host must not be cleaned up, if the archive does not verify or a required item is `MISSING`. `teardown --include-evidence` refuses until a verified archive record exists for the evidence it would remove.

The archive is copied off the machine by the owner's approved private method. This document does not change what that is, and nothing here is published to GitHub.
