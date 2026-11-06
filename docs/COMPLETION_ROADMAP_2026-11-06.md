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
* `tests/test_site_broadcast.py` is reported intermittent; **not reproduced in 40 sequential runs** (20 normal, 20 on one contended core; `docs/BROADCAST_CONTINUITY.md` section 10). Still open. The history test `test_history_lists_other_replays...` in `test_site_replay_links.py` is a known test race (frontend), and a team-page scroll-restore failure seen once is unreproduced. All three are frontend-test issues, none is a backend defect found.
* Network peeking remains possible by design (whole plays in segments); not claimed secret.
* Disk: about 2.0 GB per season (`docs/STORAGE_RETENTION.md`); no pruning exists. About 59,000 public files a season (inode and backup time at ten seasons not examined); the replay-library and results indexes grow by about 54 KB and 123 KB a season and are fetched whole.
* Backup tooling limits (`docs/PUBLIC_BACKUP.md` section 8): the real 117,707-file folder was not re-measured after the bounded-memory change (its source tree was gone; the synthetic series is flat at 31 to 34 MB, the real folder is expected to be far below the earlier 202 MB, not measured); temporary disk is now the cost and nothing checks that `--scratch` is not RAM; the lock is advisory and local; no backup scheduling, off-host copy or retention exists.
* **The public folder is the only home of earlier seasons' live recordings, Honors files and indexes and the daemon cannot rebuild it** (tested); published replay files are not reproducible from the archive. A backup and restore rehearsal of the public folder is therefore a precondition of any retention action.

### Still unbuilt (not in this batch, listed so they are not mistaken for done)
* **Adopting the real league:** the builder only makes a fresh isolated league. Broadcasting seasons 5 onward of the existing league (stopped at 188) or starting a new league is an **owner decision** (below); the technical step is small (a plan with `event_base` 188) but unbuilt and unrehearsed.
* **Unattended operation:** a supervisor that runs `next-season` and `step` ahead of the clock, restarts the daemon, and alerts; nothing schedules the builder.
* Production host configuration for the broadcast (the rules are proven on the rehearsal host only), its rollback rehearsal, and the exporter timer (still uninstalled on production).
* Storage retention actions (design only) and an exporter scoped to the current season.
* Frontend: multi-season ordering and season labels in the replay library and Watch lists (a proposal, not a defect).
* Engine items already listed as not built in the spec and `docs/BUILD_STATUS.md` (spatial extensions, All-Star, pick-for-pick trades, and others) and the broadcast's unavailable awards (MVP, All-League, ROY, COY, statistical leaders, staff halls): **league rules and engine content, outside this implementation batch.**

## 3. Shortest practical path to a continuously watchable, recurring league

1. **The capped real-time rehearsal** with the installed package: **running now** (owner-started, deadline `2026-11-08T14:18:10Z`); its result decides what step 2 contains.
2. **Fix what it finds** (in order of what the guard reports).
3. **Decide the league** (fresh league or adopt the production league at 188) and the public hostname.
4. **Package the continuity code and rehearse a hand-over in real time** on the rehearsal host with a deliberately short synthetic season (a few minutes long, same code and clock, real Caddy), because a real boundary is 48 hours away.
5. **Unattended operation:** supervisor for builder, daemon, monitor and `next-season`; alerts; a disk floor that pauses the builder.
6. **Production serving rules and rollback** on the production host, then cut-over.
7. Public-folder backup and restore rehearsal (new, M8a), then retention action R2 (`docs/STORAGE_RETENTION.md`, revised) before the third season.

Steps 1 and 3 need the owner; 4 to 7 are backend work; the frontend steps below run in parallel.

## 4. Next implementation itinerary (essential release work first)

| # | Milestone | Acceptance check | Depends on | Class |
|---|---|---|---|---|
| M1 | **Real-time capped rehearsal - IN PROGRESS** (started by the owner 2026-11-06 ~01:02 PST, deadline `2026-11-08T14:18:10Z`; not verified or touched by me) | `rehearsal_check.py` verdict COMPLETE: 47 events, 400 games, heartbeat entries throughout, zero guard trips or each trip explained, public tree < 3 GB, production fingerprint identical before and after, units disabled and stopped again at the end | installed `c28ca13696` | essential |
| M2 | **Rehearsal findings fixed** | each finding has a failing test first and passes; production untouched | M1 | essential |
| M3 | **League decision** recorded (fresh vs adopt-188; hostname) | written ruling; no code | owner | essential |
| M4 | **Adopt mode** (only if M3 says adopt): builder starts from a copy of an existing league at a season boundary (`event_base` = its events, season = next) | a copy of the production league at 188: builder fires events 189 to 195 identical in digest to a free-running twin; the production league file is never opened for writing | M3, continuity | essential if adopt |
| M5 | **Boundary rehearsal in real time**: package rebuilt from the final commit; a short synthetic season pair (minutes long) run through real Caddy and the real daemon across the hand-over, with pause and a kill around it | the checks of `tests/test_multiseason.py` hold on the host: first season's files and indexes unchanged, no early exposure, same public tree as an uninterrupted run | M1, continuity branch merged into the package | essential |
| M6 | **Unattended operation** (start a recurring league with `init --continuous`, or `broadcast_build.py continuous`, so the clock holds at each season's end): supervisor (systemd units written and tested on a scratch host, not enabled), builder scheduling ahead of the clock, `next-season` automation, alerts on `season_not_started`, `coverage_wait`, guard trips and disk floor | a scratch-host run of two short seasons with an injected builder failure and a daemon kill, no human step | M5 | essential |
| M7 | **Production serving rules and rollback rehearsal** for the broadcast on the production host | the same 55 HTTP and security checks plus the production verifier; rollback restores the fingerprint | M1 | essential |
| M8a | **Public-folder backup and restore rehearsal** (new; **built and tested locally 2026-11-06, `docs/PUBLIC_BACKUP.md`**: consistent backup while publishing, restore into an empty folder, checksum and closure checks, interrupted and damaged cases, recovery-set pairing with the store and league; real-size figures on two real seasons; **bounded memory, per-name locks and strict previously-published-bytes checks added 2026-11-06**; still to do: the same rehearsal on a scratch host from a real production backup, hard links if R3 is adopted): back up the public tree and the store, restore into a scratch host, same indexes and every listed file present; hard links preserved if R3 is adopted | restored tree byte-identical to the source; no dangling index entries | M5 | essential before any retention action |
| M8b | **Backup preflight and full-disk safety** (**built and tested locally 2026-11-06**, `docs/PUBLIC_BACKUP.md` section 3b: per-filesystem disk estimate checked against real runs, existing partials and quarantines reported and never counted as free, tmpfs refused by default, create/restore/verify stop safely on a full disk and keep the evidence, tested on a real small tmpfs). **Still to do:** a written scratch-host runbook (create, verify, restore, `status`, `check`), run end to end on a local scratch copy, and run against a real production backup only when the owner allows contact with the Droplet | the runbook is followed on a scratch copy with the preflight in the loop; on the Droplet's own disk the estimate is compared with what the run really used | M8a | essential before any retention action |
| M8 | **Retention action R2 (revised: live recordings and public-equal releases only, never the private replay export)** (move, never delete; quarantine; manifest) and exporter scoped to the season being built | on a scratch copy: removed set equals the inventory's, season rebuilt byte-identical from the archive, daemon and builder unaffected; production untouched | owner approval of R2, M6 | essential before season 3 |
| M9 | Single public copy of each replay (hard link) | byte and URL identical; backup preserves links | continuity | later |
| M10 | Old live recording policy (R4) and any frontend change it needs | owner and frontend decision | frontend | later |
| M11 | Frontend: season ordering and labels in lists; real-phone check | ChatGPT's own tests plus a physical phone | continuity | later (phone check essential before launch) |
| M12 | Intermittent test and the unexplained team-page scroll failure traced or retired | reproduced and fixed, or 200 clean runs recorded | none | later |

Out of this batch by instruction: economy, richer animation and narrative expansion, and any change to league rules.

## 5. Decisions only the owner can make

1. Fresh league or the existing league at 188 as the public league (changes what viewers see first; engine untouched either way).
2. The rehearsal start time and who watches the first 90 minutes.
3. Retention: R2 (revised) yes or no, R4 and the value of K, how many rollback copies to keep; and whether to keep two independent copies of every replay file (a backup of the public folder) as the standing rule.
4. Whether the replay library should list the newest season first (a frontend interface choice).
