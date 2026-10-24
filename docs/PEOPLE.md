# People layer (Phase 5) plan

Authority: Design Document Ed 8.21. The exit test: adding people leaves every Phase 4 fairness band inside range, and every character effect is capped and tested. Each slice is measured against the bands (`docs/FAIRNESS.md`) before the next starts.

## Why this order
Clubs today are stand-ins that choose by true ability, which makes year-to-year strength persistence low (about 0.37) and the league close to random. People make clubs smarter and so raise persistence. That is the risk to the bands, so each slice is small and measured.

| Slice | What | Status |
| --- | --- | --- |
| 5.1 | Scouting views: clubs see a noisy estimate, not true ability (Ed 8.21, §13) | built; measured below |
| 5.2 | Souls: fixed archetypes, self-view bias, trait drift bounded by the archetype (Ed 8.22) | built; measured, no band moved out of range |
| 5.3 | Player-side negotiation: demands from self-view, greed, loyalty, ambition; replaces the placeholder market floor | planned |
| 5.4 | Coaches: one pool for head coach and coordinators, small capped effects, development effect capped | planned |
| 5.5 | GMs, CEOs, fans: recall, sentiment | planned |
| 5.6 | Interactions and meeting rooms (contracts, trades, interviews); analytics definitions (§20.1); Hall of Fame | planned |

## Slice 5.1 rule (as built)
View = true overall + position bias + error. Error sd = `base_sd / sqrt(1 + seasons_k * seasons)`, scaled by `1 - evl_effect * (GM EVL - 50) / 50`, times `own_factor` for the club's own players, floored at `min_sd`. Error is fixed per (club, player, league year). Prices still use the market rating, so overrating a player means overpaying. All numbers are placeholders in `data/characters/SCOUTING.csv` and `SCOUTING_BIAS.csv`.

Open: game-day depth and the engine still use true ability (spec open item 22).

## Slice 5.2 rule (as built)
Archetype = nearest of six placeholder centres (`ARCHETYPES.csv`) to the traits at creation; fixed for life. Self-view = true overall + archetype self-bias (read by slice 5.3). Yearly drift (`SOUL.csv`): each trait closes 8 percent of the gap to the archetype centre, plus noise of 2 points, and stays within 15 points of its creation value. Only players drift so far. Shares are uneven (spec item 23).

## Owner decisions this layer needs
1. The archetype list (a proposal will come with slice 5.2).
2. The size cap on coach effects (default proposal: a head coach or coordinator moves any rating by at most one point).
3. Scouting noise levels and position biases (placeholders now).
4. Development and aging curve numbers (§20.3, still outstanding).
5. Whether a player may retire under contract (§20.19, still outstanding).
