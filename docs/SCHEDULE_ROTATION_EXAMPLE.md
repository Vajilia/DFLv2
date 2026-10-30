# Worked example: whom a club plays across eight seasons

Written 2026-10-29; the club tables were regenerated on 2026-10-30 for the realigned Eastern divisions (Edition 8.55; the rotation itself is unchanged). The rotation is **unchanged**; this only shows what it does today, on the real code (`league.season_tables`), for one club: **Montana**, the Northwest division's seed 1 in Season 1. The numbers are reproduced by `tests/test_schedule_rotation.py` (class `RotationChangesWhenAndWhereNotWhom`) and the script in section 4.

## 1. Two different things the word "division" can mean

| Term | What it is | Does it change? |
| --- | --- | --- |
| **Division identity** | The fixed name of a group of six clubs: (conference, division), e.g. Western Northwest. A club belongs to it for life (§3: "Clubs never change division"). | Never |
| **Bed** | One of eight fixture slots, numbered 1–8, in two blocks (beds 1–4 and 5–8). Each division fills one bed for the season (§3, "Seed beds and rotation"). | Every season: each division moves one bed on inside its block, and the blocks swap after four seasons |
| **Seed** | 1–5, a club's rank inside its division, from last season's final order (§3). | Every season, with results |
| **Position** | (bed, seed): the unit the schedule is written in (§4 `bed_id`, e.g. `1-1`). | Follows bed and seed |

Montana's identity is always Western Northwest. Her bed is 1, 2, 3, 4, 5, 6, 7, 8 in Seasons 1 to 8 (§3 formula: bed = 1 + 4p + (i + k) mod 4 for a Western division, i = 0 for Northwest).

## 2. The rule that produces the behaviour

Spec §6: "Season 1, which is the bed template. ... **Every later season uses these same games, each bed-and-seed position filled by the club holding it that season (§3).**"

So a season's schedule is the Season 1 list of games between *positions*. The rotation changes which division sits in which bed; it does not change the list. In Season 1's list, every pair of beds that meet use the *same seed pairing*, whichever beds they are (checked on `data/season1/SCHEDULE.csv`):

- two beds in the same block (6 pairs per block): seeds meet as 1v1, 1v2 (twice), 2v3 (twice), 3v4 (twice), 4v5 (twice), 5v5. Every one of the six pairs gets exactly this set;
- two beds in opposite blocks (16 pairs): equal seeds only, 1v1 … 5v5, once each;
- the same bed (a division's own games): each rival twice.

Every bed meets every other bed, and the seed pattern does not depend on which two beds they are. Moving whole divisions between beds (the rotation) therefore leaves the set of (division, seed) matchups exactly as it was; the same club-level set of games is re-labelled onto different beds. What the rotation does change is the *order* of the games within the season, the kickoff slots, and the home/away pattern, because the template's home/away is written per bed.

## 3. The example

Montana's 14 non-division opponents, Seasons 1–8.

**A. Seed fixed at 1 every season** (nothing else changes):

| Season | Bed | Conference opponents (6) | Interconference opponents (4) | Same as Season 1? | Home/away vs the four (alphabetical) |
| --- | --- | --- | --- | --- | --- |
| 1 | 1 | Houston, Mexico City, Oklahoma City, Omaha, San Diego, San Francisco | Alabama, Green Bay, Montreal, St. Louis | – | H A H A |
| 2 | 2 | same six | same four | yes | H A H A |
| 3 | 3 | same six | same four | yes | H A H A |
| 4 | 4 | same six | same four | yes | H A H A |
| 5 | 5 | same six | same four | yes | A H A H |
| 6–8 | 6–8 | same six | same four | yes | A H A H |

Over Seasons 1–8 each of the 14 opponents is met the same number of times, with home and away exactly balanced (4 and 4 for single games; her four division rivals twice a season, 8 and 8). The only things that moved are the order of the games, the kickoff slots, the home/away pattern and, after Season 4, which side is home against each of the four.

**B. Seed changing** (illustration: Montana finishes seed 1, 3, 2, 5, 1, 4, 2, 3 in Seasons 1–8; the other clubs stay where they are):

| Season | Seed | Bed | Conference opponents (6) | Interconference opponents (4) |
| --- | --- | --- | --- | --- |
| 1 | 1 | 1 | Houston, Mexico City, Oklahoma City, Omaha, San Diego, San Francisco | Alabama, Green Bay, Montreal, St. Louis |
| 2 | 3 | 2 | Arizona, Kansas City, Mexico City, Omaha, San Antonio, San Diego | Cincinnati, Detroit, Miami, New York |
| 3 | 2 | 3 | Dakota, Dallas, Houston, Oklahoma City, Sacramento, San Francisco | Chicago, Havana, New England, Tennessee |
| 4 | 5 | 4 | Arizona, Denver, Kansas City, Las Vegas, New Mexico, San Antonio | New Orleans, Pittsburgh, Toronto, Washington |
| 5 | 1 | 5 | **same as Season 1** | **same as Season 1** |
| 6 | 4 | 6 | Dakota, Dallas, Denver, Las Vegas, New Mexico, Sacramento | Atlanta, Carolina, Cleveland, Philadelphia |
| 7 | 2 | 7 | same as Season 3 | same as Season 3 |
| 8 | 3 | 8 | same as Season 2 | same as Season 2 |

Seasons 1 and 5 (seed 1, beds 1 and 5), 3 and 7 (seed 2, beds 3 and 7) and 2 and 8 (seed 3, beds 2 and 8) give identical opponents although the beds differ: the opponents follow the **seed**, the bed does not matter. The interconference four are always "the club holding the same seed in each Eastern division" (§3 and the validator's rule: 4 interconference games, equal seed, one per opposing division); the six conference opponents are fixed by the seed pairings above.

## 4. Reproducing

`python3 -I` with `src` on the path: build `league.Template("data/season1")`, take `initial_state()`, call `league.season_tables(tpl, state, season)` for `season` 1–8 and read the games that involve Montana (case B edits only Northwest's `seeds` mapping); or run `python3 -I tools/show_rotation_example.py`. The tests `test_with_seeds_fixed_the_opponents_never_change` and `test_with_seeds_fixed_home_and_away_balance_exactly_over_each_eight_season_cycle` pin case A.

## 5. What this does and does not say

It is a consequence of the written design, not a coding error: the code follows §3 and §6 exactly. It does mean the bed rotation alone never changes a club's opponents; if the owner wanted it to (for example, so that a club's conference opponents vary from year to year at a fixed seed), the template's seed pairing would have to depend on the bed pair, which is a change to §6 and to the Season 1 table. That is the owner's decision and has **not** been made or implemented.
