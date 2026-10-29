# DFLv2 roadmap

Written 2026-10-22, after Milestone 1. This is a plan, not a set of accepted decisions: each phase needs the owner's approval before it starts, and each "Needs from the owner" line is a decision only the owner can make. Update this file when a phase closes.

## How we work (applies to every phase)
1. **Spec first.** A rule is written into the Design Document (a new edition, hash-stamped, changelog row) before code implements it. If code and spec disagree, the spec wins; v1 (`Vajilia/DFL`) is a library of ideas, ported only after checking against the spec.
2. **Test with the change.** Each rule gets a unit test, and each phase ends with a long soak run (100+ seasons) against stated invariants. Rules are mutation-checked: break the rule on purpose and confirm a test fails.
3. **Placeholder honesty.** Anything standing in for a real model (the game resolver, development curves) is labelled as such, and no fairness or calibration claim rests on it.
4. **Small branches, owner merges.** One branch per milestone, verified from a clean clone, pushed, then merged by the owner. No production change, deploy, or reset of live league state without explicit approval and a rollback plan.
5. **Deterministic decisions only.** Every decision is made by deterministic rules, weighted choices and seeded randomness among the legal options the engine enumerates. No language model takes part in any simulation decision (owner directive, 2026-10-29; this replaces the earlier "agents second" plan). Language models, if ever used, may only write presentation text that is read-only and cannot reach the engine.
6. **Status you can trust.** `docs/BUILD_STATUS.md` says verified, untested, or proposed for each piece.

## Where we are
Updated 2026-10-29 (see `PROJECT_COMPLETION_PLAN.md` for the audited detail). Phases 0 to 3 are built, Phase 4's harness and first measurements exist, the Phase 5 people-layer slices 5.1 to 5.7 are built, and the storage half of Phase 6 (SQLite saving, atomic events, restart recovery, backup and restore) is built. The 22-player engine is the league default (Ed 8.52). Not built: the live clock and pacing, Phase 7 and Phase 8. There is no source snapshot; the engine was rebuilt from the spec. Many numbers (development curves, people-layer parameters, physics) are working values.

## The shape
Two products, in this order:
- **A. The league without people** (Phases 0–4, 6): anonymous players with ratings, autopilot decisions, a real game engine, a live clock. This is testable, fair-or-not can be measured, and it can run on the Droplet.
- **B. The league with people** (Phases 5, 7): character cards, living coaches, GMs and CEOs, negotiations, narrative (all rule-driven). Built on top of A so every addition can be tested against A's fairness bands.

Reason for the order: the people layer is the most expensive and least testable layer, and it cannot be judged for fairness until A exists. Shipping A first gives a running, watchable league early.

## Phase 0 — Intake and decisions (now)
- **Goal:** one source of truth and no hidden surprises.
- **Work:** import the snapshot zip, scan for secrets and junk, run its tests in a clean clone, and write a gap analysis against Edition 8.7. Audit v1 against the spec and list every divergence (known: tiebreak method, cap scale and floor, roster minimums, tie-game rule, fill-ins). Turn on CI (GitHub Actions running the tests on every push). Merge Milestone 1.
- **Exit test:** every divergence is a ticket marked adopt, reject or ask; CI is green.
- **Needs from the owner:** the zip; the cap decision (spec says $1,000 with an $800 floor and no banking; v1 says $100M, banking to $125M, 90% floor); approval to merge Milestone 1.

## Phase 1 — Roster and cap ledger (closed 2026-10-26, Ed 8.45)
- **Goal:** money, contracts and rosters that can never become illegal or stuck.
- **Work:** contracts (steady, rising, declining), dead money, the viability invariant, the cut to 53, practice squad, injured reserve, elevations, emergency lists, waivers, the legal-option enumerator that every decision point uses.
- **Exit test:** a 100-season soak under autopilot in which the invariant is never violated, every club always has at least one legal transaction, and no club is forced into forfeit by the rules themselves.
- **Needs from the owner:** open items on market mechanics (§20.3) as they come up.
- **Closed on the current rules (2026-10-26):** the ledger was first soaked at Ed 8.8; since then retirement under contract, upgrade signings, a 7-fold increase in trades and the living league all landed, so Phase 1 was re-closed. Added the legal-move enumerator (`Ledger.legal_moves`); ran a 100-year ledger soak and a 100-year full-league soak (real calendar, engine, free agency, draft, trades, waivers, governance): both pass with 0 forfeits and 0 clubs without a legal move; broke 15 rules on purpose and every one is caught by a test (three needed a new test that pins the spec's numbers). Results in `docs/BUILD_STATUS.md`.

## Phase 2 — Full calendar (done 2026-10-23)
- **Goal:** the whole year, not just the 18 games.
- **Work:** Ambassador Tour and Bowl played; offseason calendar (draft, free agency, trade deadline, cuts); the 8-team lottery and 48-pick draft; exile relief; the all-rookie Founding Day build.
- **Exit test:** 100 full years with every calendar event firing in order and every table validating.

## Phase 2.5 — Character creator (built 2026-10-23, Ed 8.13)
- **Goal:** one place that makes every character, before the engine needs them.
- **Work:** the character card (identity, hidden attributes, growth, traits, public view), a seeded creator, 672-player draft classes with a class-strength shift, the founding league population. Code in `src/dfl/characters/`, data in `data/characters/`, tests in `tests/test_characters.py`, sample cards from `tools/show_cards.py`.
- **Wired to the ledger (Ed 8.18, 2026-10-23):** the ledger world's players are cards (founding league, draft classes, pool top-ups from the Creator), cards develop and age yearly and free agents retire by age and rating (placeholder curves in `data/characters/DEVELOPMENT.csv` and `AGE_CURVE.csv`). The real attribute list still waits for the engine's needs (Phase 3).
- **Needs from the owner:** nothing now; the attribute set is revisited when the engine is specified.

## Phase 3 — Game engine (the big one) — built 2026-10-27 (Ed 8.49)
- **Done:** all six slices: run and pass plays with a drive model, penalties (Ed 8.46), injuries at NFL frequency, overtime and ties (measured: 1.4 to 1.8 percent play-level, 0.9 percent spatial), box scores and play-by-play (Ed 8.47), the play table and, for the 2D view, the per-snap trace table (Ed 8.48 and 8.49). The 22-player engine passes all 24 game bands at the default check and is the league default (Ed 8.52); the play-level engine remains for speed in tests and soaks. Other extensions are deferred (item 47); the trace archive is open (item 49). See `docs/ENGINE.md`.
- **Exit test:** met by the play-level engine (bands over thousands of games, tie frequency measured). Owner ruled 2026-10-27 (Ed 8.50): the 22-player engine becomes the default only at 24 of 24 bands, and the website's 2D animation is a read-only view of that engine's trace. Phase 3 is closed in Ed 8.52: the seven missed bands are closed (spec §20 item 46) and the default switched; the Phase 4 fairness measurement was re-run on the 22-player engine (docs/FAIRNESS.md).
- **Goal:** replace the placeholder resolver with real football.
- **Work, in slices, spec before code:** (1) run and pass plays and a drive model, (2) penalties from the ~20 most common NFL penalties, (3) injuries at NFL-average frequency, (4) overtime and ties, (5) box scores, (6) play log and the second per-play table for the 2D view. Each slice has calibration bands (scoring, plays per game, penalties, injuries, tie rate).
- **Exit test:** engine output sits inside the accepted football-reasonable bands over thousands of games, and tie frequency is measured, not assumed.
- **Needs from the owner:** the spatial-engine decisions (play catalogue, blocking, coverage, ball flight; §20.2). This is the largest open item. The snapshot may already contain much of it, which would shorten this phase a lot.

## Phase 4 — Fairness and calibration
- **First pass done (2026-10-23):** harness, bands as data and a six-league measurement (first pass 10 pass, 2 fail, 3 unclear; after Ed 8.19 13 pass, 1 fail, 1 unclear; after the owner raised the win % spread ceiling to 0.21 in Ed 8.20, 14 pass, 0 fail, 1 unclear); see `docs/FAIRNESS.md`. No owner decision waiting. The exit test (every band passing over many seeds) is not met yet, and it must be re-run when the people layer replaces the stand-in clubs.
- **Goal:** prove the league meets its own definition of fair competitiveness.
- **Work:** a harness for the fairness bands (v1's 15, in `rules.py`, are the starting point), the exile standard (competitive for 3rd or better within about 3 seasons, never automatic), strength-of-schedule push toward average, century-long runs, and the parity profiles. Build **tight** first; **loose** and **none** are separate test beds added afterward.
- **Exit test:** every band passes with error-bar verdicts over many seeds; any trend outside the bands is reported, not tuned away silently.
- **Needs from the owner:** whether the bands are too tight for long-term enjoyment (you've flagged this); approval of any band change.

## Phase 5 — People
- **Goal:** living characters, carefully bounded.
- **Progress:** slice 5.1 (scouting views) built, Ed 8.21; plan and remaining slices in `docs/PEOPLE.md`.
- **Work:** players first (archetypes as fixed souls, perception, personality, development, aging, retirement), then coaches (one pool for head coaches and coordinators, small capped effects), then GMs, CEOs and fans. Negotiations as meeting rooms only where independent parties need a say (contracts, trades, interviews). Analytics definitions (§20.1).
- **Exit test:** adding people leaves every Phase 4 band inside range; all character effects are capped and tested.
- **Needs from the owner:** development and aging curves (§20.3); anything that changes how agents may act.

## Phase 6 — Persistence, pacing and live clock
- **Goal:** a league that runs continuously and survives restarts.
- **Work:** storage (SQLite first, a path to Postgres), the live clock with game blocks and per-instance pause, restart-safe state, backups. Resolve the pacing contradiction: documents currently say about 48 hours, about 55 hours, and earlier 168 hours per season.
- **Exit test:** kill and restart mid-season with no lost or duplicated games; a full season completes on the chosen clock.
- **Needs from the owner:** the real-time length of a season (§20.5).

## Phase 7 — Presentation and story
- **Goal:** something worth watching.
- **Work:** read-only site restyle, richer play-by-play text, field strip and score timeline, 2D top-down view from the per-play table, the narrative layer (read-only, never visible to the engine; about $20 a month budget), the Commissioner page, chaos injections as in-world events.
- **Exit test:** a test proving the narrative layer cannot affect results; the site works from a Chromebook.

## Phase 8 — Release
- **Goal:** the live site switches over safely.
- **Work:** the Truth → Beta → Alpha → Truth pipeline written and tested; secrets, configuration and security review; Droplet deployment with backup and rollback rehearsed; a dry-run Founding Day; then Start.
- **Exit test:** a rollback rehearsal that works; owner sign-off. Nothing before this phase touches the live site.

## Risks to watch
1. **Scope.** This is a very large project. If time is short, stop after Phase 4 plus a minimal Phase 6: a watchable, fair league with anonymous players.
2. **Engine unknowns.** Phase 3 can overturn assumptions made earlier (tie rate, scoring, injury load). Build its calibration harness early.
3. **Determinism.** Language models are slow, costly and not reproducible, so none takes part in simulation decisions (owner directive, 2026-10-29).
4. **Three parity profiles triple the test surface.** Tight first.
5. **Knowledge outside the repo.** The snapshot and earlier decisions live in other conversations. Phase 0 exists to bring them in.
6. **Specification drift.** Several documents disagree (pacing, cap, tiebreaks). The Design Document is the single authority, and each phase ends with an edition bump.

## Not doing (until the owner says otherwise)
Deploying anything, changing the live site or `Vajilia/DFL`, or running anything against live state; 3D visuals; a commercial or multi-user product beyond a handful of peers.
