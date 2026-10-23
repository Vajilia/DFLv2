# DFLv2 build status

**Authority.** The owner ruled on 2026-10-22 that anything decided in the Design Document (`docs/spec/DFL_Design_Document_Ed8.12.md`) takes priority over anything salvaged from `Vajilia/DFL` (v1). Where v1 code or docs disagree with the spec, the spec wins; v1 is a library of ideas, ported only after checking it against the spec.

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

## Not built yet (next)
1. The real game engine behind the `Resolver` interface (spec open item 2).
2. Fairness bands and parity profiles, then the people layer, persistence and pacing, presentation (see ROADMAP).
3. Everything in spec section 20.

## Not imported
The 339-file `DFL-vBeta_source_snapshot_2026-10-20.zip` is not in this repository and has not been seen. Nothing here claims to include it.
