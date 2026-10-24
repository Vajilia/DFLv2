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
