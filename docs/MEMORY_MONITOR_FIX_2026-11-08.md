# Monitor memory rule: finding, fix, measurements and a proposed recovery

Branch `backend/memory-monitor-fix-2026-11-08`, from `59964d7` (whose code is identical to the frozen integration candidate `9f2a6e9`; the only commit between them is the freeze record).
**Local only. Nothing was deployed, installed, restarted, released or resumed. The Droplet was touched only by read-only commands (see `docs/evidence/memory_monitor/failed_rehearsal_2026-11-07/MANIFEST.md`).**
The failed rehearsal's `TRIPPED` latch is still in place, the broadcast is still paused at 55,743 s, and its logs and evidence are untouched.
This is a **new candidate**: under the freeze rule any code change is one. The change is confined to the monitor's memory check in `tools/rehearsal_check.py` (section 6). Nothing else that was validated at `9f2a6e9` was re-run unless section 5 says so.

## 1. What happened

On Nov 7 at 14:52:54 UTC (06:52 PST), 15.5 hours into the real-time rehearsal (position 55,743 s of 172,800), the monitor wrote `memory_high`: "daemon memory 578 MB of a 629 MB limit". The old rule (`tools/rehearsal_check.py` before this branch)
compared `memory.current` with 90 % of `memory.max` on **one** sample. `memory.current` includes the kernel's file cache, which the kernel gives back when it needs the room; a daemon that publishes tens of thousands of files fills the cache toward its limit and stays there.
The same two lines are in the recurring service's monitor (`deploy/supervisor/dfl-broadcast-monitor.service` passes `--cgroup` too, for a daemon whose MemoryMax is 192 MB), so the candidate had the same defect.

## 2. What is known and what is not

| | |
|---|---|
| **Known** (read from the daemon's cgroup after the trip) | `memory.events` low/high/max/oom/oom_kill all **0** since the unit started 31 h earlier; the group never reached its limit and nothing was killed. `memory.pressure` `total=0` for both `some` and `full`: not one microsecond of memory stall. `pgscan_direct 0`; `pgscan_kswapd 126,336` pages (about 493 MiB trimmed in the background). `MemoryHigh` unset. |
| Known (the daemon's own memory) | While paused it holds 22.4 MB of anonymous memory, 202.5 MB of file cache (24 KB dirty, 0 writeback, 0 mapped, 0 shared) and 45.0 MB of kernel memory, 44.8 MB of it reclaimable slab. Earlier measurements (`docs/BROADCAST_CONTINUITY.md`) put the daemon's own use at 26 to 32 MB and flat. `anon + file + kernel` equals `memory.current` exactly. |
| Known (the monitor's record) | Only `current` = 578,011,136 and `max` = 629,145,600. **No breakdown was recorded.** |
| Known (host graph) | DigitalOcean's memory graph is flat at about 24 % all day with no rise before the trip (its definition of "used" is unverified). |
| **NOT known** | **The composition of the 578 MB at 14:52:54 UTC.** It was not recorded and cannot be recovered. |
| Inference, not proof | It was mostly clean file cache and reclaimable slab: the peak equals the trip value (`MemoryPeak` 578,273,280 vs 578,011,136), nothing in the kernel's own counters shows pressure, and the program's own memory is tens of MB. Not excluded: a short burst of anonymous or dirty memory at that instant that has since been released. A burst that large (500 MB of 600) with zero direct reclaim, zero stalls and a flat host graph would be unusual, but the record cannot rule it out. |

So the finding is: **the trip was most likely a false alarm from a rule that counted reclaimable cache, but this is an inference.** The rule is fixed so that the next trip, true or false, carries the evidence this one lacks.

## 3. The rule

MemoryMax stays the hard boundary and is never read for writing, raised or lowered. The monitor reads the daemon's cgroup files (`memory.current`, `memory.max`, `memory.stat`, `memory.events`, `memory.pressure`) and computes:

* **reclaimable** = *clean, unmapped* file cache (`file − file_dirty − file_writeback − file_mapped − shmem`) + `slab_reclaimable`. Nothing else is discounted: dirty and writeback pages (they have to be written first), mapped files, shared memory, anonymous memory (there is no swap), unreclaimable kernel memory and page tables count in full. Where two of these overlap the overlap is subtracted twice, so the estimate is never too high. Without `memory.stat` nothing is discounted.
* **pinned** = `memory.current − reclaimable`.

The broadcast stops (the existing `memory_high` trip, same latch, same evidence folder) when either:

1. **Sustained:** `pinned` is above **90 % of `memory.max`** on every sample for **60 s** and at least **3 samples** (the monitor samples every 10 s, so six). A single high sample, or an excursion shorter than the window, resets nothing and trips nothing. It fires once per episode and re-arms when the memory comes back under the line.
2. **The kernel's own verdict:** `memory.events` `oom` or `oom_kill` rose since the last sample. At once. (The first sample sets the baseline; counts that existed when the monitor started are not new events.)

`high` and `max` events (the kernel reclaiming at the limit, which it does by design: the supervisor measurement saw 62,532 with no kill) and memory pressure (PSI) are **recorded and are not triggers**. A daemon that is really starved stops renewing its lease, which the monitor already reports as `daemon_silent`.

**What the monitor can and cannot catch.** After the pinned memory crosses the line the daemon has 10 % of MemoryMax left. If it grows faster than `0.1 × MemoryMax / 60 s` (about 1.0 MB/s for the rehearsal's 600 MB, 0.3 MB/s for the recurring daemon's 192 MB) the kernel reaches the limit before the 60 s window ends. Then the protection is the kernel's: it kills the process, `OOMPolicy=stop` stops the unit (the unit templates set it; its behaviour under real systemd is still deployment gate 1), the daemon stops renewing its lease and the monitor trips on `daemon_silent` and on the `oom_kill` event. That is the same protection as before this change. The sustained rule exists for the slow leak the kernel would reach minutes later, and it gives the operator the evidence first.

**What is recorded.** Every trip's evidence now holds `anon`, `file`, `file_dirty`, `file_writeback`, `file_mapped`, `shmem`, `kernel`, `slab_reclaimable`, `slab_unreclaimable`, `unevictable`, `active_file`, `inactive_file`, the full `memory.events`, the pressure files, the reclaimable and pinned estimates, the rule's parameters and the **last 12 samples** (two minutes). Every heartbeat line in `watch.log` (every 30 s) now carries a compact `mem` object (`current`, `max`, `anon`, `file`, `dirty`, `writeback`, `mapped`, `shmem`, `kernel`, `slab_reclaimable`, `pinned`, `events`, `psi_some10`, `psi_full10`), about 320 bytes a line (0.9 MB a day), so a run has a memory time series (the first rehearsal had none).

## 4. Tests

`tests/test_memory_rule.py` (22 tests; committed first, failing, in `18df1ee`): the real Droplet files parse and reconcile (`anon + file + kernel = current`); a series that fills the group with cache and slab past the old line never trips and stays untripped when held; `high`/`max` events do not trip; one sample and a 20 s excursion do not trip but the same number held does; the window is in seconds **and** samples; dropping under the line resets the count;
anonymous memory held near the limit trips with the full breakdown; the same 280 MB of file pages is healthy clean and a trip when dirty/writeback; mapped files, shared memory and unreclaimable slab are not discounted while reclaimable slab is; "only clean unmapped cache is discounted, never all file cache" is an equality on the estimate; an OOM event trips at once and old OOM counts at monitor start do not; no limit means no rule; pressure is recorded and never trips;
the Watch records the breakdown in the heartbeat line, trips with the evidence file, and trips on an `oom_kill` event. `tests/test_rehearsal_guard.py::test_resource_violations_trip` was updated on purpose: with no `memory.stat` the monitor discounts nothing, and one high sample no longer trips; a held one does.

**Mutation check** (`tools/memory_rule_mutants.py`): see section 5.

## 5. Measurements

### 5.1 Real workloads in kernel memory cgroups (`tools/memory_workloads.py`, `docs/evidence/memory_monitor/workloads_cgroup_v1/`)

Each scenario ran real processes in a real memory cgroup with a hard limit of **256 MiB** (a stand-in for MemoryMax), sampled once a second with the monitor's own reader, and judged every sample by the old rule (one reading of `memory.current` above 90 %) and by the new rule, live at 1 Hz with the production window and **replayed from the same trace at the monitor's real 10 s cadence at each of the ten possible phases**. Nothing stopped a workload when the rule tripped (the harness only observes), so "killed" is the kernel's own doing. Percentages are of the limit. `failcnt` is the number of times the group hit its limit (the v1 stand-in for `memory.events max`); pressure is the host's PSI (nothing else was running).

| scenario | peak current | peak pinned | peak anon | peak dirty+wb | PSI some / full avg10 | failcnt | old rule trips | new rule, live 1 Hz | new rule, 10 s cadence | kernel kill |
|---|---|---|---|---|---|---|---|---|---|---|
| `healthy_cache` | 100 % | 10 % | 7 % | 0 % | 0.2 / 0.2 | 83120 | 1 s | no trip | 0/10 phases | - |
| `anon_spike` | 94 % | 94 % | 93 % | 0 % | 0.0 / 0.0 | 0 | 6 s | no trip | 0/10 phases | - |
| `anon_plus_cache` | 100 % | 56 % | 55 % | 0 % | 0.4 / 0.2 | 89069 | 1 s | no trip | 0/10 phases | - |
| `anon_plateau` | 95 % | 94 % | 93 % | 0 % | 0.0 / 0.0 | 0 | 22 s | sustained_unreclaimable at 82 s | 10/10 phases (82 to 91 s) | - |
| `anon_slow_leak` | 100 % | 100 % | 100 % | 0 % | 0.0 / 0.0 | 105 | 47 s | sustained_unreclaimable at 107 s | 10/10 phases (107 to 116 s) | 134 s |
| `anon_fast_growth` | 100 % | 99 % | 99 % | 0 % | 0.0 / 0.0 | 1731 | 15 s | oom_event at 17 s | 10/10 phases (17 to 26 s) | 17 s |
| `dirty_flood` | 100 % | 100 % | 7 % | 90 % | 18.4 / 18.1 | 173186 | 4 s | no trip | 0/10 phases | - |
| `anon_plus_dirty` | 100 % | 100 % | 80 % | 54 % | 34.3 / 34.2 | 222589 | 1 s | no trip | 2/10 phases (72 to 101 s) | - |

What each shows:
* **`healthy_cache`**: `memory.current` reaches **100 %** of the limit and the kernel hit the limit **83,120 times** (reclaiming, as designed), yet only 10 % is unreclaimable, pressure never exceeded 0.2 %, and nothing was killed. The old rule trips in the first seconds; the new rule never does. This is the first rehearsal's situation, reproduced.
* **`anon_spike`** (93 % anonymous for 15 s, then freed) and **`anon_plus_cache`** (55 % anonymous plus a cache stream at 100 %): old rule trips, new rule does not, at any phase.
* **`anon_plateau`** (anonymous memory held at 93 %): the new rule trips after the 60 s window (live 82 s; 82 to 91 s depending on the phase; the line was first crossed at about 22 s) and no kill occurred.
* **`anon_slow_leak`** (85 %, then +0.3 MB/s; the kernel killed it at **134 s**): the new rule tripped at **107 s live (107 to 116 s at the 10 s cadence), 18 to 27 s before the kernel's kill**, at all ten phases. (Re-run once after a wrongly written check; see the report's `note`. The rule and the scenario are unchanged.)
* **`anon_fast_growth`** (15 MB/s past the limit): the kernel OOM-killed the workload at 17 s; the new rule tripped **on the `oom_kill` event at that same second** (at the 10 s cadence the ten phases saw it 0 to 9 s later, 17 to 26 s: the sampling lag). The sustained window could not have helped here and is not what protects: the kernel's boundary is.
* **`dirty_flood`** (a writer that never syncs): dirty plus writeback peaked at **90 %** of the limit and pinned memory touched 100 % in bursts of at most 4 s, with pressure `full` up to 18 %; nothing was killed. Dirty pages are counted as pinned (checked sample by sample) and the new rule did not trip because no burst lasted the window.
* **`anon_plus_dirty`** (80 % anonymous plus the dirty writer): pinned memory sawtoothed **between 80 % and 100 %** for 150 s with dirty plus writeback up to 54 %, pressure `full` up to **34 %**, and nothing was killed. **The verdict here depends on sampling phase**: the live 1 Hz judge did not trip (the longest unbroken run over the line was 8 s) and 2 of the 10 phases of the 10 s cadence did (their samples landed on the highs, 7 in a row). This is the rule's known soft spot (section 9).

### 5.2 What the pressure numbers say (recorded, not used as a trigger)

Memory pressure separates the cases by two orders of magnitude: 0.0 to 0.4 % for every healthy or single-cause run, 18 % and 34 % (`full avg10`) for the two dirty-flood runs. It is recorded in every sample and every trip's evidence. I did **not** make it a trigger: a stall is a performance problem rather than an integrity problem (a daemon that is really starved stops renewing its lease and is caught as `daemon_silent`), the floods here are harsher than the Droplet's cgroup-v2 dirty throttling, v1 gives only the host-wide file, and the Droplet's own daemon group shows `total=0` over 31 hours. If the owner wants stalls to stop the broadcast, `memory.pressure` `full avg60` above about 10 % on the cgroup's own file (never the host's) is the data-supported place to start; that would be a separate change with its own tests.

### 5.3 Mutation check (`tools/memory_rule_mutants.py`, `docs/evidence/memory_monitor/mutation_check.json`)

Baseline: 22 tests, none failing. Each of eight deliberate breakages is caught by at least one test: the old rule (6 tests fail), **subtracting all file cache** (3), treating all kernel memory as reclaimable (2), no sustain window (5), a window by time with no minimum sample count (1), a line above the limit, i.e. "just raise the limit" (7), ignoring the kernel's OOM events (3), tripping on `max` events (1).

### 5.4 Existing suites

Run on this branch after the change: `tests/test_memory_rule.py`, `test_rehearsal_check`, `test_rehearsal_guard`, `test_rehearsal_deploy`, `test_rehearsal_package`, `test_rehearsal_quarantine`, `test_rehearsal_traversal`, `test_rehearsal_umask`, `test_unit_templates`, `test_public_cap`, `test_monitor_multiseason`, `test_broadcast_run`: **150 tests, all pass** (157 s). `test_startup_check` and `test_integration_m6` (which start the monitor from the unit files' `ExecStart`): **7 tests, all pass** (58 s). (On this host the unit's `--cgroup` path does not exist, so those runs exercise "no cgroup files", not the rule.)
The rest of the repository's tests, and the heavier validations of `9f2a6e9` (boundary, reveal, dead-monitor, OOM, capacity), were **not re-run**: the change does not touch what they exercise.
The same suites against the **pinned** commit with the patch applied: only `tests/test_memory_rule.py` was run there (22 of 22 pass); the pin's own suites were not.


## 6. Scope: the smallest fix

Changed: `tools/rehearsal_check.py` only (the reader `read_cgroup_memory`, `memory_estimates`, `MemoryJudge`, `Watch.memory_check` replacing the 8-line block in `cheap()`, and the `mem` object in the heartbeat line; about 200 lines, of which most are the reader and the evidence). New: `tests/test_memory_rule.py`, `tests/fixtures/cgroup_v2_droplet_2026-11-08/`, `tools/memory_workloads.py`, `tools/memory_rule_mutants.py`, this document and `docs/evidence/memory_monitor/`. One test and one doc row were updated (`test_resource_violations_trip`, `docs/BROADCAST_REHEARSAL.md`).
**Not changed:** the daemon, the guard, the units (`MemoryMax`, `OOMPolicy`, `--every`, `--cgroup` paths), the engine, `src/dfl`, data, the site, league rules, the Droplet, the pin. No limit was raised and none was lowered.
The v1 branch of the reader exists only so the rule can be exercised against real workloads on a host without a v2 memory controller; the Droplet and the target units are v2.

## 7. Proposed recovery procedure (NOT executed; for the owner's decision)

The failed run **cannot pass**: its acceptance rule (`rehearsal_check.py verdict`) needs zero violations and no automatic pause, and it has one of each. Whatever is decided, it is now an evidence run.

**Decide first what the run is for.** The pinned code (`d2e66bb`) is not an ancestor of this branch, so 32 more hours on it say less about the recurring service than a fresh run of the new candidate would. Continuing buys the remaining 68 % of the real-time broadcast (events 23 to 47, honors, the later windows) on the old code, labelled as an extended evidence run. If that is not worth 32.5 more hours of attention, leave it paused and start clean.

**Option A: stop here and keep the evidence (my recommendation unless the extra data is wanted).**
1. Leave the latch, the logs and the paused clock exactly as they are. Do not run `release`.
2. Copy `/srv/dflv2-rehearsal/monitor/{TRIPPED,trips.jsonl,evidence/}` and a snapshot of `watch.log` to a second place with `cp -a` and a `sha256sum` manifest (copying changes nothing; it needs the owner's go-ahead because it writes a new directory).
3. When the new candidate passes its gates (including the systemd lifecycle gate), run a fresh 48 h rehearsal from it with this memory rule.

**Option B: continue the same run with a monitor-only patch.** The patch is `docs/evidence/memory_monitor/pinned_monitor_patch/rehearsal_check_memory_rule_on_d2e66bb.patch` (sha256 of `rehearsal_check.py` before `f3066c71ac32dfe85f764b46b7fcbd315a9ae545b80bad4ef919d5cfc9fc7319`, after `878e3413d18b94e6e0306e67ba7eb6d7a1ba0a45551e1fc4989e034e1ced8a48`). It applies to the pin with one cosmetic help-text hunk dropped, and `tests/test_memory_rule.py` passes against the patched pin (22 of 22). It changes only the monitor; the daemon stays at `d2e66bb`.
1. Option A step 2 first (preserve).
2. Make a **new** directory `/opt/dflv2-src/<new name>/` as a copy of the pinned tree, replace its `tools/rehearsal_check.py` with the patched file and check the sha256 above. Never edit `/opt/dflv2-src/d2e66bb…` in place.
3. Read-only check on the live cgroup with the patched code (prints the pinned fraction; writes nothing). Expect a few percent.
4. Point `dfl-rehearsal-monitor.service` at the new directory (a drop-in, then `daemon-reload` and a monitor restart). The restart must finish well inside the guard's 120 s heartbeat limit; the latch is already in place, so the daemon stays paused throughout.
5. `rehearsal_check.py release --guard /srv/dflv2-rehearsal/monitor --reason "<text>"` (moves `TRIPPED` into `evidence/released/`; nothing is deleted; refused while the heartbeat is stale), then `broadcast_run.py resume --root /srv/dflv2-rehearsal/private --guard /srv/dflv2-rehearsal/monitor`. The clock continues from 55,743 s; nothing is skipped or replayed.
6. Watch the first hour's heartbeat lines: `mem.pinned` should stay in the tens of MB while `mem.current` climbs. Roll back by pausing and restoring the unit's original `ExecStart`.
Cost and honesty: 32.5 h; the run's record must say "tripped once on a false `memory_high`, resumed after a monitor-only patch, daemon unchanged", never "passed"; the owner is the one who decides whether that has value.

**Option C (not recommended): resume with the unit as it is.** The old rule would trip again when the cache refills, probably within hours. **Option D (not recommended): remove `--cgroup` from the monitor unit.** It silences the check and loses the early warning; MemoryMax would still hold, but it is a change made to make a number go away.

Whichever option: do not reboot the Droplet ("System restart required" is pending) while the rehearsal is wanted.

## 8. Production, read-only (Nov 8, 07:36 to 07:40 UTC)

Event 188 confirmed: `feed/status.json` `league_events` 188, `published_events` 188, release `e00188-cf545fbf7b`, runner `stopped`; `feed/latest.json` names the same release; `league.db` unchanged since Nov 3 07:48; the league runner service is inactive since then. The only production timers firing are the 1-minute feed export and the 5-minute site probe (details in the manifest). No production file other than the feed export's own `status.json`, lock and `-shm` changed since the rehearsal started.
This confirms the state, not the cause of the earlier concern: nothing about the rehearsal's memory reached production.

## 9. Uncertainties and limits

* **The memory breakdown at the original trip is unknown** (section 2).
* The real-workload run is on **cgroup v1** (this host has no v2 memory controller and no systemd); the Droplet is **cgroup v2**. The v2 reader is tested against the Droplet's real files, not against a v2 kernel under load. Dirty pages are throttled per group in v2 and globally in v1, so the dirty-flood scenarios are harsher here than on the Droplet.
* **Known soft spot: a signal that hovers at the line is decided by sampling phase.** `anon_plus_dirty` (section 5.1) kept pinned memory between 80 % and 100 % for 150 s without a 60 s unbroken run over the line: no trip at 1 Hz, a trip at 2 of 10 phases of the 10 s cadence. Both outcomes are defensible (the group was never killed), but it means the rule does not promise to catch a sawtooth. The kernel's boundary still holds in that case. A windowed-fraction rule or a pressure trigger (section 5.2) would close it; neither was added, to keep the fix small and because the flood is harsher on this host's cgroup v1 than the Droplet's cgroup v2 will be.
* **The rule cannot beat a fast leak** (section 3): above about 1 MB/s on 600 MB the kernel acts first. That is the intended division of labour, not a gap in the tests.
* The 60 s window, 90 % line and 3-sample minimum are chosen, not derived: the leak-rate argument in section 3 shows what they protect against, and the workloads show they work, but another window would also work.
* The sandbox limit was 256 MiB, the rehearsal's is 600 MiB: the rates scale (section 3), the behaviour is the same kernel mechanism.
* Actual systemd behaviour (`OOMPolicy=stop`, restarts, ordering) is **still unverified** and remains deployment gate 1.
* Only the monitor changed; the boundary, startup, OOM and reveal validations of `9f2a6e9` were not re-run on this branch (section 6 lists what was).
