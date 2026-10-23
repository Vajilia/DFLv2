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

**Tie frequency (spec open item 7).** With a 10-minute overtime and the both-sides possession rule the engine ties about 1.5% of regular-season games, and about 21% of overtime games end level. That is higher than "highly unlikely" and higher than the NFL's recent rate, because both sides must get the ball and a game can end level after a field goal each. Options for the owner: a longer overtime, a different tiebreak-in-overtime rule, or accept it.

Speed: about 290 games per second, so a 1,056-game season takes about four seconds.
