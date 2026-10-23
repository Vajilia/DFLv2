# DFL — Living Design Document
**Edition 8.15 · 2026-10-23** (amends Edition 8.14; changes listed in §19)
**Content hash (SHA-256 of this file with this line removed):** `6a77c043f6ca8fb89a5e17637f0e291d9ae8995ebf47a01a6bcd38d71a547454`

**Normative fence.** §0–§20 are normative. Where this document and any earlier artifact disagree, this document wins. Reference code: `tools/validate.py`, `tools/run_seasons.py`, `src/dfl/` and `tests/` in the DFLv2 repository (§18). The old archive, the "Audit changes" list, and the workbook are all superseded by this text.

**Reading order for implementers:** §2 → §3 → §5 → §6 → §10 → §11 → §17 → §18.

---

## §0 Current values
Single source. Nothing below repeats a figure that appears here.

| Item | Value |
|---|---|
| League / clubs / conferences / divisions | DFL · 48 · 2 · 8 (six clubs each) |
| Active clubs / ambassadors | 40 · 8 |
| Regular season | 18 games per club, 9H/9A, 360 games |
| Numbered fixture weeks / calendar weeks / active-club breaks | 19 · 27 · 3 (after weeks 4, 9, 14) |
| Bye window | weeks 15–19, one per club |
| Kickoff windows | 5 — Sat 17:30, Sun 10:00, Sun 13:00, Sun 17:30, Mon 17:30 |
| Postseason | 14 clubs (7 per conference), 13 games |
| Ambassador Tour | 8 clubs, 7 rounds, 28 games |
| Cap · reserve · min salary · comp threshold · CEO salary | $1,000 · $25 · $5 · $75 · $2,000 |
| Rookie scale (R1–R7) · first-round option | $25/$18/$14/$11/$9/$7/$5 · $35 |
| Roster · practice squad · preseason ceiling · required places · Bowl places | 53 · 16 · 90 · 48 · 53 |
| Contracts counted against the cap before the final cut · credited seasons that exempt a released player from waivers | 69 (53 + 16) · 4 |
| Draft | 7 rounds × 48 = 336 picks |
| Bracket winner paths · halls · directional relationships | 8,192 · 59 · 2,256 |
| Draft class size · pool floor per position · leadership candidates per role | 672 · 8 · 4 |
| Real time: offseason · per competitive week block · year | 45 min · 120 min · ≈55 h |
| Season length target | about 50 hours of real time; a target, and missing it is not a defect |
| Recall threshold · CEO election cycle | sentiment 20 · every 3 seasons |
| Tiara recipients · Hall credited seasons · starting memories | 53 + 6 staff · 5 · 32 |
| Physics timestep · decision interval | 0.05 s · ≤0.10 s |
| International venue pool · venues drawn per season | 50 (minimum 8) · 8 (7 Tour rounds + the Bowl) |

---

## §1 Identity and scope
- All characters are female. The DFL owns all 48 clubs.
- The user is a pure viewer: no control, no secret information, no controlling vote.
- League time is a fixed UTC civil calendar with no daylight-saving changes.
- No monetary inflation. No cap rollover. No cap-space trading.
- Simulation is deterministic; the front end is a renderer.

## §2 World clock and season calendar
- The universe opens **1 January 2000**. Season 1's genesis offseason occupies January–June 2000.
- **The DFL year runs 1 January to 31 December.** The offseason (January to June) is the front of the same year as the season it leads into, and the league year (the cap year) turns on 1 January. Colleges are rotated to match in the storyline; the draft class simply arrives on the DFL's schedule.
- **Offseason order** (NFL order and spacing, compressed into the first half of the year; the dates are provisional and live in `data/OFFSEASON.csv`, owner may change them without touching anything else):

| Event | Date |
|---|---|
| League year turns: contracts expire, retirements take effect, cap enforced (top-count rule, §11) | 1 January |
| Leadership hiring window | 1–31 January |
| Trading opens (§12); free agency opens | 1 February |
| Draft: round 1 on the last Thursday of April; rounds 2–3 the next day; rounds 4–7 and compensatory picks the day after | late April |
| Camp opens (preseason ceiling applies) | 1 June |
| Coronation venue vote | 15 June |
| Final cut to 53 and 16, then the waiver period | the Tuesday and Wednesday before week 1 |

In the DFL calendar a year's events fire in the order of this table, then the 27-week season below, and close on 31 December.
- **The Diamond Coronation is the last Sunday of the simulated year.** All scheduling is derived backwards from it.
- Fixed offsets: final regular-season Monday = **C−34**; wild card C−28; divisional C−21; conference C−14; **Ambassador Bowl C−7**; Coronation C. All rungs are Sundays, 7 days apart.
- Wild card, divisional and conference rounds occupy the fourth, third and second Sundays before the Coronation. The Bowl is the Sunday immediately preceding it, at noon league time.
- Three active-club breaks follow numbered weeks 4, 9 and 14; ambassadors may play Tour rounds in them (round 4 falls in calendar week 11). Byes sit in weeks 15–19.
- A year is 27 calendar weeks: 22 regular-season weeks containing 19 numbered fixture weeks, then five postseason/ambassador weeks.

```
calendar_week,phase,numbered_week,saturday,sunday,monday,note
1,regular,1,2000-07-01,2000-07-02,2000-07-03,
2,regular,2,2000-07-08,2000-07-09,2000-07-10,
3,regular,3,2000-07-15,2000-07-16,2000-07-17,
4,regular,4,2000-07-22,2000-07-23,2000-07-24,
5,break,,2000-07-29,2000-07-30,2000-07-31,active-club break
6,regular,5,2000-08-05,2000-08-06,2000-08-07,
7,regular,6,2000-08-12,2000-08-13,2000-08-14,
8,regular,7,2000-08-19,2000-08-20,2000-08-21,
9,regular,8,2000-08-26,2000-08-27,2000-08-28,
10,regular,9,2000-09-02,2000-09-03,2000-09-04,
11,break,,2000-09-09,2000-09-10,2000-09-11,active-club break
12,regular,10,2000-09-16,2000-09-17,2000-09-18,
13,regular,11,2000-09-23,2000-09-24,2000-09-25,
14,regular,12,2000-09-30,2000-10-01,2000-10-02,
15,regular,13,2000-10-07,2000-10-08,2000-10-09,
16,regular,14,2000-10-14,2000-10-15,2000-10-16,
17,break,,2000-10-21,2000-10-22,2000-10-23,active-club break
18,regular,15,2000-10-28,2000-10-29,2000-10-30,bye group: seed 3
19,regular,16,2000-11-04,2000-11-05,2000-11-06,bye group: seed 1
20,regular,17,2000-11-11,2000-11-12,2000-11-13,bye group: seed 2
21,regular,18,2000-11-18,2000-11-19,2000-11-20,bye group: seed 5
22,regular,19,2000-11-25,2000-11-26,2000-11-27,bye group: seed 4
23,postseason,,,2000-12-03,,wild card round
24,postseason,,,2000-12-10,,divisional round
25,postseason,,,2000-12-17,,conference championships
26,ambassadors,,,2000-12-24,,Ambassador Bowl
27,postseason,,,2000-12-31,,Diamond Coronation
```

## §3 League structure
- Two conferences (Western, Eastern), four divisions each, six clubs per division: five active and one ambassador.
- **Exile and seeds.** After the last regular-season game, each division's five active clubs are ranked by the tiebreak cascade below. Ranks 1–4 become the next season's seeds 1–4. The last-place club is exiled: it is the next season's ambassador, and exile cannot be declined. The current ambassador returns as seed 5, so every division has seeds 1–5 and one ambassador every year. Season 1 seeds are drawn by chance.
- **Tiebreak cascade.** It orders every ranking: divisions, playoff seeding and the exile decision. Steps run in this order, each used only while clubs remain tied: (1) overall wins; (2) overall win %; (3) division wins; (4) division win %; (5) conference wins; (6) conference win %; (7) strength of schedule; (8) point differential; (9) strength of victory; then a seeded draw. Steps 3–4 apply only when every tied club is in the same division, and steps 5–6 only when every tied club is in the same conference. A tied group is sorted once by the whole cascade; there is no step-by-step reduction. The cascade is deliberately a list of columns, so every step can be shown beside each club on the standings page in this order; it intentionally has no head-to-head step and no restart (owner ruling, Edition 8.9).
- **Records.** A tied game counts as not played in the standings only: it adds no win or loss, no division or conference record, and is left out of every win % and every opponent list (so out of strength of schedule and strength of victory). For statistical purposes it is played: games played, points scored and points allowed include it, and so does point differential. Win % = wins ÷ (wins + losses), counted as 0 when a club has no decisions. Strength of schedule = the combined win % of every opponent played (their total wins ÷ their total wins and losses, once per game played); strength of victory = the same for every opponent beaten; for both, a higher figure ranks higher. Point differential = points scored minus points allowed over all regular-season games. Ambassadors' Tour results play no part.
- **Playoffs.** Identical to the NFL's. Seven clubs per conference: the four division winners (seeds 1–4) and the three best other clubs (seeds 5–7), all ordered by the cascade. Seed 1 has a bye. Wild card round: 2 v 7, 3 v 6, 4 v 5. Divisional round: seed 1 plays the lowest remaining seed and the other two survivors meet. Then the conference championships, then the Diamond Coronation between the two conference champions. A division's last-place club is exiled and cannot take a wild card. Playoff games are played at the higher seed's home venue. The Coronation's venue is chosen by a fictitious fan vote held in June and announced on 4 July from the 48 DFL club venues; no venue may host the Coronation twice within a rolling 40-year window, which leaves nine eligible venues each year. The vote is decided by a seeded random draw among the nine eligible venues. If a finalist's own venue wins the vote, it plays at home.
- Ambassadors keep identity, full roster obligations and full cap, play the 7-game Tour, and receive a first-round lottery band.
- **Seed beds and rotation.** The schedule template has eight seed beds, each holding seed positions 1–5, in two blocks: beds 1–4 and beds 5–8. Each division fills one bed, its five active clubs holding the bed's positions by seed. In Season 1 the Western divisions fill beds 1–4 in the order Northwest, Southwest, Central, Southern, and the Eastern divisions fill beds 5–8 in the order Midwest, Northeast, East, Southeast. Each season every division moves one bed on inside its block (bed 4 to bed 1, bed 8 to bed 5). After four seasons the conferences flip: in Season 5 the Western divisions fill beds 5–8 in the same order and the Eastern divisions fill beds 1–4. The next four seasons rotate inside the new blocks, then the conferences flip back, so the pattern repeats every eight seasons. Divisions never change conference. In formula form, for season n let k = (n − 1) mod 4, p = ⌊(n − 1) ÷ 4⌋ mod 2, and i = the division's position (0–3) in its conference's order above: a Western division fills bed 1 + 4p + (i + k) mod 4 and an Eastern division fills bed 1 + 4(1 − p) + (i + k) mod 4. Clubs never change division; only seeds and beds move.
- 59 halls: 1 league, 2 conference, 8 division, 48 club.

## §4 Table conventions and sort orders
- All tables are CSV, UTF-8, LF, with a header row. Dates are ISO `YYYY-MM-DD`. Times are `HH:MM` league time (UTC).
- Booleans are lowercase `true`/`false`. Empty cells are empty, never `"Exile"`.
- Canonical sorts (also asserted in §18): `TEAMS` conference → division → division_rank, where conference and division order follow `COLOR_REGISTRY` (Western, then Eastern; Northwest, Southwest, Central, Southern; Midwest, Northeast, East, Southeast), not alphabetical order; `SCHEDULE` week → game; `TOUR` round → game; `INTL_VENUES` venue_id; `CALENDAR` calendar_week; `COLOR_REGISTRY` row order as listed (league brand, conferences, then divisions in the order above).
- Club name columns are `team_city` and `team_name`; a bare `city` belongs to `INTL_VENUES` only. City values in `SCHEDULE` and `TOUR` are `team_city` values. `team_city` is a region label and may differ from the stadium's town (`location`): Minnesota plays in Minneapolis.
- `bed_id` is the durable fixture slot, `bed-rank`: the bed (1–8) and the club's seed in it, for example `1-1` for Northwest seed 1 in Season 1. A division's bed changes by season (§3). Ambassadors carry no bed_id.
- `division_rank` is the club's seed for the season, set from the previous season's final division ranking (§3). Ambassadors carry 6.
- `is_active` means *plays the regular season this year*. `is_league_member` means *is one of the 48 permanent clubs*. They are different columns because they were once conflated.

## §5 TEAMS
Season 1 values. Sorted conference → division → division_rank. Ambassadors have blank `bed_id` and `division_rank` 6.

```
team_id,abbr,team_city,team_name,conference,division,bed_id,division_rank,club_status,is_active,is_league_member,draft_band,venue,location,primary_color,primary_hex,secondary_color,secondary_hex,tertiary_color,tertiary_hex
30,MON,Montana,Outriders,Western,Northwest,1-1,1,active,true,true,standard,IronGate Stadium,"Helena, MT",Dark Forest Green,#174A32,Tan,#C2A477,Topaz,#E6A32E
21,SEA,Seattle,Orcas,Western,Northwest,1-2,2,active,true,true,standard,Lumen Field,"Seattle, WA",College Navy,#002244,Action Green,#69BE28,Topaz,#E6A32E
31,UTA,Utah,Avalanche,Western,Northwest,1-3,3,active,true,true,standard,NorthStar Bank Field,"Salt Lake City, Utah",Glacier Blue,#8FC8D8,Deep Navy Blue,#16324F,Topaz,#E6A32E
39,CAL,Calgary,Stampede,Western,Northwest,1-4,4,active,true,true,standard,Redwood Credit Union Park,"Calgary, Canada",Root Beer Brown,#492716,Prairie Green,#688B45,Topaz,#E6A32E
40,IDA,Idaho,Tomahawks,Western,Northwest,1-5,5,active,true,true,standard,Continental Freight Stadium,"Boise, ID",Pine Green,#17543B,Steel Gray,#87949E,Topaz,#E6A32E
41,POR,Portland,Tempest,Western,Northwest,,6,ambassador,false,true,lottery_1_8,HarborTrust Field,"Portland, OR",Storm Gray,#606A75,Electric Purple,#A334E8,Topaz,#E6A32E
7,SFX,San Francisco,Shine,Western,Southwest,2-1,1,active,true,true,standard,Candlestick Park,"San Francisco, CA",Scarlet Red,#AA0000,Gold,#D3BC8D,Peridot,#A5C83B
20,SDX,San Diego,Sirens,Western,Southwest,2-2,2,active,true,true,standard,Qualcomm Stadium,"San Diego, CA",Powder Blue,#6CAEDF,Gold,#D3BC8D,Peridot,#A5C83B
33,SAC,Sacramento,Sting,Western,Southwest,2-3,3,active,true,true,standard,Twin Rivers Stadium,"West Sacramento, CA",Electric Yellow,#EFFF00,Black,#101010,Peridot,#A5C83B
6,ARI,Arizona,Thunderbirds,Western,Southwest,2-4,4,active,true,true,standard,State Farm Stadium,"Glendale, AZ",Cardinal Red,#97233F,Black,#000000,Peridot,#A5C83B
19,LVR,Las Vegas,Invaders,Western,Southwest,2-5,5,active,true,true,standard,Allegiant Stadium,"Las Vegas, NV",Black,#000000,Silver,#B0B7BC,Peridot,#A5C83B
4,LAL,Los Angeles,Legion,Western,Southwest,,6,ambassador,false,true,lottery_1_8,SoFi Stadium,"Inglewood, CA",Royal Blue,#003DA5,Silver,#B0B7BC,Peridot,#A5C83B
43,OCO,Oklahoma City,Outlaws,Western,Central,3-1,1,active,true,true,standard,TriAxis Financial Stadium,"Oklahoma City, OK",Desert Tan,#C2A477,Blued Gunmetal,#293C4A,Tourmaline,#D94B89
34,OMA,Omaha,Assassins,Western,Central,3-2,2,active,true,true,standard,SilverGate Field,"Omaha, NE",Obsidian,#111318,Acid Green,#A8FF2A,Tourmaline,#D94B89
27,DAK,Dakota,Divas,Western,Central,3-3,3,active,true,true,standard,Union Capital Stadium,"Bismarck, ND",Gotham Green,#125740,Silver,#B0B7BC,Tourmaline,#D94B89
18,KCC,Kansas City,Reapers,Western,Central,3-4,4,active,true,true,standard,Arrowhead Stadium,"Kansas City, MO",Red,#C8102E,Gold,#D3BC8D,Tourmaline,#D94B89
16,DEN,Denver,Unicorns,Western,Central,3-5,5,active,true,true,standard,Empower Field at Mile High,"Denver, CO",Orange,#FF5F05,Navy,#002244,Tourmaline,#D94B89
5,MIN,Minnesota,Warriors,Western,Central,,6,ambassador,false,true,lottery_1_8,U.S. Bank Stadium,"Minneapolis, MN",Purple,#4F2683,Gold,#D3BC8D,Tourmaline,#D94B89
17,HOU,Houston,Faeries,Western,Southern,4-1,1,active,true,true,standard,NRG Stadium,"Houston, TX",Deep Steel Blue,#03202F,Battle Red,#A71930,Garnet,#9A1B36
42,MCA,Mexico City,Aztecas,Western,Southern,4-2,2,active,true,true,standard,Ironclad Insurance Stadium,"Mexico City, Mexico",Turquoise,#20AFA6,Sun Gold,#E9B44C,Garnet,#9A1B36
1,DAL,Dallas,Starlings,Western,Southern,4-3,3,active,true,true,standard,AT&T Stadium,"Arlington, TX",Navy,#002244,Silver,#B0B7BC,Garnet,#9A1B36
29,SAV,San Antonio,Vaqueras,Western,Southern,4-4,4,active,true,true,standard,Stratos Bank Stadium,"San Antonio, TX",Bronze,#CD7F32,Desert Teal,#3D8C8C,Garnet,#9A1B36
44,NMS,New Mexico,Scorpions,Western,Southern,4-5,5,active,true,true,standard,Crown Harbor Field,"Albuquerque, NM",Midnight Plum,#321C31,Desert Copper,#D47842,Garnet,#9A1B36
45,BAJ,Baja,Barracudas,Western,Southern,,6,ambassador,false,true,lottery_1_8,PrairieStar Stadium,"Cabo San Lucas, Mexico",Abyssal Teal,#083B4C,Predator Silver,#A8B7BC,Garnet,#9A1B36
35,SLR,St. Louis,Archers,Eastern,Midwest,5-1,1,active,true,true,standard,MetroAxis Field,"St. Louis, MO",Navy,#002244,Metallic Gold,#B3995D,Aquamarine,#55C5D8
9,CHI,Chicago,Foxes,Eastern,Midwest,5-2,2,active,true,true,standard,Soldier Field,"Chicago, IL",Navy,#002244,Orange,#FF5F05,Aquamarine,#55C5D8
38,TEN,Tennessee,Belles,Eastern,Midwest,5-3,3,active,true,true,standard,Nissan Stadium,"Nashville, TN",Navy,#0C2340,Titans Blue,#4B92DB,Aquamarine,#55C5D8
3,GBP,Green Bay,Glamour,Eastern,Midwest,5-4,4,active,true,true,standard,Lambeau Field,"Green Bay, WI",Green,#203731,Gold,#D3BC8D,Aquamarine,#55C5D8
23,CIN,Cincinnati,Shadow Katz,Eastern,Midwest,5-5,5,active,true,true,standard,Paycor Stadium,"Cincinnati, OH",Orange,#FF5F05,Black,#000000,Aquamarine,#55C5D8
24,IND,Indianapolis,Pit Vipers,Eastern,Midwest,,6,ambassador,false,true,lottery_1_8,Lucas Oil Stadium,"Indianapolis, IN",Racing Red,#D71920,Electric Lime,#C6FF00,Aquamarine,#55C5D8
47,MOS,Montreal,Storm,Eastern,Northeast,6-1,1,active,true,true,standard,Evergreen Credit Stadium,"Montreal, Canada",Frost Silver,#B8C5D1,Nordic Blue,#245A81,Emerald,#009B77
26,NEP,New England,Rebels,Eastern,Northeast,6-2,2,active,true,true,standard,Gillette Stadium,"Foxborough, MA",Navy,#002244,Red,#C8102E,Emerald,#009B77
46,TOR,Toronto,Sentinels,Eastern,Northeast,6-3,3,active,true,true,standard,GoldenRail Stadium,"Toronto, Canada",Crimson,#A6192E,Steel Gray,#78858B,Emerald,#009B77
12,PHI,Philadelphia,Harpies,Eastern,Northeast,6-4,4,active,true,true,standard,Lincoln Financial Field,"Philadelphia, PA",Midnight Green,#004C54,Silver,#B0B7BC,Emerald,#009B77
10,NYG,New York,Goliaths,Eastern,Northeast,6-5,5,active,true,true,standard,MetLife Stadium,"East Rutherford, NJ",Blue,#0B2265,Red,#C8102E,Emerald,#009B77
22,BUF,Buffalo,Brats,Eastern,Northeast,,6,ambassador,false,true,lottery_1_8,Highmark Stadium,"Orchard Park, NY",Royal Blue,#003DA5,Red,#C8102E,Emerald,#009B77
36,CAR,Carolina,Comets,Eastern,East,7-1,1,active,true,true,standard,Bank of America Stadium,"Charlotte, NC",Carolina Blue,#0085CA,Black,#101820,Turquoise,#26A9A0
2,DET,Detroit,Soul,Eastern,East,7-2,2,active,true,true,standard,Ford Field,"Detroit, MI",Honolulu Blue,#0076B6,Silver,#B0B7BC,Turquoise,#26A9A0
14,WAS,Washington,Commandos,Eastern,East,7-3,3,active,true,true,standard,Northwest Stadium,"Landover, MD",Burgundy,#773141,Gold,#D3BC8D,Turquoise,#26A9A0
28,PIT,Pittsburgh,Riviters,Eastern,East,7-4,4,active,true,true,standard,Acrisure Stadium,"Pittsburgh, PA",Black,#101820,Light Blue,#83C9EF,Turquoise,#26A9A0
15,CLE,Cleveland,Mud Dawgs,Eastern,East,7-5,5,active,true,true,standard,Huntington Bank Field,"Cleveland, OH",Brown,#311D00,Orange,#FF5F05,Turquoise,#26A9A0
32,BAL,Baltimore,Rooks,Eastern,East,,6,ambassador,false,true,lottery_1_8,M&T Bank Stadium,"Baltimore, MD",Purple,#4F2683,Black,#000000,Turquoise,#26A9A0
48,ALA,Alabama,Thunder,Eastern,Southeast,8-1,1,active,true,true,standard,Granite Stadium,"Birmingham, AL",Dark Thunder Blue,#102A43,Lightning Yellow,#F5D547,Alexandrite,#7040A0
37,JAX,Florida,Fangs,Eastern,Southeast,8-2,2,active,true,true,standard,EverBank Stadium,"Jacksonville, FL",Teal,#006778,Black,#101820,Alexandrite,#7040A0
25,MIA,Miami,Monsters,Eastern,Southeast,8-3,3,active,true,true,standard,Hard Rock Stadium,"Miami Gardens, FL",Aqua,#008E97,Orange,#FF5F05,Alexandrite,#7040A0
8,ATL,Atlanta,Mavericks,Eastern,Southeast,8-4,4,active,true,true,standard,Mercedes-Benz Stadium,"Atlanta, GA",Black,#000000,Red,#C8102E,Alexandrite,#7040A0
11,NOS,New Orleans,Mayfairs,Eastern,Southeast,8-5,5,active,true,true,standard,Caesars Superdome,"New Orleans, LA",Black,#000000,Old Gold,#9F8958,Alexandrite,#7040A0
13,TBB,Tampa Bay,Corsairs,Eastern,Southeast,,6,ambassador,false,true,lottery_1_8,Raymond James Stadium,"Tampa, FL",Red,#C8102E,Pewter,#5A5A5A,Alexandrite,#7040A0
```

## §6 SCHEDULE
Season 1, which is the bed template. Sorted week → game. 360 rows; weeks 1–14 have 20 games, weeks 15–19 have 16. Beds are derivable: `away_bed = bed_id(away)`, `home_bed = bed_id(home)`. Game 1 of each week is Saturday 17:30, the last game is Monday 17:30, all others on Sunday at 10:00, 13:00 or 17:30. Kickoff-slot points: Monday 17:30 = 5, Saturday 17:30 = 4, Sunday 17:30 = 3, Sunday 13:00 = 2, Sunday 10:00 = 1; each active club's 18 games total 33 to 38 points. Tour games carry no slot points. Every later season uses these same games, each bed-and-seed position filled by the club holding it that season (§3); dates come from that year's calendar.

```
week,game,date,kickoff,away,home
1,1,2000-07-01,17:30,Oklahoma City,St. Louis
1,2,2000-07-02,10:00,Las Vegas,New Orleans
1,3,2000-07-02,10:00,Arizona,Pittsburgh
1,4,2000-07-02,10:00,Kansas City,Philadelphia
1,5,2000-07-02,10:00,Seattle,Florida
1,6,2000-07-02,10:00,Mexico City,Chicago
1,7,2000-07-02,10:00,Dakota,Tennessee
1,8,2000-07-02,10:00,Calgary,Atlanta
1,9,2000-07-02,10:00,Denver,Cincinnati
1,10,2000-07-02,10:00,Omaha,New England
1,11,2000-07-02,10:00,Montana,Carolina
1,12,2000-07-02,13:00,Sacramento,Miami
1,13,2000-07-02,13:00,San Francisco,Alabama
1,14,2000-07-02,13:00,San Antonio,Green Bay
1,15,2000-07-02,13:00,San Diego,Detroit
1,16,2000-07-02,13:00,Houston,Montreal
1,17,2000-07-02,13:00,Utah,Washington
1,18,2000-07-02,17:30,New Mexico,New York
1,19,2000-07-02,17:30,Idaho,Cleveland
1,20,2000-07-03,17:30,Dallas,Toronto
2,1,2000-07-08,17:30,Chicago,Omaha
2,2,2000-07-09,10:00,Montreal,Oklahoma City
2,3,2000-07-09,10:00,New York,Denver
2,4,2000-07-09,10:00,Detroit,Seattle
2,5,2000-07-09,10:00,Washington,Sacramento
2,6,2000-07-09,10:00,Pittsburgh,Calgary
2,7,2000-07-09,10:00,Cleveland,Las Vegas
2,8,2000-07-09,10:00,Philadelphia,San Antonio
2,9,2000-07-09,10:00,Tennessee,Dallas
2,10,2000-07-09,10:00,New England,Mexico City
2,11,2000-07-09,10:00,Carolina,San Francisco
2,12,2000-07-09,13:00,Alabama,Montana
2,13,2000-07-09,13:00,Green Bay,Kansas City
2,14,2000-07-09,13:00,Florida,San Diego
2,15,2000-07-09,13:00,New Orleans,Idaho
2,16,2000-07-09,13:00,Miami,Utah
2,17,2000-07-09,13:00,Atlanta,Arizona
2,18,2000-07-09,17:30,St. Louis,Houston
2,19,2000-07-09,17:30,Toronto,Dakota
2,20,2000-07-10,17:30,Cincinnati,New Mexico
3,1,2000-07-15,17:30,Montana,St. Louis
3,2,2000-07-16,10:00,Washington,Dallas
3,3,2000-07-16,10:00,Carolina,Houston
3,4,2000-07-16,10:00,Arizona,Green Bay
3,5,2000-07-16,10:00,Seattle,New England
3,6,2000-07-16,10:00,San Francisco,Montreal
3,7,2000-07-16,10:00,New Orleans,Denver
3,8,2000-07-16,10:00,Florida,Mexico City
3,9,2000-07-16,10:00,Miami,Dakota
3,10,2000-07-16,10:00,Calgary,Philadelphia
3,11,2000-07-16,10:00,San Diego,Chicago
3,12,2000-07-16,13:00,Sacramento,Toronto
3,13,2000-07-16,13:00,Idaho,Cincinnati
3,14,2000-07-16,13:00,Alabama,Oklahoma City
3,15,2000-07-16,13:00,Las Vegas,New York
3,16,2000-07-16,13:00,Atlanta,San Antonio
3,17,2000-07-16,13:00,Cleveland,New Mexico
3,18,2000-07-16,17:30,Utah,Tennessee
3,19,2000-07-16,17:30,Pittsburgh,Kansas City
3,20,2000-07-17,17:30,Detroit,Omaha
4,1,2000-07-22,17:30,Chicago,Seattle
4,2,2000-07-23,10:00,New England,San Diego
4,3,2000-07-23,10:00,Tennessee,Sacramento
4,4,2000-07-23,10:00,San Antonio,Pittsburgh
4,5,2000-07-23,10:00,Cincinnati,Las Vegas
4,6,2000-07-23,10:00,Green Bay,Calgary
4,7,2000-07-23,10:00,Denver,Cleveland
4,8,2000-07-23,10:00,Omaha,Florida
4,9,2000-07-23,10:00,St. Louis,San Francisco
4,10,2000-07-23,10:00,Dakota,Washington
4,11,2000-07-23,10:00,Kansas City,Atlanta
4,12,2000-07-23,13:00,Toronto,Utah
4,13,2000-07-23,13:00,New York,Idaho
4,14,2000-07-23,13:00,Dallas,Miami
4,15,2000-07-23,13:00,New Mexico,New Orleans
4,16,2000-07-23,13:00,Philadelphia,Arizona
4,17,2000-07-23,13:00,Mexico City,Detroit
4,18,2000-07-23,17:30,Oklahoma City,Carolina
4,19,2000-07-23,17:30,Montreal,Montana
4,20,2000-07-24,17:30,Houston,Alabama
5,1,2000-08-05,17:30,Florida,New Orleans
5,2,2000-08-06,10:00,New York,Montreal
5,3,2000-08-06,10:00,San Francisco,Montana
5,4,2000-08-06,10:00,Kansas City,Omaha
5,5,2000-08-06,10:00,Toronto,Philadelphia
5,6,2000-08-06,10:00,Carolina,New England
5,7,2000-08-06,10:00,Atlanta,Washington
5,8,2000-08-06,10:00,Arizona,Idaho
5,9,2000-08-06,10:00,San Antonio,Las Vegas
5,10,2000-08-06,10:00,Dakota,Oklahoma City
5,11,2000-08-06,10:00,Tennessee,Detroit
5,12,2000-08-06,13:00,San Diego,Utah
5,13,2000-08-06,13:00,New Mexico,Houston
5,14,2000-08-06,13:00,Mexico City,Sacramento
5,15,2000-08-06,13:00,Alabama,Chicago
5,16,2000-08-06,13:00,St. Louis,Cincinnati
5,17,2000-08-06,13:00,Pittsburgh,Cleveland
5,18,2000-08-06,17:30,Seattle,Dallas
5,19,2000-08-06,17:30,Miami,Green Bay
5,20,2000-08-07,17:30,Calgary,Denver
6,1,2000-08-12,17:30,San Francisco,Mexico City
6,2,2000-08-13,10:00,Denver,Oklahoma City
6,3,2000-08-13,10:00,San Antonio,Utah
6,4,2000-08-13,10:00,Cincinnati,Tennessee
6,5,2000-08-13,10:00,Omaha,Dakota
6,6,2000-08-13,10:00,Las Vegas,Kansas City
6,7,2000-08-13,10:00,New Mexico,Idaho
6,8,2000-08-13,10:00,Alabama,Atlanta
6,9,2000-08-13,10:00,Miami,Pittsburgh
6,10,2000-08-13,10:00,New Orleans,Cleveland
6,11,2000-08-13,10:00,Montreal,St. Louis
6,12,2000-08-13,13:00,Montana,Houston
6,13,2000-08-13,13:00,Sacramento,Seattle
6,14,2000-08-13,13:00,Dallas,Calgary
6,15,2000-08-13,13:00,Philadelphia,New York
6,16,2000-08-13,13:00,New England,Toronto
6,17,2000-08-13,13:00,Green Bay,Chicago
6,18,2000-08-13,17:30,Arizona,San Diego
6,19,2000-08-13,17:30,Detroit,Washington
6,20,2000-08-14,17:30,Florida,Carolina
7,1,2000-08-19,17:30,Detroit,Miami
7,2,2000-08-20,10:00,Washington,Carolina
7,3,2000-08-20,10:00,Idaho,Montana
7,4,2000-08-20,10:00,Omaha,Denver
7,5,2000-08-20,10:00,Mexico City,New Mexico
7,6,2000-08-20,10:00,Dallas,San Antonio
7,7,2000-08-20,10:00,Dakota,Calgary
7,8,2000-08-20,10:00,Green Bay,Toronto
7,9,2000-08-20,10:00,Cleveland,Cincinnati
7,10,2000-08-20,10:00,St. Louis,Alabama
7,11,2000-08-20,10:00,Chicago,Montreal
7,12,2000-08-20,13:00,Oklahoma City,Seattle
7,13,2000-08-20,13:00,Utah,Arizona
7,14,2000-08-20,13:00,San Diego,Houston
7,15,2000-08-20,13:00,Tennessee,Florida
7,16,2000-08-20,13:00,Atlanta,New York
7,17,2000-08-20,13:00,Pittsburgh,New Orleans
7,18,2000-08-20,17:30,Las Vegas,San Francisco
7,19,2000-08-20,17:30,Philadelphia,New England
7,20,2000-08-21,17:30,Kansas City,Sacramento
8,1,2000-08-26,17:30,Denver,San Antonio
8,2,2000-08-27,10:00,Carolina,Pittsburgh
8,3,2000-08-27,10:00,Miami,Alabama
8,4,2000-08-27,10:00,Oklahoma City,Omaha
8,5,2000-08-27,10:00,New Mexico,Dallas
8,6,2000-08-27,10:00,Dakota,Mexico City
8,7,2000-08-27,10:00,Seattle,Utah
8,8,2000-08-27,10:00,New York,New Orleans
8,9,2000-08-27,10:00,Washington,Florida
8,10,2000-08-27,10:00,Cleveland,Atlanta
8,11,2000-08-27,10:00,Montana,San Diego
8,12,2000-08-27,13:00,Idaho,Kansas City
8,13,2000-08-27,13:00,Houston,San Francisco
8,14,2000-08-27,13:00,Arizona,Las Vegas
8,15,2000-08-27,13:00,Toronto,Detroit
8,16,2000-08-27,13:00,St. Louis,Green Bay
8,17,2000-08-27,13:00,Montreal,New England
8,18,2000-08-27,17:30,Calgary,Sacramento
8,19,2000-08-27,17:30,Cincinnati,Chicago
8,20,2000-08-28,17:30,Tennessee,Philadelphia
9,1,2000-09-02,17:30,Miami,Atlanta
9,2,2000-09-03,10:00,Utah,Omaha
9,3,2000-09-03,10:00,Dallas,Houston
9,4,2000-09-03,10:00,San Francisco,Arizona
9,5,2000-09-03,10:00,Las Vegas,Sacramento
9,6,2000-09-03,10:00,Kansas City,Denver
9,7,2000-09-03,10:00,Toronto,Montreal
9,8,2000-09-03,10:00,Green Bay,Cincinnati
9,9,2000-09-03,10:00,Detroit,Cleveland
9,10,2000-09-03,10:00,New England,New York
9,11,2000-09-03,10:00,Carolina,Alabama
9,12,2000-09-03,13:00,Idaho,San Antonio
9,13,2000-09-03,13:00,Seattle,Dakota
9,14,2000-09-03,13:00,Calgary,New Mexico
9,15,2000-09-03,13:00,Pittsburgh,Tennessee
9,16,2000-09-03,13:00,Chicago,Washington
9,17,2000-09-03,13:00,New Orleans,Philadelphia
9,18,2000-09-03,17:30,Mexico City,Montana
9,19,2000-09-03,17:30,Florida,St. Louis
9,20,2000-09-04,17:30,Oklahoma City,San Diego
10,1,2000-09-16,17:30,New Mexico,Arizona
10,2,2000-09-17,10:00,Pittsburgh,Detroit
10,3,2000-09-17,10:00,Cleveland,Carolina
10,4,2000-09-17,10:00,Idaho,Seattle
10,5,2000-09-17,10:00,Sacramento,San Francisco
10,6,2000-09-17,10:00,Chicago,Miami
10,7,2000-09-17,10:00,Florida,Toronto
10,8,2000-09-17,10:00,San Antonio,Dakota
10,9,2000-09-17,10:00,Denver,Las Vegas
10,10,2000-09-17,10:00,Philadelphia,Cincinnati
10,11,2000-09-17,10:00,Kansas City,Utah
10,12,2000-09-17,13:00,Mexico City,Oklahoma City
10,13,2000-09-17,13:00,Dallas,San Diego
10,14,2000-09-17,13:00,Montana,Calgary
10,15,2000-09-17,13:00,New York,Green Bay
10,16,2000-09-17,13:00,New England,Washington
10,17,2000-09-17,13:00,Tennessee,St. Louis
10,18,2000-09-17,17:30,Houston,Omaha
10,19,2000-09-17,17:30,Alabama,Montreal
10,20,2000-09-18,17:30,Atlanta,New Orleans
11,1,2000-09-23,17:30,New England,Tennessee
11,2,2000-09-24,10:00,Florida,Miami
11,3,2000-09-24,10:00,Arizona,Dallas
11,4,2000-09-24,10:00,Las Vegas,New Mexico
11,5,2000-09-24,10:00,Oklahoma City,Houston
11,6,2000-09-24,10:00,Montana,Utah
11,7,2000-09-24,10:00,Calgary,Seattle
11,8,2000-09-24,10:00,Washington,Philadelphia
11,9,2000-09-24,10:00,Green Bay,Cleveland
11,10,2000-09-24,10:00,New York,Pittsburgh
11,11,2000-09-24,10:00,Detroit,Montreal
11,12,2000-09-24,13:00,Omaha,San Francisco
11,13,2000-09-24,13:00,Dakota,Kansas City
11,14,2000-09-24,13:00,San Diego,Sacramento
11,15,2000-09-24,13:00,St. Louis,Carolina
11,16,2000-09-24,13:00,Toronto,Chicago
11,17,2000-09-24,13:00,New Orleans,Alabama
11,18,2000-09-24,17:30,Denver,Idaho
11,19,2000-09-24,17:30,Cincinnati,Atlanta
11,20,2000-09-25,17:30,San Antonio,Mexico City
12,1,2000-09-30,17:30,Las Vegas,Calgary
12,2,2000-10-01,10:00,Oklahoma City,Kansas City
12,3,2000-10-01,10:00,Denver,Dakota
12,4,2000-10-01,10:00,Mexico City,Dallas
12,5,2000-10-01,10:00,Utah,Idaho
12,6,2000-10-01,10:00,Pittsburgh,Toronto
12,7,2000-10-01,10:00,Miami,New England
12,8,2000-10-01,10:00,Detroit,St. Louis
12,9,2000-10-01,10:00,Carolina,Chicago
12,10,2000-10-01,10:00,New Orleans,Cincinnati
12,11,2000-10-01,10:00,San Francisco,San Diego
12,12,2000-10-01,13:00,Omaha,Montana
12,13,2000-10-01,13:00,Houston,Seattle
12,14,2000-10-01,13:00,Sacramento,Arizona
12,15,2000-10-01,13:00,Atlanta,Tennessee
12,16,2000-10-01,13:00,Alabama,Florida
12,17,2000-10-01,13:00,Montreal,Philadelphia
12,18,2000-10-01,17:30,San Antonio,New Mexico
12,19,2000-10-01,17:30,Washington,Green Bay
12,20,2000-10-02,17:30,Cleveland,New York
13,1,2000-10-07,17:30,Cincinnati,Pittsburgh
13,2,2000-10-08,10:00,San Diego,Las Vegas
13,3,2000-10-08,10:00,Houston,San Antonio
13,4,2000-10-08,10:00,Chicago,Tennessee
13,5,2000-10-08,10:00,New York,Toronto
13,6,2000-10-08,10:00,New Mexico,Denver
13,7,2000-10-08,10:00,Sacramento,Omaha
13,8,2000-10-08,10:00,Alabama,Detroit
13,9,2000-10-08,10:00,Montana,Oklahoma City
13,10,2000-10-08,10:00,St. Louis,New England
13,11,2000-10-08,10:00,Green Bay,New Orleans
13,12,2000-10-08,13:00,Utah,Mexico City
13,13,2000-10-08,13:00,Dallas,Kansas City
13,14,2000-10-08,13:00,Calgary,Idaho
13,15,2000-10-08,13:00,Montreal,Carolina
13,16,2000-10-08,13:00,Cleveland,Washington
13,17,2000-10-08,13:00,Atlanta,Florida
13,18,2000-10-08,17:30,Seattle,San Francisco
13,19,2000-10-08,17:30,Philadelphia,Miami
13,20,2000-10-09,17:30,Dakota,Arizona
14,1,2000-10-14,17:30,Omaha,Dallas
14,2,2000-10-15,10:00,Kansas City,New Mexico
14,3,2000-10-15,10:00,Seattle,Montana
14,4,2000-10-15,10:00,Utah,Calgary
14,5,2000-10-15,10:00,Houston,Mexico City
14,6,2000-10-15,10:00,Chicago,St. Louis
14,7,2000-10-15,10:00,Tennessee,Green Bay
14,8,2000-10-15,10:00,Carolina,Detroit
14,9,2000-10-15,10:00,San Diego,Dakota
14,10,2000-10-15,10:00,Philadelphia,Cleveland
14,11,2000-10-15,10:00,Montreal,Florida
14,12,2000-10-15,13:00,Idaho,Las Vegas
14,13,2000-10-15,13:00,San Francisco,Oklahoma City
14,14,2000-10-15,13:00,Arizona,Denver
14,15,2000-10-15,13:00,Cincinnati,New York
14,16,2000-10-15,13:00,Toronto,Atlanta
14,17,2000-10-15,13:00,New Orleans,Miami
14,18,2000-10-15,17:30,Sacramento,San Antonio
14,19,2000-10-15,17:30,New England,Alabama
14,20,2000-10-16,17:30,Washington,Pittsburgh
15,1,2000-10-28,17:30,Philadelphia,Montreal
15,2,2000-10-29,10:00,Pittsburgh,Carolina
15,3,2000-10-29,10:00,Atlanta,Alabama
15,4,2000-10-29,10:00,New York,New England
15,5,2000-10-29,10:00,Kansas City,Oklahoma City
15,6,2000-10-29,10:00,New Mexico,Mexico City
15,7,2000-10-29,10:00,Arizona,San Francisco
15,8,2000-10-29,13:00,Calgary,Montana
15,9,2000-10-29,13:00,Denver,Omaha
15,10,2000-10-29,13:00,San Antonio,Houston
15,11,2000-10-29,13:00,Chicago,Cincinnati
15,12,2000-10-29,13:00,Green Bay,St. Louis
15,13,2000-10-29,13:00,Cleveland,Detroit
15,14,2000-10-29,17:30,Las Vegas,San Diego
15,15,2000-10-29,17:30,New Orleans,Florida
15,16,2000-10-30,17:30,Seattle,Idaho
16,1,2000-11-04,17:30,Dakota,Denver
16,2,2000-11-05,10:00,Washington,Cleveland
16,3,2000-11-05,10:00,Florida,Atlanta
16,4,2000-11-05,10:00,Miami,New Orleans
16,5,2000-11-05,10:00,Seattle,Calgary
16,6,2000-11-05,10:00,Idaho,Utah
16,7,2000-11-05,10:00,San Diego,Arizona
16,8,2000-11-05,13:00,Sacramento,Las Vegas
16,9,2000-11-05,13:00,Mexico City,San Antonio
16,10,2000-11-05,13:00,Dallas,New Mexico
16,11,2000-11-05,13:00,Tennessee,Cincinnati
16,12,2000-11-05,13:00,New England,Philadelphia
16,13,2000-11-05,13:00,Detroit,Pittsburgh
16,14,2000-11-05,17:30,Omaha,Kansas City
16,15,2000-11-05,17:30,Toronto,New York
16,16,2000-11-06,17:30,Chicago,Green Bay
17,1,2000-11-11,17:30,Carolina,Washington
17,2,2000-11-12,10:00,New Orleans,Atlanta
17,3,2000-11-12,10:00,St. Louis,Tennessee
17,4,2000-11-12,10:00,New York,Philadelphia
17,5,2000-11-12,10:00,Houston,Dallas
17,6,2000-11-12,10:00,Idaho,Calgary
17,7,2000-11-12,10:00,New Mexico,San Antonio
17,8,2000-11-12,13:00,Las Vegas,Arizona
17,9,2000-11-12,13:00,San Francisco,Sacramento
17,10,2000-11-12,13:00,Denver,Kansas City
17,11,2000-11-12,13:00,Cincinnati,Green Bay
17,12,2000-11-12,13:00,Montreal,Toronto
17,13,2000-11-12,13:00,Alabama,Miami
17,14,2000-11-12,17:30,Oklahoma City,Dakota
17,15,2000-11-12,17:30,Cleveland,Pittsburgh
17,16,2000-11-13,17:30,Utah,Montana
18,1,2000-11-18,17:30,Kansas City,Dakota
18,2,2000-11-19,10:00,Green Bay,Tennessee
18,3,2000-11-19,10:00,Florida,Alabama
18,4,2000-11-19,10:00,Philadelphia,Toronto
18,5,2000-11-19,10:00,Mexico City,Houston
18,6,2000-11-19,10:00,San Antonio,Dallas
18,7,2000-11-19,10:00,Arizona,Sacramento
18,8,2000-11-19,13:00,Montana,Seattle
18,9,2000-11-19,13:00,San Diego,San Francisco
18,10,2000-11-19,13:00,Omaha,Oklahoma City
18,11,2000-11-19,13:00,Atlanta,Miami
18,12,2000-11-19,13:00,St. Louis,Chicago
18,13,2000-11-19,13:00,Pittsburgh,Washington
18,14,2000-11-19,17:30,Calgary,Utah
18,15,2000-11-19,17:30,Detroit,Carolina
18,16,2000-11-20,17:30,New England,Montreal
19,1,2000-11-25,17:30,Carolina,Cleveland
19,2,2000-11-26,10:00,Miami,Florida
19,3,2000-11-26,10:00,Washington,Detroit
19,4,2000-11-26,10:00,Cincinnati,St. Louis
19,5,2000-11-26,10:00,Sacramento,San Diego
19,6,2000-11-26,10:00,Oklahoma City,Denver
19,7,2000-11-26,10:00,Montana,Idaho
19,8,2000-11-26,13:00,Utah,Seattle
19,9,2000-11-26,13:00,Dakota,Omaha
19,10,2000-11-26,13:00,Houston,New Mexico
19,11,2000-11-26,13:00,Montreal,New York
19,12,2000-11-26,13:00,Toronto,New England
19,13,2000-11-26,13:00,Alabama,New Orleans
19,14,2000-11-26,17:30,Dallas,Mexico City
19,15,2000-11-26,17:30,Tennessee,Chicago
19,16,2000-11-27,17:30,San Francisco,Las Vegas
```

Composition per club: 18 games, 9H/9A; 8 divisional (4H/4A, each rival twice); 4 interconference, equal-seed, one per opposing division; 6 same-conference non-division; one bye.

## §7 Ambassador Tour and Ambassador Bowl
- The Tour is a single round robin: 7 rounds, 28 games, among the 8 ambassadors, on calendar weeks 1, 4, 8, 11, 15, 18, 22. Consecutive rounds are 21 or 28 days apart.
- Each Tour round is played at one League-assigned international venue: all four games of the round are played there over the round's weekend (game 1 Saturday 17:30, games 2 and 3 Sunday 10:00 and 13:00, game 4 Monday 17:30). The Bowl has its own venue. No venue is any club's home ground.
- Eight venues are drawn per season from `INTL_VENUES` under the season seed, one per Tour round plus one for the Bowl, from a single pool with no reserved tier and with no venue reused within a season. The draw is recorded in the year-end handoff.
- Each game carries a template-assigned nominal home tag, balanced to 3–4 per club, with no club nominally home, and none nominally away, in three consecutive rounds. (Never home in consecutive rounds is unsatisfiable for eight clubs in a single round robin; this template has six repeats in total, the minimum possible.) The nominal home kicks the first half.
- The Bowl is the Sunday immediately preceding the Coronation, at noon league time, between Western and Eastern Ambassador All-Stars.
- Each conference's best Tour club supplies its HC and all three coordinators. All eight ambassador HCs vote after the Tour.
- All-Star selection uses per-position ranked ballots, all candidates ranked, Droop quota `floor(8 / (places + 1)) + 1`, seeded ties. 53 places per conference, drawn from that conference's four clubs.
- Rosters lock one simulated hour before kickoff. A shortfall is a validation failure repaired before play.
- Winners' final 53 present at kickoff plus the four Bowl coaches receive Tour Rings.

```
tour_round,calendar_week,date,kickoff,game,away_nominal,home_nominal,venue_slot
1,1,2000-07-01,17:30,1,Tampa Bay,Portland,TR-1
1,1,2000-07-02,10:00,2,Baja,Indianapolis,TR-1
1,1,2000-07-02,13:00,3,Los Angeles,Baltimore,TR-1
1,1,2000-07-03,17:30,4,Minnesota,Buffalo,TR-1
2,4,2000-07-22,17:30,1,Portland,Baja,TR-2
2,4,2000-07-23,10:00,2,Baltimore,Tampa Bay,TR-2
2,4,2000-07-23,13:00,3,Indianapolis,Minnesota,TR-2
2,4,2000-07-24,17:30,4,Buffalo,Los Angeles,TR-2
3,8,2000-08-19,17:30,1,Baltimore,Portland,TR-3
3,8,2000-08-20,10:00,2,Minnesota,Baja,TR-3
3,8,2000-08-20,13:00,3,Tampa Bay,Buffalo,TR-3
3,8,2000-08-21,17:30,4,Los Angeles,Indianapolis,TR-3
4,11,2000-09-09,17:30,1,Portland,Minnesota,TR-4
4,11,2000-09-10,10:00,2,Buffalo,Baltimore,TR-4
4,11,2000-09-10,13:00,3,Baja,Los Angeles,TR-4
4,11,2000-09-11,17:30,4,Indianapolis,Tampa Bay,TR-4
5,15,2000-10-07,17:30,1,Buffalo,Portland,TR-5
5,15,2000-10-08,10:00,2,Los Angeles,Minnesota,TR-5
5,15,2000-10-08,13:00,3,Baltimore,Indianapolis,TR-5
5,15,2000-10-09,17:30,4,Tampa Bay,Baja,TR-5
6,18,2000-10-28,17:30,1,Portland,Los Angeles,TR-6
6,18,2000-10-29,10:00,2,Indianapolis,Buffalo,TR-6
6,18,2000-10-29,13:00,3,Minnesota,Tampa Bay,TR-6
6,18,2000-10-30,17:30,4,Baja,Baltimore,TR-6
7,22,2000-11-25,17:30,1,Indianapolis,Portland,TR-7
7,22,2000-11-26,10:00,2,Los Angeles,Tampa Bay,TR-7
7,22,2000-11-26,13:00,3,Buffalo,Baja,TR-7
7,22,2000-11-27,17:30,4,Baltimore,Minnesota,TR-7
```

## §8 INTL_VENUES
50 venues in five regions. A season draws 8 (§7), so the pool minimum is 8. No city may equal a club `team_city`. Several cities host more than one venue, so `city` is not unique. `suitability` is `proven` for the seven venues named as previous NFL regular-season hosts and blank where unclassified; it plays no part in the draw. Capacities are approximate nominal figures, not guaranteed seating for a game. `lat` and `lon` are approximate (about 100 m) and `timezone` is an IANA zone; both are unverified working values.

```
venue_id,city,country,venue_name,capacity,region,suitability,lat,lon,timezone,active
INTL-001,London,England,Wembley Stadium,90000,Europe,proven,51.556,-0.280,Europe/London,true
INTL-002,London,England,Tottenham Hotspur Stadium,62850,Europe,proven,51.604,-0.066,Europe/London,true
INTL-003,London,England,Twickenham Stadium,82000,Europe,proven,51.456,-0.342,Europe/London,true
INTL-004,London,England,London Stadium,62500,Europe,,51.539,-0.017,Europe/London,true
INTL-005,Manchester,England,Old Trafford,74000,Europe,,53.463,-2.291,Europe/London,true
INTL-006,London,England,Emirates Stadium,60700,Europe,,51.555,-0.108,Europe/London,true
INTL-007,Cardiff,Wales,Principality Stadium,74500,Europe,,51.478,-3.183,Europe/London,true
INTL-008,Edinburgh,Scotland,Murrayfield Stadium,67100,Europe,,55.942,-3.241,Europe/London,true
INTL-009,Dublin,Ireland,Croke Park,82300,Europe,,53.361,-6.251,Europe/Dublin,true
INTL-010,Dublin,Ireland,Aviva Stadium,51700,Europe,,53.335,-6.229,Europe/Dublin,true
INTL-011,Saint-Denis,France,Stade de France,80700,Europe,,48.925,2.360,Europe/Paris,true
INTL-012,Munich,Germany,Allianz Arena,75000,Europe,proven,48.219,11.625,Europe/Berlin,true
INTL-013,Berlin,Germany,Olympiastadion,74200,Europe,,52.515,13.240,Europe/Berlin,true
INTL-014,Frankfurt,Germany,Deutsche Bank Park,58000,Europe,proven,50.069,8.645,Europe/Berlin,true
INTL-015,Dortmund,Germany,Signal Iduna Park,81300,Europe,,51.493,7.452,Europe/Berlin,true
INTL-016,Madrid,Spain,Santiago Bernabéu,78000,Europe,proven,40.453,-3.688,Europe/Madrid,true
INTL-017,Madrid,Spain,Metropolitano Stadium,70000,Europe,,40.436,-3.600,Europe/Madrid,true
INTL-018,Milan,Italy,San Siro,75800,Europe,,45.478,9.124,Europe/Rome,true
INTL-019,Rome,Italy,Stadio Olimpico,70600,Europe,,41.934,12.455,Europe/Rome,true
INTL-020,Lisbon,Portugal,Estádio da Luz,68100,Europe,,38.753,-9.185,Europe/Lisbon,true
INTL-021,Amsterdam,Netherlands,Johan Cruyff Arena,55900,Europe,,52.314,4.942,Europe/Amsterdam,true
INTL-022,Gothenburg,Sweden,Ullevi Stadium,43000,Europe,,57.706,11.987,Europe/Stockholm,true
INTL-023,Tokyo,Japan,Japan National Stadium,67700,Asia,,35.678,139.715,Asia/Tokyo,true
INTL-024,Yokohama,Japan,Nissan Stadium,72300,Asia,,35.510,139.606,Asia/Tokyo,true
INTL-025,Saitama,Japan,Saitama Stadium 2002,63700,Asia,,35.803,139.718,Asia/Tokyo,true
INTL-026,Seoul,South Korea,Seoul World Cup Stadium,66700,Asia,,37.568,126.897,Asia/Seoul,true
INTL-027,Seoul,South Korea,Seoul Olympic Stadium,69900,Asia,,37.516,127.072,Asia/Seoul,true
INTL-028,Kuala Lumpur,Malaysia,Bukit Jalil National Stadium,87400,Asia,,3.055,101.692,Asia/Kuala_Lumpur,true
INTL-029,Jakarta,Indonesia,Gelora Bung Karno Stadium,77200,Asia,,-6.219,106.802,Asia/Jakarta,true
INTL-030,Lusail,Qatar,Lusail Stadium,88900,Asia,,25.421,51.491,Asia/Qatar,true
INTL-031,Kolkata,India,Salt Lake Stadium,85000,Asia,,22.570,88.410,Asia/Kolkata,true
INTL-032,Ahmedabad,India,Narendra Modi Stadium,132000,Asia,,23.092,72.597,Asia/Kolkata,true
INTL-033,Johannesburg,South Africa,FNB Stadium,94700,Africa,,-26.235,27.982,Africa/Johannesburg,true
INTL-034,Cape Town,South Africa,Cape Town Stadium,58300,Africa,,-33.903,18.411,Africa/Johannesburg,true
INTL-035,Durban,South Africa,Moses Mabhida Stadium,55000,Africa,,-29.829,31.030,Africa/Johannesburg,true
INTL-036,Johannesburg,South Africa,Ellis Park Stadium,62500,Africa,,-26.198,28.060,Africa/Johannesburg,true
INTL-037,Cairo,Egypt,Cairo International Stadium,75000,Africa,,30.069,31.315,Africa/Cairo,true
INTL-038,Casablanca,Morocco,Stade Mohammed V,45000,Africa,,33.578,-7.643,Africa/Casablanca,true
INTL-039,Rio de Janeiro,Brazil,Maracanã Stadium,78800,South America,,-22.912,-43.230,America/Sao_Paulo,true
INTL-040,São Paulo,Brazil,Neo Química Arena,49200,South America,proven,-23.545,-46.474,America/Sao_Paulo,true
INTL-041,São Paulo,Brazil,MorumBIS Stadium,66800,South America,,-23.600,-46.720,America/Sao_Paulo,true
INTL-042,Belo Horizonte,Brazil,Mineirão Stadium,61900,South America,,-19.866,-43.971,America/Sao_Paulo,true
INTL-043,Buenos Aires,Argentina,Estadio Monumental,85000,South America,,-34.545,-58.450,America/Argentina/Buenos_Aires,true
INTL-044,Montevideo,Uruguay,Estadio Centenario,60200,South America,,-34.895,-56.153,America/Montevideo,true
INTL-045,Santiago,Chile,Estadio Nacional,48700,South America,,-33.464,-70.610,America/Santiago,true
INTL-046,Melbourne,Australia,Melbourne Cricket Ground,100000,Oceania,,-37.820,144.983,Australia/Melbourne,true
INTL-047,Sydney,Australia,Stadium Australia,83500,Oceania,,-33.847,151.063,Australia/Sydney,true
INTL-048,Perth,Australia,Optus Stadium,61200,Oceania,,-31.951,115.889,Australia/Perth,true
INTL-049,Adelaide,Australia,Adelaide Oval,53600,Oceania,,-34.916,138.596,Australia/Adelaide,true
INTL-050,Auckland,New Zealand,Eden Park,50000,Oceania,,-36.875,174.745,Pacific/Auckland,true
```

## §9 Colour system
- `sprite_key = primary_hex + secondary_hex` and must be unique across all 48 clubs. This is the rendering key; primary alone is not unique.
- `tertiary_hex` is the division gemstone and is shared by all six clubs in a division.
- Sacramento `#EFFF00` is high-luminance and needs a dark sprite stroke. Montreal, Utah are light — verify contrast.
- White is reserved for away-uniform variations, never a primary/secondary/tertiary slot.
- Gemstone hexes other than Alexandrite are working values and may be refined without changing assignments.

```
scope,name,hex,role
League brand,Diamond,#B9F2FF,Primary league gemstone
League brand,Amethyst,#9966CC,Secondary league gemstone
Western Conference,Sapphire,#0F52BA,Conference gemstone
Eastern Conference,Ruby,#E0115F,Conference gemstone
Western/Northwest,Topaz,#E6A32E,Division gemstone
Western/Southwest,Peridot,#A5C83B,Division gemstone
Western/Central,Tourmaline,#D94B89,Division gemstone
Western/Southern,Garnet,#9A1B36,Division gemstone
Eastern/Midwest,Aquamarine,#55C5D8,Division gemstone
Eastern/Northeast,Emerald,#009B77,Division gemstone
Eastern/East,Turquoise,#26A9A0,Division gemstone
Eastern/Southeast,Alexandrite,#7040A0,Division gemstone
```

---

## §10 Game rules
- The game uses an explicit DFL rulebook, not a live external ruleset.
- Four 15-minute quarters. 40-second play clock, 25 seconds after administrative stoppages. Three timeouts per half, not banked.
- Clock stops on incompletions, out-of-bounds, scores, penalty administration, timeouts and possession changes. First downs briefly stop the clock to reset the chains.
- Home kicks to start the first half, away the second. No coin toss. In overtime the away side kicks first, alternating each period.
- Kickoffs from the 35, touchbacks to the 25. After a safety the scoring side receives a free kick from the opponent's 20.
- Scoring: touchdown 6, field goal 3, safety 2, PAT 1, two-point tries always available.
- Overtime, regular season and Tour: one 10-minute period; both teams receive a possession even after an opening touchdown, except that a defensive score ends it; then sudden death. Time is extended only to finish the required answering possession.
- Postseason overtime: successive 15-minute periods under the same possession guarantee, two timeouts reset each period.
- Challenges: two per team, a third earned if the first is upheld. An unsuccessful challenge consumes a timeout; no timeout, no challenge.
- Two unsportsmanlike penalties eject a player.
- Penalties: false start/offside 5; delay of game 5; offensive holding 10; defensive holding 5 plus automatic first down; illegal contact 5 plus automatic first down; defensive pass interference spot plus automatic first down; offensive pass interference 10; personal foul and unsportsmanlike 15.
- Formations, route landmarks, blocking rules, coverage responsibilities, reads and reactions come from the league concept vocabulary. A coach assembles a playbook; a weekly game plan selects a subset against perceived opposition.
- Familiarity belongs to assignments and teammates and develops through repetition. Designated players may check before the snap, with limited reach and delay.
- Fixed physics timestep 0.05 s. Decision checks are event-driven with a maximum 0.10 s interval.
- At neutral sites the nominal home is assigned by the fixture template, or by higher seed with a seeded tie.
- Uncommon penalties may be implemented partially; gaps are recorded in an implementation coverage register.

## §11 Positions, rosters, contracts, cap
- Normal roster 53 plus practice squad 16, at most 6 of whom may be veterans with more than 2 credited seasons (NFL default). Preseason ceiling 90 including practice squad (the NFL's number). Required places 48; Bowl places 53 (5 discretionary).
- Positional minimums (the 48 required places): K 1; P 1; QB 3; HB 3, or HB 2 and FB 1; WR 5; TE 2; LT 2, LG 2, C 1, RG 2, RT 2; LE 2, RE 2, DT 3; ROLB 2, MLB 3, LOLB 2; CB 5; FS 2, SS 2; LS 1. Coverage is checked at the cut and on voluntary packages.
- A club may operate temporarily short while seeking affordable replacements, but must be able to field 11 medically available legal players or forfeit.
- **Emergency fill-ins (NFL defaults).** Game-day active roster 48, or 47 with fewer than 8 offensive linemen. Between games a club may place a player on injured reserve (out at least four games; at most eight players per club per season may be designated to return), elevate up to two practice-squad players for a game (each player at most three times a season; an elevated player reverts to the practice squad afterward), sign a free agent at no less than the minimum salary, or claim a waived player, each subject to the viability invariant. Nothing can be signed during a game. If no medically available player remains at a position during a game, the club's emergency list for that position supplies a dressed player, who plays out of position. Before every kickoff each club files an emergency list with at least one name for every position, including K, P, LS and QB; the engine applies its out-of-position penalty.
- Injured reserve and suspension supply no cap relief.
- **No banking.** Unused cap room does not carry over; each league year starts from the same cap.
- Cap $1,000, emergency reserve $25, minimum salary $5, comp qualifying threshold $75.
- **No salary floor.** A club may spend as little as it likes; unused room is lost (no banking). Chronic underspending is answered by fan sentiment and the recall ballot (§14), not by a payment rule.
- **Cap timing (adapted from the NFL's top-51 rule).** The cap is enforced all year. From the start of the league year until the final cut to 53 and 16, a club's cap number is dead money plus its highest cap hits, counting only the §0 number of contracts; the extra camp bodies do not count. From the final cut until the league year closes every contract counts. A signing, claim, trade or restructure that leaves a club over the cap and raises its cap number is rejected. A release is never blocked by the cap (its accelerated bonus may carry a club over; it then cannot add cap until it is back under). At the final cut every club must be at or under the cap, a cut that leaves a club over is refused, and the season cannot begin with any club over. The viability invariant binds at all times, which is what keeps the final cut reachable.
- Practice-squad salaries count against the cap like any other salary.
- Dead money counts against the cap. Release or trade immediately accelerates every uncharged bonus onto the original club's current cap.
- **Viability invariant.** For every club-year: `dead money + required-minimum roster cost (48 × $5 = $240) + reserve ≤ cap`, so **dead money ≤ $735**. Checked before every signing, release, trade and restructure, for the current and every committed future year. "Dead money" here is the strict reading: everything the club would owe in that year if every contract were released at the start of it (guarantees, earned base, every uncharged bonus accelerated, and dead charges already booked). In the current year the replacement cost is pro-rated to the paid weeks still to come. A move is rejected if it leaves a year over the limit and raises that year's figure; time alone (earned salary accruing) does not freeze a club.
- **Transactions.** A move is rejected if it breaks a roster rule, the viability invariant, or (from the final cut) the cap as stated under Cap timing. Nothing else rejects a move: no league vote and no fairness veto.
- **Restructure.** Converts part of a current or future year's base salary into a bonus prorated evenly over the contract's remaining years (at most five). Base salary may not fall below the minimum salary. It is a transaction like any other and is subject to the viability invariant and the cap timing rule.
- **Split release.** Once per club per league year, one release's uncharged bonus accelerates 50/50 across the current and next year instead of in full.
- **Viability margin** = cap − dead money − minimum-roster cost − reserve, logged per club-year; flag below $50.
- Contract shapes: steady, rising or declining. At most five covered years and five bonus proration years. Bonus is paid on signing and cap-prorated evenly. The receiving club assumes unpaid base and attached guarantees, never the bonus.
- Removed: incentives, roster bonuses, June 1 designation, franchise tag, restricted and exclusive-rights free agency, no-trade clauses, conditional compensation, cap-space trades.
- Rookie reserve (NFL rookie-pool principle): until a draft pick is made, the pick's rookie-scale cap hit (rounds 8 and 9 at the round-7 scale, $5) counts in the owning club's cap number for the year of that draft, so a club always keeps room to sign its class; the reserve moves with a traded pick and is replaced by the real contract when the pick is made. It adds no committed money, because rookie contracts carry no guarantees.
- Rookies accept selection and sign four fixed years at $25/$18/$14/$11/$9/$7/$5. First-round option salary $35. Within-round slot rank breaks otherwise equal terms.
- **Jersey numbers (Editions 8.14, 8.15).** A number tells a viewer the position. Primary blocks: 1-9 K, P, LS · 10-14 QB · 15-19 TE · 20-29 CB · 30-39 HB, FB · 40-49 FS, SS · 50-59 ROLB, MLB, LOLB · 60-79 LT, LG, C, RG, RT · 80-89 WR · 90-99 LE, RE, DT. Zero and double zero are not used. A number is unique among everyone on a club's roster, practice squad and injured reserve.
  - *Overflow*, only when the primary block is full: WR may use open numbers 15-19, TE open 80-89, LB open 40-49, DL open 70-79. A player in her primary block has priority: an overflow holder is reassigned when a primary player needs her number.
  - *Camp*: before the final cut any open number from 1 to 99 is legal. At the final cut anyone outside her primary and overflow blocks is reassigned.
  - Capacity check: a normal 53 plus 16 needs at most 10 numbers in any block, and WR and TE together fit comfortably with the overflow.
  - *Stewardship*: a number is assigned to a player when she joins a club (signing, draft, waiver claim, trade) and is hers for as long as she is with that club, including through the club's exile and its return. It is freed when she leaves. A traded or claimed player keeps her number if it is free at the new club and legal, otherwise she is given another. A player who leaves and later signs again with the same club takes her old number back if it is free (builder's default). A new arrival takes the lowest open number in her primary block.
  - *No retired numbers*: a freed number is open to anyone at once.
- Extensions require two completed seasons and mutual consent.
- Salary prorates over the 22 paid regular-season weeks, same denominator for active and ambassador clubs. Midseason deals pay remaining weeks only and expire at year close. Postseason-only replacements pay one salary-week equivalent.
- Released players enter one simulated-day waiver period, except a player with the §0 number of credited seasons or more, who goes straight to free agency (NFL vested-veteran rule); her guarantees and bonus stay with the releasing club. Priority is last season's draft order until four league weeks finish, then weekly from current standings, with Tour standings in their prior draft bands.
- Future-year minimum-salary reservations are feasibility checks, not charges.

## §12 Draft, trades, waivers
- Seven rounds, 48 ordinary selections per round, 336 total.
- Order: non-playoff clubs, then clubs by elimination round, then runner-up and champion; worse regular-season record first, relevant tiebreakers reversed.
- Returning ambassadors receive first-round slots 1–8 by an equal-chance seeded lottery; the lottery identity follows the original pick through trades and supplies their later-round order too.
- Compensatory picks follow round seven, at most two per club and 48 league-wide. Qualifying movements require a first-year base ≥ $75 on the contract signed, and, for a departure, a top-quarter snap share at the old club. A club's qualifying signings cancel its qualifying departures, highest value first (NFL netting, so a club cannot churn free agents for picks); each unmatched departure earns a pick, best value first, ties to the club picking earlier. Compensation is for free-agent losses only; parity is the job of draft order, the lottery and place-based scheduling (see §20).
- Trade assets are named players and owned picks in the next three not-yet-completed drafts.
- Trading opens at the global close of leadership hiring. In competition, finalise only Tuesday 00:01 through Friday 23:59 league time, ending Friday 23:59 before the first game of numbered week 9.
- No league vote, waiting period or subjective fairness veto. A trade that leaves either club over the cap and raises its cap number is rejected.

## §13 Characters, AI and memory
- All characters are female. Every character has a durable ID and an immutable creation portrait.
- Hidden ability is never exposed to hiring actors, voters, fan expectations or narration.
- Scouting and analytics estimate from evidence with position-specific biases. Official position is permanent.
- Starting budget 32 detailed memories per character; recent and defining events stay detailed, routine events merge into relationship summaries.
- Relationships are sparse and directed, recording only actual contacts.
- Draft classes contain 672 players, at least 12 per position. Pools hold at least 8 medically available unsigned players per position and 4 willing candidates per leadership role. A character may stay in a pool (free agents, leadership candidates) at most four consecutive years: if she is still unsigned at the fourth league-year opening after she entered it, she retires permanently and is removed from the list (her career record is archived by the people layer). Being hired resets the clock to zero, and a player released later starts a new count. A drafted player who goes unsigned enters the pool when the draft ends. This bounds database size.
- **Character card (Edition 8.13).** Every player and every club leader (CEO, GM, head coach, offensive, defensive and special-teams coordinators) has one card, made by the character creator, a standalone module: seed and request in, complete card out. The ledger and the engine read cards and never generate characters.
  - *Identity* is fixed at creation: durable ID, role, name, birth year, official position, creation height and weight, and the creation look (a small set of appearance choices, not an image, so the portrait can be redrawn identically). Age is derived from the birth year. Names are unique among listed characters and reusable after a character leaves the list.
  - *Hidden state*: true attributes on a 1-99 scale, a personal growth profile (peak age, remaining ceiling, volatility), and six traits on 0-100 (ambition, loyalty, work ethic, temperament, greed, leadership). The viewer, hiring actors, voters and narration see only the public view: name, role, position, age, body and look. There is no stored overall rating; any overall is derived from the hidden attributes and is for the engine only.
  - *Attributes* are data (`data/characters/`). Players have eight core attributes (speed, acceleration, strength, agility, stamina, durability, awareness, composure) plus two or three skills of their position group; leaders have three role attributes. Attributes within a card correlate through one shared talent factor.
  - *Creation*: each card is drawn from its own random stream seeded by the world seed and its ID, so it does not depend on how many cards were made before it. Position is chosen first and attributes and body are drawn conditional on it, which keeps class quotas exact.
  - *Draft class*: 672 prospects aged 21 to 23, at least 12 at every position, remaining places shared by how many of each position a club carries. Each class has its own strength shift (some classes are weak, some strong); this is a fairness parameter, set in data.
  - *Founding league*: each club gets a 53, a 16-player practice squad, six leaders and the pool gets at least 8 per position; veterans are spread over ages 21 to 34 with talent shifted up (survivors). All players are adults.
  - Slots for the 32 detailed memories and the sparse directed relationship map exist on the card and are filled by the people layer.
- Retirement takes effect at offseason opening. Development and aging curves are data, not narrative.

## §14 Fans, governance, expectations
- Each club has a fan card representing one million fans. Three viewpoints are seeded between 20 and 50 percent and normalised.
- Sentiment returns gradually toward 50. After a club's last game, sentiment at or below 20 triggers a recall ballot; a fan majority removes the CEO. No midseason automatic dismissal.
- CEO elections occur every three seasons and after recall or vacancy. Elected successors take office in the next offseason.
- Leadership dismissals are permitted only after a club's last game. Poaching is allowed only inside the leadership window, with candidate consent, at most three hiring passes, one appointment per candidate per window.
- No club may start a season short of its six designated leaders; fallback appointments come from pre-consenting reserves and close before the window ends.
- After the cut to 53, evaluate all fixtures together using public production only. Save unrounded expected wins and postseason probabilities; opposing expected wins and ties must reconcile across games.
- Franchise attitudes are narrative-only: 2,256 directional relationships, defaults 50 within division, 20 same-conference, 5 opposite; heat half-life two league weeks; at most 2 points per event and 10 per season; maximum 30 accumulated heat. Rendered favour is attitude minus heat.

## §15 Honours, analytics, legacy
- 59 halls (1 league, 2 conference, 8 division, 48 club). Each has an All-Time All-22 (offence 11, defence 11) plus K, P and LS specialist registers.
- Eligibility requires a Tiara and five credited seasons. A season with at least one active or Tour roster appearance counts once.
- All-Time membership is replaceable; ever-membership and the resulting Hall entry are permanent within scope. Equal or better displaces the holder; ties break against the incumbent, and between challengers by seeded draw.
- Tiara recipients are the winning club's final 53 plus its CEO, GM, HC and three coordinators. At most one Tiara per season per person.
- One league-wide fan Hall entrant per year, chosen by single-winner ranked choice from Tiara-holding eligible runners-up, seeded ties.
- Analytical score: 60 percent role execution, 25 percent opponent-adjusted difficulty, 15 percent leverage, on era-standardised production.
- Records store person or club ID, event links, season, scope, unit and opportunity count.

## §16 Narration and viewer surface
- Six top-level entries: Home, Teams, Players, Schedule, Standings, Statistics.
- The viewer observes only; no control and no hidden data.
- Returning viewers receive a public-event digest since their last seen event. Reloading a page creates no new narrative state.
- Media is a mouth, never a causal actor. Private rumour chains are not simulated.

## §17 Persistence and determinism
- One authoritative simulation process. Every event carries an ID, simulated time, deterministic ordering key, rule version, domain-specific random key, participants, visibility and resulting state changes.
- Reserve/commit ensures money, picks, roster places and contracts cannot be assigned twice.
- Full-rate traces record positions, ball state, intent references and decisive events at 0.05 s with lossless chunk compression. Rendering is a flat top-down chalkboard with medallion sprites and a persistent geometric intent overlay.
- The year-end handoff carries season ID, next season ID, seeds and exiles, living references, contracts and schedules, pending waivers and obligations, owned picks and lottery identity, relationships and memory summaries, medical state, governance state, fan cards, future fixtures and venue decisions, career records and honour references, random counters, the next event queue and the rule version.
- Season completion, its snapshot and permission to start the next season commit in one durable transaction; the permission is consumed once.
- History is archived once and never copied wholesale into every handoff.
- Same inputs, rule version, event ID and seeded key reproduce the same result.

## §18 Validator
Reference implementation: `tools/validate.py <tables_dir> [--season N]`; exit code is the number of failed checks. `tools/run_seasons.py` generates any season from the Season 1 template and runs the validator over many seasons of simulated results. Checks that depend on an open item (§20) are reported as warnings.

**SCHEDULE** — 360 rows; no self-fixtures; per club 18 games, 9H/9A; per club 8 divisional (4H/4A, each rival twice), 4 interconference (equal-seed, one per opposing division), 6 same-conference non-division; weeks 1–14 have 20 games and weeks 15–19 have 16; exactly 3 breaks after weeks 4/9/14; per club exactly one bye in weeks 15–19 and each such week's byes form one complete seed group across all eight divisions; all dates are Sat/Sun/Mon; no game inside Tue 00:01–Fri 23:59 and no regular-season game on an active-club break date; every week 1 pairing is equal-seed; per club, kickoff-slot points (§6) total 33–38.

**CALENDAR** — final regular-season Monday = last Sunday of December − 34; wild card/divisional/conference at C−28/C−21/C−14; Bowl at C−7; Coronation at C; all rungs Sundays exactly 7 days apart; Coronation is the last Sunday of the year; 27 calendar weeks containing 19 numbered fixture weeks.

**TOUR** — 28 rows, 7 rounds × 4 games; no self-fixtures; every club 7 games against 7 distinct opponents; 3–4 nominal homes per club, none nominally home and none nominally away in three consecutive rounds; 12 intra-conference and 16 cross-conference; rounds on calendar weeks 1/4/8/11/15/18/22 with 21- or 28-day gaps between rounds' first games; last round ≥14 days before the Bowl; each round's four games share one `venue_slot` and the seven slots are distinct; game 1 of a round is Saturday 17:30, games 2 and 3 Sunday 10:00 and 13:00, game 4 Monday 17:30; no venue is a club home.

**VENUES** — `venue_id` and `venue_name` unique; no city equals a club `team_city`; pool ≥8 (one season's draw: 7 Tour rounds + the Bowl); every active row has capacity > 0.

**TEAMS** — `team_id`, `abbr`, `team_name` unique; exactly 40 active and 8 ambassador, one ambassador per division; `is_active == (club_status = "active")`; `is_league_member` true on all 48; `bed_id` unique among active and shaped `bed-rank`, each division's five active clubs sharing one bed; **`(primary_hex, secondary_hex)` unique across all 48**; `tertiary_hex` matches `COLOR_REGISTRY`.

**ROTATION** — in every season the eight divisions fill beds 1–8 exactly once; each division moves one bed on inside its block; the conferences flip every fourth season and the pattern repeats every eight; seeds 1–4 are the previous season's ranks 1–4, seed 5 is the returning ambassador, and each division's last-place club is the next ambassador; every generated season passes every check above, with each season's final order produced by `src/dfl/standings.py` from simulated results.

**STANDINGS** — `tests/test_standings.py` passes: tie handling (standings versus statistics), the cascade including the wins-before-win % order, the division and conference step skips and the sorted-once rule, the seeded draw, playoff seeding and bracket, exile and seed hand-off, the Coronation window, and invariants over 150 simulated seasons on the real schedule (every club 18 games, wins equal losses, point differentials sum to zero, input order irrelevant).

**CROSS-FILE** — every Schedule and Tour city resolves to exactly one `team_city` (a stadium town such as Minneapolis does not); no ambassador appears in the Schedule; the eight ambassadors equal the eight `lottery_1_8` clubs; gemstone hexes match between `TEAMS` and `COLOR_REGISTRY`.

**CAP** — for every club-state in a multi-season run, at least one legal transaction exists; dead money never exceeds $735; the positional minimums total 48; the viability margin is logged every club-year.

## §19 Changelog and supersession policy
- One normative block, fenced with an edition stamp, date and content hash. Edition 8.15 supersedes Editions 8, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9, 8.10, 8.11, 8.12, 8.13 and 8.14 and everything prior.
- Every rule has a stable ID. A change rewrites the value under that ID and touches nothing else.
- A changelog table records `date | rule ID | old → new | reason | edition`. The old "Audit changes" list is generated from this table and never hand-written. To verify the content hash, remove the hash line, then take SHA-256 of the remaining file (UTF-8, LF); `tools/validate.py --doc` does this.
- Superseded text is deleted or tagged `superseded by <ID> @ ed.N`. Historical rationale is kept only where it still explains a live choice.
- §0 is the single source for every figure. A mechanical check scans for numeric and money tokens and flags any figure appearing twice with different values.

| date | section | old → new | reason | edition |
|---|---|---|---|---|
| 2026-10-22 | §4, §5 | `city`, `name` → `team_city`, `team_name` | §18 named columns that did not exist; a bare `city` also clashed with `INTL_VENUES.city` | 8.1 |
| 2026-10-22 | §4 | sort order undefined for non-alphabetical conference/division order → defined as `COLOR_REGISTRY` order | the TEAMS table was never alphabetical | 8.1 |
| 2026-10-22 | §6 | other games "Sunday 10:00 or 13:00" → "10:00, 13:00 or 17:30" | 38 scheduled games kick off Sunday 17:30, a window already in §0 | 8.1 |
| 2026-10-22 | §7, §18 | no nominal home in consecutive rounds → none home or away in three consecutive rounds | the old rule has no solution for eight clubs (four clubs would need homes in rounds 1, 3, 5, 7 and could never play each other) | 8.1 |
| 2026-10-22 | §7 TOUR | 7 of 28 nominal home tags flipped; Minneapolis → Minnesota | satisfy the new rule (six repeats, the minimum possible); Minneapolis is a stadium town, not a `team_city` | 8.1 |
| 2026-10-22 | §8, §18 | pool-size check marked pending | skeleton of 12 rows against a minimum of 29 | 8.1 |
| 2026-10-22 | head | commentary removed; edition stamp and content hash added | §19 requires both | 8.1 |
| 2026-10-22 | §20 | open items added for the break-week conflict and the missing rule IDs | surfaced by the first review | 8.1 |
| 2026-10-22 | §0, §2, §5 calendar | "league-wide break" → "active-club break" | Tour round 4 plays in a break week; closes the old open item 7 | 8.2 |
| 2026-10-22 | §7, §18 | venues drawn per game → one venue per Tour round plus one for the Bowl (8 per season); `kickoff` column added and `venue_slot` is now `TR-round` | four games share one stadium, so they need distinct kickoff slots | 8.2 |
| 2026-10-22 | §0, §8, §18 | 12-venue skeleton, pool ≥29 → 50-venue pool, minimum 8; `city` no longer unique; `tier` replaced by `suitability`; Singapore National Stadium dropped | pool supplied by the league owner; a season needs only 8 venues | 8.2 |
| 2026-10-22 | §19 | changelog table sat under §20 → moved into §19 | build error in 8.1 | 8.2 |
| 2026-10-22 | §20 | venue-pool and break-week items closed; remaining items renumbered | resolved | 8.2 |
| 2026-10-22 | §3 | exile and seed rule, tiebreak cascade, tie-game rule and NFL playoff format written down | specified by the league owner; closes the standings gap found in the readiness review | 8.3 |
| 2026-10-22 | §4 | `division_rank` defined as the club's seed for the season | the exile rule makes seeds a product of last season's standings | 8.3 |
| 2026-10-22 | §6, §18 | kickoff-slot points rule added (33–38 per club) | league owner's rule; Season 1 already satisfies it (range 33–38, all 40 clubs) | 8.3 |
| 2026-10-22 | §20 | items 7–10 added | open after the review | 8.3 |
| 2026-10-22 | §3, §4, §5, §18 | seed-bed rotation written down; `bed_id` `division_code-rank` → `bed-rank` | owner's rule; a division code cannot be the durable slot once divisions move between beds | 8.4 |
| 2026-10-22 | §3 | playoffs at the higher seed's home venue; Coronation venue by fictitious fan vote, no repeat within 40 years | owner's rule | 8.4 |
| 2026-10-22 | §0, §11, §18 | required places 36 → 47; Bowl discretionary places 17 → 6; dead-money ceiling $795 → $740 | the owner's positional minimums total 47 | 8.4 |
| 2026-10-22 | §11 | positional minimums added | owner's list | 8.4 |
| 2026-10-22 | §20 | rotation, postseason-venue and league-minimum items closed; new items added; renumbered | resolved by the owner | 8.4 |
| 2026-10-22 | §0, §11, §18 | long snapper added to the positional minimums; required places 47 → 48; Bowl discretionary places 6 → 5; dead-money ceiling $740 → $735 | owner's addition | 8.5 |
| 2026-10-22 | §3 | Coronation venue drawn from the 48 DFL club venues; a finalist whose venue wins plays at home | owner's rule | 8.5 |
| 2026-10-22 | §20 | wins-before-win% order, long snapper, Tour template and bed-order items closed; tie-frequency calibration item added | confirmed by the owner | 8.5 |
| 2026-10-22 | §3 | a tied game is not played in the standings but is played for statistics (games, points, point differential) | owner's rule | 8.6 |
| 2026-10-22 | §3 | Coronation vote decided by a seeded random draw among the nine eligible venues | owner's rule (simplest method) | 8.6 |
| 2026-10-22 | §11 | emergency fill-ins: game-day roster, injured reserve, practice-squad elevation, between-game signings and a per-position emergency list | owner asked for a system; NFL defaults | 8.6 |
| 2026-10-22 | §18 | `standings.py` and `test_standings.py` added; `rotation.py` now ranks simulated results with `standings.py` | first test round | 8.6 |
| 2026-10-22 | §20 | vote-weighting and emergency fill-in items closed; fill-in parameters and standings assumptions added | resolved or restated | 8.6 |
| 2026-10-22 | §11 | cap room does not carry over (no banking) | owner's ruling | 8.7 |
| 2026-10-22 | §0, §11 | practice squad 8 → 16, at most 6 veterans | owner's ruling (NFL size) | 8.7 |
| 2026-10-22 | §3 | a division's last-place club cannot take a wild card | owner's ruling | 8.7 |
| 2026-10-22 | §11 | injured reserve: at most 8 designated returns per club per season | owner's ruling (NFL default) | 8.7 |
| 2026-10-22 | §0, §20 | season length target about 50 hours, non-binding | owner's ruling | 8.7 |
| 2026-10-22 | header, §18 | reference code now the DFLv2 repository layout | repository move | 8.7 |
| 2026-10-22 | §0, §11 | salary floor $800 and the floor supplement → removed | owner's ruling: fan sentiment and recall answer underspending | 8.8 |
| 2026-10-22 | §11 | overage tolerated and cleared at the cut → cap unenforced until the final cut, enforced from then | owner's ruling | 8.8 |
| 2026-10-22 | §11 | practice-squad salaries count against the cap (stated) | owner's ruling | 8.8 |
| 2026-10-22 | §11 | viability "dead money" defined strictly; restructure defined | left to the builder by the owner; recorded in docs/LEDGER.md | 8.8 |
| 2026-10-22 | §11 | no cap until the final cut → cap enforced all year, counting only the highest 69 contracts before the final cut | owner's ruling: defer to the NFL's top-51 method; stops free-agency hoarding | 8.9 |
| 2026-10-22 | §11, §12 | every released player goes through waivers → a player with 4 or more credited seasons goes straight to free agency | owner's ruling: NFL vested-veteran rule | 8.9 |
| 2026-10-22 | §3 | tiebreak cascade confirmed as written; rationale recorded (columns shown on the standings page) | owner's ruling; the NFL's head-to-head, restart and common-games steps are deliberately not adopted | 8.9 |
| 2026-10-22 | §2 | year undefined → the DFL year runs 1 January to 31 December; offseason order and provisional dates added | owner's ruling ("dialed back to Jan. 1st to Dec. 31st"); dates are the builder's proposal pending owner confirmation | 8.10 |
| 2026-10-22 | §12 | compensatory picks counted every qualifying departure → departures netted against signings (NFL) | builder's default after owner asked for the NFL's method; compensation is not a parity tool | 8.10 |
| 2026-10-22 | §0, §11 | preseason ceiling 75 → 90 including the practice squad | owner's ruling (the NFL's number); 75 could not hold a 69-man club plus a full draft class | 8.11 |
| 2026-10-23 | §13 | four consecutive seasons in a pool ended active candidacy → at four consecutive years she retires permanently and is removed from the list; being hired resets the clock | owner's ruling (keeps the database from bloating) | 8.12 |
| 2026-10-23 | §11 | added the rookie reserve: unmade picks count at the rookie scale in the cap number | owner approved the builder's recommendation (the NFL's rookie-pool principle); unsigned draft picks were 2.5% of picks in the 100-year test | 8.12 |
| 2026-10-23 | §13 | character card defined: identity fixed, hidden attributes/growth/traits, public view, per-card seeded creation, 672-player classes with a class-strength shift, founding league | owner asked for a creator built from scratch on current ideas and left the card design to the builder | 8.13 |
| 2026-10-23 | §11 | jersey-number blocks by position (QB 10-14, TE 15-19, WR 80-89), overflow blocks, unique per club, camp relaxation | owner's ranges; builder's capacity check and overflow recommendation, accepted by the owner | 8.14 |
| 2026-10-23 | §11 | jersey-number stewardship: assigned on joining, kept while with the club (through exile), freed on leaving; no retired numbers | owner's ruling | 8.15 |

## §20 Open items
1. Definitions for the analytics terms: role execution, opponent-adjusted difficulty, leverage, replacement baseline, opportunity threshold.
2. Engine content: play catalogue, numeric route landmarks, blocking rules, coverage responsibilities, ball flight, injury and fatigue models.
3. Negotiation and market-clearing mechanics; development, aging and retirement curves.
4. Bowl roster repair mechanism when a position shortfall occurs.
5. The real-time to simulated-time mapping under deterministic pacing (target about 50 hours a season, non-binding).
6. §19 promises a stable ID on every rule; none are assigned yet, so the changelog uses section references.
7. Tie-game frequency: ties are excluded from records, so the order of overall wins before overall win % matters only when ties occur. Calibrate in simulation so that ties stay highly unlikely.
8. Fill-in parameters inherited from the NFL and not yet confirmed: game-day roster 48/47, two practice-squad elevations a game and three a player a season, four-game injured reserve minimum. The out-of-position penalty belongs with engine content (item 2).
9. Standings assumptions to confirm: higher strength of schedule and strength of victory rank higher (NFL default); a club with no decisions has a win % of 0; the seeded draw is the last resort and is rerun from a season-and-scope seed.
10. Calibrate the pre-cut cap count (§0) once the market exists: it is adapted from the NFL's top 51 of a 90-player camp roster, and the ratio does not carry over exactly. (The rookie reserve, §11, is built and replaces the open question about rookie room.)
11. Floor removal relies on the sentiment and recall mechanism pricing poor spending and poor results; its inputs are unbuilt (people layer). Payroll spread across clubs is a fairness-band candidate.
12. Emergency cap borrowing (owner proposal): deferred. The viability invariant is built so that no legal roster is ever unreachable; revisit only if a real jam appears once the market exists.
13. Exile-return loop: a club exiled as last place returns the next year as seed 5 with a lottery pick in slots 1-8; whether this traps weak clubs in a cycle, and whether extra picks for exiled clubs would help or would reward losing, must be measured with the real engine (parity profiles, Phase 4).
14. Offseason dates in §2 are provisional.
15. Character attributes, their means and the age curve are placeholders until the engine (Phase 3) says which attributes it uses and how; the attribute list and every distribution are data in `data/characters/` and may change. Overall ratings are not yet comparable across positions (a calibration job for Phase 3/4).
16. Appearance: the creation look is immutable; whether and how a character visibly ages is decided in presentation (Phase 7), by redrawing from the stored look.
17. Leader pools and hiring (four willing candidates per role, §13) use the same creator and the four-year pool rule; the hiring market itself is people-layer work (Phase 5).
18. Jersey numbers: none open. The same-club re-signing reclaim and lowest-open assignment are the builder's defaults (change on request).
