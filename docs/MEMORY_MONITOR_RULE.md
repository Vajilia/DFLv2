# The monitor's memory rule

Technical description of the daemon-memory check in `tools/rehearsal_check.py` (`read_cgroup_memory`, `memory_estimates`, `MemoryJudge`, `Watch.memory_check`), its tests, and what was measured.
**This is a code change, so it is a new candidate**: nothing here has been deployed, installed or run under systemd. The cgroup-v2 / real-systemd validation is planned in `docs/MEMORY_MONITOR_VALIDATION_PLAN.md` and has **not** been done.

## 1. Why the rule changed

The earlier check compared `memory.current` with 90 % of `memory.max` on **one** sample. `memory.current` includes clean file cache, which the kernel hands back when it needs the room, so a daemon that publishes tens of thousands of files fills its cache toward the limit and stays there, healthy. The same check is in the recurring service's monitor (it also passes `--cgroup`), so both had the defect.

**What is known about the one earlier trip.** The monitor recorded only `memory.current` and `memory.max`. **The breakdown at the moment of the trip (anonymous / cache / dirty / kernel) was not recorded and cannot be recovered.** Counters read afterwards were consistent with cache (no kernel reclaim pressure, no stalls, no OOM events, and a peak equal to the trip value). The trip is therefore classified as **likely cache-related, not proven false**: a short burst of non-reclaimable memory at that instant that was then released is not excluded by the record.
The rule was changed so that the next trip, true or false, carries the evidence that one lacked.

## 2. The rule

`MemoryMax` stays the hard boundary. It is the kernel's: the monitor never writes, raises or lowers it.

The monitor reads the daemon's cgroup files (`memory.current`, `memory.max`, `memory.stat`, `memory.events`, `memory.pressure`) and computes two **estimates**:

* **reclaimable** = *clean, unmapped* file cache (`file − file_dirty − file_writeback − file_mapped − shmem`) + `slab_reclaimable`. Nothing else is discounted: dirty and writeback pages (they must be written first), mapped files, shared memory, anonymous memory (there is no swap), unreclaimable kernel memory and page tables count in full. Where two of these overlap (a mapped shared page is in both `file_mapped` and `shmem`) the overlap is subtracted twice, so the model itself never overstates `reclaimable`. Without `memory.stat` nothing is discounted.
* **pinned** = `memory.current − reclaimable`.

**`pinned` is an estimated non-reclaimable footprint, built from `memory.stat`. It is not an exact kernel measurement** (the kernel does not report what it could reclaim; it reports what it did). It is conservative by design: when in doubt a page counts as non-reclaimable (see the limits in section 6).

The broadcast stops (the existing `memory_high` trip kind, same latch, same evidence folder) when either:

1. **Sustained:** `pinned` is above **90 % of `memory.max`** on every sample for **60 s** and at least **3 samples** (the monitor samples every 10 s, so six). One high sample, or an excursion shorter than the window, trips nothing. It fires once per episode and re-arms when the estimate comes back under the line.
2. **The kernel's own verdict:** `memory.events` `oom` or `oom_kill` rose since the previous sample. At once. The first sample sets the baseline, so counts that existed when the monitor started are not new events.

`high` and `max` events (the kernel reclaiming at the limit, which it does by design) and memory pressure (PSI) are **recorded and are not triggers**. A daemon that is really starved stops renewing its lease, which the monitor already reports as `daemon_silent`.

**What the monitor can and cannot catch.** After `pinned` crosses the line the daemon has 10 % of `MemoryMax` left. If it grows faster than `0.1 × MemoryMax / 60 s` (about 0.3 MB/s for a 192 MB limit, 1.0 MB/s for 600 MB) the kernel reaches the limit before the window ends. Then the protection is the kernel's: it OOM-kills, `OOMPolicy=stop` is meant to stop the unit, the daemon stops renewing its lease and the monitor trips on `daemon_silent` and on the `oom_kill` event. That is the protection that existed before this change; the sustained rule adds early warning, with evidence, for a slow leak the kernel would reach minutes later. `OOMPolicy=stop` under real systemd is **not yet verified** (validation plan, stage V3).

**What is recorded.** Every trip's evidence holds `anon`, `file`, `file_dirty`, `file_writeback`, `file_mapped`, `shmem`, `kernel`, `slab_reclaimable`, `slab_unreclaimable`, `unevictable`, `active_file`, `inactive_file`, the full `memory.events`, the pressure files, both estimates, the rule's parameters and the **last 12 samples**. Every heartbeat line carries a compact `mem` object (`current`, `max`, `anon`, `file`, `dirty`, `writeback`, `mapped`, `shmem`, `kernel`, `slab_reclaimable`, `pinned`, `events`, `psi_some10`, `psi_full10`), about 320 bytes per line, so a run has a memory time series.

## 3. Tests

`tests/test_memory_rule.py` (22 tests; committed first and failing against the previous tool), on a **synthetic** fixture (`tests/fixtures/cgroup_v2_synthetic/`, invented numbers that obey `anon + file + kernel = current`):
the parser reads the files and reconciles; a series that fills the group with cache and slab past the old line never trips, however long it is held; `high`/`max` events do not trip; one sample and a 20 s excursion do not trip but the same value held does; the window is in seconds **and** samples; dropping under the line resets the count; anonymous memory held near the limit trips with the full breakdown; the same file pages are healthy when clean and a trip when dirty/writeback; mapped files, shared memory and unreclaimable slab are not discounted while reclaimable slab is; "only clean unmapped cache is discounted, never all file cache" is an equality on the estimate; an OOM event trips at once and OOM counts that predate the monitor do not; no limit means no rule; pressure is recorded and never trips; the `Watch` writes the breakdown into the heartbeat, trips with an evidence file, and trips on an `oom_kill` event.
`tests/test_rehearsal_guard.py::test_resource_violations_trip` was updated on purpose: with no `memory.stat` nothing is discounted, one high sample no longer trips, and a held one does.

**Mutation check** (`tools/memory_rule_mutants.py`; result in `docs/measurements/memory_monitor/mutation_check.json`): baseline 22 tests, none failing. Each of eight deliberate breakages is caught by at least one test: the earlier rule (6 tests fail), subtracting **all** file cache (3), treating all kernel memory as reclaimable (2), no sustain window (5), a window by time with no minimum sample count (1), a line above the limit, i.e. "just raise the limit" (7), ignoring the kernel's OOM events (3), tripping on `max` events (1).

## 4. Measurements on real kernel cgroups (`tools/memory_workloads.py`; `docs/measurements/memory_monitor/`)

**Test host: cgroup v1, no systemd** (a sandbox without a v2 memory controller). The kernel's accounting, page cache, dirty/writeback handling and OOM killer are real; the file names differ (the reader maps v1 onto the v2 names), `failcnt` stands in for `memory.events max`, the pressure file is the whole host's, and dirty throttling is global instead of per group, so dirty floods are **harsher** here than on v2. The limit was 256 MiB. The harness only observes: nothing stopped a workload when a rule tripped, so "kernel kill" is the kernel's own doing. Percentages are of the limit.
Every sample (1 Hz) was judged by the earlier rule (one reading of `memory.current` above 90 %) and by the new rule, live at 1 Hz with the production window and **replayed from the same trace at the monitor's 10 s cadence at each of the ten possible phases**.

| scenario | peak current | peak pinned (est.) | peak anon | peak dirty+wb | PSI some / full avg10 | failcnt | earlier rule trips | new rule, live 1 Hz | new rule, 10 s cadence | kernel kill |
|---|---|---|---|---|---|---|---|---|---|---|
| `healthy_cache` | 100 % | 10 % | 7 % | 0 % | 0.2 / 0.2 | 83120 | 1 s | no trip | 0/10 phases | - |
| `anon_spike` | 94 % | 94 % | 93 % | 0 % | 0.0 / 0.0 | 0 | 6 s | no trip | 0/10 phases | - |
| `anon_plus_cache` | 100 % | 56 % | 55 % | 0 % | 0.4 / 0.2 | 89069 | 1 s | no trip | 0/10 phases | - |
| `anon_plateau` | 95 % | 94 % | 93 % | 0 % | 0.0 / 0.0 | 0 | 22 s | sustained at 82 s | 10/10 phases (82–91 s) | - |
| `anon_slow_leak` | 100 % | 100 % | 100 % | 0 % | 0.0 / 0.0 | 105 | 47 s | sustained at 107 s | 10/10 phases (107–116 s) | 134 s |
| `anon_fast_growth` | 100 % | 99 % | 99 % | 0 % | 0.0 / 0.0 | 1731 | 15 s | `oom_event` at 17 s | 10/10 phases (17–26 s) | 17 s |
| `dirty_flood` | 100 % | 100 % | 7 % | 90 % | 18.4 / 18.1 | 173186 | 4 s | no trip | 0/10 phases | - |
| `anon_plus_dirty` | 100 % | 100 % | 80 % | 54 % | 34.3 / 34.2 | 222589 | 1 s | no trip | 2/10 phases (72–101 s) | - |

* **`healthy_cache`**: `memory.current` reaches 100 % and the kernel hit the limit 83,120 times (reclaiming, as designed), yet only about 10 % is non-reclaimable, pressure stayed at 0.2 % and nothing was killed. The earlier rule trips within seconds; the new one never does.
* **`anon_spike`** (93 % anonymous for 15 s, then freed) and **`anon_plus_cache`** (55 % anonymous plus a cache stream at 100 %): earlier rule trips; new rule does not, at any phase.
* **`anon_plateau`** (anonymous memory held at 93 %): new rule trips after the window (82 s live, 82–91 s by phase; the line was first crossed at about 22 s); no kill occurred.
* **`anon_slow_leak`** (85 %, then +0.3 MB/s; the kernel killed it at 134 s): new rule tripped at 107 s live (107–116 s by phase), **18–27 s before the kernel's kill**, at all ten phases. (Re-run once after a wrongly written check; the report's `note` says so. Rule and scenario unchanged.)
* **`anon_fast_growth`** (15 MB/s past the limit): the kernel OOM-killed it at 17 s and the new rule tripped on the `oom_kill` event the same second (0–9 s later at the 10 s cadence: sampling lag). The sustained window could not help here and is not what protects: the kernel's boundary is.
* **`dirty_flood`** (a writer that never syncs): dirty plus writeback peaked at 90 %; the estimate touched 100 % in bursts of at most 4 s, pressure `full` up to 18 %; nothing was killed. Dirty pages are counted in the estimate (checked sample by sample); the rule did not trip because no burst lasted the window.
* **`anon_plus_dirty`** (80 % anonymous plus the dirty writer): the estimate sawtoothed between 80 % and 100 % for 150 s, dirty plus writeback up to 54 %, pressure `full` up to 34 %, no kill. **The verdict depends on sampling phase**: the live 1 Hz judge did not trip (longest unbroken run over the line: 8 s) and 2 of the 10 phases at the 10 s cadence did. This is the rule's known soft spot (section 6).

**Pressure** separates the cases by two orders of magnitude (0.0–0.4 % for every healthy or single-cause run, 18 % and 34 % `full avg10` for the two dirty floods). It is recorded and **not** used as a trigger: a stall is a performance problem rather than an integrity problem, the floods here are harsher than per-group v2 throttling, and v1 only offers a host-wide file. If stalls should stop the broadcast, `memory.pressure` `full avg60` above about 10 % on the cgroup's own file is the data-supported place to start, as a separate change with its own tests.

## 5. Scope

Changed: `tools/rehearsal_check.py` only, in the monitor's memory check (the reader, `memory_estimates`, `MemoryJudge`, `Watch.memory_check` replacing the short block in `cheap()`, and the `mem` object in the heartbeat; about 200 lines, mostly the reader and the evidence). New: `tests/test_memory_rule.py`, `tests/fixtures/cgroup_v2_synthetic/`, `tools/memory_workloads.py`, `tools/memory_rule_mutants.py`, `docs/measurements/memory_monitor/`, this document and the validation plan. One test and one doc row were updated.
**Not changed:** the daemon, the guard, the units (`MemoryMax`, `OOMPolicy`, `--every`, `--cgroup` paths), the engine, `src/dfl`, data, the site, league rules. No limit was raised or lowered.
The v1 branch of the reader exists only so the rule can be exercised against real workloads on a host without a v2 memory controller; the target units are cgroup v2.

## 6. Limits and uncertainties

* **The memory breakdown at the original trip is unknown** (section 1).
* The real-workload measurements are on **cgroup v1 without systemd**. The v2 reader is tested against **synthetic** files, not against a v2 kernel under load. The dirty-flood scenarios are harsher here than v2 will be.
* **Known soft spot: a signal that hovers at the line is decided by sampling phase** (`anon_plus_dirty`). Both outcomes are defensible (the group was never killed) but the rule does not promise to catch a sawtooth; the kernel's boundary still holds. A windowed-fraction rule or a pressure trigger would close it; neither was added, to keep the change small.
* **The rule cannot beat a fast leak** (section 2): that is the intended division of labour with the kernel.
* The 60 s window, 90 % line and 3-sample minimum are chosen, not derived; the leak-rate argument shows what they protect against and the workloads show they work, but another window would also work.
* The sandbox limit was 256 MiB, the production limits are larger or smaller: the rates scale, the mechanism is the same.
* `pinned` is an estimate. It is built to err toward overstating non-reclaimable memory (overlaps are subtracted twice), but that is a design intent, not a guarantee: clean, unmapped cache can occasionally be temporarily unreclaimable (locked or in-use pages), which would make the estimate too low. Nothing here measures the error under a real v2 workload; validation stage V1 measures it.
* **Systemd behaviour is unverified** (`OOMPolicy=stop`, restarts, ordering, latch preservation across restarts): see the validation plan.
* Only the monitor changed. The earlier boundary, startup, OOM and reveal validations were not re-run on this branch.
