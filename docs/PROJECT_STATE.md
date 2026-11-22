# DFL project state (canonical)

This is the one document that says where the project stands. Other documents are history or reference. Sanitized: no hosts, machine ids, credentials or hashes of private evidence. Updated at the end of each milestone.

## Milestone 1 clock

| | |
|---|---|
| Authorized | 2026-11-22 01:06 PST (09:06:56Z), by the owner's Milestone 1 instruction |
| Allowance | 12 hours of active agent work, CI waiting excluded |
| Used at last update (2026-11-22 09:11Z) | about 0.1 h (wall-clock since the start; no VM time) |
| Remaining | about 11.9 h. Milestone 1 stops here by instruction; Milestone 2 is not authorized |

## Revisions and release configuration

| Part | Value |
|---|---|
| Candidate (code under test) | `205f891fc58eeed5330b36493523be1dc6792859` (candidate 4, pinned in `tools/vtest/PIN`; unchanged) |
| Alert package | as hashed in `alerts/RELEASE_MANIFEST.json` (unchanged) |
| Harness | `5a97dad140c7ea4f3c9007c623ef2553a5a31691` on branch `fix/v5-alert-journal-collection-2026-11-19` (CI run 44514172644 passed). `pin-check` passes at this revision |
| Production | untouched. Not accessed in Milestone 1 |

`vtest.py affected --recorded 9bc3197` (the harness of the last qualifying V0-V3 passes) names **V0, V1, V2, V3, V4 and V5** for repeat: the alert package is part of the release configuration and V4 and V5 have never passed in a qualifying run. So one new, complete run under this single release configuration is needed; old passes do not carry over.

## Validation results (nothing has been re-run in Milestone 1)

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

No paid validation machine is authorized now (the earlier one-session authorization was used). The plan below is complete; the only missing input is the authorization in "The single request".

## Validation plan (from the real CLI and runbook)

Settings: 1 vCPU, 1 GiB, swap off, at least 5 GiB free disk, ext4 or xfs, kernel 5.19 or later, systemd 243 or later, Ubuntu 24.04. One new machine, used for nothing else. Production is never a target.

**S0 setup (about 35 min).** `touch /etc/dfl-vtest-disposable`; install `git python3 strace util-linux e2fsprogs`; create user `dfl-vtest`; clone and check out the harness revision `5a97dad`; then `vtest.py pin-check` (must pass), `preflight` (must say READY), `plan`, `install`, `install-alerts`. The harness starts no unit.

**Phase A: narrow V5 smoke (non-qualifying).** `vtest.py run --stages V5 --smoke --run-id v5-smoke-1` (about 0.9 h). Then `archive --run-id v5-smoke-1` (exit 0 required), export to the private evidence bucket, verify SHA-256 off the machine. Pass means the stage passes with `complete: true` in the manifest and all of these present: `V5/attribution_{monitor_stopped,daemon_start_limit,monitor_exit2,delivery_failure}.json`, `V5/alert_sandbox.json`, `V5/alert_journal_timestamped.txt`, `V5/alert_journal_status.json`, `V5/alert_journal.txt`. Stop on any failure: export, destroy, report.

**Phase B: qualifying set, only if Phase A passes and the window permits** (one install, one harness revision):

| Run | Command | About |
|---|---|---|
| q1 | `run --stages V0,V1 --run-id q1` | 70 min, then archive and export |
| q2 | `run --stages V2,V3 --run-id q2` | 45 min, then archive and export |
| q3 | `run --stages V4 --run-id q3` | 40 min, archive and export before the reboot |
| q4-clean | `sudo reboot`; start nothing; `install-alerts`; `run --stages V4 --after-reboot --run-id q4-clean` | 15 min |
| q5-capture | `run --stages V4 --capture-reboot-state --run-id q5-capture` | with q5-hard, 20 min |
| q5-hard | provider hard reset (or sysrq `b`); start nothing; `install-alerts`; `run --stages V4 --after-hard-reset --run-id q5-hard` | |
| q6 | `run --stages V5 --run-id q6` | 0.9 h, then archive and export |

Estimated total: Phase A about 1.8 h; Phase B about 4.6 h; about 6.4 h with nothing failing, 7.4 h with margin. Stop rules: the first stage that fails ends execution; archive what exists, verify off-host, destroy the machine, report. No automatic retry, no replacement machine, no extension.

## The single request (what the owner is asked to approve once)

- One new disposable machine as above, created by the connected provisioning tool, not by this session.
- Spend ceiling $2 for the machine (about a cent an hour, so under $0.10 for the full window), no snapshots or backups. The evidence bucket and its key are the existing private route; the key is revoked after verification.
- Lifetime: 9 hours from creation. No stage starts after hour 8. Verified export and destruction by hour 9. No extension.
- Valid for one use within 7 days of approval.
- On a failed run: evidence exported and verified, machine destroyed, execution ends for review.

## Public feeds: inventory

Classification is from the repository's documents and code. Nothing was re-checked on the live site (no production access); the last read-only observation of the preview is recorded as 2026-11-06/11-11 at league event 188.

**Already published by the exporter and specified (format v1):**
- `feed/latest.json`, release manifest, season, calendar, notices, teams, schedule, standings, playoffs, history, venues, rules, 48 rosters (`docs/PUBLIC_FEED_FORMAT.md`)
- `status.json` (`dfl-feed-status`), `playback/index.json` and per-game playback files (`docs/PUBLIC_PLAYBACK_FORMAT.md`)
- Broadcast: `clock.json`, `schedule.json`, `live/<game>/…`, `replays/index.json`, and `honors/index.json` (empty until the Bowl ceremony) (`docs/BROADCAST_FRONTEND_CONTRACT.md`)

**Derivable from existing records (exporter work, no engine change):**
- Box scores, team game stats, per-player box lines, injuries, penalties, from the game archive (`docs/ARCHIVE.md`)
- Season summaries and standings history, from the archive and history
- Season leaderboards and player pages, by aggregating the per-player box lines

**Requires new engine or data work, or an owner decision:**
- Analytics (EPA-based scores) and the Hall of Fame pages beyond the honors feed: engine recording and definitions were built in Edition 8.30 (`docs/ANALYTICS_AND_HALL.md`); a public contract does not exist yet
- Draft, transactions and awards pages: whether the engine records enough to publish is **unverified**; check first in Milestone 2
- Narrative publishing (recaps, stories): nothing exists; new work
- Team and player photos or logos: assets, not feeds

## Recommended first-launch scope

In: schedule and results; live and replay broadcast of games; standings and playoffs; clubs and rosters; history; notices and status; honors at the Bowl; the existing site preview as the approved visual direction. These feeds exist and need integration and validation, not invention.
Can wait: leaderboards and player pages (exporter work, after launch), box-score pages (derivable), analytics and Hall pages, draft/transactions/awards, narrative layer, photos and logos.
Not in scope: changing league rules, canon or the viewer experience.

## Milestone 2 work package (proposed, not authorized)

Limits: 12 hours of active agent work; at most one new validation machine under its own separate authorization; production untouched; criteria defined before implementation.
1. Separate-machine recovery drill and multi-season continuity run on a disposable machine, using the existing bucket-scoped recovery route (requires its own spend authorization).
2. Written factual feed contracts for box scores, leaderboards and player pages, derived from the archive, with fixture tests (no engine change); verify the draft/transactions/awards question from records.
3. Bounded narrative layer: first define measurable completion criteria for the owner to approve, then implement only against them.
Completion: each item passes its stated criteria with evidence, or is reported as not met.

## Rules in force

Failed runs stay failed. Candidate, `tools/vtest/PIN` and acceptance criteria are not changed without the owner. Evidence stays private. Production is never touched by disposable-host testing.
