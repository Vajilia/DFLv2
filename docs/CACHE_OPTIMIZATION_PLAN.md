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
