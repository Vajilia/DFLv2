# Validation plan: the memory rule on cgroup v2 under real systemd

**Status: a plan. Nothing in it has been run.** It is the work that `docs/MEMORY_MONITOR_RULE.md` marks as unverified: the rule so far has been tested on synthetic v2 files and on real workloads under cgroup **v1 without systemd**. This plan closes that gap on a disposable machine, with generated data only.
Nothing here deploys the candidate, restarts anything that exists today, or touches the production service, a running rehearsal, or any real or unreleased season data.

## 0. Entry, exit and abort

* **Entry:** the owner approves this plan and names one candidate revision (a commit hash on this branch or a later one). Any code change after approval restarts the plan.
* **Exit:** a written report (section 8) with every acceptance check marked pass, fail or not run, with the raw sample logs attached. A single failed check means the candidate does not proceed to a deployment gate.
* **Abort at once if:** any write lands outside `/srv/dfl-vtest/` or `dfl-vtest-*` unit files; free disk on the test VM falls below 4 GB; the VM gains any route to, or credentials for, another host; or the 12 h cap on wall-clock time is reached. On abort: copy out the evidence (section 6), then roll back.

## 1. Resources

| Need | Specification |
|---|---|
| Machine | one **disposable VM** of the same size class as the target (2 vCPU, 2 GB RAM, about 25 GB disk), snapshot-capable, created for this test and destroyed after it |
| OS | the same distribution release and kernel family as the target, systemd as PID 1, unified cgroup v2 (`stat -fc %T /sys/fs/cgroup` is `cgroup2fs`), memory controller enabled for `system.slice`, swap **off** (the target has none); record `uname -r`, `systemd --version`, `cat /sys/fs/cgroup/cgroup.controllers` |
| Software | what the target already runs: `python3`, `git`, `systemd`; nothing is installed on the VM beyond the candidate's own checkout and the test scripts; no web server (a loopback static file server from the Python standard library serves the generated public folder) |
| Data | a **generated** fixture league and season from the repository's own test generator, plus synthetic files to fill the page cache; no real feed, no real or unreleased season data, no copied backups |
| Time | about 9 h of attended wall-clock time (V0 1 h, V1 2 h, V2 1.5 h, V3 1 h, V4 1 h, V5 1 h, report 1.5 h), hard cap 12 h |
| People | the owner (approval, and a yes/no at each stage boundary); no one else needs access |
| Test tooling still to be written (test-only, in `tools/`, reviewed before use) | (a) a sampler that records `memory.current`, `memory.stat`, `memory.events`, `memory.pressure` of a named cgroup once a second with `read_cgroup_memory` into a JSONL file; (b) a load unit that runs the existing workload scenarios (`tools/memory_workloads.py`: cache stream, anonymous growth, dirty writer) inside a systemd service with `MemoryMax=` set, in place of the harness's v1 cgroup helper; (c) `vtest_install.sh` / `vtest_teardown.sh` (section 3). These are the only new code the plan needs; they are not part of the candidate. |

## 2. Isolation

* **Not** the production host, **not** a host with a running rehearsal or any broadcast: a new VM in its own network scope, no inbound access except the operator's shell, no route to production, no shared volumes, no stored secrets, no API tokens, no credentials copied in. Nothing is created that needs a password.
* Units are **copies** of the repository templates, renamed `dfl-vtest-daemon.service`, `dfl-vtest-monitor.service`, `dfl-vtest-load@.service`, with paths under `/srv/dfl-vtest/`, `--url http://127.0.0.1:<port>`, and the test-only changes listed in section 3. The real units' names and paths are never used.
* Loopback only. No alerting, mail or message of any kind is configured or sent; the monitor writes to its log and evidence folder only.
* Every unit starts **disabled**; no timer is created; nothing is enabled to start at boot except where stage V5(d) needs a reboot, and that uses a one-shot manual `systemctl start` after boot, not an enabled unit.
* Evidence leaves the VM only as a reviewed report with the host name, address and account identifiers removed.

## 3. Setup and rollback

**Setup (stage V0, about 1 h).**
1. Create the VM; record the facts in section 1. Take the snapshot **`vtest-clean`** before anything is copied in.
2. Check out the approved revision into `/opt/dfl-vtest-src/<hash>/`; verify the hash. Run `tests/test_memory_rule.py` and the monitor/guard test modules there (they must pass on the VM's kernel and Python).
3. `vtest_install.sh` installs the three test units with these deliberate, test-only differences from the templates, each printed in a diff for review: unit names and paths; `--min-free-gb` and `--max-public-gb` lowered to fit the VM's disk; `MemoryMax` on the daemon copy set per stage (section 5), never on a real unit. The memory-related lines (`OOMPolicy=stop`, `Restart=`, `RestartSec=`, `StartLimit*`, `--every`, `--cgroup` form) are **unchanged** from the templates, since they are what is being validated.
4. Take the snapshot **`vtest-installed`**.

**Rollback.**
* Between stages: revert to `vtest-installed`, so each stage starts from the same state. **Copy the stage's evidence out first.**
* `vtest_teardown.sh`: stops and disables only units whose names start with `dfl-vtest-`, removes only their unit files and drop-ins and `/srv/dfl-vtest/` (it refuses any path not under those prefixes, and prints before it acts), then `systemctl daemon-reload`. It is for use inside the VM; it never runs anywhere else.
* End of test: destroy the VM after the report is accepted. Nothing outside the VM was changed, so nothing else needs undoing.

## 4. What every stage records

A 1 Hz sampler log of the relevant cgroup(s); `journalctl -u 'dfl-vtest-*'` for the stage; the monitor's `watch.log` (heartbeat lines carry `mem`); the evidence folder; `memory.events` before and after; `systemctl show` for `MemoryMax`, `OOMPolicy`, `NRestarts`, `Result`, `ExecMainStatus`; sha256 of `TRIPPED` wherever one exists. A stage's checks are decided from these files, not from memory.

## 5. Stages and acceptance checks

Each check is pass or fail on recorded numbers. "The daemon" is the real daemon unit run against the generated fixture; "the load" is the `dfl-vtest-load@` unit.

### V1: healthy cache growth (about 2 h)
*Purpose:* the failure that motivated the change, on a real v2 kernel; and a measurement of the estimate's error.
*Setup:* daemon with `MemoryMax` as in its template; the load streams several times `MemoryMax` of generated files through the page cache for at least 60 minutes so that `memory.current` sits at or near the limit.
Checks:
1. `memory.current` reached at least 95 % of `memory.max` and stayed above 90 % for at least 30 minutes. (If it does not, the stage did not test what it was meant to, and fails.)
2. **No trip, no `TRIPPED`, no violation lines** in `watch.log`; `oom_kill` stays 0; the daemon's lease stays alive.
3. The estimated `pinned` stays below 30 % of the limit throughout, and below 60 MB above the daemon's own anonymous memory.
4. Estimate error measured: at ten sampled instants, drop clean caches (`echo 1 > /proc/sys/vm/drop_caches` is **not** used, since it is global and changes the host's state; instead use `memory.reclaim` on the test cgroup if the kernel offers it) and compare the memory the kernel actually gave back with the sampler's `reclaimable` estimate; report the ratio. Not a pass/fail gate on its own, but a result below 0.8 is written up as a limit.
5. Every heartbeat line in the stage has the `mem` object and its fields are consistent (`anon + file + kernel = current` within a page of rounding).
6. Run once more with the monitor stopped for the whole stage and compare `memory.events max`: the group is demonstrably being reclaimed (`max` or `pgscan` rose) while healthy, which is what the old rule misread.

### V2: sustained anonymous-memory growth (about 1.5 h)
*Purpose:* the rule fires on genuine danger, through the full chain: load, monitor, `TRIPPED`, daemon pause. The monitor watches the **load's** cgroup (set `MemoryMax` equal to the daemon's) while sharing the guard folder with the real daemon.
Scenarios and expectations (each from `vtest-installed`):
* *Plateau* at 93 %: trips between 60 s and 100 s after crossing 90 % (the cadence adds up to 10 s); evidence has the breakdown with `anon` as the dominant term and 12 recent samples; the daemon pauses within 2 s of the latch.
* *Slow leak* (0.85 × limit, then +0.3 MB/s on a 192 MB-class limit, scaled to the limit used): trips **before** `oom_kill` rises; the margin is recorded.
* *Fast growth* (several MB/s past the limit): the kernel kills the load; the monitor trips on `oom_event`, or the daemon-silent path trips; either is acceptable, which one occurred is recorded; no state is left half-written.
* *Short spike* (93 % for 20 s, then freed): **no** trip.
* *Hover* (`anon` + dirty writer at 80–100 %): result recorded without a pass/fail expectation, to see whether per-group v2 throttling avoids the soft spot seen on v1.
Checks: expected trip or no-trip in each scenario; one `TRIPPED` per trip; trip kind `memory_high`; `reason` in the evidence is `sustained_unreclaimable` or `oom_event` as expected; the daemon stops advancing its clock after the latch and does not resume by itself.

### V3: OOM recovery (about 1 h)
*Purpose:* what actually happens when the kernel kills the real daemon, which is the protection against a leak the rule cannot outrun.
*Setup:* measure the daemon's steady footprint first (from V1), then set the **test copy's** `MemoryMax` to about 70 % of it (a deliberate, documented departure) so the real daemon is OOM-killed by its own working set. Record that the departure is the only way the kill is produced.
Checks:
1. The kernel's `oom_kill` rose; with `OOMPolicy=stop` the **whole unit** stops: no process of the unit survives (`systemd-cgls`, `pgrep` by cgroup), no orphan holding the lease or lock.
2. `systemctl show` reports `Result=oom-kill` and the restart behaviour matches the template (`Restart=on-failure`, `RestartSec=5`): restarts occur, and after `StartLimitBurst` starts in the interval the unit stays **failed**, as designed.
3. The monitor trips (`oom_event`, `memory_high`, or the daemon-silent kind), whichever it reports is recorded; `TRIPPED` exists before the daemon can publish anything new, or the daemon starts paused.
4. **Public bytes unchanged:** a sha256 manifest of the public folder before the kill and after recovery differs only by files the broadcast schedule legitimately adds before the kill; nothing is rewritten or truncated. The daemon's own consistency check (`broadcast_run.py status`, `rehearsal_check.py verdict` on the fixture) reports no corruption.
5. The cgroup is removed with the unit; when the monitor next reads it, it does not crash and does not raise a false memory trip from a missing cgroup.
6. After `release` and `resume` (section V5 procedure) the clock continues from the paused position; nothing is skipped or replayed.

### V4: monitor failure (about 1 h)
*Purpose:* the protection must not depend on the monitor staying healthy.
Checks (all with the real daemon running and no latch present at the start):
1. `kill -9` of the monitor: systemd restarts it after `RestartSec`; the heartbeat gap stays below the guard's stale limit; **no trip** and no pause.
2. `systemctl stop` of the monitor and leaving it stopped: after the guard's stale limit the **daemon trips itself** (`monitor_silent`) and pauses; `TRIPPED` is written by the daemon; the evidence is written.
3. `SIGSTOP` of the monitor (a hang rather than a crash): same outcome as 2.
4. Monitor killed by the kernel for its own memory (a test copy with a small `MemoryMax`): restarts; after `StartLimitBurst` it stays failed and the daemon trips `monitor_silent`.
5. The monitor's cgroup path is wrong or unreadable: the monitor logs it, keeps running, trips nothing from that alone, and the heartbeat continues (a missing memory file is not a memory emergency).
6. First-start grace: starting the daemon before the monitor and the monitor before the daemon both give no trip inside the grace period and a trip after it if the other never comes.

### V5: preservation of the trip latch (about 1 h)
*Purpose:* a latch, once written, survives everything that can happen to the services, and only the documented procedure clears it.
Start from a state with a `TRIPPED` latch written by V2's plateau scenario (reproduced). Record its sha256. After each step, re-hash and check the daemon:
a. restart the **daemon**; b. restart the **monitor**; c. restart **both** in each order; d. **reboot the VM** and start the units manually.
Checks:
1. The sha256 of `TRIPPED` is **identical** after every step; the file is not rewritten, renamed or removed.
2. The daemon stays **paused** after every step: its clock does not advance; no public file is added.
3. `broadcast_run.py resume` is **refused** while the latch exists.
4. `rehearsal_check.py release` is **refused** while the monitor's heartbeat is stale and **accepted** when fresh; it moves `TRIPPED` into `evidence/released/` and **deletes nothing** (before and after file lists compared; the evidence folder only grows).
5. After `release`, `resume` is accepted and the clock continues from the paused position.
6. A second trip after the release produces a second latch and a second evidence folder; the first remains in `released/`.

## 6. Evidence handling

Copy each stage's files out of the VM before reverting it, into a folder named for the stage and the candidate hash, with a `sha256sum` manifest. Nothing is deleted automatically, on the VM or anywhere else; the VM's destruction at the end is a manual step after the report is accepted.

## 7. Risks and how the plan contains them

| Risk | Containment |
|---|---|
| A test escapes to a real service | separate VM, no route to other hosts, `dfl-vtest-` prefix check in install and teardown, loopback only |
| The kernel or systemd differs from the target and the result does not transfer | same OS release and kernel family; the versions are recorded; the report states how close they are |
| `memory.reclaim` is not available on the chosen kernel (check V1.4) | the error measurement is then replaced by comparing `memory.events max` against the estimate; the limit is written up either way |
| A stage is inconclusive (for example the cache never reaches 95 %) | the stage fails as written; it is repeated with more load, not edited into a pass |
| The 12 h cap is exceeded | abort, copy out, report what ran |
| The test disturbs how the candidate is judged | no code is changed during the plan; a needed change ends the plan and starts a new candidate |

## 8. Report

One document: the revision tested; the VM facts; for each check pass / fail / not run with the file that decides it; the measured estimate error (V1.4); the actual trip times against the 60 s window and the leak-rate argument; every deviation from this plan; and a recommendation (proceed to the next deployment gate, or not). Delivered to the owner, who decides.

## 9. Not covered here

Deployment itself; the production service's units; a full-length broadcast rehearsal (this plan does not start one); the engine's own behaviour; capacity of the public folder beyond the fixture; any change to limits. The systemd lifecycle of the **real** units (install, enable, boot ordering, uninstall) remains a separate deployment gate.
