# Remaining validation: what changed, what repeats, and the proposed session (2026-11-12)

Nothing here provisions, deploys or changes a credential. Candidate 4 (`205f891`) is unchanged. The owner's decisions of 2026-11-12: **B** for the q3b question (the harness follows the documented recovery order, and the monitor's exit stays visible as a finding; q3b itself stays **FAILED**), and the next milestone order in `docs/LAUNCH_ROADMAP_EVIDENCE.md` section 6.

## 1. Harness corrections versus candidate changes

A **harness correction** changes only `tools/vtest/` (and its tests). It changes the harness revision, not the pin; the affected checks are repeated; nothing about the code under test moves. A **candidate change** touches a file of the code under test (the unit files, the daemon, the monitor, the start gate or stop hook): it needs the owner's approval, a new pin, an entry in `docs/CANDIDATE_HISTORY.md` and the stages `vtest.py affected` names.

| Change | Kind | New pin? | What must repeat |
|---|---|---|---|
| Helper processes die with the command that started them; `run` and `archive` refuse while one exists; `stop-helpers`; `teardown --yes` stops them (defect #4) | Harness (`26d0cdd`) | No | Not a judgment change. The launch path of the sampler, probe and loaders changed, which no real machine has exercised yet: the first real use is the V4 smoke run below. If that smoke shows any difference, repeat the stage concerned. |
| One runner at a time | Harness, already in place (`72e3d11`); a structural test now fails if a new writing command lacks the lock | No | Nothing |
| V4 recovery follows the documented order (monitor first, fresh heartbeat seen, then release); the monitor's death is a finding row | Harness (`26d0cdd`) | No | **V4** (`q3c`), then the reboot checks, which read the state V4 leaves |
| Earlier V4 fixes `9bc3197`, `e224d3a` | Harness | No | V4 (as above) |
| Alert package (drop-ins, `dfl-alert`, poll timer) and the V5 drill | **New component**, not part of the candidate (design: `docs/ALERT_DESIGN_2026-11-12.md`; not built) | No, as long as the candidate's unit files stay byte-identical | **V5** (new); **V3 and V4 repeated with the drop-ins installed**, to show the alert path does not change what they judge |
| Putting `OnFailure=` or other `StartLimit*` values into the candidate's own unit files | **Candidate change** | **Yes** | The stages `affected` names for unit-file changes: V0, V2, V3, V4 at least (V0 compares the unit files byte for byte) |
| Changing the monitor's exit-2 behaviour (for example a supervised restart that waits for storage) | **Candidate change** | **Yes** | `affected` names the stages; V3 and V4 at least, V1 if the memory rule is touched |
| Any automatic recovery | Not proposed. If ever proposed: candidate change, and it may never release or resume a latch | n/a | n/a |

**Not done in this batch, deliberately:** the alert package itself and the V5 stage (a design only), and any candidate change. The two findings (monitor stays stopped; start budget exhausted) remain true of the candidate until a person decides otherwise; the design removes the silence, it does not change what the candidate does.

**Checks that stand and checks that do not.** V0 through V3 passed in qualifying runs at harness `9bc3197`; this batch does not invalidate them, and they were not re-run. V4 has not passed in a qualifying run. q3 (harness defect) and q3b (the monitor was not started before the release) stay recorded as FAILED. The V0 to V4 results come from different harness revisions; that limitation is recorded in the evidence table and is not removed by anything here.

## 2. Local test results (this batch, no machine involved)

* New tests, each of which **failed before its fix**: `tests/test_vtest_helpers.py` (15: a real helper survives its parent's SIGKILL before the fix, found and stopped after it; the scan matches only helper scripts by full path; the command line is read again before every signal; `run`, `archive`, `stop-helpers`, `teardown`; every writing command holds the run lock), `tests/test_vtest_recovery_order.py` (9: q3b's release rc 4 and resume rc 3 reproduced on the old order; monitor started and fresh heartbeat seen before `release`; no release without a fresh heartbeat; the finding row; a surviving monitor makes no finding; findings listed in `SUMMARY.json`).
* The whole vtest suite: **298 tests, 0 failures** (274 before this batch).
* The pin gate: green on a clean tree; the code under test is unchanged since the pin.

## 3. Proposed remaining validation session (one concrete proposal)

**Goal:** a qualifying V4 pass, the reboot checks, and a first alert drill, in that order, on one new disposable VM. This is **not** the full-broadcast rehearsal, which comes after (roadmap section 6).

**Before any VM (local work, one more batch, no machine):** build the alert package and the V5 stage with tests first, per `docs/ALERT_DESIGN_2026-11-12.md`. The session is not started until those tests pass and the owner has seen the results. If the owner prefers to validate V4 and the reboot checks first and V5 later, steps 1 to 5 below stand alone (a second, shorter session then covers V5 and the repeat of V3/V4 with the drop-ins).

**The session:** one VM of the same class as before (1 vCPU, 1 GiB, no swap, systemd, cgroup v2), the same pin and the same install procedure, the standing rules (spend limit, time-box, hard stop, evidence copied off with SHA-256 verification by the authenticated console, no public HTTP server, no credentials in chat or on the VM, production and the Spaces key untouched).

1. Setup, `pin-check`, `preflight`, `install` (the installer's first real use at the new harness revision). About 30 minutes.
2. **V4-only smoke run** (non-qualifying), to exercise the new helper launch path and the new recovery order on a real machine before a qualifying run is spent. About 40 minutes. Stop here if it fails.
3. **`q3c`**: qualifying V4. About 40 minutes. The monitor's exit during the fault is expected to appear as the finding row, not a failure.
4. **Clean reboot** and `q4-clean`, then **`q5-capture`, hard reset and `q5-hard`** (`docs/VTEST_EXECUTION_SCHEDULE.md`, reboot steps). About 35 minutes. (In the schedule `q4-clean` and `q5-*` are the reboot checks, not a full-length run.)
5. **V5 alert drill and the V3/V4 repeat with the drop-ins installed**, if the package is ready. Duration not yet known; it is a new stage.
6. Archive each run, copy it off, verify SHA-256, then destroy only that VM and verify cleanup.

**Estimate:** about 3 hours of runs plus exports if nothing fails; the previous session's archives for V4 and the reboot checks are small (tens of kilobytes), so console transfer is feasible between runs. A time-box of 8 hours and a spend limit of $2 are ample (the machine class costs well under a cent per hour at list price). The same discipline applies: set the hard stop before starting, qualifying runs end an hour before it, and the last hour is for evidence and cleanup.

**Stop rules:** stop at the first stage that does not pass; a harness defect is fixed locally with a failing test first and repeated; a candidate defect is a new pin. Do not weaken a criterion. Report each run separately.

**Decisions needed from the owner before the session:** the time-box and spending limit; whether to include V5 (needs the package built first); and, separately, the alert delivery channel (not needed for the disposable machine, which uses a local sink).

## 4. Independent of the above

The **separate-host restore** (host-loss recovery) does not depend on V4 or the alerts and can be scheduled on its own. The **real-web-server season-boundary rehearsal** and the **clean full-length broadcast** come after this validation line is finished (roadmap section 6).
