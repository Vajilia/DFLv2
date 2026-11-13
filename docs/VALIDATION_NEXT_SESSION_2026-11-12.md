# Remaining validation: what changed, what repeats, and the proposed session (revised 2026-11-12, evening)

Nothing here provisions, deploys or changes a credential. Candidate 4 (`205f891`) is unchanged and production is untouched. The owner's decisions of 2026-11-12: **B** for the q3b question (the harness follows the documented recovery order and the monitor's exit stays visible as a finding; q3b itself stays **FAILED**), the milestone order in `docs/LAUNCH_ROADMAP_EVIDENCE.md` section 6, and, in the evening batch, to build the alert package and V5 locally with tests first.

## 0. A correction to an earlier version of this document

The first version said that V0 to V3 "stand" because the helper-launch change was "not a judgment change". **That was wrong, and it is withdrawn.** `vtest.py affected --recorded 9bc3197` names V0 to V4 for the changes since the last qualifying runs, because `tools/vtest/common.py` is shared machinery (it launches the sampler, the clock probe and the loaders that every stage's measurements come from), and the change moved how those helpers are started (a kernel parent-death signal set in the child before it runs). The unit tests exercise that with real processes, but no real machine has run it. The retained V0 to V3 results are therefore **informational history, not qualifying results**, for the reasons in section 2. No criterion was changed.

## 1. Harness corrections versus candidate changes

A **harness correction** changes only `tools/vtest/` (and its tests): the harness revision changes, the pin does not, and the stages `vtest.py affected` names repeat. A **candidate change** touches the code under test (unit files, daemon, monitor, start gate, stop hook): it needs the owner's approval, a new pin, a `docs/CANDIDATE_HISTORY.md` entry and the stages `affected` names. The alert package is a third kind, **release configuration** (below).

| Change | Kind | New pin? | What repeats, and why |
|---|---|---|---|
| Helper processes die with the command that started them; `run` and `archive` refuse while one exists; `stop-helpers`; `teardown --yes` stops them (defect #4) (`26d0cdd`) | Harness, **shared plumbing** (`common.py`) | No | Every stage that uses a sampler, probe or loader: V0 to V5 (section 2). Not a judgment change in the sense that no criterion moved; **a behaviour change in how the measurements are taken**. |
| One runner at a time (`72e3d11`) | Harness, already in place | No | Nothing by itself (it was in the revision that produced the V0 to V3 results' successors; the structural test now fails if a writing command lacks the lock). |
| V4 recovery follows the documented order; the monitor's death is a finding row (`26d0cdd`); earlier V4 fixes `9bc3197`, `e224d3a` | Harness, V4's own steps | No | V4, then the reboot checks, which read the state V4 leaves. |
| **Alert package** (`alerts/`: drop-ins, alert unit, poll timer, `dfl_alert.py`) and its installer `install_alerts.py`; stage V5; the evidence exporter | **Release configuration** (not code under test), harness additions | **No**, as long as the candidate's unit files stay byte-identical (a test compares the code under test with the pin; the pin gate stays green) | The package is classified so a change to it names **V0, V2, V3, V4, V5** (V1 excluded, with the reason written in `classify_why`); V5 is new; the exporter measures nothing and repeats nothing. |
| Putting `OnFailure=` or other `StartLimit*` values into the candidate's own unit files | **Candidate change** | **Yes** | The stages `affected` names for unit-file changes: V0, V2, V3, V4, V5 at least. |
| Changing the monitor's exit-2 behaviour (for example a supervised restart that waits for storage) | **Candidate change** | **Yes** | V3, V4, V5 at least; V1 if the memory rule is touched. |
| Any automatic recovery | Not proposed. If ever proposed: candidate change, and it may never release or resume a latch | n/a | n/a |

## 2. Revised validation matrix: the scope follows behaviour

**Stance.** The next session repeats **every stage, V0 to V5, under one release configuration**: the pinned candidate, the alert package as hashed in `alerts/RELEASE_MANIFEST.json`, and one harness revision. Earlier results are kept as informational history. The reasons are about what changed in behaviour, stage by stage, not about convenience; where retaining a result would have been defensible, the cost of repeating it is stated so the owner can overrule.

| Stage | What it judges | What changed that it depends on | Earlier result | In the next session | Why not retain the earlier result |
|---|---|---|---|---|---|
| **V0** setup facts | users, permissions, applied sandbox, lifecycle probe | `common.py` (helper launch); the units on the machine now include the drop-ins; the record of effective settings is new | passed (`9bc3197`, qualifying) | repeat (about 7 min) | Cheap, and it is the stage that records what is in force; it must describe the machine the later stages ran on, drop-ins included. |
| **V1** memory rule under cache growth | the monitor does not trip and nothing is killed while the daemon's cgroup fills with clean cache | the sampler, probe and loaders (what V1's measurements are read from) are launched differently; the poll timer now runs on the machine | passed (`9bc3197`, qualifying) | repeat (about 61 min) | V1's verdict is read entirely from helper output, and the helper launch path is the thing that changed. The code under test did **not** change, which is why this is the one stage where retaining is arguable: it would save about an hour of an eight-hour session. Not retained, because the launch path is untried on a real kernel and V1 is where a helper that dies early would silently shorten the observation. |
| **V2** sustained pressure and OOM: what the monitor does and what systemd does | victim choice, latch, journal | helper launch; the poll timer and an alert process (about 15 MB when it runs) now exist beside the daemon during a real kernel OOM | passed | repeat (about 18 min) | The alert process is a new candidate victim and a new consumer of the machine's memory exactly when the kernel chooses a victim. |
| **V3** monitor dies or hangs; the daemon pauses itself | start gate, stop hook, stale heartbeat, the monitor's own start limit | helper launch; **the monitor's drop-in will fire the alert path during V3's monitor start-limit drill** | passed | repeat (about 25 min) | This is the interference check the design promised: the alert path must not change what V3 judges. |
| **V4** latch persistence, release, immutable guard, then reboot checks | the documented recovery order, latch preservation, the run marker through restarts, clean reboot and hard reset | helper launch; V4's recovery order corrected; the immutable-guard fault now also kills the monitor with a drop-in present | **never passed** (q3 harness defect, q3b recovery order; both stay FAILED) | `q3c`, then `q4-clean`, `q5-capture`, `q5-hard` (about 38 + 35 min) | n/a |
| **V5** alert drill (new) | detection without the monitor, record, one message, delivery attempted vs received, never recovers, site vs simulation | the whole package | none | first run | n/a |

**Worst case, stated:** if the owner chose to retain V0 to V3 anyway, the session would save about 1 h 50 min of the 7 h 20 min; but the qualifying set would then mix harness revisions and release configurations, which is the limitation already recorded in the evidence table for V0 to V4, and V3 in particular would not have run with the alert path present. The recommendation is to repeat.

## 3. Local test results (this batch; no machine involved)

Every new behaviour had a test that failed before it passed.

* `tests/test_dfl_alert.py`: **40** (detection, record-first, delivery vs receipt, retry and dedupe, never-recovers source and runtime checks, lock, no-channel behaviour). One test written earlier was **wrong and replaced**: it asserted that the tool refuses to run without a delivery command; that would have left an unconfigured host both silent and unrecorded. Six mutants of the tool were caught earlier; the final file is the tested file.
* `tests/test_alert_release_config.py`: **18** (deterministic rendering for both name sets, the drop-in adds exactly one directive, the alert unit cannot alert about itself, validation set equals production set up to names, manifest current and tamper-evident, env template holds no value, classification of `alerts/`, the pin gate stays green, offline `systemd-analyze verify` of the three units for both name sets).
* `tests/test_vtest_v5.py` (V5 judgments and scenario glue, tested on a scripted machine), `tests/test_vtest_install_alerts.py` (the installer end to end on a relocated root; teardown removes drop-ins), `tests/test_vtest_export_evidence.py` (AWS's published signature vector, a fake object store: private-only, no listing or deletion, no credential printed or written).
* **Mutation check of V5's judgments:** 10 mutants (a judgment ignoring the failed unit's state, the duplicate check, the message count, the time limit, a "received" claim, a failed hand-off, a changed latch, a new release record, an ack, a false alarm): **all caught**.
* Expected stage-set changes in older tests (V5 added; the daemon/monitor changes now name V5) were updated, **never loosened**: every expectation now names the same stages plus V5.
* Offline: `systemd-analyze verify` accepts the three units. **Not tested anywhere:** any of this on a real systemd (that is V5's job), the exporter against a real object store.
* Totals (run as `python3 -m unittest discover -s tests -p <pattern>`): harness group `test_vtest*.py` **375 OK**; `test_dfl_alert.py` **40 OK**; `test_alert_release_config.py` **18 OK**. The repo-wide suite was started separately and its result is reported separately. One V5 row was softened with a failing test first: in the exit-2 drill, a monitor that failed with a status other than 2 now still tests the alert and is recorded for review (a finding about the candidate), while a monitor that did not fail stays invalid (not tested).

## 4. Proposed session (one concrete proposal; not started)

**Goal:** a qualifying set for the whole line, V0 to V5, on one new disposable VM, under the release configuration of section 2. It is **not** the full-broadcast rehearsal.

**Machine and rules:** the same class as before (1 vCPU, 1 GiB, no swap, systemd, cgroup v2, ext4 or xfs), the same pin and install procedure plus `install-alerts`; the standing rules (spend limit, time-box, hard stop, evidence off the machine with SHA-256 verification, no public HTTP server, no credentials in chat or on the machine beyond the evidence-bucket key the owner types at a hidden prompt, production and the working Spaces key untouched).

**Order matters in two places.** (1) The reboot checks must follow `q3c` immediately: V5 and every stage's baseline release any latch, which would destroy the state the reboot checks read. (2) The poll timer is started, not enabled: after a reboot it is not running, so `install-alerts` is run again (it re-verifies the manifest) before V5.

| Clock | What | About |
|---|---|---|
| 0:00 | **Setup**: `pin-check`, `preflight`, `install`, **`install-alerts`**, read the effective-settings record, one export of the setup record to the private bucket as a first real test of the transfer | 35 min |
| 0:35 | **Smoke (non-qualifying)**: V0, V4 and V5 with short settings, the three things never run on a real machine. **Stop at the first failure.** Export | 1 h 40 min |
| 2:15 | `q1`: V0 + V1. Export | 70 min |
| 3:25 | `q2`: V2 + V3. Export | 50 min |
| 4:15 | `q3c`: V4. Export (before the reboot) | 45 min |
| 5:00 | clean reboot, `q4-clean`. Export | 20 min |
| 5:20 | `q5-capture`, hard reset, `q5-hard`. Export | 25 min |
| 5:45 | `install-alerts` again; `q6`: V5. Export | 65 min |
| 6:50 | Verify every archive's SHA-256 with the receiver, the owner accepts or rejects, destroy only this machine and verify cleanup | 30 min |
| **7:20** | End if nothing fails | |

**Time-box proposal: 9 hours**, so a failed stage can be diagnosed and the cleanup is not rushed (qualifying runs must end an hour before the hard stop). **Spend limit: $2** (the machine class is well under a cent an hour at list price; the limit is a guard, not a forecast). Per-stage times are the harness's own estimates from `vtest.py plan` (V0 7, V1 61, V2 18, V3 25, V4 38, V5 56 minutes, qualifying) plus about 5 minutes per export; V5's figure is an upper bound built from its own time limits and has never been measured.

**Stop rules:** stop at the first stage that does not pass; a harness defect is fixed locally with a failing test first and the named stages repeated; a candidate defect is a new pin; an **invalid** check (for example, the monitor did not exit 2 in V5's exit-2 drill) is reported as not tested and is never counted as a pass. Do not weaken a criterion. Report each run separately. Keep every earlier failure (q3, q3b, smoke-4) recorded as it is.

**Alternative:** two sessions (V0 to V4 and the reboot checks; then V5), which halves the day's length but doubles the setup and breaks "one install". Not recommended; the owner may choose it.

## 5. Decisions needed from the owner before the session

1. **The time-box and spend limit** (proposed: 9 hours, $2), and whether to repeat all of V0 to V5 (recommended) or to retain V0 to V3 with the consequences in section 2.
2. **The evidence transfer** (`docs/EVIDENCE_TRANSFER_PRIVATE.md`): create the private evidence Space and a key limited to it; confirm the provider offers such keys. If it does not, say so before the session: the fallback is worse.
3. **The delivery channel** (`docs/ALERT_DESIGN_2026-11-12.md`): not needed for the disposable machine (it uses a file channel), but needed before production.

## 6. Independent of the above

The **separate-host restore** (host-loss recovery) does not depend on V4 or the alerts and can be scheduled on its own. The **real-web-server season-boundary rehearsal** and the **clean full-length broadcast** come after this validation line is finished (roadmap section 6).
