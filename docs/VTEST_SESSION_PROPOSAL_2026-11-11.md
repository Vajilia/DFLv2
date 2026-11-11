# Proposal: the next VM session (systemd validation of candidate 4 only)

**Status: a proposal for the owner's approval. Nothing is provisioned, scheduled or spent.** Details of each step are in `docs/VTEST_EXECUTION_SCHEDULE.md`; this page is the decision sheet. Candidate 4 is `205f891` (`tools/vtest/PIN`; `docs/CANDIDATE_HISTORY.md`).

## What the session is, and is not

* **Is:** one disposable VM, one install, one pin, one harness revision. Smoke run first (`smoke-1`, non-qualifying: it finds harness faults on the first real systemd execution and proves nothing about the candidate), then the qualifying runs `q1`-`q5` covering V0-V4 (setup facts; the memory rule on cgroup v2; the unit lifecycle, OOM latch, start gate and stop hook; clean reboot and hard reset).
* **Is not:** the restore onto a separate host, the full-length broadcast, scheduled uploads, or any change to production. Those are separate sessions (below). The VM holds no production data and **no Spaces key**: its fixture is generated on the machine.

## Duration

| | |
|---|---|
| Expected, nothing failing | about **5.5 hours** wall-clock (setup 15 min, smoke 1 h 50, `q1` 1 h 15, `q2` 50 min, `q3` 45 min, clean reboot 15 min, capture and hard reset 20 min), the schedule's own estimates |
| Time-box | **8 hours** from the start of `install`. At 8 hours, whatever has not finished is recorded as not run and the session stops |
| One repeat allowed | a failed stage may be repeated once after a **harness-only** fix, taking the session to at most **12 hours**. A candidate defect is not fixed in this session: it stops, is exported, and becomes candidate 5 with a new approval |

Stop at the first stage that does not pass. Nothing is cleaned up on a failure. Nothing is called validated until the report lists every check, including anything incomplete.

## Export checkpoints (evidence is private and never goes to GitHub)

| Point | After | Required before going on |
|---|---|---|
| X0 | `smoke-1` | `vtest.py archive --run-id smoke-1` exits 0; copied off; SHA-256 matches on the receiving side |
| X1 | `q1` (V0, V1) | same |
| X2 | `q2` (V2, V3) | same |
| X3 | `q3` (V4), **before the reboot** | same |
| X4 | `q4-clean` (after the clean reboot) | same |
| X5 | `q5-capture` and `q5-hard` | same |

The VM is destroyed only after every archive has exited 0 and its checksum matches off the machine, and after the owner has accepted the report. `teardown --include-evidence --yes` refuses until then.

## Spending limit

* Machine: 1 vCPU, 1 GiB RAM, swap off, at least 5 GiB free disk, Ubuntu 24.04 LTS or similar (systemd 243 or newer as PID 1, kernel 5.19 or newer, cgroup v2). About $6 a month, roughly a cent an hour, on third-party price listings: **confirm the price on DigitalOcean's create page** before approving.
* **Limit: $2.00 for the whole session** (VM hours, plus any snapshot or bandwidth). Expected cost: about $0.10 for 8 hours, $0.15 for 12. I stop and ask before anything that would exceed $2.00 or add a second machine.
* The real exposure is **forgetting to destroy the VM** (it would bill about $6 a month indefinitely) and the account-wide **$35 a month cap**: I cannot see billing, so the owner needs to confirm the month's usage so far leaves room for $2. I propose a reminder to destroy the VM within 24 hours of the end of the session; it is not set until approved.

## Decisions needed from the owner before provisioning

1. **Approve the VM, the 8-hour time-box (12 with one harness-only repeat) and the $2.00 limit.**
2. **Name the private method for getting archives off the machine** (six copy points). Options, with the trade-off for each:
   * *Owner copies with `scp` from their own computer* using an SSH key the owner controls. Needs the owner to run a command six times; no secret ever passes through me. Recommended if the owner is comfortable doing it.
   * *Archives uploaded to a private Spaces prefix.* Needs a bucket key on the VM; the only key level DigitalOcean offers that can upload is delete-capable, so this puts a key that can destroy the recovery bucket on a machine built to be broken. **Not recommended.**
   * *Read through the web console and copy by hand.* Only for the small result files, not whole archives.
3. **Confirm that the VM checks out exactly one commit** (named at provisioning, recorded in the report). The harness refuses to run an install made by another revision, and a later commit to this branch is not part of the session.

## What I do, and what I stop for

* I can do without asking: the standing routine harness-only fixes and retries within the repeat allowance above.
* I stop and ask for: any change to the code under test (a new candidate), any weakening of a criterion, any spending beyond the limit, any second machine, any use of a credential, anything on the production droplet.
* The owner enters every secret; none is typed, captured, logged or pushed by me.

## Scheduled separately (not part of this session, not scheduled until the owner picks them)

| Session | Needs | Depends on | Proposed order |
|---|---|---|---|
| **Separate-host restore** (host-loss recovery: restore from the bucket onto a different machine, and the restore-point replay) | A second disposable machine and a credential to read the bucket (the working Spaces key is retained; a read-only level does not exist, so the key must be entered by the owner on that host and removed afterwards). About 2 hours and a few cents. The drill so far was the same host and proves only that the upload verifies | Independent of the systemd session | Next after it, or in parallel if the owner prefers; the owner chooses the date |
| **Full-length broadcast** (a real-time multi-season run: hand-over, hold, recovery across a boundary) | A machine running for the length of the broadcast (hours to days, so its cost is separate and larger), the real-engine season-1 root, real phones for the frontend | The systemd validation passing: it runs the daemon, monitor and latch that session proves | After the validation and the restore, with its own approval and spending limit |

Neither is combined with the VM above. Each gets its own duration, export points and spending limit when the owner is ready to choose a date.
