# Independent-review handoff (checkpoint `9211b28`, updated for the persistence verification work)

Written 2026-10-29 for an independent reviewer; updated the same day after the owner's instruction to finish the persistence verification before Step 7 (sections 3 and 4 are new, sections 0, 2, 5 and 7 are corrected). Repository `Vajilia/DFLv2`, branch `audit/completion-plan`. **Nothing is merged or deployed.** This supersedes the status lines of `docs/REVIEW_HANDOFF_STEPS_3_4.md` (whose architecture section is still accurate and should be read with this).

## 0. Status in one paragraph

Saving, restart recovery, backup/restore, the upgrade path, the permanent archive and archive consistency (Steps 3 and 4) are built and tested, **and the persistence verification the owner asked for is done** (section 3): the journal's state check is now complete (all six random generators and everything else reachable from the universe, with a short list of excluded caches, each with its reason), parity-profile settings belong to the league instead of the process, development saves (schema 3) are refused clearly and can be carried forward, and regression tests cover each. One item stays **OPEN**: the single unexplained `StateMismatch` in the first full test run (section 2). A *defect* that could produce exactly that failure was found, reproduced and fixed; **that it caused the original failure is not proved**, because the original run was not instrumented. So **persistence is not described as fully verified.** Several earlier verification claims were wrong or weaker than stated; they are corrected in section 4. The Bowl's ballots and final-53 construction are **incomplete** (not built, not fully specified). Live-clock pacing is deferred. Step 7 (roster, contract and transaction verification) is **done** (section 10; `docs/STEP7_LEDGER_VERIFICATION.md`): it found and fixed five defects, one of them a second, separate source of run-to-run differences (section 10, item 5). The owner's next step after 7 is next.

## 1. What is at `9211b28` and what came after

| Commit | Content |
| --- | --- |
| `1317f3b` | Step 3: durable saving, restart recovery, atomic event processing, backup/restore |
| `dd9ef8e` | C11 save-schema/code separation and upgrade path; Step 4 permanent archive |
| `a1e0fe7` | Archive consistency at open (save schema 3) |
| `f49ede1` | Step 5: season controller and calendar verification; stale game dates after Season 1 fixed |
| `c87c6aa` | Rulings document: day counts |
| **`9211b28`** | **Owner rulings of 2026-10-29 (items 4, 8, 9, 14; spec Ed 8.53): ledger-kept trading window and its saved-state compatibility, emergency list for every position, one season draw priority; Step 6 schedule and rotation tests** |
| `030b51a` | Digest address-reuse defect fixed and failure diagnostics (section 2), Bowl marked incomplete (Ed 8.54), worked rotation example, first version of this handoff |
| after `030b51a` (`git log 030b51a..HEAD`) | Persistence verification work: complete journal digest and save schema 4, league-owned parity settings, schema-3 compatibility and upgrade, regression tests, corrected claims (sections 3 and 4) |

Review the checkpoint with `git diff 4232e22 9211b28 -- src tools tests docs`. Review what followed with `git diff 9211b28 HEAD`. A reviewer who wants the failure as it stood should check out `9211b28` and run `tests/test_digest.py` from the later commit against it (four of its six tests fail there).

## 2. The open item: intermittent upgrade/import digest mismatch

**Observed.** One full run (`python3 -I -m unittest discover -s tests`, 470 tests, random hash seed, 2,159 s): `test_upgrade.RoundTrip.test_other_ways_an_import_is_refused` raised `StateMismatch: ... state digest 48a454e22593 against 3cc48c8e1d54`. The assertion is that a state decoded from an export has the same exhaustive digest as the state exported. It passed alone, under 19 hash seeds, together with the new test modules before it, and in a second full run (483 tests). No report or log of the failing process exists beyond that message.

**Mechanism found (reproduced).** `neutral.deep_digest` memoised by object address and let temporaries die: `Random.getstate()` returns a new tuple; after it was freed the next generator's tuple could occupy the same address and inherit its digest. Evidence:
- Blindness: two lists holding different generators digest equal; advancing five of the six generators of a real league left the digest unchanged (`tests/test_digest.py`, four failures on `9211b28`).
- Instability: decoding one exported document 200 times under artificial heap fragmentation gave 10 digests different from the original on `9211b28` and 0 after the fix (90 more copies across other fragmentation seeds: 2 and 0). Reproduce with `python3 -I tools/diag_digest_stability.py --iterations 200 --seed 4` (add `--src <older checkout>/src` to run it against `9211b28`); `tests/test_digest.py::DoesNotDependOnMemoryLayout` is the synthetic version that always runs.

**Two different things, kept apart.**
- *The reproduced digest defect:* demonstrated. It was reproduced on purpose (blindness: five of six generators invisible to the digest; instability: 10 of 200 identical decodes digested differently under artificial heap fragmentation), it is fixed, and regression tests fail on `9211b28` and pass after.
- *The original uninstrumented failure:* one `StateMismatch` in one run, with nothing recorded but the message. That the defect caused it is **not proved**. The defect can produce a failure of exactly that form, and it is the only mechanism found that can, but the failure was never reproduced and no evidence ties it to this mechanism. Equally, nothing contradicts it. Until it recurs with diagnostics, or a reviewer shows otherwise, the item stays OPEN.

**Diagnostics now in place (after `9211b28`).** On any import refusal `persist.mismatch_report` records expected and actual digests; digest of each part of the league (two levels) and each generator's state on both sides, with the differing names; document differences against a fresh encoding; three fresh decodes (`digest_is_unstable` if identical input digests differently); code fingerprint, Python, numpy, SQLite, zlib versions; hash seed and randomisation flag, pid, cwd; the tests run before it in order (`tests/_diag.py`) and module-level settings (`R.CAP`, parity profile, production cache, `random` state, temp dir). `DFL_DIAG_DIR=<dir>` writes it as JSON; it is attached to the exception as `.diagnostics`.

**What the defect weakened, and what the follow-up work found.** See section 4 for the full list of corrected claims. In short: the exhaustive digest was not the guarantee it was described as (before the fix); and the journal's own digest, which the earlier handoff listed as a question ("is covering two of six generators acceptable?"), was **not** acceptable and is replaced (section 3).

**Other checks done.** Temporary files: every relevant test uses its own `mkdtemp`; no collisions found. Unordered iteration: persistence code sorts what it hashes or writes; no use of `hash()`; the new digest also treats dict order as state and was checked under four hash seeds. Repeat testing was bounded (above). **Shared mutable state: the earlier text here called the process-wide parity profile "harmless for the shipped profile, latent for the test beds; not changed". That was an understatement. It was a real defect and is fixed (section 3.2).**

**To close.** Reviewer confirms mechanism and fix; the full suite and restore soaks pass on the fixed code (results in section 4); no recurrence in later runs.

## 3. Persistence verification work (after `030b51a`)

### 3.1 The journal's state check is complete (save schema 4)
- `persist.journal_digest(u)` is now `neutral.deep_digest(u)`: one digest over everything reachable from the universe, taken after every event and checked by replay. It replaces `quick_digest`, which covered the ledger (eight fields per player), the cursors, **two of the six generators** (`u.rng`, `world.rng`) and a few books.
- All six generators: `u.rng`, `world.rng`, `dev_rng`, `soul_rng`, `coach_rng`, `gov_rng`, each by complete state, each at its own place in the graph (exchanging two generators' states is noticed).
- Everything else: all player fields, contracts, character cards, club estimates (`_views`, `_judged`, which are state: they are frozen for the year at first use), pools, governance, logs, the season run, the resolver and squads. Dicts and lists count in order; sets as sets.
- **Exclusions, documented in `neutral.EXCLUDED` with reasons and tested both ways:** `_units`, `_memo` (recomputable caches), `dir` (machine path; the tables are under the code fingerprint), `archive_buffer`, `archive` (the per-event hand-off, covered by the journal's record count and digest and the archive chain). Outside the universe: code and data (fingerprint), the archive (its own chain), seed and construction arguments (`meta`). Wording in `docs/PERSISTENCE.md`, "The state digest".
- Cost: about 0.3 to 0.6 s per event on the placeholder engine; chosen over a cheaper partial check on purpose. A tiered scheme (full only at snapshots) was considered and rejected because it would again leave most events checked in part.
- Versioned: `persist.FORMAT_DIGESTS` ties each save schema to its digest algorithm (`neutral.DIGEST_VERSION` = 2), asserted at import; files record `meta.digest_algorithm`; state documents are version 2 and carry `digest_algorithm` (version 1 documents are refused by name).

### 3.2 Parity settings belong to the league
- Defect, shown: in the tree at `9211b28`, building a second league on another profile changed the first league's state **from its fifth event** (`python3 -I tools/diag_profile_leak.py --src <checkout at 9211b28>/src --tables <checkout>/data/season1` exits 1: the first league is altered from event 5, free-agency opening; on the current code it exits 0). Separately, a league created under a non-default profile could not be recovered in a fresh process by its own code: replay raised `ReplayDivergence` at event 11 (observed while preparing the schema-3 upgrade test).
- Fix: `parity.settings(name)` only returns values; the cap is `Ledger.cap`, the returner rule is `Universe.returner_block` / `SeasonRun.returner_block`, the priority clubs `World.priority`. `rules.CAP`, `draft.RETURNER_BLOCK`, `parity.apply` and `parity.current` no longer exist as switches. A full audit of module-level state found only read-only data tables (parameters, spatial, analytics, character tables) and `persist.DIAG_CONTEXT`.
- Tests (`tests/test_parity.py`): two differently configured leagues (shipped rules and `rigged`) stepped alternately in one process equal their isolated runs by digest at every third event for 30 events (the other league created *after* the first, the order that broke the old switch); restoring one league from its file while the other runs changes neither; state written before the settings were league-owned is given the ones its profile named.

### 3.3 Development saves
- Schema 3 (this repository up to `030b51a`) is **refused** by `Runner.open` with a message that says what it is, why (partial digest, algorithm 1 against 2), that it is not damaged or modified, and the command that carries it forward. `compat` reports `upgradable`; `status` and `verify` still read it.
- `upgrade OLD NEW --run-originating-code` carries it forward. The worker applies the file's parity profile before opening it under its own code (that code did not), the import fills `cap` and `returner_block` by adapter from the profile name, those names are left out of the comparison and recorded in the lineage (`adapted_attributes`), and everything else must equal the exporter's digest exactly. `tests/test_format3_upgrade.py` uses the exact source of `030b51a` (vendored as `tests/fixtures/schema3_code_030b51a.tar.gz`, hash pinned) to write a genuine schema-3 league on the `none` profile, refuse it, upgrade it without `accept_state_change`, and continue it 6 events equal to an unbroken run; a changed player, a changed generator word, and version-1 documents are still refused.

### 3.4 Regression tests added (`tests/test_state_digest.py`, 19 tests)
- Advancing each of the six generators changes the digest and changes only that generator's part; every kind of draw (including the cached Gaussian) is seen; exchanging two generators' states is seen; restoring the state restores the digest.
- Every attribute of the universe, world, ledger, resolver and season run is either in the digest or excluded with a reason (the test replaces each in turn with a probe; 80+ attributes).
- Independently decoded copies, a JSON round trip, an unpickled copy, a league rebuilt from its seed and a league built in another process under another hash seed all digest equal; one changed value anywhere does not.
- Replay refuses a snapshot whose `dev_rng`, `soul_rng`, `coach_rng` or `gov_rng` is one draw off (each of the four the old digest could not see).

## 4. Corrections to earlier verification claims

| Earlier claim (where) | What was true | Status |
| --- | --- | --- |
| "Journal digests identical at all 49 events" after a kill and restore (restore soaks, `test_persist`, archive tests, the Steps 3 to 4 handoff) | The journal digest then in use covered two of six generators and part of the world. Identical digests did not show identical state. State was also compared by other means (leagues played on identically, `test_upgrade` compared generators by value), which is why nothing was seen to go wrong, but the stated check was weaker than stated. | Superseded: re-run under schema 4 (section 6). |
| "The exhaustive digest was identical" (restore soaks, upgrade, archive restore) | Until `030b51a` the digest could not see most generators and was unstable for identical input (address-reuse defect). | Fixed in `030b51a`; stronger again in schema 4 (dicts and lists in order, `_memo` excluded). |
| Parity profile "harmless for the shipped profile, latent for test beds; not changed" (plan section 15, this handoff) | A real defect: a second league could change the first; a restored non-default league could not be replayed. | Fixed (3.2). |
| "`persist.quick_digest`: is covering two of six acceptable?" (a reviewer question) | Not acceptable; no reviewer answer was needed. | Replaced (3.1). |
| "Restoring a league is exact" for leagues on `none` or `rigged` | Not established by anything before 3.2; fairness runs used one profile per process and never restored. | Established for schema 4 by the tests above. |
| "Unordered iteration: no code iterates a set where order changes results" (section 2, "Other checks done"; hash seeds 1 to 19) | True of the persistence path only. In the simulation, trade proposals ordered a club's picks with a key that ties (same round and year, different original club), so the set's order decided; found 2026-10-30 by the 22-player restore soak and fixed (section 10, item 5). A restored or resumed league could have differed from an uninterrupted one in that way. It cannot explain the original failure, which compared a decoded state with the state exported (no event is stepped). | Fixed; regression test `tests/test_hash_order.py`. |
| The original `StateMismatch` was "explained" by the digest defect (wording in places) | Mechanism demonstrated; attribution to that failure **unproven**. | OPEN (section 2). |

Not wrong, but worth stating: the earlier validator, ledger soak (`9fdf159d…`) and the play-level results did not depend on any digest changed here.

## 5. Rulings of 2026-10-29 as implemented

Details in `docs/OWNER_RULINGS_NEEDED.md` and `PROJECT_COMPLETION_PLAN.md` section 13; spec Ed 8.53 (Ed 8.54 adds only the Bowl-incomplete marker).
- Item 4 (Bowl): replacement and ring rules written; **INCOMPLETE: ballots and final-53 construction not built and not fully specified**; the Bowl plays composite All-Star sides.
- Item 8 (rosters): values confirmed; inactive players replace injured players only before the roster locks; emergency list covers every position, adapts as injuries happen, no player in two positions; **the out-of-position penalty is not built**.
- Item 9 (standings): one seeded priority per club per season in every scope; draft is the exact reverse.
- Item 14 (calendar): dates approved; year close and 1 January gap stated correctly; **the ledger itself keeps the trading window**; older saved state is settled from the fired events and an upgrade across it requires `accept_state_change`.
- Step 6: rotation and schedules verified for 48 seasons (13 tests). **Finding, no change made:** with a club's seed fixed, the bed rotation never changes its opponents (it changes order, kickoff slots, home/away). Worked example and the exact spec rule: `docs/SCHEDULE_ROTATION_EXAMPLE.md`. The owner has not decided whether to change this.

## 6. Reproducing the evidence

From the repository root, Python 3.13 with numpy.

```
python3 -I -m unittest discover -s tests                       # whole suite, about 50 minutes (the complete digest is taken after every event)
DFL_DIAG_DIR=out/diag python3 -I -m unittest discover -s tests # same, writing mismatch reports if one occurs
python3 -m unittest tests.test_state_digest tests.test_format3_upgrade tests.test_parity tests.test_digest   # the persistence verification tests
python3 -I tools/diag_digest_stability.py --iterations 200 --seed 4   # bounded stability loop (exit 1 if any copy digests differently)
python3 -I tools/diag_profile_leak.py --events 8                      # does a second league alter the first? (exit 1 if so; --src <older checkout>/src to compare)
python3 -I tools/validate.py data/season1 --doc docs/spec/DFL_Design_Document_Ed8.54.md   # 75 checks
python3 -I tools/run_ledger_soak.py --years 3 --seed 1
python3 -I tools/run_restore_soak.py --years 1 --seed 1 --kills 4 --play-level
python3 -I tools/run_restore_soak.py --years 1 --seed 1 --kills 3          # 22-player engine, about an hour
python3 -I tools/show_rotation_example.py                      # the worked rotation example
```
Do not edit `src/dfl` or `data/` while a test run or soak that reopens league files is in progress (the file records the code's fingerprint).

Results at the commit that carries this handoff (save schema 4):
- Full suite: **523 tests**. First run: 521 passed and 2 stale expectations failed (a test that a document at version 1 is a valid starting point for a foreign-code case, and a test that dict order is ignored by the digest; both were written for the old algorithm and were updated to the new rules on purpose). After the updates the two modules that contained them (`test_upgrade` 14, `test_persist` 21) pass; nothing else changed between the runs. Modules touched by the work (`test_state_digest` 19, `test_format3_upgrade` 7, `test_parity` 10) pass.
- Validator against Ed 8.54: 75 of 75.
- Ledger soak, 3 years: PASS, final ledger digest `9fdf159d…` (unchanged).
- Restore soak, 1 year, play-level engine, 4 deaths (two between events, two inside a commit): PASS; **complete** journal digests identical at all 49 events; final digest `0cf52f5e174ce7b8` identical across uninterrupted, resumed and restored runs; archive digest `34d86b85ec63e3bf` identical. (The final digest differs from the earlier `674c9fdc…` because the digest algorithm changed, not because the league did.)
- Restore soak, 1 year, 22-player engine, 3 deaths (at events 27, 39, 42; two after an event, one inside a commit), code of commit 060c001: **PASS**. 49 events, journal digests identical for all 49, exhaustive final digest identical for the uninterrupted run, the resumed run and the restored backup (`dc87c782a4d7dbab`), archive digest identical (`a3d3ca3ea8ee56b9`). 2389 s (reference alone 845 s). This covers the persistence mechanism only; the original uninstrumented failure remains open.
- Bounded stability loop (`tools/diag_digest_stability.py --iterations 200 --seed 4`): 200 of 200 equal on the current code. (On `9211b28`: 10 of 200; after `030b51a`: 0 of 200.)
- Profile leak (`tools/diag_profile_leak.py`): exit 1 on the tree at `9211b28` (first league altered from event 5), exit 0 on the current code.
- Hash seeds: the digest of a 49-event league, with a decode round trip, is identical under `PYTHONHASHSEED` 0, 1, 2 and 777; the test suite adds two more seeds in a subprocess.

**Step 7 code (2026-10-30), after the Step 7 changes (waiver priority, game-day limit, midseason deals, year-turn waivers, trade pick order):**
- Full suite: **560 tests, all pass** (4,054 s).
- Validator: 75 of 75 against Ed 8.54.
- Ledger soak, 3 years, 48 clubs: PASS, final digest `41a4da4f1435a5688182f55a552c7a3d3c52cf8404360ebc27aa331ab131a466` (it was `9fdf159d…`; the waiver order no longer draws random numbers, so trajectories changed on purpose).
- Restore soak, play-level engine, 4 deaths: PASS; 49 of 49 journal digests identical; exhaustive `b21f4c28e1f6762d`; archive `555d1d31312f3239`.
- Restore soak, 22-player engine, 3 deaths: **first attempt FAILED, second PASSED.** The first (after the waiver, game-day and midseason changes) found the journal of the resumed run differing from the uninterrupted one from event 21 (the first week with standings-based waiver priority). Cause: the hash-order dependence in trade proposals above, not a persistence fault; reproduced from a saved state (the same event gave two leagues in 3 of 8 runs), fixed, and the soak re-run: PASS, 49 of 49 journal digests identical, exhaustive `849e3cd1dc8d6d80`, archive `e295bbdf59c78191`. Both reports are kept in `out/` (`restore_soak_22_v5_first_attempt_FAILED.md`, `restore_soak_22_v5.md`; git ignores `out/`, so the digests above are the record in the repository).
- `tests/test_format3_upgrade.py` was reworked: the schema-3 fixture's code and this code no longer agree after event 4 (the waiver order changed at event 5), so the independent reference comparison uses a file of 4 events; a 12-event file is checked by value, continued, verified and reopened.

## 7. Not verified, or verified only partly

1. **The cause of the original failure (section 2).** The suite has been run four times in all (once before the digest fix, three times after) and the failure has not recurred, which is evidence of nothing without knowing the rate.
2. The Bowl ballots and final-53 (not built). The out-of-position penalty (not built).
3. Real storage faults, authentication, cross-machine behaviour, scale (100-year soak not run), concurrency across machines; upgrade across a real previous release is now tested for schema 3 with the exact earlier source, still on short leagues (12 events) and on the placeholder engine; the schema-3 file with a 22-player-engine history, an archive, or many years was not tried. See `docs/REVIEW_HANDOFF_STEPS_3_4.md` section 5.
4. The complete digest is checked after every event, but it can only see state that is reachable from the universe: anything a future change hides in a module global, a closure or a file outside the league would not be seen. `tests/test_state_digest.py` fails when an attribute is added and excluded without a reason, not when state is hidden elsewhere. The audit of module-level state (section 3.2) is a snapshot of today.
5. Dict order is now state in the digest. It was checked under several hash seeds and across processes; a code path that builds a dict in set-iteration order would show up as a replay divergence, not silently, but none was found.
6. Real-time pacing: deferred by instruction. The ≈50-hour season remains provisional.
7. Seeds 16 and 17 of engine calibration; the full fairness measurement (fairness runs use one profile per process; with league-owned settings that restriction is no longer needed, but the measurement was not re-run).

## 8. Where a reviewer should look first

1. `neutral.deep_digest` (the single digest definition) and `neutral.EXCLUDED`: is any exclusion unjustified, and is there any state a league needs to continue that the walk from the universe cannot reach? Then `persist.mismatch_report`.
2. `Runner._replay` and `Runner._commit`; `Runner.open`; `archive_check` (unchanged since the Steps 3–4 handoff).
3. Schema handling: `persist.compatibility`, `_refuse`, `upgrade`, `_WORKER` (applies the old file's parity profile), `Runner.import_state` (adapter-supplied attributes left out of the comparison; is that exactly as strong as it should be?), `neutral.ADAPTERS`.
4. League-owned settings: `parity.settings`, `Ledger.cap` and its uses in `ledger.py`, `world.py`, `governance.py`; `Universe.returner_block`, `SeasonRun.returner_block`.
5. `Ledger.trade_open` / `open_trading` / `close_trading` and their call sites in `year.py` and `ledger/world.py` (`step_year`).
6. `standings.priority` and its callers in `league.py`.
7. `ledger/gameday.emergency_list` and `engine/squad.py` (`emergency_for`).
8. Step 7: `tests/ledger_oracle.py` (is the independent model faithful to §11? it states its two readings), `tests/test_step7_oracle.py`, then the five fixes in `docs/STEP7_LEDGER_VERIFICATION.md` §2 (`gameday.build`, `Universe.waiver_priority` and `World.waiver_order`, `Ledger.sign` and `World.offer` midseason, `Ledger.new_year`, `trading._assets`).

## 9. Next stage

The owner's next step after 7 (the plan's itinerary is in `PROJECT_COMPLETION_PLAN.md` section 6). Live pacing remains deferred. The Bowl's ballots and final-53 construction remain incomplete.

## 10. Step 7: roster, contract and transaction verification (2026-10-30)

Full account: `docs/STEP7_LEDGER_VERIFICATION.md` (method, results, rule-by-rule trace, owner questions L1 to L4 also in `docs/OWNER_RULINGS_NEEDED.md`). In short: an independent model of the ledger written from §0, §11 and §12 (`tests/ledger_oracle.py`) is compared with the ledger after every call of long seeded random transaction sequences, and on 3,000 random contracts; the document's numbers are read from its text and compared with the code. Defects found and fixed:

1. Game-day list could exceed 48 (49 or 50 dressed) when a practice-squad player was elevated into a full list; the 47-player trim could drop an elevated player while still counting her elevation.
2. Waiver priority was a seeded shuffle; now last season's draft order for four weeks, then the standings, Tour clubs in their prior draft bands (reading L1, owner question).
3. Midseason signings could be multi-year; the document says they expire at year close. `Ledger.sign` now rejects them and `World.offer` offers one year.
4. `Ledger.new_year` left players on waivers with expired contracts (not reachable through the World, which clears the wire first); they now clear to free agency.
5. Trade proposals depended on set iteration order (hash randomisation) when a club held two picks of the same round and year; found by the 22-player restore soak, reproduced, fixed, tested.

Not built, listed so nothing is claimed that is not there: extensions, postseason-only replacements, restructuring a future year's base. The committed-money cache `Club._cmap` is still part of the state digest (a derived cache; coherent in every sequence run; excluding it needs a digest-algorithm and schema change, recommended for the next format change).

**What this does to the open item (section 2).** Nothing changes its status. A second source of process-to-process difference was found and fixed in the simulation, but the original failure compared a decoded state with the state exported and stepped no event, so this defect cannot be its cause.
