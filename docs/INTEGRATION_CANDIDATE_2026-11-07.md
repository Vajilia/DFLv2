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

Together 1472M, under a 2 GB machine with room for the system and Caddy. `MemoryMax=1500M` is **not** adopted. Not tested: an out-of-memory kill of each unit and what the monitor and supervisor then do (the fault tests cover a daemon death, which is what an OOM kill looks like from outside).

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

**Start order:** daemon, then monitor, then supervisor. **Stop order:** supervisor, monitor, daemon. Stopping the supervisor does not stop the broadcast; stopping the daemon pauses viewers' position (it resumes from its clock state).

**Reading it:** `broadcast_supervisor.py check` exit codes 0 ok, 1 warning, 2 critical, 3 unknown/stale. Daemon exit 70 = public cap exceeded, 75 = another daemon holds the lock: neither restarts (`RestartPreventExitStatus`). Monitor exit 1 = violations logged; 2 = it cannot write its latch.

| Situation | What it means | What to do |
|---|---|---|
| Guard `TRIPPED` | The monitor paused the daemon; evidence is under `GUARD/evidence` | Read the evidence. Only then `rehearsal_check.py release …` and `broadcast_run.py resume --root … --guard …`. The daemon does not resume by itself after a trip, on purpose. |
| `public_cap_near` | 85% of this season's cap | Look at the folder; decide ceiling, per-season number or retention. Nothing is deleted for you. |
| `public_cap_exceeded` / daemon exit 70 | Over the cap; the daemon stopped, nothing deleted | Find out why (runaway or real growth). Raise the number in `broadcast.env` only after that, restart the three units. |
| `interrupted_evidence` in health | A previous `next-season` was interrupted; its `.partial` was set aside | Keep it until you've looked; remove by hand when done. It never blocks the next attempt. |
| Builder failing | Broadcast holds at the first unbuilt item; supervisor retries with backoff, escalates | Read `supervisor/health.json` and the build log. Viewers see a hold, not a skip. |
| Low disk or inodes | Supervisor stops building first (8 GB); monitor trips at 6 GB | Free space (retention is M8, approval-gated), then release and resume as above. |

## 9. Remaining gaps

1. **Not run on the host.** Nothing here ran on the Droplet. The measurements are from a sandbox; the kernel, disk and cgroup version differ (cgroup v1 here, v2 under systemd, so `MemoryHigh`/`failcnt` semantics differ).
2. **No real season boundary with the real engine and daemon** (rehearsal M5). The two-season run uses a synthetic four-event year with the real builder CLI; the real-engine runs are one season's events plus `next-season`. The first real hand-over has not been observed.
3. **Memory under a real unit** is a hard limit emulated by a cgroup; an actual `MemoryMax` kill of each unit was not exercised.
4. **Public cap:** no ceiling number; the cumulative-slack limitation (section 7); per-season measurement not built.
5. **Monitor memory** was modelled on the real file list repeated per season, not measured over real multiple seasons.
6. **The tuned CPU settings are unproven as necessary** (section 6). The measurement says they were not needed on this host.
7. **Alerts:** nothing connected or sent. **Installer:** none written. Both need review before use.
8. **Branch not merged to anything.** The monitor branch's two later commits are excluded (section 1). Heavy regression suites were not rerun (section 4).
9. Retention (M8) is separate. There is still no automatic space recovery, by design.
