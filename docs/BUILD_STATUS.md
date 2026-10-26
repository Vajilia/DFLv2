# DFLv2 build status

**Authority.** The owner ruled on 2026-10-22 that anything decided in the Design Document (`docs/spec/DFL_Design_Document_Ed8.37.md`) takes priority over anything salvaged from `Vajilia/DFL` (v1). Where v1 code or docs disagree with the spec, the spec wins; v1 is a library of ideas, ported only after checking it against the spec.

## Milestone 1: league layer (this branch)
Verified on a clean clone (`python3 -I -m unittest discover -s tests`, then `python3 -I tools/run_seasons.py`):

| Piece | File | Status |
| --- | --- | --- |
| Specification, Edition 8.7 (stamped, hashed) | `docs/spec/` | verified: `tools/validate.py --doc` |
| Season 1 tables (the schedule template) | `data/season1/` | verified: validator, 75 checks |
| Standings, tiebreak cascade, playoffs, exile hand-off, Coronation venue window | `src/dfl/standings.py` | verified: 15 tests incl. mutation-checked rules |
| Eight-season bed rotation | `src/dfl/schedule_bed_rotation.py` (pre-existing) | verified against spec section 3 for 64 seasons |
| Season loop: relabel template by (bed, seed), play, rank, exile/return, playoffs, Coronation | `src/dfl/league.py` | verified: 9 tests, 120 seasons |
| Game resolver seam and a **placeholder** resolver | `src/dfl/resolver.py` | placeholder only |
| Multi-season runner and report | `tools/run_seasons.py` | verified: 100 seasons, 0 validator failures |

The placeholder resolver can exercise structure, standings and rotation. It is **not** evidence for scoring realism, tie frequency, or fairness bands; those need the real game engine.

## Phase 0: intake and decisions (2026-10-22)
| Item | Status |
| --- | --- |
| v1 audit against Edition 8.7 | done, source read only (nothing executed): `docs/V1_DIVERGENCES.md`, 22 rows, all five questions answered |
| CI on every push | done: `.github/workflows/ci.yml`, first run green on GitHub |
| Milestone 1 merged to `main` | done (fast-forward, with the owner's consent) |
| Snapshot zip import and gap analysis | cancelled: no snapshot exists; DFLv2 is rebuilt from the spec |
| Owner decisions (cap banking, practice squad size, last-place wild card, IR returns, season length) | done: Edition 8.7 |

## Phase 1: roster and cap ledger (built; `docs/LEDGER.md`)
| Piece | Status |
| --- | --- |
| Contracts, bonuses, release/split/trade/waiver arithmetic, rookie scale | `src/dfl/ledger/contracts.py`; unit-tested |
| Rosters, practice squad, IR, cut to 53, floor supplement, viability gate, rollback | `src/dfl/ledger/ledger.py`; unit-tested |
| Game day: active 48/47, elevations, emergency list, forfeit test | `src/dfl/ledger/gameday.py`; unit-tested |
| Determinism (same seed, same digest across hash seeds) | tested |
| 100-year soak, 48 clubs (2 bonus-heavy), seed 1, placeholder world, Ed 8.8 rules | **PASS** in 1,619 s: 86,400 games, 0 forfeits, 0 legal-move failures; 642 short-handed games (0.7%, up from 32 under Ed 8.7 because the cap now binds in season) and 142 emergency lists; 2,324 trades (76 refused), 895 claims; lowest payroll at the cut 509. Final digest `96fec3c8c0dd...`. CI runs a 3-year version. |

Read the soak honestly: "invariant never violated" means every club is inside the limit at each season start and at each year-end audit, and no move ever worsened an over-limit year. Within a season clubs drift over the limit as earned salary accrues (peak committed 1,422 against a 735 limit, margin minimum -447; in-season overage peak 66; the pre-cut overage peak of 358 is allowed by Ed 8.8). The world driving the soak is a placeholder, so rates (supplements, trades, injuries) say nothing about realism.

## Phase 2: the full calendar (built; exit test result below)
| Piece | File | Status |
| --- | --- | --- |
| The DFL year (Jan 1 to Dec 31), offseason table, in-season events derived from the Coronation | `src/dfl/calendar.py`, `data/OFFSEASON.csv` | tested for 300 years; Season 1 reproduces the published CALENDAR table exactly |
| Season played step by step: weeks, Tour rounds, venue draw, playoffs, Bowl, Coronation | `src/dfl/league.py` (`SeasonRun`) | tested (28 Tour games, 8 distinct venues, Bowl, hand-off) |
| Draft order, returning-ambassador lottery, NFL-netted compensatory picks | `src/dfl/draft.py` | tested (incl. lottery uniformity over 4,000 draws) |
| Ledger events as separate steps, free-agent movement log | `src/dfl/ledger/` | tested |
| The integrated year: every event fires once, in date order, league and ledger in step | `src/dfl/year.py`, `tools/run_years.py` | tested (3-year unit test; 100-year run below) |

**Exit test (Edition 8.11 rules, seed 1): PASS.** 100 full years: 4,900 calendar events (49 a year), each fired once in date order; 100 seasons of tables passed the validator (0 failures); 77,600 game days, 0 forfeits, 45 short-handed, 9 emergency lists, 0 legal-move failures; 33,601 draft picks made; 2,860 trades (38 refused), 1,179 waiver claims; all 48 Coronation venues used. Final digest `126b76533613...`. Command: `python3 -I tools/run_years.py --years 100 --seed 1` (about 33 minutes).

Caveats: 846 picks (2.5%) went unsigned, almost all at the two deliberately reckless clubs sitting at their cap limit (the NFL reserves cap for its rookie pool; spec open item 10). Only 1 compensatory pick was awarded in 100 years because the placeholder price curve almost never pays a $75 first-year base; the comp rules are covered by unit tests, not by this run.

Found by integrating (and fixed): the ledger closed trades one week early (now matches the calendar, with a test); a club at the cap could not fill a positional minimum (autopilot now makes cap room); the 75-player preseason ceiling could not hold a 69-man club plus a full draft class (owner ruled the NFL's 90, Edition 8.11).

Not built (people layer, engine): leadership hiring, All-Star voting and rosters, real snap shares for compensatory picks, exile-return parity measurement.

## Ed 8.12 ledger changes and the character creator (2026-10-23)
- **Verified by tests:** four-consecutive-year pool limit (retire and delete; reset on hire), rookie reserve in the cap number (unmade picks at rookie scale; 10-year check: 0 unsigned picks). **100-year rerun under Ed 8.12 (seed 1): PASS.** 4,900 events in order, 100 validator passes with 0 failures, 77,600 game days, 0 forfeits, 40 short-handed, 10 emergency lists, 0 legal-move failures, 33,600 draft picks made and **0 unsigned** (was 846 of 33,601), 2,850 trades (36 refused), 1,140 waiver claims, 0 compensatory picks (placeholder prices rarely reach $75), 48 distinct Coronation venues, digest `471e8541f9ea...`, 1,077 s.
- **Character creator (Ed 8.13): built, standalone.** `src/dfl/characters/`, data in `data/characters/`, 15 tests, sample output via `tools/show_cards.py`. **Placeholder honesty:** the attribute list, means, spreads and age curve are provisional until the engine exists, and overall ratings are not comparable across positions yet. Not yet wired to the ledger world (still placeholder `ovr`).

- **Jersey numbers (Ed 8.15): built in the ledger**, 9 tests. A real finding: a position's block caps how many such players a club can carry (e.g. 10 CBs), so the placeholder autopilot now sheds surplus players; **100-year rerun under Ed 8.15 (seed 1): PASS.** 4,900 events in order, 100 validator passes with 0 failures, 77,600 game days, 0 forfeits, 34 short-handed, 8 emergency lists, 0 legal-move failures, 33,601 picks made and 0 unsigned, 2,883 trades (20 refused), 1,506 waiver claims, 1 compensatory pick, 48 distinct Coronation venues, digest `8ef20974c043...`, 1,226 s.

## Game engine, slice 1 (2026-10-23)
- **Built:** `src/dfl/engine/` (rules core per spec section 10, play-level resolver, injuries), `data/engine/` (parameters, penalties, bands), `tools/calibrate_engine.py`, 13 tests. About 290 games per second. **Placeholder honesty:** the numbers are tuned to NFL aggregates (21 to 22 of 24 bands met per seed, misses marginal); they say nothing yet about DFL parity. Measured tie frequency is about 1.5% of regular-season games (owner accepted, Ed 8.18). Wired into the season loop (`tools/run_seasons.py --engine`, 100 seasons, 0 validator failures); the Bowl uses placeholder All-Star squads (best four per position of the Tour clubs). With frozen rosters the league is lopsided over time (clubs exiled 1 to 48 times, one club 24 titles), as expected; this is the baseline for parity work, not a verdict. Details in `docs/ENGINE.md`.

## Cards joined to the ledger (Ed 8.18, 2026-10-23)
- **Built:** the ledger world's players are character cards. The founding league, every 672-player draft class and every pool top-up come from the `Creator`; each card develops once a year at offseason opening (`src/dfl/characters/development.py`, data in `data/characters/DEVELOPMENT.csv` and `AGE_CURVE.csv`); free agents retire by years past their own peak and low rating, on top of the four-year pool rule. The engine now plays from the ledger itself (`src/dfl/engine/ledger_engine.py`): a club's squad is its game-day active list, and in-game injuries are written back to the ledger as games missed. `Universe` uses it by default (`engine=False` keeps the placeholder resolver). 19 new tests (149 in all).
- **A real defect found and fixed on the way:** the placeholder autopilot signed the best free agent at the league minimum whenever a market offer failed (and when filling minimums), which was harmless while ratings did not matter and wrecked parity once they did (one club reached an 82 average against a league 74). Players now sign for the minimum only if priced at or near it. Prices were also re-centred (`PRICE_BASE`).
- **Living-league measurement** (`tools/league_health.py`, 40 years, seed 1, years 6 to 40 averaged): mean rating of the 53 holds at about 67.8 with average roster age 26.0 (no inflation or drift); club-strength spread 1.2 points; win-percentage spread 0.21 (the NFL's is about 0.19); about 20.5 points per team game; 5.7 ties and 8 exiles a season; strength explains win% (correlation 0.66); **24 distinct champions in 40 years, at most 4 titles each** (frozen rosters gave 12 champions and one club with 24 titles); the unsigned pool settles near 2,600 characters. Placeholder honesty: stand-in clubs pick by true overall (perfect information), prices are a placeholder curve, and the curves are the builder's, so this shows the league is stable and mixes, not that it is fair. It is the baseline for the Phase 4 bands.
- **100-year rerun under Ed 8.18 with cards, development and the engine (seed 1): PASS.** 4,900 events in order, 100 validator passes with 0 failures, 77,600 game days, 0 forfeits, 0 short-handed, 0 emergency lists, 0 legal-move failures, 33,600 draft picks made and 0 unsigned, 2,878 trades (47 refused), 890 waiver claims, 0 compensatory picks (placeholder prices rarely reach $75), 48 distinct Coronation venues, digest `3fdd57a78389...`, 3,520 s.

## Phase 4 first pass: fairness bands (2026-10-23)
- **Built:** `src/dfl/fairness.py`, `tools/fairness.py`, `data/fairness/BANDS.csv` (v1's 15 bands, owner-accepted 2026-10-04), 12 tests. Six leagues of 36 seasons on the living league. First pass (Ed 8.18): 10 PASS, 2 FAIL, 3 UNCLEAR; the owner then restored the v1 returner draft rule (Ed 8.19) and the re-measure gave 13 PASS, 1 FAIL (win % spread 0.207 against 0.19), 1 UNCLEAR; the owner then raised that ceiling to 0.21 (Ed 8.20, the NFL's own spread is about 0.19 to 0.21), so the current score is **14 PASS, 0 FAIL, 1 UNCLEAR (exile effect)**. Full tables, definitions and caveats in `docs/FAIRNESS.md`. No owner decision is waiting on the bands; all must be re-run when the people layer arrives.

- **Phase 4 remainder (2026-10-23):** two 100-season leagues show no drift (rating, scoring and club spread flat; no decade over 3 titles for one club); schedule push toward average measured at +0.002 per rating point, i.e. none; exile standard covered by the returner bands; parity test beds `none` and `rigged` (`src/dfl/parity.py`): the rigged control fails 8 of 15 bands and removing the cap alone fails 5, so the bands do see unfairness. Details in `docs/FAIRNESS.md`.

## People layer, slice 5.1: scouting views (Ed 8.21, 2026-10-23)
- **Built:** every club decision about players (draft, free agency, releases, practice squad, waivers) uses a noisy club view instead of true ability; error shrinks with credited seasons and the club GM's evaluation rating, and is smaller for a club's own players. Data: `data/characters/SCOUTING.csv`, `SCOUTING_BIAS.csv` (placeholders). 5 tests in `tests/test_living.py`. Plan for the rest of the layer: `docs/PEOPLE.md`.
- **Not yet:** game day and the engine still use true ability (spec item 22). Fairness re-measure with scouting on (six tight leagues): 12 PASS, 3 UNCLEAR, 0 FAIL; the core measures did not move (`docs/FAIRNESS.md`). Watch: exile effect, repeat champion rate.

## People layer, slice 5.2: souls (Ed 8.22, 2026-10-23)
- **Built:** fixed archetype per player (six placeholders), self-view bias, bounded yearly trait drift (`src/dfl/characters/soul.py`, `ARCHETYPES.csv`, `SOUL.csv`); 8 tests; full suite 183 pass. Fairness re-measure (six tight leagues, scouting and souls on): 12 PASS, 3 UNCLEAR, 0 FAIL, no band moved out of range; see `docs/FAIRNESS.md`.

## People layer, slice 5.3: player demands (Ed 8.23, 2026-10-23)
- **Built:** free agents above the minimum bargain from self-view, greed, ambition, loyalty and time waited; clubs pay up to their own scouting view plus slack (`World.demand`, `willing`, `negotiate`; `data/characters/NEGOTIATION.csv`); 6 tests. Fairness re-measure (six tight leagues, scouting, souls and demands on): 12 PASS, 3 UNCLEAR, 0 FAIL; see `docs/FAIRNESS.md`.

## People layer, slice 5.4: coaches (Ed 8.25, 2026-10-24)
- **Built, in the owner's structure:** one coach category, posts held not born into, one common pool of new, fired and resigned coaches, CEO over head coach and head coach over coordinators (hiring and firing in the leadership window, on last season's records), retirement, capped teaching effect (`src/dfl/ledger/coaching.py`, `data/characters/COACHING.csv`); 11 tests. Fairness re-measure (six tight leagues): 14 PASS, 1 UNCLEAR, 0 FAIL; see `docs/FAIRNESS.md`. Not built: coach pay, poaching, CEO and GM turnover (slice 5.5).

## Trades, stage 1 (Ed 8.26, 2026-10-24)
- **Built:** common public player list, private club valuation, trading block, three-round protocol with autopilot general managers, ledger dry-run, trade log; picks yes, no cash or cap space (`src/dfl/ledger/trading.py`, `data/characters/TRADING.csv`, `docs/TRADES.md`); 14 tests. Fairness re-measure (six tight leagues): 12 PASS, 3 UNCLEAR, 0 FAIL; the whole people layer leaves payroll, roster quality and talent concentration unchanged (`docs/FAIRNESS.md`). Trade volume is under 40 percent of the old stand-in's. Stages 2 to 4 (public market value, pluggable deciders, language-model agents) are proposals.

## Governance, slice 5.5 (Ed 8.27, 2026-10-24)
- **Built:** fan cards and sentiment, recall ballots, CEO term and vacancy elections, CEO and GM pools, GM dismissal and hire, hidden narrative-only GM discretion (`src/dfl/ledger/governance.py`, `data/characters/GOVERNANCE.csv`); 21 tests. Fairness re-measure (six tight leagues): 13 PASS, 2 UNCLEAR, 0 FAIL; see `docs/FAIRNESS.md`.

## Meeting rooms, slice 5.6 (Ed 8.29, 2026-10-24)
- **Built:** contract room (rounds, GM negotiating rating), trade room, interviews with candidate consent; recall rate about one a year; numbering repair at the final cut (`src/dfl/ledger/rooms.py`, `data/characters/ROOMS.csv`); 16 new tests. Fairness re-measure (six tight leagues): 13 PASS, 2 UNCLEAR (best_team_title_odds 0.262 against 0.25, exile_effect), 0 FAIL; see `docs/FAIRNESS.md`. Analytics definitions and the Hall of Fame are a proposal only (`docs/ANALYTICS_AND_HALL.md`).

## Production, analytics and halls, slice 5.7 (Ed 8.30, 2026-10-24)
- **Built:** EPA/WPA per-player recording (result-neutral, proven by a 240-game digest), the analytical score, Tiaras to the Coronation winner, 59 halls with All-Time registers and a yearly fan entrant (`src/dfl/engine/production.py`, `src/dfl/ledger/analytics.py`, `honours.py`, `data/analytics/`); 17 tests. Fairness: recording is result-neutral; twelve tight leagues 13 PASS, 2 UNCLEAR, 0 FAIL (`docs/FAIRNESS.md`).

## Expected-points refit (Ed 8.32, 2026-10-25)
- **Built:** the expected-points table is refit after every season from the league's own plays (next-score value, three-season window, smoothed, pulled toward the data table, never falling with yards gained) and used for the next season's valuation. Result-neutral (four seasons identical with and without it). Only the yard-line curve is refit; the down and distance costs are still data (spec item 31).

## Trades stage 2 (Ed 8.34, 2026-10-25)
- **Built:** the public trade price comes from public production, age and the going level at the position, not from the hidden overall (spec item 33). Edition 8.35 also moved the bargain-or-minimum test for free agents to each club's own view (spec item 34).

## Coach judgement (Ed 8.36, 2026-10-25)
- **Built:** game-day starters are chosen by the coaches' judged overall (true overall plus an error with no lean, shrinking with the player's seasons and with the head coach's game management and the coordinator's adjustments), fixed per staff, player and league year (`coaching.judged`, data keys `judge_*` in `COACHING.csv`; spec item 22 closed, item 35). The engine still reads true cards; only who dresses and starts changes. Still reading true ability: the all-star sides, `ledger.py` year-roll ranking, and the market-rating sort when no club asks.- **Ed 8.37:** the year-end top-quarter ranking that feeds compensatory picks now follows the coaches' judged overall (`Ledger.new_year(rank=...)`, taken before the year turns; spec item 36). Only the all-star sides and the market-rating sort with no club asking still read true ability.

## Not built yet (next)
1. The real game engine behind the `Resolver` interface (spec open item 2).
2. Fairness bands and parity profiles, then the people layer, persistence and pacing, presentation (see ROADMAP).
3. Everything in spec section 20.

## Not imported
The 339-file `DFL-vBeta_source_snapshot_2026-10-20.zip` is not in this repository and has not been seen. Nothing here claims to include it.
