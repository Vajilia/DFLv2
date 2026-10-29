# DFL v2 — Project Completion Plan

Audit date: 2026-10-29. Branch: `audit/completion-plan` (from `milestone-1-league-layer` at 4232e22; no newer work exists on the remote, which holds only `main` and `milestone-1-league-layer`).

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
| Unit tests (`unittest discover`) | **333 tests OK**, 733.8 s (RE-RUN) |
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
9. **Long-run stability on the spatial default.** Only 3-year soaks and 30 structural seasons are verified. No 100-year soak on the spatial default. numpy cross-platform determinism is unverified (CI runs on one platform).

## 4. Missing

| Phase | Missing | Notes |
| --- | --- | --- |
| 6 Persistence, pacing, live clock | SQLite persistence; restart safety; real-time pacing (target ≈50 h/season, non-binding per Ed 8.7); backups; trace archive | Nothing of this exists in `src` |
| 7 Presentation / story | Website with the six entries; 2D animation viewer on the spatial traces; narrative layer; Commissioner page; chaos injections | `boxscore.py` has `play_by_play` and `narrate` as seeds. Narrative must be template/rule-based (C1) |
| 8 Release | Truth → Beta → Alpha → Truth pipeline; secrets and security review; deployment; rollback rehearsal | Out of scope until the owner approves any deploy |
| Spec items | Open §20 items 1–50 that are not yet resolved (see C-list) | |
| Docs | Stable rule IDs (item 6) | |

## 5. Conflicting or unresolved requirements

| ID | Conflict | Proposed handling |
| --- | --- | --- |
| C1 | **Language-model deciders.** `docs/TRADES.md` stage 3 (approved "later") and the roadmap's "agents second" assume LLM decision-makers. The new directive forbids LLMs in simulation decisions. The spec is silent. | Treat the directive as binding. Replace stage 3 with deterministic weighted deciders. Narrative text, if any, is template-driven and never feeds back into state. Needs owner confirmation and a doc edit. |
| C2 | **Pacing.** README says 48 h; spec §0 arithmetic ≈55 h; owner ruling target ≈50 h (Ed 8.7, non-binding); §20 item 5 open. | Use ≈50 h as a non-binding target; fix README. |
| C3 | **Snapshot/import.** `BETA_STATUS.md` and `SOURCE_IMPORT_STATUS.md` demand importing a snapshot; BUILD_STATUS and V1_DIVERGENCES say no snapshot exists and v2 is rebuilt from the spec. | Retire the import documents. |
| C4 | **Stale text.** README ("24 tests", documentation-only); BUILD_STATUS ("Edition 8.7" row, stale "Not built yet" list); ROADMAP "Where we are"; `requirements.txt` "opt-in" comment on numpy. | Refresh in the first doc-cleanup commit. |
| C5 | **Calibration claim vs result.** Docs say the play-level engine passes all 24 bands; seed 1 shows one miss. | Re-state the claim with the seed range, or fix the band. |
| C6 | **Silent-spec items defaulting to NFL.** V1-11 (offseason count: top 51 salaries) is "default NFL, add to spec once confirmed". | Owner to confirm; keep flagged until then. |
| C7 | **Open §20 items called out in the audit:** 4 (Bowl roster repair), 6, 7, 8, 9, 14, 49 and all others in 1–50 not listed here. | Review the full list with the owner; each needs a ruling or an explicit "defer". |

## 6. Itinerary (ROADMAP Phases 0–8, adjusted)

| Stage | Status | Remaining work |
| --- | --- | --- |
| 0 Intake | Done, verified | none |
| 1 Roster / cap ledger | Done, verified | none |
| 2 Full calendar | Done, verified | none |
| 2.5 Character creator | Done, numbers provisional | tune with Phase 4 |
| 3 Game engine | Done, provisional calibration | resolve play-level margin_sd miss; seed 16/17 overruns; 100-year soak |
| 4 Fairness | Harness done, results partial | full 36-season × 6-league measurement (~35 h) after people-layer numbers are frozen |
| 5 People | Built, provisional | replace true-ability stand-ins; tune; rule on decisions needed in `docs/PEOPLE.md` |
| 6 Persistence / pacing / live clock | **Not started** | next major build |
| 7 Presentation / story | **Not started** | after 6 |
| 8 Release | **Not started** | after 7; no deploy without owner approval |

## 7. Recommended next stage

**Stage A (small, first): housekeeping and decisions.** Resolve C1–C6 with the owner, refresh stale docs, and settle the play-level margin_sd miss (re-state or fix). No simulation changes.

**Stage B: Phase 6 start.** Persistence design: SQLite schema for league state, a deterministic save/restore round-trip test (restore at any week must reproduce the digest), then the pacing/clock layer.

**In parallel (background compute, not blocking):** a 100-year soak on the spatial default, and a cross-platform numpy determinism check in CI.

Do not merge this branch, deploy, or touch the live site, `Vajilia/DFL`, or `step6-rebuild`.
