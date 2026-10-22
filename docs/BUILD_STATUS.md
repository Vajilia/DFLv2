# DFLv2 build status

**Authority.** The owner ruled on 2026-10-22 that anything decided in the Design Document (`docs/spec/DFL_Design_Document_Ed8.7.md`) takes priority over anything salvaged from `Vajilia/DFL` (v1). Where v1 code or docs disagree with the spec, the spec wins; v1 is a library of ideas, ported only after checking it against the spec.

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
| 100-year soak, 48 clubs (2 bonus-heavy), seed 1, placeholder world | **PASS** in 2,075 s: 86,400 games, 0 forfeits, 0 legal-move failures, 32 short-handed games, 9 emergency lists, 2,387 trades (13 refused), 992 waiver claims, 1,189 floor supplements. Final digest `49a0b5eb38da...`. CI runs a 3-year version. |

Read the soak honestly: "invariant never violated" means every club is inside the limit at each season start and at each year-end audit, and no move ever worsened an over-limit year. Within a season clubs drift over the limit as earned salary accrues (peak committed 1,380 against a 735 limit, margin minimum -405; overage peak 130). Floor supplements are exempt from the gate (A3). The world driving the soak is a placeholder, so rates (supplements, trades, injuries) say nothing about realism.

## Not built yet (next)
1. (Ledger is done; see above.)
2. Ambassador Tour and Bowl results (the tables are generated; games are not played).
3. The real game engine behind the `Resolver` interface (spec open item 2).
4. Everything in spec section 20.

## Not imported
The 339-file `DFL-vBeta_source_snapshot_2026-10-20.zip` is not in this repository and has not been seen. Nothing here claims to include it.
