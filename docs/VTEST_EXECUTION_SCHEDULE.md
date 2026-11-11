# Execution schedule: memory-rule validation of candidate 4 (originally written for candidate 3)

**Status: a schedule, not a result. Nothing has been provisioned or run.** The candidate is now candidate 4, `205f891` (see `docs/CANDIDATE_HISTORY.md`; candidate 3 was `ecf62ea`), and it and the harness revision named in the report are unvalidated. The decision sheet for the session (duration, export checkpoints, spending limit) is `docs/VTEST_SESSION_PROPOSAL_2026-11-11.md`. Nothing starts until the owner approves the machine and its cost.

One disposable VM, one install, one harness revision, one pin. A qualifying result is the set of runs `q1`-`q5` below, all from the same install. Evidence is private: it is archived on the machine, copied off by the owner's approved private method, and **never pushed to GitHub**.

## Before provisioning (owner)

1. Approve the machine and its hourly cost. Roughly 6 hours of wall-clock if nothing fails (table below); retests are extra and chosen by `vtest.py affected` (section "If something fails").
2. Name the private method for copying archives off the machine (the schedule has six copy points).
3. Machine: 1 vCPU, 1 GiB RAM, swap **off**, >= 5 GiB free disk, ext4 or xfs (needed for `chattr +i`), kernel >= 5.19 (reclaim probes), systemd >= 243 as PID 1, cgroup v2 writable. Used for nothing else.

## S0. Setup (operator, about 15 min)

```
sudo touch /etc/dfl-vtest-disposable                       # your statement that the machine is disposable
sudo apt-get install -y git python3 strace util-linux e2fsprogs   # strace: durability probe; util-linux: setpriv; e2fsprogs: chattr/lsattr
sudo useradd --system --user-group --no-create-home --shell /usr/sbin/nologin dfl-vtest
git clone <repository> && cd <repository> && git checkout <revised pin>      # the harness revision in the report
python3 tools/vtest/vtest.py pin-check                      # code under test == pinned candidate
sudo python3 tools/vtest/vtest.py preflight                 # must say READY
sudo python3 tools/vtest/vtest.py plan                      # read what will run
sudo python3 tools/vtest/vtest.py install                   # renders units, builds the fixture, sets owners and modes; starts nothing
```

The harness never creates users. `install` refuses root or a missing user, and `run` refuses an install made by a different harness revision.

## S1. Smoke run (about 1.8 h; non-qualifying)

`sudo python3 tools/vtest/vtest.py run --smoke --run-id smoke-1` (V0-V4, short settings). Its purpose is to find harness faults on the first real systemd execution. It proves nothing about the candidate.

**Checkpoint X0:** `vtest.py archive --run-id smoke-1`; exit 0 required; copy off; compare SHA-256.

If a stage stops: read its evidence, stop. Fix locally, new harness revision, then `teardown --yes` and `install` again (see "If something fails").

## S2. Qualifying runs

| Run | Command | About | Then |
|---|---|---|---|
| `q1` | `run --stages V0,V1 --run-id q1` | 70 min | **X1** archive, copy off |
| `q2` | `run --stages V2,V3 --run-id q2` | 45 min | **X2** archive, copy off |
| `q3` | `run --stages V4 --run-id q3` | 40 min | **X3** archive, copy off (before the reboot) |

V0 is first because the setup facts (user, permissions, applied sandbox, marker order, durability probe) must hold before anything is measured; it is stage-cheap (about 7 min). The stages are separate runs only so that evidence leaves the machine at each checkpoint; the first stage that does not pass stops the run and blocks the next until `--acknowledge-stop 'reason'` (recorded).

### Reboot steps (V4 reboot phase)

The full V4 (`q3`) saves the state a reboot is judged against. The units are never enabled, so after a reboot nothing runs.

1. **Clean reboot.** After X3: `sudo reboot`. When the machine is back, **do not start any unit**. Run `run --stages V4 --after-reboot --run-id q4-clean`. The stage reads the run marker the reboot left (it must be closed: otherwise every reboot would start held), then starts the units itself, then checks that the latch survived, the daemon is held, and the start recorded nothing. **X4:** archive, copy off. (`start-units` first makes the marker rows `invalid`, not pass.)
2. **Re-record the state for the second variant.** The record from `q3` describes a machine that no longer exists. With the units running and held: `run --stages V4 --capture-reboot-state --run-id q5-capture`. It refuses (and writes nothing) unless the latch, both services, the held daemon and an open marker are present.
3. **Hard reset.** Reset the machine without a clean shutdown: the provider's power-cycle or hard-reset action (closest to a power cut), or, if `kernel.sysrq` allows it, `echo b | sudo tee /proc/sysrq-trigger`. Do not start any unit. Run `run --stages V4 --after-hard-reset --run-id q5-hard`: the marker must still be there, and the start must record exactly one `unaccounted_stop` saying the host restarted, and the daemon must be held. **X5:** archive `q5-capture` and `q5-hard`, copy off.

The hard-reset phase shows only that the marker survives a reset of this machine. It does not show what a power cut does to production hardware (the hypervisor's cache usually survives a guest's reset).

### Time

| Phase | About |
|---|---|
| Setup S0 | 15 min |
| Smoke S1 and X0 | 1 h 50 min |
| `q1` (V0, V1) and X1 | 1 h 15 min |
| `q2` (V2, V3) and X2 | 50 min |
| `q3` (V4) and X3 | 45 min |
| Clean reboot, `q4-clean`, X4 | 15 min |
| Capture, hard reset, `q5-hard`, X5 | 20 min |
| **Total, nothing failing** | **about 5.5 h** |

Estimates come from `vtest.py plan`; real timings are recorded by the run.

## End: destroy the machine only after

1. every archive (`smoke-1`, `q1`-`q5-hard`) exited 0, was copied off, and its SHA-256 matches on the receiving side;
2. the owner has accepted the report.

`teardown --include-evidence --yes` refuses until a verified archive of every run exists. Destroying the VM is the owner's act.

## If something fails

* A failing or incomplete stage **stops the run and leaves everything as it is**. Do not clean up. Archive what exists (X-point), then read the evidence.
* A **harness** defect: fix the harness locally, commit, then `vtest.py affected --recorded <harness revision of the run that failed>` names the stages to repeat and why, by the behaviour the change can affect (for example a change to `perms.py` repeats V0 only; to `lifecycle.py`, V2-V4). Run `teardown --yes` (it keeps the evidence folder; a plain second `install` is refused because the pinned tree already exists) and then `install` again (it is tied to the harness revision), then repeat only those stages. A change to shared machinery repeats everything, and the output says so.
* A **candidate** defect: not fixed by the harness. It is a new candidate with a new pin; `affected` names the stages that new pin's changes reach (a change confined to the start gate and stop hook, `tools/broadcast_run.py`, repeats V0, V2, V3, V4, not V1). The memory-rule stage V1 stays valid for a change that cannot reach it.
* Nothing is described as validated until the report lists every check with its status, including anything incomplete.

## What this schedule does not cover

Production install and boot ordering, a builder user separate from the daemon's user (modelled by root here), that the disk honours fsync, a genuinely full disk (the immutable-folder scenario in V4 is a different fault), and a full-length broadcast.
