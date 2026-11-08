# vtest: validation harness for the memory rule

Validates the pinned candidate (`tools/vtest/PIN`) on a **disposable** machine with systemd and cgroup v2. Synthetic data only. Nothing here touches a real service.

**Status: written and unit-tested; never executed under systemd.** The only execution so far was `selftest` (plain processes, no systemd) and `tests/test_vtest.py` (fakes and synthetic samples). Expect to find harness bugs on the first real run; the smoke run exists for that.

## What it can and cannot measure

The kernel gives no exact "non-reclaimable footprint". The monitor's `pinned` is an **estimate**, and V1 does **not** measure its error. V1 measures:

* a controlled input: the loader logs the bytes it read; the estimate must be consistent with that known input (a bound, not an error figure);
* kernel reclaim activity (`memory.events max`, `pgscan`, `pgsteal`) proving the cache was really being reclaimed;
* reclaim probes (`memory.reclaim`, kernel 5.19+): memory the kernel gave back is a lower-bound evidence of reclaimable memory, never a truth value;
* pressure counters (PSI) and workload survival (no trip, no OOM, no restart, loader still reading).

## Stages (the four requested checks)

| Stage | Question |
|---|---|
| V1 | Real cgroup-v2 cache growth: no false trip during a sustained healthy workload |
| V2 | Sustained anonymous pressure trips as designed (V2a); an OOM: incident record and hold, victim, systemd's actual recovery (V2b) |
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
6. Copy `/srv/dfl-vtest/evidence/<run-id>/` off the machine (each stage has `MANIFEST.sha256`). Review before publishing anything.

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

* OOM victim: normally the injected load, not the daemon. V2b reports the victim from the kernel log; a load-only or unknown victim leaves `V2b.daemon_oom_exercised` **incomplete (not_run)**, so V2 is not a pass. Exercising a daemon kill is a step not yet written.
* V2b requires a persistent incident record and hold after an OOM (latch, new evidence, daemon paused, clock stopped, latch unchanged). No latch fails V2 and is a defect of the candidate, to be fixed in a new candidate; the harness contains no runtime fix.
* Injecting load into the daemon's cgroup stands in for a real leak; it does not exercise the daemon's own allocation behaviour.
