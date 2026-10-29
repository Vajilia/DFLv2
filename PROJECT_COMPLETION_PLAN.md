# DFL v2 — Project Completion Plan

Audit date: 2026-10-29, updated the same day after the housekeeping pass (section 9) and the durable-saving build (section 10). Branch: `audit/completion-plan` (from `milestone-1-league-layer` at 4232e22; no newer work exists on the remote, which holds only `main` and `milestone-1-league-layer`).

## 0. Ground rules this plan follows

- **Authoritative edition: Ed 8.52** (`docs/spec/DFL_Design_Document_Ed8.52.md`, hash `269082ad…e9633`). The validator confirms the hash and 75 checks. The spec wins over every other document, including v1 (`Vajilia/DFL`), which is a library of ideas only.
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
8. **Traces.** Not stored for league games (item 49), so a played game cannot be replayed from a league run.
9. **Long-run stability on the spatial default.** Only 3-year soaks, one restore-soak year and 30 structural seasons are verified. No 100-year soak on the spatial default. numpy cross-platform determinism is unverified (CI runs on one platform).
10. **League files and code releases.** A league file opens only under the exact source and data that wrote it (section 10, C11). File growth over 100 years is estimated, not measured.

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
| C2 | **Pacing.** README 48 h; spec §0 arithmetic ≈55 h; owner ruling ≈50 h (Ed 8.7, non-binding); §20 item 5. | **Resolved.** README and ROADMAP say ≈50 h, non-binding. The clock mapping itself is Phase 6 clock work |
| C3 | **Snapshot/import.** `BETA_STATUS.md` and `SOURCE_IMPORT_STATUS.md` demanded a snapshot that does not exist. | **Resolved.** Both marked superseded (kept as history); the README import section corrected |
| C4 | **Stale text** (README, BUILD_STATUS edition row and "next" list, ROADMAP "Where we are", `requirements.txt` numpy comment). | **Resolved** |
| C5 | **Calibration claim vs result.** Docs said the play-level engine passes all 24 bands. | **Resolved by restating the claim**, not by changing the engine or the bands. Re-measured: seeds 1, 2, 3 (1,000 games each) miss 1, 2, 3 bands, all marginally. The play-level engine is the fast mode and is not held to 24/24; the owner ruling of Ed 8.50 puts that bar on the spatial engine, which meets it at the default check. The golden digest is untouched |
| C6 | **V1-11 offseason cap count** was marked "default to NFL". | **Resolved:** Ed 8.9 (§11) already defines it: before the final cut a club's cap number is dead money plus its highest 69 contracts (the NFL top-51 method adapted). `V1_DIVERGENCES.md` updated |
| C7 | **Open §20 items.** | **Open; none blocks the next stage.** Needing an owner ruling when convenient, with the builder's recommended default: **4** Bowl roster repair (fill from the next-ranked ballot candidates of the same conference, then the emergency list); **8** the NFL-inherited fill-in parameters (confirm 48/47, 2 elevations a game, 3 a player, 4-game IR minimum as built); **9** standings assumptions (confirm higher strength of schedule ranks higher, no decisions = 0 win %); **14** offseason dates (confirm as built). Routine, no ruling needed: **6** rule IDs (next spec edition), **7** ties (monitored figure), **49** trace archive (with the website), **5** pacing map (Phase 6 clock) |
| C8 | **Home-field edge at a neutral venue.** | **Approved by the owner 2026-10-29:** neutral venues provide no home-field advantage. Implemented (section 8) |
| C9 | **Nominal home of the Bowl** (no template, no seeds). | **Approved by the owner 2026-10-29:** a seeded draw. Implemented (section 8) |
| C10 | **Opening possession and the nominal home.** The owner asked that opening possession follow "the established DFL coin-toss rule", independently of the nominal home. | **No such rule exists, and no gap.** Spec §10 says: "Home kicks to start the first half, away the second. No coin toss. In overtime the away side kicks first, alternating each period." The DFL has no coin toss; "home" there means the nominal home, which §6 assigns at neutral sites and §7 repeats for the Tour ("The nominal home kicks the first half"). So the earlier statement that the nominal home "still kicks first" is exactly the spec rule, and nothing was added. A test (`OpeningPossession`) now locks it in for home and neutral games, and checks the engine contains no coin toss. If the owner wants a coin toss or a possession rule independent of the nominal home, that is a substantive rule change for a new edition; the simplest fair candidate would be a seeded draw per game, but none is implemented |
| C11 | **League files are tied to the code that wrote them** (section 10). | **Open, for Phase 8.** Recommended: add a neutral (JSON/CSV) export and a tested import before the first release, or freeze code between releases |

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
| 6 Persistence / pacing / live clock | **Storage half built and verified** (section 10); clock half not started | live clock, game blocks, pause; backup scheduling |
| 7 Presentation / story | **Not started** | after 6 |
| 8 Release | **Not started** | after 7; no deploy without owner approval |

## 7. Recommended next stage

**Next: the live clock and pacing (Phase 6, second half).** Map simulated events to real time (target ≈50 h a season, non-binding), run game blocks on that clock with per-instance pause, and have the clock process call the durable `Runner` one event at a time, with scheduled backups. The exit test is the roadmap's: a full season completes on the chosen clock, and a kill and restart mid-season loses and duplicates nothing (the second half of that test is already proven; section 10).

**In parallel, as background compute:** a 100-year soak on the spatial default, a second platform in CI for numpy determinism, and the full 36-season × six-league fairness measurement once the people-layer numbers are frozen.

**Owner rulings wanted when convenient (none blocks the clock):** C7 items 4, 8, 9, 14, and C11 (how league files survive a code release).

Do not merge this branch, deploy, or touch the live site, `Vajilia/DFL`, or `step6-rebuild`.

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

