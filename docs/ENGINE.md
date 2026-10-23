# Game engine (Phase 3, slice 1)

Authority: Design Document Ed 8.16, section 10 (rules) and 17 (determinism). Code in `src/dfl/engine/`, parameters in `data/engine/`, tests in `tests/test_engine.py`, calibration in `tools/calibrate_engine.py`.

## What it is
A **play-level** engine. The rules core is exact to section 10 (four 15-minute quarters, 40-second play clock, three timeouts a half, no coin toss, kickoffs from the 35 with touchbacks to the 25, free kick after a safety, scoring, the ten rulebook penalties, overtime of one 10-minute period with a possession for each side and a defensive score ending it, successive 15-minute periods in the postseason). How a play *turns out* is a statistical model driven by the hidden attributes of the 22 players on the field. It is **not** the spatial engine of section 10 (positions, routes, ball flight); that replaces the play resolver later behind the same interface, and the calibration bands below are what it must keep meeting.

Players come from character cards (`dfl.characters`). A `Squad` picks the best available player at each position (4-3 defense; QB, HB, TE, 3 WR, 5 OL), turns their attributes into unit ratings (quarterback, pass protection, receivers, back, run blocking, pass rush, coverage, run defense, kicker, punter), and the play model reads the matchups as z-scores against a league baseline stored in `PARAMS.csv`. Coaches (OC, DC, head coach) add a small capped effect. Nothing hidden leaves the engine: the result carries stats and a play log only.

## Builder defaults where the spec is silent (change on request)
- Pass/run mix from down, distance, score, clock; hurry-up when trailing late; clock-milking and kneel-downs when protecting a lead.
- Fourth down: punt, field goal (about 55 yards, kicker-dependent) or go; going more often when trailing late.
- Extra point by default; a two-point try when down 2 after the touchdown, and sometimes late when down 5, 8 or up 1. No tries in overtime.
- A first down briefly stops the clock (section 10); the model gives the next snap 26-34 s instead of 30-40 s.
- Penalties: accept or decline by whichever is better for the other side; offensive penalties repeat the down; half-the-distance rule near the goal.
- Overtime possession guarantee applies in each postseason period (the spec's wording is "the same possession guarantee").
- Timeouts: taken late in a half by a side that is behind (or the tied side at the end of the first half).
- Injuries: a small per-snap rate weighted by durability; injured players are out for the rest of the game; weeks are drawn from a table. A short position is filled by the best leftover player (the out-of-position penalty is still open).

## Not modeled yet
Challenges and replay, fatigue and substitution patterns, nickel and dime packages, playbooks and game plans, special-teams returns beyond a distribution, the two-minute warning, formations as such, weather, individual defensive stats, All-Star composite squads (the Ambassador Bowl needs them; the people layer decides who is on them).

## Calibration (Ed 8.16 parameters, 10,000 games per seed, 48 founding-league clubs)
Targets are NFL aggregate bands in `data/engine/BANDS.csv`. Of 24 bands, seed 1 misses 2, seed 2 misses 2, seed 3 misses 3; the misses are marginal (punts per team 4.8-5.2 against 5.0, tie rate 1.4-1.6% against 1.5%, margin spread 15.8 against 15.5, and on seed 3 completion rate, yards per attempt and yards per carry about 0.1 over). Different seeds make different clubs, so some spread across seeds is expected. These are **placeholders tuned to the NFL's aggregates**, not evidence about the DFL: parity, home edge and tie frequency need the people layer and Phase 4.

**Tie frequency (spec open item 7).** With a 10-minute overtime and the both-sides possession rule the engine ties about 1.5% of regular-season games, and about 21% of overtime games end level. That is higher than the NFL's recent rate, because both sides must get the ball and a game can end level after a field goal each. **The owner accepted this rate (2026-10-23); the overtime rule stands.** Re-measure with the spatial engine.

Speed: about 290 games per second, so a 1,056-game season takes about four seconds.

## Wired into the season loop (2026-10-23)
`EngineResolver` (`src/dfl/engine/resolver.py`) plugs the engine into `league.play_season`: one squad per club from character cards, injuries carried from game to game, All-Star composite squads for the Ambassador Bowl (best four per position across the conference's Tour clubs, until the people layer's voting exists). `python3 tools/run_seasons.py --engine` runs it.

100 seasons, seed 1, **static rosters** (no draft, aging, free agency or retirement between seasons): 0 validator failures, win-percentage spread 0.18 (the NFL's is about 0.19), year-to-year win% correlation 0.40, about 5.7 ties a season. But the league is not fair over time: individual clubs were exiled between 1 and 48 times in 100 years, 12 clubs won every title and one club won 24. **That is the expected result of frozen rosters, not evidence about the DFL's parity machinery.** It is the baseline the draft, the lottery, the cap and the schedule must beat, and it is why the next step is connecting cards to the ledger (draft classes, development, aging, retirement) so the fairness bands can be measured on a living league.

## Playing from the living ledger (Ed 8.18)
`LedgerEngine` (`src/dfl/engine/ledger_engine.py`) replaces the static squads: each game's squad is the ledger's game-day active list as cards (elevations and emergency players included) with the club's coaches; injuries go back to the ledger (`World.injure`) and cost real games; postseason rounds heal a week each. Over 40 living years (draft, development, aging, free agency) the league holds steady: about 20.5 points per team game, win% spread 0.21, 5.7 ties a season, 24 different champions. Details in `docs/BUILD_STATUS.md`. Scoring is a little under the NFL's 22 to 23 because the calibration was done on the founding league; recalibrate on the living league when the spatial engine arrives.
