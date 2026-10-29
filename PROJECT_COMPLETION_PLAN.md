# DFL v2 — Project Completion Plan

Audit date: 2026-10-29, updated the same day after the housekeeping pass (section 9) and the durable-saving build (section 10). Branch: `audit/completion-plan` (from `milestone-1-league-layer` at 4232e22; no newer work exists on the remote, which holds only `main` and `milestone-1-league-layer`).

## 0. Ground rules this plan follows

- **Authoritative edition: Ed 8.53** (`docs/spec/DFL_Design_Document_Ed8.53.md`, hash `3022ef39…eb24`; amends Ed 8.52 only with the owner's rulings of 2026-10-29, section 13). The validator confirms the hash and 75 checks. The spec wins over every other document, including v1 (`Vajilia/DFL`), which is a library of ideas only.
- **NFL rates are used only where the spec is silent or DFL does not differ by design.** Nothing in this plan replaces an established DFL rule (no banking, 16-man practice squad, no franchise tags, 48-man game day, 20 position labels, DFL tiebreak cascade, etc.) with an NFL rule. Where the spec is silent, the plan lists the item under section 4 for a ruling instead of picking the NFL answer.
- **No language models in simulation decisions.** Simulation decisions must be deterministic rules, weighted choices and seeded randomness. Current `src` has no LLM or network imports, no unseeded random and no wall-clock use. Two planning documents contradict this directive (section 4, C1).
- **Itinerary.** The request referred to "the stages in this itinerary" but no list came with it. This plan uses the repository's own `docs/ROADMAP.md` (Phases 0–8) as the itinerary, adjusted for completed work. If a different list was intended, send it and the stages can be re-cut.
- **Evidence labels.** `RE-RUN` = executed on today's code in this audit. `CLAIM` = stated in repo docs or the earlier audit, not re-executed today.

## 1. Baseline checks run today

| Check | Result |
| --- | --- |
| Validator vs Ed 8.52 (`tools/validate.py data/season1 --doc …Ed8.52.md`) | **75 / 75 pass** (RE-RUN) |
| Unit tests (`unittest discover`) | **333 tests OK**, 733.8 s (RE-RUN, start of audit); **374 tests OK** after the fixes and durable saving (section 10) |
| 30 structural seasons | **0 validator failures**, identical on re-run (RE-RUN) |
| 3-year ledger soak | **PASS**, digest `9fdf159d…` (RE-RUN) |
| 3-year universe soak (spatial default) | **PASS** (RE-RUN) |
| Spatial default band check, 1,000 games | **24 / 24 pass**, three bands near an edge (RE-RUN) |
| Play-level engine band check, seed 1, 1,000 games | **FAIL on one band**: margin_sd 15.808 vs band 11–15.5 (RE-RUN). Contradicts the "passes all 24" statement in the docs |

### 1a. Late results

- **3-year universe soak (spatial default, seed 1): PASS** (RE-RUN, 2,515 s). Digest `308b548d…a310a`. 2,328 games, 0 forfeits, 0 legal-move failures, 246 trades, 0 cap overage. Note: elevations were 0 across the whole run, which deserves a look (the elevation rules are unit-tested, but the league loop may never exercise them).
- **Spatial default band check, 1,000 games, seed 1: 24 / 24 pass** (RE-RUN, 1,105 s). Three bands sit close to an edge: punts 4.91 (max 5.0), margin_sd 15.37 (max 15.5), tie_rate 0.018 (max 0.02). This is consistent with the known league-to-league sensitivity (seeds 16 and 17 overrun caps, CLAIM).

## 2. Implemented and verified

"Verified" means a test, validator check or soak exercises it and it passed today.

| Area | What exists | Evidence |
| --- | --- | --- |
| Spec integrity | Hash-stamped Ed 8.52; validator checks the data against it | Validator 75/75 (RE-RUN) |
| Phase 0 Intake | Season 1 data set, schedule skeleton, bed rotation, tiebreak cascade, standings | Validator + 30 seasons, 0 failures (RE-RUN) |
| Phase 1 Roster / cap ledger | Cap $1,000, reserve $25, min salary $5, no banking, 16-man practice squad, 48/47 game day, elevations, IR rules | Unit tests + ledger soak (RE-RUN) |
| Phase 2 Calendar / year loop | Season, playoffs, draft, free agency, offseason loop, trades | Unit tests + 3-year soaks (RE-RUN); elevations never fired in the soak |
| Phase 2.5 Character creator | Character generation and development, as unit-tested | Unit tests (RE-RUN); numbers are provisional, see section 3 |
| Phase 3 Engine, mechanics | Play-level resolver (golden digest) and 22-player spatial engine run end to end; spatial is league default | Unit tests (RE-RUN); golden digest in tests |
| Determinism | Same seed gives identical 30-season output; no unseeded random, wall-clock or network use in `src` | Identical re-run + source scan (RE-RUN) |
| Phase 5 People layer, mechanics | Scouting, souls, demands, coaches, governance, rooms, analytics/halls (slices 5.1–5.7 all built) | Unit tests (RE-RUN) |
| Phase 4 Fairness harness | `tools/fairness.py` exists and runs | Unit tests (RE-RUN); results are provisional |
| Special-game venues, Tiaras, award idempotence, CI smoke | Neutral venues, Coronation host rule, exact Tiara set, idempotent awards, crash-failing CI smoke | 20 regression tests (`tests/test_special_games.py`); section 8 |
| Durable saving and restart recovery | SQLite league file, atomic events, snapshot fallback and journal replay, hash chain, online backup, verified restore | 21 tests (`tests/test_persist.py`), real process kills, and a one-year restore soak on the 22-player engine; section 10 |
| CI | Unit tests, validator, 30-season run, ledger and universe soaks, 20-game spatial smoke | `.github/workflows/ci.yml`; green on 4232e22 (CLAIM) |

## 3. Implemented but provisional

Code exists and runs; the numbers or behaviour have not been fully verified or are placeholders.

1. **Spatial engine calibration.** 24/24 bands at the default check and on seeds 14 and 15 (seed 1 re-run today: 24/24; seeds 14 and 15 are CLAIM). Seeds 16 and 17 overrun the caps: league-to-league sensitivity is unresolved. Physics (`PHYSICS.csv`), plays, routes are fitted values, not independently validated. Extensions (spec §20 item 47) not done.
2. **Play-level engine.** Fast mode still the golden digest, but fails the margin_sd band today (above).
3. **Fairness.** Only 2 × 12 seasons measured on the spatial engine. The full 36-season × 6-league measurement would take about 35 h and has not been run. Phase 4 was also to be re-run after the people layer; not done.
4. **Character attributes and development curves.** Parameters are working values.
5. **People-layer numbers.** Scouting noise, soul traits, demand weights, coach effects, governance thresholds, room and analytics values are working values. Some stand-ins still read true ability instead of scouted or perceived ability.
6. **EP curve, trade prices, penalty attribution.** Working values.
7. **Tie rate.** Monitored; the spatial engine's 0.015–0.025 is noisy (item 7).
8. **Traces.** Now stored for every league game played with the archive on (section 11); the 22-player engine's traces are complete (every simulated snap) and verified. Seasons played before the archive existed have none.
9. **Long-run stability on the spatial default.** Only 3-year soaks, one restore-soak year and 30 structural seasons are verified. No 100-year soak on the spatial default. numpy cross-platform determinism is unverified (CI runs on one platform).
10. **League files and code releases.** Resolved in design and proved for behaviour-neutral code changes (C11, section 10a). The upgrade is not yet exercised on a release that changes league rules or the shape of the state. File growth over 100 years is estimated from measurement, not run.

## 4. Missing

| Phase | Missing | Notes |
| --- | --- | --- |
| 6 Live clock and pacing | The real-time clock: game blocks, per-instance pause, a season that completes on a chosen clock (target ≈50 h, non-binding per Ed 8.7); scheduling of backups | The storage half is built (section 10). Nothing in `src` reads a clock yet, by design |
| 6 Trace archive | Archive format, storage service and cost (spec §20 item 49) | Decide with the website work |
| 7 Presentation / story | Website with the six entries; 2D animation viewer on the spatial traces; narrative layer; Commissioner page; chaos injections | `boxscore.py` has `play_by_play` and `narrate` as seeds. The narrative layer must be template/rule-based and read-only (C1) |
| 8 Release | Truth → Beta → Alpha → Truth pipeline; secrets and security review; deployment; rollback rehearsal; a neutral export of league state so that code releases do not strand league files (section 10) | Out of scope until the owner approves any deploy |
| Spec items | Open §20 items that are not yet resolved (C7) | |
| Docs | Stable rule IDs (item 6) | Next spec edition |

## 5. Conflicting or unresolved requirements

| ID | Conflict | Status |
| --- | --- | --- |
| C1 | **Language-model deciders.** `docs/TRADES.md` stage 3 and the roadmap's "agents second" assumed LLM decision-makers; the directive forbids them in simulation decisions. | **Resolved 2026-10-29 (housekeeping).** `TRADES.md` stage 3 is now pluggable *deterministic* deciders; `ROADMAP.md` rule 5 and risk 3 rewritten; the README states the rule. Narrative text, if built, is template-driven, read-only and cannot reach the engine |
| C2 | **Pacing.** README 48 h; spec §0 arithmetic ≈55 h; owner ruling ≈50 h (Ed 8.7, non-binding); §20 item 5. | **Resolved, and now provisional (owner 2026-10-29).** README and ROADMAP say ≈50 h, non-binding, and that the live clock is deferred until saving and archiving were complete. The clock mapping itself is Phase 6 clock work |
| C3 | **Snapshot/import.** `BETA_STATUS.md` and `SOURCE_IMPORT_STATUS.md` demanded a snapshot that does not exist. | **Resolved.** Both marked superseded (kept as history); the README import section corrected |
| C4 | **Stale text** (README, BUILD_STATUS edition row and "next" list, ROADMAP "Where we are", `requirements.txt` numpy comment). | **Resolved** |
| C5 | **Calibration claim vs result.** Docs said the play-level engine passes all 24 bands. | **Resolved by restating the claim**, not by changing the engine or the bands. Re-measured: seeds 1, 2, 3 (1,000 games each) miss 1, 2, 3 bands, all marginally. The play-level engine is the fast mode and is not held to 24/24; the owner ruling of Ed 8.50 puts that bar on the spatial engine, which meets it at the default check. The golden digest is untouched |
| C6 | **V1-11 offseason cap count** was marked "default to NFL". | **Resolved:** Ed 8.9 (§11) already defines it: before the final cut a club's cap number is dead money plus its highest 69 contracts (the NFL top-51 method adapted). `V1_DIVERGENCES.md` updated |
| C7 | **Open §20 items.** | **Open; none blocks the next stage.** Full question, recommended default and gameplay consequences for each of 4, 8, 9 and 14 are in `docs/OWNER_RULINGS_NEEDED.md`. In short: **4** Bowl roster repair (fill from the next-ranked ballot candidates of the same conference, then the emergency list); **8** the NFL-inherited fill-in parameters (confirm 48/47, 2 elevations a game, 3 a player, 4-game IR minimum as built); **9** standings assumptions (confirm higher strength of schedule ranks higher, no decisions = 0 win %); **14** offseason dates (confirm as built). Routine, no ruling needed: **6** rule IDs (next spec edition), **7** ties (monitored figure), **49** trace archive (format chosen and built, section 11; hosting with the website), **5** pacing map (deferred with the live clock; the ≈50-hour season is provisional) |
| C8 | **Home-field edge at a neutral venue.** | **Approved by the owner 2026-10-29:** neutral venues provide no home-field advantage. Implemented (section 8) |
| C9 | **Nominal home of the Bowl** (no template, no seeds). | **Approved by the owner 2026-10-29:** a seeded draw. Implemented (section 8) |
| C10 | **Opening possession and the nominal home.** The owner asked that opening possession follow "the established DFL coin-toss rule", independently of the nominal home. | **No such rule exists, and no gap.** Spec §10 says: "Home kicks to start the first half, away the second. No coin toss. In overtime the away side kicks first, alternating each period." The DFL has no coin toss; "home" there means the nominal home, which §6 assigns at neutral sites and §7 repeats for the Tour ("The nominal home kicks the first half"). So the earlier statement that the nominal home "still kicks first" is exactly the spec rule, and nothing was added. A test (`OpeningPossession`) now locks it in for home and neutral games, and checks the engine contains no coin toss. If the owner wants a coin toss or a possession rule independent of the nominal home, that is a substantive rule change for a new edition; the simplest fair candidate would be a seeded draw per game, but none is implemented |
| C11 | **League files are tied to the code that wrote them** (section 10). | **Resolved 2026-10-29 (section 10a).** Save schema (readable?) and code identity (exact?) are separate verdicts; the originating code is kept inside every file; clear rejections; a tested upgrade path that never touches the original file; export is only half of it, and the import is proved by value (state, identifiers, exact money, every random state). No migration framework: two empty hooks (`neutral.RENAMES`, `neutral.ADAPTERS`). Not yet exercised on a rule-changing release |

## 6. Itinerary (ROADMAP Phases 0–8, adjusted)

| Stage | Status | Remaining work |
| --- | --- | --- |
| 0 Intake | Done, verified | none |
| 1 Roster / cap ledger | Done, verified | none |
| 2 Full calendar | Done, verified | none |
| 2.5 Character creator | Done, numbers provisional | tune with Phase 4 |
| 3 Game engine | Done, provisional calibration | seed 16/17 overruns; 100-year soak (the play-level band claim is restated, C5) |
| 4 Fairness | Harness done, results partial | full 36-season × 6-league measurement (~35 h) after people-layer numbers are frozen |
| 5 People | Built, provisional | replace true-ability stand-ins; tune; rule on decisions needed in `docs/PEOPLE.md` |
| 6 Persistence / archive / live clock | **Saving built, accepted provisionally by the owner (independent review pending); upgrade path built (10a); permanent archive built (11)**; clock half deferred | live clock, game blocks, pause; backup scheduling |
| 7 Presentation / story | **Not started** | after 6 |
| 8 Release | **Not started** | after 7; no deploy without owner approval |

## 7. Recommended next stage

**Next: the live clock and pacing (Phase 6, second half), now that saving, upgrade and archiving are in place.** The owner deferred it until they were complete. Map simulated events to real time (the ≈50-hour season is **provisional**, not a target to build to), run game blocks on that clock with per-instance pause, and have the clock process call the durable `Runner` one event at a time, with scheduled `backup-all`. The exit test is the roadmap's: a full season completes on the chosen clock, and a kill and restart mid-season loses and duplicates nothing (proved already; sections 10 and 11).

**Before that, independent review of Step 3** (the owner accepted it provisionally), and the four rulings in `docs/OWNER_RULINGS_NEEDED.md`.

**In parallel, as background compute:** a 100-year soak on the spatial default (it will also measure archive and file growth), a second platform in CI for numpy determinism, and the full 36-season × six-league fairness measurement once the people-layer numbers are frozen.

## 8. Fix log, 2026-10-29

Four defects from the earlier audit were re-checked against the current code first. All four still existed. Regression tests are in `tests/test_special_games.py` (17 tests); 14 of them fail on the pre-fix code and all pass now.

| # | Defect (still present when checked) | Fix | Tests |
| --- | --- | --- | --- |
| 1 | `SeasonRun.play_coronation` always passed the Eastern champion as the engine's home side, whatever the venue. The engine had no neutral-venue mode at all, so Tour games, the Bowl and a neutral Coronation all gave their nominal home side the home-field edge. | New `SeasonRun.coronation_sides`: a finalist whose own venue won the vote hosts and is the home side (Western or Eastern); otherwise the venue is neutral, the higher seed is the nominal home, and equal seeds go to a seeded draw. If both finalists share the winning venue it counts as neutral. The game dict now carries `neutral`, and `Game`, `play_game`, both engine resolvers and the placeholder resolver honour it (neutral removes the home-field edge; the nominal home still kicks first). | `CoronationVenue` (5), `EngineNeutral` (3) |
| 1b | Audit of other special games. | **Tour** (28 games a season, no venue is any club's home): now neutral. **Bowl**: was always Eastern home with the edge; now neutral, with the nominal home a seeded draw per season (the All-Star sides have no seeds). **Playoffs**: already correct (higher seed hosts, not neutral); now covered by a test. **Regular season**: correct; the schedule has no neutral games. **`play_playoffs` helper**: correct. | `OtherSpecialGames` (3) |
| 2 | `honours.award_tiara` gave Tiaras to `roster` plus `ir`. The rule (§15, Ed 8.30 as built, confirmed unchanged in Ed 8.52) is the winning club's final 53 plus the CEO, GM, HC and three coordinators. | The set is now exactly `club.roster` (at most 53, asserted) plus the six leaders. Injured reserve and the practice squad are excluded. New `honours.tiara_set`. | `Tiaras` (2) |
| 3 | Calling `award_tiara` twice doubled `world.stats["tiaras"]`, although the personal award sets did not duplicate. | Idempotent: the counter adds only Tiaras newly given; a repeat call changes no state and returns the same set. A different year counts again. | `Tiaras` (2) |
| 4 | CI ran `calibrate_engine.py … --spatial … \|\| true`, which hid crashes along with expected small-sample band misses. The tool's exit code was the number of failed bands, so one miss was indistinguishable from a crash (exit 1). | New `--informational` flag: band misses are printed and exit 0; an exception exits 1; an incomplete run or non-finite statistic exits 2. The CI step uses the flag and no longer has `\|\| true`. | `CiSmoke` (2) |

**Rulings.** The owner approved both provisional choices on 2026-10-29 (C8, C9 in section 5): neutral venues give no home-field advantage, and the Ambassador Bowl's nominal home is a seeded draw. The kickoff question raised with them is answered in C10: the spec already fixes opening possession (no coin toss; the nominal home kicks first), so nothing was added. Neither choice changes any stored rule or data file.

**Checks after the fixes (RE-RUN 2026-10-29):**
- Full unit suite: **350 tests OK** (333 earlier plus 17 new), 758 s.
- Validator vs Ed 8.52: 75/75.
- 30 structural seasons: 0 validator failures; Coronation venue 40-year rule holds; a finalist hosts in 6.7% of seasons.
- 3-year ledger soak: PASS, digest `9fdf159d…` (identical to before; nothing in the ledger changed).
- CI smoke command run locally: exits 0 with 5 informational band misses in 20 games.
- 3-year universe soak: **PASS**, digest `f340fce7…` (changed from `308b548d…`, as expected), 2,328 games, 0 forfeits, 0 legal-move failures, 2,514 s. The in-season overage figure (1.9) is the documented within-season drift that the audit allows; the invariants held at every season start and year end.

**Not changed:** the golden engine digest (play_game defaults to non-neutral), the calibration bands, the spec and any data file. League-level digests change because the Tour, Bowl and Coronation now draw differently; that is expected.

## 9. Housekeeping pass (Stage A), 2026-10-29

Routine implementation choices were made without asking; nothing here changes a league rule.

| Item | What was done |
| --- | --- |
| C1 language models | `TRADES.md` stage 3 and decision 1, `ROADMAP.md` rule 5, risk 3 and "not doing", README: deterministic deciders only. A model may at most write read-only presentation text |
| C2 pacing | README and ROADMAP: about 50 h, non-binding (Ed 8.7) |
| C3 snapshot | `BETA_STATUS.md`, `SOURCE_IMPORT_STATUS.md`: marked superseded, kept as history |
| C4 stale text | README rewritten (status, test count, import section); BUILD_STATUS edition row, play-level engine row and "not built yet" list; ROADMAP "Where we are"; `requirements.txt` comment |
| C5 band claim | BUILD_STATUS restated with today's seed 1, 2, 3 measurements (1, 2, 3 marginal misses). No engine change; golden digest untouched |
| C6 V1-11 | `V1_DIVERGENCES.md`: resolved by Ed 8.9 |
| C7 | Items needing rulings are listed with recommended defaults in section 5; none blocks the next stage |
| Elevations were 0 in the soak | Looked at: elevation only fires when a position is short or the 48-man list cannot be filled (`gameday.build`), which the engine path rarely produces (2 short-handed games in 3 years). Unit-tested in `test_ledger.py`; wired in `world.play_paid_week`. Not a defect; a league-level exercise of it remains a verification gap |
| Opening possession (C10) | Checked against spec §10: no coin toss, nominal home kicks. Test added; no rule added |

## 10. Durable saving, restart recovery, atomic events, backup and restore (Phase 6, storage half)

Design and limits: `docs/PERSISTENCE.md`. Code: `src/dfl/persist.py`; `Universe.step_event()` (the unit of work is one of the 49 calendar events a year; `play_year()` is unchanged in result); `tools/league_store.py`; `tools/run_restore_soak.py`.

**What it does.** One SQLite file holds a journal (one row per committed event, with a digest of the league and a hash chain) and snapshots (zlib-compressed pickles read through an allow-listed unpickler, each with its SHA-256). An event runs in memory and is committed in one transaction; any failure leaves the file at the previous event and the runner reloads. `Runner.open` takes the newest good snapshot and replays the journal after it, comparing each event's digest. `backup` is SQLite's online backup, verified; `restore` verifies and never overwrites. The file stores a fingerprint of the source and data files and refuses to open under different code unless told to.

**Proof that restored equals uninterrupted** (all RE-RUN 2026-10-29):

| Proof | Result |
| --- | --- |
| Refactor changes nothing | Two seasons, seed 3, play-level engine: season-report and ledger digests identical before and after `step_event` (`f2958ddf…`) |
| Saved run killed at the year boundary, killed after week 1, killed mid-season with replay | Journal digest identical at all events; exhaustive digest (`deep_digest`: every value and every random state, independent of dict and set order) identical to the uninterrupted run |
| Exception inside an event; crash between journal row and snapshot; second writer | File unchanged / rolled back / fork refused; retry reaches the identical league |
| Real process death (`os._exit`) between events and inside a commit, resumed by other processes with different hash randomisation | Identical final digest and journal |
| Damaged newest snapshot; all snapshots damaged; edited journal row; journal gap; other code; hostile pickle | Fallback and replay to the identical state; `Unrecoverable`; hash-chain failure and `ReplayDivergence`; gap reported; `CodeMismatch`; unpickler refuses |
| Backup taken while running, restored, run on | Identical to the never-stopped run and to the uninterrupted reference |
| 22-player engine, week 1 and Tour round 1 under a restore | Identical exhaustive digest |
| **Restore soak, one whole year, 22-player engine (the default), 3 process deaths, 1 backup restored** | **PASS**: 49 of 49 journal digests identical; final exhaustive digest `24ad373b…` identical for the uninterrupted, killed-and-resumed and restored-backup runs; 2,494 s |
| Same soak, play-level engine, 4 deaths (this is the CI step) | PASS, 89 s |

**Limits (full list in `docs/PERSISTENCE.md`).** League files are tied to the exact code and data that wrote them (C11); integrity checks detect damage but are not authentication; file growth over 100 years is estimated, not measured; one machine and one writer; backups are not yet scheduled; the live clock is not built. An event is the smallest durable unit, so a crash in the middle of a game week replays that whole week (identically).

**Cost.** A snapshot takes about 0.3 to 0.4 s; at the default of one per event that adds about 20 s to a league year.


## 10a. C11 resolved: league files, code identity, upgrade (2026-10-29)

The owner accepted Step 3 provisionally, **subject to later independent review**, and asked that C11 be settled first. Design: `docs/PERSISTENCE.md` (Compatibility, versions and upgrade). Code: `src/dfl/persist.py` (save schema 2), `src/dfl/neutral.py`. Tests: `tests/test_upgrade.py` (12), plus the 21 persistence tests, all kept.

| Owner's requirement | What was built | Evidence (RE-RUN 2026-10-29) |
| --- | --- | --- |
| Distinguish save-schema compatibility from exact code/data identity | `meta.format` (can this code read the tables?) and `meta.fingerprint` (is this the exact source and data?) are separate verdicts; `compatibility()` and the `compat` command report both without loading anything; a file opens for use only if both hold; `status` and `verify` work under any code | `Compatibility` tests: ok/exact, ok/differs, unsupported |
| Preserve the originating versions for reproducibility | Every file carries the source and data that wrote it (zip, about 0.2 MB, with a per-file manifest) and every later code version that wrote to it; `extract-code` restores them byte for byte and checks the fingerprint. Each archived game stores its rule version and engine | An old file, run under its own extracted code, reproduces the same event digest |
| Reject unsupported combinations clearly | `UnsupportedFormat` (format 1: first development build, never held a league of record, no converter; future formats: newer code needed) and `CodeMismatch` naming the files that differ and the way forward; state from other code is refused unless a new code epoch is asked for; a state that differs from its export digest is `StateMismatch` and nothing is written | Open, export and upgrade all refuse format 1 and 99 with the reason; foreign-code import refused; tampered digest refused with no file written; existing destination refused |
| Documented, tested upgrade path that preserves the original file | `upgrade OLD NEW --run-originating-code`: work on a copy; export under the originating code in a separate process (opt-in, it executes code from the file); neutral document; import under current code; compare by value; lineage recorded; NEW continues OLD's event numbers and hash chain; OLD's hash checked before and after | Original file byte-identical after the upgrade; lineage holds old fingerprint, last event, chain value and file hash; the upgraded league then plays 8 events with digests equal to an unbroken league |
| Prove export/import preserves required state, identifiers, exact money, random state; do not describe export alone as migration | Round trip under the same code | Exhaustive digest equal; every `Fraction` equal in numerator and denominator (and still `Fraction`); player, club and card identifiers equal; every random generator's full state equal; both leagues then play 8 more events with identical digests and identical hash-chain values; document bytes deterministic |
| Avoid a large migration framework | None built. Two empty hooks, `neutral.RENAMES` and `neutral.ADAPTERS`, are the places to say what a release changed | |

**Honest limits.** Proved for behaviour-neutral code changes and for the round trip. A release that changes league rules changes the league's future by design, and one that changes the shape of the state needs an adapter written and tested for that release; none exists because no such release does. Extracting the code means running it: only upgrade files you trust. Reproduction is exact within a code version; across an upgrade the past is preserved (old file, old code, archive) and the future is the new code's.

## 11. Step 4: the permanent archive (2026-10-29)

Design and format: `docs/ARCHIVE.md`. Code: `src/dfl/archive.py`; capture in `engine/ledger_engine.py` and `year.py`; game ids from `league.py`; reader `tools/read_archive.py` (standard library only); tests `tests/test_archive.py` (15). The tool `league_store.py` gained `archive-verify`, `archive-stats`, `archive-digest`, `backup-all`, `restore-all`.

**What it stores.** A second SQLite file beside the league file, append-only (triggers refuse UPDATE and DELETE), holding every game as played (score, team and player statistics, injuries, penalties, the full structured play table), **every simulated snap's trace** from the 22-player engine in a documented binary container (positions every 0.05 s, ball, events, intents, formation, play, coverage; rule and engine version), and a season summary. Game ids come from the league layer (`S001/reg:w03:g07`, `S001/tour:r4:g02`, `S001/po:Western:wild_card:s7v2`, `S001/bowl`, `S001/coronation`). Capture is part of normal league execution: `Runner.create` turns it on by default; with it off the league runs as before.

| Requirement | Proof (RE-RUN 2026-10-29) |
| --- | --- |
| Capturing history does not change what the league does | Real 22-player week 1 with the archive on and off: quick digest identical after every event. Whole-year soak and the full season below produce the same chain on two separate runs |
| Complete | In the 22-player engine every snap with a play call has a trace (verified per game; `verify` fails otherwise). One whole season: 402 games, 47,985 traces, 104 to 151 per game |
| Integrity | Every row carries a SHA-256 of its uncompressed content; `verify` re-hashes games, traces and records, parses every container, checks declared against stored trace counts and each event's batch digest, and checks the append-only triggers. Damage to a game, a trace, or a missing trace is found by `verify` and by the stand-alone reader. Edits and deletes are refused |
| Retry safety | The archive is written first, idempotently, then the league journal. Crash between the two: archive exactly one event ahead, never behind; the retry finds identical rows and changes nothing, and the final archive digest equals a run that never crashed. A different record under an existing id is `ArchiveConflict` and nothing is written. Replaying the journal on recovery checks the regenerated records against the archive; an archive that is behind is refused (`ArchiveMissing`) unless `repair_archive` is asked for, and is then rebuilt exactly |
| Backup and restore | `backup-all` copies league first, then archive, so the archive is never behind; both verified; `restore-all` verifies both, never overwrites, and opens the league. A damaged archive cannot be backed up. Restore soak, one year, play-level engine (the CI step), 4 process deaths, one backup restored: PASS, 87 s. **Same soak on the 22-player engine** (3 deaths at events 27, 39 and 42, alternately between events and inside a commit; backup after event 38 restored with its archive and run to the end): **PASS**, 2,631 s; journal identical at 49 of 49 events; final league digest `17a6b04d…` identical; **archive digest `37929ed7…` identical** for the uninterrupted, killed-and-resumed and restored runs, covering every game and all of that year's traces |
| Historical readability | `python3 -I -S tools/read_archive.py` (no site-packages: no numpy, no DFL code) lists games, prints a game, prints a trace as JSON frames, prints the season summary and verifies the file; a test compares its output with the module and with the engine's own `trace.decode` (positions identical to the hundredth of a yard) |

**Measured storage** (22-player engine, season 1, seed 1, one full league year, 49 events; final format with delta-coded positions and 16 KB pages; the file verifies):

| Quantity | Measured |
| --- | --- |
| Games archived | 402: 360 regular, 28 Tour, 12 playoff, Bowl, Coronation |
| Traced snaps | 47,985 (119 a game on average; 104 to 151) |
| One trace, stored | 2,539 bytes on average (7,949 uncompressed, 3.1 times smaller) |
| One game's record (play table and statistics, no traces), stored | 5.2 KB on average (regular season) |
| **One game, everything** | **about 308 KB** (303 KB of traces) |
| **One season**, payload | 123.9 MB (traces 121.8 MB, game records 2.1 MB, season summary 9.5 KB) |
| **One season**, archive file on disk | **142.8 MB** (15% database overhead on top of the payload) |
| Cost to a hundred seasons | about 14 GB of archive, plus about 0.2 MB of code per file and the league file |
| League file after the season | 8.4 MB (five snapshots of 0.5 to 2.0 MB each, two of them year checkpoints, the journal and the 0.2 MB code record) |
| Season run time | 1,076 s (shared machine; capture overhead was not isolated) |

Before the delta coding and the page size change the same season was 4,824 bytes a trace and a 274 MB file; the change is exact (round trip checked on measured traces) and was made now, before any permanent archive exists, because afterwards it would be a format migration. Spec §17's estimate was about 4 KB a snap and about 0.5 GB a season; the measured season is about 0.14 GB. A further reduction (coarser time steps for stored traces) would lose information as played, so it was not taken.

**Limits.** (Closed by section 11a: an archive older than the newest snapshot, restored on its own, is now detected at open.) The snapshot still carries each season's full result in memory (`reports[*]["result"]`); trimming it is separate. Checksums are not authentication. Seasons played before this step have no archive. Hosting, indexing for the website and a viewer are Phase 7.

**Checks run on the final code (2026-10-29).** Full unit-test suite `python3 -I -m unittest discover -s tests`: **401 tests, all pass** (was 333 at the start of the audit; 21 persistence, 12 upgrade, 15 archive among them; one persistence test's hook counting was adapted because the commit gained an archive hook point, the guarantee it checks is unchanged). Validator: 75 of 75. 30-season run: 30 validated, 0 failures. Ledger soak (3 years) and universe soak (3 years): PASS. Restore soaks: above. Not run: the 100-year soak, seeds 16 and 17, the full fairness measurement (unchanged from section 3).


## 11a. Archive consistency at open (2026-10-29, owner's instruction after the Step 3/4 review request)

**The gap that was open.** An archive restored on its own from an older backup was detected at open only if the missing events lay in the journal being replayed. One older than the newest snapshot (the usual case when snapshots are taken every event) was not detected at all.

**What changed.** Save schema 3 (schemas 1 and 2 were development builds): the league has a random `league_id`, also stored in its archive; each journal row records how many archive records its event produced and a digest of them, both inside the hash chain; `Runner.open` compares every journal row with the archive's batch table before replaying or writing anything. It refuses (`ArchiveInconsistent`) an archive that is missing records, holds different ones, belongs to another league, is absent or unreadable, or is ahead by more than the one event an interrupted commit leaves; it accepts that one event. `repair_archive=True` rebuilds missing events by replay from the newest snapshot before the gap, each rebuilt batch checked against the journal; `allow_archive_ahead=True` accepts an older league beside a newer archive of the same league (used by `restore-all`). `upgrade` and `import` refuse an inconsistent pair. Details: `docs/ARCHIVE.md`.

**Proof.** `tests/test_archive_consistency.py`, 22 tests: older archive beside a newer league (gap older than the newest snapshot, both files untouched by the refusal, every way of opening including the command line), rebuild by replay equal to the uninterrupted run, a rebuild against a falsified journal digest stopped, deleted, altered and extra batches, another league's archive with identical games, missing and unreadable archive, one event ahead after an interrupted commit accepted and made exact, an older league beside a newer archive refused unless asked for and then confirmed event by event, a diverging league stopped, journal edits breaking the hash chain, upgrade and import refusing an inconsistent pair, a backup set whose archive was copied later restoring, and a runner without its archive unable to commit history. One existing test (`archive is behind the league`) now expects the new error.


## 12. Step 5: season controller and calendar verification (2026-10-29)

Scope: `calendar.py`, `Universe.step_event` / `SeasonRun` in `year.py` and `league.py`, against Ed 8.52 §2 and §7. Live-clock pacing is not part of this step (deferred). Tests: `tests/test_season_controller.py` (17, new).

**Verified (tests).**
- Calendar, every year 2000 to 2399 (leap years, every possible Coronation date 25 to 31 December): 27 calendar weeks with all Sundays exactly 7 days apart; the Coronation is the last Sunday of the year; wild card, divisional, conference and Bowl are C−28, C−21, C−14, C−7; the final regular-season Monday is C−34; 22 paid weeks containing 19 numbered weeks; active-club breaks after numbered weeks 4, 9 and 14 (calendar weeks 5, 11, 17); byes in numbered weeks 15 to 19 with the seed order the calendar notes give; Tour rounds on calendar weeks 1, 4, 8, 11, 15, 18, 22, 21 or 28 days apart, round 4 in a break week, and the last round at least 14 days before the Bowl.
- Offseason in every year: 1 January (league year, hiring opens), 31 January (hiring closes), 1 February (trading, free agency), draft round 1 on the last Thursday of April with rounds 2 to 3 and 4 to 7 on the next two days, camp 1 June, venue vote 15 June, final cut on the Tuesday and waivers on the Wednesday before week 1 (week 1 falls between 24 June and 1 July), all before the season, all inside the calendar year; 49 events a year, none repeated.
- Controller, three whole years (placeholder resolver): every year fires its 49 events once, on their calendar dates, in calendar order; the paid-week counter equals the number of week and break events fired (22 a year); trading rounds happen only at `trading_open` and in paid weeks 1 to 9, and the ledger's window is closed from the Friday before numbered week 9 until the season ends; each year has 360 regular games, 28 Tour games, 12 playoff games, the Bowl and the Coronation; every active club plays 18 games, never twice in a week, with its one bye in the week its seed is assigned; each ambassador plays exactly the 7 Tour rounds and nothing else; the postseason rounds, Bowl and Coronation fall on their Sundays; the Coronation is held where the June vote said.

**Defect found and fixed.** Every season after the first took its games from Season 1's tables and kept Season 1's dates: a game in Season 5 was dated 1 July 2000. Nothing in the simulation read the date, which is why no test caught it, but the archive's games and any viewer would have shown the wrong year. Now `season_tables` moves every date by the difference between that year's Coronation and Season 1's (the calendar is anchored on the Coronation, so this is exact and keeps weekday and kickoff), `write_tables(..., year=)` writes that year's `CALENDAR.csv` (the validator passes on 30 generated seasons), and each archived game record carries `date` and `kickoff` (playoff rounds, Bowl and Coronation on their Sundays; the Bowl at 12:00 as §2 says; the spec gives no kickoff time for playoffs or the Coronation, so none is recorded). Game dates are not part of any digest of league state, so no existing league history changes; archives written from now on carry the new fields.

**Findings recorded, not changed.**
1. *Trading window outside the season.* **FIXED 2026-10-29 (section 13).** The ledger reported trading as open whenever the phase was not "season", which included the weeks after the season ends and January. The ledger now keeps the window itself.
2. *Emergency list.* **FIXED 2026-10-29 (section 13).** The game-day builder named a fill-in only for a position with nobody dressed; every position now has one.
3. *Real-time mapping.* The controller advances by event, not by clock; nothing yet maps events to simulated or real hours. Deferred by instruction (the ≈50-hour season remains provisional; the validator's arithmetic check reads 54.75 h from the calendar's slot lengths).

**Not verified.** The controller was exercised with the placeholder resolver (fast) for three years and with both real engines in the unit tests and soaks of earlier steps; the 100-year soak was not run; individual game kickoff times are carried but nothing enforces that a game is resolved at its kickoff (events are weekly).

**Checks run on the final code of Steps 3 to 5 (2026-10-29).** Full unit-test suite `python3 -I -m unittest discover -s tests`: **440 tests, all pass** (401 before this round: 22 archive-consistency and 17 season-controller tests added; one archive test now expects the new refusal). Validator `tools/validate.py data/season1 --doc …Ed8.52.md`: 75 of 75. 30 generated seasons, each validated with its own year's calendar: 30 validated, 0 failures. Ledger soak (3 years): PASS. Full-league soak (3 years, 22-player engine): PASS, 2,174 s. Restore soaks, one year, killed and resumed and restored: play-level engine (4 deaths) PASS, and the **22-player engine** (3 deaths, backup restored with its archive) PASS, 2,411 s; both with identical journal digests at all 49 events, identical exhaustive league digest and identical archive digest across the uninterrupted, killed-and-resumed and restored runs. A check against the previous commit with the placeholder resolver: the date change leaves the league's results (winners, venues, ledger digests) identical. Not run: the 100-year soak, seeds 16 and 17, the full fairness measurement.


## 13. Owner rulings of 2026-10-29 (items 4, 8, 9, 14) implemented, with their clarifications

Approved as recommended; nothing broadened. Spec Ed 8.53 (§2, §3, §7, §11, §12, §19, §20 items 4, 8, 9, 14); wording and the corrected statements are in `docs/OWNER_RULINGS_NEEDED.md`.

| Item | What changed | Proof |
| --- | --- | --- |
| 4 Bowl | **Spec only.** Next-ranked medically available replacement from the same conference's four Ambassador clubs, count not rerun. Exhausted ballot: the place is filled by a new distinct player, never by moving a rostered player; permanent position, emergency game assignment and the one exception to the positional minimums are separate things (§7). Rings go to the final 53 present and medically ready at kickoff. Bowl rosters and ballots belong to the people layer and are not built, so there is no code to test yet. | none (nothing to run) |
| 8 Rosters | Values confirmed (48/47, 2 elevations a game, 3 a player, 4-game IR, 8 returns). Inactive players replace injured players only before the game-day roster locks, never during a game (corrected). Emergency list now covers every position (up to four dressed healthy players, nearest positions first, first names not repeated across positions); the engine takes the first listed player still in the game and not already on the field, so injuries during a game and the K/P clash are handled and no player holds two positions at once. The out-of-position penalty is still not built and stays a tracked engine requirement (§20 item 2). | `tests/test_emergency.py` (13, including 300 random injury trials) |
| 9 Standings | One seeded priority per club per season (`standings.priority`) replaces the per-scope draw; division, seeding, wild card, Tour and draft all use the same one, so a scope never reverses an identical tie. Standings best first; draft order is the exact reverse. SOS/SOV direction, no decisions = 0 and the cascade are unchanged. | `tests/test_standings.py` `SeasonDrawPriority` (5) |
| 14 Calendar | Dates approved after checking against the leadership window (1–31 Jan on last season's records fixed at the year close), contract expiry (1 Jan league year turn) and the 31 December honours close. Corrected: the year turns on 1 January, which follows the Coronation "the next morning" only when it is on 31 December; otherwise 1–6 empty days lie between, and only the year close falls in them. **Trading is now kept by the ledger itself** (`open_trading` at the 1 February event, closed at the Friday close, at the end of the season and at the year turn), so January, the postseason and December are refused whoever asks. | `tests/test_calendar.py` `YearEnd` (3: all seven gaps, every year 2000–2299); `tests/test_season_controller.py` (3 simulated years with gaps 0, 1, 2 days; window closed in January, open only between opening and close); `tests/test_ledger.py`; `tests/test_trading_window.py` (7) |

**Saved-state compatibility (handled explicitly, not by keeping the old rule).** Ledger state written before this change has no window entry. `neutral.ADAPTERS` fills it as "unknown, treated as closed", and a second adapter on the league object settles it from the events already fired (open exactly between that year's `trading_open` and `trade_close`). The state document therefore differs by one new entry, so `upgrade` of an older file needs `accept_state_change=True` and says so (tested: the refusal without it, and an upgrade of a file written by the previous code at every stage of a year: the ledger gets the window the calendar gives it). `RENAMES` stays empty.

**Side effect to know about.** A tie that nothing before the last step separates is now broken by a different number, so a seeded league that reached that step may come out differently from an earlier run. In the earlier simulation of 400 seasons the draw was never reached, so this should be rare, but it is not zero. Nothing else in the rules changed. Earlier league files are still refused unless upgraded, as before.


## 14. Step 6: scheduling and rotation verification (2026-10-29)

Scope: `schedule_bed_rotation.py`, `league.season_tables` and the state hand-off, against Ed 8.53 §3 (beds, rotation, seeds), §6 (schedule) and §7 (Tour). Tests: `tests/test_schedule_rotation.py` (13, new), written from the spec and the Season 1 template rather than from the code. Live-clock pacing is not part of this step (deferred).

**Verified (48 consecutive seasons through the real season loop and hand-off, six full eight-season cycles; the rotation itself for 64).**
- Rotation: every division moves exactly one bed on each season inside its block (bed 4 to bed 1, bed 8 to bed 5); the conferences flip every fourth season; in any eight consecutive seasons a division fills every bed exactly once; the founding table and the formula in §3 agree; clubs never change division.
- Hand-off: seeds 1–4 are the previous season's first four in each division's final order, the previous ambassador returns as seed 5, the last-place club is exiled to ambassador, 48 distinct clubs, 40 active.
- Schedule: every season is the Season 1 game list (week, game, kickoff, bed-and-seed of both clubs) filled by whoever holds each bed and seed; 360 games; weeks 1–14 have 20 games and all 40 clubs play, weeks 15–19 have 16; nobody plays twice in a week; exactly one bye each in weeks 15–19, eight clubs a week, one seed group a week; each club plays 18 games, 9 home and 9 away; each division rival twice (once home, once away); exactly 4 interconference games against one club from each opposing division; 6 conference games, two against each other division; no other repeat; ambassadors play no regular-season game; game dates fall on the Saturday, Sunday or Monday of that year's own calendar week with the first game Saturday 17:30 and the last Monday 17:30; every club's kickoff-slot points total 33–38.
- Tour: 28 games, a round robin among the eight ambassadors of that season, nominal home 3 or 4 times per club and never three consecutive rounds, every season.
- The validator's own 20-season check (`tests/test_league.py`) and the 30-season generated-table run are unchanged and pass.

**Finding to know about (a property of the design in §6, not a defect, and not changed).** *Rotation moves when and where a club plays, not whom.* The template pairs bed-and-seed positions, and every division moves together, so with a club's seed held fixed its 18 opponents are the same every season (checked over 16 seasons). The rotation changes the order of the games, the kickoff slots and the home/away pattern; opponents change only because seeds change with the final standings (equal-seed interconference games, seed-based conference games). Over any eight seasons at a fixed seed, home and away against each opponent balance exactly (4 and 4 for single games, 8 and 8 for division rivals). The owner may want to know this when judging whether the rotation does what was hoped. Over 64 simulated seasons with random results, interconference pairings of two clubs ranged from 2 to 20 meetings (mean 8.9) because seed changes drive them; this is indicative, since real results will differ.

**Not verified.** Real-engine seasons were not re-run for this step (the schedule does not depend on the engine); the 100-year soak was not run; no reading of what a viewer would show for the rotation, since presentation is not built.

**Checks run on the final code of sections 13 and 14 (2026-10-29).** Full unit-test suite `python3 -I -m unittest discover -s tests`: **483 tests, all pass** (440 before; 43 added: 13 emergency, 5 draw priority, 3 calendar year-end, 1 ledger window, 7 trading-window/compatibility, 13 schedule and rotation, 1 season-controller honours close). Validator `tools/validate.py data/season1 --doc …Ed8.53.md`: 75 of 75. Ledger soak (3 years): PASS, final digest `9fdf159d…` unchanged from the baseline. Restore soak, one year, play-level engine, 4 deaths: PASS, journal digests identical at all 49 events and identical league and archive digests across the uninterrupted, resumed and restored runs. **One unexplained transient:** in the first full run (470 tests, before the schedule tests were added) `test_upgrade.RoundTrip.test_other_ways_an_import_is_refused` failed once with a state-digest mismatch on import; it passed on its own, in 19 further runs under different hash seeds, in combination with the new test modules, and in the second full run. I could not reproduce it and do not know the cause; I am recording it rather than calling it fixed, because it concerns the export/import comparison. Not re-run after the change: the 22-player restore soak and full-league soak (neither touches the changed code paths beyond what the play-level runs and the unit tests cover), the 100-year soak, seeds 16 and 17, the full fairness measurement.
