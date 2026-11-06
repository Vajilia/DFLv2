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
| Real-engine determinism as a repeatable test | `tests/test_multiseason_engine.py`: needs a finished first season (`DFL_TEST_YEAR1_ROOT`, about 35 minutes to make; only copied), **skipped otherwise**. Run on 2026-11-06 against the disposable year-1 league: passes (13 events of season 2: identical league digests, only season 2's games taken). |
| **Real engine, the whole second season** (2026-11-06) | Builder-driven copy of a finished year-1 league taken through **all of season 2**: events 48 to 94 (offseason, draft, 36 regular-season weeks, Tour, postseason, Honors, year close), 400 games, Honors parts `fan_entrant, halls, rings, tiara`. A free-running twin (league runner alone) to event 94: **`league_store.py digest` identical** (journal and deep hash `485b49f5d0e12d66cfb53d77ee555e15a2544e38b72242d6193c20f9845a03c0`, 94 events). Both ran with another job sharing the CPU; the builder took about 14 minutes for the 47 events. |
| **Real store, the daemon through the whole of season 2** (`broadcast_multiseason_check.py --through-end`, virtual time, step 20 s, on a hard-link copy so season 1's files were not touched) | Hand-over at 172,800 s, first season-2 results at 181,740 s, **season 2 ends at 345,600 s** (no overrun in this season). At the end: **800 games public** (400 + 400, ids repeating between the seasons, `(season, id)` and `gid` unique), 800 replay-library entries, **8 Honors parts** (4 per season) with their files, `latest.json` at the season's last event, every live recording `final`, every replay file present, season-1 files byte-identical, no release or replay of an event not yet due public. **0 problems**, 288 s. Public tree: 117,707 files, 1.55 GB (indexes: playback 246 KB, replay library 107 KB). |
| Late preparation of the next season | section 8. Failing tests committed first (`f4378dd`), fix in `a1d60d1`. |
| **Ten seasons in virtual time** (`broadcast_tenseason_check.py`) | section 9: 10 synthetic seasons, 9 boundaries, 0 problems, 9 kill-and-restart runs. |
| Existing suites | `test_season`, `test_broadcast`, `_guard`, `_timetable`, `_plan`, `test_honors`, `test_broadcast_build` (now 8 tests, one added: `next-season` refused until the season is built), `test_broadcast_run`: unchanged and passing. |

## 4. What does not change, and one observation for the frontend

No frontend change is required and none was made. `site/assets/honors.js` keys sections by `season|kind` and titles them "Season N", so the cumulative Honors index renders several seasons as is. **Proposal, not made:** the Watch and replay-library lists receive all seasons sorted by `gid` (oldest first); if the page lists them in index order, a "newest season first" ordering is a frontend choice. `broadcast/schedule.json` lists only the season on air.

## 5. Limits and open items

* The builder's exporter is asked for the **whole archive** at every event. Measured: it skips games already exported (an event with 20 games took 3.2 s in season 2 with 424 games in the archive; the median event with games took 2.9 s in season 1), so no growth is visible at two seasons; the per-event time at ten or more seasons is not measured (it re-reads the archive's game list and rewrites an index that grows by 400 entries a season).
* A season's builds assume 47 events and the same calendar (`plan.events`); a changed calendar needs its own plan.
* The first season of a store keeps its layout at the root; a store created by `init` has no knowledge of seasons until `next-season` is run.
* The real-store check now covers the whole of season 2 (see the evidence table); seasons 3 to 10 are only covered by synthetic seasons (3 games each), not by real-size ones.
* About 59,000 public files a season (117,707 for two): ten seasons are about 590,000 files; the host's inode count and the backup/`tar` time at that size have not been looked at.
* The replay library and results indexes grow with every season (about 123 KB and 54 KB a season at real size: 246 KB and 107 KB for two seasons, so roughly 1.2 MB and 0.5 MB at ten) and are rewritten whole at every results block and fetched whole by the site. Caddy compression was not measured. A paged or per-season index would be an interface change and is only noted here.
* The builder's exporter cost at ten *real* seasons is still unmeasured (it was flat at two).
* Not claimed: zero network peeking, real-time behaviour over a real boundary (a real-time rehearsal across two seasons is not planned), phone behaviour.

## 6. Commands

```
python -m unittest tests.test_multiseason                                   # about 1 minute
python tools/broadcast_build.py next-season --root R                        # after season 1 is completely built
python tools/broadcast_build.py step --root R                               # builds season 2 (events 48 to 94)
python tools/broadcast_multiseason_check.py --root Rcopy --public P --state S --step 20 --past 600   # use COPIES; P and S new
python tools/broadcast_multiseason_check.py --root Rcopy --public P --state S --step 20 --past 600 --through-end   # the whole of season 2 (about 5 minutes, 1.6 GB public)
python tools/broadcast_tenseason_check.py --workdir D --seasons 10 --step 5 --kill-at 2,5,9 --report D.json       # about 5 minutes with the restarts
python tools/broadcast_build.py continuous --root R                         # mark a store as a recurring league (the clock holds at each season's end)
```

## 8. Late preparation of the next season (defect found and fixed, 2026-11-06)

**The defect.** A season-1 store with no prepared successor let the clock run past the end of the season (nothing stops it: the horizon is only set when a successor exists). If the successor was prepared later, the hand-over wrote `offset_s = end of season 1`, but the clock was already well beyond that: the new season started *already elapsed*. Anything due in the elapsed part, including results blocks and live windows, would be revealed at once without viewing time. (Test `test_an_unmarked_store_that_ran_past_its_end_...` failed on this before the fix: offset 4,220 against a clock at 7,224.)

**The fix, smallest form.**

| Piece | Behaviour |
|---|---|
| `store/continuous` (marker file; `broadcast_build.py init --continuous`, or `broadcast_build.py continuous --root R [--off]` on an existing store) | The end of the season on air is a horizon: with the production (guarded) clock the clock **holds at the end** (status `paused`, `coverage_wait`) until the next season is on air. A store without the marker keeps the old single-season behaviour (the clock runs on), so the installed rehearsal package is unaffected. |
| Hand-over offset | The end of the season, or, if the clock is already more than `HANDOVER_GRACE_S` (10 s) past it, the clock's own position (rounded up). The new season therefore **never starts in the past**. The grace covers one-second ticking and a successor that is already prepared (the guarded clock stops at the end). |
| Partly built successor | Unchanged and now tested: the clock holds at the builder's horizon inside the new season and continues when more is built. |

**Tests** (`tests.test_multiseason.LatePreparation` and one in `Boundary`; all pass): a marked store holds at the end for 400 s then crosses into season 2 with the carry offset equal to the end, one second per second, nothing of season 2 public meanwhile; a partly built successor holds at the horizon (never past it) and continues; an unmarked store that ran 3,000 s past its end starts season 2 at the hand-over (nothing public at that moment, event 5 released 100 s later); the public tree after a long hold plus a late, incremental preparation equals the tree of a store whose successor was ready from the start; a *guarded* daemon killed 40 s before the boundary and restarted after 400 s still hands over at the end with no jump.

**One consequence recorded.** With the *unguarded* virtual clock a restart whose lease carries the viewers' position past the end hands over at that later position (the same rule), so the ten-season check restarts 120 s before a boundary, not 40 s; the 40 s case is covered by the guarded test above, which is the production configuration. **Open:** nothing yet sets the marker for the rehearsal host or the production plan; a recurring league should be started with `init --continuous` (decision for the unattended-operation milestone M6).

## 9. Ten seasons in virtual time (2026-11-06)

`tools/broadcast_tenseason_check.py --seasons 10 --step 5 --kill-at 2,5,9`: ten synthetic seasons (the playback fixture under seasons 901 to 910, 4 events and 3 games a season, the **same three game ids in every season** with different scores) in one store, the real daemon on a never-restarted virtual clock (42,500 s), unguarded.

Checked at every boundary and at the end, all passing (**0 problems**):
* every public file seen at an earlier boundary is byte-identical at the end (indexes and `latest.json` compared entry by entry instead);
* playback index 3 entries per season, replay library 3, Honors parts 1, exactly `3k` after season `k`; earlier entries all present and unchanged; `(season, id)` and `gid` unique; every playback file equal to its entry (season, gid, score, SHA-256); `latest.json` never moves back;
* no result marker or Honors of season `k+1` public before it begins; no later season in any index while an earlier one is on air;
* **recovery:** the daemon killed and restarted (after a 400 s gap) 120 s before, 1 s after and 700 s after the boundaries ending seasons 2, 5 and 9 (nine runs): the final public tree equals the uninterrupted run's, file for file.

Measurements (the synthetic seasons are tiny, so sizes show growth *rate*, not real size):

| After season | Public files | Carry record | Playback index | Replay index | Honors index |
|---|---|---|---|---|---|
| 1 | 429 | 0 (none yet) | 1,022 B | 471 B | 659 B |
| 5 | 2,117 | 5.8 KB | 4,730 B | 2,103 B | 879 B |
| 10 | 4,227 | 12.9 KB | 9,365 B | 4,143 B | 1,154 B |

* Growth is linear (about 927 B of playback index, 408 B of replay index and 55 B of Honors index per season of 3 games; real size is 400 games: 246 KB and 107 KB for two real seasons, measured above).
* **Real-size carry record:** 177 KB after one real season; a carry scaled to nine real seasons (3,600 playback and replay entries) is **1.75 MB, and `materialize` of the next season with it takes 0.34 s** (0.08 s with one). It runs when `builds.json` changes (about once an event), not every tick.
* **Daemon memory:** with `tracemalloc` and a forced `gc` at each boundary, the daemon holds **+0.9 MB traced and about +2 MB resident over ten synthetic seasons**. (The first report of the check showed 51 to 128 MB resident: that was the check harness hashing and holding trees, not the daemon.) At real size the carry alone is 1.75 MB at nine seasons, so the in-memory growth is of that order, not a concern; a real-size ten-season daemon memory run was not made.
* **Tick time** on a quiet machine: mean 1.5 ms, max 11.5 ms over the whole run (the first, concurrent run showed ticks of 2.6 to 4.0 s near the end, when two other long jobs were hashing a 1.5 GB tree on the same core; recorded, not attributed to the daemon).
* **Disk** (real, the two-season run): the public tree is 1.55 GB of file bytes (1.8 GB as `du` counts blocks) for two seasons, about 0.8 GB and 59,000 files a season; the private store is on top of that (`docs/STORAGE_RETENTION.md`).

## 10. Appendix: the intermittent broadcast test (investigation, 2026-11-06)

Method: `tests.test_site_broadcast` (7 tests, real Chromium) run **sequentially, one process at a time, nothing else running**, 20 times normally and 20 times pinned to one core (`taskset -c 0`) with a busy loop competing for that same core (about half the CPU; each run 14 to 15 s instead of 11 s). The harness kept the complete output of any failing run (none was kept).

Result: **40 of 40 runs passed; the failure was not reproduced** in either condition. It stays **unresolved**: the original unidentified failure was seen when suites ran together, and a single clean run family proves nothing about that case. If it recurs, capture the exact assertion and the whole output of that run (the harness is a ten-line shell loop: `python -W ignore -m unittest -v tests.test_site_broadcast > run_N.log`, delete the log when the exit status is 0). Unrelated, already recorded in `docs/COMBINED_RELEASE_PLAN.md`: `test_history_lists_other_replays_of_a_season_collapsed_with_a_label_and_a_link` (frontend) is a known test race, and `tests/test_site_recovery.py` imports a sibling module and must be run with `python -m unittest discover -s tests -p test_site_recovery.py` (2 tests, passing); run as `tests.test_site_recovery` it fails at import, which is the test layout, not a defect of the code under test.
