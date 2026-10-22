# DFLv2 roadmap

Written 2026-10-22, after Milestone 1. This is a plan, not a set of accepted decisions: each phase needs the owner's approval before it starts, and each "Needs from the owner" line is a decision only the owner can make. Update this file when a phase closes.

## How we work (applies to every phase)
1. **Spec first.** A rule is written into the Design Document (a new edition, hash-stamped, changelog row) before code implements it. If code and spec disagree, the spec wins; v1 (`Vajilia/DFL`) is a library of ideas, ported only after checking against the spec.
2. **Test with the change.** Each rule gets a unit test, and each phase ends with a long soak run (100+ seasons) against stated invariants. Rules are mutation-checked: break the rule on purpose and confirm a test fails.
3. **Placeholder honesty.** Anything standing in for a real model (the game resolver, development curves) is labelled as such, and no fairness or calibration claim rests on it.
4. **Small branches, owner merges.** One branch per milestone, verified from a clean clone, pushed, then merged by the owner. No production change, deploy, or reset of live league state without explicit approval and a rollback plan.
5. **Autopilot first, agents second.** Every decision has a deterministic autopilot that picks among legal options the engine enumerates. AI agents are an optional layer on top, never required for a season to run.
6. **Status you can trust.** `docs/BUILD_STATUS.md` says verified, untested, or proposed for each piece.

## Where we are
Milestone 1 is done and merged to `main`; Phase 0 is partly done (divergence register, CI, merge) and waits on the snapshot zip and five owner decisions (`docs/V1_DIVERGENCES.md`). Milestone 1 contents: specification Edition 8.6, Season 1 template, standings and tiebreaks, the eight-season rotation, a season loop with a placeholder game resolver, 24 tests, a 100-season run with 0 validator failures. Missing: the 339-file snapshot (`DFL-vBeta_source_snapshot_2026-10-20.zip`, still only in the ChatGPT conversation), the roster and cap ledger, Tour and Bowl games, the real game engine, and everything about people.

## The shape
Two products, in this order:
- **A. The league without people** (Phases 0–4, 6): anonymous players with ratings, autopilot decisions, a real game engine, a live clock. This is testable, fair-or-not can be measured, and it can run on the Droplet.
- **B. The league with people** (Phases 5, 7): character cards, living coaches, GMs and CEOs, agents, negotiations, narrative. Built on top of A so every addition can be tested against A's fairness bands.

Reason for the order: people-and-agents is the most expensive and least testable layer, and it cannot be judged for fairness until A exists. Shipping A first gives a running, watchable league early.

## Phase 0 — Intake and decisions (now)
- **Goal:** one source of truth and no hidden surprises.
- **Work:** import the snapshot zip, scan for secrets and junk, run its tests in a clean clone, and write a gap analysis against Edition 8.6. Audit v1 against the spec and list every divergence (known: tiebreak method, cap scale and floor, roster minimums, tie-game rule, fill-ins). Turn on CI (GitHub Actions running the tests on every push). Merge Milestone 1.
- **Exit test:** every divergence is a ticket marked adopt, reject or ask; CI is green.
- **Needs from the owner:** the zip; the cap decision (spec says $1,000 with an $800 floor and no banking; v1 says $100M, banking to $125M, 90% floor); approval to merge Milestone 1.

## Phase 1 — Roster and cap ledger
- **Goal:** money, contracts and rosters that can never become illegal or stuck.
- **Work:** contracts (steady, rising, declining), dead money, the viability invariant, the cut to 53, practice squad, injured reserve, elevations, emergency lists, waivers, the legal-option enumerator that every decision point uses.
- **Exit test:** a 100-season soak under autopilot in which the invariant is never violated, every club always has at least one legal transaction, and no club is forced into forfeit by the rules themselves.
- **Needs from the owner:** open items on market mechanics (§20.3) as they come up.

## Phase 2 — Full calendar
- **Goal:** the whole year, not just the 18 games.
- **Work:** Ambassador Tour and Bowl played; offseason calendar (draft, free agency, trade deadline, cuts); the 8-team lottery and 48-pick draft; exile relief; the all-rookie Founding Day build.
- **Exit test:** 100 full years with every calendar event firing in order and every table validating.

## Phase 3 — Game engine (the big one)
- **Goal:** replace the placeholder resolver with real football.
- **Work, in slices, spec before code:** (1) run and pass plays and a drive model, (2) penalties from the ~20 most common NFL penalties, (3) injuries at NFL-average frequency, (4) overtime and ties, (5) box scores, (6) play log and the second per-play table for the 2D view. Each slice has calibration bands (scoring, plays per game, penalties, injuries, tie rate).
- **Exit test:** engine output sits inside the accepted football-reasonable bands over thousands of games, and tie frequency is measured, not assumed.
- **Needs from the owner:** the spatial-engine decisions (play catalogue, blocking, coverage, ball flight; §20.2). This is the largest open item. The snapshot may already contain much of it, which would shorten this phase a lot.

## Phase 4 — Fairness and calibration
- **Goal:** prove the league meets its own definition of fair competitiveness.
- **Work:** a harness for the fairness bands (v1's 15, in `rules.py`, are the starting point), the exile standard (competitive for 3rd or better within about 3 seasons, never automatic), strength-of-schedule push toward average, century-long runs, and the parity profiles. Build **tight** first; **loose** and **none** are separate test beds added afterward.
- **Exit test:** every band passes with error-bar verdicts over many seeds; any trend outside the bands is reported, not tuned away silently.
- **Needs from the owner:** whether the bands are too tight for long-term enjoyment (you've flagged this); approval of any band change.

## Phase 5 — People
- **Goal:** living characters, carefully bounded.
- **Work:** players first (archetypes as fixed souls, perception, personality, development, aging, retirement, the origin-card creator), then coaches (one pool for head coaches and coordinators, small capped effects), then GMs, CEOs and fans. Negotiations as meeting rooms only where independent parties need a say (contracts, trades, interviews). Analytics definitions (§20.1).
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
3. **Agents versus determinism.** Language-model agents are slow, costly and not reproducible. Keeping autopilot as the default protects testing and cost.
4. **Three parity profiles triple the test surface.** Tight first.
5. **Knowledge outside the repo.** The snapshot and earlier decisions live in other conversations. Phase 0 exists to bring them in.
6. **Specification drift.** Several documents disagree (pacing, cap, tiebreaks). The Design Document is the single authority, and each phase ends with an edition bump.

## Not doing (until the owner says otherwise)
Deploying anything, changing the live site or `Vajilia/DFL`, or running agents against live state; 3D visuals; a commercial or multi-user product beyond a handful of peers.
