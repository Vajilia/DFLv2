# vtest: validation harness for the memory rule

Validates the pinned candidate (`tools/vtest/PIN`) on a **disposable** machine with systemd and cgroup v2. Synthetic data only. Nothing here touches a real service.

**Status: written and unit-tested; never executed under systemd. The pinned candidate is unvalidated.** Executed so far: `selftest` (plain processes, no systemd), the unit tests (fakes and synthetic samples), the start gate, stop hook, daemon and monitor as an unprivileged user on a scratch layout (they wrote only inside their three folders), and the durability probe against the real code under strace. Expect to find harness bugs on the first real run; the smoke run exists for that. The run order, checkpoints and reboot steps are in `docs/VTEST_EXECUTION_SCHEDULE.md`.

## What it can and cannot measure

The kernel gives no exact "non-reclaimable footprint". The monitor's `pinned` is an **estimate**, and V1 does **not** measure its error. V1 measures:

* a controlled input: the loader logs the bytes it read; the estimate must be consistent with that known input (a bound, not an error figure);
* kernel reclaim activity (`memory.events max`, `pgscan`, `pgsteal`) proving the cache was really being reclaimed;
* reclaim probes (`memory.reclaim`, kernel 5.19+): memory the kernel gave back is a lower-bound evidence of reclaimable memory, never a truth value;
* workload survival (no trip, no OOM, no restart, loader still reading);
* broadcast behaviour measured directly, in two phases that never overlap (publication of the fixture's first window, then cache stress): the lease never lapses, every live segment is published before viewers need it (the viewing-coverage check), no publication error; clock stand-stills and tick age are reported and do not gate. Criteria and their rationale: `docs/MEMORY_MONITOR_VALIDATION_CRITERIA.md`. `broadcast_probe.py` samples the clock once a second from outside the daemon's cgroup;
* pressure counters (PSI) are recorded as a diagnostic and gate nothing (they include the loader).

## Stages (V0 is the setup; V1-V4 are the four requested checks)

| Stage | Question |
|---|---|
| V0 | Is the setup what the design says? The services run as a dedicated unprivileged user with the production sandbox and permissions (owners and modes judged; create-a-file probes as that user; systemd's applied properties and the running processes agree); the harness collects the kernel log itself; a clean stop closes the run marker and a start opens one for this invocation before the main process starts; a strace of the real start gate and stop hook shows the order write / fsync / rename / unlink / fsync of the folder. **Measures nothing about the memory rule** |
| V1 | Real cgroup-v2 cache growth: no false trip during a sustained healthy workload |
| V2 | Sustained anonymous pressure trips as designed (V2a); an OOM: incident record and hold, reported victim, systemd's actual recovery (V2b); the same with the daemon as the OOM victim (V2c). Both also judge the lifecycle design: the stop hook (not the gate's fail-safe) wrote the latch, before the restart, as one incident line, with the run marker closed and reopened |
| V3 | Monitor killed, stopped, hung, or repeatedly OOM-killed: the daemon pauses itself safely. V3e: negative control, a daemon `kill -9` / restart / stop+start that is not an OOM records nothing |
| V4 | An existing latch survives restarts and kill -9 of either service, and keeps the daemon paused; only the documented `release` clears it. Failure injection: with the guard folder immutable an OOM must not let a new daemon run, and recovery must start the daemon held. Reboot: marker closed after a clean reboot, kept after a hard reset |

## Minimum resources

* A disposable Linux machine or VM (not a host that runs anything real): 1 vCPU, 1 GiB RAM, swap **off**, 5 GiB free disk, kernel >= 5.4 (>= 5.19 for reclaim probes), systemd >= 243 as PID 1, **writable cgroup v2** with the memory controller, `python3`, `git`, `strace`, `setpriv` (util-linux), `chattr`/`lsattr` (e2fsprogs), root, and a dedicated service user (below).
* A container without its own systemd does not qualify. The failure-injection case needs a filesystem that supports `chattr +i`. A cloud VM costs money: **needs the owner's explicit approval.** A local VM (any hypervisor) or a spare machine costs nothing.
* Time: smoke run about 1.8 h; qualifying stages about 2.5 h; with setup, six archives and the two reboot variants about 5.5 h attended (`docs/VTEST_EXECUTION_SCHEDULE.md`).

## Operator steps (all on the disposable machine)

1. Create the marker by hand: `sudo touch /etc/dfl-vtest-disposable` (the harness refuses to run without it; it is your statement that the machine is disposable). Install the tools (`sudo apt-get install -y git python3 strace util-linux e2fsprogs`) and create the service user (the harness never creates users): `sudo useradd --system --user-group --no-create-home --shell /usr/sbin/nologin dfl-vtest`.
2. `git clone` the repository at the harness revision; `python3 tools/vtest/vtest.py pin-check` (code under test must equal the pin).
3. `python3 tools/vtest/vtest.py preflight`: must say READY (read-only).
4. `sudo python3 tools/vtest/vtest.py plan` to read exactly what will run; then `sudo python3 tools/vtest/vtest.py install` (renders `dfl-vtest-*` units from the pinned templates, prints each layout change, sets owners and modes for the service user, starts nothing). `install` refuses root or a missing user; `run` refuses an install made by another harness revision. To install again (after a failed install or a new harness revision) run `sudo python3 tools/vtest/vtest.py teardown --yes` first, which keeps the evidence folder; a plain second `install` is refused because the pinned tree already exists.
5. `sudo python3 tools/vtest/vtest.py run --smoke` first (non-qualifying shakedown), then `run` for the qualifying run. After the full V4, after a **clean** reboot run `run --stages V4 --after-reboot` **before starting any unit** (the stage reads the run marker the reboot left, judges it, then starts the units itself; `start-units` first makes the marker rows `invalid`). The full V4 saves the state the reboot is judged against once, so before the **second** variant run `run --stages V4 --capture-reboot-state` (it refuses unless the latch, both services, a held daemon and an open marker are present). For the **hard-reset** variant the operator cuts the power or resets the disposable machine instead of rebooting it, then runs `run --stages V4 --after-hard-reset`. Stages can be run in separate runs (`--run-id`) so that evidence leaves the machine at each checkpoint: see the schedule.
6. `sudo python3 tools/vtest/vtest.py archive --run-id <run-id>`: collects the unit journals, kernel log and OOM lines, unit states and machine facts, writes `EVIDENCE_MANIFEST.json/.txt` (every required item with size and SHA-256, or `MISSING` / `NOT_PRODUCED` with the reason), makes one compressed archive in `/srv/dfl-vtest/export/`, reads it back and verifies it. Exit 3 prints `DO NOT CLEAN UP THE HOST`: the archive does not verify or required evidence is missing.
7. Copy the archive off the machine by the approved private method and check its SHA-256 there. Review before publishing anything; evidence is private and never goes to GitHub. `teardown --include-evidence --yes` refuses until a verified archive of every run exists.

## Failure injection (V4.immutable_guard: its own scenario)

This is **not** a stand-in for a full disk. `chattr +i` makes every create, rename and unlink in the folder fail with EPERM; a full disk gives ENOSPC and can leave partial writes (that case is covered only by a local test on a 48 KiB tmpfs). A pass here says what happens when the guard folder cannot be written at all.

The one filesystem fault the harness injects is `chattr +i` on `/srv/dfl-vtest/guard` (refused for any other path), read back with `lsattr`, and removed in a `finally`; if it cannot be removed the stage stops and tells you the exact `chattr -i` command. The filesystem must support it (ext4, xfs; not tmpfs): if `chattr` fails the case is `invalid`, not a pass. While the folder is immutable the monitor may be unable to refresh its heartbeat for a few seconds (the daemon trips on silence only after 120 s).

## Who runs as whom

* **The services** (daemon, monitor) run as the dedicated unprivileged service user (`dfl-vtest`), under the template's sandbox. One shared user mirrors the production templates (`dflv2` for daemon, monitor and supervisor). The service user owns its store (`root/store`), the public folder and the guard folder; everything else, the pinned code included, is root's and read-only to it. A production builder user separate from the daemon's user is **not** modelled (the harness, as root, plays the builder).
* **The harness** runs as root, never as a child of a service. Kept separate from the services: placing loaders in the daemon's cgroup, collecting the kernel log, `chattr`, unit control. The operator's documented commands (`release`, `resume`) are run **as the service user**.
* **Kernel log:** the service user may be unable to read it (production: probably not, so an incident record says the victim is unknown, which is allowed). The test's victim always comes from the harness's own collection; for V2c that needs root (`journalctl -k` / `dmesg`). Without it the victim is unknown to the test and the check is incomplete. V2 also requires a row saying the independent collection identified the victim.
* V2c also needs write access to `/proc/<pid>/oom_score_adj` of the daemon's test-cgroup processes (raising needs nothing more; lowering would need `CAP_SYS_RESOURCE` and is refused).

## Rules the harness enforces

* Code under test is the pinned tree, extracted with `git archive` and verified blob by blob. Harness commits on top do not change it.
* Writes only under `/srv/dfl-vtest`, `/opt/dfl-vtest-src`, `/etc/dfl-vtest`, `dfl-vtest-*` unit files. `systemctl` may only change `dfl-vtest-*` units; cgroup writes are limited to `memory.reclaim` and `cgroup.procs` of those units. Preflight refuses a machine with real DFL units or paths.
* **Stops at the first stage that does not pass.** Nothing is cleaned up on a stop; evidence is kept; a later run is refused until `--acknowledge-stop 'reason'` (recorded).
* `invalid` (the workload did not do what the test needs) is not a pass.
* Nothing is deleted automatically. `teardown` needs `--yes` and keeps evidence unless `--include-evidence`.
* Nothing is sent anywhere; the monitor's site is loopback.

## When to repeat a stage

Retests are selected by the behaviour a change can affect. `python3 tools/vtest/vtest.py affected --recorded <harness rev of the recorded run>` prints the stages to repeat **and the reason for each file**:

| Change | Repeat |
|---|---|
| the start gate, stop hook or daemon (`tools/broadcast_run.py`) | V0, V2, V3, V4 (V1, the memory rule, does not read it); and it is a new candidate (new pin) |
| the monitor (`tools/rehearsal_check.py`), `src/`, the fixture generator, any other code or `deploy/` file | all stages; new candidate |
| a unit template: comments only / a lifecycle directive / a resource limit / anything else | none / V0, V2, V3, V4 / all / all |
| harness: a stage file | that stage |
| harness: `perms.py`, `durability.py` | V0 |
| harness: `lifecycle.py` | V2, V3, V4 |
| harness: shared machinery (`common.py`, `render.py`, `sampler.py`, `loaders.py`, `site_server.py`, `evaluate.py`, `scenarios.py`, `PIN`) | all stages |
| docs, other tests, driver / preflight / evidence | none |

## Deviations from production units (recorded in `install.json`)

Unit names, paths and the loopback URL; `User=`/`Group=` set to the dedicated service user (the install refuses root); `[Install]` removed; `--min-free-gb` floor lowered to fit a small disk. Memory limits, `OOMPolicy`, `Restart*`, `StartLimit*` and every `ExecStart` flag are **unchanged** (checked by `invariants`).

## Known limits

* OOM victim: in V2b normally the injected load. V2c makes the daemon the preferred victim by **raising** `oom_score_adj` to 1000 for the processes already in the daemon's test cgroup (guarded, raise-only, 0..1000, that cgroup only, undone by the restart; the load keeps the default). `V2.daemon_oom_exercised` passes only with **kernel evidence naming the daemon** as a victim; otherwise it is **incomplete (not_run)** and V2 is not a pass. An incomplete stage blocks the full run.
* V2b and V2c require a persistent incident record and hold after an OOM (latch, new evidence, daemon paused, clock stopped, latch unchanged). No latch fails V2 and is a defect of the candidate, to be fixed in a new candidate; the harness contains no runtime fix.
* Injecting load into the daemon's cgroup stands in for a real leak; it does not exercise the daemon's own allocation behaviour.
* The durability probe shows the **order** of the program's own system calls, not that the disk honoured them. A power cut on a running machine cannot be tested; V4's hard reset shows only that the marker survives a reset of this machine.
* The service user's permissions are judged from the harness's layout, which mirrors the templates and the integration document. How production's builder user and daemon user share the store is not modelled.
