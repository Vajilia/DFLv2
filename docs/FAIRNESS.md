# Fairness bands on the living league (Phase 4, first pass)

Measured 2026-10-23 on the Ed 8.18 league (character cards, development, draft, free agency, the engine). Six independent leagues
(seeds 1 to 6), 36 seasons each, the first 8 dropped as warm-up. Tool: `tools/fairness.py` (`run` writes season records, `report`
prints the table); logic in `src/dfl/fairness.py`; bands are data in `data/fairness/BANDS.csv`; 12 tests in `tests/test_fairness.py`.

The 15 bands are v1's working limits, which the owner accepted on 2026-10-04 (V1-18). They are ported unchanged except where the v2 rules differ
(noted below). A band is judged on the mean over leagues with a two-standard-error interval: PASS if the whole interval is inside the band,
FAIL if it is entirely outside, UNCLEAR otherwise. Nothing was tuned to produce these numbers.

| band | measured (mean ± 2 SE) | allowed | verdict |
| --- | --- | --- | --- |
| win_pct_sd | 0.205 ± 0.003 | 0.13 to 0.19 | FAIL |
| year_to_year_corr | 0.209 ± 0.026 | 0.1 to 0.5 | PASS |
| close_game_share | 0.420 ± 0.005 | 0.35 to 0.55 | PASS |
| repeat_champion_rate | 0.119 ± 0.073 | 0.0 to 0.12 | UNCLEAR |
| max_titles_in_20 | 3.074 ± 0.601 | 0.0 to 4.7 | PASS |
| worst_league_titles | 4.000 ± 0.000 | 0.0 to 7.0 | PASS |
| best_team_title_odds | 0.226 ± 0.078 | 0.08 to 0.25 | UNCLEAR |
| returner_avg_finish | 3.148 ± 0.065 | 2.8 to 3.4 | PASS |
| returner_win_div | 0.190 ± 0.015 | 0.1 to 0.3 | PASS |
| returner_fifth_again | 0.245 ± 0.024 | 0.1 to 0.3 | PASS |
| returner_top3_in_3 | 0.811 ± 0.019 | 0.75 to 0.97 | PASS |
| returner_playoff_first | 0.323 ± 0.009 | 0.2 to 0.55 | PASS |
| exile_effect | 0.057 ± 0.598 | -0.5 to 0.5 | UNCLEAR |
| exile_double_early | 0.243 ± 0.017 | 0.0 to 0.1 | FAIL |
| stuck_at_bottom | 0.090 ± 0.008 | 0.0 to 0.1 | PASS |

**10 PASS, 2 FAIL, 3 UNCLEAR.**

## The two failures
1. **win_pct_sd 0.205 against a 0.19 ceiling.** The NFL's own spread is about 0.19 to 0.21, so the ceiling is at or a little below real football.
   Not tuned. It is driven by how strongly the engine turns roster strength into wins and by how far apart the stand-in clubs are, and the
   second will change when the people layer replaces the stand-ins. Owner decision later: raise the ceiling (about 0.21) or tune the engine.
2. **exile_double_early 0.243 against a 0.10 ceiling.** v2's draft (spec section 12) gives every returning ambassador a top-8 lottery pick but has no
   rule for their next draft. v1 had one the owner accepted on 2026-10-04: returners pick as a block at the end of the 9 to 34 band (picks 27 to 34)
   in their second draft. Without it a returner has about a 24% chance of a top-16 pick, close to what luck gives (about 20%). **Owner decision
   needed: restore the v1 rule.** The band is adapted from v1's wording to the v2 rule: "top-8 pick on returning, then a top-16 pick in the next draft"
   (every returner gets the first part).

## The three unclear
repeat_champion_rate (0.119 against 0.12), best_team_title_odds (0.226 against 0.25) and exile_effect (0.06 points against plus or minus 0.5) all straddle
their limits; more leagues or more seasons narrow the error bars. The measured values themselves are inside the limits.

## Definitions the builder had to choose
- Win percentage counts a tie as half a win. Eighteen games a club (40 active clubs, 360 games a season).
- "Strongest on paper": the active club with the highest mean hidden rating of its best starters at each position (22 on offense and defense, K and P).
- A club exiled after season s is the ambassador in s+1 and returns in s+2; "first season back" is s+2. Exile effect compares the win % two seasons later
  of clubs that finished 5th versus 4th, converted at 34 points per 1.00 of win % (half a win per point over 17 games).
- Stuck at bottom: club-seasons that follow three straight 4th-or-5th division finishes, with exile years skipped.

## What these results do and do not show
- The stand-in clubs are random (free-agent order, weekly releases, trades) and choose by true rating, so this is a baseline for the rules, not a verdict
  on them. Club strength correlates only 0.37 from one year to the next in these leagues; win percentage 0.21. Real agents will probably make strong
  clubs stay strong, and the bands must be re-run when the people layer arrives (Phase 5 exit test).
- Win % spread above the bands while year-to-year correlation is low means single seasons are more lopsided than the NFL's but the lopsidedness does not last.
  That is a different shape from the bands' intent and worth discussing, not just a number to move.
- Six leagues give tight bounds on the large-sample bands and loose ones on rare events (title concentration, exile effect).
