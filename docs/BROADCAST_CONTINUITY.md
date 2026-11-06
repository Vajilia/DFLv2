# Multi-season broadcast continuity

Branch `backend/multiseason-continuity-2026-11-06`, from `backend/rehearsal-package-2026-11-05` at `c28ca13696b0d8607fe92e7509cc2edbbdc07738`. **Local implementation and tests only.** Nothing was deployed, no Caddy reload, no unit or timer touched, nothing merged, the installed rehearsal package and the production league (stopped at event 188) were not touched. `src/dfl/`, `data/`, league rules and `site/` are unchanged. Every league used below is a disposable copy in a temporary folder.

## 1. The gap this closes

`docs/BROADCAST_INTEGRATION.md` section 5 listed it: the broadcast was **one league year**. A second year's plan did not carry the first year's Honors index or replay indexes forward, so on a never-restarted public folder:

* `playback/index.json`, `broadcast/replays/index.json` and `broadcast/honors/index.json` are rebuilt from the season on air alone. When the second season began they would have been **rewritten without the first season's games and awards**, and `latest.json` and its release group would have been removed (a reveal group with nothing due is deleted by design).
* The plan, the builder, the clock and the event numbering all assumed seasons 1 and events 1 to 47.

## 2. What was built (smallest compatible change)

| Piece | Change |
|---|---|
| `src/dflsite/season.py` | `materialize(plan, builds, carry=None, hold_at_end=False)`. With a **carry record** the season starts at `carry.offset_s` on the same clock, and every cumulative file (replay library, playback index, Honors index, `latest.json`) starts from what the earlier seasons made public, restated at the first second. The daemon (`SeasonBroadcaster`) writes the carry record itself, at the end of the season on air, when the next one is prepared and fits. |
| `src/dflsite/broadcast.py` | one line: the replay library index is `carry_replays + this season's ended games`. |
| `tools/broadcast_plan.py` | the plan records `event_base` (league events before the season: 0, 47, 94, ...). |
| `tools/broadcast_build.py` | `next-season` prepares `store/seasons/sNNN/{plan,builds}.json` once the season being built is complete (the builder may run ahead of the broadcast, as before). `step` works on the highest season; its builds use the league's own event numbers (48 to 94 for season 2); it takes only that season's games from the exporter (which covers the whole archive). |
| `tools/broadcast_multiseason_check.py` | development check on a real built store in virtual time across the boundary. |

Unchanged: the first season's files stay at the store root; a store without a carry record or a prepared next season behaves exactly as before (all 59 existing broadcast tests pass unmodified).

### The hand-over

`store/carry.json` is **the whole hand-over, one atomic file**. Before it exists the old season is current; after, the new one is; a crash can leave nothing in between and the next process finishes by simply reading the store. It holds: the new season number, `offset_s` (the **actual** end of the season before on the broadcast clock, overruns included), the cumulative playback entries, Honors parts and replay-library entries of the finished seasons, and the last release (events and pointer). The daemon writes it when all of these hold: the season on air is completely built, the clock has reached its end, the prepared season is the next number, its first event follows the last one made public, and every game id in it is new. Otherwise nothing happens; the reason is logged once on stderr (`season_not_started`) and **the clock holds at the end of the season on air** (`coverage_wait`), so viewers are never run past what is prepared. Only the daemon ever changes season.

The broadcast clock is never restarted: season 2's plan runs at `offset_s` + its own plan seconds. Pause, the lease, the publication guard and recovery therefore work across the boundary with no new mechanism.

### Why game ids that repeat cannot select another season's game

The site's ids (`reg:w01:g01`) repeat every season; the archive ids (`S001/reg:w01:g01`) do not. Nothing here is keyed by the short id: windows, builds, live folders (`S001-reg-w01-g01`), replay files (`<gid-slug>.<sha10>.json`), the playback index (sorted by `gid`) and the replay library all use the full `gid`. Beyond that, the daemon refuses to start a season whose builds contain a `gid` already played or whose games do not belong to the plan's season, and the check tool asserts `(season, id)` and `gid` unique in the indexes and every playback file equal to its entry (season, gid, score, SHA-256).

## 3. Evidence

All of it on this branch's code. Production, the Droplet and `site/` were not involved.

| Check | Result |
|---|---|
| `tests.test_multiseason` (synthetic two-season store in virtual time; the second season **repeats season 1's ids with other scores**) | 15 tests. Written first and committed failing (`7b1fdfd`: 6 failures, 4 errors against the old code), passing after the daemon change (`404cd16`). |
| Earlier season stays as it was | every season-1 file byte-identical after the boundary (schedule, clock and status excepted: they move with the position); season-1 entries in all three indexes equal and in order; `latest.json` never moves back; no index ever disappears. |
| Repeated ids | 6 entries, 6 distinct `gid`, 6 distinct `(season, id)`, 6 files; each file's `gid`, `season`, `id`, score and SHA-256 equal its entry; the same short id has two different results. |
| No exposure of the next season | events 6 to 8 and season-2 Honors never appear in any public file before the block that reveals them; the results index and `playback/` hold no season-2 file; a season-2 replay is listed only after that game's own live folder says `final`; one minute into season 2's first window the first game is `live`, a later window has no folder. |
| Pause, restart, recovery | pause across the boundary: position and tree frozen, resume continues, hand-over happens; a daemon killed and restarted 40 s before, 1 s after and 700 s after the boundary ends in the same tree as an uninterrupted run; a disk-full failure writing the carry record is retried and finished by the next process; the tree at four positions is the same however it was ticked. |
| Guarded clock (production mode) | crosses the boundary without a jump or a stop; holds at the end of a season whose successor does not fit (status `paused`, 4 events published); holds at the end of the last prepared season. |
| A next season that does not fit | foreign game id / wrong first event: no carry record, nothing of season 2 public, clock held. |
| **Real engine, determinism across the boundary** | A copy of a finished real year-1 league (seed 20261105): `next-season`, then the builder fired events 48 to 60 (offseason, draft, week 1, Tour round 1) one at a time with feed, replay and live-recording builds in between. A twin copy was run by the league runner alone to event 60. **`league_store.py digest` of both: identical** (journal, state and RNG consequences). Only season 2's 24 games were taken from the exporter (not 424). |
| **Real store, daemon in virtual time** (`broadcast_multiseason_check.py`, step 20 s) | Season 1 whole (400 games, 59,287 files at its end), hand-over at 172,800 s, season 2's first results public at 181,740 s; 424 games public, 4 Honors parts (season 1's), season-1 files and index entries unchanged, `(season, id)` and `gid` unique with ids repeated between seasons, every playback file equal to its entry, no release not yet due public. **0 problems**, 100 s. |
| Existing suites | `test_season`, `test_broadcast`, `_guard`, `_timetable`, `_plan`, `test_honors`: 59 tests pass unchanged. |

## 4. What does not change, and one observation for the frontend

No frontend change is required and none was made. `site/assets/honors.js` keys sections by `season|kind` and titles them "Season N", so the cumulative Honors index renders several seasons as is. **Proposal, not made:** the Watch and replay-library lists receive all seasons sorted by `gid` (oldest first); if the page lists them in index order, a "newest season first" ordering is a frontend choice. `broadcast/schedule.json` lists only the season on air.

## 5. Limits and open items

* The builder's exporter is asked for the **whole archive** at every event. Measured: it skips games already exported (an event with 20 games took 3.2 s in season 2 with 424 games in the archive; the median event with games took 2.9 s in season 1), so no growth is visible at two seasons; the per-event time at ten or more seasons is not measured (it re-reads the archive's game list and rewrites an index that grows by 400 entries a season).
* A season's builds assume 47 events and the same calendar (`plan.events`); a changed calendar needs its own plan.
* The first season of a store keeps its layout at the root; a store created by `init` has no knowledge of seasons until `next-season` is run.
* The real-store check proves the first 24 season-2 games and the first results block; the whole second season was not run in virtual time (the synthetic store covers a whole second season; its builds are the same format).
* Not claimed: zero network peeking, real-time behaviour over a real boundary (a real-time rehearsal across two seasons is not planned), phone behaviour.

## 6. Commands

```
python -m unittest tests.test_multiseason                                   # about 1 minute
python tools/broadcast_build.py next-season --root R                        # after season 1 is completely built
python tools/broadcast_build.py step --root R                               # builds season 2 (events 48 to 94)
python tools/broadcast_multiseason_check.py --root Rcopy --public P --state S --step 20 --past 600   # use COPIES; P and S new
```
