# Workbook reconciliation (owner's workbook of 2026-10-30)

The owner attached `22468576-DFL_Workbook_20261030.xlsx` with the instruction to inspect and reconcile it before changing team data, preserving stable team IDs. This is the record of that reconciliation. Spec Edition 8.55 (§4, §5, §6, §9, §19) carries the result; `data/season1/TEAMS.csv`, `SCHEDULE.csv` and `COLOR_REGISTRY.csv` are the files changed.

## Method
1. Read every sheet of the workbook (Teams, Schedule, colour registry, postseason dates) with cached cell values, and compared each Teams row to the repository's `TEAMS.csv` by `team_id`.
2. Applied only the differences the workbook states. Every `team_id` is unchanged (48 ids, 1–48, one per club); no club was added or removed; the 40 active / 8 ambassador split and the ambassador in each division are unchanged.
3. Rebuilt `SCHEDULE.csv` by mapping the club names of the old template onto the new ones through the stable ids, and checked it against the workbook's own Schedule sheet: **identical in all 360 rows** (week, game, date, kickoff, away, home).
4. Checked the result with `tools/validate.py data/season1 --doc docs/spec/DFL_Design_Document_Ed8.57.md` (75 checks, all passing).

## Division membership after reconciliation

| Conference | Division (bed in Season 1) | Gemstone | Seeds 1–5 (team_id) | Ambassador (team_id) |
|---|---|---|---|---|
| Western | Northwest (1) | Peridot | Montana Outriders (30), Seattle Orcas (21), Utah Avalanche (31), Calgary Stampede (39), Idaho Tomahawks (40) | Portland Tempest (41) |
| Western | Southwest (2) | Topaz | San Francisco Shine (7), San Diego Sirens (20), Sacramento Sting (33), Arizona Thunderbirds (6), Las Vegas Invaders (19) | Los Angeles Legion (4) |
| Western | Central (3) | Tourmaline | Oklahoma City Outlaws (43), Omaha Assassins (34), Dakota Divas (27), Kansas City Reapers (18), Denver Unicorns (16) | Minnesota Warriors (5) |
| Western | Southern (4) | Garnet | Houston Faeries (17), Mexico City Mariachas (42), Dallas Starlings (1), San Antonio Vaqueras (29), New Mexico Scorpions (44) | Baja Barracudas (45) |
| Eastern | Great Lakes (5) | Aquamarine | Green Bay Glamour (3), Chicago Foxes (9), Detroit Soul (2), Cleveland Mud Dawgs (15), Toronto Sentinels (46) | Buffalo Brats (22) |
| Eastern | Atlantic (6) | Emerald | Montreal Storm (47), New England Rebels (26), New York Gladiators (10), Philadelphia Harpies (12), Washington Commandos (14) | Baltimore Rooks (32) |
| Eastern | Midland (7) | Turquoise | St. Louis Archers (35), Tennessee Belles (38), Cincinnati Shadow Katz (23), Carolina Comets (36), Pittsburgh Riviters (28) | Indianapolis Pit Vipers (24) |
| Eastern | Southeast (8) | Alexandrite | Alabama Thunder (48), Havana Mariposas (37), Miami Monsters (25), Atlanta Mavericks (8), New Orleans Mayfairs (11) | Tampa Bay Corsairs (13) |

Division order in §4: Western (Northwest, Southwest, Central, Southern), Eastern (Great Lakes, Atlantic, Midland, Southeast). The old Eastern divisions Midwest, Northeast and East no longer exist; the Eastern clubs were redistributed by the workbook (18 division changes in all) and the rotation formula (§3) is unchanged, with the Eastern divisions filling beds 5–8 in the order Great Lakes, Atlantic, Midland, Southeast.

## Changes by club (old → new, by `team_id`)
| id | Change |
|---:|---|
| 2 | Detroit: division East → Great Lakes |
| 3 | Green Bay: Midwest → Great Lakes |
| 9 | Chicago: Midwest → Great Lakes |
| 10 | New York: name Goliaths → **Gladiators**; Northeast → Atlantic |
| 12 | Philadelphia: Northeast → Atlantic |
| 14 | Washington: East → Atlantic |
| 15 | Cleveland: East → Great Lakes |
| 22 | Buffalo: Northeast → Great Lakes |
| 23, 24, 35, 38 | Cincinnati, Indianapolis, St. Louis, Tennessee: Midwest → Midland |
| 26 | New England: Northeast → Atlantic |
| 28, 36 | Pittsburgh, Carolina: East → Midland |
| 32 | Baltimore: East → Atlantic |
| 37 | **Florida Fangs (JAX, EverBank Stadium, Jacksonville) → Havana Mariposas (HAV, Estadio Panamericano, Havana, Cuba)**; id 37 kept; stays in the Southeast |
| 42 | Mexico City: name Aztecas → **Mariachas** |
| 46, 47 | Toronto: Northeast → Great Lakes; Montreal: Northeast → Atlantic |

Seeds and beds changed for the clubs that moved (10 `division_rank` and 11 `bed_id` values), because each division's five active clubs hold seeds 1–5 in the workbook's order and beds follow the new division order. Season 1 seeds were "drawn by chance" (§3); the workbook's seeds are the Season 1 draw.

## Colour registry
Division gemstones (`tertiary_hex`): Northwest **Peridot** `#A5C83B` (was Topaz), Southwest **Topaz** `#E6A32E` (was Peridot), Great Lakes Aquamarine `#55C5D8`, Atlantic Emerald `#009B77`, **Midland Turquoise `#26A9A0`**, Southeast Alexandrite `#7040A0`. The 22 clubs whose division gemstone changed carry the new colour in `tertiary_color` and `tertiary_hex`; primary and secondary colours are unchanged, so every `(primary_hex, secondary_hex)` pair is still unique across the 48 clubs.

## Venue and postseason
The workbook's Schedule sheet lists the postseason dates (wild card 2000-12-03, divisional 12-10, conference championships 12-17, Diamond Coronation 2000-12-31) at "Amethyst Aloha Stadium". These match the calendar the code derives (§2) and the permanent Coronation venue of the owner's ruling (§3).

## Not imported (typing slips in the workbook; the repository spelling stands)
- New Mexico's `location` is "Albequerque, NM" in the workbook (the repository keeps "Albuquerque, NM") and St. Louis's is "St. Louis, Mo" (the repository keeps "St. Louis, MO").
- The column header "abreviation" for `abbr`.

## For the owner to confirm (nothing here blocks anything)
- **New York's name (team 10) is Gladiators.** The workbook says Gladiators; the repository said Goliaths before this batch and now says Gladiators (`data/season1/TEAMS.csv`, pinned by `tests/test_team_identity.py`). Direction of the change: Goliaths → Gladiators; nothing was reversed.
- Havana's venue, **Estadio Panamericano**, was taken from the workbook as written.
- The eight ambassadors keep their identities (Portland, Los Angeles, Minnesota, Baja, Buffalo, Baltimore, Indianapolis, Tampa Bay); the realignment moved Buffalo (Northeast → Great Lakes) and Baltimore (East → Atlantic) with their divisions.
