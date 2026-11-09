# vtest: validation harness for the memory rule

Validates the pinned candidate (`tools/vtest/PIN`) on a **disposable** machine with systemd and cgroup v2. Synthetic data only. Nothing here touches a real service.

**Status: written and unit-tested; never executed under systemd.** The only execution so far was `selftest` (plain processes, no systemd) and `tests/test_vtest.py` (fakes and synthetic samples). Expect to find harness bugs on the first real run; the smoke run exists for that.

## What it can and cannot measure

The kernel gives no exact "non-reclaimable footprint". The monitor's `pinned` is an **estimate**, and V1 does **not** measure its error. V1 measures:

* a controlled input: the loader logs the bytes it read; the estimate must be consistent with that known input (a bound, not an error figure);
* kernel reclaim activity (`memory.events max`, `pgscan`, `pgsteal`) proving the cache was really being reclaimed;
* reclaim probes (`memory.reclaim`, kernel 5.19+): memory the kernel gave back is a lower-bound evidence of reclaimable memory, never a truth value;
* workload survival (no trip, no OOM, no restart, loader still reading);
* broadcast behaviour measured directly, in two phases that never overlap (publication of the fixture's first window, then cache stress): the clock never stands still past its limit, the lease never lapses, every live segment is published before viewers need it, no publication error. Criteria and their rationale: `docs/MEMORY_MONITOR_VALIDATION_CRITERIA.md`. `broadcast_probe.py` samples the clock once a second from outside the daemon's cgroup;
* pressure counters (PSI) are recorded as a diagnostic and gate nothing (they include the loader).

## Stages (the four requested checks)

| Stage | Question |
|---|---|
| V1 | Real cgroup-v2 cache growth: no false trip during a sustained healthy workload |
| V2 | Sustained anonymous pressure trips as designed (V2a); an OOM: incident record and hold, reported victim, systemd's actual recovery (V2b); the same with the daemon as the OOM victim (V2c) |
| V3 | Monitor killed, stopped, hung, or repeatedly OOM-killed: the daemon pauses itself safely |
| V4 | An existing latch survives restarts and kill -9 of either service, and keeps the daemon paused; only the documented `release` clears it |

## Minimum resources

* A disposable Linux machine or VM (not a host that runs anything real): 1 vCPU, 1 GiB RAM, swap **off**, 5 GiB free disk, kernel >= 5.4 (>= 5.19 for reclaim probes), systemd >= 243 as PID 1, **writable cgroup v2** with the memory controller, `python3`, `git`, root.
* A container without its own systemd does not qualify. A cloud VM costs money: **needs the owner's explicit approval.** A local VM (any hypervisor) or a spare machine costs nothing.
* Time: smoke run about 20 min; qualifying run about 3 h attended.

## Operator steps (all on the disposable machine)

1. Create the marker by hand: `sudo touch /etc/dfl-vtest-disposable` (the harness refuses to run without it; it is your statement that the machine is disposable).
2. `git clone` the repository at the harness revision; `python3 tools/vtest/vtest.py pin-check` (code under test must equal the pin).
3. `python3 tools/vtest/vtest.py preflight`: must say READY (read-only).
4. `sudo python3 tools/vtest/vtest.py plan` to read exactly what will run; then `sudo python3 tools/vtest/vtest.py install` (renders `dfl-vtest-*` units from the pinned templates, prints each layout change, starts nothing).
5. `sudo python3 tools/vtest/vtest.py run --smoke` first (non-qualifying shakedown), then `run` for the qualifying run. `--with-reboot` adds the V4 reboot phase; after the reboot run `--after-reboot`.
6. `sudo python3 tools/vtest/vtest.py archive --run-id <run-id>`: collects the unit journals, kernel log and OOM lines, unit states and machine facts, writes `EVIDENCE_MANIFEST.json/.txt` (every required item with size and SHA-256, or `MISSING` / `NOT_PRODUCED` with the reason), makes one compressed archive in `/srv/dfl-vtest/export/`, reads it back and verifies it. Exit 3 prints `DO NOT CLEAN UP THE HOST`: the archive does not verify or required evidence is missing.
7. Copy the archive off the machine by the approved private method and check its SHA-256 there. Review before publishing anything; evidence is private and never goes to GitHub. `teardown --include-evidence --yes` refuses until a verified archive of every run exists.

## Privileges needed

root; for V2c also write access to `/proc/<pid>/oom_score_adj` of the daemon's test-cgroup processes (raising needs nothing more; lowering would need `CAP_SYS_RESOURCE` and is refused) and a readable kernel log (`journalctl -k`, or `dmesg` as root / `kernel.dmesg_restrict=0`). Without the log the victim is unknown and the check is incomplete.

## Rules the harness enforces

* Code under test is the pinned tree, extracted with `git archive` and verified blob by blob. Harness commits on top do not change it.
* Writes only under `/srv/dfl-vtest`, `/opt/dfl-vtest-src`, `/etc/dfl-vtest`, `dfl-vtest-*` unit files. `systemctl` may only change `dfl-vtest-*` units; cgroup writes are limited to `memory.reclaim` and `cgroup.procs` of those units. Preflight refuses a machine with real DFL units or paths.
* **Stops at the first stage that does not pass.** Nothing is cleaned up on a stop; evidence is kept; a later run is refused until `--acknowledge-stop 'reason'` (recorded).
* `invalid` (the workload did not do what the test needs) is not a pass.
* Nothing is deleted automatically. `teardown` needs `--yes` and keeps evidence unless `--include-evidence`.
* Nothing is sent anywhere; the monitor's site is loopback.

## When to repeat a stage

`python3 tools/vtest/vtest.py affected --recorded <harness rev of the recorded run>` prints the stages to repeat:

| Change | Repeat |
|---|---|
| any code under test (`src/`, `tools/` outside `tools/vtest/`, unit templates, `tests/test_season.py`, fixtures) | all stages, **and it is a new candidate** (new pin) |
| a stage file (`stage_v2.py` etc.) | that stage |
| shared harness code (`common.py`, `render.py`, `sampler.py`, `loaders.py`, `site_server.py`, `evaluate.py`, `scenarios.py`, `PIN`) | all stages |
| docs, other tests, CLI/preflight/render | none |

## Deviations from production units (recorded in `install.json`)

Unit names, paths and the loopback URL; `User=`/`Group=` dropped (runs as root) unless `--user`; `[Install]` removed; `--min-free-gb` floor lowered to fit a small disk. Memory limits, `OOMPolicy`, `Restart*`, `StartLimit*` and every `ExecStart` flag are **unchanged** (checked by `invariants`).

## Known limits

* OOM victim: in V2b normally the injected load. V2c makes the daemon the preferred victim by **raising** `oom_score_adj` to 1000 for the processes already in the daemon's test cgroup (guarded, raise-only, 0..1000, that cgroup only, undone by the restart; the load keeps the default). `V2.daemon_oom_exercised` passes only with **kernel evidence naming the daemon** as a victim; otherwise it is **incomplete (not_run)** and V2 is not a pass. An incomplete stage blocks the full run.
* V2b and V2c require a persistent incident record and hold after an OOM (latch, new evidence, daemon paused, clock stopped, latch unchanged). No latch fails V2 and is a defect of the candidate, to be fixed in a new candidate; the harness contains no runtime fix.
* Injecting load into the daemon's cgroup stands in for a real leak; it does not exercise the daemon's own allocation behaviour.
