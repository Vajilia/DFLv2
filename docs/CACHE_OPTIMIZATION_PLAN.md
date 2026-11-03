# Plan: bounding the contract-release and player-estimate caches

Status: **plan only**. Nothing here is implemented in the live league. Everything is to be built and measured on isolated copies first. Written 2026-11-01 after the 141-event trial.

## 1. Why

Measured at events 47 / 94 / 141 (`tools/analyze_memory.py`, see `PROJECT_COMPLETION_PLAN.md`):

| | 47 | 94 | 141 |
|---|---|---|---|
| runner resident / peak (MB) | 178.1 / 221.8 | 222.4 / 296.5 | 277.7 / 325.9 |
| contract `_memo` (MB) | 33.7 | 55.5 | 68.8 |
| contract `_memo` entries | 85,247 | 134,460 | 167,880 |
| `World._views` (MB) / entries | 10.7 / 80,520 | 15.5 / 102,425 | 18.0 / 126,884 |
| snapshot pickled with / without `_memo` (MB) | 8.12 / 5.90 | 10.95 / 7.26 | 13.21 / 8.51 |

The service limit is 1,200 MB (`MemoryMax`). At about +50 MB resident per season the runner reaches the limit after roughly 18 more seasons if nothing else grows; the 47-season goal is not reachable with these caches as they are. The `_memo` is the biggest single contributor and the cheapest to fix. About 82 -> 112 MB of resident memory beyond a fresh load is still unexplained and must be measured before anyone claims the caches are the whole story.

## 2. What each cache is

**`Contract._memo`** (`src/dfl/ledger/contracts.py`). `release_charges(year, week, split, keep_guarantees)` stores its result under that 4-tuple. The contract is a *frozen* dataclass; the result is a pure function of the contract's own fields and the arguments. Callers only ask about the *current* `(L.year, L.week)` (world.py, ledger.py) or about later years at week 0. So:
- recomputable: yes, strictly; the code already says so (`neutral.EXCLUDED["_memo"]`, left out of the state digest);
- authoritative: no;
- obsolete entries: every key with `(year, week)` before the current league position can never be asked again by current callers. Entries accumulate because nothing removes them, and because retired or released players' contracts stay in the state.
- It is nevertheless **pickled into every snapshot and held in memory**; that is the only reason it costs checkpoint space.

**`World._views`** (`src/dfl/ledger/world.py`, `view()`). A club's estimate of a player: `rating + position bias + sd * z`, where `z = rng_for(seed, "scout", club, pid, year).gauss(0, 1)` is a *stateless* stream (a fresh `Random` seeded by a hash of its labels; it does not touch the universe's one stateful stream, `year.py: self.rng`). `_views` and `_judged` are cleared once per league year in `age_and_retire`.
- The random part is recomputable. **The cached value is not necessarily recomputable**: `sd` depends on `p.seasons`, the general manager's `EVL`, and whether `p.club == cid`, and `rating` can change; the cache freezes whatever those were at the first query in the league year. If any input changes mid-year (a trade or signing changes `p.club`; a GM change), a recomputation after a restart would differ from the value the club actually decided on. That makes `_views` part of the *decision record* for the year until proven otherwise.
- It is **not** in `neutral.EXCLUDED`, so it is currently part of the state digest. Excluding or rebuilding it changes the digest algorithm.
- Obsolete entries: none by year (cleared yearly). Growth between seasons comes from the number of (club, player) pairs queried, which tracks the player pool.

Stateful random streams in the universe (must stay bit-identical): `year.py` `self.rng`; game-level streams are derived from it per game. `rng_for` and the `random.Random(f"...")` helpers in standings/stv are stateless.

## 3. Options, in the order I would try them

1. **Stop saving `_memo`** (drop it from pickles; rebuild lazily). Safe by construction (frozen contract, pure function, already excluded from the digest). Expected saving at event 141: about 4.7 MB of 13.2 MB per snapshot (about 36%) and the memory the memo holds if it is also not kept alive. No effect on digests, results or random streams. Needs a `__getstate__`/`__reduce__` on `Contract` or a `_memo` write-as-empty at serialization. Changes `src/dfl`, therefore the code fingerprint (see section 6).
2. **Bound the memo in memory**: drop keys older than the current `(year, week)` at each week and year step, or hold only the keys of the current decision period. Because callers only ask about "now", a window of the current `(year, week)` plus week-0 entries of future years preserves everything a decision can use within the period. Cheap to prove (it is a pure function; a miss only costs a recompute).
3. **`_views`**: do *not* touch before measurement. Step A: on an isolated copy, instrument `view()` so that at every event boundary every cached entry is recomputed from current state and compared with the cached value; record any difference. Step B, only if there is zero difference over the whole history *and* a code argument that the inputs cannot change within a league year (or after listing exactly which inputs can and capturing them): exclude it from pickles and from the digest and rebuild lazily. Step C otherwise: keep the entries that were consulted in the current year but store them compactly (for example the `sd` and rating only, 2 floats, or a float32 array) rather than as Python dict entries, and age out `_judged`. Expected `_views` saving is small (12-18 MB resident) compared with the memo, so it is the second priority.
4. **Unexplained resident growth**: measure with `tracemalloc` and `gc` statistics on an isolated runner copy before claiming anything.

## 4. What must not change (the proof obligations)

On an isolated copy of the event-141 league (never the live files), run the **old code** and the **new code** from the same snapshot through the same events (the cap is a service setting; on a copy it can be run as far as needed, at about 70-80 s per regular week) and require:

1. **Decisions and results identical**: every journal row's `n_records`, `records_digest` and the archive batch digests equal; every game's score and trace hash equal; contracts, trades, signings, draft picks and releases equal.
2. **State identical under one common digest.** The current digest includes `_views`/`_judged`. A run with the optimization cannot reproduce the old digest when the caches are removed. So both runs are digested a second time with `neutral.DIGEST_IGNORE + ("_views", "_judged")` and those digests must be equal event by event. The old runner's committed digests stay as the record of the first 141 events and are not rewritten.
3. **Random states identical**: after every event, `random.Random.getstate()` of the universe's stateful stream (and any other stateful `Random` found by walking the universe) is equal; additionally a test that a rebuilt `view()` consumes no draw from it (true by construction, to be asserted).
4. **Cache equivalence tests**: (a) a restored snapshot with the cache emptied produces the same `release_charges` as the in-memory cache for every contract, every key asked; (b) the `_views` instrumentation from option 3A reports zero differences, or the exact list of divergent inputs.
5. **Across restart**: kill and restart the isolated runner at random points; the finished chain equals the uninterrupted one (the existing runner crash tests already do this for the unoptimized code).
6. **Old saves still load** (or the migration is explicit, see section 6).

## 5. Measurements before and after (same copy, same events, same machine, league otherwise idle)

For events 1 to 47 as a short pass and then 94 to 141 (the heaviest) of an isolated copy: runner resident and peak (already in `events.jsonl`: `vmrss_mb`, `maxrss_mb`); fresh-process load cost per snapshot (`analyze_memory.py snap`); snapshot size (compressed and pickled, `snapshots.size`); WAL size at the end of each event; per-event seconds split into engine time and serialize/digest/commit time (add timers around `persist.dumps` and the digest); entries and bytes of each cache. Report every number as before / after / difference with units (MB or MiB stated, files at rest vs write-ahead log stated separately, as in the corrected disk table).

## 6. Consequences and decisions that belong to the owner

- Any change under `src/dfl` or `data/` changes the **code fingerprint**, and a league file embeds the code that wrote it and **refuses to open under different code**. The live league cannot simply be upgraded. Options: (a) keep the trial league on the current code and apply the optimization only to the next league or new development league; (b) a deliberate, owner-approved migration through `persist.export`/`import` with `accept_code_change` (a new code epoch, with the neutral state document checked by deep digest). Recommendation: (a) for the trial league, because it is stopped and held for review.
- Excluding `_views` from the digest is a **digest-algorithm change** (`neutral.DIGEST_VERSION`); it breaks comparability with the 141 committed digests unless done at a new epoch.
- The tools and site can continue to change without this concern; they are outside the fingerprint.

## 7. Staging

1. Instrumentation only (no behaviour change): the `_views` check, per-event timers, `tracemalloc` runs, on an isolated copy.
2. Option 1 and 2 on the isolated copy; proofs 1-5; measurements.
3. Review the numbers with the owner; decide section 6.
4. Option 3 only if step 1 shows it is safe.
5. Nothing touches `/srv/dflv2/league`, the runner service, or `DFL_UNTIL`.

## 8. Result of the isolated trial of options 1 and 2 (2026-11-02)
Branch `experiment/contract-memo-cache` (not merged, not deployed, no league on the server touched). Change: `Contract.release_charges` drops earlier weeks of a year once a later week of that year is asked (at most covered years x 4 flag combinations stay); `__getstate__`/`__setstate__` keep the memo out of every pickle and discard one read from an older snapshot. Tests: `tests/test_contract_memo.py` (6 tests; 3 fail on the old code).

Method (`tools/memo_experiment.py`): the same real league (seed 7, play-level, no spatial engine, a snapshot after every event) created from scratch under the old tree (47e12e6) and the new tree and run for 141 events, three seasons, in two processes at the same time on one machine. A league file is bound to the code that wrote it, so each run starts a new league; no existing league is opened.

**Unchanged (proved on all 141 events):** the journal's state digest, hash chain, archive record count and records digest, the event key and the state of the league's random stream are equal event by event (7 fields x 141); the archive's content digest at the end is equal; the states of all six random generators reachable from the league state (by name) are equal. The state digest never contained the memo (`neutral.EXCLUDED`), which is why it can be equal. Not covered here: the visible-estimate cache `_views` (untouched), a restart across the two code versions (impossible by design: different code fingerprints), and the server's league (not run at all).

**Measured (old -> new; small local league, so use the ratios, not the absolute numbers):**
| event | stored snapshot (compressed bytes) | resident MB | peak MB |
|---|---|---|---|
| 47 | 1,947,820 -> 1,816,503 | 152.7 -> 123.5 | 196.7 -> 176.0 |
| 94 | 2,605,228 -> 2,286,381 | 211.5 -> 147.5 | 290.4 -> 199.9 |
| 141 | 3,183,816 -> 2,720,894 | 283.4 -> 163.8 | 333.6 -> 221.7 |

Memo entries held (all contracts): event 50 87,989 -> 13,891; event 90 167,942 -> 18,277; event 141 249,147 -> 22,851; largest memo on one contract 94 -> 7. Resident memory grew 131 MB over the three seasons before and 40 MB after. Event time over the three seasons (summed seconds; the two runs shared one machine, so treat as indicative): 213.2 s -> 184.9 s; season 3 alone 85.3 s -> 69.6 s. There was no slowdown from recomputing, because the memo only matters within one week.

What this does not show: that the server's real league (about 2-3x larger contract counts, spatial engine) behaves the same; that is to be measured by loading the server copy's event-141 snapshot under the new code in a fresh process (`analyze_memory.py snap`, read-only) and by running further events on a scratch copy. Decisions still open for the owner are in section 6: any adoption changes the code fingerprint, so it belongs to the next league or an explicit migration.

## 9. The same change on the real event-141 snapshot (server scratch copy, read-only, 2026-11-02)
Source: the scratch restore of the verified backup (`/root/offsite-restore-test`, never the live league), event-141 snapshot, loaded in fresh processes, `nice`d, one process at a time. Old tree = be05f57, new tree = f6d4c08 (this branch), both as side checkouts on the Droplet; nothing deployed, the service untouched.
| | old code | new code |
|---|---|---|
| state digest of the loaded snapshot equals the journal's digest at event 141 | yes | **yes** (same digest, so the stored chain is still consistent) |
| snapshot rewritten by that code, compressed | 3,246,559 B | 2,767,463 B (-14.8%) |
| fresh process, resident after loading and a garbage collection | 200.8 MB (peak 213.9) | 115.5 MB (peak 123.8): -85 MB, -42% |
| objects alive after load (`analyze_memory.py`) | 447,274 | 126,697 |
Caution found on the way: loading a snapshot *written by the old code* with the new code frees the memo but not the memory (resident after load 203 MB, as before: the process had already allocated the memo while reading). The saving appears once snapshots are written by the new code, which is the case for any new league.
The runner's resident size in the event log (277.7 MB at event 141, against 165.3 MB for a fresh load by the earlier measurement) is still higher than any fresh-load figure; part of that gap, and the rest of the unexplained growth, remains unmeasured here.

## 10. Acceptance checks asked for before adopting the change (2026-11-02)
**Which engine powered the first 141-event comparison (section 8): not the 22-player engine.** `tools/memo_experiment.py` created that league with `spatial=False`, i.e. the older play-by-play engine (fast). The trial league and the next league use the 22-player spatial engine (rule version `engine-1.2 (Ed 8.52; play-level and 22-player spatial)`). So the same 141-event comparison was repeated with `--spatial 1` (the harness option added for this): the same real league (seed 7, snapshot after every event) created from scratch under the old tree (47e12e6) and under this branch, two processes at the same time, 141 events, three seasons.
**Unchanged, spatial engine (all 141 events):** state digest, hash chain, archive record count and records digest, event key and random-stream state, equal event by event (7 fields x 141); the archive content digest at the end is equal; the states of all six random generators reachable from the league state are equal (`memo_experiment.py diff`: IDENTICAL; log kept in the session, not in the repository).
**Measured, spatial engine (old -> new):**
| event | stored snapshot (compressed bytes) | resident MB | peak MB |
|---|---|---|---|
| 47 | 1,951,713 -> 1,820,128 | 179.6 -> 153.7 | 218.2 -> 191.9 |
| 94 | 2,629,909 -> 2,317,494 | 231.6 -> 178.9 | 313.1 -> 233.7 |
| 141 | 3,229,011 -> 2,766,689 | 283.3 -> 193.7 | 354.2 -> 254.5 |
Memo entries held: event 50 88,061 -> 13,906; event 90 168,210 -> 18,425; event 140 249,624 -> 23,246; largest memo on one contract 94 -> 7. Total event time over 141 events 2,596.6 s -> 2,577.1 s (games dominate under the spatial engine, so no speed claim is made; the saving is memory and snapshot size).
**Explicit tests (not loosened):** `tests/test_cap_tools.py::test_memory_report` requires 0 contracts with a cache after a snapshot is loaded in a fresh process and equal pickle sizes with and without the cache. `tests/test_contract_memo.py::LoadedLeague` loads a real saved league (restart path: snapshot, journal replayed) and checks: every contract cache is empty; the caches rebuild on the first question, stay within covered years x 4, and every answer equals the plain computation; every answer (all years, every third week, all four flag combinations, every contract of the league) equals the answer of the code as it was before the change (fixture `tests/fixtures/contracts_before_memo.py`); a later save carries no cache and the league digest is unchanged. These fail on the old code (checked: 5 failures and 1 error there).
**Upgrade of a copy of the existing trial league:** section 11; real-data result: a scratch copy of the verified backup (141 events) upgraded under this branch's code in about 30 s (`state_changed: false`, same hash chain, `compat` exact, `verify --deep` ok), then 6 further events (offseason_open through draft_round_1) equal, row for row, the same 6 events fired by a second copy under the old code. The copies are byte-identical to the originals, which were not opened. Only offseason events were covered; no game week was run past event 141 on the real copy.

## 11. Moving an existing league to the cache change: the upgrade path (documented and tested, 2026-11-02)
The code fingerprint covers `src/dfl` and `data`, so the memo change gives a new code identity (`efaa71678327...`; the trial league was written by `a8fc26342e9f...`, the same as every deployed revision so far: the only modified file is `src/dfl/ledger/contracts.py`). The new code **refuses** the old file (`CodeMismatch`, message names the upgrade); nothing in the old file or its archive is rewritten by the refusal. History is not lost, because the repository already has an explicit upgrade (`persist.upgrade`, command `tools/league_store.py upgrade OLD NEW --run-originating-code [--archive FILE]`):
1. the old file carries the exact code that wrote it (its `code` table); the league is exported under that code, as a neutral state document, in a separate process (this runs code from the file, so it must be asked for);
2. the document is imported under the new code into a **new file**; the rebuilt state is compared by value with the digest taken at export (`state_changed: false` is the expected result), and the new file continues the old file's sequence numbers and hash chain, and records in its lineage the old fingerprint, event number, chain value and the SHA-256 of the old file;
3. the old file is hashed before and after and is never written.
The archive is the one thing the new league keeps writing to (it is permanent history and each code epoch is recorded in it). So the upgrade is always done **in a copy of the league directory**, never in the live one:
1. have a verified backup (`league_backup.py create/verify`) and the league stopped and paused;
2. copy `league.db` and its archive into a new directory; hash the copies against the originals;
3. with the new code at a pinned commit: `league_store.py upgrade COPY/league.db COPY/league_v2.db --run-originating-code --archive COPY/archive` (the new league file refers to that archive copy);
4. check: `compat` says `code: exact`; `verify --deep` says `ok`; the new file's lineage names the old fingerprint, event 141 and the old chain value; `cap_state.py fingerprint` of the copy shows 141 events and the same chain;
5. the next capped run uses the upgraded copy; the original directory stays exactly as it is, as the record of the three-season trial (and the verified off-server backup remains the second copy).
**Tested:** (a) `tests/test_contract_memo_upgrade.py` writes a league with the code as it was before this change (fixture `tests/fixtures/contracts_before_memo.py`, whose snapshot carries the full caches), shows the new code refuses it, upgrades a copy, and checks: original files byte-identical; state equal by value; hash chain continued; every cache empty after the upgrade; archive history unchanged by the move and both code epochs recorded; deep verification clean; and the next 10 events of the upgraded league equal, row for row (event key, state digest, hash chain, archive record count and records digest), the same 10 events fired by the league left on the old code. (b) The same procedure on the real trial league (server scratch copy of the verified backup, 141 events, 22-player engine, rule version engine-1.2): see section 10.

## 12. The deployed release, and the one-season continuation on the server (2026-11-02 / 03)

**What was deployed.** Not the experiment commit but a pinned release, `7e59cbbaea23b0de353545c64a6f5c39cffe774d` (branch `release/engine-memo-2026-11-02`): the previously deployed tools release `59ea680` plus only the contract-release cache change and its compatibility work. `src/dfl` and `data` are byte-identical to this experiment commit (`0f084fe`), so both have the same code fingerprint (`efaa71678327...`). They differ in everything outside the engine: the release does **not** contain the replay viewer and its exporter, verifier, demo builder and tests, `site/assets/playback.*`, `config.js`, `src/dflsite/playback.py`, `tools/memo_experiment.py`, the Caddy playback routes, or this document. The release adds the compatibility tests (`tests/test_contract_memo_upgrade.py`, the explicit `test_cap_tools` memory-report checks, `tests/fixtures/contracts_before_memo.py`). The full suite was run on `0f084fe` (889 tests OK); the release was verified with the 20 focused test files that cover everything it changes or that depends on the league file, publishing and deployment (362 tests, exit 0).

**Procedure used** (section 11): stop runner and exporter; fresh verified backup; upgrade a copy into a separate private directory; check preservation (141 events and journal rows, hash chain `731a6f68...`, journal and deep state digests, all 6 random-number generators, archive digest `d65bbce9...`, archive verify clean); deploy the pinned release (all checks matched); rename the directories (original kept as `league-141-original`, upgraded copy became `league`); cap 188, pace 900 s; one runner and one exporter. The runner stopped by itself at event 188 (11:48 PM PST).

**Result (events 142-188, new code, compared with events 95-141, old code, same position in the season, same event keys):**

| | old code (season 3) | new code (season 4) |
|---|---|---|
| resident memory per event, mean / max | 243.8 / 278.2 MB | 205.2 / 249.1 MB |
| peak per event (maxrss), mean / max | 312.1 / 325.9 MB | 277.6 / 307.1 MB |
| game weeks (19): seconds, mean / max | 68.4 / 79.3 | 70.1 / 77.1 |
| league size logged at season end (database + WAL + shm; see section 13.3) | 27.81 MB | 25.96 MB |
| export (publish) seconds, mean / max | 1.71 / 3.13 | 1.12 / 2.02 |
| exporter peak memory, mean / max | 201 / 244 MB | 164 / 176 MB |

Start gaps were exactly 900.0 s for all 46 intervals, no restarts, service peak 439 MB (limit 1200 MB). The archive grew from 423.5 MB to 565.2 MB (1,200 to 1,600 games, 141,978 to 189,201 traces) and verifies clean; `verify` of the league file is clean.

**Reading these numbers honestly.** The saving is real but smaller in a running league than in the earlier laboratory measurements (those measured a freshly loaded league; resident 283 to 194 MB). In a running league the game-week working set dominates the peak (peak down 6%, resident down 16% on average). The comparison is not perfectly like-for-like: the new season is one season later in history, and the upgraded process started from a restart (144.9 MB at its first event, against about 222 MB for the old process at the same point), so memory grew more during the new season (+90 MB) than in the old one (+55 MB) and the gap narrowed through the year, from about -80 MB at the start to about -43 MB at the year close. Whether the gap would keep narrowing over more seasons is not known. The upgraded league file started much smaller (4.7 MB, against 23.6 MB, as logged) and grew to 25.96 MB by the end of the season; section 13.3 explains this from measurements (it is mostly the number of snapshots kept, plus the write-ahead log that the logged figure includes).

## 13. Decision record, and why the upgraded league file started small (2026-11-03)

### 13.1 Decision record

**Accepted: the contract-release cache optimization**, as deployed in release `7e59cbb` (section 12), is the league's engine code from event 141 on. The grounds, all measured: with the 22-player engine the state digest, hash chain, archive records and digest, event keys and random-stream state were equal event by event over 141 events (section 10); the existing league was moved by the documented upgrade with no change of state (section 11); the 47 live events 142-188 ran under the new code with exact pacing, no restart, a clean archive verification and a clean `verify` of the league file (section 12); stored snapshots are smaller (11,353,036 against 13,852,982 bytes uncompressed at event 141, section 13.3) and measured memory in the live season was lower than in the same season of the previous year (section 12).

**Unresolved: long-run memory growth.** What is known is one season. Under the new code the process was at 144.9 MB resident at its first event (a freshly started process) and 234.5 MB at the year close, +90 MB over the season (peak 307.1 MB, 1,322 MB of the machine's memory available at the end); under the old code the previous season grew +55 MB from a higher start. The gap to the old code narrowed from about -80 MB to about -43 MB during the season. The comparison is not like-for-like (a later season, and a restarted process against a long-running one), so it cannot say whether growth levels off, continues, or is just the start-up effect of a fresh process. No defect has been found and none has been ruled out.

**How it will be settled: not on the live league.** The live trial is not to be extended solely to investigate memory; further seasons are played only for the league's own purposes, if the owner decides to. The question is answered with isolated comparisons from the same starting state, run on scratch copies, never touching `/srv/dflv2/league`:

1. *Same start, one variable.* Start A: the event-141 file kept as `league-141-original`, run under the old code. Start B: an upgrade of a copy of that same file (section 11; `state_changed: false`), run under the new code. Both begin from the same state digest at event 141. A second pair can start from the event-188 file to test later history.
2. *Same work.* Each arm runs the league's own 22-player engine through the same events with `tools/league_runner.py FILE --pace 0 --until-events N`, in its own scratch directory, one arm at a time on the same otherwise idle machine, for at least three seasons (N = 141 + 3 x 47 = 282). At pace 0 a season is a game-week time of about 70 s x 19 plus the rest, roughly half an hour of compute per arm per season.
3. *Same measurements.* Per event, `events.jsonl` (resident `vmrss_mb`, peak `maxrss_mb`, available memory, seconds); at each year-end checkpoint `tools/analyze_memory.py table`, which loads the snapshot in a fresh process, so "resident of the long-running process minus a fresh load of the same state" separates what the process accumulates from what the league holds. The decision fields (state digest, chain, archive records) must be equal between arms at every event; a difference there is a bug, not a memory result.
4. *Noise and the restart effect.* Each arm is run twice, alternating order. A third arm runs the new code but restarts the runner at every year-end checkpoint, which separates process age from league history.
5. *Reading.* The question is the slope of resident memory against events for the same event keys. Only a positive slope that is larger under the new code than under the old would be a regression. The result goes into this document before any conclusion is drawn.

### 13.2 What the league file holds

The league file (`league.db`) is a SQLite file in write-ahead-log (WAL) mode with four tables: `snapshots` (zlib level 3 compressed pickles of the whole league state, one per kept event), `journal` (one small row per event: digest and hash chain), `code` (the source code that wrote the file) and `meta`. Snapshots are the only large part. The policy (`src/dfl/persist.py`): the newest 3 ordinary snapshots are kept, plus every year-end snapshot ("checkpoint") for ever. The permanent record of games lives in the separate archive file (`league.db.archive`), which is not part of any size below.

### 13.3 Why the upgraded file started small, and why it grew

Measured read-only on the server (SQLite opened `immutable=1`, nothing written, nothing copied; league stopped at 188):

| | original (event 141, old code) | upgraded at creation (event 141) | upgraded now (event 188) |
|---|---|---|---|
| file bytes | 19,513,344 | 3,248,128 | 17,518,592 |
| pages x 4096 / free pages | 4,764 / 67 (274,432 B) | | 4,277 / 63 (258,048 B) |
| snapshots kept | 7: checkpoints 0, 47, 94, 141; ordinary 138, 139, 140 | 1: checkpoint 141 | 5: checkpoints 141, 188; ordinary 185, 186, 187 |
| snapshot bytes (compressed, all kept) | 18,920,080 (97% of the file) | about 2,984,628 | 16,966,223 (97%) |
| newest snapshot, compressed / uncompressed | 3,248,437 / 13,852,982 | 2,984,628 / 11,353,036 | 3,293,705 / 11,455,098 |
| journal rows | 141 (seq 1-141) | none | 47 (seq 142-188) |
| code table | 1 fingerprint `a8fc2634...`, 227,516 B zip | 1 fingerprint `efaa7167...` | same |
| `-wal` / `-shm` | 0 B / 32,768 B | | 0 B / 32,768 B |

**Where the 16,265,216 bytes went** (19,513,344 to 3,248,128):

| cause | bytes | share |
|---|---|---|
| snapshot count: 6 of the 7 snapshots are not carried into the new file (0, 47, 94, 138, 139, 140); only the base checkpoint 141 is | 15,671,643 | 96.4% |
| cache removal: the carried snapshot 141 is 18.0% smaller uncompressed and 8.1% smaller compressed (263,809 B) | 263,809 | 1.6% |
| database free pages: the 67 free pages of the old file are not copied into a new file | 274,432 | 1.7% |
| journal: the 141 old rows stay in the old file (the new file's chain starts from its base) | 36,864 | 0.2% |
| remainder (page rounding and small tables) | 18,468 | 0.1% |

*Compression* is not a cause: both files use the same level (zlib 3). Cache removal does change the ratio (compressed/uncompressed 23.4% with the caches, 26.3% without, because the caches compress well), which is why removing them saves 18% before and only 8% after compression.

**Why it then grew to 17.5 MB:** the new file keeps filling the same policy: from 1 snapshot to 5 (3 rotating ordinary ones and 2 checkpoints, about 3.3 MB each), plus 47 journal rows (16,384 B). In steady state it grows by about one snapshot (about 3.3 MB) per season for the year-end checkpoint, plus the journal. The original file would have shown the same shape; at event 141 it had simply accumulated 4 checkpoints and 3 ordinary snapshots.

**WAL and checkpoint state.** Both files are in WAL mode (file header bytes 18-19 are `02 02`; `PRAGMA journal_mode` reports `delete` through an immutable connection, so the header is the evidence). Both `-wal` files are 0 bytes now, i.e. fully checkpointed, and a 32,768 B `-shm` index remains. The runner's `league_bytes` in `events.jsonl` is the size of database plus `-wal` plus `-shm` (`tools/league_runner.py`), and the WAL is transient, so the logged figure is larger than what the file occupies: at event 188 it logged 25,960,312 bytes, against 17,518,592 bytes on disk (+ 32,768 `-shm`), so about 8.4 MB of the logged figure was write-ahead log at that moment; in the previous season the same arithmetic gives 27.81 MB logged against 19.51 MB on disk, about 8.3 MB. This is arithmetic from two measurements, not a direct reading of the WAL (it was emptied when the runner stopped). The like-for-like on-disk sizes at the season end are therefore 19.51 MB (old code, event 141) and 17.52 MB (new code, event 188), 10% smaller; the early logged value (4.7 MB at event 142 against 23.6 MB) compares a 3.25 MB database with a database that held 7 snapshots.

### 13.4 Preserved content (a small file is not evidence of lost data; this is the evidence)

| check | result |
|---|---|
| original file unchanged | `league-141-original/league.db` is 19,513,344 B, last modified 2026-11-01 23:08 (before the upgrade); its archive is 423,510,016 B, modified at the same time; SHA-256 of the file now `6dabe5c3bd133f3296b6cb57eebf0959fb44f485b995cfa08221026c565290c8` (the baseline for any later comparison) |
| the new file continues the old chain | its base is seq 141 with chain `731a6f68...`, which is the old file's head at 141 (old digest `02c2858e...`); one lineage entry; archive base through 141 |
| archive: nothing lost or changed | batches 96 to 128, games 1,200 to 1,600, records 3 to 4, traces 141,978 to 189,201. Rows of the original archive that are missing or different in the new one (compared on game/record/batch identity, season, sequence, kind, clubs, SHA-256 and size): 0 batches, 0 games, 0 records, 0 traces. Games with sequence <= 141 that the new archive has and the original has not: 0. Games added after 141: 400. |
| journal | new file holds rows 142-188 (47); rows 1-141 are in the old file, 141 rows, head 141 |

**What this does not show.** The comparison reads the stored SHA-256 and size columns, not the payload bytes; payload integrity is what `archive verify` checks, and it was clean after the run (section 12). And one thing really is not in the new file: the earlier restore points (checkpoints 0, 47, 94) and journal rows 1-141 exist only in the old file. The new file alone cannot replay the league from the beginning; the old file plus the archive can. So `league-141-original` is part of the league's record and must be kept and backed up with it.
