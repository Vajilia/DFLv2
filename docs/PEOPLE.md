# People layer (Phase 5) plan

Authority: Design Document Ed 8.21. The exit test: adding people leaves every Phase 4 fairness band inside range, and every character effect is capped and tested. Each slice is measured against the bands (`docs/FAIRNESS.md`) before the next starts.

## Why this order
Clubs today are stand-ins that choose by true ability, which makes year-to-year strength persistence low (about 0.37) and the league close to random. People make clubs smarter and so raise persistence. That is the risk to the bands, so each slice is small and measured.

| Slice | What | Status |
| --- | --- | --- |
| 5.1 | Scouting views: clubs see a noisy estimate, not true ability (Ed 8.21, §13) | built; measured below |
| 5.2 | Souls: fixed archetypes, self-view bias, trait drift bounded by the archetype (Ed 8.22) | built; measured, no band moved out of range |
| 5.3 | Player-side negotiation: demands from self-view, greed, loyalty, ambition (Ed 8.23); clubs pay up to their own view | built; measured, no band moved out of range |
| 5.4 | Coaches (owner's structure, Ed 8.25): one category, posts held not born into, one common pool (new, fired, resigned), CEO over head coach, head coach over coordinators, leadership window, capped teaching effect | built; measured, no band moved out of range |
| 5.5 | GMs, CEOs, fans: sentiment, recall ballots, CEO elections, GM hire and dismissal, hidden narrative-only discretion (Ed 8.27) | built; measured, no band moved out of range |
| 5.6 | Interactions and meeting rooms (contracts, trades, interviews); analytics definitions (§20.1); Hall of Fame | planned |

## Slice 5.1 rule (as built)
View = true overall + position bias + error. Error sd = `base_sd / sqrt(1 + seasons_k * seasons)`, scaled by `1 - evl_effect * (GM EVL - 50) / 50`, times `own_factor` for the club's own players, floored at `min_sd`. Error is fixed per (club, player, league year). Prices still use the market rating, so overrating a player means overpaying. All numbers are placeholders in `data/characters/SCOUTING.csv` and `SCOUTING_BIAS.csv`.

Open: game-day depth and the engine still use true ability (spec open item 22).

## Slice 5.2 rule (as built)
Archetype = nearest of six placeholder centres (`ARCHETYPES.csv`) to the traits at creation; fixed for life. Self-view = true overall + archetype self-bias (read by slice 5.3). Yearly drift (`SOUL.csv`): each trait closes 8 percent of the gap to the archetype centre, plus noise of 2 points, and stays within 15 points of its creation value. Only players drift so far. Shares are uneven (spec item 23).

## Slice 5.3 rule (as built)
Above-minimum free agents bargain. Demand = price of her self-view overall x (1 + 0.20 greed + 0.08 ambition, each centred on 50) minus 0.15 loyalty for her last club only, minus 10 percent per year waited in the pool; floor the league minimum. A club pays up to the price of its own scouting view plus 10 percent slack. Deal at her demand if within that; otherwise no deal. In a one-year test about 36 percent of attempted deals were refused and deals averaged 4 percent over the market curve. Rookies keep the scale; contract shape is still the autopilot's.

## Slice 5.4 rule (as built)
Coach is one category; the head-coach and coordinator posts are held, not born into. One common pool holds new, fired and resigned coaches (16 minimum, four-year rule). The CEO may dismiss the head coach after a bad season (win percentage under .300, 35 percent, scaled by the CEO's temperament) and hires a replacement by the club's own rating, with error shrunk by the GM's evaluation. The head coach hires coordinators the same way (error shrunk by his game management), and a new head coach or, after a bad season, the old one may dismiss them (25 and 20 percent). Coaches may resign (3 percent, up with ambition, down with loyalty) and retire (from 60, certain at 66). Teaching: head coach 40 percent and the position group's coordinator 60 percent, at most half an attribute point a year. The pool is made 0.7 talent deviations weaker than the founders so the average coach stays flat (62 overall over 60 years). Not built: coach contracts and pay, poaching, scheme sides (spec item 25). The CEO and GM do not yet move (slice 5.5).

## Slice 5.5 rule (as built)
Fan card per club: three viewpoints (results, investment, stability; the builder's names, the spec names none) with shares seeded 20 to 50 and normalised; each has a satisfaction (start 50) that closes 15 percent of its gap to 50 each season and moves by its signal times a gain (24, 18, 12). Signals: win percentage against .500 (full at .25 away), payroll against 91 percent of the cap (full at 4 points away), share of last year's 22 best-paid players still with the club (reference 65 percent). Sentiment = share-weighted satisfaction. After the last game: sentiment at or below 20 calls a recall ballot; viewpoints below 35 vote to remove; a majority of fans removes the CEO. Term elections every third season (clubs staggered), plus on vacancy: each viewpoint votes for the candidate it rates highest (CEO attributes plus an error of 4, the incumbent plus 4 for familiarity and up to 10 for the viewpoint's mood); a winner who is not the incumbent takes office in the next offseason and the old CEO joins the pool. The CEO hires the GM from a pool of four or more (error shrinks with his governance rating) and may dismiss her after a bad season (25 percent, scaled by his temperament); she may resign. CEOs and GMs retire from 62, certain at 66; four-year pool rule. New candidates are weaker than founders (CEO -1.3, GM -0.7) so the averages hold near 60. Discretion is a hidden GM attribute drawn from its own random stream; no engine or ledger source reads it and a test proves that changing every GM's discretion changes nothing that happens. Numbers: `data/characters/GOVERNANCE.csv`. Code: `src/dfl/ledger/governance.py`; 21 tests in `tests/test_governance.py`.

Not built: CEO effect on spending (would be a fairness channel), GM-to-GM relationships (recommended narrative-only for the same reason), contracts and pay for CEOs and GMs, poaching, the story layer that reads the governance log.

## Owner decisions this layer needs
1. The archetype list (a proposal will come with slice 5.2).
2. The size cap on coach effects (default proposal: a head coach or coordinator moves any rating by at most one point).
3. Scouting noise levels and position biases (placeholders now).
4. Development and aging curve numbers (§20.3, still outstanding).
5. Whether a player may retire under contract (§20.19, still outstanding).
6. Slice 5.5: whether the CEO's financial rating should ever move spending, and whether GM relationships may shade trades (builder recommends no to both), the three viewpoint names, and how rare recalls should be.
