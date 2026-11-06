# Completion roadmap reconciled with the code (2026-11-06) and the next implementation itinerary

Written from the repository (branches below) and the production and rehearsal records in `docs/COMBINED_RELEASE_PLAN.md`, `docs/BROADCAST_*.md`, `docs/STAGING_DEPLOYMENT_RECORD_20261031.md` and `docs/BROADCAST_REHEARSAL.md`, plus the state read on the Droplet on 2026-11-06 (read-only). **No percentages: a line is either true, with its evidence, or it is listed as open.** `PROJECT_COMPLETION_PLAN.md` (2026-10-29), `BETA_STATUS.md` and `docs/BUILD_STATUS.md` pre-date most of this and are partly superseded; where they disagree with this file, the code and the records cited here win. Nothing below was deployed, merged or started by this batch.

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
* Multi-season continuity: hand-over by one carry record on a never-restarted clock; cumulative replay, playback and Honors indexes; builder `next-season`; see `docs/BROADCAST_CONTINUITY.md`.
* Read-only storage inventory and retention proposal: `docs/STORAGE_RETENTION.md`.
* Not in the installed package: the rehearsal runs one season (the package would need a rebuild from the final commit to carry the continuity code).

### Awaiting the real-time rehearsal (needs the owner to pick a start, someone watching the first 90 minutes, and a new deadline)
* One capped 48-hour season in real time (47 events, 400 games, forced overrun `w041:450`), the guard and monitor under real load, the first real operation of the daemon and builder on the 1 vCPU / 2 GB host.
* Real-phone behaviour, network-peeking limits as measured by someone, the broadcast frontend on a real device.
* Pause, restart and recovery in real time (proven in virtual time and with a real-time smoke test of the daemon).

### Known but unresolved
* `tests/test_site_broadcast.py` is reported intermittent; **not reproduced** so far (see section 4 of the handoff; result in `docs/BROADCAST_CONTINUITY.md` appendix if it recurs). The history test `test_history_lists_other_replays...` in `test_site_replay_links.py` is a known test race (frontend), and a team-page scroll-restore failure seen once is unreproduced. All three are frontend-test issues, none is a backend defect found.
* Network peeking remains possible by design (whole plays in segments); not claimed secret.
* Disk: about 2.0 GB per season (`docs/STORAGE_RETENTION.md`); no pruning exists.

### Still unbuilt (not in this batch, listed so they are not mistaken for done)
* **Adopting the real league:** the builder only makes a fresh isolated league. Broadcasting seasons 5 onward of the existing league (stopped at 188) or starting a new league is an **owner decision** (below); the technical step is small (a plan with `event_base` 188) but unbuilt and unrehearsed.
* **Unattended operation:** a supervisor that runs `next-season` and `step` ahead of the clock, restarts the daemon, and alerts; nothing schedules the builder.
* Production host configuration for the broadcast (the rules are proven on the rehearsal host only), its rollback rehearsal, and the exporter timer (still uninstalled on production).
* Storage retention actions (design only) and an exporter scoped to the current season.
* Frontend: multi-season ordering and season labels in the replay library and Watch lists (a proposal, not a defect).
* Engine items already listed as not built in the spec and `docs/BUILD_STATUS.md` (spatial extensions, All-Star, pick-for-pick trades, and others) and the broadcast's unavailable awards (MVP, All-League, ROY, COY, statistical leaders, staff halls): **league rules and engine content, outside this implementation batch.**

## 3. Shortest practical path to a continuously watchable, recurring league

1. **Run the capped real-time rehearsal** with the installed package (owner picks the time). Essential, already prepared; everything else assumes its result.
2. **Fix what it finds** (in order of what the guard reports).
3. **Decide the league** (fresh league or adopt the production league at 188) and the public hostname.
4. **Package the continuity code and rehearse a hand-over in real time** on the rehearsal host with a deliberately short synthetic season (a few minutes long, same code and clock, real Caddy), because a real boundary is 48 hours away.
5. **Unattended operation:** supervisor for builder, daemon, monitor and `next-season`; alerts; a disk floor that pauses the builder.
6. **Production serving rules and rollback** on the production host, then cut-over.
7. Retention action R2 (`docs/STORAGE_RETENTION.md`) before the third season.

Steps 1 and 3 need the owner; 4 to 7 are backend work; the frontend steps below run in parallel.

## 4. Next implementation itinerary (essential release work first)

| # | Milestone | Acceptance check | Depends on | Class |
|---|---|---|---|---|
| M1 | **Real-time capped rehearsal** (owner start time; first 90 minutes watched; deadline replaced by the owner) | `rehearsal_check.py` verdict COMPLETE: 47 events, 400 games, heartbeat entries throughout, zero guard trips or each trip explained, public tree < 3 GB, production fingerprint identical before and after, units disabled and stopped again at the end | installed `c28ca13696` | essential |
| M2 | **Rehearsal findings fixed** | each finding has a failing test first and passes; production untouched | M1 | essential |
| M3 | **League decision** recorded (fresh vs adopt-188; hostname) | written ruling; no code | owner | essential |
| M4 | **Adopt mode** (only if M3 says adopt): builder starts from a copy of an existing league at a season boundary (`event_base` = its events, season = next) | a copy of the production league at 188: builder fires events 189 to 195 identical in digest to a free-running twin; the production league file is never opened for writing | M3, continuity | essential if adopt |
| M5 | **Boundary rehearsal in real time**: package rebuilt from the final commit; a short synthetic season pair (minutes long) run through real Caddy and the real daemon across the hand-over, with pause and a kill around it | the checks of `tests/test_multiseason.py` hold on the host: first season's files and indexes unchanged, no early exposure, same public tree as an uninterrupted run | M1, continuity branch merged into the package | essential |
| M6 | **Unattended operation**: supervisor (systemd units written and tested on a scratch host, not enabled), builder scheduling ahead of the clock, `next-season` automation, alerts on `season_not_started`, `coverage_wait`, guard trips and disk floor | a scratch-host run of two short seasons with an injected builder failure and a daemon kill, no human step | M5 | essential |
| M7 | **Production serving rules and rollback rehearsal** for the broadcast on the production host | the same 55 HTTP and security checks plus the production verifier; rollback restores the fingerprint | M1 | essential |
| M8 | **Retention action R2** (move, never delete; quarantine; manifest) and exporter scoped to the season being built | on a scratch copy: removed set equals the inventory's, season rebuilt byte-identical from the archive, daemon and builder unaffected; production untouched | owner approval of R2, M6 | essential before season 3 |
| M9 | Single public copy of each replay (hard link) | byte and URL identical; backup preserves links | continuity | later |
| M10 | Old live recording policy (R4) and any frontend change it needs | owner and frontend decision | frontend | later |
| M11 | Frontend: season ordering and labels in lists; real-phone check | ChatGPT's own tests plus a physical phone | continuity | later (phone check essential before launch) |
| M12 | Intermittent test and the unexplained team-page scroll failure traced or retired | reproduced and fixed, or 200 clean runs recorded | none | later |

Out of this batch by instruction: economy, richer animation and narrative expansion, and any change to league rules.

## 5. Decisions only the owner can make

1. Fresh league or the existing league at 188 as the public league (changes what viewers see first; engine untouched either way).
2. The rehearsal start time and who watches the first 90 minutes.
3. Retention: R2 yes or no, R4 and the value of K, how many rollback copies to keep.
4. Whether the replay library should list the newest season first (a frontend interface choice).
