# DFL project state (canonical)

This is the one document that says where the project stands. Other documents are history or reference. Sanitized: no hosts, machine ids, credentials or hashes of private evidence. Updated at the end of each milestone.

## Candidate 5 package (2026-11-22, authorized 08:17 PST; complete)

Candidate 4 (`205f891`) plus the two confirmed baseline engine fixes, as **candidate 5 (`512dd1e`)**, pinned on a preparation lineage. Work record: `docs/BASELINE_DEFECTS_2026-11-22.md`; history: `docs/CANDIDATE_HISTORY.md`. The Sacramento cap and upgrade-policy features are **deferred, not rejected**, unmerged and not in candidate 5 (branch `feature/sacramento-cap-2026-11-22`). Nothing has been run under systemd for candidate 5. Nothing in candidate 4's validation record is a result for candidate 5.

* **Fixes.** (1) Founding Day: a drawn rookie round is lowered only as far as needed for the club to complete **all** of its 53 roster and 16 squad signings within the cap (the ledger's own cap number with the signings still to come, top-N rule included); the contract is always an ordinary scale contract of some round. (2) Free agency no longer ends a club's whole period when one drawn position has no candidate; the existing roster, budget, ceiling and 40-pass limits are unchanged and the loop is bounded.
* **Frequencies and compatibility are sample results.** The founding failure was seen on 1 of 60 screened test seeds (7771400-7771459); one in 60 is too few to estimate a true rate. Founding digests of the 59 screened seeds that founded on `4a1f26e` are identical on the candidate (the failing seed now founds). That is a check on specified seeds, not a proof for every seed. The free-agency fix changes league evolution after the first free agency (a sample observation).
* **Not changed:** signings below an incumbent (investigated; judged not a defect), scouting accuracy, negotiation rules, camp limits, game outcomes, the pin gate, `pin-check`, acceptance criteria, `tools/vtest` logic, `alerts`.
* **Derived values, updated through their mechanisms (not papered over):** `tools/vtest/PIN` (new candidate, owner's instruction of 2026-11-22 08:17 PST) and its approval test; the engine code fingerprint constant in `tests/test_replay_tools.py` (the deployed release's `efaa7167...9973` is kept there as a record, the candidate's `059667d2...745b` is asserted). `alerts/RELEASE_MANIFEST.json` is not derived from the engine and still matches (`release_config.py manifest`).
* **Production implication (note only; production not touched).** The engine fingerprint is no longer the deployed release's, so a league written by the deployed engine is refused by candidate 5 until carried over with `persist.upgrade` (`league_store.py upgrade OLD NEW --run-originating-code --archive ARCHIVE_COPY`, `docs/DEV_LEAGUE_RUNBOOK.md`). Deploying candidate 5 to production is a separate decision, not part of this package.
* **Checks run (final code revision `512dd1e`, tree `1fbbfeb`).** New `tests/test_baseline_defects.py`: 9 tests, red on `4a1f26e`, green on the fix (worst case: every founding draw forced to round 1 still founds all 48 clubs at 53 + 16 within the cap; lowered rounds are exact scale contracts; free-agency limits with the empty position always drawn first). Full local run, every test file one at a time (118 files, about 1,900 tests): all pass; skipped explicitly: three real-engine tests (`test_backup_resume_real`, `test_supervisor_real`, `test_multiseason_engine`, they need a finished first-season root that is not present) and one case in `test_site_deploy` (needs root or a clean tree). `tools/validate.py`: 75 passed, 0 failed. `pin-check`: OK. The other CI steps (30-season run, soaks, restore soak, spatial smoke) were run by GitHub CI only, not locally (two CPUs shared with the test run). The four pin-gate tests pass after the pin and fingerprint updates. CI results are recorded below.

## Real-system execution handoff (refreshed 2026-11-22 for candidate 5)

For the provisioning session. Everything is prepared; this session buys nothing and creates no machine. Code under test: candidate 5 `512dd1e`. Run from the head of `prep/candidate-5-2026-11-22` (see Revisions). Limits unchanged from the owner's correction of 01:17 PST: one new machine, spend ceiling $2, no snapshots or backups, testing stops at T0+8 h, verified export and destruction by T0+9 h, evidence by private SSH/SCP, one use, production untouched, no credentials entered by this session. The plan below is the one to follow; its S0 checkout now names the preparation branch head. Every old pass is void for candidate 5, so a Phase A pass is the first evidence for it.

**Recommended next action:** the owner authorizes the provisioning session to create the one machine and run Phase A (the narrow V5 smoke) of the plan below against candidate 5, then Phase B only if Phase A passes and time allows.

## Milestone 1 clock

| | |
|---|---|
| Authorized | 2026-11-22 01:06 PST (09:06:56Z), by the owner's Milestone 1 instruction |
| Allowance | 12 hours of active agent work, CI waiting excluded |
| Used at last update (2026-11-22 09:20Z) | about 0.3 h (wall-clock since the start; no VM time) |
| Remaining | about 11.7 h. Milestone 1 stops here by instruction; Milestone 2 is not authorized |

## Revisions and release configuration

| Part | Value |
|---|---|
| Candidate (code under test) | `512dd1eedc62d013f184377b81b198f53224e7ee` (candidate 5, pinned in `tools/vtest/PIN`), branch `integration/launch-candidate-5-2026-11-22`. Candidate 4 `205f891` stays recorded in `docs/CANDIDATE_HISTORY.md`, unchanged |
| Harness | Logic `5a97dad140c7ea4f3c9007c623ef2553a5a31691` (CI run 44514172644 passed), unchanged since: `tools/vtest` differs from it only in `PIN`. The commit a run is made from is the head of `prep/candidate-5-2026-11-22` (tests and docs fixed at `1fbbfebf89f2a7c012018b1acf8ffbe8d89271d1`; later docs-only commits are equally valid; run `pin-check` first) |
| Alert package | `alerts/RELEASE_MANIFEST.json` (git blob `1c0920b6d9637a69c6f4ffb9506437f4da68cfec`), unchanged and matching |
| Production | untouched. Not accessed in Milestone 1 |

`vtest.py affected --recorded 5a97dad` names **V0 to V5** for repeat (the pin moved, so every stage is affected) and `--recorded 9bc3197` does too. One new, complete run under this single release configuration (candidate 5, this harness, this alert package) is needed; old passes do not carry over.

## Validation results (nothing has been re-run in Milestone 1; every result below is for candidate 4 or earlier; candidate 5 has no result)

| Check | Status | Notes |
|---|---|---|
| V0-V3 qualifying (harness `9bc3197`, without the alert package) | PASSED, historical | Superseded by the release configuration above |
| V4 qualifying | **FAILED** (q3b), stays failed | Smoke-5 passed but is non-qualifying |
| V4 reboot checks (q4-clean, q5-capture, q5-hard) | **NOT RUN** | |
| V5 smoke, harness `aef65af` (2026-11-16/17) | **FAILED**, stays failed | Latch attribution not preserved |
| V5 smoke, harness `aaddb43` (2026-11-19) | **FAILED**, stays failed | Evidence collection fault (journal query); fixed in `5a97dad` |
| V5 on `5a97dad` | **NOT RUN** | Next step; see the plan |
| Any run under the full release configuration | **NOT RUN** | |

Evidence is private and never in GitHub. Raw archives for the failed runs are held by the owner's private export route; the sanitized records are `docs/V5_LATCH_ATTRIBUTION_2026-11-17.md` and `docs/LAUNCH_ROADMAP_EVIDENCE.md`.

## What is proven and what is not

Proven by tests and CI (not by a real system): the V5 evidence collector no longer sends journal wildcards, tells a failed query from an empty one, and cannot report a pass with missing attribution; mutation checks done.
Proven on real systems, historically: V0-V3 qualifying (see above) and the evidence export path.
Not proven: that the candidate with the alert package passes V4 and V5 on a real machine; reboot and hard-reset survival of the V4 state; the daemon's own stale-heartbeat trip preserved and attributed on a real host; V5's other three hold scenarios (never reached in the last real run); that `journalctl -u <instance>` returns instance entries as the sandbox reproduction assumes (earlier real runs agree, the sandbox cannot show it).

## The one blocker

The purchase of the validation machine is made by the provisioning session (ChatGPT, through its DigitalOcean connection), not by this session. Everything else is prepared. The limits below were corrected by the owner on 2026-11-22 01:17 PST.

## Validation plan (re-read against the actual CLI, preflight and stage code on 2026-11-22)

**Machine.** One new disposable machine, 1 vCPU, 1 GiB, Ubuntu 24.04, ext4, swap off, at least 5 GiB free disk (preflight: root, marker file, no production paths or units, systemd 243 or later as PID 1, cgroup v2 read-write with the memory controller, swap off, kernel 5.4 or later (5.19 for the reclaim probes), `/usr/bin/python3` 3.8 or later, `setpriv` and `strace` (hard), `chattr` and `lsattr` (V4 needs them), user `dfl-vtest`). Packages: `git python3 strace util-linux e2fsprogs`. Nothing else is installed. The repository is public; the machine clones `https://github.com/Vajilia/DFLv2`.

**Clock (settled).** All times count from the provider's `created_at` for the machine (T0). All testing stops at **T0+8 h**. Verified export and destruction finish by **T0+9 h**. Before each stage or run, start it only if `now + estimate x 1.25` is at or before T0+8 h (estimates below). If a stage is still running at T0+8 h, stop the runner (`stop-helpers`), archive what exists, mark that stage INCOMPLETE (no verdict) and go to export. No extension, no replacement machine.

**Evidence route (settled): private SSH/SCP to the Chromebook; no Spaces key.** The machine holds no credential. The Chromebook (or ChatGPT's Desktop Commander session on it) pulls each archive over SSH as root with `scp root@<vm>:/srv/dfl-vtest/export/evidence-<run>.tar.gz <dir>/` and computes `sha256sum` itself. The check is against the digest `vtest.py archive` printed on the machine (taken from a separate `sha256sum` on the machine) and the digest in `/srv/dfl-vtest/state/archive-<run>.json`. The Chromebook then reads `EVIDENCE_MANIFEST.json` out of the archive and requires `"complete": true`. An archive counts as exported only after both checks. Only if SSH/SCP proves unavailable does the Spaces tool (`tools/vtest/export_evidence.py`, bucket-scoped key, hidden prompt) become the fallback. Archives stay private; nothing is committed.

**Run ids** use the prefix `m1-` so they cannot be confused with the historical `q3b`, `smoke-5` and similar. The reboot stages do not depend on a particular id.

**S0 setup (about 35 min).**
```
sudo touch /etc/dfl-vtest-disposable
sudo apt-get install -y git python3 strace util-linux e2fsprogs
sudo useradd --system --user-group --no-create-home --shell /usr/sbin/nologin dfl-vtest
git clone https://github.com/Vajilia/DFLv2 && cd DFLv2
git checkout prep/candidate-5-2026-11-22         # the head; run pin-check next (candidate 5 is its ancestor; harness logic is 5a97dad's)
python3 tools/vtest/vtest.py pin-check            # must pass
sudo python3 tools/vtest/vtest.py preflight       # must end READY
sudo python3 tools/vtest/vtest.py plan
sudo python3 tools/vtest/vtest.py install         # starts nothing
sudo python3 tools/vtest/vtest.py install-alerts  # checks alerts/RELEASE_MANIFEST.json before writing; records effective settings
```
(Any docs-only successor of `1fbbfeb` on that branch is equally valid; `pin-check` must pass.) Read `STATE/alerts_install.json` and the effective settings before running anything.

**Phase A: narrow V5 smoke (non-qualifying).** `sudo python3 tools/vtest/vtest.py run --stages V5 --smoke --run-id m1-v5-smoke` (about 0.9 h), then `archive --run-id m1-v5-smoke` (exit 0 required), export and verify. Pass means the stage passes, the manifest says `complete: true`, and these exist in the archive: `V5/attribution_{monitor_stopped,daemon_start_limit,monitor_exit2,delivery_failure}.json`, `V5/alert_sandbox.json`, `V5/alert_journal_timestamped.txt`, `V5/alert_journal_status.json`, `V5/alert_journal.txt`. Any failure, missing item or exit other than 0: export, destroy, stop for review.

**Phase B: only if Phase A passed and the next stage fits before T0+8 h.** Order matters: the reboot checks must follow `m1-q3` immediately (V5 and every stage's baseline release any latch, which would destroy the state the reboot is judged against). The poll timer is started, not enabled, so `install-alerts` is run again before the final V5 only, not between the reboot stages.

| Run | Command (all `sudo python3 tools/vtest/vtest.py ...`) | Estimate |
|---|---|---|
| m1-q1 | `run --stages V0,V1 --run-id m1-q1`, then archive, export | 66 min + export |
| m1-q2 | `run --stages V2,V3 --run-id m1-q2`, archive, export | 42 min + export |
| m1-q3 | `run --stages V4 --run-id m1-q3`, archive, export (before the reboot) | 36 min + export |
| m1-q4-clean | `sudo reboot`; start nothing; `run --stages V4 --after-reboot --run-id m1-q4-clean`, archive, export | about 15 min |
| m1-q5-capture | `run --stages V4 --capture-reboot-state --run-id m1-q5-capture` (units running and held) | about 20 min with q5-hard |
| m1-q5-hard | provider hard reset (or `echo b > /proc/sysrq-trigger`); start nothing; `run --stages V4 --after-hard-reset --run-id m1-q5-hard`, archive, export | |
| m1-q6 | `install-alerts` again, then `run --stages V5 --run-id m1-q6`, archive, export | 54 min + export |

Each export is about 10 min. Expected timeline with nothing failing: Phase A ends near T0+1:40, Phase B near T0+6:40, leaving about 1 h 20 min before the testing cutoff. After the last export is verified, destroy only this machine. If a run reports `DO NOT CLEAN UP THE HOST` (archive exit 3), export what exists anyway, then destroy; execution ends for review. Failed runs are not retried on the same machine.

**Stops.** The first failed or incomplete stage ends the session after export and destruction. A candidate or alert-release change is never made in the session. Production is not touched; no machine other than the new one is touched.

## Execution authorization (as corrected by the owner, 2026-11-22 01:17 PST)

One new machine as above, created by the provisioning session; spend ceiling $2, no snapshots or backups (the machine costs about a cent an hour); testing cutoff T0+8 h; verified export and destruction by T0+9 h; evidence by private SSH/SCP to the Chromebook; one use.

## Public feeds: inventory (corrected by the owner, 2026-11-22 01:17 PST)

Classification is from the repository's documents and code plus the owner's correction. Nothing was re-checked on the live site (no production access); the last read-only observation of the preview is recorded as 2026-11-06/11-11 at league event 188.

**Already published by the exporter and specified (format v1):**
- `feed/latest.json`, release manifest, season, calendar, notices, teams, schedule, standings, playoffs, history, venues, rules, 48 rosters (`docs/PUBLIC_FEED_FORMAT.md`)
- `status.json` (`dfl-feed-status`), `playback/index.json` and per-game playback files (`docs/PUBLIC_PLAYBACK_FORMAT.md`)
- Broadcast: `clock.json`, `schedule.json`, `live/<game>/…`, `replays/index.json`, and `honors/index.json` (empty until the Bowl ceremony) (`docs/BROADCAST_FRONTEND_CONTRACT.md`)

**Already exist in the separate website preview (not in this repository):** basic player profiles and team/player search, built from the published roster facts. Career statistics and integration of this work into the main repository are separate, still-open work.

**Derivable from existing records (exporter work, no engine change):**
- Box scores, team game stats, per-player box lines, injuries, penalties, from the game archive (`docs/ARCHIVE.md`)
- Season summaries and standings history, from the archive and history
- Career statistics, season leaderboards and the stats side of player pages, by aggregating the per-player box lines

**Requires new engine or data work, or an owner decision:**
- Analytics (EPA-based scores) and Hall of Fame pages beyond the honors feed: engine recording and definitions were built in Edition 8.30 (`docs/ANALYTICS_AND_HALL.md`); a public contract does not exist yet
- Draft, transactions and awards pages: whether the engine records enough to publish is **unverified**; check first in Milestone 2
- Bounded narrative coverage (in the agreed project scope): nothing exists yet; new work, to be defined with measurable completion criteria before implementation
- Team and player photos or logos: assets, not feeds

## Scope: initial limited release versus project completion

**Initial limited release (recommended first launch).** Schedule and results; live and replay broadcast; standings and playoffs; clubs and rosters; the player profiles and team/player search already built in the preview (integrated into the main repository); history; notices and status; honors at the Bowl. The existing site preview stays the visual direction. These exist and need integration and validation, not invention.

**Project completion (agreed scope, after the initial release).** Everything above plus: career statistics, leaderboards and box-score pages (exporter work); analytics and Hall pages; draft, transactions and awards where the engine supports them; bounded narrative coverage; photos and logos. Completion is not claimed until each item meets stated criteria.

Not in scope for either: changing league rules, canon or the viewer experience.

## Milestone 2 work package (proposed, not authorized)

Limits: 12 hours of active agent work; at most one new validation machine under its own separate authorization; production untouched; criteria defined before implementation.
1. Separate-machine recovery drill and multi-season continuity run on a disposable machine, using the existing bucket-scoped recovery route (requires its own spend authorization).
2. Written factual feed contracts for box scores, career statistics, leaderboards and player-page statistics, derived from the archive, with fixture tests (no engine change); verify the draft/transactions/awards question from records; plan the integration of the preview's profiles and search into the main repository.
3. Bounded narrative layer: first define measurable completion criteria for the owner to approve, then implement only against them.
Completion: each item passes its stated criteria with evidence, or is reported as not met.

## Rules in force

Failed runs stay failed. Candidate, `tools/vtest/PIN` and acceptance criteria are not changed without the owner. Evidence stays private. Production is never touched by disposable-host testing.
