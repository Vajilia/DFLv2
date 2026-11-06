# Completion roadmap reconciled with the code (2026-11-06) and the next implementation itinerary

Written from the repository (branches below) and the production and rehearsal records in `docs/COMBINED_RELEASE_PLAN.md`, `docs/BROADCAST_*.md`, `docs/STAGING_DEPLOYMENT_RECORD_20261031.md` and `docs/BROADCAST_REHEARSAL.md`, plus the state read on the Droplet on 2026-11-06 (read-only). **No percentages: a line is either true, with its evidence, or it is listed as open.** `PROJECT_COMPLETION_PLAN.md` (2026-10-29), `BETA_STATUS.md` and `docs/BUILD_STATUS.md` pre-date most of this and are partly superseded; where they disagree with this file, the code and the records cited here win. Nothing below was deployed, merged or started by this batch.

**Status line (updated 2026-11-06, after the owner's 01:02 PST message): the real-time rehearsal is IN PROGRESS** on the rehearsal host, started by the owner with the installed package `c28ca13696` (both services active, the clock advancing, monitor heartbeats `"ok": true`, safety deadline `2026-11-08T14:18:10Z`: all as reported by the owner; **not verified by me, and deliberately not touched** - no restart, rebuild, replacement or other disturbance was made or is planned). **Multi-season real-time verification remains pending** (M5): the rehearsal exercises one season only; the installed package does not contain the continuity code. Production is stopped at event 188 and was not touched.

## 1. Pins

| What | Branch | Full commit |
|---|---|---|
| `main` (stale; nothing of the broadcast is merged) | `main` | `9c9254f3d11967e7d520db92ee98398f590f405b` |
| Engine and tools on the production server (not touched) | `release/engine-memo-2026-11-02` | `7e59cbbaea23b0de353545c64a6f5c39cffe774d` |
| Replay files, Caddy replay rules, exporter (deployed 2026-11-04) | `release/replay-tools-2026-11-03` | `69700562fdfe9cfbed4ea15111ba66af1fbb039d` (the branch tip is later: `b0f14bd9715d0eb08d6dc19adbb00ebfcbd0e05b`) |
| Website on production (deployed 2026-11-04) | `frontend/replay-integration` | `8ebe676648e239b693371c5e95dcfbc3c8f8ea11` (branch tip `e4c8d32a61f082afa2b97c74835fa8d4a1a07cce`) |
| Watch now frontend and 48-hour timetable | `frontend/watch-now-48h` | `ece0f7e71fc38c9ec76a85bf7ad9b5b7a5e6d1d2` |
| Broadcast backend (builder, daemon, Honors) | `backend/broadcast-integration-2026-11-04` | `656a61f18877f7d6217f3fd959938ed2b82ace59` |
| Frontend Honors and status, release label (in the rehearsal package's history) | `frontend/broadcast-honors-2026-11-05`, `frontend/broadcast-release-label-2026-11-05` | `acac7fce48f40c8d8ab6e13f95030b8324da042a`, `971fa5b6cd4455bab2c70063913bc349769cbb6e` |
| Rehearsal tooling, guard, permission-safe installer, quarantine | `backend/rehearsal-package-2026-11-05` | **installed on the rehearsal host**: `c28ca13696b0d8607fe92e7509cc2edbbdc07738` |
| Multi-season continuity, storage inventory, this document | `backend/multiseason-continuity-2026-11-06` | see the final handoff (the branch tip) |

Dependencies of the stack: continuity builds on the rehearsal branch, which builds on the broadcast backend, status-pause (`1dde945`), the Honors view and the label branches, on the Watch-now frontend. The deployed site (`8ebe676`) is **older than** all of the broadcast frontend: nothing of Watch now or Honors is on production.

## 2. What is true now

### Implemented, tested, and deployed to production (unchanged by this batch)
* The league engine and ledger as of `7e59cbb` (League stopped at event 188 = four seasons, feed release `e00188-cf545fbf7b`; verified unchanged, read-only, on the morning of 2026-11-06 (about 08:20 UTC) by the `cap_state.py` fingerprint and `verify_deployment.py`, after the rehearsal install of 2026-11-05 17:47 UTC).
* The public site `8ebe676` and the 1,600-game replay library with its Caddy cache rules (`6970056`).

### Implemented and tested, **deployed only to the rehearsal host** (`rehearsal.diamondfootballleague.com`)
* The broadcast package at `c28ca13696`: viewer shell and rules serving over HTTPS with real Caddy; 15 preflight checks and 55 HTTP and security checks including 290 traversal and canary probes passed at install; **both rehearsal units disabled and stopped, the deadline expired on purpose (`2000-01-01T00:00:00Z`), no broadcast clock exists, `latest.json` answers 404.** Package SHA-256 `1db371c4…08f07f1`, 249,415,680 bytes.
* The permission-independent installer (explicit modes; checks as the real Caddy and service users; regression test under `umask 027` proven to fail on the previous installer) and the reversible quarantine of the failed first install.

### Implemented and tested **locally only** (this batch, `backend/multiseason-continuity-2026-11-06`)
* Multi-season continuity: hand-over by one carry record on a never-restarted clock; cumulative replay, playback and Honors indexes; builder `next-season`; see `docs/BROADCAST_CONTINUITY.md`. Verified in virtual time: a real engine league through **all of season 2 to year close** (twin digests identical at event 94; the daemon through postseason, Honors and year close: 800 games, 8 Honors parts, 0 problems), **ten synthetic seasons** with restarts at three boundaries, pause/restart/crash across boundaries, and **late preparation of the next season** (a defect found and fixed: the clock now holds at a season's end in a store marked `continuous`, and a late successor starts at the hand-over, never in the past).
* Read-only storage inventory and retention proposal: `docs/STORAGE_RETENTION.md`. **Evidence changed the proposal:** published replay files cannot be re-made from the archive (220 of 400 differ), the daemon does not repair an earlier season's public files, so the public folder is permanent and needs its own backup before any retention action; live recordings are re-made exactly.
* Public-folder backup and recovery set (`docs/PUBLIC_BACKUP.md`, branch `backend/public-backup-2026-11-06`): consistent backup while publishing, restore, builder exclusion by lock, quarantine of stale `.partial` folders, and (2026-11-06, second pass) **bounded inventory and manifest memory**: create, verify and restore hold about 31 to 34 MB at every size from 29,000 to 472,000 files (synthetic, no index documents; the closure check still loads the playback/replay/Honors indexes whole, about 1.3 KB per listed game, measured, negligible at about 400 games a season) (was 67 to 799 MB for create and 49 to 463 MB for verify and restore), at the price of temporary disk of about 0.27 KB per file (128 MB at 472,000 files); **one operation per backup name** (a second create, `--discard-partial`, quarantine or restore on a name in use is refused and touches nothing); and a strict, shared check that every previously published file keeps its exact bytes across a restore and resume (the only exception: a newly built release manifest's `generated_at`, comparison only, documented in `docs/PUBLIC_BACKUP.md` section 6). Local only; the Droplet and the rehearsal were not touched.
* Not in the installed package: the rehearsal runs one season (the package would need a rebuild from the final commit to carry the continuity code).

### The real-time rehearsal (IN PROGRESS since the owner's start on 2026-11-06 ~01:02 PST; the items below are what it must still show)
* One capped 48-hour season in real time (47 events, 400 games, forced overrun `w041:450`), the guard and monitor under real load, the first real operation of the daemon and builder on the 1 vCPU / 2 GB host.
* Real-phone behaviour, network-peeking limits as measured by someone, the broadcast frontend on a real device.
* Pause, restart and recovery in real time (proven in virtual time and with a real-time smoke test of the daemon).
* **Not covered by this rehearsal, still pending:** multi-season real-time verification (hand-over, hold at season end, recovery across a boundary in real time). A season is 48 hours, so M5 uses a short synthetic season pair on the host.

### Known but unresolved
* **Resolved test race (frontend):** `test_history_lists_other_replays_of_a_season_collapsed_with_a_label_and_a_link` in `test_site_replay_links.py` was a test race (it read the rows before the `toggle` event built them). It was fixed by commit `c7cbb98` (the test waits for the rows to be visible). I read the fix in the repository; I did not re-run that test in this batch. It is **not** one of the open items below.
* **Still unresolved, observed and not explained** (docs/COMBINED_RELEASE_PLAN.md, `docs/BROADCAST_CONTINUITY.md` section 10): (1) a team-page **scroll-restoration** failure seen twice in one rehearsal run (not reproduced in ten repeats; cause unproven), and (2) `tests/test_site_broadcast.py` **reported intermittent**, not reproduced in 40 sequential runs (20 normal, 20 on one contended core). Neither is a known backend defect; both stay open as M12.
* Network peeking remains possible by design (whole plays in segments); not claimed secret.
* Disk: about 2.0 GB per season (`docs/STORAGE_RETENTION.md`); no pruning exists. The measured basis and the line between capacity safeguards and deletion policy are in section 3a; the earlier "retention before season 3" requirement is withdrawn there. About 59,000 public files a season (inode and backup time at ten seasons not examined); the replay-library and results indexes grow by about 54 KB and 123 KB a season and are fetched whole.
* Backup tooling limits (`docs/PUBLIC_BACKUP.md` section 8): the real 117,707-file folder was not re-measured after the bounded-memory change (its source tree was gone; the synthetic series is flat at 31 to 34 MB, the real folder is expected to be far below the earlier 202 MB, not measured); temporary disk is now the cost and nothing checks that `--scratch` is not RAM; the lock is advisory and local; no backup scheduling, off-host copy or retention exists.
* **The public folder is the only home of earlier seasons' live recordings, Honors files and indexes and the daemon cannot rebuild it** (tested); published replay files are not reproducible from the archive. A backup and restore rehearsal of the public folder is therefore a precondition of any retention action.

### Still unbuilt (not in this batch, listed so they are not mistaken for done)
* **Adopting the real league:** the builder only makes a fresh isolated league. Broadcasting seasons 5 onward of the existing league (stopped at 188) or starting a new league is an **owner decision** (below); the technical step is small (a plan with `event_base` 188) but unbuilt and unrehearsed. **It is not needed for the boundary rehearsal or unattended-operation rehearsals, which use fresh isolated leagues (section 3b).**
* **Unattended operation:** a supervisor that runs `next-season` and `step` ahead of the clock, restarts the daemon, and alerts; nothing schedules the builder.
* Production host configuration for the broadcast (the rules are proven on the rehearsal host only), its rollback rehearsal, and the exporter timer (still uninstalled on production).
* Storage retention actions (design only) and an exporter scoped to the current season.
* Frontend: multi-season ordering and season labels in the replay library and Watch lists (a proposal, not a defect).
* Engine items already listed as not built in the spec and `docs/BUILD_STATUS.md` (spatial extensions, All-Star, pick-for-pick trades, and others) and the broadcast's unavailable awards (MVP, All-League, ROY, COY, statistical leaders, staff halls): **league rules and engine content, outside this implementation batch.**

## 3. Launch requirements, and what can wait

"Launch" here means: the recurring league is public on the production host, runs without a person at the keyboard, and what has been published can be recovered. Everything else is a later improvement. The split below replaces the earlier single list; it also corrects two statements in it (the retention timing and the league decision as a gate), explained in sections 3a and 3b.

### Launch requirements (in dependency order)

1. **The capped real-time rehearsal finishes and is judged** (M1, running; deadline `2026-11-08T14:18:10Z`), and its findings are fixed with a failing test first (M2).
2. **The boundary rehearsal** (M5): continuity code packaged and run through the real daemon and real Caddy across a hand-over using a short synthetic season pair. Needs the rehearsal host free, which means **the current rehearsal has finished and been verified, its evidence is preserved, and its services are stopped** (not necessarily the 54-hour safety deadline: see the note below), or a local scratch with real Caddy. **Does not need the league decision** (3b).
3. **Unattended operation with capacity safeguards** (M6): supervisor, `next-season` automation, alerts, **and the capacity safeguards of 3a** (a disk floor that pauses the builder, a public-size cap that is a policy per season and not the rehearsal's fixed 3 GB, disk and inode alerts).
4. **A verified recovery set exists off the host before the first public season on production, and the runbook has been rehearsed** (M8a, M8b: built, tested and now run end to end on a scratch copy, `docs/BACKUP_RUNBOOK.md`, `docs/BACKUP_RUNBOOK_REHEARSAL_2026-11-06.md`; still to do before launch: the same on a scratch host from a real production backup, with the Droplet only when the owner allows contact). Reason: the public folder is the only home of published replays and cannot be rebuilt (section 2); this is protection against loss, not against a full disk.
5. **Production serving rules and a rollback rehearsal** (M7).
6. **The owner's decisions** (M3a hostname and M3b fresh-or-adopt), and **adopt mode (M4) only if the answer is adopt**. These gate the cut-over and what viewers see first; they do not gate items 1 to 5 (3b).
7. **A real-phone check** of the broadcast frontend (M11a).

**Timing of M5 on the rehearsal host.** The `2026-11-08T14:18:10Z` deadline is a safety cut-off for the running rehearsal, not a waiting period. M5 may start as soon as all three hold: (1) the rehearsal has completed and been verified (`rehearsal_check.py` verdict, or the owner ends it and records why); (2) its evidence (monitor log, evidence folder, final `latest.json` and status, the production fingerprint check) is copied somewhere that survives the host being reused; (3) its services are stopped and disabled by the owner or with the owner's say-so. If that happens before the deadline, M5 does not wait for it. Until then nothing is done on that host by me.

### Later improvements (none blocks launch)

* Retention actions R2, R3, R4 and the exporter scoped to the season being built (M8, M9, M10): these are *deletion policy*, see 3a. They need the owner's approval and a verified backup first.
* Season ordering and labels in the replay library and Watch lists (M11b, a frontend proposal).
* Automated backup scheduling and off-host transfer (the launch requirement is one verified off-host set and a written manual routine, not automation).
* The intermittent frontend test and the unexplained scroll failure (M12).
* Engine and league-rule content (awards, spatial extensions and so on): outside this batch.

### 3a. Retention before season 3: what the measurements do and do not support

An earlier version of this file said retention action R2 was "essential before season 3". **That requirement is withdrawn as stated: no measurement supports season 3 as a capacity deadline.** What is measured (`docs/STORAGE_RETENTION.md` sections 1 and 3; Droplet figures read on 2026-11-06):

| Fact | Value | Source |
|---|---|---|
| Growth | about 2.0 GB a season (1.1 GB private, 0.95 GB public), linear; about 59,000 public files a season | measured on disposable full seasons |
| Droplet disk | 47 GB, about 44 % used, so about 26 GB free | read-only look, 2026-11-06 |
| Disk left before the 6 GB floor | (26 - 6) / 2.0, **about 10 seasons**; about 13 to a full disk | arithmetic on the two lines above (the earlier text said "a dozen"; this is the same figure computed here) |
| Time that is in seasons | a season is 48 hours on air, so back to back, 10 seasons is about 20 days; a slower cadence stretches it | design (cadence is an owner choice, not decided) |
| R2 would free | about 0.6 GB of the 2.0 GB a season (30 %) | inventory, `STORAGE_RETENTION.md` section 3 |

What that means, plainly:

* **Season 3 is about 4 days after launch if seasons run back to back and roughly 20 GB is still free then.** Nothing about disk size makes season 3 special. R2 would stretch about 10 seasons to about 14, not change the order of magnitude. The lasting answer to growth is a larger or second volume and off-host copies, not deleting recordings. Whether R2 is ever worth its risk is therefore an open question, not a pre-launch requirement.
* **Where season 3 or 4 genuinely matters is a configuration cap, not the disk.** The rehearsal units start the daemon with `--max-public-gb 3` (exit 70, no restart) and the monitor stops the broadcast above 3 GB. A season publishes 0.78 to 0.95 GB, so a production unit copied from the rehearsal would stop publishing around the end of season 3 or during season 4 although about 20 GB of disk is free. That is a capacity safeguard that must become a per-season policy (launch item 3), not a reason to delete anything.
* **A backup on the same disk is the real near-term capacity cost.** A recovery set is about the size of the tree it copies, so with a full set kept on the same volume the budget is about 2.0 + 2.0 GB a season: (26 - 6) / 4.0, **about 5 seasons** (arithmetic, not measured on the Droplet). A restore rehearsal needs room for another copy on a scratch host. That is why item 4 requires the set to be off the host and the preflight (`tools/backup_space.py`, M8b) refuses a destination that does not fit.

**Capacity safeguards (protect the broadcast from running out of room; reversible; launch requirements):**

| Safeguard | State |
|---|---|
| Free-disk floor that stops the daemon / monitor (rehearsal: 6 GB) | built for the rehearsal; a builder-side pause and a production floor are M6, unbuilt |
| Public-size cap per season (replace the fixed 3 GB) | unbuilt; one-line unit change plus a rule; needs the owner's number |
| Disk, inode and backup-age alerts | unbuilt (M6) |
| Backup preflight, RAM-backed refusal, full-disk handling | built and rehearsed (M8b) |
| Off-host recovery set | procedure built; first real one not yet taken |
| Larger or second volume | owner and hosting decision, no code |

**Deletion policy (removes or moves data; irreversible in effect; later; needs approval):**

| Rule | Frees | Precondition |
|---|---|---|
| R2 private live-recording cache after hand-over | about 0.6 GB a season | owner approval; verified backup pair newer than the season close; move, never delete; quarantine; manifest |
| R3 single public copy of each replay (hard link) | about 0.25 GB a season | backup must preserve links; byte and URL identical |
| R4 prune old live recordings (keep K = 2) | part of the public 0.95 GB | frontend decision on what viewers lose |
| R1 permanent history, R5 feed keep-list, R6 operator backups | not removed | - |

Nothing in the second table is needed to launch, and none of it is built.

### 3b. The league decision (fresh or adopt) and the boundary rehearsal

**The earlier path listed "decide the league" as step 3 before the boundary rehearsal. The code does not require it, so it is removed as a dependency of the isolated synthetic rehearsal.** What the code does:

* `src/dflsite/season.py` (the hand-over) reads only the store: plan, builds, clock, `carry.json`. It never opens or identifies a league (its only league mentions are comments and the unavailable-awards list).
* `tools/broadcast_build.py next-season` takes `event_base` from what was built (`done`) and checks that the league's event count equals it; it works for any league the root holds. `init --continuous` and `continuous` mark the store; both work on the fresh isolated league `init` makes.
* `tests/test_multiseason.py` runs the whole boundary on a synthetic store (season-901/902 identities, repeated game ids) with **no league at all**, and `tools/broadcast_multiseason_check.py` and `tools/broadcast_tenseason_check.py` use fresh leagues.

So M5 and M6 are done on disposable fresh leagues, whichever the owner picks. **What still depends on the decision:**

| Item | Depends on fresh-or-adopt? | Why |
|---|---|---|
| M5 boundary rehearsal (synthetic) | **no** | above |
| M6 unattended operation, scratch two-season run | **no** | uses fresh leagues; the supervisor does not care how the league began |
| M8a/M8b backup and restore rehearsal | **no** | a set is a copy of whatever root it is given |
| M4 adopt mode | **yes** | builder only creates a fresh league today; adopting needs a plan with `event_base` 188 and a read-only copy of the production league |
| M7 serving rules and rollback | no for the rules; the **hostname** decides the final cut-over and DNS | the rules are rehearsed on the production host's current name |
| Cut-over and what viewers see first | **yes** | seasons 5 onward of the real league, or a new league starting at season 1 |

If the owner later chooses adopt, the continuity behaviour is re-verified under adopt only by M4's own acceptance check (a copy of the league at 188 fires events 189 to 195 with digests identical to a free-running twin, the production league file never opened for writing). That is one check, not a repeat of M5. I did not find a code dependency beyond that; this was read from the code listed above, and M4 itself is unbuilt, so the claim "nothing else breaks under adopt" is unproven until M4 exists.

## 4. Milestones

Launch requirements first, then later improvements. "Depends on" lists only real dependencies.

### 4a. Launch requirements

| # | Milestone | Acceptance check | Depends on |
|---|---|---|---|
| M1 | **Real-time capped rehearsal - IN PROGRESS** (started by the owner 2026-11-06 ~01:02 PST, deadline `2026-11-08T14:18:10Z`; not verified or touched by me) | `rehearsal_check.py` verdict COMPLETE: 47 events, 400 games, heartbeat entries throughout, zero guard trips or each trip explained, public tree < 3 GB, production fingerprint identical before and after, units disabled and stopped again at the end | installed `c28ca13696` |
| M2 | **Rehearsal findings fixed** (failing test first) | each finding has a test that failed and now passes; production untouched | M1 |
| M3a | **Public hostname** recorded | written ruling; no code | owner |
| M3b | **League: fresh or adopt-188** recorded | written ruling; no code | owner |
| M4 | **Adopt mode**, only if M3b says adopt: builder starts from a copy of an existing league at a season boundary (`event_base` = its events, season = next) | a copy of the production league at 188: builder fires events 189 to 195 identical in digest to a free-running twin; the production league file is never opened for writing | M3b, continuity |
| M5 | **Boundary rehearsal in real time**: package rebuilt from the final commit; a short synthetic season pair (minutes long) run through real Caddy and the real daemon across the hand-over, with pause and a kill around it | the checks of `tests/test_multiseason.py` hold on the host: first season's files and indexes unchanged, no early exposure, same public tree as an uninterrupted run | continuity branch merged into the package; the rehearsal host free as defined under launch item 2 (rehearsal finished and verified, evidence preserved, services stopped), or a local scratch with real Caddy. **Not M3.** |
| M6 | **Unattended operation with capacity safeguards** (`init --continuous`; supervisor as systemd units written and tested on a scratch host, not enabled; builder scheduling ahead of the clock; `next-season` automation; **builder pause at the disk floor; per-season public cap; disk, inode and backup-age alerts**; alerts on `season_not_started`, `coverage_wait`, guard trips) | a scratch-host run of two short seasons with an injected builder failure, a daemon kill and a simulated low-disk pause, no human step | M5 |
| M7 | **Production serving rules and rollback rehearsal** for the broadcast on the production host | the same 55 HTTP and security checks plus the production verifier; rollback restores the fingerprint | M1 |
| M8a | **Public-folder backup and restore rehearsal** (**built and tested locally 2026-11-06**, `docs/PUBLIC_BACKUP.md`; bounded memory, per-name locks, strict previously-published-bytes checks; still to do: the same from a real production backup on a scratch host, hard links if R3 is ever adopted) | restored tree byte-identical to the source; no dangling index entries | M5 |
| M8b | **Backup preflight, full-disk safety and runbook** (**built, tested, and the runbook executed end to end on a local scratch copy 2026-11-06**: `docs/BACKUP_RUNBOOK.md`, transcript `docs/BACKUP_RUNBOOK_REHEARSAL_2026-11-06.md`; covers preflight, create, verify, RAM-backed and low-space refusals, full disk during copying, quarantine, retry, restore). **Still to do:** one off-host set before launch, and a real production backup only when the owner allows contact with the Droplet; on the Droplet's own disk the estimate is compared with what the run really used | the runbook is followed with the preflight in the loop; the estimate is compared with real use on the Droplet's disk | M8a |
| M11a | **Real-phone check** of the broadcast frontend | a physical phone, ChatGPT's own tests | continuity |

### 4b. Later improvements

| # | Milestone | Acceptance check | Depends on |
|---|---|---|---|
| M8 | Retention action R2 (revised: live recordings and public-equal releases only, never the private replay export) and the exporter scoped to the season being built. **Deletion policy, not a capacity safeguard (3a).** | on a scratch copy: removed set equals the inventory's, season rebuilt byte-identical from the archive, daemon and builder unaffected; production untouched | owner approval of R2; M8a, M8b; M6 |
| M9 | Single public copy of each replay (hard link, R3) | byte and URL identical; backup preserves links | continuity |
| M10 | Old live recording policy (R4) and any frontend change it needs | owner and frontend decision | frontend |
| M11b | Frontend: season ordering and labels in lists | ChatGPT's own tests | continuity |
| M12 | Intermittent test and the unexplained team-page scroll failure traced or retired | reproduced and fixed, or 200 clean runs recorded | none |
| - | Backup scheduling and off-host transfer automation | scheduled run, alert on a missing or failed set | M8b |

Out of this batch by instruction: economy, richer animation and narrative expansion, and any change to league rules.

## 5. Decisions only the owner can make

1. **Hostname** (gates the final cut-over and DNS, not the rehearsals) and **fresh league or the existing league at 188** (changes what viewers see first and whether M4 is built; engine untouched either way). Neither blocks M5, M6 or M8.
2. The rehearsal start time and who watches the first 90 minutes.
3. **Capacity numbers:** the per-season public cap, the production disk floor (the rehearsal uses 6 GB), the season cadence (back to back or spaced; it sets how fast 2 GB a season is used), and whether to add a volume or move to a larger disk.
4. **Backups:** where the off-host copy lives and who holds it; whether to keep two independent copies of every replay file as a standing rule.
5. **Retention (later):** R2 yes or no (3a shows it is not needed to launch), R4 and the value of K, how many rollback copies to keep.
6. Whether the replay library should list the newest season first (a frontend interface choice).
