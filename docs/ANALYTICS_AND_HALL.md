# Analytics definitions and the Hall of Fame

Status: **built in Edition 8.30 (2026-10-24)** after the owner approved the definitions, the Coronation-winner reading of the Tiara and the engine recording work. What is built, and where it departs from the proposal below, is in the Design Document §15 and §20 item 29. Departures: the fan ballot's "runners-up" are the best eligible players outside the league hall (not the losing finalist's players), and the expected-points curve is refit each season from the league's own plays (Edition 8.32; yard-line curve only). Original proposal follows. The Design Document is the authority (§15, §20 item 1). Builder's amendments are marked **(builder)**.

## What blocks building this now
The engine records team totals per game (plays, yards, sacks, points and so on) and which players played, but **no per-player production**. Every analytics term and the Hall's All-Time registers need production credited to people. So the order is: (1) per-player recording in the engine, (2) the definitions below, (3) the Hall. Recording is an engine task of its own (credit each play's result to the players who took part, by role) and should be measured against the engine's existing calibration, since it touches the play loop.

## Proposed definitions (§20 item 1) **(builder)**
All production is measured in **expected points added (EPA)**: the change in the offence's expected points from the play's start to its end, from a table fitted on the league's own plays (down, distance, field position), refitted each season. A play's EPA is split among the players who took part by their role in it (passer, target, rusher, blockers, rushers, coverage and so on) using fixed role shares (data).
- **Opportunity**: one play in which a player had a role.
- **Opportunity threshold**: the fewest opportunities that make a season countable for ranking: one quarter of the median full-time workload at her position. Below it she is listed, not ranked.
- **Replacement baseline**: the average EPA per opportunity of players at her position who passed the threshold but were not among that position's starters at their clubs (the bench level). Role execution is measured above it.
- **Role execution**: her EPA per opportunity less the replacement baseline, times her opportunities (production above a bench player).
- **Opponent-adjusted difficulty**: each opportunity is weighted by the quality of the opposing unit it was against (that unit's mean role execution that season, standardised). The same production against a stronger unit counts for more.
- **Leverage**: each opportunity is weighted by the swing in win probability the play carried (larger late in close games). A play that decided a game counts for more than a garbage-time play.
- **Era standardisation**: every season's values are converted to z-scores within position against that season's league distribution, so eras with more or less scoring compare fairly.
- **Analytical score** (§15): 60 percent role execution, 25 percent opponent-adjusted difficulty, 15 percent leverage, each era-standardised.
Open for the owner: whether kickers, punters and long snappers use EPA or their own measures (field goal value over expectation, net punting, snap quality); whether defence is credited play by play or by unit.

## Hall of Fame (§15)
Buildable once the score exists:
- Eligibility: a Tiara and five credited seasons (a season with at least one active or Tour appearance counts once). Note the ledger's `seasons` counter counts roster membership at the year turn, which is not the same; appearances need recording.
- Tiara: the winning club's final 53 plus its CEO, GM, head coach and three coordinators, at most one per person per season. Open for the owner: "winning club" is taken here to mean the Coronation winner.
- 59 halls, All-Time All-22 registers per hall, ever-membership permanent, ties against the incumbent, seeded draws between challengers.
- One fan Hall entrant a year by ranked choice among Tiara-holding eligible runners-up (needs a definition of "runner-up": the losing finalist's players is the builder's reading).
- Records store person or club ID, event links, season, scope, unit and opportunity count.

## Recommendation
Approve or amend the definitions, then I build per-player recording first and measure it against the engine's calibration before anything else rests on it. Building the Hall's Tiara registry earlier is possible but would give a Hall with no ranking.
