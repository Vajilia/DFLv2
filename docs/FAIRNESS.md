# Fairness bands on the living league (Phase 4, first pass and re-measure)

Measured 2026-10-23 on the living league (character cards, development, draft, free agency, the engine). Six independent leagues
(seeds 1 to 6), 36 seasons each, the first 8 dropped as warm-up. Tool: `tools/fairness.py` (`run` writes season records, `report`
prints the table); logic in `src/dfl/fairness.py`; bands are data in `data/fairness/BANDS.csv`; 12 tests in `tests/test_fairness.py`.

The 15 bands are v1's working limits, which the owner accepted on 2026-10-04 (V1-18). They are ported unchanged except where the v2 rules differ
(noted below). A band is judged on the mean over leagues with a two-standard-error interval: PASS if the whole interval is inside the band,
FAIL if it is entirely outside, UNCLEAR otherwise. Nothing was tuned to produce these numbers.

## Result under Ed 8.20 (current): 14 PASS, 0 FAIL, 1 UNCLEAR

The same six leagues as the Ed 8.19 run, scored against the one changed band (win_pct_sd ceiling 0.19 to 0.21, owner's ruling 2026-10-23, no new simulation needed because the band only moves the threshold). Under Ed 8.19 with the old ceiling the result was 13 PASS, 1 FAIL, 1 UNCLEAR.

| band | measured (mean ± 2 SE) | allowed | verdict |
| --- | --- | --- | --- |
| win_pct_sd | 0.207 ± 0.002 | 0.13 to 0.21 | PASS |
| year_to_year_corr | 0.201 ± 0.028 | 0.1 to 0.5 | PASS |
| close_game_share | 0.423 ± 0.005 | 0.35 to 0.55 | PASS |
| repeat_champion_rate | 0.071 ± 0.018 | 0.0 to 0.12 | PASS |
| max_titles_in_20 | 2.722 ± 0.297 | 0.0 to 4.7 | PASS |
| worst_league_titles | 4.000 ± 0.000 | 0.0 to 7.0 | PASS |
| best_team_title_odds | 0.190 ± 0.040 | 0.08 to 0.25 | PASS |
| returner_avg_finish | 3.116 ± 0.043 | 2.8 to 3.4 | PASS |
| returner_win_div | 0.188 ± 0.017 | 0.1 to 0.3 | PASS |
| returner_fifth_again | 0.246 ± 0.018 | 0.1 to 0.3 | PASS |
| returner_top3_in_3 | 0.817 ± 0.011 | 0.75 to 0.97 | PASS |
| returner_playoff_first | 0.315 ± 0.011 | 0.2 to 0.55 | PASS |
| exile_effect | -0.160 ± 0.469 | -0.5 to 0.5 | UNCLEAR |
| exile_double_early | 0.000 ± 0.000 | 0.0 to 0.1 | PASS |
| stuck_at_bottom | 0.084 ± 0.003 | 0.0 to 0.1 | PASS |

Win % spread passes narrowly (0.207, upper bound 0.209 against 0.21): it sits at the NFL's own level and will move when the people layer replaces the stand-in clubs, so it is the band to watch.

## What changed from the first pass (Ed 8.18: 10 PASS, 2 FAIL, 3 UNCLEAR)
The first pass found 24% of exiled clubs getting a top-16 pick in the draft after their return (limit 10%). v2 gave returners a top-8 lottery pick but
no rule for their next draft; v1 had one the owner accepted on 2026-10-04. The owner ruled "yes" to restoring it (Ed 8.19: a returner that misses the
playoffs picks as a block at the end of the non-playoff clubs, never ahead of her record). **exile_double_early is now 0.000, which is true by
construction, not a statistical finding**: the block puts non-playoff returners at picks 27 to 34 and playoff returners at 35 and later. The other
changes (repeat champions, best-team title odds moving from UNCLEAR to PASS) are the same population drawn again with different draft orders and
are within the noise of six leagues; do not read them as effects of the rule.

First-pass table (Ed 8.18), for the record:

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

## The win % spread ruling and the one unclear
1. **win_pct_sd**: measured 0.207 against the old 0.19 ceiling. The NFL's own spread is about 0.19 to 0.21, so on 2026-10-23 the owner accepted the builder's recommendation to raise the ceiling to 0.21 rather than tune the engine toward a limit stricter than real football (Ed 8.20).
2. **exile_effect -0.16 points (plus or minus 0.47)** straddles its limit of plus or minus 0.5: no sign of a lasting exile effect, but the error bar is too wide to say it is inside the band. More leagues would settle it.

## Century runs (Ed 8.20, tight): two leagues of 100 seasons (seeds 11, 12)
Tool: `tools/fairness.py run --years 100`, then `report`, which also prints the decade-by-decade trend and the schedule push. First 8 seasons dropped.

| band | measured (mean ± 2 SE) | allowed | verdict |
| --- | --- | --- | --- |
| win_pct_sd | 0.208 ± 0.003 | 0.13 to 0.21 | UNCLEAR |
| year_to_year_corr | 0.204 ± 0.033 | 0.1 to 0.5 | PASS |
| close_game_share | 0.421 ± 0.008 | 0.35 to 0.55 | PASS |
| repeat_champion_rate | 0.054 ± 0.022 | 0.0 to 0.12 | PASS |
| max_titles_in_20 | 2.623 ± 0.096 | 0.0 to 4.7 | PASS |
| worst_league_titles | 4.000 ± 0.000 | 0.0 to 7.0 | PASS |
| best_team_title_odds | 0.212 ± 0.054 | 0.08 to 0.25 | UNCLEAR |
| returner_avg_finish | 3.125 ± 0.130 | 2.8 to 3.4 | PASS |
| returner_win_div | 0.185 ± 0.022 | 0.1 to 0.3 | PASS |
| returner_fifth_again | 0.230 ± 0.061 | 0.1 to 0.3 | PASS |
| returner_top3_in_3 | 0.815 ± 0.035 | 0.75 to 0.97 | PASS |
| returner_playoff_first | 0.319 ± 0.033 | 0.2 to 0.55 | PASS |
| exile_effect | 0.238 ± 1.344 | -0.5 to 0.5 | UNCLEAR |
| exile_double_early | 0.000 ± 0.000 | 0.0 to 0.1 | PASS |
| stuck_at_bottom | 0.088 ± 0.010 | 0.0 to 0.1 | PASS |

Schedule push toward average: opponents' mean strength per point of own strength, slope -0.024 (a schedule that ignores strength gives -0.026, because a club never plays herself), so the push is +0.002 per point; correlation -0.098; league slopes -0.023, -0.025. Positive push = stronger clubs face tougher schedules.

Trend by decade (flags mark a banded figure outside its limits):

League 1

| seasons | win % spread | close games | points/team game | most titles by one club | distinct champions | roster rating | club-strength spread |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2000-2009 | 0.201 | 0.438 | 20.6 | 2 | 9 | 68.8 | 1.09 |
| 2010-2019 | 0.205 | 0.434 | 20.9 | 1 | 10 | 67.7 | 1.13 |
| 2020-2029 | 0.212* | 0.423 | 21.1 | 2 | 9 | 67.5 | 1.22 |
| 2030-2039 | 0.208 | 0.422 | 19.9 | 3 | 7 | 68.0 | 1.13 |
| 2040-2049 | 0.207 | 0.419 | 20.4 | 3 | 7 | 68.2 | 1.18 |
| 2050-2059 | 0.211* | 0.426 | 20.0 | 2 | 9 | 68.0 | 1.19 |
| 2060-2069 | 0.209 | 0.423 | 20.9 | 2 | 9 | 67.8 | 1.11 |
| 2070-2079 | 0.211* | 0.418 | 20.3 | 2 | 8 | 67.4 | 1.18 |
| 2080-2089 | 0.207 | 0.428 | 19.5 | 2 | 9 | 67.8 | 1.12 |
| 2090-2099 | 0.211* | 0.429 | 20.3 | 2 | 9 | 67.4 | 1.16 |

League 2

| seasons | win % spread | close games | points/team game | most titles by one club | distinct champions | roster rating | club-strength spread |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2000-2009 | 0.190 | 0.437 | 20.6 | 3 | 6 | 69.0 | 1.01 |
| 2010-2019 | 0.207 | 0.403 | 20.2 | 1 | 10 | 68.3 | 1.13 |
| 2020-2029 | 0.200 | 0.430 | 20.9 | 2 | 9 | 67.6 | 1.10 |
| 2030-2039 | 0.211* | 0.419 | 20.0 | 2 | 9 | 67.6 | 1.16 |
| 2040-2049 | 0.213* | 0.411 | 19.9 | 3 | 7 | 67.6 | 1.19 |
| 2050-2059 | 0.205 | 0.417 | 20.0 | 3 | 6 | 67.6 | 1.16 |
| 2060-2069 | 0.208 | 0.404 | 20.6 | 1 | 10 | 67.9 | 1.22 |
| 2070-2079 | 0.208 | 0.419 | 20.0 | 2 | 8 | 67.7 | 1.17 |
| 2080-2089 | 0.201 | 0.422 | 20.0 | 2 | 9 | 67.9 | 1.09 |
| 2090-2099 | 0.207 | 0.422 | 19.4 | 3 | 8 | 67.9 | 1.16 |

**Reading it.**
- **No drift in a century.** Roster rating stays between 67 and 69, points per team game between 19 and 21, club-strength spread between 1.0 and 1.2, and no decade has more than 3 titles for one club or fewer than 6 distinct champions in 10 seasons. Nothing is trending; the league does not run away or collapse.
- **Win % spread sits on its ceiling** (0.208 against 0.21; decades range 0.190 to 0.213, and 8 of 20 decades land just over, marked *). Over a century this band is UNCLEAR, not passing comfortably. Same message as before: it is the band to watch when the people layer arrives.
- **Best-team title odds (0.21) and exile effect** remain UNCLEAR on two leagues; the values are inside the limits but the bars are wide.
- **Schedule push toward average: none.** A schedule that ignores strength still shows a slope of -0.026 (a club cannot play herself); the measured slope is -0.024, so the push is +0.002 per rating point, which across the league's strength spread (about 1.2 points) is less than 0.003 rating points of opponent strength. Opponents are chosen by last season's seed, and seed predicts strength weakly (strength repeats only 0.37 year to year). No band covers this and none is failing; if you want an actual push, the lever is how the schedule template ties opponents to place (spec section 6). I recommend leaving it alone: the bands pass without it.
- **Exile standard (competitive for 3rd or better within about 3 seasons, never automatic):** covered by the returner bands: 81% of returners finish 3rd or better at least once in three seasons (limit 75% to 97%) and 32% make the playoffs in their first season back (limit 20% to 55%), so it is neither blocked nor automatic.

## People layer slice 5.1: scouting views on (Ed 8.21), six tight leagues of 36 seasons (seeds 1 to 6)

Baseline for comparison: eight pre-scouting leagues (the six Ed 8.20 leagues plus seeds 7 and 8, same code otherwise).

| band | pre-scouting (8 leagues) | scouting on (6 leagues) | verdict now |
| --- | --- | --- | --- |
| win_pct_sd | 0.207 ± 0.002 | 0.206 ± 0.002 | PASS |
| year_to_year_corr | 0.196 ± 0.023 | 0.191 ± 0.024 | PASS |
| close_game_share | 0.425 | 0.424 | PASS |
| repeat_champion_rate | 0.062 ± 0.022 | 0.101 ± 0.034 | UNCLEAR (interval reaches 0.135 against a 0.12 ceiling) |
| max_titles_in_20 | 2.67 | 2.48 | PASS |
| worst_league_titles | 4 | 3 | PASS |
| best_team_title_odds | 0.196 ± 0.033 | 0.226 ± 0.082 | UNCLEAR (6 leagues too few; 8 leagues pre-scouting passed) |
| returner bands (6) | all PASS | all PASS | PASS |
| exile_effect | -0.172 ± 0.352 | 0.684 ± 0.495 | UNCLEAR (point estimate above the 0.5 ceiling) |
| exile_double_early | 0.000 | 0.000 | PASS |
| stuck_at_bottom | 0.083 | 0.084 | PASS |

Reading it honestly:
- **No band moved outside its range with certainty**: 12 PASS, 3 UNCLEAR, 0 FAIL. The three unclear bands are each a noisy ratio or rare-event measure; they were unclear in some earlier six-league runs too (best_team_title_odds, exile_effect), so six leagues cannot separate a real move from noise.
- **The core measures did not move**: win % spread 0.206, year-to-year correlation 0.191 against 0.196, schedule push +0.004 per rating point (still none). Scouting error at the placeholder size is **close to inert** for competitive balance: clubs were already near-random choosers for other reasons (prices, cap, roster churn), and they were not far enough from the truth before to be hurt by an error of this size. Do not read it as proof that smarter clubs will be harmless. Persistence has not risen, so the test of "smarter clubs raise persistence" is still ahead (coaches, GMs, negotiation).
- **Watch list**: exile_effect (point estimate 0.68 against a ceiling of 0.5, interval 0.19 to 1.18) and repeat_champion_rate (0.101 against 0.12). Next step is more leagues with scouting on (seeds 7 to 12) if either persists; if exile_effect stays above the ceiling with 12 leagues it becomes a real finding to report, not a thing to tune away.

## People layer slice 5.2: souls on (Ed 8.22), six tight leagues of 36 seasons (seeds 1 to 6)

12 PASS, 3 UNCLEAR, 0 FAIL. Scouting-only column is the 5.1 run above.

| band | pre-people (8 leagues) | scouting only (6) | scouting + souls (6) | verdict now |
| --- | --- | --- | --- | --- |
| win_pct_sd | 0.207 | 0.206 | 0.205 ± 0.002 | PASS |
| year_to_year_corr | 0.196 | 0.191 | 0.179 ± 0.019 | PASS |
| repeat_champion_rate | 0.062 | 0.101 | 0.083 ± 0.057 | UNCLEAR |
| best_team_title_odds | 0.196 | 0.226 | 0.161 ± 0.048 | PASS |
| returner_top3_in_3 | 0.823 | 0.843 | 0.785 ± 0.039 | UNCLEAR (interval 0.746 to 0.824 against a 0.75 floor) |
| exile_effect | -0.172 | 0.684 | 0.198 ± 0.367 | UNCLEAR (inside the band at the centre; interval wide) |
| other nine bands | PASS | PASS | PASS | PASS |

Reading it:
- **Nothing moved outside a band with certainty**, and the scouting-run exile effect (0.68) came back to 0.20, which supports reading it as noise, not a real shift.
- **Slow drift in the persistence measure**: year-to-year correlation reads 0.196, 0.191, 0.179 across the three states. Each step is inside its error bar, so it is not a finding, but it points the opposite way from the expectation that people raise persistence. Souls' only live path is trait drift into work ethic, which reduces the spread of work ethic over time (the 15-year population check showed work ethic spread falling from 17.8 to 14.3), so a small flattening of development differences is the likely mechanism. Watch it; do not tune it.
- **Watch list**: returner_top3_in_3 (floor 0.75, point estimate 0.785), repeat_champion_rate, exile_effect. Twelve or more leagues would settle them.

## People layer slice 5.3: player demands on (Ed 8.23), six tight leagues of 36 seasons (seeds 1 to 6)

12 PASS, 3 UNCLEAR, 0 FAIL.

| band | pre-people (8) | scouting (6) | + souls (6) | + demands (6) | verdict now |
| --- | --- | --- | --- | --- | --- |
| win_pct_sd | 0.207 | 0.206 | 0.205 | 0.206 ± 0.002 | PASS |
| year_to_year_corr | 0.196 | 0.191 | 0.179 | 0.197 ± 0.020 | PASS |
| repeat_champion_rate | 0.062 | 0.101 | 0.083 | 0.083 ± 0.048 | UNCLEAR |
| best_team_title_odds | 0.196 | 0.226 | 0.161 | 0.226 ± 0.057 | UNCLEAR (interval reaches 0.283 against a 0.25 ceiling) |
| returner_top3_in_3 | 0.823 | 0.843 | 0.785 | 0.810 ± 0.023 | PASS |
| exile_effect | -0.172 | 0.684 | 0.198 | 0.083 ± 0.603 | UNCLEAR (centre well inside; interval wide) |
| other nine bands | PASS | PASS | PASS | PASS | PASS |

Reading it:
- **No band left its range with certainty.** The three unclear bands swap places from run to run (best_team_title_odds passed with souls and is unclear again; returner_top3_in_3 was unclear with souls and passes now), which is what noise on rare-event measures looks like. Six leagues cannot settle them; they need the 12-league run.
- **Persistence recovered** to 0.197 from 0.179, so the dip seen with souls was noise, as suspected. Schedule push is +0.011 per point (still near zero).
- **What this does not show**: payroll and cap pressure are not among the 15 bands. Demands cost about 4 percent over the market curve in a one-year check, and 36 percent of attempted deals were refused; long-run effects on club payroll and talent concentration should be looked at directly before the people layer is called done (planned with the coaches slice).

## People layer slice 5.4: coaches on (Ed 8.25), six tight leagues of 36 seasons (seeds 1 to 6)

Final design: one coach category, common pool, CEO over head coach, head coach over coordinators, leadership window, capped teaching, pool 0.7 weaker. **14 PASS, 1 UNCLEAR, 0 FAIL.**

| band | + demands (6) | coaches, first design (6) | teaching-off control (6) | coaches, final design (6) | verdict now |
| --- | --- | --- | --- | --- | --- |
| win_pct_sd | 0.206 | 0.209 ± 0.003 | 0.210 ± 0.003 | 0.206 ± 0.004 | PASS |
| year_to_year_corr | 0.197 | 0.204 | 0.191 | 0.187 ± 0.019 | PASS |
| repeat_champion_rate | 0.083 | 0.083 | 0.083 | 0.071 ± 0.032 | PASS |
| best_team_title_odds | 0.226 | 0.149 | 0.196 | 0.190 ± 0.035 | PASS |
| returner_top3_in_3 | 0.810 | 0.802 | 0.807 | 0.810 ± 0.008 | PASS |
| exile_effect | 0.083 | 0.450 | -0.083 | -0.134 ± 0.616 | UNCLEAR (interval wide; centre inside) |
| other nine bands | PASS | PASS | PASS | PASS | PASS |

Reading it:
- **The win % spread move seen in the first coach design was noise, not teaching.** A control with the teaching effect switched off read 0.210, as high as the run with it on; the final design reads 0.206 again. The cap of half an attribute point a year therefore stays.
- **Coach quality drifted up in the first design** (average coach 62.4 to 64.8 overall in 40 years) because clubs always take the best of four candidates from a pool only slightly weaker than the founders. Making the pool 0.7 talent deviations weaker holds the average near 62 over 60 years, with the dismissals, resignations and hiring of the final design running.
- **Persistence** reads 0.187, inside its band; no trend has appeared across the people layer (0.196 before, 0.191, 0.179, 0.197, 0.204, 0.191, 0.187 across states, all inside their error bars).
- The earlier worry about payroll and talent concentration is still to be measured directly.

## Trades stage 1 on (Ed 8.26), six tight leagues of 36 seasons (seeds 1 to 6): 12 PASS, 3 UNCLEAR, 0 FAIL

| band | coaches final (6) | + trades (6) | verdict now |
| --- | --- | --- | --- |
| win_pct_sd | 0.206 ± 0.004 | 0.209 ± 0.004 | UNCLEAR (interval 0.205 to 0.213 against a 0.21 ceiling) |
| year_to_year_corr | 0.187 | 0.211 ± 0.024 | PASS |
| repeat_champion_rate | 0.071 | 0.042 ± 0.012 | PASS |
| best_team_title_odds | 0.190 | 0.220 ± 0.050 | UNCLEAR (interval reaches 0.27 against a 0.25 ceiling) |
| exile_effect | -0.134 | 0.295 ± 0.314 | UNCLEAR (centre inside, wide interval) |
| other ten bands | PASS | PASS | PASS |

The win % spread reads 0.206 to 0.209 across the last several states and sits against the ceiling the owner set at 0.21 in Ed 8.20; it has been at 0.205 to 0.210 in every state since, with and without the people layer, so this is the league's own level rather than a people-layer effect, but it leaves no headroom. Persistence has risen a little (0.187 to 0.211, inside error bars) as clubs get smarter, as expected; schedule push is still about zero (+0.002 per rating point).

## Governance, slice 5.5 on (Ed 8.27), six tight leagues of 36 seasons (seeds 1 to 6): 13 PASS, 2 UNCLEAR, 0 FAIL

Fairness bands: 6 league(s), 36 seasons each, first 8 dropped as warm-up.

| band | measured (mean ± 2 SE) | allowed | verdict |
| --- | --- | --- | --- |
| win_pct_sd | 0.206 ± 0.003 | 0.13 to 0.21 | PASS |
| year_to_year_corr | 0.207 ± 0.023 | 0.1 to 0.5 | PASS |
| close_game_share | 0.424 ± 0.005 | 0.35 to 0.55 | PASS |
| repeat_champion_rate | 0.119 ± 0.035 | 0.0 to 0.12 | UNCLEAR |
| max_titles_in_20 | 2.667 ± 0.422 | 0.0 to 4.7 | PASS |
| worst_league_titles | 4.000 ± 0.000 | 0.0 to 7.0 | PASS |
| best_team_title_odds | 0.167 ± 0.044 | 0.08 to 0.25 | PASS |
| returner_avg_finish | 3.112 ± 0.089 | 2.8 to 3.4 | PASS |
| returner_win_div | 0.196 ± 0.012 | 0.1 to 0.3 | PASS |
| returner_fifth_again | 0.237 ± 0.031 | 0.1 to 0.3 | PASS |
| returner_top3_in_3 | 0.835 ± 0.021 | 0.75 to 0.97 | PASS |
| returner_playoff_first | 0.331 ± 0.019 | 0.2 to 0.55 | PASS |
| exile_effect | 0.186 ± 0.371 | -0.5 to 0.5 | UNCLEAR |
| exile_double_early | 0.000 ± 0.000 | 0.0 to 0.1 | PASS |
| stuck_at_bottom | 0.080 ± 0.006 | 0.0 to 0.1 | PASS |

Schedule push toward average: opponents' mean strength per point of own strength, slope -0.016 (a schedule that ignores strength gives -0.026, because a club never plays herself), so the push is +0.010 per point; correlation -0.066; league slopes -0.020, 0.001, -0.022, -0.025, -0.016, -0.015. Positive push = stronger clubs face tougher schedules.

**Reading it.**
- No band fails. The two UNCLEAR bands (repeat_champion_rate, exile_effect) are the noise-limited ones that have wavered in every state; repeat champions sit at the 0.12 ceiling (0.119 ± 0.035). Win % spread 0.206 and persistence 0.207 are where they were before the slice; the schedule push is +0.010 (none).
- This slice adds no new lever on results: CEO and GM ratings reach the game only through paths that already existed (GM evaluation in scouting views, GM greed and negotiation in trade margins, CEO temperament in dismissals), so little movement was expected. The band check confirms it.
- Governance rates (one 20-season league, seed 23, final parameters): 9 recalls in 20 seasons (about one every two seasons across 40 playing clubs), 276 term elections of which the sitting CEO lost about 8 percent, about 3 CEO and 2 GM retirements a year, about 6 GM hires a year. Averages hold: GM evaluation 60.0, CEO financial judgment 61.4 after 20 years.
- Limits: sentiment effects reach the game only through CEO turnover, which in turn touches only temperament-scaled dismissals, so this measure cannot show what a stronger link (CEO spending, relationships) would do. Both are deliberately not built (spec item 27).

## Meeting rooms, slice 5.6 on (Ed 8.29), six tight leagues of 36 seasons (seeds 1 to 6): 13 PASS, 2 UNCLEAR, 0 FAIL

Fairness bands: 6 league(s), 36 seasons each, first 8 dropped as warm-up.

| band | measured (mean ± 2 SE) | allowed | verdict |
| --- | --- | --- | --- |
| win_pct_sd | 0.207 ± 0.002 | 0.13 to 0.21 | PASS |
| year_to_year_corr | 0.205 ± 0.018 | 0.1 to 0.5 | PASS |
| close_game_share | 0.424 ± 0.004 | 0.35 to 0.55 | PASS |
| repeat_champion_rate | 0.089 ± 0.024 | 0.0 to 0.12 | PASS |
| max_titles_in_20 | 2.870 ± 0.524 | 0.0 to 4.7 | PASS |
| worst_league_titles | 4.000 ± 0.000 | 0.0 to 7.0 | PASS |
| best_team_title_odds | 0.262 ± 0.048 | 0.08 to 0.25 | UNCLEAR |
| returner_avg_finish | 3.072 ± 0.088 | 2.8 to 3.4 | PASS |
| returner_win_div | 0.179 ± 0.026 | 0.1 to 0.3 | PASS |
| returner_fifth_again | 0.207 ± 0.017 | 0.1 to 0.3 | PASS |
| returner_top3_in_3 | 0.825 ± 0.027 | 0.75 to 0.97 | PASS |
| returner_playoff_first | 0.320 ± 0.018 | 0.2 to 0.55 | PASS |
| exile_effect | 0.289 ± 0.727 | -0.5 to 0.5 | UNCLEAR |
| exile_double_early | 0.000 ± 0.000 | 0.0 to 0.1 | PASS |
| stuck_at_bottom | 0.084 ± 0.009 | 0.0 to 0.1 | PASS |

Schedule push toward average: opponents' mean strength per point of own strength, slope -0.017 (a schedule that ignores strength gives -0.026, because a club never plays herself), so the push is +0.009 per point; correlation -0.072; league slopes -0.017, -0.020, -0.010, -0.022, -0.022, -0.011. Positive push = stronger clubs face tougher schedules.

**Reading it.**
- No band fails. The two UNCLEAR bands are the noise-limited ones that have wavered before: exile_effect, and best_team_title_odds, which sits at 0.262 ± 0.048 against a 0.25 ceiling (it was 0.167 before this slice and has been UNCLEAR in earlier states). The rooms slice is the first to change it in the unclear direction, so it is worth a 12-league run before anyone concludes it is noise. Win % spread 0.207, persistence 0.205 and the schedule push (+0.009, none) did not move.
- Per league over 36 seasons: about 10,800 contract rooms (350 refused), 350 trades, 3,300 interviews of which 350 were declined (10 percent; forced hires 0.2), 28.5 recalls (0.8 a year, a little under the one a year the owner asked for), 465 term elections, 127 CEOs replaced.
- Limits: the GM negotiating rating moves what a club pays by a few percent, and good negotiators can concentrate talent slightly; this measure cannot separate that from noise at six leagues.

## Twelve-league confirmation of slice 5.6 and 5.7 (Ed 8.29 and 8.30), seeds 1 to 12: 13 PASS, 2 UNCLEAR, 0 FAIL

Seeds 1 to 6 are the slice 5.6 run above; seeds 7 to 12 were run on the same code (slice 5.6, pinned). Slice 5.7 (production recording, analytics, halls) changes no game or season: a digest of 240 engine games matches its pre-recording value and four league seasons of seed 1 are identical, so these twelve leagues stand for 5.7 as well.

Fairness bands: 12 league(s), 36 seasons each, first 8 dropped as warm-up.

| band | measured (mean ± 2 SE) | allowed | verdict |
| --- | --- | --- | --- |
| win_pct_sd | 0.207 ± 0.001 | 0.13 to 0.21 | PASS |
| year_to_year_corr | 0.199 ± 0.013 | 0.1 to 0.5 | PASS |
| close_game_share | 0.424 ± 0.003 | 0.35 to 0.55 | PASS |
| repeat_champion_rate | 0.071 ± 0.018 | 0.0 to 0.12 | PASS |
| max_titles_in_20 | 2.565 ± 0.384 | 0.0 to 4.7 | PASS |
| worst_league_titles | 4.000 ± 0.000 | 0.0 to 7.0 | PASS |
| best_team_title_odds | 0.217 ± 0.049 | 0.08 to 0.25 | UNCLEAR |
| returner_avg_finish | 3.096 ± 0.057 | 2.8 to 3.4 | PASS |
| returner_win_div | 0.175 ± 0.018 | 0.1 to 0.3 | PASS |
| returner_fifth_again | 0.215 ± 0.016 | 0.1 to 0.3 | PASS |
| returner_top3_in_3 | 0.825 ± 0.015 | 0.75 to 0.97 | PASS |
| returner_playoff_first | 0.321 ± 0.010 | 0.2 to 0.55 | PASS |
| exile_effect | 0.090 ± 0.540 | -0.5 to 0.5 | UNCLEAR |
| exile_double_early | 0.000 ± 0.000 | 0.0 to 0.1 | PASS |
| stuck_at_bottom | 0.085 ± 0.005 | 0.0 to 0.1 | PASS |

Schedule push toward average: opponents' mean strength per point of own strength, slope -0.018 (a schedule that ignores strength gives -0.026, because a club never plays herself), so the push is +0.007 per point; correlation -0.076; league slopes -0.017, -0.020, -0.010, -0.022, -0.022, -0.011, -0.034, -0.013, -0.007, -0.024, -0.016, -0.023. Positive push = stronger clubs face tougher schedules.

**Reading it.**
- best_team_title_odds, UNCLEAR at 0.262 on the first six leagues, is 0.217 ± 0.049 on twelve and 0.173 on the second six alone: the first reading was noise, not an effect of the GM's negotiating rating. It stays UNCLEAR only because its error bar crosses the 0.25 ceiling; the mean is inside the band.
- exile_effect is UNCLEAR as in every measurement so far (its error bar is wider than its band at this sample size).
- No band fails. Win % spread 0.207 (ceiling 0.21), persistence 0.199, schedule push +0.007 (none).

## The people layer's effect on payroll, quality and talent concentration (one 12-season league, seed 21; years 6 to 12)

Measured directly, as promised when player demands were added. Baseline is the commit before the people layer began (random same-position swaps; clubs choose by true ability; no demands, souls or coaches); "people layer" is the Ed 8.26 code.

| measure | before the people layer | with scouting, souls, demands, coaches and trades |
| --- | --- | --- |
| average club payroll (cap number, of 1,000) | 934 | 926 |
| spread of payroll between clubs (sd) | 24.6 | 27.8 |
| average roster rating | 67.9 | 68.1 |
| spread of roster rating between clubs (sd) | 1.03 | 1.13 |
| share of the top 5 percent of players held by the 5 richest-in-talent clubs | 0.207 | 0.206 |
| trades a year | 29.2 | 11.2 |

- **Payroll, quality and concentration did not move.** Demands cost about 4 percent over the old price curve, but clubs end up at the cap either way, so payroll is set by the cap, not by prices. The concentration figure picks the top five clubs after the fact, so it reads above the even-spread 0.104 in both states; the comparison, not the level, is the finding.
- **Trade volume fell to under 40 percent** of the stand-in's. Most deals are one-for-one swaps (2 players a trade) accepted in round 1; counters average 0.6 a year, so the second and third rounds, and with them the general manager's negotiation skill, are nearly unused. The real NFL makes more trades; raising the number of clubs that propose per run is the lever (`attempts_per_call`), and it is the owner's call.
- **One league, one seed.** These are single-league figures; the pattern is consistent with the band results over six leagues, but treat the small differences (payroll spread, club spread) as unproven.

## Parity test beds (src/dfl/parity.py): can the bands see unfairness?
v1 defines two profiles: **tight** (the rules as written) and **none** (no salary or fairness regulation). Two 36-season leagues each (seeds 21, 22), compared with the tight result above. In v2 `none` removes the salary cap and the end-of-block rule for returners (the lottery stays: returners have no standing to draft by). I added a third bed, `rigged`, as a control rather than a design option: no cap, and two clubs always shop first in free agency.

**none (no cap, no returner block):**

| band | measured (mean ± 2 SE) | allowed | verdict |
| --- | --- | --- | --- |
| win_pct_sd | 0.245 ± 0.000 | 0.13 to 0.21 | FAIL |
| year_to_year_corr | 0.373 ± 0.013 | 0.1 to 0.5 | PASS |
| close_game_share | 0.346 ± 0.006 | 0.35 to 0.55 | UNCLEAR |
| repeat_champion_rate | 0.339 ± 0.107 | 0.0 to 0.12 | FAIL |
| max_titles_in_20 | 6.500 ± 6.111 | 0.0 to 4.7 | UNCLEAR |
| worst_league_titles | 10.000 ± 0.000 | 0.0 to 7.0 | FAIL |
| best_team_title_odds | 0.571 ± 0.214 | 0.08 to 0.25 | FAIL |
| returner_avg_finish | 3.277 ± 0.071 | 2.8 to 3.4 | PASS |
| returner_win_div | 0.181 ± 0.022 | 0.1 to 0.3 | PASS |
| returner_fifth_again | 0.312 ± 0.009 | 0.1 to 0.3 | FAIL |
| returner_top3_in_3 | 0.736 ± 0.019 | 0.75 to 0.97 | UNCLEAR |
| returner_playoff_first | 0.308 ± 0.054 | 0.2 to 0.55 | PASS |
| exile_effect | 0.407 ± 0.460 | -0.5 to 0.5 | UNCLEAR |
| exile_double_early | 0.299 ± 0.027 | 0.0 to 0.1 | FAIL |
| stuck_at_bottom | 0.104 ± 0.008 | 0.0 to 0.1 | UNCLEAR |

Schedule push toward average: opponents' mean strength per point of own strength, slope 0.030 (a schedule that ignores strength gives -0.026, because a club never plays herself), so the push is +0.056 per point; correlation 0.117; league slopes 0.034, 0.027. Positive push = stronger clubs face tougher schedules.

**rigged (control):**

| band | measured (mean ± 2 SE) | allowed | verdict |
| --- | --- | --- | --- |
| win_pct_sd | 0.244 ± 0.005 | 0.13 to 0.21 | FAIL |
| year_to_year_corr | 0.434 ± 0.025 | 0.1 to 0.5 | PASS |
| close_game_share | 0.341 ± 0.004 | 0.35 to 0.55 | FAIL |
| repeat_champion_rate | 0.607 ± 0.071 | 0.0 to 0.12 | FAIL |
| max_titles_in_20 | 13.111 ± 3.111 | 0.0 to 4.7 | FAIL |
| worst_league_titles | 15.000 ± 0.000 | 0.0 to 7.0 | FAIL |
| best_team_title_odds | 0.625 ± 0.036 | 0.08 to 0.25 | FAIL |
| returner_avg_finish | 3.263 ± 0.080 | 2.8 to 3.4 | PASS |
| returner_win_div | 0.152 ± 0.018 | 0.1 to 0.3 | PASS |
| returner_fifth_again | 0.268 ± 0.045 | 0.1 to 0.3 | UNCLEAR |
| returner_top3_in_3 | 0.764 ± 0.067 | 0.75 to 0.97 | UNCLEAR |
| returner_playoff_first | 0.286 ± 0.071 | 0.2 to 0.55 | PASS |
| exile_effect | -0.175 ± 0.408 | -0.5 to 0.5 | UNCLEAR |
| exile_double_early | 0.259 ± 0.009 | 0.0 to 0.1 | FAIL |
| stuck_at_bottom | 0.107 ± 0.019 | 0.0 to 0.1 | UNCLEAR |

Schedule push toward average: opponents' mean strength per point of own strength, slope 0.056 (a schedule that ignores strength gives -0.026, because a club never plays herself), so the push is +0.082 per point; correlation 0.213; league slopes 0.049, 0.063. Positive push = stronger clubs face tougher schedules.

**Reading it.**
- **The harness can see unfairness.** The rigged control fails 8 of 15 bands (title concentration 13 in 20 seasons against a 4.7 limit; the strongest club wins 63% of titles; repeat champions 61%). A league that is fair by construction would not do that.
- **Removing the cap alone is enough to fail 5 bands**: win % spread 0.245, repeat champions 34%, the strongest club winning 57% of titles, one club taking 10 titles in 20 seasons, returners finishing 5th again 31%. I expected little here, because clubs have no money or revenue yet, and I was wrong: even random stand-in clubs concentrate talent when nothing limits what they can sign. The cap is doing real parity work now, before any economy exists.
- exile_double_early rises to 0.30 without the block rule, confirming the Ed 8.19 rule is what holds that band.
- **Loose** (between tight and none) is not defined by v1 and would be an arbitrary cap multiple; not built. Say if you want one.
- Limits: two leagues each; the revenue, ownership and sentiment effects wait for the people layer, so `none` here is a cap-and-draft test, not the full v1 meaning.

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

## Ed 8.31: recalls about twice a year; coaching and GM turnover toward NFL rates (2026-10-24)

Six leagues, 36 seasons each (seeds 1 to 6), first 8 seasons dropped. Recall threshold 25 (a first try at 23 gave 1.47 recalls a year, too few; its fairness was 12 pass, 3 unclear, 0 fail).

**Result: 13 PASS, 2 UNCLEAR, 0 FAIL.** The unclear bands are `repeat_champion_rate` (0.119 ± 0.048 against a ceiling of 0.12; the first try gave 0.048, so this reads as noise, not drift) and `exile_effect` (-0.102 ± 0.466, as in every earlier measurement, too few exiles to decide). Win-percentage spread 0.209 (ceiling 0.21), year-to-year persistence 0.184, schedule push +0.003.

Front-office turnover, share of playing clubs changing the post in a year (about 40 playing clubs a year assumed):

| post | measured | NFL benchmark |
| --- | --- | --- |
| head coach | 25.0% | about 21% (6.8 changes a year, 2004 to 2013) |
| offensive coordinator | 33.9% | about 30 to 35% (10 to 18 of 32 clubs a year, 2017 to 2019) |
| defensive coordinator | 33.5% | assumed like the offensive side |
| special-teams coordinator | 32.8% | assumed like the offensive side |
| general manager | 20.9% | about 15 to 20% (about 14% fired a year, plus exits) |
| CEO (replaced or retired) | 20.6% | no NFL comparison (DFL has recalls and elections) |

Recalls a year across the league, by league: 1.81, 1.92, 1.92, 2.00, 2.06, 1.44 (mean 1.86). Head coach turnover is about four points above the NFL figure; left as is, within the builder's five-point reading of "close to". The benchmarks are press counts, not a census. Settings: COACHING.csv and GOVERNANCE.csv, Design Document Ed 8.31 item 30.

## Ed 8.32: expected-points table refit each season (2026-10-25)

Not a fairness change: the refit is read only when a play is valued. Proof: four seasons of seed 7 played with the refit and with it switched off give the same league digest every year. The refit table after four seasons rose above the data table by about 0.6 points at midfield (2.33 against 1.7 at the 50), the same as on 1,600 sampled games. The 268 earlier tests and the 240-game digest still pass, and seven new tests cover the refit.

## Ed 8.33: CEO tenure and the refit costs (2026-10-25)

Six leagues, 36 seasons each (seeds 1 to 6), first 8 dropped. Settings: incumbent bonus 20, mood weight 25, CEO retirement from 74 (certain at 82), CEO mean age 46, recall threshold 25.5. Three earlier six-league runs tuned these (threshold 25 gave 1.6 recalls a year, 26 and 27 gave 2.4).

**Result: 13 PASS, 2 UNCLEAR, 0 FAIL.** Unclear: `win_pct_sd` (0.208 ± 0.002 against a ceiling of 0.21) and `repeat_champion_rate` (0.113 ± 0.057 against 0.12). The cost refit and CEO changes do not touch games, so these drift only through the random streams.

| measure | result |
| --- | --- |
| recalls a year, league | 2.10 (leagues 1.61 to 2.47) |
| CEO turnover | 9.8% of clubs a year = 5.3 recall + 1.5 lost term election + 3.3 retirement (was about 21%) |
| head coach / coordinators / GM | 25% / 32 to 34% / 21% (unchanged) |

Accountability, term elections by the club's fan sentiment that year: sitting CEO lost 19% under 35, 7% at 35 to 45, 4% at 45 to 55, 1% above 55. The CEO is now the longest-tenured post (about 10 years against about 5 for a general manager and 4 for a head coach). Retirement runs high in this window because all founding CEOs are of similar age; it should settle lower in a longer league.

## Ed 8.34: trades stage 2, the public trade price (2026-10-25)

Six leagues, 36 seasons each (seeds 1 to 6), first 8 dropped; the run before it (Ed 8.33, same seeds) is the comparison.

**Result: 11 PASS, 4 UNCLEAR, 0 FAIL.** Unclear: `win_pct_sd` (0.210 ± 0.002 against a ceiling of 0.21; 0.208 before), `repeat_champion_rate` (0.095 ± 0.035), `best_team_title_odds` (0.220 ± 0.072) and `exile_effect` (0.285 ± 0.574, too few exiles to decide). The first three straddle their limits on error bars alone; none was outside its limit in the last run except through noise, and the change touches trading, not games.

Trade activity barely moved: trades 2,126 against 2,045 (36 seasons x 6 leagues), refusals 2,377 against 2,299, deals 64,773 against 65,004. Schedule push +0.015 per point (was +0.004), correlation -0.043; positive means stronger clubs face tougher schedules, so the direction is the intended one, but the league slopes now spread wider (-0.003 to -0.021).

Public estimate accuracy over eight seasons (seed 3): correlation with true overall within a position 0.48, 0.41 and 0.34 after seasons 1, 3 and 8, against 0.72, 0.69 and 0.64 for a club's own scouting view.

## Ed 8.35: clubs price free agents by their own view (2026-10-25), first run at recall threshold 25.5

Six leagues, 36 seasons each, first 8 dropped. **12 PASS, 3 UNCLEAR, 0 FAIL** (unclear: `repeat_champion_rate` 0.089 ± 0.048, `best_team_title_odds` 0.226 ± 0.054, `exile_effect` 0.435 ± 0.345). The league became more even: win-percentage spread 0.195 (was 0.208 to 0.210, ceiling 0.21), now passing outright. Trades 2,312 (was 2,126), refusals 2,032 (was 2,377), deals 64,132 (was 64,773). Recalls fell to 341 in 216 league-seasons, 1.58 a year, because fewer clubs sink to the recall line when the league is more even; the threshold was raised from 25.5 to 26 and the leagues rerun (next section). Schedule push +0.022 per point, correlation -0.018, league slopes -0.028 to +0.026 (wide).

Second run, recall threshold 26: **14 PASS, 1 UNCLEAR (`best_team_title_odds` 0.185 ± 0.105), 0 FAIL**, win-percentage spread 0.193. Recalls 1.65 a year (leagues 1.31 to 2.17), still short of two; CEO turnover 8.5% (4.1 recall + 1.4 lost term election + 3.1 retirement); head coach 25%, coordinators 32 to 33%, general manager 20%. Term elections lost by the sitting CEO: 20% under sentiment 35, 8% at 35 to 45, 4% at 45 to 55, 1% above. Threshold raised to 27 and rerun (next section).

Third run, recall threshold 27 (the setting kept): **15 PASS, 0 UNCLEAR, 0 FAIL**, the first run in which every band passes outright. Win-percentage spread 0.193 ± 0.002, repeat-champion rate 0.077, best-team title odds 0.202 ± 0.044, exile effect -0.038 ± 0.443. Recalls 2.04 a year (leagues 1.58 to 2.44); CEO turnover 9.5% (5.1 recall + 1.3 lost term election + 3.2 retirement); head coach 25.6%, coordinators 32 to 33%, general manager 20.5%. Sitting CEO lost term elections 17% under sentiment 35, 8% at 35 to 45, 4% at 45 to 55, 0% above. Schedule push +0.013 per point. Six leagues are few: the exile band and the title bands still carry wide error bars.

## Ed 8.36 (coach judgement, 2026-10-25)
Starters follow the coaches' judged overall (true overall plus a zero-mean error, 3.5 points with no evidence). Six leagues, 36 seasons each, first 8 dropped. **14 PASS, 1 UNCLEAR, 0 FAIL**; the unclear band is `exile_effect` (0.086 ± 0.535 against ±0.5, a wide interval from few exiled clubs, not a drift: the mean is inside the band and the band was -0.038 ± 0.443 a run earlier). Win% spread 0.198 (0.193 before), year-to-year correlation 0.227, close-game share 0.442, best-team title odds 0.173. The error has no lean, so club strength is unchanged on average; the effect is a small shuffle among starters of near-equal rating. Governance rates are unchanged by this edition (coach-judgement does not touch hiring or firing). Report: the six `fairness.py` runs at seeds 1 to 6.
