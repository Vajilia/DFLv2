# Integration candidate, 2026-11-07: M6 supervisor + corrected monitor + continuity, measured together

Branch `backend/integration-m6-monitor-2026-11-07`. **A candidate and a runbook only.** Nothing was installed, enabled, deployed, started on a host, or sent. The Droplet and the running rehearsal were not touched. `src/dfl`, `data`, `site` and the league rules are unchanged (`git diff 284125a -- src data site` is empty); every change is in `tools/`, `tests/`, `deploy/`, `docs/`.

## 1. Pins

| What | Commit |
|---|---|
| Base: the latest continuity and backup work | `284125a` (`backend/public-backup-2026-11-06`) |
| M6 supervisor candidate | `99632c1` (`backend/unattended-m6-2026-11-07`, on `284125a`) |
| Corrected monitor (temporary-file race) | `5118701` (`backend/monitor-tmp-race-2026-11-06`, on `c28ca13`) |
| Merge of the two | `8b7710c` (clean merge, no conflict) |
| Monitor follows seasons and the builder | `716b52d` |
| Per-season public cap | `6d3ae6a` |
| `.partial` leftover kept as evidence | `dc75a1b` |
| Two-season fault run, then permission test | `d3661a6`, `2caa64e` |
| Measurements, limits, unit templates, this document | the commit at the head of the branch (`git rev-parse HEAD`) |

**Not included on purpose:** the monitor branch has two later commits (`d2e66bb`, `2711712`: fresh-rehearsal package and live drill). The request pinned `5118701`, so they are not here. Merging them is the owner's call and has not been tested against this work.

## 2. What was wrong, found by running the three together

Merging was not enough: the merged monitor would have **stopped a correct broadcast** the first time it was run with the supervisor. The rehearsal monitor builds its oracle once, from one season fully built before the run. Two assumptions break in recurring operation:

1. The builder keeps adding events while the clock runs.
2. At the hand-over (`store/carry.json`) the season on air changes, so every season-2 file looks "early or unknown" to a season-1 oracle.

Proof that the test is real: with the old oracle put back (`/tmp` mutation run, `FollowingOracle` replaced by the one-shot oracle) the integration rig trips the guard at virtual second 1122 with `early_or_unknown_file` (`latest.json`, `releases/e00001-…/manifest.json`). With the fix, a correct two-season broadcast has zero violations.

Changes, all test-first (failing test written, seen to fail, then fixed):

| Change | Where | Why |
|---|---|---|
| `FollowingOracle` and `Expected` | `tools/broadcast_season_check.py`, `tools/rehearsal_check.py` | The monitor re-reads `carry.json` and the on-air season's `builds.json` and re-derives what is allowed. Earlier seasons' files stay **expected** (removing one is `missing_file`), and they live in one shared set, not one copy per call (memory, section 6). |
| Per-season public cap | new `tools/publiccap.py`; daemon, monitor, supervisor health | Section 7. |
| Leftover `sNNN.partial` | `tools/broadcast_build.py`, supervisor | Section 5. |
| Unit templates | `deploy/supervisor/` | Section 6; `tests/test_unit_templates.py` parses each `ExecStart` with the real tool. |
| Measurement harness | `tools/measure_combined.py` | Section 6. |

The independent oracle in `tools/broadcast_season_check.py` still shares no code with the daemon's publisher.

## 3. Two synthetic seasons, everything running together (`tests/test_integration_m6.py`)

Virtual time (nothing sleeps). **Real:** the daemon, the daemon's own Guard, the monitor with its oracle and guard, the supervisor, and the real builder command line as a child process (`tests/fake_builder.py` replaces only the cost of one event). Not the real engine; the engine is exercised in section 6.

**Baseline:** two seasons, no fault: no violation, no trip, supervisor stays one season ahead, broadcast ends at the end of season 2.

**Every fault in one run, in order:**

| Injected | Safe hold | Recovery |
|---|---|---|
| Monitor trip on low disk | daemon pauses, position frozen, evidence kept | `release` then `resume`; goes on |
| Builder fails repeatedly | broadcast holds at the first unbuilt item; supervisor health says why | recovers by itself when the builder works |
| Daemon killed mid-broadcast | monitor reports `daemon_silent`, no trip | new daemon process continues from the clock state |
| Low disk, then low inodes (supervisor) | nothing built, nothing changed; broadcast holds at the end of season 1 | resumes when room appears |

Checked at every step, not only at the end: viewers' position never goes back, never moves faster than time, never passes the first unbuilt item; **no public file contains a result before the position at which the plan releases it** (event release markers and the Honors marker); the monitor logs only the violations the injected fault should cause.

At the end: the earlier season's public files and private records are byte-for-byte what they were when it closed; the final public tree equals the baseline's (faults delay, never change what is published); every file that existed at any check still exists, except the daemon's own atomically rewritten status files.

**Permissions as the substantive check** (`Permissions`, run as real uids 61002 builder/supervisor and 61001 daemon): the supervisor and builder prepare and build season 2 with no write permission in the public folder. Before/after comparison of every public file (hash, size, mode, owner, inode, mtime) is identical; finished-season records are identical. Direct probes as the builder's user: create, overwrite, rename, delete and mkdir in the public folder all return `EACCES`. A control as the daemon's user shows the same probes are `ALLOWED` where permissions allow, so the refusals were the permissions and nothing else. Then the daemon hands over and publishes season 2 into the same folder without disturbing season 1. A source scan for deletion calls was not relied on.

## 4. What passed

| Run | Result |
|---|---|
| `test_monitor_multiseason test_public_cap test_rehearsal_check test_rehearsal_guard` | 65 OK |
| `test_integration_m6 test_broadcast_supervisor test_broadcast_build test_multiseason` | 67 OK |
| `test_broadcast_run test_rehearsal_deploy test_rehearsal_package test_rehearsal_quarantine test_rehearsal_traversal test_rehearsal_umask test_season test_supervisor_real` | 64 OK, 1 skipped |
| `test_unit_templates` | 6 OK |

Not run: `test_recovery_set`, `test_backup_resume`, `test_multiseason_engine` and the full suite. They do not import anything this branch changed except through `tools/broadcast_build.py` (`next-season` only); an hour-long run was not worth it for the change size. Run them before any release decision.

## 5. Interruption during `sNNN.partial` creation

`next-season` builds the next season in `store/seasons/sNNN.partial` and renames it into place. A process death in between leaves the `.partial`. Before this branch a restart would either overwrite it or stop on it.

Now (`set_aside`, `tests/test_broadcast_supervisor.py` class `Partial`): the restart **renames the leftover to `sNNN.partial.interrupted-<UTC time>-<pid>`** (one atomic rename in the same folder; nothing is copied, deleted or overwritten; a name clash gets a counter), prints `interrupted_leftover` with the new path, builds a fresh `.partial`, and renames it into place. The supervisor's health lists the evidence under `seasons.interrupted_evidence`, reports `partial_pending` while a `.partial` is being built, and a person cleans up evidence at their own pace. `target()` ignores names that are not `sNNN`, so evidence is never mistaken for a season, and a second interruption does not block the third attempt. Tested at every interruption point of the creation, with two interruptions in a row (two pieces of evidence), and with a leftover that is a plain file; in each the restart succeeds and the evidence is untouched.

## 6. Memory and contention: 1 vCPU, 2 GB, real engine (`tools/measure_combined.py`)

**Setup:** a finished real season-1 root (copied, ~1 GB), monitor + daemon + supervisor as separate processes pinned to one CPU, inside a 2 GB memory cgroup (cgroup v1 here; production is systemd). The supervisor runs the real builder: `next-season`, then one real engine event after another. **This is a sandbox with a different disk and kernel, not the Droplet.**

| Run | Length | Builder (events/min) | Daemon tick gap p99 / max | Peak RSS daemon / monitor / supervisor+builder (MB) | Monitor violations |
|---|---|---|---|---|---|
| baseline (monitor + daemon) | 3 min | none | 1.02 / 1.20 s | 31.9 / 33.7 / – | 0 |
| default (all three, no priorities) | 12 min | 3.17 | 1.02 / 1.48 s | 32.0 / 34.1 / 216.9 | 0 |
| tuned (supervisor at Nice 15, CPU weight ~20 vs 100) | 12 min | 3.17 | 1.01 / 1.19 s | 32.0 / 33.9 / 217.0 | 3 `daemon_silent`, no trip (daemon SIGKILLed at 240 s, restarted 120 s later) |
| limits (192 / 512 / 512 MB hard) | 8 min | 3.62 | 1.01 / 1.24 s | 32.1 / 33.9 / 203.6 | 0 |

Challenge to the plan's assumption: **the CPU priorities did not matter here.** The builder takes ~96% of the one CPU while it works; the daemon uses 0.3% and never had a tick later than 1.5 s *without* any priority. The `Nice=15 / CPUWeight=20` in the supervisor unit are kept as cheap insurance, not because this run needed them. A slower host or a heavier season could change that; it was not tested.

Other findings:

* **Page cache is not a need.** The supervisor's cgroup high-water mark was 1.24 to 1.27 GB when unlimited, but its processes peaked at 204 to 217 MB. The rest is the engine's files in the page cache. Under a hard 512 MB limit the builder was no slower; the kernel trimmed cache (62,532 `failcnt` hits) and never killed anything (`oom_kill 0` in all three groups). Reading `MemoryMax` off the cgroup's high-water mark would have over-sized it six-fold.
* **Daemon memory is flat:** 32 MB, cgroup 26 to 29 MB, +2 MB over ten seasons (`docs/BROADCAST_CONTINUITY.md`).
* **Monitor memory grows with seasons** because it keeps every public file name (about 59,290 a season, 42 characters on average). Measured on the shared-set structure (`taskset -c 1`, real file list repeated per season; includes the monitor holding the disk listing as well as the oracle):

| Seasons on air | Oracle state | Process RSS | Peak RSS | Full-pass compare |
|---|---|---|---|---|
| 1 | 8.5 MB | 58 MB | 55 MB | 0.01 s |
| 5 | 36 MB | 106 MB | 109 MB | 0.15 s |
| 10 | 81 MB | 168 MB | 192 MB | 0.31 s |
| 20 | 147 MB | 293 MB | 343 MB | 0.67 s |
| 30 | 238 MB | 384 MB | 430 MB | 1.04 s |

That is about 12 to 14 MB a season. It is **linear and bounded by the disk** (at ~0.95 GB a season the public folder reaches any plausible budget long before 30 seasons), so a fixed limit is acceptable. Before the shared-set change (`Expected`) each oracle call copied the earlier seasons' sets; the change is why memory is flat per call.

### Limits derived from the evidence

| Unit | `MemoryMax` | Basis |
|---|---|---|
| daemon | **192M** | 6x the 32 MB peak; flat over ten seasons. The monitor trips the guard at 90% of the daemon's cgroup value, so a leak pauses the broadcast well before a kill. (The rehearsal's 600M was a guess.) |
| monitor | **512M** | ~30 seasons at the measured growth (430 MB peak). |
| supervisor (+ builder step) | **768M** | 3.5x the 217 MB process peak; 512M was **validated** (no kill, no slowdown), 768M adds 50% for an engine step on a real season that has not run under the unit on the host. |

Together 1472M, under a 2 GB machine with room for the system and Caddy. `MemoryMax=1500M` is **not** adopted. The induced out-of-memory kills of each unit, and what the others then do, are in section 11.

## 7. The fixed 3 GB public cap

**What the conflict was.** The rehearsal runs one season and stops the broadcast above 3 GB (daemon `--max-public-gb 3`, monitor `--max-public-gb 3`). The public folder never shrinks (every release, replay and recording stays: `docs/STORAGE_RETENTION.md` rule R1) and grows 0.78 to 0.95 GB a season (measured). A fixed 3 GB stops a **correct** broadcast around the end of season 3 or in season 4 with tens of GB free. That is a timer, not a safeguard. Confirmed by test: a cap sized for one season stops a correct second season (daemon exit 70, monitor trip); the per-season cap runs on.

**What replaces it** (`tools/publiccap.py`, one function shared by the daemon, monitor and supervisor):

```
cap = base + per_season x (seasons on air, read from store/carry.json)    never above the ceiling
```

It moves only at a hand-over, so a season the builder did not finish never raises it. Defaults in `deploy/supervisor/broadcast.env.template`: 1.25 GB a season (30% above the largest measured), 0.25 GB base, in **one environment file** read by all three units so they cannot disagree (`test_unit_templates` checks it).

**What it still catches** (tested): a runaway season (a duplicated tree, a leaking copy) is stopped in that season (daemon exit 70, monitor trip) at a size proportional to the seasons honestly published; the supervisor warns at 85% (`public_cap_near`) and goes critical at 100% (`public_cap_exceeded`) before either process has to stop anything. Free-disk floors (monitor 6 GB, supervisor 8 GB, so building stops first) apply independently.

**What it does not catch, plainly.** The cap is cumulative: allowance a season does not use carries forward and can hide a later overrun. With 30% headroom that is at most ~0.3 GB a season, so after ten seasons an overrun of about 3 GB is still under the cap. A per-season check would need the daemon to record the folder's size at each hand-over, which adds a second writer to the store; not built. The bounds on that slack are the ceiling, the floors and the 85% warning.

**The ceiling is the owner's number and has no default.** `PUBLIC_CEILING_GB=__CEILING_GB__` in the template; the units will not start with that text, by design. It depends on the disk and the retention decision (M8), so I did not guess one. Without a ceiling the effective safeguard against slow growth is the free-disk floors alone.

Flag rules: a fixed and a per-season cap together are refused; a ceiling without a per-season cap is refused. The rehearsal's units are untouched and still use the fixed cap, correctly, for one season.

## 8. Runbook (recurring operation; none of it has been run on a host)

Templates: `deploy/supervisor/dfl-broadcast-daemon.service`, `dfl-broadcast-monitor.service`, `dfl-supervisor.service`, `dfl-supervisor-check.service/.timer`, `broadcast.env.template`. Placeholders `__COMMIT__ __ROOT__ __PUBLIC__ __GUARD__ __HOST__ __CEILING_GB__`; **no installer renders them.**

**Before first start (owner):** set `PUBLIC_CEILING_GB`; decide the alert channel (`docs/UNATTENDED_OPERATION.md` section 7); the root exists and was `init --continuous`; the daemon user owns the public folder and the guard; the builder user owns the root; the public folder is read-only to the supervisor unit.

**Start order (reproduced with the units' own ExecStart lines, section 15):** daemon, then monitor, then supervisor; starting the daemon and the monitor in the same instant, which is what systemd does at boot, is equally clean. Do **not** start the monitor alone first: it reports `daemon_silent` (true, no daemon yet) until the daemon has renewed its lease. **This is the recurring service's order, not the installed rehearsal's.** The rehearsal (`deploy/rehearsal/`, `docs/BROADCAST_REHEARSAL.md` section 8) starts the **monitor first, then the daemon**: its state is fresh (position 0, no lapsed lease, empty guard folder), and it runs the commit pinned at install, which predates the guard fix below. The two orders are not interchangeable (section 15, "Two start orders"); nothing about the installed rehearsal was changed. **Stop order:** supervisor, monitor, daemon. Stopping the supervisor does not stop the broadcast; stopping the daemon pauses viewers' position (it resumes from its clock state).

**Reading it:** `broadcast_supervisor.py check` exit codes 0 ok, 1 warning, 2 critical, 3 unknown/stale. Daemon exit 70 = public cap exceeded, 75 = another daemon holds the lock: neither restarts (`RestartPreventExitStatus`). Monitor exit 1 = violations logged; 2 = it cannot write its latch.

| Situation | What it means | What to do |
|---|---|---|
| Guard `TRIPPED` | The monitor paused the daemon; evidence is under `GUARD/evidence` | Read the evidence. Only then `rehearsal_check.py release …` and `broadcast_run.py resume --root … --guard …`. The daemon does not resume by itself after a trip, on purpose. |
| `public_cap_near` | 85% of this season's cap | Look at the folder; decide ceiling, per-season number or retention. Nothing is deleted for you. |
| `public_cap_exceeded` / daemon exit 70 | Over the cap; the daemon stopped, nothing deleted | Find out why (runaway or real growth). Raise the number in `broadcast.env` only after that, restart the three units. |
| `interrupted_evidence` in health | A previous `next-season` was interrupted; its `.partial` was set aside | Keep it until you've looked; remove by hand when done. It never blocks the next attempt. |
| Builder failing | Broadcast holds at the first unbuilt item; supervisor retries with backoff, escalates | Read `supervisor/health.json` and the build log. Viewers see a hold, not a skip. |
| Low disk or inodes | Supervisor stops building first (8 GB); monitor trips at 6 GB | Free space (retention is M8, approval-gated), then release and resume as above. |

## 9. A real-engine season boundary (`tools/boundary_real.py`)

**Two runs, kept apart.** The **supported-controls run** (the default) prepares its fixture before anything starts and from then on only starts, waits and stops the processes: after they start, nothing edits `clock.json` or `ready_until`, and a check proves it from the clock's own log (below). The **accelerated run** (`--accelerated-tail-s 12000`) repeats it and then, after stopping the processes, rewrites `clock.json` and drives the daemon code in virtual time to reach season 2's first results, which real time cannot do in a reasonable run (they are 2 h 29 min in). Everything labelled `[accelerated]` comes from that second kind and is not a supported control. A season is 48 hours of show clock (172,800 s), so the first 47 h 54 min cannot run in real time. **Stage 1** drives the real daemon code in-process over virtual time (as `tests/test_backup_resume_real.py` and `tools/engine_comparison.py` do) on a copy of the finished real season-1 root (47 events, 400 games) up to 6 minutes before its end: 59,231 public files, 94 to 99 s. That stage is **fixture preparation** (it also writes the production key `ready_until` through the clock's own API, because 20 s virtual ticks cannot keep its 3 s horizon): it stands in for two days of broadcasting, runs before any process starts, and the monitor does not watch it. Everything after it is real time and separate processes started with the **exact `ExecStart` argument lists of the unit files** (section 15; the ceiling is a test number), each in a memory group at its proposed `MemoryMax` (192M, 512M, 768M), in the documented order (daemon, monitor 3 s later, supervisor later):

| Stage | What ran | Result |
|---|---|---|
| 2: the hold | Daemon and monitor reach the end of season 1 with **no season 2 prepared**, 90 s of standing | Position stayed at 172,800.0 s, no file appeared in the public folder, daemon alive, monitor not tripped |
| 3: the hand-over | Supervisor started: `next-season` and the **real engine** build events one after another | The daemon handed over **2 s** after the supervisor started (the plan alone names the season; events follow at ~3.6 a minute: 20 built in 5.5 min); `carry.json` = season 2 on air, season 1 finished; 300 s of season 2 broadcast |
| 5: first results, **accelerated run only** | After stopping the three processes, `clock.json` was rewritten (`ready_until` dropped) and the real daemon code ran 12,000 s more of virtual time into season 2 | Position reached 185,112 s, never went back, never faster than the 20 s tick (this virtual clock is **not** gated by publication coverage, so stage 5 shows nothing about holds; holds are shown in stage 2 and in the fault runs); the playback index grew 400 to 424 games, the replay library 400 to 425, Honors stayed at 4 |
| 4: from the files | Compared with a snapshot taken at the hold | See below |

Checks: **21 passed of 21 in the supported-controls run and 25 of 25 in the accelerated run** (the four extra are the stage-5 checks), on the same fixture. Both runs: every season-1 public file has its bytes and keeps owner, mode, inode, size and modification time (59,290 files; only `clock.json`, `schedule.json`, `latest.json`, `status.json` and the two indexes that grew were replaced, which is their design); each index keeps every entry it had at the hold as an exact prefix; the public folder and the private store pass the backup tool's reference closure (no dangling index entry, every release matches its manifest); the monitor logged **no violation** and never tripped over the real-time run (26 and 27 heartbeats over about 13 minutes); **the clock's own log for the real-time run holds two entries, `downtime` (the daemon recording the outage when it started) and `coverage_wait` (its hold at the end of season 1), both written by the daemon's code; nothing else touched the clock**; the viewers' position (778 and 780 samples, one a second) never went back, never exceeded 1.0001 s per second, and never passed the end of season 1 before the hand-over; the monitor's independent oracle, run once more over the final files, finds no early result. Peak memory: daemon 37 to 39 MB, monitor 73 MB, supervisor and builder 144 to 199 MB; the groups peaked at 27 to 32, 65 and 169 to 538 MB (page cache included) against limits of 192, 512 and 768 with **zero** limit hits and zero kernel kills.

What this does and does not establish:

* **Cumulative indexes grew only in the accelerated run's stage 5**, in virtual time, *in this boundary run*; section 17 observes the growth in real time with supported controls. In the 5.5 real minutes after the hand-over no result is due (season 2's first games are released 8,940 s in), so the real-time stages prove the hold, the hand-over and the absence of early results; the indexes' growth and prefix property are shown by the virtual tail on the real files, and only for the 20 events that were built (it needs about 14 built to reach the first results; a 70-second smoke run with 2 built events correctly showed no growth and failed that check). Two hours 29 minutes of real-time broadcasting into season 2 was not run.
* **Run history, in the open:** run 1 failed one check: the monitor reported `daemon_silent` in its first pass, because the harness started the monitor 3 s *before* the daemon and the clock had been left with a lapsed lease by stage 1. The report was correct (the daemon was not running yet) and that harness order was not the documented one. Changing it to daemon-first passed, but only because the guard folder was empty: section 15 shows that the daemon-first start had its own defect after an outage, which is now fixed, and these two final runs use the units' own arguments. Earlier reports are in `/tmp/season/` (scratch, not committed).
* The replay library has one more entry than the playback index at the instant the accelerated run stopped (425 against 424). That is now reconciled game by game: section 16.
* The clock was left in `running` with a lapsed lease at the end of stage 1, which is the state a host leaves after an outage (and the reason the daemon records a `downtime` entry when it starts). Stage 1's writes are fixture preparation; the only mid-test rewrite of `clock.json` is the accelerated run's stage 5, which the supported-controls run does not do.

## 10. Recovery-set and backup-resume suites

| Suite | Result |
|---|---|
| `tests.test_recovery_set tests.test_backup_resume` | **24 OK**, 514 s (real engine, 13 events; includes the builder-exclusion, partial-quarantine and hand-over-during-set tests) |
| `tests.test_backup_resume_real` (opt-in, real engine, a set taken across a real hand-over, originals deleted, restored, both copies resumed in lock step) | **1 test OK**, 621 s (real builder: season 2's first 13 events; real daemon over season 1 in virtual time to a minute before the hand-over; a set taken while the daemon crosses the end of the season inside the non-quiescent public copy; originals deleted; restored; two more real events on each copy with equal league digests; trees equal at every checkpoint; nothing early; earlier files byte-identical) |

The earlier run of this module in this session was *skipped* (no `DFL_SEASON1_ROOT`); a skip is not a pass, so it was run with the finished season-1 root.

## 11. Memory limits, with induced OOM kills (`tools/oom_check.py`)

**What is verified and what is not.** Verified here: the kernel's memory-cgroup OOM killer killing a unit's process in a group at the proposed limit, and what the three programs do about it. **Not verified: any systemd behaviour** (`OOMPolicy=`, `Restart=`, `RestartSec=`, `StartLimitBurst`, `After=` ordering): there is no systemd on the test host, and the harness only imitates the restart. **Two kinds of kill, kept apart.** *`cgroup_oom_kill`*: the kernel's memory-cgroup OOM killer, in a memory group at the unit's proposed `MemoryMax`, evidence being the group's `oom_kill` counter rising and the victim's wait status (signal 9). *`sigkill`*: `kill -9` from outside, which shows a process can vanish and says nothing about the limit. Both were run for the daemon, side by side.

**How the OOM kill is induced, and what is simulated.** No unit process leaks. A second process in the *same* group allocates until the group is over its limit and the kernel then picks and kills a victim by itself. This sandbox does not let a process lower its `oom_score_adj`, so the allocator cannot be made unkillable (my first design killed the allocator instead of the daemon: found by the first run, which failed); the unit process meant to be the victim has its `oom_score_adj` **raised to 1000** (what `OOMScoreAdjust=1000` does). The kernel does the killing; the *choice* of victim is steered. The host has cgroup v1 and no systemd, so **systemd's reaction is emulated and labelled so in the reports**: after the kill the harness waits the unit's `RestartSec` (daemon 5 s, monitor 10 s, supervisor 30 s), terminates whatever else is in the unit's group (`OOMPolicy=stop`, `KillMode=control-group`) and starts the command again. `StartLimitBurst` is not exercised. On systemd, the default `OOMPolicy` is inherited from `DefaultOOMPolicy=` (and is `continue` for units with `Delegate=`) ([systemd.service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html)), so the three templates now say `OOMPolicy=stop` explicitly (a test checks it).

Each scenario starts from the real finished season 1 positioned 20 minutes before its end (live broadcast, about 59,000 public files), with the three units running, warms up 75 s, snapshots the public folder, kills, observes, restarts, and then checks: every earlier public file keeps its bytes and the indexes their prefix, evidence files are unchanged, public folder and store pass the closure, the monitor reported only the violations the fault should cause, the viewers' position never went back or ran fast, and the monitor's oracle finds no early result.

| Scenario | Kind | What happened | Result |
|---|---|---|---|
| daemon, 192M | `cgroup_oom_kill` | Kernel killed the daemon 1.2 s after the over-fill (`oom_kill` 0 to 2: the daemon, then the allocator). Position advanced only 3.6 s after the last sample before the kill (the 3 s publication horizon) and then stayed stopped; monitor: `daemon_silent` only, **no trip**; restarted, position resumed from where the viewers' clocks stopped (+60 s in 60 s); downtime recorded in `clock_log.jsonl` | PASS |
| daemon | `sigkill` | The same, kill by signal from outside | PASS, identical outcome: the recovery path does not depend on how the daemon dies |
| monitor, 512M, **restarted in 10 s** | `cgroup_oom_kill` | Second `start` record, heartbeats resumed; **the daemon never held** (no TRIPPED, clock `running` at every sample) | PASS |
| monitor, 512M, **stays dead** | `cgroup_oom_kill` | With no monitor the daemon **stopped the broadcast itself after 120 s** (`monitor_silent` TRIPPED, clock paused, process alive, position frozen). The restarted monitor found nothing and did **not** resume it; `release` kept the trip as evidence (`released/…-TRIPPED.json`, 277 bytes) and `resume` continued from the held position (+44 s) | PASS |
| builder step, 768M | `cgroup_oom_kill` | Kernel killed the real engine step and its runner. The supervisor recorded `failed: league runner failed (-9)`, backed off, and 98 s later the next step succeeded and health returned to `ok`; no orphaned engine process; daemon and monitor undisturbed (no violation) | PASS (second run, see below) |
| supervisor process, 768M | `cgroup_oom_kill` | Kernel killed the supervisor (`oom_kill` +2 in all); the step and the engine were gone as well; daemon and monitor untouched (separate groups); restarted after 30 s, took the builder lock back, events built again | PASS |

Normal operation at the limits (the real-time boundary run, section 9): the three groups peaked at 32, 50 and 538 MB and never reached their limits.

**What the tests found.**

1. The first builder-OOM check passed for the wrong reason: it looked for "failed or warning" in the health after 35 s and an unrelated `capacity_low` warning satisfied it. I replaced it with a poller (twice a second) that records every distinct builder state, and the check now needs `failed` with `backing_off` and later `ok`. The scenario was rerun; the evidence above is from that run. (The first, weak run also happened when the free disk was 10.2 GiB: the supervisor correctly did not start a season under its 10.5 GiB rule. Disk scratch from earlier work was cleaned up.)
2. A daemon death costs viewers **no jump and no spoiler**: the clock stands within seconds and resumes where it stopped. A monitor death is **not** silent: the daemon holds the broadcast after 120 s, by design, and a person must release and resume. If the monitor's unit restarts within 120 s (its `RestartSec` is 10 s) nobody notices.
3. The monitor's own pre-trip (daemon memory over 90% of its cgroup limit) could not be exercised against a real cgroup: it reads cgroup v2 files (`memory.current`, `memory.max`), this host has v1. It is covered only by the test with fake files (`tests/test_rehearsal_guard.py`). That is a gap for the first host run.
4. The kernel killed 1 or 2 processes per event (the unit's process plus the allocator, which kept growing until I stopped it); a real leak would kill the unit's process, and `OOMPolicy=stop` takes the rest of the unit with it.

## 12. The monitor-branch commits omitted after `5118701`

`backend/monitor-tmp-race-2026-11-06` has two commits after `5118701`: **`d2e66bb`** "Fresh rehearsal preparation: fresh_run.sh, live monitor drill, plan doc" and **`2711712`** "Record the fresh-rehearsal package and its scratch rehearsal".

| Commit | Files | Does it change code that runs in this candidate? |
|---|---|---|
| `d2e66bb` | **new:** `deploy/rehearsal/fresh_run.sh`, `tools/monitor_live_drill.py`, `tests/test_fresh_run.py`, `docs/FRESH_REHEARSAL_2026-11-06.md`; **edited:** two documentation files and the two regenerated patch files under `docs/patches/` | **No.** `git diff 5118701 d2e66bb -- src tools site data deploy` adds only the two new files above; `tools/rehearsal_check.py` is byte-identical to `5118701`'s. The regenerated patch files now include the `.tmp-<name>-<pid>` rule for `status.json` (the three lines at `tools/rehearsal_check.py:408-410`) that **`5118701` already makes in the code** (and 31 added lines in the test patch, an annotation that a stale temporary file also trips `early_or_unknown_file`): they bring the saved patches in line with code that is already merged here, they add nothing new |
| `2711712` | one new document recording the fresh-rehearsal package | **No.** Documentation only |

**Conclusion: neither contains a fix this candidate needs.** The temporary-file race fix is `5118701` itself, which is what was merged. What `d2e66bb` adds is *rehearsal staging* (a restart from position zero without the installer) and a live drill of the monitor next to a real daemon; both belong with the rehearsal package, not with unattended operation. One piece is useful here: the drill is an independent live check of the monitor, so it was run against this candidate's monitor (the file taken from the branch, run from the working tree, not committed): **17 of 17 expectations held** (clean phase with 90 heartbeats and no violation; a deliberate stale temporary file reported as `stale_tmp`; the latch, release with a reason, resume and position preserved; an unauthorized future file tripped the monitor and paused the daemon; no `monitor_error` in the whole run). The branch's own record says 18: the eighteenth is the optional `--expect-sha256` comparison, which I did not supply because this is not that file. It ran from a `git archive` of this branch's HEAD, so it exercised `FollowingOracle` and the other monitor changes.

## 13. Capacity calculation (the public ceiling stays unset)

**No ceiling is chosen here.** `PUBLIC_CEILING_GB` remains `__CEILING_GB__` in `deploy/supervisor/broadcast.env.template`, so the units will not start until the owner sets it. `tools/capacity_calc.py` is pure arithmetic (no host access, writes nothing, deletes nothing, 10 tests) that turns *measured* inputs into a budget; its defaults are the 2026-11-06 scratch-host figures (`docs/STORAGE_RETENTION.md`, `docs/PUBLIC_BACKUP.md`) and are to be replaced by host measurements.

```
need(N seasons) = public(N) + private(N) + backup workspace(N) + retained evidence + free-disk reserve + OTHER   <= disk
public(N)  = 0.25 + N x 0.955 GB        permanent: nothing removed (R1); 0.78 GB by bytes, 0.955 GB on disk
private(N) = 0.011 + N x 1.073 GB       league file does not grow; archive 145 + feed 50 + replay export 244 + live recordings 633 MB a season (the last is R2's candidate, undecided)
backup(N)  = sets kept on this volume x N x set + one restore/verify destination (N x set, if the restore test runs here) + 0.27 KB per file of working index
set        = public + private per season ~ 2.03 GB (a measured recovery set was 1.95 GB per season on disk, 3.9 GB for two seasons)
evidence   = allowance 0.1 GB + quarantined backup .partial folders (each up to one set)
             measured: one monitor trip leaves 277 bytes; plan + builds of a full season are 34 KB + 196 KB
reserve    = max(monitor floor 6 GB, supervisor to start a season: (floor 8 + step margin 0.5 + season 2) GiB = 11.27 GB decimal)
OTHER      = everything on the volume that is not the broadcast: system, code, caches, rollback copies, tarballs: MEASURE, never assume
```

The reserve is the supervisor's own rule (`capacity_need('next-season')`), and a test ties the calculator to that code. **Units differ and the tools do not say so:** the supervisor's floors are GiB (2^30), the monitor's floor and the public cap are decimal GB. 8 GiB is 8.59 GB, so "keep the builder's floor above the monitor's" holds, but a person adding the numbers by hand would be off by 7%.

Illustration only (disk 47 GB, OTHER 10 GB: the 47 GB is the last figure the docs record for the Droplet, the 10 GB is an assumption; neither is a measurement):

| Backup policy on this volume | 1 season | 2 | 3 | 4 | 6 | 12 | Seasons that fit |
|---|---|---|---|---|---|---|---|
| one set kept here, restore test here, one quarantined partial | 29.8 | 37.9 | 46.1 | 54.2 | 70.5 | 119.4 | **3** |
| no set kept here, restore test here, one quarantined partial | 27.8 | 33.9 | 40.0 | 46.1 | 58.3 | 95.0 | **4** |
| backups and restore tests off this volume | 23.7 | 25.8 | 27.8 | 29.9 | 34.0 | 46.4 | **12** |

(GB needed at N seasons.) What this says, plainly:

* **The public ceiling is not what limits the broadcast.** At 3 to 6 seasons the public folder is 3.1 to 6.0 GB. What decides how many seasons the disk holds is *where backups live*: kept on the same volume, a backup costs twice the data it protects (the set plus the restore test's destination), and a quarantined partial costs one more set. In the illustration the same disk holds 3, 4 or 12 seasons depending on that policy alone.
* The sensible reading for the ceiling: **ceiling = public(N) at the owner's chosen horizon, plus a margin**; with the defaults 3.1 GB for 3 seasons, 4.1 for 4, 6.0 for 6, against a per-season cap that grows 1.25 GB a season (4.0, 5.3, 7.8): the ceiling should sit at or just under the cap's value at the horizon, so it bites when the horizon is reached and not earlier.
* **Retention (M8) changes the private column most:** R2 would reclaim the 633 MB a season of live recordings (about 59% of the private growth) but is undecided and needs a verified backup first.

What to measure on the host, all read-only, before choosing: `df -B1 /` and `df -i /` (disk, inodes); `du -sx` of everything outside the broadcast (OTHER); `tools/storage_inventory.py --root R --public P --json` for the real per-season sizes; the size of one finished recovery set and of its restore destination; the backup location decided for this volume (the single largest lever); and the evidence folders' sizes after a month. Then `python3 tools/capacity_calc.py --disk-gb D --other-used-gb O --set '{...}'`.

## 14. Remaining gaps

1. **Not run on the host.** Everything here ran in a sandbox; kernel, disk and cgroup version (v1 here, v2 under systemd) differ. **Actual systemd recovery is unverified**: `Restart=`, `RestartSec=`, `StartLimitBurst`, `OOMPolicy=` and the `After=` start ordering are emulated or untested (the start-order reproduction in section 15 starts the processes directly). `MemoryMax` was exercised as a cgroup v1 limit; kernel OOM kills were tested, systemd's reaction to them was not.
2. **The real-time part of the boundary is 6 minutes before and 5.5 minutes after the hand-over** (and 5 minutes around the first results reveal, section 17); the 48 hours before and the first 2 h 29 min of season 2 are virtual time on the real files, and season 2's first results appear in the boundary run only in its accelerated tail; section 17 crosses them in real time with supported controls after a pre-start fixture jump (the 48 hours and the first 2 h 26 min of season 2 before it are still virtual). A full real-time season boundary is the rehearsal M5 and needs the rehearsal host free.
3. **The monitor's 90% daemon-memory pre-trip is untested against a real cgroup** (v2 files; only a fake-file test).
4. **Public ceiling unset**, the cap's cumulative-slack limitation stands (section 7), the backup location is undecided and decides the capacity (section 13).
5. **Monitor memory** at many seasons is a model on the real file list, not 20 real seasons.
6. **The tuned CPU settings are unproven as necessary** (section 6).
7. **Alerts, installer:** none. **Retention (M8)** is separate and there is still no automatic space recovery, by design.
8. **The branch is not merged** anywhere; the two later monitor-branch commits are excluded on purpose (section 12). `test_multiseason_engine` and the full suite were not run.

## 15. Start-up order, reproduced with the units' own ExecStart lines (`tools/startup_check.py`)

**Question asked:** is the earlier `daemon_silent` in the boundary harness a harness fault or a fault of the supported start-up procedure? **Answer: the harness fault was real but it was not the whole story.** The first run's `daemon_silent` was a correct report in a harness order (monitor first, after a lapsed lease), as section 9 said. But reproducing it with the units' own arguments found a **real defect in the supported daemon-first start**, which the earlier harness could not see because its guard folder was always empty.

**Method.** The tool starts the processes with the exact `ExecStart` argument lists read from `deploy/supervisor/*.service` and the numbers in `broadcast.env.template` (placeholders replaced; the pinned checkout is this checkout; `--url` points at a small local server that serves the public folder under `/feed` with the front end's cache headers; the ceiling is a test number; the monitor's `--cgroup` path does not exist here and is skipped by the monitor, as designed). The fixture is prepared before anything starts: a copy of the finished real season 1, a clock driven in virtual time to 1,500 s and left stopped for 400 s (a lapsed lease, as after a host outage), and, where stated, a monitor heartbeat file from an hour earlier (what the previous run leaves in the guard folder). No `After=`, restart policy or memory limit is applied: this tests the **programs'** start-up behaviour, not systemd's.

| Order | Before the fix | After the fix |
|---|---|---|
| daemon, monitor 5 s later, empty guard folder (first install) | clean | clean |
| daemon, monitor 10 s later, **old heartbeat file** (after an outage) | **daemon tripped itself at once**: `monitor_silent`, "no heartbeat from the monitor for 3600 s"; broadcast paused, position frozen | clean; position +34 s in 45 s |
| daemon and monitor started in the same instant, old heartbeat file (systemd at boot) | **tripped the same way** | clean on 5 of 5 runs |
| monitor first, daemon 10 s later, old heartbeat file | no trip; `daemon_silent` logged (once at 0.1 s; after the fix twice, the second 0.1 s after the daemon started, before it had renewed) | the same: not a trip, and not the documented order |

**The defect and the fix.** The daemon trips itself if the monitor's heartbeat file is older than 120 s. The 240 s first-start grace applied only when the file did not exist. After any outage the file *does* exist and is hours old, so the daemon, started first (or together with the monitor), tripped before the monitor could write a new one, and the broadcast stayed held until a person ran `release` and `resume`. My earlier statement that the daemon "waits for the monitor's first heartbeat" was true only on a first install. The fix (`tools/broadcast_run.py`, `Guard.step`): a heartbeat last written **before this daemon started** counts as "no heartbeat yet", with the same grace measured from the daemon's own start. A heartbeat written after the start still goes stale in 120 s, as before. Regression tests, written first and seen to fail: `test_a_heartbeat_left_by_an_earlier_run_does_not_trip_a_daemon_that_has_just_started`, `test_the_first_start_grace_is_measured_from_the_daemons_own_start` (the grace is exact: held at 239 s, trips at 241 s), and `test_a_heartbeat_written_after_the_start_still_goes_stale`. One existing test encoded the old rule (a 500 s old heartbeat trips even with a 600 s grace); I changed it to "the monitor does not come back within the grace, so the daemon trips".

**What the fix gives up, to challenge it.** A daemon that is restarted while the monitor is dead no longer trips at once; it trips after at most 240 s. That is the same exposure as a first install, and a daemon that keeps dying is stopped by `StartLimitBurst=3` (three starts in an hour), but a daemon that restarted every few minutes beside a dead monitor would never reach its grace, so the guard would not trip. The stricter alternative is to keep the old rule and make the monitor start first with the daemon waiting for a fresh heartbeat (a systemd `ExecStartPost` wait in the monitor unit, not written). I chose the code fix because it does not depend on systemd ordering that I cannot test here; if you prefer the strict rule, say so.

**Two start orders, and why they differ.**

| | Installed rehearsal (`deploy/rehearsal/`) | Recurring service (`deploy/supervisor/`) |
|---|---|---|
| Documented order | **monitor, then daemon** (`systemctl start dfl-rehearsal-monitor dfl-rehearsal`) | **daemon, then monitor, then supervisor**; daemon and monitor together are equally fine |
| State at start | first start: position 0, no lapsed lease, empty guard folder; ends at a `--deadline` | any start, including after an outage: a lapsed lease and an old heartbeat file |
| Guard rule in the code it runs | the commit pinned at install: older than the fix, so an old heartbeat file trips the daemon at once (assumed from the repository history; the host was not inspected) | this checkout: a heartbeat older than the daemon's start gets the 240 s grace |
| What goes wrong in the other's order | daemon first, with an old heartbeat file: instant `monitor_silent` trip | monitor first: `daemon_silent` logged until the daemon renews its lease; never a trip |

The rehearsal's order is right for the code it runs, and the recurring order is right for the code in this checkout. Neither document was ever wrong about its own service, but my earlier text in this file said "production starts the daemon first" as if there were one production order; there are two services, and the first sentence of section 8 now says which is which.

**Validation batch, same day (after `7ccc490`).**

*The grace never overrides a latch.* In `Guard.step` the latch check is first and the heartbeat/grace logic runs only when no latch exists; `tests/test_rehearsal_guard.py` now proves it for every combination of heartbeat (older than the start, absent, fresh) and time (1 s, 100 s, 239 s, 500 s after the start): with a latch from an earlier run the broadcast is held, `TRIPPED` is neither replaced nor cleared, and no second trip is recorded; a monitor that starts after a latch and writes a fresh heartbeat does not clear it and `resume --guard` is refused; and a real daemon run over a latch and a one-hour-old heartbeat holds at once. A deliberately broken guard (grace checked before the latch) fails the first of these, so the test can fail.

*Daemon restart with a dead monitor, with real processes* (`tools/startup_check.py`, scenarios `daemon-restart-monitor-back-within-grace` and `daemon-restart-dead-monitor`; the heartbeat left behind is 10 minutes old and the lease lapsed 10 s before the daemon started; sampled once a second from the first second; 18 checks, all passed; evidence in `docs/evidence/dead_monitor_restart_2026-11-07.json`):

| Case | Result |
|---|---|
| Monitor back 100 s after the daemon (inside the grace) | No trip over 285 s, which is past the 240 s mark; the position advanced 283.1 s; the monitor logged nothing and wrote 7 heartbeats; the clock was running at the end |
| Monitor never back | The daemon ran unwatched while the grace lasted (position advanced 232.1 s between 5 s and 238 s) and **tripped itself at 240.1 s** after it was started: `monitor_silent`, source `daemon`, the clock seen paused in the same second, `PAUSE` present, one line in `trips.jsonl`, then the position stood still to the end of the run (50 s) |
| A monitor started at 255 s, **after** the trip | It ran (2 heartbeats) and did **not** clear or replace the latch: still one trip, still paused, the daemon still up. Only `release` followed by `resume` would continue |

Evidence preserved: `TRIPPED`, `trips.jsonl`, the heartbeat, the monitor's log, `clock.json`, `clock_log.jsonl` and the daemon's output were copied beside each run, and the compact record above is committed. **The exposure the fix accepts is exactly this:** up to 240 s of broadcast with no monitor after a daemon restart, bounded by the daemon's own trip. This is measured with real processes; it says nothing about systemd (see below).

**Not rerun:** the OOM scenarios of section 11 (`tools/oom_check.py`) were not repeated after this change. None of them restarts the daemon while the monitor is dead, which is the only case the rule change touches (the daemon that outlives a dead monitor still trips after 120 s, as section 11 recorded).

**The one reliable order:** daemon, then monitor, then supervisor (what the units' `After=` lines express; unchanged). The boundary harness now uses exactly this order and these argument lists.

**Not established:** systemd's own ordering (a simple service counts as started when it is forked, so "same instant" is my model of boot); the 5 of 5 same-instant runs are a measured race on this host, not a guarantee (the monitor reports a lapsed lease only as a logged violation, never a trip, so losing the race costs a log line).

## 16. The replay library against the playback index, by `(season, game ID)` (`tools/index_reconcile.py`)

**The numbers.** After the accelerated run: replay library 425 entries, playback index 424. Reconciled by `(season, gid)` with the monitor's independent oracle (the plan, the builds and the carry record), not by the files:

| | Count |
|---|---|
| in both indexes, both due | 424 (all 400 of season 1 and 24 of season 2) |
| in the library only, **broadcast ended, results not due** (`reveal_delay`) | **1: `S002/reg:w02:g01`** (ended at 184,185 s; its block releases the results at 189,460 s: 5,275 s later) |
| in neither, broadcast not ended (`not_yet`) | 103 |
| duplicates in either index | 0 (425 and 424 entries are 425 and 424 distinct keys) |
| omissions, early entries, unplanned entries | 0 |

**Why it is not a duplication or an omission.** The two indexes have different release rules, both intended in the code: the library lists a game when its **broadcast ends** (`src/dflsite/broadcast.py`, `reconcile`: `want["end"]`), because the viewers have just watched it; the playback index lists it when its **block's results are released** (`src/dflsite/season.py`, `materialize`: the block's `events_at`), which for a window is after the slot ends and the recap begins. So the library is expected to run ahead of the playback index by the games that have ended and not been released, and never behind.

**Tested, not just argued.** `tests/test_index_reconcile.py` runs a three-game, two-window season through the real daemon code second by second: the library is ahead exactly between a game's end and its block's release, always by the `reveal_delay` kind, never after the release; the playback index never lists a game the library does not; at the end of the season both list the same three games; and each fault the tool names is found when it is made (a duplicate, a game missing from either index, a result released early, an unplanned entry). On the real files, a continuation in virtual time after the run showed the gap growing as the next window's games ended (10 games behind at 185,512 s, 20 at 189,312 s) and **closing completely at the release**: at 189,472 s both indexes held the same 444 games, every key `both`, no duplicate, and the same at 189,612 s and 191,012 s. That continuation is accelerated (a clock rewrite) and was run once by hand on the scratch files; it is not a committed script.

**What I did not settle, as a design question rather than a fault.** A library entry carries the game's `final` score from the moment the broadcast ends, 1 h 28 min before this game's results are released in the playback index. That follows from "the replay library is what viewers have watched", but it is a rule the owner may want to look at: a viewer who watched the game knows the score, one who did not can read it from the library an hour and a half before the recap. This is the existing design, not changed here.

## 17. Crossing season 2's first results in real time (`tools/reveal_real.py`)

**What it is.** Season 2's first results are released 8,940 s (2 h 29 min) after the season starts, so a real-time test cannot sit through them from the boundary. The run has three labelled parts: **segment A**, `tools/boundary_real.py` unchanged (real time, supported controls: season 1's end, the hold, the hand-over, the supervisor building season 2 with the real engine; 21 of 21 checks); a **jump**, which is fixture preparation with every process stopped (the clock is rewritten and the real daemon code is driven in virtual time, 421 ticks in 2 s, from 173,174 s to 181,594 s, ending with a freshly lapsed lease and the production `ready_until`); and **segment B**, in which the daemon, monitor and supervisor are started with the unit files' own `ExecStart` lines in the documented order and the harness then only reads, for 317 s of real time, from 137 s before the reveal to 180 s after it. **The clock is rewritten once, between A and B, with nothing running. Nothing edits it after either segment starts**, and the clock's own log for segment B holds one entry, `downtime`, the daemon recording the outage when it started (checked: every entry is one the daemon's code writes).

**What was observed (22 of 22 checks passed; compact record in `docs/evidence/reveal_real_2026-11-07.json`):**

| | Before the reveal (127 one-second samples) | At the reveal (position 181,740 s) | After (171 samples) |
|---|---|---|---|
| Playback index | 400 entries the whole time | **400 to 424 in one step, seen at 181,741.1 s** (1.1 s after the reveal's position) | 424 |
| Replay library | 424 (24 season-2 games had already ended) | unchanged | 424 |
| `index_reconcile` by (season, game ID), 64 records, one every 5 s | 400 `both`, **24 `reveal_delay`**, 192 `not_yet` | | **424 `both`**, none waiting, 296 `not_yet`; no duplicate, no omission, no early entry in any record |

The 24 games that were waiting are exactly the 24 that appear in the playback index after the reveal, so the library running ahead of the playback index is the reveal delay of section 16, now seen closing in real time. Also over segment B: every file and index entry present when B started is unchanged and in the same place (the indexes only grew by appending); the public folder and the private store pass the backup tool's reference closure; the monitor logged no violation, never tripped and wrote 10 heartbeats; the viewers' position never went back and never ran faster than time (1.00 s per second); the monitor's oracle, run once more over the final files, finds no early result. Memory groups: daemon 46 MB, monitor 60 MB, supervisor 675 MB (page cache included, against 768M; zero limit hits and zero kernel kills).

**The 425/424 comparison as a reproducible check.** Section 16's numbers came from a one-off look at the end state of an accelerated run. They are now produced by commands, not by hand: `tools/index_reconcile.py --root R --public P` classifies every game by (season, game ID); `tests/test_index_reconcile.py` proves the classification on the test season (the gap exists between a game's end and its release, closes at it, and each fault is found); `tests/test_reveal_real.py` proves the jump and the crossing in miniature; and `tools/reveal_real.py` makes the comparison every 5 s on the real files and checks that the playback index grows by exactly the games that were waiting.

**Limits, stated plainly.**
* Segment B is 5 minutes around **one** reveal (the first of a season). Later reveals, the end of season 2, a second hand-over and any reveal with the supervisor busy are not covered.
* The jump is an accelerated step. What it produces (a public tree and clock as a long run would leave them) is checked by the same oracle and closure as everything else, but it was made in virtual time on the real files, not broadcast.
* The two indexes were not seen to *differ for long* in B: the library was already 24 ahead when B started, so B shows the closing, and the section 16 look and the test season show how the gap opens.
* Still no systemd: the processes were started directly, in the order the units express, not by systemd.

