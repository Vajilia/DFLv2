# DFL project state (canonical)

This is the one document that says where the project stands. Other documents are history or reference. Sanitized: no hosts, machine ids, credentials or hashes of private evidence. Updated at the end of each milestone.

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
git checkout 5a97dad140c7ea4f3c9007c623ef2553a5a31691
python3 tools/vtest/vtest.py pin-check            # must pass
sudo python3 tools/vtest/vtest.py preflight       # must end READY
sudo python3 tools/vtest/vtest.py plan
sudo python3 tools/vtest/vtest.py install         # starts nothing
sudo python3 tools/vtest/vtest.py install-alerts  # checks alerts/RELEASE_MANIFEST.json before writing; records effective settings
```
(`fa6ad15` is a docs-only successor of `5a97dad` and is equally valid; `5a97dad` is the named harness revision.) Read `STATE/alerts_install.json` and the effective settings before running anything.

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
