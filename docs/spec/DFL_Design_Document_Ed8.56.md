# DFL — Living Design Document
**Edition 8.56 · 2026-10-30** (amends Edition 8.55; changes listed in §19)
**Content hash (SHA-256 of this file with this line removed):** `9e5be6a9aaaa31a27789df5fda91982120b8e9b9b6553b270f8ce4ea1af26ae6`

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
| Postseason | 12 clubs (6 per conference), 11 games (10 conference playoff games and the Coronation) |
| Diamond Coronation venue | Amethyst Aloha Stadium (permanent, neutral) |
| Ambassador Tour | 8 clubs, 7 rounds, 28 games |
| Cap · reserve · min salary · comp threshold · CEO salary | $1,000 · $25 · $5 · $75 · $2,000 |
| Rookie scale (R1–R7) · first-round option | $25/$18/$14/$11/$9/$7/$5 · $35 |
| Roster · practice squad · preseason ceiling · required places · Bowl places | 53 · 16 · 90 · 48 · 53 |
| Contracts counted against the cap before the final cut · credited seasons that exempt a released player from waivers | 69 (53 + 16) · 4 |
| Draft | 7 rounds × 48 = 336 picks |
| Bracket winner paths · halls · directional relationships | 2,048 · 59 · 2,256 |
| Draft class size · pool floor per position · leadership candidates per role | 672 · 8 · 4 |
| Real time: offseason · per competitive week block · year | 45 min · 120 min · ≈55 h |
| Season length target | about 50 hours of real time; a target, and missing it is not a defect |
| Recall threshold · CEO election cycle | sentiment 32 · every 3 seasons |
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
| Final cut to 53 and 16, then the waiver period | the Tuesday and Wednesday before week 1 |

In the DFL calendar a year's events fire in the order of this table, then the 27-week season below, and close on 31 December: 47 dated events a year (Edition 8.55 removed the Coronation venue vote of 15 June and its announcement of 4 July). *Owner ruling of 2026-10-29: the offseason dates above are approved.* They agree with the leadership, contract and honours rules: contracts end with the league year and expire on 1 January; the leadership window runs 1–31 January on the previous season's records, which were fixed at the year close; honours and the year's results are written at the year close on 31 December.
- **The year close and the new year.** The Coronation is the last Sunday of the year, so it falls on one of 25–31 December and the year closes (31 December: results, governance, honours) between 0 and 6 days after it. The league year turns on 1 January. The turn follows the Coronation "the next morning" only when the Coronation is itself on 31 December (for example 2000); in a year whose last Sunday is earlier (2001: 30 December; 2002: 29 December; 2005: 25 December) the days between are empty, and only the year close falls in them. Nothing else, trading and signing included, happens between the Coronation and 1 January.
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
- **Tiebreak cascade.** It orders every ranking: divisions, playoff seeding, the exile decision, the draft and the Week 4 comparison of the conferences. Steps run in this order, each used only while clubs remain tied: (1) overall wins; (2) overall win %; (3) division wins; (4) division win %; (5) conference wins; (6) conference win %; (7) point differential; (8) strength of schedule; (9) strength of victory; (10) the seeded draw. Steps 3–4 apply only when every tied club is in the same division, and steps 5–6 only when every tied club is in the same conference; inapplicable steps are skipped. A tied group is sorted once by the whole cascade; there is no step-by-step reduction. The cascade is deliberately a list of columns, so every step can be shown beside each club on the standings page in this order; it intentionally has no head-to-head step and no restart (owner ruling, Edition 8.9). *Order of steps 7–9 (owner's ruling of 2026-10-30, Edition 8.55).* Point differential now precedes strength of schedule and strength of victory; before, strength of schedule was step 7 and point differential step 8. *Draw priority (owner ruling of 2026-10-29).* Each club has one seeded priority per season, fixed by the season's seed and the club alone, and the same in every ranking that season (division, seeding, wild card, Tour, draft order), so changing the scope of a ranking can never reverse an otherwise identical tie. Standings rank best first, with a higher point differential, strength of schedule, strength of victory and priority ranking higher; a club with no decisions has a win % of 0. The draft order is the exact reverse of the ranking (worst first), so every step, the priority included, runs the other way there.
- **Records.** A tied game counts as not played in the standings only: it adds no win or loss, no division or conference record, and is left out of every win % and every opponent list (so out of strength of schedule and strength of victory). For statistical purposes it is played: games played, points scored and points allowed include it, and so does point differential. Win % = wins ÷ (wins + losses), counted as 0 when a club has no decisions. Strength of schedule = the combined win % of every opponent played (their total wins ÷ their total wins and losses, once per game played); strength of victory = the same for every opponent beaten; for both, a higher figure ranks higher. Point differential = points scored minus points allowed over all regular-season games. Ambassadors' Tour results play no part.
- **Playoffs.** Six clubs per conference: the four division winners (seeds 1–4) and the two best other clubs (seeds 5–6), all ordered by the cascade. Seeds 1 and 2 have a bye in the Wild Card round. Wild card round: seed 3 hosts seed 6 and seed 4 hosts seed 5. Divisional round, reseeded: seed 1 hosts the lowest surviving seed and seed 2 hosts the other survivor. Conference championship: the higher remaining seed hosts. The winners of the two conference championships meet in the Diamond Coronation. The four-round postseason calendar of §2 is unchanged (wild card, divisional, conference championships, Coronation). A conference plays five playoff games, ten in all, and the Coronation makes eleven. A division's last-place club is exiled and cannot take a wild card. Conference playoff games are played at the higher seed's home venue. *(Owner's ruling of 2026-10-30, Edition 8.55; replaces the seven-club NFL bracket.)*
- **The Diamond Coronation.** The Coronation is always played at **Amethyst Aloha Stadium**, the DFL's own stadium, owned and renovated by the DFL. There is no venue vote, no rotating host and no repeat rule, and no club's own venue is ever used. The game is always neutral: the engine gives neither finalist a home-field edge (§10). *(Owner's ruling of 2026-10-30, Edition 8.55; replaces the fan vote of June, the announcement of 4 July and the 40-year window.)*
- **Nominal home of the Coronation (designated after Week 4).** Which finalist is the nominal home is decided from the first four weeks and frozen. Pool every interconference game of numbered weeks 1–4 (80 games) and treat each conference as one aggregate team. Rank the two aggregates by the cascade above, skipping the steps that cannot apply to two conferences (3–6): (1) wins; (2) win %; (7) point differential; (8) strength of schedule; (9) strength of victory; (10) a seeded draw. *Aggregation.* Wins and losses are the pooled games each conference won and lost. A tied pooled game is not played for wins, losses or the opponent lists but its points count toward point differential (the rule for clubs, above). Aggregate strength of schedule = the combined win % of the opponents the aggregate played: one entry for every decided pooled game, the opponent being the club on the other side, and each opponent's wins and losses taken from her full regular-season record over numbered weeks 1–4 (all her games, not only the interconference ones); aggregate strength of victory = the same over the opponents the aggregate beat. A higher figure ranks higher. Only the games of numbered weeks 1–4 are read; Tour games and later games play no part. Because one conference's win is the other's loss and the two point differentials sum to zero, equal wins always mean equal win %, so step 2 can never separate them, and point differential decides unless it is exactly zero. The seeded draw gives each conference one number fixed by the season's seed and the conference alone, so it is reproducible. The champion of the winning conference is the Coronation's nominal home (the finalist of the other conference is the nominal away side). The decision is made when Week 4 is complete, saved with the figures of every step, the step that decided it and the draw numbers, and never recomputed; nothing played afterwards can change it. The designation controls only the established rules for the nominal home side: it kicks the first half (§10) and takes the home uniform (§9). *(Owner's ruling of 2026-10-30, Edition 8.55.)*
- Ambassadors keep identity, full roster obligations and full cap, play the 7-game Tour, and receive a first-round lottery band.
- **Seed beds and rotation.** The schedule template has eight seed beds, each holding seed positions 1–5, in two blocks: beds 1–4 and beds 5–8. Each division fills one bed, its five active clubs holding the bed's positions by seed. In Season 1 the Western divisions fill beds 1–4 in the order Northwest, Southwest, Central, Southern, and the Eastern divisions fill beds 5–8 in the order Great Lakes, Atlantic, Midland, Southeast. Each season every division moves one bed on inside its block (bed 4 to bed 1, bed 8 to bed 5). After four seasons the conferences flip: in Season 5 the Western divisions fill beds 5–8 in the same order and the Eastern divisions fill beds 1–4. The next four seasons rotate inside the new blocks, then the conferences flip back, so the pattern repeats every eight seasons. Divisions never change conference. In formula form, for season n let k = (n − 1) mod 4, p = ⌊(n − 1) ÷ 4⌋ mod 2, and i = the division's position (0–3) in its conference's order above: a Western division fills bed 1 + 4p + (i + k) mod 4 and an Eastern division fills bed 1 + 4(1 − p) + (i + k) mod 4. Clubs never change division; only seeds and beds move.
- 59 halls: 1 league, 2 conference, 8 division, 48 club.

## §4 Table conventions and sort orders
- All tables are CSV, UTF-8, LF, with a header row. Dates are ISO `YYYY-MM-DD`. Times are `HH:MM` league time (UTC).
- Booleans are lowercase `true`/`false`. Empty cells are empty, never `"Exile"`.
- Canonical sorts (also asserted in §18): `TEAMS` conference → division → division_rank, where conference and division order follow `COLOR_REGISTRY` (Western, then Eastern; Northwest, Southwest, Central, Southern; Great Lakes, Atlantic, Midland, Southeast), not alphabetical order; `SCHEDULE` week → game; `TOUR` round → game; `INTL_VENUES` venue_id; `CALENDAR` calendar_week; `COLOR_REGISTRY` row order as listed (league brand, conferences, then divisions in the order above).
- Club name columns are `team_city` and `team_name`; a bare `city` belongs to `INTL_VENUES` only. City values in `SCHEDULE` and `TOUR` are `team_city` values. `team_city` is a region label and may differ from the stadium's town (`location`): Minnesota plays in Minneapolis.
- `bed_id` is the durable fixture slot, `bed-rank`: the bed (1–8) and the club's seed in it, for example `1-1` for Northwest seed 1 in Season 1. A division's bed changes by season (§3). Ambassadors carry no bed_id.
- `division_rank` is the club's seed for the season, set from the previous season's final division ranking (§3). Ambassadors carry 6.
- `is_active` means *plays the regular season this year*. `is_league_member` means *is one of the 48 permanent clubs*. They are different columns because they were once conflated.

## §5 TEAMS
Season 1 values. Sorted conference → division → division_rank. Ambassadors have blank `bed_id` and `division_rank` 6.

```
team_id,abbr,team_city,team_name,conference,division,bed_id,division_rank,club_status,is_active,is_league_member,draft_band,venue,location,primary_color,primary_hex,secondary_color,secondary_hex,tertiary_color,tertiary_hex
30,MON,Montana,Outriders,Western,Northwest,1-1,1,active,true,true,standard,IronGate Stadium,"Helena, MT",Dark Forest Green,#174A32,Tan,#C2A477,Peridot,#A5C83B
21,SEA,Seattle,Orcas,Western,Northwest,1-2,2,active,true,true,standard,Lumen Field,"Seattle, WA",College Navy,#002244,Action Green,#69BE28,Peridot,#A5C83B
31,UTA,Utah,Avalanche,Western,Northwest,1-3,3,active,true,true,standard,NorthStar Bank Field,"Salt Lake City, Utah",Glacier Blue,#8FC8D8,Deep Navy Blue,#16324F,Peridot,#A5C83B
39,CAL,Calgary,Stampede,Western,Northwest,1-4,4,active,true,true,standard,Redwood Credit Union Park,"Calgary, Canada",Root Beer Brown,#492716,Prairie Green,#688B45,Peridot,#A5C83B
40,IDA,Idaho,Tomahawks,Western,Northwest,1-5,5,active,true,true,standard,Continental Freight Stadium,"Boise, ID",Pine Green,#17543B,Steel Gray,#87949E,Peridot,#A5C83B
41,POR,Portland,Tempest,Western,Northwest,,6,ambassador,false,true,lottery_1_8,HarborTrust Field,"Portland, OR",Storm Gray,#606A75,Electric Purple,#A334E8,Peridot,#A5C83B
7,SFX,San Francisco,Shine,Western,Southwest,2-1,1,active,true,true,standard,Candlestick Park,"San Francisco, CA",Scarlet Red,#AA0000,Gold,#D3BC8D,Topaz,#E6A32E
20,SDX,San Diego,Sirens,Western,Southwest,2-2,2,active,true,true,standard,Qualcomm Stadium,"San Diego, CA",Powder Blue,#6CAEDF,Gold,#D3BC8D,Topaz,#E6A32E
33,SAC,Sacramento,Sting,Western,Southwest,2-3,3,active,true,true,standard,Twin Rivers Stadium,"West Sacramento, CA",Electric Yellow,#EFFF00,Black,#101010,Topaz,#E6A32E
6,ARI,Arizona,Thunderbirds,Western,Southwest,2-4,4,active,true,true,standard,State Farm Stadium,"Glendale, AZ",Cardinal Red,#97233F,Black,#000000,Topaz,#E6A32E
19,LVR,Las Vegas,Invaders,Western,Southwest,2-5,5,active,true,true,standard,Allegiant Stadium,"Las Vegas, NV",Black,#000000,Silver,#B0B7BC,Topaz,#E6A32E
4,LAL,Los Angeles,Legion,Western,Southwest,,6,ambassador,false,true,lottery_1_8,SoFi Stadium,"Inglewood, CA",Royal Blue,#003DA5,Silver,#B0B7BC,Topaz,#E6A32E
43,OCO,Oklahoma City,Outlaws,Western,Central,3-1,1,active,true,true,standard,TriAxis Financial Stadium,"Oklahoma City, OK",Desert Tan,#C2A477,Blued Gunmetal,#293C4A,Tourmaline,#D94B89
34,OMA,Omaha,Assassins,Western,Central,3-2,2,active,true,true,standard,SilverGate Field,"Omaha, NE",Obsidian,#111318,Acid Green,#A8FF2A,Tourmaline,#D94B89
27,DAK,Dakota,Divas,Western,Central,3-3,3,active,true,true,standard,Union Capital Stadium,"Bismarck, ND",Gotham Green,#125740,Silver,#B0B7BC,Tourmaline,#D94B89
18,KCC,Kansas City,Reapers,Western,Central,3-4,4,active,true,true,standard,Arrowhead Stadium,"Kansas City, MO",Red,#C8102E,Gold,#D3BC8D,Tourmaline,#D94B89
16,DEN,Denver,Unicorns,Western,Central,3-5,5,active,true,true,standard,Empower Field at Mile High,"Denver, CO",Orange,#FF5F05,Navy,#002244,Tourmaline,#D94B89
5,MIN,Minnesota,Warriors,Western,Central,,6,ambassador,false,true,lottery_1_8,U.S. Bank Stadium,"Minneapolis, MN",Purple,#4F2683,Gold,#D3BC8D,Tourmaline,#D94B89
17,HOU,Houston,Faeries,Western,Southern,4-1,1,active,true,true,standard,NRG Stadium,"Houston, TX",Deep Steel Blue,#03202F,Battle Red,#A71930,Garnet,#9A1B36
42,MCA,Mexico City,Mariachas,Western,Southern,4-2,2,active,true,true,standard,Ironclad Insurance Stadium,"Mexico City, Mexico",Turquoise,#20AFA6,Sun Gold,#E9B44C,Garnet,#9A1B36
1,DAL,Dallas,Starlings,Western,Southern,4-3,3,active,true,true,standard,AT&T Stadium,"Arlington, TX",Navy,#002244,Silver,#B0B7BC,Garnet,#9A1B36
29,SAV,San Antonio,Vaqueras,Western,Southern,4-4,4,active,true,true,standard,Stratos Bank Stadium,"San Antonio, TX",Bronze,#CD7F32,Desert Teal,#3D8C8C,Garnet,#9A1B36
44,NMS,New Mexico,Scorpions,Western,Southern,4-5,5,active,true,true,standard,Crown Harbor Field,"Albuquerque, NM",Midnight Plum,#321C31,Desert Copper,#D47842,Garnet,#9A1B36
45,BAJ,Baja,Barracudas,Western,Southern,,6,ambassador,false,true,lottery_1_8,PrairieStar Stadium,"Cabo San Lucas, Mexico",Abyssal Teal,#083B4C,Predator Silver,#A8B7BC,Garnet,#9A1B36
3,GBP,Green Bay,Glamour,Eastern,Great Lakes,5-1,1,active,true,true,standard,Lambeau Field,"Green Bay, WI",Green,#203731,Gold,#D3BC8D,Aquamarine,#55C5D8
9,CHI,Chicago,Foxes,Eastern,Great Lakes,5-2,2,active,true,true,standard,Soldier Field,"Chicago, IL",Navy,#002244,Orange,#FF5F05,Aquamarine,#55C5D8
2,DET,Detroit,Soul,Eastern,Great Lakes,5-3,3,active,true,true,standard,Ford Field,"Detroit, MI",Honolulu Blue,#0076B6,Silver,#B0B7BC,Aquamarine,#55C5D8
15,CLE,Cleveland,Mud Dawgs,Eastern,Great Lakes,5-4,4,active,true,true,standard,Huntington Bank Field,"Cleveland, OH",Brown,#311D00,Orange,#FF5F05,Aquamarine,#55C5D8
46,TOR,Toronto,Sentinels,Eastern,Great Lakes,5-5,5,active,true,true,standard,GoldenRail Stadium,"Toronto, Canada",Crimson,#A6192E,Steel Gray,#78858B,Aquamarine,#55C5D8
22,BUF,Buffalo,Brats,Eastern,Great Lakes,,6,ambassador,false,true,lottery_1_8,Highmark Stadium,"Orchard Park, NY",Royal Blue,#003DA5,Red,#C8102E,Aquamarine,#55C5D8
47,MOS,Montreal,Storm,Eastern,Atlantic,6-1,1,active,true,true,standard,Evergreen Credit Stadium,"Montreal, Canada",Frost Silver,#B8C5D1,Nordic Blue,#245A81,Emerald,#009B77
26,NEP,New England,Rebels,Eastern,Atlantic,6-2,2,active,true,true,standard,Gillette Stadium,"Foxborough, MA",Navy,#002244,Red,#C8102E,Emerald,#009B77
10,NYG,New York,Gladiators,Eastern,Atlantic,6-3,3,active,true,true,standard,MetLife Stadium,"East Rutherford, NJ",Blue,#0B2265,Red,#C8102E,Emerald,#009B77
12,PHI,Philadelphia,Harpies,Eastern,Atlantic,6-4,4,active,true,true,standard,Lincoln Financial Field,"Philadelphia, PA",Midnight Green,#004C54,Silver,#B0B7BC,Emerald,#009B77
14,WAS,Washington,Commandos,Eastern,Atlantic,6-5,5,active,true,true,standard,Northwest Stadium,"Landover, MD",Burgundy,#773141,Gold,#D3BC8D,Emerald,#009B77
32,BAL,Baltimore,Rooks,Eastern,Atlantic,,6,ambassador,false,true,lottery_1_8,M&T Bank Stadium,"Baltimore, MD",Purple,#4F2683,Black,#000000,Emerald,#009B77
35,SLR,St. Louis,Archers,Eastern,Midland,7-1,1,active,true,true,standard,MetroAxis Field,"St. Louis, MO",Navy,#002244,Metallic Gold,#B3995D,Turquoise,#26A9A0
38,TEN,Tennessee,Belles,Eastern,Midland,7-2,2,active,true,true,standard,Nissan Stadium,"Nashville, TN",Navy,#0C2340,Titans Blue,#4B92DB,Turquoise,#26A9A0
23,CIN,Cincinnati,Shadow Katz,Eastern,Midland,7-3,3,active,true,true,standard,Paycor Stadium,"Cincinnati, OH",Orange,#FF5F05,Black,#000000,Turquoise,#26A9A0
36,CAR,Carolina,Comets,Eastern,Midland,7-4,4,active,true,true,standard,Bank of America Stadium,"Charlotte, NC",Carolina Blue,#0085CA,Black,#101820,Turquoise,#26A9A0
28,PIT,Pittsburgh,Riviters,Eastern,Midland,7-5,5,active,true,true,standard,Acrisure Stadium,"Pittsburgh, PA",Black,#101820,Light Blue,#83C9EF,Turquoise,#26A9A0
24,IND,Indianapolis,Pit Vipers,Eastern,Midland,,6,ambassador,false,true,lottery_1_8,Lucas Oil Stadium,"Indianapolis, IN",Racing Red,#D71920,Electric Lime,#C6FF00,Turquoise,#26A9A0
48,ALA,Alabama,Thunder,Eastern,Southeast,8-1,1,active,true,true,standard,Granite Stadium,"Birmingham, AL",Dark Thunder Blue,#102A43,Lightning Yellow,#F5D547,Alexandrite,#7040A0
37,HAV,Havana,Mariposas,Eastern,Southeast,8-2,2,active,true,true,standard,Estadio Panamericano,"Havana, Cuba",Teal,#006778,Black,#101820,Alexandrite,#7040A0
25,MIA,Miami,Monsters,Eastern,Southeast,8-3,3,active,true,true,standard,Hard Rock Stadium,"Miami Gardens, FL",Aqua,#008E97,Orange,#FF5F05,Alexandrite,#7040A0
8,ATL,Atlanta,Mavericks,Eastern,Southeast,8-4,4,active,true,true,standard,Mercedes-Benz Stadium,"Atlanta, GA",Black,#000000,Red,#C8102E,Alexandrite,#7040A0
11,NOS,New Orleans,Mayfairs,Eastern,Southeast,8-5,5,active,true,true,standard,Caesars Superdome,"New Orleans, LA",Black,#000000,Old Gold,#9F8958,Alexandrite,#7040A0
13,TBB,Tampa Bay,Corsairs,Eastern,Southeast,,6,ambassador,false,true,lottery_1_8,Raymond James Stadium,"Tampa, FL",Red,#C8102E,Pewter,#5A5A5A,Alexandrite,#7040A0
```

## §6 SCHEDULE
Season 1, which is the bed template. Sorted week → game. 360 rows; weeks 1–14 have 20 games, weeks 15–19 have 16. Beds are derivable: `away_bed = bed_id(away)`, `home_bed = bed_id(home)`. Game 1 of each week is Saturday 17:30, the last game is Monday 17:30, all others on Sunday at 10:00, 13:00 or 17:30. Kickoff-slot points: Monday 17:30 = 5, Saturday 17:30 = 4, Sunday 17:30 = 3, Sunday 13:00 = 2, Sunday 10:00 = 1; each active club's 18 games total 33 to 38 points. Tour games carry no slot points. Every later season uses these same games, each bed-and-seed position filled by the club holding it that season (§3); dates come from that year's calendar.

```
week,game,date,kickoff,away,home
1,1,2000-07-01,17:30,Oklahoma City,Green Bay
1,2,2000-07-02,10:00,Las Vegas,New Orleans
1,3,2000-07-02,10:00,Arizona,Carolina
1,4,2000-07-02,10:00,Kansas City,Philadelphia
1,5,2000-07-02,10:00,Seattle,Havana
1,6,2000-07-02,10:00,Mexico City,Chicago
1,7,2000-07-02,10:00,Dakota,Detroit
1,8,2000-07-02,10:00,Calgary,Atlanta
1,9,2000-07-02,10:00,Denver,Toronto
1,10,2000-07-02,10:00,Omaha,New England
1,11,2000-07-02,10:00,Montana,St. Louis
1,12,2000-07-02,13:00,Sacramento,Miami
1,13,2000-07-02,13:00,San Francisco,Alabama
1,14,2000-07-02,13:00,San Antonio,Cleveland
1,15,2000-07-02,13:00,San Diego,Tennessee
1,16,2000-07-02,13:00,Houston,Montreal
1,17,2000-07-02,13:00,Utah,Cincinnati
1,18,2000-07-02,17:30,New Mexico,Washington
1,19,2000-07-02,17:30,Idaho,Pittsburgh
1,20,2000-07-03,17:30,Dallas,New York
2,1,2000-07-08,17:30,Chicago,Omaha
2,2,2000-07-09,10:00,Montreal,Oklahoma City
2,3,2000-07-09,10:00,Washington,Denver
2,4,2000-07-09,10:00,Tennessee,Seattle
2,5,2000-07-09,10:00,Cincinnati,Sacramento
2,6,2000-07-09,10:00,Carolina,Calgary
2,7,2000-07-09,10:00,Pittsburgh,Las Vegas
2,8,2000-07-09,10:00,Philadelphia,San Antonio
2,9,2000-07-09,10:00,Detroit,Dallas
2,10,2000-07-09,10:00,New England,Mexico City
2,11,2000-07-09,10:00,St. Louis,San Francisco
2,12,2000-07-09,13:00,Alabama,Montana
2,13,2000-07-09,13:00,Cleveland,Kansas City
2,14,2000-07-09,13:00,Havana,San Diego
2,15,2000-07-09,13:00,New Orleans,Idaho
2,16,2000-07-09,13:00,Miami,Utah
2,17,2000-07-09,13:00,Atlanta,Arizona
2,18,2000-07-09,17:30,Green Bay,Houston
2,19,2000-07-09,17:30,New York,Dakota
2,20,2000-07-10,17:30,Toronto,New Mexico
3,1,2000-07-15,17:30,Montana,Green Bay
3,2,2000-07-16,10:00,Cincinnati,Dallas
3,3,2000-07-16,10:00,St. Louis,Houston
3,4,2000-07-16,10:00,Arizona,Cleveland
3,5,2000-07-16,10:00,Seattle,New England
3,6,2000-07-16,10:00,San Francisco,Montreal
3,7,2000-07-16,10:00,New Orleans,Denver
3,8,2000-07-16,10:00,Havana,Mexico City
3,9,2000-07-16,10:00,Miami,Dakota
3,10,2000-07-16,10:00,Calgary,Philadelphia
3,11,2000-07-16,10:00,San Diego,Chicago
3,12,2000-07-16,13:00,Sacramento,New York
3,13,2000-07-16,13:00,Idaho,Toronto
3,14,2000-07-16,13:00,Alabama,Oklahoma City
3,15,2000-07-16,13:00,Las Vegas,Washington
3,16,2000-07-16,13:00,Atlanta,San Antonio
3,17,2000-07-16,13:00,Pittsburgh,New Mexico
3,18,2000-07-16,17:30,Utah,Detroit
3,19,2000-07-16,17:30,Carolina,Kansas City
3,20,2000-07-17,17:30,Tennessee,Omaha
4,1,2000-07-22,17:30,Chicago,Seattle
4,2,2000-07-23,10:00,New England,San Diego
4,3,2000-07-23,10:00,Detroit,Sacramento
4,4,2000-07-23,10:00,San Antonio,Carolina
4,5,2000-07-23,10:00,Toronto,Las Vegas
4,6,2000-07-23,10:00,Cleveland,Calgary
4,7,2000-07-23,10:00,Denver,Pittsburgh
4,8,2000-07-23,10:00,Omaha,Havana
4,9,2000-07-23,10:00,Green Bay,San Francisco
4,10,2000-07-23,10:00,Dakota,Cincinnati
4,11,2000-07-23,10:00,Kansas City,Atlanta
4,12,2000-07-23,13:00,New York,Utah
4,13,2000-07-23,13:00,Washington,Idaho
4,14,2000-07-23,13:00,Dallas,Miami
4,15,2000-07-23,13:00,New Mexico,New Orleans
4,16,2000-07-23,13:00,Philadelphia,Arizona
4,17,2000-07-23,13:00,Mexico City,Tennessee
4,18,2000-07-23,17:30,Oklahoma City,St. Louis
4,19,2000-07-23,17:30,Montreal,Montana
4,20,2000-07-24,17:30,Houston,Alabama
5,1,2000-08-05,17:30,Havana,New Orleans
5,2,2000-08-06,10:00,Washington,Montreal
5,3,2000-08-06,10:00,San Francisco,Montana
5,4,2000-08-06,10:00,Kansas City,Omaha
5,5,2000-08-06,10:00,New York,Philadelphia
5,6,2000-08-06,10:00,St. Louis,New England
5,7,2000-08-06,10:00,Atlanta,Cincinnati
5,8,2000-08-06,10:00,Arizona,Idaho
5,9,2000-08-06,10:00,San Antonio,Las Vegas
5,10,2000-08-06,10:00,Dakota,Oklahoma City
5,11,2000-08-06,10:00,Detroit,Tennessee
5,12,2000-08-06,13:00,San Diego,Utah
5,13,2000-08-06,13:00,New Mexico,Houston
5,14,2000-08-06,13:00,Mexico City,Sacramento
5,15,2000-08-06,13:00,Alabama,Chicago
5,16,2000-08-06,13:00,Green Bay,Toronto
5,17,2000-08-06,13:00,Carolina,Pittsburgh
5,18,2000-08-06,17:30,Seattle,Dallas
5,19,2000-08-06,17:30,Miami,Cleveland
5,20,2000-08-07,17:30,Calgary,Denver
6,1,2000-08-12,17:30,San Francisco,Mexico City
6,2,2000-08-13,10:00,Denver,Oklahoma City
6,3,2000-08-13,10:00,San Antonio,Utah
6,4,2000-08-13,10:00,Toronto,Detroit
6,5,2000-08-13,10:00,Omaha,Dakota
6,6,2000-08-13,10:00,Las Vegas,Kansas City
6,7,2000-08-13,10:00,New Mexico,Idaho
6,8,2000-08-13,10:00,Alabama,Atlanta
6,9,2000-08-13,10:00,Miami,Carolina
6,10,2000-08-13,10:00,New Orleans,Pittsburgh
6,11,2000-08-13,10:00,Montreal,Green Bay
6,12,2000-08-13,13:00,Montana,Houston
6,13,2000-08-13,13:00,Sacramento,Seattle
6,14,2000-08-13,13:00,Dallas,Calgary
6,15,2000-08-13,13:00,Philadelphia,Washington
6,16,2000-08-13,13:00,New England,New York
6,17,2000-08-13,13:00,Cleveland,Chicago
6,18,2000-08-13,17:30,Arizona,San Diego
6,19,2000-08-13,17:30,Tennessee,Cincinnati
6,20,2000-08-14,17:30,Havana,St. Louis
7,1,2000-08-19,17:30,Tennessee,Miami
7,2,2000-08-20,10:00,Cincinnati,St. Louis
7,3,2000-08-20,10:00,Idaho,Montana
7,4,2000-08-20,10:00,Omaha,Denver
7,5,2000-08-20,10:00,Mexico City,New Mexico
7,6,2000-08-20,10:00,Dallas,San Antonio
7,7,2000-08-20,10:00,Dakota,Calgary
7,8,2000-08-20,10:00,Cleveland,New York
7,9,2000-08-20,10:00,Pittsburgh,Toronto
7,10,2000-08-20,10:00,Green Bay,Alabama
7,11,2000-08-20,10:00,Chicago,Montreal
7,12,2000-08-20,13:00,Oklahoma City,Seattle
7,13,2000-08-20,13:00,Utah,Arizona
7,14,2000-08-20,13:00,San Diego,Houston
7,15,2000-08-20,13:00,Detroit,Havana
7,16,2000-08-20,13:00,Atlanta,Washington
7,17,2000-08-20,13:00,Carolina,New Orleans
7,18,2000-08-20,17:30,Las Vegas,San Francisco
7,19,2000-08-20,17:30,Philadelphia,New England
7,20,2000-08-21,17:30,Kansas City,Sacramento
8,1,2000-08-26,17:30,Denver,San Antonio
8,2,2000-08-27,10:00,St. Louis,Carolina
8,3,2000-08-27,10:00,Miami,Alabama
8,4,2000-08-27,10:00,Oklahoma City,Omaha
8,5,2000-08-27,10:00,New Mexico,Dallas
8,6,2000-08-27,10:00,Dakota,Mexico City
8,7,2000-08-27,10:00,Seattle,Utah
8,8,2000-08-27,10:00,Washington,New Orleans
8,9,2000-08-27,10:00,Cincinnati,Havana
8,10,2000-08-27,10:00,Pittsburgh,Atlanta
8,11,2000-08-27,10:00,Montana,San Diego
8,12,2000-08-27,13:00,Idaho,Kansas City
8,13,2000-08-27,13:00,Houston,San Francisco
8,14,2000-08-27,13:00,Arizona,Las Vegas
8,15,2000-08-27,13:00,New York,Tennessee
8,16,2000-08-27,13:00,Green Bay,Cleveland
8,17,2000-08-27,13:00,Montreal,New England
8,18,2000-08-27,17:30,Calgary,Sacramento
8,19,2000-08-27,17:30,Toronto,Chicago
8,20,2000-08-28,17:30,Detroit,Philadelphia
9,1,2000-09-02,17:30,Miami,Atlanta
9,2,2000-09-03,10:00,Utah,Omaha
9,3,2000-09-03,10:00,Dallas,Houston
9,4,2000-09-03,10:00,San Francisco,Arizona
9,5,2000-09-03,10:00,Las Vegas,Sacramento
9,6,2000-09-03,10:00,Kansas City,Denver
9,7,2000-09-03,10:00,New York,Montreal
9,8,2000-09-03,10:00,Cleveland,Toronto
9,9,2000-09-03,10:00,Tennessee,Pittsburgh
9,10,2000-09-03,10:00,New England,Washington
9,11,2000-09-03,10:00,St. Louis,Alabama
9,12,2000-09-03,13:00,Idaho,San Antonio
9,13,2000-09-03,13:00,Seattle,Dakota
9,14,2000-09-03,13:00,Calgary,New Mexico
9,15,2000-09-03,13:00,Carolina,Detroit
9,16,2000-09-03,13:00,Chicago,Cincinnati
9,17,2000-09-03,13:00,New Orleans,Philadelphia
9,18,2000-09-03,17:30,Mexico City,Montana
9,19,2000-09-03,17:30,Havana,Green Bay
9,20,2000-09-04,17:30,Oklahoma City,San Diego
10,1,2000-09-16,17:30,New Mexico,Arizona
10,2,2000-09-17,10:00,Carolina,Tennessee
10,3,2000-09-17,10:00,Pittsburgh,St. Louis
10,4,2000-09-17,10:00,Idaho,Seattle
10,5,2000-09-17,10:00,Sacramento,San Francisco
10,6,2000-09-17,10:00,Chicago,Miami
10,7,2000-09-17,10:00,Havana,New York
10,8,2000-09-17,10:00,San Antonio,Dakota
10,9,2000-09-17,10:00,Denver,Las Vegas
10,10,2000-09-17,10:00,Philadelphia,Toronto
10,11,2000-09-17,10:00,Kansas City,Utah
10,12,2000-09-17,13:00,Mexico City,Oklahoma City
10,13,2000-09-17,13:00,Dallas,San Diego
10,14,2000-09-17,13:00,Montana,Calgary
10,15,2000-09-17,13:00,Washington,Cleveland
10,16,2000-09-17,13:00,New England,Cincinnati
10,17,2000-09-17,13:00,Detroit,Green Bay
10,18,2000-09-17,17:30,Houston,Omaha
10,19,2000-09-17,17:30,Alabama,Montreal
10,20,2000-09-18,17:30,Atlanta,New Orleans
11,1,2000-09-23,17:30,New England,Detroit
11,2,2000-09-24,10:00,Havana,Miami
11,3,2000-09-24,10:00,Arizona,Dallas
11,4,2000-09-24,10:00,Las Vegas,New Mexico
11,5,2000-09-24,10:00,Oklahoma City,Houston
11,6,2000-09-24,10:00,Montana,Utah
11,7,2000-09-24,10:00,Calgary,Seattle
11,8,2000-09-24,10:00,Cincinnati,Philadelphia
11,9,2000-09-24,10:00,Cleveland,Pittsburgh
11,10,2000-09-24,10:00,Washington,Carolina
11,11,2000-09-24,10:00,Tennessee,Montreal
11,12,2000-09-24,13:00,Omaha,San Francisco
11,13,2000-09-24,13:00,Dakota,Kansas City
11,14,2000-09-24,13:00,San Diego,Sacramento
11,15,2000-09-24,13:00,Green Bay,St. Louis
11,16,2000-09-24,13:00,New York,Chicago
11,17,2000-09-24,13:00,New Orleans,Alabama
11,18,2000-09-24,17:30,Denver,Idaho
11,19,2000-09-24,17:30,Toronto,Atlanta
11,20,2000-09-25,17:30,San Antonio,Mexico City
12,1,2000-09-30,17:30,Las Vegas,Calgary
12,2,2000-10-01,10:00,Oklahoma City,Kansas City
12,3,2000-10-01,10:00,Denver,Dakota
12,4,2000-10-01,10:00,Mexico City,Dallas
12,5,2000-10-01,10:00,Utah,Idaho
12,6,2000-10-01,10:00,Carolina,New York
12,7,2000-10-01,10:00,Miami,New England
12,8,2000-10-01,10:00,Tennessee,Green Bay
12,9,2000-10-01,10:00,St. Louis,Chicago
12,10,2000-10-01,10:00,New Orleans,Toronto
12,11,2000-10-01,10:00,San Francisco,San Diego
12,12,2000-10-01,13:00,Omaha,Montana
12,13,2000-10-01,13:00,Houston,Seattle
12,14,2000-10-01,13:00,Sacramento,Arizona
12,15,2000-10-01,13:00,Atlanta,Detroit
12,16,2000-10-01,13:00,Alabama,Havana
12,17,2000-10-01,13:00,Montreal,Philadelphia
12,18,2000-10-01,17:30,San Antonio,New Mexico
12,19,2000-10-01,17:30,Cincinnati,Cleveland
12,20,2000-10-02,17:30,Pittsburgh,Washington
13,1,2000-10-07,17:30,Toronto,Carolina
13,2,2000-10-08,10:00,San Diego,Las Vegas
13,3,2000-10-08,10:00,Houston,San Antonio
13,4,2000-10-08,10:00,Chicago,Detroit
13,5,2000-10-08,10:00,Washington,New York
13,6,2000-10-08,10:00,New Mexico,Denver
13,7,2000-10-08,10:00,Sacramento,Omaha
13,8,2000-10-08,10:00,Alabama,Tennessee
13,9,2000-10-08,10:00,Montana,Oklahoma City
13,10,2000-10-08,10:00,Green Bay,New England
13,11,2000-10-08,10:00,Cleveland,New Orleans
13,12,2000-10-08,13:00,Utah,Mexico City
13,13,2000-10-08,13:00,Dallas,Kansas City
13,14,2000-10-08,13:00,Calgary,Idaho
13,15,2000-10-08,13:00,Montreal,St. Louis
13,16,2000-10-08,13:00,Pittsburgh,Cincinnati
13,17,2000-10-08,13:00,Atlanta,Havana
13,18,2000-10-08,17:30,Seattle,San Francisco
13,19,2000-10-08,17:30,Philadelphia,Miami
13,20,2000-10-09,17:30,Dakota,Arizona
14,1,2000-10-14,17:30,Omaha,Dallas
14,2,2000-10-15,10:00,Kansas City,New Mexico
14,3,2000-10-15,10:00,Seattle,Montana
14,4,2000-10-15,10:00,Utah,Calgary
14,5,2000-10-15,10:00,Houston,Mexico City
14,6,2000-10-15,10:00,Chicago,Green Bay
14,7,2000-10-15,10:00,Detroit,Cleveland
14,8,2000-10-15,10:00,St. Louis,Tennessee
14,9,2000-10-15,10:00,San Diego,Dakota
14,10,2000-10-15,10:00,Philadelphia,Pittsburgh
14,11,2000-10-15,10:00,Montreal,Havana
14,12,2000-10-15,13:00,Idaho,Las Vegas
14,13,2000-10-15,13:00,San Francisco,Oklahoma City
14,14,2000-10-15,13:00,Arizona,Denver
14,15,2000-10-15,13:00,Toronto,Washington
14,16,2000-10-15,13:00,New York,Atlanta
14,17,2000-10-15,13:00,New Orleans,Miami
14,18,2000-10-15,17:30,Sacramento,San Antonio
14,19,2000-10-15,17:30,New England,Alabama
14,20,2000-10-16,17:30,Cincinnati,Carolina
15,1,2000-10-28,17:30,Philadelphia,Montreal
15,2,2000-10-29,10:00,Carolina,St. Louis
15,3,2000-10-29,10:00,Atlanta,Alabama
15,4,2000-10-29,10:00,Washington,New England
15,5,2000-10-29,10:00,Kansas City,Oklahoma City
15,6,2000-10-29,10:00,New Mexico,Mexico City
15,7,2000-10-29,10:00,Arizona,San Francisco
15,8,2000-10-29,13:00,Calgary,Montana
15,9,2000-10-29,13:00,Denver,Omaha
15,10,2000-10-29,13:00,San Antonio,Houston
15,11,2000-10-29,13:00,Chicago,Toronto
15,12,2000-10-29,13:00,Cleveland,Green Bay
15,13,2000-10-29,13:00,Pittsburgh,Tennessee
15,14,2000-10-29,17:30,Las Vegas,San Diego
15,15,2000-10-29,17:30,New Orleans,Havana
15,16,2000-10-30,17:30,Seattle,Idaho
16,1,2000-11-04,17:30,Dakota,Denver
16,2,2000-11-05,10:00,Cincinnati,Pittsburgh
16,3,2000-11-05,10:00,Havana,Atlanta
16,4,2000-11-05,10:00,Miami,New Orleans
16,5,2000-11-05,10:00,Seattle,Calgary
16,6,2000-11-05,10:00,Idaho,Utah
16,7,2000-11-05,10:00,San Diego,Arizona
16,8,2000-11-05,13:00,Sacramento,Las Vegas
16,9,2000-11-05,13:00,Mexico City,San Antonio
16,10,2000-11-05,13:00,Dallas,New Mexico
16,11,2000-11-05,13:00,Detroit,Toronto
16,12,2000-11-05,13:00,New England,Philadelphia
16,13,2000-11-05,13:00,Tennessee,Carolina
16,14,2000-11-05,17:30,Omaha,Kansas City
16,15,2000-11-05,17:30,New York,Washington
16,16,2000-11-06,17:30,Chicago,Cleveland
17,1,2000-11-11,17:30,St. Louis,Cincinnati
17,2,2000-11-12,10:00,New Orleans,Atlanta
17,3,2000-11-12,10:00,Green Bay,Detroit
17,4,2000-11-12,10:00,Washington,Philadelphia
17,5,2000-11-12,10:00,Houston,Dallas
17,6,2000-11-12,10:00,Idaho,Calgary
17,7,2000-11-12,10:00,New Mexico,San Antonio
17,8,2000-11-12,13:00,Las Vegas,Arizona
17,9,2000-11-12,13:00,San Francisco,Sacramento
17,10,2000-11-12,13:00,Denver,Kansas City
17,11,2000-11-12,13:00,Toronto,Cleveland
17,12,2000-11-12,13:00,Montreal,New York
17,13,2000-11-12,13:00,Alabama,Miami
17,14,2000-11-12,17:30,Oklahoma City,Dakota
17,15,2000-11-12,17:30,Pittsburgh,Carolina
17,16,2000-11-13,17:30,Utah,Montana
18,1,2000-11-18,17:30,Kansas City,Dakota
18,2,2000-11-19,10:00,Cleveland,Detroit
18,3,2000-11-19,10:00,Havana,Alabama
18,4,2000-11-19,10:00,Philadelphia,New York
18,5,2000-11-19,10:00,Mexico City,Houston
18,6,2000-11-19,10:00,San Antonio,Dallas
18,7,2000-11-19,10:00,Arizona,Sacramento
18,8,2000-11-19,13:00,Montana,Seattle
18,9,2000-11-19,13:00,San Diego,San Francisco
18,10,2000-11-19,13:00,Omaha,Oklahoma City
18,11,2000-11-19,13:00,Atlanta,Miami
18,12,2000-11-19,13:00,Green Bay,Chicago
18,13,2000-11-19,13:00,Carolina,Cincinnati
18,14,2000-11-19,17:30,Calgary,Utah
18,15,2000-11-19,17:30,Tennessee,St. Louis
18,16,2000-11-20,17:30,New England,Montreal
19,1,2000-11-25,17:30,St. Louis,Pittsburgh
19,2,2000-11-26,10:00,Miami,Havana
19,3,2000-11-26,10:00,Cincinnati,Tennessee
19,4,2000-11-26,10:00,Toronto,Green Bay
19,5,2000-11-26,10:00,Sacramento,San Diego
19,6,2000-11-26,10:00,Oklahoma City,Denver
19,7,2000-11-26,10:00,Montana,Idaho
19,8,2000-11-26,13:00,Utah,Seattle
19,9,2000-11-26,13:00,Dakota,Omaha
19,10,2000-11-26,13:00,Houston,New Mexico
19,11,2000-11-26,13:00,Montreal,Washington
19,12,2000-11-26,13:00,New York,New England
19,13,2000-11-26,13:00,Alabama,New Orleans
19,14,2000-11-26,17:30,Dallas,Mexico City
19,15,2000-11-26,17:30,Detroit,Chicago
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
- **Ballots and the final 53 (Edition 8.56; complete).** *From the owner's outline, unchanged:* All-Star selection uses per-position ranked ballots with every candidate ranked, Droop quota `floor(8 / (places + 1)) + 1`, seeded ties, and 53 places per conference drawn from that conference's four Ambassador clubs: 48 positional places and 5 discretionary places. *The sequence from vote to locked roster is built as follows. Items 3 to 9 are the builder's readings of that outline, marked for the owner's ratification in §20 item 4; none is hidden in code.*
  1. **Who votes and when.** The eight Ambassador head coaches vote once, the moment the last Tour round has been played, and each ballots both conferences (the quota's 8 is these eight voters). The ballots, the counts and every tie draw are saved with the league and in the permanent archive, and are never recomputed.
  2. **Candidates.** Every player on the 53-player roster of one of the conference's four Ambassador clubs at that moment; injured reserve and the practice squad are not candidates. A player has one permanent position, so she is on exactly one positional ballot, and no tie ever crosses positions.
  3. **A ballot** ranks every candidate of the position by what the voter's coaches judge her overall to be: the coaches' view of §13 (Squads), the true overall plus a symmetric error that shrinks with the player's seasons of evidence and with the staff's judgement. A head coach has no lean towards her own club's players.
  4. **Positional counts.** One count for each position, with the places of the positional minimums of §11 (K 1, P 1, QB 3, HB 3, WR 5, TE 2, LT 2, LG 2, C 1, RG 2, RT 2, LE 2, RE 2, DT 3, ROLB 2, MLB 3, LOLB 2, CB 5, FS 2, SS 2, LS 1: 48 places). The backfield is counted as three halfbacks; a fullback can win only a discretionary place.
  5. **Discretionary count.** The 5 remaining places are one count over every candidate not elected in a positional count, whatever the position (quota 2). Each voter ranks that whole field by the same judged overall, so overalls are taken as comparable across positions.
  6. **The count.** Each ballot starts at weight 1 and counts for its highest-ranked candidate still in the count. While places remain: if as many candidates remain as places, all of them are elected; otherwise the candidate with the highest tally at or above the quota is elected and each ballot that counted for her keeps the fraction (tally − quota) / tally of its weight (weighted inclusive Gregory transfer); if nobody has a quota, the candidate with the lowest tally is eliminated and her ballots pass on at full weight. Arithmetic is exact (fractions, never floats).
  7. **Ties** (for the highest tally to elect, the lowest to eliminate, or the order of those elected without a quota) are settled by a seeded draw among the tied candidates alone; the draw depends only on the season's seed, the ballot, the step and the tied players, so it is reproducible and does not depend on the order of ballots or players.
  8. **The order of a count.** The elected, in the order of election; then the rest, by carrying on eliminating the lowest tally until none is left, the last eliminated ranking highest. This is "the order of the original ballot" the replacement rule below uses. The count is never rerun.
  9. **Lock and play.** Rosters lock one simulated hour before kickoff. A shortfall is a validation failure repaired before play (below). Only players who were on the ballot can be chosen; a player who joined the four clubs after the vote is not. All 53 are dressed. The side is coached by its conference's best Tour club (head coach and three coordinators), whose judgement chooses the starters; its emergency list is built as for a club (§11). A league run without the ledger (the standalone harness) has no players to vote on and plays the older composite of the best healthy players at each position.
- **Bowl rosters: replacements and exhausted ballots (owner ruling of 2026-10-29).** Each conference's roster is exactly 53 distinct players, drawn from its four Ambassador clubs. Three things are kept apart. *Permanent position*: the position on the player's card, which selection and play never change. *Emergency game assignment*: a rostered player named on the Bowl club's emergency list (§11) who plays another position in the game; it fills no roster place and changes no permanent position. *Exception to the positional minimums*: the one case below.
  - When a selected player is not medically available before the lock, her place is filled by the next-ranked medically available player of the same position from that conference's four Ambassador clubs who is not already on the roster, in the order of the original ballot (the order of the count, item 8 above). The count is not rerun.
  - If that position's ballot is exhausted (no such player is left), the place is still filled by a new, distinct player. Moving a player who is already on the roster would fill one place by emptying another, so it never counts. The place goes to the best-ranked medically available player of the four clubs who is not on the roster, taking the positions nearest the empty one first (the neighbour order of the game-day active list, §11) and then the rest in the canonical order of positions. She keeps her permanent position and is named on the emergency list for the position that is short, so that she plays it as an emergency game assignment.
  - Only then is the positional minimum of the short position not met by permanent position. This exception is allowed for that case alone, is written to the validation log with the players concerned, and ends if a medically available player of the position is later found. If fewer than 53 medically available distinct players exist in the four clubs, the roster plays short (§11: at least 11 medically available legal players, or forfeit).
- Tour Rings go to the winners' final 53 who are present and medically ready at kickoff (taking a snap is not required) plus the four Bowl coaches. *Built (Edition 8.56): everyone on the winning side's locked roster, all of whom were medically available when it locked an hour earlier, and the head coach and three coordinators of the winning conference's best Tour club.*

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
Western/Northwest,Peridot,#A5C83B,Division gemstone
Western/Southwest,Topaz,#E6A32E,Division gemstone
Western/Central,Tourmaline,#D94B89,Division gemstone
Western/Southern,Garnet,#9A1B36,Division gemstone
Eastern/Great Lakes,Aquamarine,#55C5D8,Division gemstone
Eastern/Atlantic,Emerald,#009B77,Division gemstone
Eastern/Midland,Turquoise,#26A9A0,Division gemstone
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
- Two unsportsmanlike penalties eject a player (taunting counts as unsportsmanlike). The ejected player is out for the rest of the game, is replaced from the squad, and is not injured.
- Statistics conventions (Edition 8.47, the NFL's): a touchdown run or catch counts only the yards to the goal line; a kneel-down is a rush for minus one yard; a foul that would carry the ball more than half the distance to the goal moves it half that distance, except a spot foul, which puts the ball on the one-yard line at most.
- Two-minute warning (Edition 8.46, the NFL's rule, unspecified before): in the second and fourth quarters the clock stops when it first reaches 2:00 and no timeout is charged. Not given in overtime.
- Penalties: false start/offside 5; delay of game 5; offensive holding 10; defensive holding 5 plus automatic first down; illegal contact 5 plus automatic first down; defensive pass interference spot plus automatic first down; offensive pass interference 10; personal foul and unsportsmanlike 15. Beyond that base list the NFL's other fouls are in the catalogue (`data/engine/PENALTIES.csv`, 46 in all, Edition 8.46), at the NFL's accepted rates by type: pre-snap fouls (illegal formation, shift and motion, too many men, neutral zone infraction, encroachment), roughing the passer 15 and a first down, intentional grounding (10 yards or the spot, and the down), ineligible player downfield 5, illegal use of hands, face mask 15, unnecessary roughness 15, horse collar 15, taunting 15, the illegal blocks, tripping, and the kicking-play fouls (holding and blocks in the back on returns, roughing and running into the kicker, kick catch and fair catch interference, offside on a kick). Enforcement follows the NFL: dead-ball fouls from the previous spot with the down replayed; half the distance to the goal; a spot foul to the one-yard line at most; fouls after the play added to where it ended; the side a live-ball foul hurts may decline it.
- Formations, route landmarks, blocking rules, coverage responsibilities, reads and reactions come from the league concept vocabulary. A coach assembles a playbook; a weekly game plan selects a subset against perceived opposition.
- Familiarity belongs to assignments and teammates and develops through repetition. Designated players may check before the snap, with limited reach and delay.
- Fixed physics timestep 0.05 s. Decision checks are event-driven with a maximum 0.10 s interval.
- At neutral sites the nominal home is assigned by the fixture template, or by higher seed with a seeded tie. The Diamond Coronation is the exception: it is always neutral, always at Amethyst Aloha Stadium, and its nominal home is the finalist of the conference designated after Week 4 (§3). A neutral site gives no home-field edge.
- Uncommon penalties may be implemented partially; gaps are recorded in an implementation coverage register (`docs/ENGINE_COVERAGE.md`, written in Edition 8.46).
- Spatial engine (Edition 8.48, owner's go-ahead of 2026-10-26 to finish Phase 3; the numbers are data in `data/engine/spatial/`). Each scrimmage play is played out by 22 players on a field of 100 yards plus two 10-yard end zones and 53⅓ yards wide, hashes 6 yards apart, at the fixed timestep and decision interval of §0. A player is a point with a position and a velocity, a top speed, an acceleration and a deceleration taken from her hidden speed, acceleration and agility, steering toward a target point set by her assignment. Nothing is scripted to succeed: separation, pressure, tackles and yards come from what the players do.
- Formations, plays and coverage (Edition 8.48): the offense lines up in a formation (`FORMATIONS.json`) and runs a play from the catalogue (`PLAYS.json`): inside zone, outside zone, power, draw and quarterback sneak; quick game, curl-flat, mesh, smash, four verticals, deep shot, play-action, flood and screen. Routes are numeric landmarks (stem depth, break angle, length after the break, `ROUTES.json`). The defense lines up in a front and a coverage (`COVERAGES.json`): cover 0 (man, all-out pressure), cover 1, cover 2, cover 3 and cover 4, with up to two extra rushers. A zone defender covers the receiver inside her landmark (depth and width); a man defender follows her receiver, seeing him as he was 0.15 to 0.35 s ago (awareness and coverage skill). Play calling uses down, distance, field position, score and clock; the coaches' ratings move the odds slightly, capped.
- Blocking (Edition 8.48): a blocker and the defender she is assigned to are engaged on contact. An engagement has a leverage that moves each timestep by the difference between the two players' blocking and shedding ratings (strength, weight, technique and the relevant skill) plus noise. A run blocker who wins drives the defender away from the hole; a pass rusher who wins is free to chase the quarterback; a defender who wins sheds and pursues. Two blockers on one defender add their ratings. An engaged defender cannot tackle.
- Passing (Edition 8.48): the quarterback drops back, reads his receivers in the play's order and throws when one is open enough (a calm, cautious quarterback needs more; a pressured one less), after a release delay. The ball flies a parabola at a speed set by throw power and lands where he aims (the receiver's projected position) plus an error that grows with distance, speed and pressure and shrinks with accuracy. The first player in reach of the ball contests it, the receiver by hands and contested-catch skill and a defender by coverage and ball skills; the result is a catch, an incompletion (a pass defended is one) or an interception. The receiver then runs for yards after the catch.
- Running and tackling (Edition 8.48): the ball carrier picks a path through the lanes the blockers and defenders leave, looking ahead as far as her vision allows. A defender who is in reach and not engaged attempts a tackle; it succeeds by her tackling against the carrier's elusiveness and strength, the closing speed and the angle; a missed tackle slows the defender and costs the carrier speed; a second defender at the same moment makes the tackle more likely. A fumble can occur at contact.
- Traces (§17, Edition 8.48): a play can record every position at 0.05 s, the ball, each player's intent (route, zone, man, block, rush, pursuit) and the decisive events (snap, release, catch, contact, shed, tackle), compressed without loss. Recording a trace never changes a result.
- Spatial engine, status and use (Edition 8.49): the play-level engine stays the league's default and is the only engine the season loop plays; a game is played with 22-player snaps only when asked for (`spatial=True`), and a trace is kept only when asked for as well. Penalties, injuries, the clock, kicking, kick returns and the box score are the same code in both modes; the spatial engine replaces the draw of a run or a pass snap's result. The spatial engine is measured against the same bands as the play-level engine (`tools/calibrate_engine.py --spatial`) and against snap-level targets (`tools/calibrate_spatial.py`, section 20 item 46).
- Quarterback under pressure (Edition 8.49): a quarterback feels a free rusher once she is within `press_near` yards; once she is within `press_go` yards he throws to the best-open receiver, or, with a chance that rises as his awareness falls (`press_bail`), gets rid of the ball out of bounds or runs, and a run is chosen over a throwaway by his mobility and the room ahead. Before that he reads in the play's order and takes the first receiver whose perceived separation clears his margin; the margin rises with the depth of the throw (`depth_risk`), for a throw to the back (`check_pen`) and, on third and fourth down, for each yard a catch would fall short of the first-down marker (`stick_pen`). Perceived separation counts a free defender closing on the throw point while the ball is in the air (`perc_close`) and carries a read error that falls with awareness.
- Play calling and alignment (Edition 8.49): the calls use down, distance, field position, score and clock only, never the clubs' players, so that the fairness property of this section holds; on third and fourth down with five or more yards to go the check-down concepts give way to concepts that run to the marker. In man coverage the two corners take the two wide receivers by where they line up, whichever way the play is mirrored.
- Home field and coaching (Edition 8.49): the offense's coaching edge and the home-field edge (z, capped at plus or minus 1) raise its blocking ratings by `edge_eng` per z and remove `edge_perc` per z of the quarterback's read error.
- Contact limits (Edition 8.49): an engaged run defender can reach out for a carrier who passes within `eng_arm_reach` yards, with `eng_arm_p` of a free defender's chance; the pair of an engagement is brought to its geometry at no more than `eng_snap` yards a step and no player is pushed more than 0.25 yards a step to clear an overlap, so that no one moves faster than anyone can run.
- Engine build order (Edition 8.16): the rules above are implemented first with a play-level resolver, in which each play's outcome is drawn from the attributes of the players on the field, and calibrated against aggregate bands (`data/engine/BANDS.csv`). The spatial engine (22 players moving at the fixed timestep) replaces the play resolver behind the same interface and must keep meeting those bands. The play-level engine's defaults for decisions this section leaves open are listed in `docs/ENGINE.md`.

## §11 Positions, rosters, contracts, cap
- Normal roster 53 plus practice squad 16, at most 6 of whom may be veterans with more than 2 credited seasons (NFL default). Preseason ceiling 90 including practice squad (the NFL's number). Required places 48; Bowl places 53 (5 discretionary).
- Positional minimums (the 48 required places): K 1; P 1; QB 3; HB 3, or HB 2 and FB 1; WR 5; TE 2; LT 2, LG 2, C 1, RG 2, RT 2; LE 2, RE 2, DT 3; ROLB 2, MLB 3, LOLB 2; CB 5; FS 2, SS 2; LS 1. Coverage is checked at the cut and on voluntary packages.
- A club may operate temporarily short while seeking affordable replacements, but must be able to field 11 medically available legal players or forfeit.
- **Emergency fill-ins (NFL defaults).** Game-day active roster 48, or 47 with fewer than 8 offensive linemen. Between games a club may place a player on injured reserve (out at least four games; at most eight players per club per season may be designated to return), elevate up to two practice-squad players for a game (each player at most three times a season; an elevated player reverts to the practice squad afterward), sign a free agent at no less than the minimum salary, or claim a waived player, each subject to the viability invariant. Nothing can be signed during a game. Inactive players may replace injured players before the game-day roster locks; no inactive player enters from the inactive list during a game. If no medically available player remains at a position during a game, the club's emergency list for that position supplies a dressed player, who plays out of position. Before every kickoff each club files an emergency list with at least one name for every position, including K, P, LS and QB (up to four names, nearest positions first, every name a dressed and healthy player of another position; first names are not repeated across positions while enough players exist). The choice uses only dressed, medically available players and is made as injuries happen: a listed player who is hurt or already on the field is skipped. A player is never assigned to two positions at the same time. Her permanent position does not change; the engine must apply an out-of-position penalty (§20 item 2), which is not yet built.
- Injured reserve and suspension supply no cap relief.
- **No banking.** Unused cap room does not carry over; each league year starts from the same cap.
- Cap $1,000, emergency reserve $25, minimum salary $5, comp qualifying threshold $75.
- **No salary floor.** A club may spend as little as it likes; unused room is lost (no banking). Chronic underspending is answered by fan sentiment and the recall ballot (§14), not by a payment rule.
- **Upgrade signings (Edition 8.39, NFL practice).** At camp, before the cut to 53, a club signs a free agent who is clearly better than her weakest starter at that position: by the club's own view with age counted, at least `upgrade_margin` (5) points higher, for a price that fits under the cap, at most `upgrade_max` (3) a camp, the biggest gap first. Starters counted: one at kicker, punter, quarterback, running back, tight end, each offensive line spot, each defensive end, each linebacker spot, each safety and the long snapper; two at defensive tackle; three at wide receiver and cornerback. The cut then sheds whoever the club values least, so a veteran can replace a drafted rookie, as in the NFL.
- **Legal-move enumerator (Phase 1 close, Edition 8.45).** The ledger can list every single transaction a club may legally make at any moment (`Ledger.legal_moves`): signing a free agent at the league minimum to the 53 or the practice squad, releasing a player from any list, placing an injured player on injured reserve, returning one, and promoting a practice-squad player. Each entry is proved by a dry run that is rolled back, so listing changes nothing. The test that a club always has a legal move (§11 forfeit rule) is the first entry of that list. Trades and waiver claims involve other clubs and are enumerated by their own steps.
- **Cap timing (adapted from the NFL's top-51 rule).** The cap is enforced all year. From the start of the league year until the final cut to 53 and 16, a club's cap number is dead money plus its highest cap hits, counting only the §0 number of contracts; the extra camp bodies do not count. From the final cut until the league year closes every contract counts. A signing, claim, trade or restructure that leaves a club over the cap and raises its cap number is rejected. A release is never blocked by the cap (its accelerated bonus may carry a club over; it then cannot add cap until it is back under). At the final cut every club must be at or under the cap, a cut that leaves a club over is refused, and the season cannot begin with any club over. The viability invariant binds at all times, which is what keeps the final cut reachable.
- Practice-squad salaries count against the cap like any other salary.
- Dead money counts against the cap. Release or trade immediately accelerates every uncharged bonus onto the original club's current cap.
- **Viability invariant.** For every club-year: `dead money + required-minimum roster cost (48 × $5 = $240) + reserve ≤ cap`, so **dead money ≤ $735**. Checked before every signing, release, trade and restructure, for the current and every committed future year. "Dead money" here is the strict reading: everything the club would owe in that year if every contract were released at the start of it (guarantees, earned base, every uncharged bonus accelerated, and dead charges already booked). In the current year the replacement cost is pro-rated to the paid weeks still to come. A move is rejected if it leaves a year over the limit and raises that year's figure; time alone (earned salary accruing) does not freeze a club.
- **Transactions.** A move is rejected if it breaks a roster rule, the viability invariant, or (from the final cut) the cap as stated under Cap timing. Nothing else rejects a move: no league vote and no fairness veto.
- **Restructure.** Converts part of a current or future year's base salary into a bonus prorated evenly over the contract's remaining years (at most five). Base salary may not fall below the minimum salary. It is a transaction like any other and is subject to the viability invariant and the cap timing rule. *Edition 8.55 (owner's ruling of 2026-10-30): only the current year's base salary can be restructured; restructuring a future year's base is deferred and not built.*
- **Split release.** Once per club per league year, one release's uncharged bonus accelerates 50/50 across the current and next year instead of in full. *Edition 8.55 (owner's ruling of 2026-10-30): the release year's own bonus portion stays in the release year; only the later portions divide 50/50 between that year and the next.*
- **Viability margin** = cap − dead money − minimum-roster cost − reserve, logged per club-year; flag below $50.
- Contract shapes: steady, rising or declining. At most five covered years and five bonus proration years. Bonus is paid on signing and cap-prorated evenly. The receiving club assumes unpaid base and attached guarantees, never the bonus.
- Removed: incentives, roster bonuses, June 1 designation, franchise tag, restricted and exclusive-rights free agency, no-trade clauses, conditional compensation, cap-space trades.
- Rookie reserve (NFL rookie-pool principle): until a draft pick is made, the pick's rookie-scale cap hit (rounds 8 and 9 at the round-7 scale, $5) counts in the owning club's cap number for the year of that draft, so a club always keeps room to sign its class; the reserve moves with a traded pick and is replaced by the real contract when the pick is made. It adds no committed money, because rookie contracts carry no guarantees.
- Rookies accept selection and sign four fixed years at $25/$18/$14/$11/$9/$7/$5. Within-round slot rank breaks otherwise equal terms.
- **First-round option (Edition 8.55, owner's ruling of 2026-10-30).** A first-round contract carries the club's option on a fifth year at $35; the option is not a covered year until exercised. Whether to exercise it is the club's decision, taken by its general manager: the GM exercises when the market price of the overall the club believes the player has (the club's own view, which carries the GM's evaluation) is at least $35, and otherwise declines; the rule draws no random numbers. The deadline is the opening of Draft Round 1 in the league year of the player's fourth contract year (the last Thursday of April), before the first pick. Exercising adds one covered year at $35 that is **guaranteed in full** from that moment, so it counts at once toward the viability invariant for that year and a club that could not carry it declines; the year costs nothing in the current year's cap. Declining, or missing the deadline, lets the contract end with the year and the player becomes a free agent a year earlier. Every decision is saved (year, club, player, decision, salary, year covered, guarantee, reason). The option follows a traded or claimed player to his new club.
- **Jersey numbers (Editions 8.14, 8.15).** A number tells a viewer the position. Primary blocks: 1-9 K, P, LS · 10-14 QB · 15-19 TE · 20-29 CB · 30-39 HB, FB · 40-49 FS, SS · 50-59 ROLB, MLB, LOLB · 60-79 LT, LG, C, RG, RT · 80-89 WR · 90-99 LE, RE, DT. Zero and double zero are not used. A number is unique among everyone on a club's roster, practice squad and injured reserve.
  - *Overflow*, only when the primary block is full: WR may use open numbers 15-19, TE open 80-89, LB open 40-49, DL open 70-79. A player in her primary block has priority: an overflow holder is reassigned when a primary player needs her number.
  - *Camp*: before the final cut any open number from 1 to 99 is legal. At the final cut anyone outside her primary and overflow blocks is reassigned.
  - Capacity check: a normal 53 plus 16 needs at most 10 numbers in any block, and WR and TE together fit comfortably with the overflow.
  - *Stewardship*: a number is assigned to a player when she joins a club (signing, draft, waiver claim, trade) and is hers for as long as she is with that club, including through the club's exile and its return. It is freed when she leaves. A traded or claimed player keeps her number if it is free at the new club and legal, otherwise she is given another. A player who leaves and later signs again with the same club takes her old number back if it is free (builder's default). A new arrival takes the lowest open number in her primary block.
  - *No retired numbers*: a freed number is open to anyone at once.
- Extensions require two completed seasons and mutual consent. **Not built.** Rules are proposed in `docs/OWNER_RULINGS_NEEDED.md` (L4) and await the owner's approval; until then every contract simply runs out.
- Salary prorates over the 22 paid regular-season weeks, same denominator for active and ambassador clubs. Midseason deals pay remaining weeks only and expire at year close. Postseason-only replacements pay one salary-week equivalent. **Not built:** the rules that stop them being used to upgrade a roster or bypass contract and cap restrictions are proposed in `docs/OWNER_RULINGS_NEEDED.md` (L4) and await the owner's approval.
- Released players enter one simulated-day waiver period, except a player with the §0 number of credited seasons or more, who goes straight to free agency (NFL vested-veteran rule); her guarantees and bonus stay with the releasing club. Priority is last season's draft order until four league weeks finish, then weekly from current standings, with Tour standings in their prior draft bands. *Edition 8.55 (owner's ruling of 2026-10-30): "prior draft slot" is the club's own slot in last season's draft order, never a traded pick; trading picks never transfers waiver priority. Worked example for all 48 clubs: `docs/WAIVER_PRIORITY_EXAMPLE.md`.*
- Future-year minimum-salary reservations are feasibility checks, not charges.

## §12 Draft, trades, waivers
- Seven rounds, 48 ordinary selections per round, 336 total.
- Order: non-playoff clubs, then clubs by elimination round, then runner-up and champion; worse regular-season record first, relevant tiebreakers reversed.
- Returning ambassadors receive first-round slots 1–8 by an equal-chance seeded lottery; the lottery identity follows the original pick through trades and supplies their later-round order too.
- **Second draft of a returner (Edition 8.19, restoring the owner's ruling of 2026-10-04).** A club in its first season back from exile has already had its lottery pick. If it misses the playoffs it picks, in every round, as a block at the end of the non-playoff clubs (slots 37 minus the block size through 36), worst record first inside the block, so it never picks ahead of its record. If it makes the playoffs it is placed by its playoff exit like any other club. Season 1 has no returners.
- Compensatory picks follow round seven, at most two per club and 48 league-wide. Qualifying movements require a first-year base ≥ $75 on the contract signed, and, for a departure, a top-quarter snap share (ranked by her coaches' judged overall, Edition 8.37, not her true overall) at the old club. A club's qualifying signings cancel its qualifying departures, highest value first (NFL netting, so a club cannot churn free agents for picks); each unmatched departure earns a pick, best value first, ties to the club picking earlier. Compensation is for free-agent losses only; parity is the job of draft order, the lottery and place-based scheduling (see §20).
- Trade assets are named players and owned picks in the next three not-yet-completed drafts.
- Trading opens at the global close of leadership hiring. In competition, finalise only Tuesday 00:01 through Friday 23:59 league time, ending Friday 23:59 before the first game of numbered week 9. The ledger itself enforces the window (owner ruling of 2026-10-29): a trade is rejected in January before it opens, after it closes, in the postseason and through December. Saved state from earlier builds has no recorded window; on import or upgrade it is settled from the events the league has fired (open exactly between that year's trading opening and its close).
- No league vote, waiting period or subjective fairness veto. A trade that leaves either club over the cap and raises its cap number is rejected.

## §13 Characters, AI and memory
- All characters are female. Every character has a durable ID and an immutable creation portrait.
- Hidden ability is never exposed to hiring actors, voters, fan expectations or narration.
- Scouting and analytics estimate from evidence with position-specific biases. Official position is permanent. The scouting rule is defined below (Edition 8.21).
- Starting budget 32 detailed memories per character; recent and defining events stay detailed, routine events merge into relationship summaries.
- Relationships are sparse and directed, recording only actual contacts.
- Draft classes contain 672 players, at least 12 per position. Pools hold at least 8 medically available unsigned players per position and 4 willing candidates per leadership role. A character may stay in a pool (free agents, leadership candidates) at most four consecutive years: if she is still unsigned at the fourth league-year opening after she entered it, she retires permanently and is removed from the list (her career record is archived by the people layer). Being hired resets the clock to zero, and a player released later starts a new count. A drafted player who goes unsigned enters the pool when the draft ends. This bounds database size.
- **Character card (Edition 8.13).** Every player and every club leader (CEO, GM, and the club's four coaches, who hold the head-coach, offensive-coordinator, defensive-coordinator and special-teams-coordinator posts) has one card, made by the character creator, a standalone module: seed and request in, complete card out. The ledger and the engine read cards and never generate characters.
  - *Identity* is fixed at creation: durable ID, role, name, birth year, official position, creation height and weight, and the creation look (a small set of appearance choices, not an image, so the portrait can be redrawn identically). Age is derived from the birth year. Names are unique among listed characters and reusable after a character leaves the list.
  - *Hidden state*: true attributes on a 1-99 scale, a personal growth profile (peak age, remaining ceiling, volatility), and six traits on 0-100 (ambition, loyalty, work ethic, temperament, greed, leadership). The viewer, hiring actors, voters and narration see only the public view: name, role, position, age, body and look. There is no stored overall rating; any overall is derived from the hidden attributes and is for the engine only.
  - *Attributes* are data (`data/characters/`). Players have eight core attributes (speed, acceleration, strength, agility, stamina, durability, awareness, composure) plus two or three skills of their position group; leaders have three role attributes. Attributes within a card correlate through one shared talent factor.
  - *Creation*: each card is drawn from its own random stream seeded by the world seed and its ID, so it does not depend on how many cards were made before it. Position is chosen first and attributes and body are drawn conditional on it, which keeps class quotas exact.
  - *Draft class*: 672 prospects aged 21 to 23, at least 12 at every position, remaining places shared by how many of each position a club carries. Each class has its own strength shift (some classes are weak, some strong); this is a fairness parameter, set in data.
  - *Founding league*: each club gets a 53, a 16-player practice squad, six leaders and the pool gets at least 8 per position; veterans are spread over ages 21 to 34 with talent shifted up (survivors). All players are adults.
  - Slots for the 32 detailed memories and the sparse directed relationship map exist on the card and are filled by the people layer.
- **Development, aging and retirement (Edition 8.18).** Cards change once a year, at offseason opening, and never during a season. The curves are data (`data/characters/AGE_CURVE.csv`, `DEVELOPMENT.csv`) and are placeholders until the owner rules on them (§20 item 3).
  - *Development*: the age curve gives a multiplier by years from the card's own peak age (below 1 before the peak, 1 around it, falling after). Each year every attribute is multiplied by the curve at her new age divided by the curve at her old age, so a card climbs to the potential she was created with at her peak and declines after it. How fully an attribute follows the curve depends on its kind: physical attributes fully, learned skills mostly, mental attributes little (so awareness and composure hold up with age). After her peak the fall is stretched or shortened by her **position's decline rate** (`decline` in `POSITIONS.csv`, Edition 8.38): running backs fall fastest, then cornerbacks, linebackers and receivers; quarterbacks fall slowly; kickers, punters and long snappers slowest. Before her peak she also gains her hidden remaining ceiling, spread over the years left to the peak. Work ethic speeds growth and slows decline; her personal volatility scales the yearly random change. Leaders do not develop until the people layer builds their careers.
  - *Retirement*: two kinds, both at an offseason opening. (1) **Wear** (Edition 8.38, owner's ruling of 2026-10-26 that league rates follow the NFL's; closes item 19): any player, signed or not, may retire from wear, injury and life from age 24, with a chance of `retire_c_p_base` (2 percent) plus `retire_c_p_step` (1.4 points) for each year of age past it, multiplied by her position's decline rate. A player who retires under contract takes her contract with her: it ends, and **nothing is charged to the club** (no dead money, the NFL treatment of a retiring player's unpaid salary). (2) A free agent who is well past her peak may also retire as before: nothing before six years past her own peak, then a chance that rises each year, is higher for a low true overall, and is certain at age 40. The four-year pool rule above also applies and is unchanged. A retired character leaves the list and her name becomes free.
  - *Injuries* are made by the engine and written back to the club's ledger; they heal 26 weeks over the offseason (data).
  - *Squads*: a club's squad for a game is exactly its game-day active list (§11), as cards, with its coaches' ratings; elevations and emergency players included. **Coach judgement (Edition 8.36, owner's ruling of 2026-10-25: "a coach's judgement should have the same potential for failure as she has for success")**: the active list, and who starts at each position, are chosen by what the club's coaches judge each of her own players to be, never by the true overall. The judged overall is the true overall plus an error with no lean: it is as likely to be too kind as too harsh, and its average is zero. The error is fixed for the staff, the player and the league year and is new the next year. Its size is `judge_base_sd` (3.5 points) divided by the square root of one plus `judge_seasons_k` (0.7) times the player's credited seasons, then cut or raised by up to `judge_effect` (half) according to the staff's judgement rating, half the head coach's game management and half the position group's coordinator's adjustments (`judge_hc_share`), and never below `judge_min_sd` (0.8). Judging changes only who is chosen, never how a chosen player plays: the engine still reads the true cards. The engine reads cards; it holds no second copy of any roster.
  - *Scouting views (Edition 8.21).* A club never sees a player's true ability. A club's view of a player is her hidden overall plus a position bias plus an error. The error is fixed for that club and player for one league year and is new the next year; it shrinks with the player's credited seasons (more evidence, by 1 over the square root of 1 plus 0.7 per season), shrinks with the club's own general manager's evaluation rating (a rating of 100 cuts it by half, a rating of 0 raises it by half), and is smaller for the club's own players. Every club decision about players uses the club's view: draft picks, free-agent signings, releases, practice-squad choices, waiver claims. Market prices are set by the player's market rating, so a club can overpay for a player it overrates. Game-day depth and the engine still use true ability (coaches see practice; open item 22). The error size, evidence rate, evaluator effect and position biases are data (`data/characters/SCOUTING.csv`, `SCOUTING_BIAS.csv`) and are placeholders.
  - *Souls (Edition 8.22).* Every character has a soul, given when she joins the list and never replaced: an archetype and the six traits she was created with. The archetype is the nearest of a small set of archetype centres to those traits (placeholder set: leader, competitor, mercenary, loyalist, free spirit, quiet professional; `data/characters/ARCHETYPES.csv`). It is fixed for life. Each archetype carries a self-bias: a character's self-view is her hidden overall plus that bias, so a competitor overrates herself and a loyalist is modest; negotiation reads the self-view (slice 5.3 of the people layer) and nothing else does yet. Once a year, at the offseason opening and before development, every player's six traits drift: each closes a share of the gap to her archetype's centre and takes a small random step, and no trait ever moves more than a fixed bound from the value she was created with (`data/characters/SOUL.csv`). Drift is the only way a trait changes, so a soul can shift a little with the years but cannot become another soul. Work ethic feeds development (above), so drift has a small real effect on careers. The archetype list, the self-biases and the drift numbers are placeholders for the owner.
  - *Coaches (Edition 8.25, owner's structure of 2026-10-24).* **Coach is one category of character**; the head-coach post and the three coordinator posts (offensive, defensive, special teams) are roles that a coach holds, not what she is. A coach's card has five coaching attributes (game management, motivation, teaching, scheme design, in-game adjustment); her fit for a post is those attributes weighted by the post's profile (`data/characters/PROFILE.csv`, the rows named for the posts), so the same coach fits different posts differently. **One common pool** holds new coaches and those fired or resigned; any coach in it may be hired to any post at any club, so a coordinator can become a head coach elsewhere. The pool holds at least four willing candidates for each post (16), and the four-year rule applies: a coach unsigned at the fourth year turn after she entered the pool retires; being hired resets the clock, and being fired or resigning starts it again. **Chain of command**: the CEO hires, may dismiss and influences the general manager and the head coach; the head coach hires and may dismiss the coordinators. A head coach is chosen by the club from the pool by its own rating of fit, after interviews (Meeting rooms, below), an estimate whose error shrinks with the general manager's talent evaluation (the CEO takes his advice); a coordinator is chosen by the head coach from the pool by his own rating, an error that shrinks with his game management. **Leadership window** (the January hiring window, after a season): for each club in a random order, a season below 40 percent wins gives the CEO a chance (45 percent, lower for a patient CEO and higher for an impatient one, by temperament) to dismiss the head coach; any coach may resign her post (a small chance, higher for the ambitious, lower for the loyal); an empty head-coach post is filled; a new head coach dismisses each inherited coordinator with a chance (75 percent), and after a bad season the old head coach does so too (55 percent); every empty post is then filled, never with a coach the club has just let go. Coaches also retire (never before 60; 10 percent at 60, rising 15 points a year; certain at 66), and the year turn fills any post retirement empties. Dismissals fall only after a club's last game and hires are inside the window (§14 rules unchanged). **Teaching**: once a year at the offseason opening, a club's coaches add to or take from every attribute of each of its players (roster, practice squad or injured reserve) up to a fixed cap of half an attribute point, before the attribute-kind scale of development above; the effect is the mix of the head coach's and the position group's own coordinator's teaching rating (offence and its line: offensive coordinator; defence: defensive; kickers and long snapper: special teams), centred on 50, so an average staff changes nothing and the best possible staff against the worst is worth less than about one point of overall a year. Numbers are data (`data/characters/COACHING.csv`) and placeholders. The game-day coach bonus the engine reads (`Staff.bonus`, a capped bonus on the unit ratings) is separate and unchanged. The CEO and the general manager are elected and hired as set out under Governance below (Edition 8.27).
  - *Trades (Edition 8.26, owner's design of 2026-10-24).* **One common list**: the ledger keeps a single, always-current, searchable list of every rostered player with public facts only (club, position, age, credited seasons, contract, cap hit, years left, slot, injury status, market price); it never holds hidden attributes, true overall or any club's private valuation. **Public price (Edition 8.34, trades stage 2)**: the market price on the list, and every club's estimate of what another club will pay, is the price curve at a *public estimate of the player's overall*: the going level at her position among players within a year of her age (never including her own rating), moved by her analytical scores (§15) from her latest three scored seasons, each score worth `pub_slope` (4) overall points per standard deviation times n/(n+2) for n scored seasons; a player with no scored season is priced at the going level. It reads no hidden attribute of hers. **Private valuation**: a club values a player through its own scouting view (Edition 8.21), cut for age, raised or lowered by whether the club is short or deep at her position, less her cap hit, over up to three contract years; two clubs therefore value the same player differently, which is why a trade can suit both. **Trading block**: a club puts on a public block the players it values little (surplus depth, aging or overpaid players; at most six). **Protocol**: each time the trading step runs, a random subset of clubs goes after the player on another club's block that it values most (at or above a floor), builds an offer from the players it values less than the public does and then its picks, and proposes (round 1). The other club accepts if the deal gives it surplus above its margin; otherwise it counters by asking for one more asset (round 2), or rejects. The proposer answers a counter by accepting or rejecting (round 3). **At most three rounds**; at most three players and three picks from one side; a player is in at most one deal per run; there is no limit on how many deals result. **Margin**: every club wants a better deal, so each demands surplus above a margin that its general manager's greed and negotiation rating both raise. **Ledger check**: clubs do not propose what the rules would reject; every deal is dry-run through the ledger and then made in it, whole or not at all (windows, the next three drafts' picks, roster limits, jersey numbers, the position minimums). **Only players and picks move: no cash and no cap space is ever traded.** **Log**: every proposal, counter, rejection and deal is an event in a trade log that a run can replay. Deciders sit behind one interface: the autopilot general manager plays every club; later stages place language-model agents behind the same interface (`docs/TRADES.md`). **Volume (Edition 8.42, NFL rate)**: every club gets to propose in each trading window, and goes after her best targets in turn (up to `tries_per_club`, 4) until one deal is struck, at most one deal a club a window. This brings the league to about 70 trades a year for 48 clubs, 1.5 a club, against the NFL's 60 to 100 a year for 32 clubs (the 2023 draft alone had 41). The earlier random same-position swap stays available only for comparison runs. Numbers are data (`data/characters/TRADING.csv`) and placeholders.
  - *Governance: fans, recall, elections, the front office (Edition 8.27, builder's placeholders on the owner's §14 rules).* **Fan card**: each club's one million fans split into three viewpoints named results, investment and stability, with shares seeded between 20 and 50 percent and normalised (§14); each viewpoint holds a satisfaction from 0 to 100, starting at 50, and the club's **sentiment** is the share-weighted satisfaction. After each season a club played, every viewpoint closes a share of its gap to 50 and then moves by its own signal times a gain: *results* reads the club's win percentage against .500, *investment* reads the payroll against a reference share of the cap (spending well under the reference is the chronic underspending that replaced the salary floor, §11), *stability* reads how much of last year's core (its best-paid players) is still with the club. A club that did not play (exiled) is unchanged that year. **Recall**: after the last game, sentiment at or below the recall threshold (§0) calls a ballot; each viewpoint whose satisfaction is below a recall line votes to remove the CEO, and the CEO is removed if the viewpoints voting so hold a majority of the fans. **Elections**: each club elects a CEO every third season (clubs staggered so a third of the league votes each year) and whenever the office is vacant (recall, retirement). Candidates are the sitting CEO (unless recalled) and the willing CEO candidates in a CEO pool of at least four; each viewpoint votes for the candidate it rates highest (the candidate's own public rating plus an error of its own; for the sitting CEO plus a large familiarity bonus, so a CEO whose fans are content is almost never voted out, and a strong credit or blame by that viewpoint's mood, so an unpopular one still is), and the candidate with most fans behind her wins. A winner who is not the sitting CEO takes office in the next offseason, and the CEO she replaces joins the pool; a recall or term election leaves the sitting CEO in office until then (no midseason dismissal). **General manager**: the CEO hires her from a GM pool of at least four candidates by his own rating of her fit (evaluation, negotiation and roster building), the error shrinking with his governance rating; in the leadership window, before the coaching window, a season under the bad-season line gives the CEO a chance to dismiss her (lower for a patient CEO, by temperament), and she may resign (higher for the ambitious, lower for the loyal); an empty chair is filled at once from the pool, never with the manager just let go. general managers retire (never before 62; certain at 66), CEOs later (never before 74; certain at 82), and both wait at most four year turns in a pool; the year turn fills every empty chair, so every club starts every season with all six leaders. New pool candidates are weaker than the founders (CEO shift -1.3, general manager shift -0.7 talent deviations, chosen so the average of each stays near 60). **What the front office does to the game**: the general manager's evaluation and negotiation and the CEO's temperament act only through the paths already built (scouting views, trade margins, dismissal and hiring chances); the CEO's financial, governance and public-standing ratings affect only how fans rate him in an election; no new lever on rosters or the cap exists. **Discretion**: each general manager has a hidden discretion attribute (how tight-lipped she is) that feeds only the story layer; nothing in the engine or the ledger reads it, a test proves it, and it is drawn from its own random stream so adding it shifted no other card. Every record (ballot, election, dismissal, resignation, hire) is kept in a governance log for the story layer. Numbers are data (`data/characters/GOVERNANCE.csv`) and placeholders.
  - *Meeting rooms (Edition 8.29, owner's plan: independent parties get a say where they need one).* A **room** is a bounded exchange between named parties with a transcript (round, speaker, move, terms) and an outcome. The ledger holds every room to three rules: only a party may speak, a room has a round limit by kind (three for a contract or a trade, two for an interview) and a message beyond it is refused, and a closed room is never reopened. Rooms record; the engine never reads a transcript. Transcripts are kept for the last two league years (the trade log keeps all). Three rooms are built. **Contract room**: for a free agent who bargains (above), the player opens by asking a little above her demand (6 percent); the club's general manager counters, taking back a share of the ask that rises with her negotiating rating (at the middle rating the counter equals her demand; the best negotiator takes back 60 percent more than the middle rating does, the worst 60 percent less); the player accepts a counter at or above the least she will take (8 percent below her demand, a little less for the greedy and more for the unambitious); otherwise she holds at that least price in the last round and the club accepts if it can pay it. A club that cannot pay her least price has no deal. No negotiator can take her below her least price, and none pays above the club's own ceiling. **Trade room**: each negotiation of Edition 8.26 is a room whose messages are its proposal, counter, answer and any ledger refusal. **Interview**: to fill a post (a head coach or coordinator, a general manager) the club ranks the pool by its first impression, interviews candidates in that order until three have accepted, and picks the one it rates highest after interviewing, an interview shrinking its error in judging her to 60 percent. A candidate may decline: a small chance for anyone (5 percent) and, for the most ambitious, up to 30 points more against a club that won none of its games last season, scaled by how far below .500 it finished; the answer to a given invitation is the same whenever it is asked. A declined candidate stays in the pool. If every candidate declines the club takes its first choice. Numbers are data (`data/characters/ROOMS.csv`) and placeholders. The elections of §14 are not rooms.
  - *Market floor (builder's default)*: a free agent does not sign for far less than her market price; only players priced at or near the league minimum sign for it.
  - *Player demands (Edition 8.23).* A free agent whose market price is above the league minimum bargains; the rest sign for the minimum. Her demand from a club is the price of the overall she believes she has (her self-view, above), multiplied up by her greed and her ambition, multiplied down by her loyalty if the club is her last club, and lowered by a fixed share for each year she has waited unsigned in the pool; it is never below the league minimum. A club will pay up to the price of the overall it believes she has (its scouting view) plus a small slack. If her demand is within that, the deal is struck at her demand; if not, there is no deal with that club and she stays available to others and to later years. This replaces the placeholder behaviour where every signing was at the market price. Draft picks keep the rookie scale. All numbers are data (`data/characters/NEGOTIATION.csv`) and are placeholders. Since Edition 8.29 the negotiation is held in a contract room (Meeting rooms, below), which adds offers in rounds and the general manager's negotiating rating; the price at the middle rating is still her demand. **Club prices (Edition 8.35)**: whether a free agent bargains or takes the league minimum is decided by the signing club's own view of her overall (§13, scouting views), not by her true overall: a club that takes her for a scrub signs her at the minimum however good she is, and a club that takes her for a star negotiates with her, so clubs sometimes buy bargains and sometimes overpay; the minimum-price pools a club draws fill-ins from are drawn the same way.

## §14 Fans, governance, expectations
- Each club has a fan card representing one million fans. Three viewpoints are seeded between 20 and 50 percent and normalised.
- Sentiment returns gradually toward 50. After a club's last game, sentiment at or below 32 triggers a recall ballot; a fan majority removes the CEO. No midseason automatic dismissal.
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
- *Analytics as built (Edition 8.30, owner approved the builder's definitions on 2026-10-24).* **Production**: every pass, run, scramble and sack, and every field goal and punt, is valued in **expected points added** (EPA): the points scored in the play plus the expected points of the state it leaves, less those of the state it began in, for the offence; and in **win probability added** (WPA), the swing in the offence's chance of winning from the score, time left and the value of the state. The expected-points curve and the win-probability model are data (`data/analytics`) placeholders. **Season refit (Edition 8.32)**: the engine keeps, for every recorded play, its starting yard line, down and distance and the points scored in it; a play is worth the points of the next score in its half, from the offence's side (the scoring play counts itself; a half with no further score is worth 0), with the down and distance costs taken back out so each play estimates the first-and-ten value of its yard line. After each season the table is refit at its data points as a kernel-weighted mean of the last three seasons' plays (spread 6 yards), pulled toward the data table by 300 plays' worth of weight, and made non-decreasing in yards gained; the next season's plays are valued with it (the first season uses the data table). **The down and distance costs are refit too (Edition 8.33)**: the cost of each down after the first and of each yard to go beyond ten are the least-squares slopes of a play's next-score value on them within ten-yard bands of the field, pulled toward the data costs by the same 300 plays' worth of weight and held between 0 and 2 points a down and 0 and 0.3 a yard. The refit changes no result: it is read only when a play is valued. Each play's EPA and WPA are shared among the players on the field by role (data): on a pass the passer, the target, the linemen and the other receivers; on a run the carrier, the linemen, the tight end and the receivers who block; on a sack the passer and the linemen; the defence receives the opposite sign by its linemen, linebackers and backs; a field goal or punt goes to the kicker or punter and the long snapper. Everyone on the field for a play has the **opportunity**. Penalties, kneels, kickoffs and returns are not credited. Recording draws no random number and changes no result (a digest of 240 games proves it). **Season analytics**: within each position, among players who reached the **opportunity threshold** (a quarter of the median full-time workload of the position's starters league-wide), a player's **role execution** is her EPA above the **replacement baseline** (the EPA per opportunity of the position's players who passed the threshold but were not among its starters), **opponent-adjusted difficulty** is the same with each opportunity weighted by how good the opposing unit was that season (from the clubs' public production: a defence that allowed little, an offence that made a lot; a standard deviation moves the weight by half, held between 0.3 and 1.7), and **leverage** is her WPA. Each is standardised within the position that season, which is the era standardisation, and the score is 0.60, 0.25 and 0.15 of them. Numbers are data (`data/analytics/ANALYTICS.csv`, `SHARES.csv`, `EP.csv`).
- *Tiaras and halls as built (Edition 8.30).* A Tiara goes to the **Coronation winner's** final 53 and its CEO, general manager, head coach and three coordinators, once per person per season (owner, 2026-10-24). A player is eligible with a Tiara and five credited seasons, a credited season being a league year in which she was on a game-day active list or in a Tour game. The 59 halls (league, two conferences, eight divisions, 48 clubs) each hold, by official position, the All-Time All-22 slots (offence QB, HB, TE, 3 WR, LT, LG, C, RG, RT; defence LE, RE, 2 DT, ROLB, MLB, LOLB, 2 CB, FS, SS) and K, P, LS. A player's value in a hall is the sum of her season scores in seasons at a club inside it (the club she ended the season with). After each season the registers are re-seated: the best values hold the slots, a challenger equal to an incumbent displaces her, ties between challengers go by a seeded draw, and ever-membership is permanent. One fan entrant a year joins the league hall by instant-runoff ranked choice: the ballots are the 48 clubs' three fan viewpoints, each ranking the twenty best eligible players not yet in the league hall by career value, with its own error and a bonus for a player who played for the club. Staff hold Tiaras but have no halls yet. The cards of retired players are purged, so the halls keep each member's name and position.

- Records store person or club ID, event links, season, scope, unit and opportunity count.

## §16 Narration and viewer surface
- Six top-level entries: Home, Teams, Players, Schedule, Standings, Statistics.
- The viewer observes only; no control and no hidden data.
- Returning viewers receive a public-event digest since their last seen event. Reloading a page creates no new narrative state.
- Media is a mouth, never a causal actor. Private rumour chains are not simulated.
- Box score (Edition 8.47): every game has the final score and points by period; a scoring summary; team statistics (first downs, total and per-play yards, rushing, net passing, sacks taken, third and fourth downs, turnovers, penalties and yards, punts, possession time); and player statistics under each club: passing, rushing, receiving, defense (tackles, assists, sacks, tackles for loss, passes defended, interceptions, forced and recovered fumbles), kicking (field goals with the longest, extra points, kickoffs, touchbacks), punting (average, inside the 20), returns, and penalties by type. The box score shows names, positions, counts and yards only; no rating, attribute or hidden state.
- Play table (Edition 8.47): every event of a game (kickoffs, snaps, kicks, fouls, scores, timeouts, injuries, ejections, the two-minute warning) is one row of a structured table: the clock, down, distance and spot before the event, the event, the ids of the players in it, and what a viewer needs (first down, score, foul and who committed it). The play-by-play text is written from that table. Credit for tackles, sacks, interceptions and passes defended is drawn on a random stream separate from the game's, so keeping or dropping the table never changes a result. The full-rate trace of §17 is a second table, per play, for the top-down view; it comes with the spatial engine.
- Animation is a window onto the engine (Edition 8.50, owner's ruling of 2026-10-27): the 2D play animation is what the website shows of a game, and it is the engine's own trace of the snap, drawn; it never decides, alters or re-simulates a result. The score, the box score and the play table are the same numbers the animation shows. Because only the 22-player engine produces a trace, the animation can show a game only if the 22-player engine played it; no animation is made up for a game the play-level engine played. A viewer sees a game's animation if, and only if, its snaps carry traces.
- Default engine (Edition 8.50, owner's ruling of 2026-10-27): the 22-player engine becomes the league's default engine only once it passes all 24 bands of `data/engine/BANDS.csv`; until then the play-level engine is the default and the 22-player engine is opt-in. Met in Edition 8.52: the 22-player engine passes all 24 bands at the default check (seed 1, 1,000 games) and at seeds 14 and 15 (1,500 games each), and is now the league's default engine (`Universe(spatial=True)`); the play-level engine stays available for tests and fast soak runs (`spatial=False`, `--play-level`). Traces are not yet kept for league games (item 49).

## §17 Persistence and determinism
- One authoritative simulation process. Every event carries an ID, simulated time, deterministic ordering key, rule version, domain-specific random key, participants, visibility and resulting state changes.
- Reserve/commit ensures money, picks, roster places and contracts cannot be assigned twice.
- Full-rate traces record positions, ball state, intent references and decisive events at 0.05 s with lossless chunk compression. Rendering is a flat top-down chalkboard with medallion sprites and a persistent geometric intent overlay.
- Traces are stored as played (Edition 8.51, owner's ruling of 2026-10-27): every snap's trace is written to the game archive when the game is decided and is kept permanently, unchanged, beside the game's data and the players' portraits. An animation is drawn only from a stored trace, never regenerated from a seed, so a later change to the engine can never make an old game show a play that did not happen. The trace is stored with the rule version and engine version that played it. The archive grows by about half a gigabyte a season at the format of Edition 8.48 (measured, about 4 KB a snap); the owner accepts paying for the storage. A tighter encoding may be adopted later only if it is lossless and the stored traces decode to the same positions.
- The year-end handoff carries season ID, next season ID, seeds and exiles, living references, contracts and schedules, pending waivers and obligations, owned picks and lottery identity, relationships and memory summaries, medical state, governance state, fan cards, future fixtures and the saved Week 4 Coronation designation, career records and honour references, random counters, the next event queue and the rule version.
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

**STANDINGS** — `tests/test_standings.py` passes: tie handling (standings versus statistics), the cascade including the wins-before-win % order and the point-differential, strength-of-schedule, strength-of-victory order, the division and conference step skips and the sorted-once rule, the seeded draw, six-club playoff seeding and the reseeded bracket, exile and seed hand-off, the Week 4 conference designation (wins, point differential, aggregate strength of schedule and of victory, the seeded draw, ties, only weeks 1–4 read), and invariants over 150 simulated seasons on the real schedule (every club 18 games, wins equal losses, point differentials sum to zero, input order irrelevant).

**CROSS-FILE** — every Schedule and Tour city resolves to exactly one `team_city` (a stadium town such as Minneapolis does not); no ambassador appears in the Schedule; the eight ambassadors equal the eight `lottery_1_8` clubs; gemstone hexes match between `TEAMS` and `COLOR_REGISTRY`.

**CAP** — for every club-state in a multi-season run, at least one legal transaction exists; dead money never exceeds $735; the positional minimums total 48; the viability margin is logged every club-year.

## §19 Changelog and supersession policy
- One normative block, fenced with an edition stamp, date and content hash. Edition 8.55 supersedes Editions 8, 8, 8.54.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9, 8.10, 8.11, 8.12, 8.13, 8.14, 8.15, 8.16, 8.17, 8.18, 8.19, 8.20, 8.21, 8.22, 8.23, 8.24, 8.25, 8.26, 8.27, 8.28, 8.29, 8.30, 8.31, 8.32, 8.33, 8.34, 8.35, 8.36, 8.37, 8.38, 8.39, 8.40, 8.41, 8.42, 8.43, 8.44, 8.45, 8.46, 8.47, 8.48, 8.49, 8.50, 8.51, 8.52 and 8.53 and everything prior.
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
| 2026-10-22 | §3 | playoffs at the higher seed's home venue; Coronation venue by fictitious fan vote, no repeat within 40 years (the Coronation venue part is superseded by §3 @ ed.8.55) | owner's rule | 8.4 |
| 2026-10-22 | §0, §11, §18 | required places 36 → 47; Bowl discretionary places 17 → 6; dead-money ceiling $795 → $740 | the owner's positional minimums total 47 | 8.4 |
| 2026-10-22 | §11 | positional minimums added | owner's list | 8.4 |
| 2026-10-22 | §20 | rotation, postseason-venue and league-minimum items closed; new items added; renumbered | resolved by the owner | 8.4 |
| 2026-10-22 | §0, §11, §18 | long snapper added to the positional minimums; required places 47 → 48; Bowl discretionary places 6 → 5; dead-money ceiling $740 → $735 | owner's addition | 8.5 |
| 2026-10-22 | §3 | Coronation venue drawn from the 48 DFL club venues; a finalist whose venue wins plays at home | owner's rule | 8.5 |
| 2026-10-22 | §20 | wins-before-win% order, long snapper, Tour template and bed-order items closed; tie-frequency calibration item added | confirmed by the owner | 8.5 |
| 2026-10-22 | §3 | a tied game is not played in the standings but is played for statistics (games, points, point differential) | owner's rule | 8.6 |
| 2026-10-22 | §3 | Coronation vote decided by a seeded random draw among the nine eligible venues (superseded by §3 @ ed.8.55: permanent venue, no vote) | owner's rule (simplest method) | 8.6 |
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
| 2026-10-23 | §10, §20 | engine build order: rules core and play-level resolver first, spatial engine later behind the same interface; items 2 and 7 updated with slice-1 results | owner chose the recommended path | 8.16 |
| 2026-10-23 | §20 | tie-game frequency item closed: the measured rate (about 1.5% of regular-season games) is accepted and the overtime rule stands | owner's ruling ("accept the rates") | 8.17 |
| 2026-10-23 | §13, §20 | development, aging and retirement defined and built on placeholder curve data; the engine squad is the ledger's game-day active list; market floor default; open items 19 and 20 added | owner said "start away" on connecting cards to the ledger; curves are the builder's placeholders pending a ruling | 8.18 |
| 2026-10-23 | §12, §20 | returner's second draft: a club in its first season back that misses the playoffs picks as a block at the end of the non-playoff clubs; item 13 updated, item 21 added | owner said "yes please" to restoring his 2026-10-04 v1 ruling after the fairness bands showed 24% of returners with a top-16 pick (limit 10%) | 8.19 |
| 2026-10-23 | §20 | fairness band for win-percentage spread: ceiling 0.19 raised to 0.21 | owner's ruling, on the builder's recommendation, after the living-league measurement showed 0.207 | 8.20 |
| 2026-10-23 | §13, §20 | club scouting views: every club decision about players uses a noisy, evidence-shrinking, evaluator-scaled estimate instead of true ability; items 20 and 22 | people layer, first slice (players first, per the roadmap); owner asked for the people layer after the rest of Phase 4 | 8.21 |
| 2026-10-23 | §13, §20 | souls: fixed archetype and bounded yearly trait drift; self-view; item 23 | people layer slice 5.2; owner chose the placeholder archetype set | 8.22 |
| 2026-10-23 | §13, §20 | player demands: free agents bargain from self-view, greed, ambition, loyalty and time waiting; clubs pay up to their own view; item 24 | people layer slice 5.3 | 8.23 |
| 2026-10-24 | §13, §20 | coaches: capped yearly teaching of players, coach retirement, one pool and a hiring pass; item 25 | people layer slice 5.4; owner approved the capped-effect proposal | 8.24 |
| 2026-10-24 | §13, §20 | coaches restructured to the owner's design: one category, posts held not born into, one common pool incl. fired and resigned coaches, CEO-over-HC and HC-over-coordinators hiring and firing, leadership window; item 25 | people layer slice 5.4, owner clarification | 8.25 |
| 2026-10-24 | §13, §20 | trades stage 1: common public player list, private club valuation, trading block, three-round protocol with autopilot general managers, ledger dry-run, trade log; no cash or cap space; item 26 | owner approved the staging, picks yes, no cash, no cap-space trades, three players a side | 8.26 |
| 2026-10-24 | §13, §20 | governance: fan cards and sentiment, recall ballots, CEO elections every third season and on vacancy, CEO and general-manager pools, general-manager dismissal and hire, hidden narrative-only discretion; item 27 | owner's slice 5.5 request on the §14 rules; discretion narrative-only (owner 2026-10-24) | 8.27 |
| 2026-10-24 | §13, §20 | governance: recall rate set to about one a year; CEO financial rating and general-manager relationships stay out of the engine; item 27 | owner's rulings of 2026-10-24 | 8.28 |
| 2026-10-24 | §13, §20 | meeting rooms: contract room (rounds, general manager's negotiating rating), trade room, interviews with candidate consent; final-cut numbering repair; item 28 | owner's slice 5.6 request | 8.29 |
| 2026-10-24 | §15, §20 | analytics definitions built (EPA and WPA by role, threshold, baseline, opponent-adjusted difficulty, leverage, the 60/25/15 score); Tiaras to the Coronation winner; the 59 halls with All-Time registers, ever-membership and the fan entrant; item 1 resolved, item 29 | owner approved the definitions, the Coronation-winner reading and the general manager's negotiating lever on 2026-10-24 | 8.30 |
| 2026-10-24 | §0, §13, §20 | front-office turnover: recall threshold 20 to 25.5 (about two recalls a year); bad season below 40 percent wins; head-coach dismissal 45 percent, coordinator dismissal after a bad season 55 percent, a new head coach's 75 percent, resignation 5 percent, general-manager dismissal 30 percent and resignation 4 percent, tuned to NFL turnover rates; item 30 | owner's ruling of 2026-10-24 | 8.31 |
| 2026-10-25 | §15, §20 | expected-points table refit each season from the league's own plays (next-score value, three-season window, kernel smoothing, pull toward the data table, monotone); recording stays result-neutral; item 31 | recommended by the builder, approved by the owner on 2026-10-25 | 8.32 |
| 2026-10-25 | §15, §20 | expected-points refit also reads the down and distance costs from the plays; item 31 | owner's consent of 2026-10-25 | 8.33 |
| 2026-10-25 | §0, §13, §20 | CEO tenure: incumbent bonus 4 to 20, mood weight 10 to 25, CEO retirement from 74 and certain at 82 (was 62 and 66), CEO mean age 46, recall threshold 25 to 25.5, CEO turnover 5 to 10 percent a year; item 32 | owner's ruling of 2026-10-25 | 8.33 |
| 2026-10-25 | §13, §20 | trades stage 2: the market price and a proposer's estimate of another club's valuation come from a public estimate of overall (going level at her position and age, moved by her analytical scores), not from the hidden overall; item 33 | builder's recommendation, owner's consent of 2026-10-25 | 8.34 |
| 2026-10-25 | §13, §20 | club prices: whether a free agent bargains or takes the league minimum follows the signing club's own view of her overall, not her true overall; trade price item 33 corrected; item 34 | builder's recommendation, owner's "ok" of 2026-10-25 | 8.35 |
| 2026-10-25 | §13, §20 | coach judgement: game-day starters follow the coaches' judged overall, true overall plus an error with no lean, smaller with more seasons of evidence and with a better head coach and coordinator; item 22 closed; item 35 | owner's ruling of 2026-10-25 | 8.36 |
| 2026-10-26 | §11, §20 | compensatory-pick top-quarter ranking follows the coaches' judged overall, not the true overall; items 22 and 36 | builder's recommendation, owner's go-ahead of 2026-10-26 | 8.37 |
| 2026-10-26 | §13, §20 | aging: each position has its own rate of decline after the peak; players retire from wear at any age, under contract or not, and a retiring player's contract ends with no dead money (items 3, 19 closed or reframed, 37, 38) | owner's go-ahead and rulings of 2026-10-26 (rates follow the NFL) | 8.38 |
| 2026-10-26 | §11, §20 | upgrade signings at camp: a club signs a clearly better free agent over her weakest starter, at most three a camp; item 38 built; a candidate can win only one club's CEO election a year; item 39 | owner's ruling of 2026-10-26 that rates follow the NFL's; builder's recommendation | 8.39 |
| 2026-10-26 | §0, §13, §20 | recall threshold 27 to 33 so recalls stay at about two a year after the aging changes; item 40 | owner's ruling of 2026-10-24, re-tuned by the builder | 8.40 |
| 2026-10-26 | §20 | third NFL-rate pass on age: quarterback draft weight 2 and peak 30, offensive-line and running-back decline, long-snapper peak; item 39 | owner's ruling that rates follow the NFL | 8.41 |
| 2026-10-26 | §13, §20 | trade volume to the NFL's rate: every club proposes each window and tries her best targets in turn; item 41 | owner's ruling that rates follow the NFL | 8.42 |
| 2026-10-26 | §0, §13, §20 | recall threshold 33 to 30 after trade volume rose; item 42 | owner's ruling of 2026-10-24 (about two recalls a year; CEO turnover 5 to 10 percent) | 8.43 |
| 2026-10-26 | §0, §13, §20 | recall threshold 30 to 32 (30 gave 1.56 recalls a year, 33 gave 2.53); item 42 | owner's ruling of 2026-10-24 (about two recalls a year) | 8.44 |
| 2026-10-26 | §11 | Phase 1 closed: the legal-move enumerator; a 100-year ledger soak and a 100-year full-league soak on the current rules, both passing with no forfeit and no club without a legal move; 15 rule breaks all caught by tests | owner's go-ahead of 2026-10-26 to complete Phase 1 | 8.45 |
| 2026-10-26 | §10 | Penalty catalogue grown from 10 to 46 fouls at the NFL's accepted rates by type (2023), fouls charged to the player who commits them, enforcement rules (loss of down, after-play fouls, kicking-play fouls, ejection), two-minute warning, implementation coverage register | owner's go-ahead of 2026-10-26 to finish Phase 3; the NFL's rule where §10 is silent | 8.46 |
| 2026-10-26 | §16/§10 | Box scores, scoring summary, structured play table and play-by-play text; individual defensive, kicking, punting and return statistics; statistics conventions (touchdown yards to the goal line, kneel-downs, half-the-distance rule corrected) | owner's go-ahead of 2026-10-26 to finish Phase 3 (Phase 3 slices 5 and 6) | 8.47 |
| 2026-10-26 | §10 | Spatial engine specified: field, players as moving points, formations, plays and routes as data, coverage, blocking engagements, passing with ball flight and contested catches, tackling, traces | owner's go-ahead of 2026-10-26 to finish Phase 3 | 8.48 |
| 2026-10-27 | §10 | Spatial engine calibrated and opened for use (opt-in): quarterback pressure response, perceived separation with closing defenders, third-down marker awareness, situational calls, alignment fix for mirrored man coverage, home and coaching edge, arm tackles by engaged defenders, movement limits; status and use stated | owner's instruction of 2026-10-26 to finish Phase 3 | 8.49 |
| 2026-10-27 | §16, §10 | Animation fixed as a read-only window onto the 22-player engine's trace; the 22-player engine becomes the default only at 24 of 24 bands | owner's ruling of 2026-10-27 | 8.50 |
| 2026-10-27 | §17 | Traces stored permanently as played; animations drawn only from stored traces, never regenerated | owner's ruling of 2026-10-27 | 8.51 |
| 2026-10-28 | §10, §16 | 22-player engine closes its seven missed bands (24 of 24 at the default check) and becomes the league's default engine; fairness re-measured with it | owner's ruling of 2026-10-27 (item 48) satisfied | 8.52 |
| 2026-10-29 | §7 | Bowl replacements are the next-ranked medically available players of the same conference's four Ambassador clubs without rerunning the count; exhausted ballots still fill the place with a new distinct player (permanent position, emergency game assignment and the one exception to positional minimums kept apart); rings go to the final 53 present and medically ready at kickoff | owner's ruling of 2026-10-29 (item 4) | 8.53 |
| 2026-10-29 | §11 | defaults confirmed: game-day active 48 (47 with fewer than 8 offensive linemen), two elevations a game, three a player a season, four-game injured-reserve minimum, eight designated returns; inactive players replace injured players only before the roster locks; the emergency list covers every position, uses only dressed medically available players as injuries happen and never puts a player in two positions at once | owner's ruling of 2026-10-29 (item 8) | 8.53 |
| 2026-10-29 | §3 | higher strength of schedule and of victory rank higher, no decisions = 0, cascade unchanged; the last step is one stable seeded priority per club per season, the same in every ranking; draft order is its exact reverse | owner's ruling of 2026-10-29 (item 9) | 8.53 |
| 2026-10-29 | §2, §12 | offseason dates approved after checking against the leadership, contract-expiry and 31 December honours rules; Coronation-to-1 January gap stated correctly (0 to 6 days); the ledger rejects trades outside the window | owner's ruling of 2026-10-29 (item 14) | 8.53 |
| 2026-10-29 | §7, §20 | Bowl ballots and final-53 construction marked incomplete (item 4 is ruled for replacement and rings only) | owner's instruction of 2026-10-29; no rule changed | 8.54 |
| 2026-10-30 | §3, §0, §2 | playoffs: seven clubs a conference → six (four division winners, two wild cards), seeds 1 and 2 on byes, 3 hosts 6 and 4 hosts 5, divisional round reseeded (1 hosts the lowest survivor, 2 the other), higher remaining seed hosts the conference championship; four-round calendar kept; 14 clubs and 13 games → 12 clubs and 11 games | owner's ruling of 2026-10-30 (new approved design change 1) | 8.55 |
| 2026-10-30 | §3, §2, §10 | Diamond Coronation: venue vote, rotation and 40-year window removed; permanent neutral venue Amethyst Aloha Stadium, owned and renovated by the DFL; no home-field edge; the year has 47 dated events, not 49 | owner's ruling of 2026-10-30 (change 2) | 8.55 |
| 2026-10-30 | §3, §9, §10 | nominal home of the Coronation designated after Week 4: interconference games of weeks 1–4 pooled into two aggregate conference teams ranked by the cascade without its division and conference steps (aggregation of strength of schedule and victory defined), the winner's finalist is nominal home, frozen and saved; it controls only the kickoff and uniform rules | owner's ruling of 2026-10-30 (change 3) | 8.55 |
| 2026-10-30 | §3 | cascade: point differential moves ahead of strength of schedule and strength of victory (wins, win %, division, conference, point differential, strength of schedule, strength of victory, seeded draw), league-wide | owner's ruling of 2026-10-30 (change 4) | 8.55 |
| 2026-10-30 | §4, §5, §6, §9 | team data reconciled with the owner's workbook of 2026-10-30: Eastern divisions Midwest, Northeast, East → Great Lakes, Atlantic, Midland (Southeast unchanged) and bed order; club, venue and gemstone changes (Havana, Peridot/Topaz swapped between the Northwest and Southwest, Turquoise for Midland); team_id preserved on every club | owner's ruling of 2026-10-30; details in `docs/WORKBOOK_RECONCILIATION_20261030.md` | 8.55 |
| 2026-10-30 | §11 | first-round fifth-year option is the club's decision by its general manager before Draft Round 1 of the player's fourth contract year; exercising adds a fully guaranteed $35 year; decisions saved | owner's ruling of 2026-10-30 (L3) | 8.55 |
| 2026-10-30 | §11 | split release: the release year's own bonus portion stays in that year, only the later portions divide 50/50 | owner's ruling of 2026-10-30 (L2) | 8.55 |
| 2026-10-30 | §11 | waiver priority retained provisionally; "prior draft slot" is the club's own slot, never a traded pick | owner's ruling of 2026-10-30 (L1) | 8.55 |
| 2026-10-30 | §11, §20 | extensions and postseason injury replacements included in scope, rules proposed (not built); restructuring a future year's base deferred | owner's ruling of 2026-10-30 (L4) | 8.55 |
| 2026-10-30 | §7, §20 | Bowl ballots and final-53 construction defined and built (voters, candidates, judged-overall ballots, 48 positional counts and one 5-place discretionary count, weighted Gregory Droop count with seeded ties, the order of a count, lock and repair, rings); the Bowl no longer plays composite sides when the ledger is present; the 2026-10-29 incomplete marker is retired | owner's instruction of 2026-10-30 (continue to Step 8); builder's readings listed in §20 item 4 for ratification | 8.56 |

## §20 Open items
1. Analytics definitions: approved by the owner on 2026-10-24 and built in Edition 8.30 (§15); every number is a placeholder (item 29).
2. Engine content: play catalogue, numeric route landmarks, blocking rules, coverage responsibilities, ball flight, injury and fatigue models. Slice 1 (play-level resolver, clock and rules core, injuries, calibration harness) is built; formations, blocking, coverage, ball flight, fatigue and challenges remain for the spatial engine.
3. Negotiation and market-clearing mechanics. Development, aging and retirement are built (§13, Edition 8.18; position decline and wear retirement in Edition 8.38, item 37) on placeholder curves in data; the owner has not yet ruled on their numbers, but ruled on 2026-10-26 that rates follow the NFL's.
4. **BUILT; readings to ratify (Edition 8.56).** Ruled 2026-10-29 (Edition 8.53, §7): Bowl roster repair when a position runs short, and who receives rings. Built 2026-10-30 from the owner's outline (per-position ranked ballots, Droop quota, seeded ties, 48 positional and 5 discretionary places, eight head-coach voters): the sequence in §7 items 1 to 9. The owner has not yet ruled on these builder's readings, which can each be changed without touching the others: (a) all eight head coaches vote on both conferences, once, right after the last Tour round; (b) candidates are the four clubs' 53-player rosters, not injured reserve or the practice squad; (c) ballots rank by the voter's coaches' judged overall with no lean towards her own club; (d) the backfield is counted as three halfbacks, so a fullback can only win a discretionary place; (e) the discretionary field is every candidate not elected by position and is ranked by overall alone, so overalls are treated as comparable across positions; (f) the count is the weighted inclusive Gregory transfer with exact fractions; (g) the order used for replacements is the order of the count (elected, then reverse elimination); (h) players who joined after the vote are never chosen; (i) all 53 are dressed; (j) rings go to the whole locked roster. Not claimed: that the vote picks the players a real league would; the judged overall is a placeholder error model. The older composite All-Star sides remain only in the standalone harness that has no ledger.
5. The real-time to simulated-time mapping under deterministic pacing (target about 50 hours a season, non-binding).
6. §19 promises a stable ID on every rule; none are assigned yet, so the changelog uses section references.
7. Tie-game frequency: ties are excluded from records, so the order of overall wins before overall win % matters only when ties occur. The owner accepted (2026-10-23) the roughly 1.5% of regular-season games the slice-1 engine produces under the ten-minute overtime with a possession for each side; the overtime rule stands as written. Re-measure with the spatial engine; the rate is a monitored figure, not a target.
8. Fill-in parameters ruled 2026-10-29 (Edition 8.53, §11): game-day roster 48/47, two practice-squad elevations a game and three a player a season, four-game injured-reserve minimum, eight designated returns. Still open: the engine's out-of-position penalty for an emergency assignment (item 2; the assignment itself is built).
9. Ruled 2026-10-29 (Edition 8.53, §3): higher strength of schedule and strength of victory rank higher; a club with no decisions has a win % of 0; the last resort is one seeded priority per club per season, the same in every scope.
10. Calibrate the pre-cut cap count (§0) once the market exists: it is adapted from the NFL's top 51 of a 90-player camp roster, and the ratio does not carry over exactly. (The rookie reserve, §11, is built and replaces the open question about rookie room.)
11. Floor removal relies on the sentiment and recall mechanism pricing poor spending and poor results; its inputs are unbuilt (people layer). Payroll spread across clubs is a fairness-band candidate. The sentiment inputs are built as of Edition 8.27 (item 27).
12. Emergency cap borrowing (owner proposal): deferred. The viability invariant is built so that no legal roster is ever unreachable; revisit only if a real jam appears once the market exists.
13. Exile-return loop: a club exiled as last place returns the next year as seed 5 with a lottery pick in slots 1-8; whether this traps weak clubs in a cycle, and whether extra picks for exiled clubs would help or would reward losing, is measured by the fairness bands (`docs/FAIRNESS.md`, first pass on the living league: no trap, returners finish near the league average). The second-draft block above closes the double-pick finding.
14. Ruled 2026-10-29 (Edition 8.53, §2, §12): the offseason dates are approved. Real-time pacing of the calendar stays deferred (item 5).
15. Character attributes, their means and the age curve are placeholders until the engine (Phase 3) says which attributes it uses and how; the attribute list and every distribution are data in `data/characters/` and may change. Overall ratings are not yet comparable across positions (a calibration job for Phase 3/4).
16. Appearance: the creation look is immutable; whether and how a character visibly ages is decided in presentation (Phase 7), by redrawing from the stored look.
17. Leader pools and hiring (four willing candidates per role, §13) use the same creator and the four-year pool rule; the hiring market itself is people-layer work (Phase 5).
18. Jersey numbers: none open. The same-club re-signing reclaim and lowest-open assignment are the builder's defaults (change on request).
19. Retirement of a player under contract (closed by Edition 8.38): a player under contract may retire from wear; her contract ends and the club is charged nothing. The NFL's own treatment is that a retiring player's unpaid salary is not owed and a club may recover bonus money already paid; the DFL takes the simpler half and recovers nothing.
20. Talent economy: the free-agent pool stays large (about 2,600 characters in the living-league measurement, mostly undrafted draftees and released veterans waiting out the four-year rule), the stand-in clubs choose players by their scouting views (§13, Edition 8.21) with placeholder error sizes, and prices are a placeholder curve. Whether the pool size, the draft-to-veteran talent gap and rookie-scale surplus keep the league fair is the Phase 4 measurement.
21. Fairness bands (Phase 4): v1's 15 working limits are ported as data (`data/fairness/BANDS.csv`) and measured on the living league (`docs/FAIRNESS.md`). One change: the ceiling on win-percentage spread is 0.21, not 0.19 (owner's ruling 2026-10-23; the NFL's own spread is about 0.19 to 0.21, and the engine's 0.207 was not to be tuned toward a limit stricter than real football). Every band must be re-run when the people layer replaces the stand-in clubs.
22. Coaches' view (closed by Edition 8.36, owner's ruling of 2026-10-25): starters are chosen by the coaches' judged overall with a symmetric error (§13, Squads). The size of the error (3.5 points with no evidence, the 0.7 evidence rate, the half-strength staff effect, the 0.8 floor, the equal mix of head coach and coordinator) are placeholders. The all-star sides and the autopilot's market-rating sort when no club asks still read true ability; the year-roll ranking of a club's best players, which feeds compensatory picks, moved to the coaches' judged overall in Edition 8.37 (item 36).
23. Souls: the archetype list and centres, the self-biases and the drift pull, noise and bound are placeholders. The nearest-centre rule gives uneven shares (about 30 percent quiet professional, 5 percent loyalist) because the centres are not equally spread; the owner may prefer fixed shares drawn at creation instead. Self-view has no consumer until the negotiation slice. Leaders' traits do not drift yet (coaches, general managers and CEOs belong to later slices of the people layer).
24. Player demands: the effects of greed, ambition, loyalty and waiting, and the club slack, are placeholders. A player does not yet weigh the club (its record, its facilities, its other players) against the money. Players do not re-sign before the pool; her last club competes with the rest only through the loyalty discount. Contract length and shape are still the autopilot's random choice, not a negotiated term. The contract room (Edition 8.29) negotiates the yearly price only; it has no agents and no negotiation by the club's other people, and the player has no negotiating rating of her own.
25. Coaches: no contract, salary, offers or negotiation for a coach (hiring is by the club's rating alone, and a coach takes any post offered); a coach's preferences for a club play no part; firing is triggered by win percentage alone (no unit statistics yet), and the CEO's and head coach's patience is a single temperament term; poaching of employed coaches is not built (a coach reaches another club by resigning or being let go, ambition raising the chance of resigning); the CEO's influence on and dismissal of the general manager, and CEO elections and fan recalls, are built in Edition 8.27 (item 27). The coordinators' three profiles are identical in weights, so a coach is equally good as offensive, defensive or special-teams coordinator until scheme side is modelled. The cap of half an attribute point a year, the 40/60 head-coach and coordinator mix, every probability above, the retirement ages and the weaker pool (shift -0.7 talent deviations, chosen so the average coach stays flat over 60 years) are placeholders for the owner.
26. Trades, stage 1: the market price now comes from public evidence (item 33); a proposer estimates the other club's valuation from that public price, not from the other club's view; no multi-asset counters beyond one extra asset; no three-way trades; no relationship, loyalty or trust effects between general managers yet; language-model deciders are stage 3. Every number in the trading data file is a placeholder.
27. Governance: the three viewpoint names and meanings, every gain, the pull toward 50, the reference payroll and core shares, the recall line, the incumbent bonus, the voters' error, the pool shifts and the retirement ages are placeholders. The viewpoints are the builder's reading of §14, which names none. With the recall threshold at 27 and the recall line below the middle, a ballot almost always carries, so the line adds little over the threshold; the recall rate is set to about **two a year** across the league (owner's ruling of 2026-10-24, replacing the earlier about one a year). Exiled clubs have no season and so no sentiment update and no election that year. Voters judge a candidate by her three CEO attributes only (public standing); the CEO's financial rating does not move the club's spending, and general-manager relationships (trust between two managers) do not shade trades: both would be fairness channels, so the owner accepted the builder's recommendation (2026-10-24) that they stay out of the engine; any relationship between general managers is narrative-only. CEOs and general managers have no contracts or pay, are not poached, and the CEO has no term limit or age beyond the retirement rule. The story layer that reads the governance log and the discretion attribute is unbuilt.
28. Meeting rooms: every number in the room data file is a placeholder. The general manager's negotiating rating moves what a club pays by a few percent at most, a lever on payroll the fairness bands watch (the owner approved it on 2026-10-24). Interviews give candidates a limited say; a candidate has no preferences about a club beyond its last record and her own ambition, and no offer of pay. Trade and contract rooms are conducted by the autopilot; pluggable deciders and language-model agents (docs/TRADES.md, stage 3) would sit behind the same room interface. Analytics definitions (item 1) and the halls are built in Edition 8.30 (item 29). Also in this edition: the final cut repairs a jersey-numbering shortfall (a rare ledger case found in a long run) by shedding a surplus player at the position instead of stopping the league.
29. Analytics and halls: the expected-points curve, the down and distance costs, the win-probability model, the role shares, the threshold share, the difficulty gain and every other number in the analytics data files are placeholders; the expected-points curve and the down and distance costs are refit each season (Editions 8.32 and 8.33). A season is valued with the table refit from the seasons before it, so the first season uses the data table and a change in the league's style shows a season late. The defence is credited by role group, not by matchup, so a lineman on a strong line gets the line's credit; the credit to linemen is the same for each of the five. Special teams other than kicks and punts (returns, coverage, blocking) are not credited. A player's club for a hall is the club at the end of the season. Staff have Tiaras but no halls, and have no analytic score. The fan ballot's "runners-up" is the builder's reading (the best eligible players outside the league hall), and its voters, bonus and error are placeholders. Credited seasons count a game-day active-list place, not a play. Hall records do not yet store event links or opportunity counts for every register change; the log has year, scope, position, person and kind. Recording adds to the cost of every game.
30. Front-office turnover against the NFL (Edition 8.31, owner's ruling of 2026-10-24: recalls about twice a year, the rest at or near NFL rates). Targets, each a yearly share of clubs that change the post: head coach about 21 percent (the NFL averaged 6.8 head-coach changes a year over 2004 to 2013, 21 percent of 32 clubs), general manager about 15 to 20 percent (about 14 percent a year were dismissed over five recent years, plus resignations and retirements), coordinators about 30 to 35 percent (offensive-coordinator changes ran from 10 of 32 clubs in 2017 to 18 in 2018 and 16 in 2019; defensive and special-teams figures were not found and are assumed alike). The NFL figures are public press counts, not a full census, and the coordinator band is the least certain; the builder treats 'close to' as within about five points. A season under 40 percent wins is now the bad season (was 30). CEO turnover has no NFL match (the DFL has fan recalls and term elections that the NFL's owners do not); the owner ruled on it on 2026-10-25, see item 32. Measured rates are in docs/FAIRNESS.md. Every number is a placeholder (items 25 and 27).
31. Refit of the expected-points table: the three-season window, the 6-yard spread and the 300-play pull toward the data table (`ep_window`, `ep_bandwidth`, `ep_prior_n`) are placeholders; the next-score reading of a play's value, ignoring the point-after kick and the clock, is the builder's. On 1,600 sampled games the refit rose above the data table by about 0.6 points at midfield, the league scoring more than the placeholder assumed. The costs are read from the plays by least squares within ten-yard bands, which treats every down and distance at a yard line as one population; teams choose to kick on fourth down, so the fourth-down cost reflects their choices. On 1,200 sampled games the down cost came out 0.69 a down (data 0.55) and the distance cost 0.064 a yard (data 0.05).
32. CEO tenure (owner's ruling of 2026-10-25: CEO turnover 5 to 10 percent a year, the CEO the longest-tenured post, accountability kept). Turnover has three parts: recalls (about two a year across the league, 5 percent of clubs, by the earlier ruling), term elections lost, and retirements. To fit the band the incumbent's familiarity bonus is 20 points (was 4), the weight of a viewpoint's mood on the incumbent 25 points (was 10), a CEO retires from 74 and is certain to at 82 (was 62 and 66; general managers unchanged), and CEOs are drawn younger (mean age 46, was 50), because a CEO kept in office until retirement already turns the post over about every 25 years; with the recall threshold moved from 25 to 25.5 to hold recalls at about two a year (and to 27 in Edition 8.35, when clubs pricing free agents by their own view made the league more even, 0.193 win-percentage spread against 0.21, and recalls fell to 1.6 a year at 25.5 and at 26). Measured on six leagues of 36 seasons, CEO turnover was 9.8 percent of clubs a year, down from about 21: 5.3 percent by recall (2.1 recalls a year), 1.5 percent by lost term elections and 3.3 percent by retirement (which runs high in the measured window because the founding CEOs all age together). Of term elections the sitting CEO lost 19 percent when sentiment was under 35, 7 percent at 35 to 45, 4 percent at 45 to 55 and 1 percent above 55, and a CEO whose fans fall to the recall threshold is removed by ballot. Accountability now rests mostly on recalls, about three quarters of the removals other than retirement; elections are close to a formality for a CEO with content fans. Because the weak are voted or recalled out and the rest stay long, the average CEO now runs a few points above the founding CEOs (about 64 against 60 on financial rating in a 24-club test). The CEO age ceiling of 82 and every number here are placeholders; raising the bonus further would make elections empty, lowering it takes turnover back above 10 percent. Measured rates are in docs/FAIRNESS.md.
33. Public trade price (trades stage 2, Edition 8.34): the three-season window, the 4-point slope, the 2-season doubt, the one-year age window and the minimum group of 8 are placeholders. Measured over eight seasons, the public estimate correlates 0.34 to 0.48 with a player's true overall within a position, against 0.64 to 0.72 for a club's own scouting view, and its spread is 2.3 to 3.1 points against a true spread of 6.2 to 6.9: the market is a coarse guide, as it should be, and a proposer misjudges what another club will pay. Single-season scores predict true overall weakly (correlation 0.25 over all positions; 0.57 for quarterbacks, 0.47 for halfbacks, about 0 for kickers, long snappers and middle linebackers), and the estimate does not yet weigh positions differently. The going level reads the true ratings of the other players at a position in aggregate, which a real market knows only roughly. Contract prices were already set by a player's self-view against a club's own view (Edition 8.23); what still read the true overall was only whether a player bargains or takes the minimum, which Edition 8.35 moves to the club's view (item 34).
34. Club prices (Edition 8.35): a club's view of a free agent decides whether she bargains or signs at the minimum, and its fill-in pools (`quote`); the true overall (`ask`) is used only where no club is asking (the league's own bookkeeping, the market-rating sort when no club is looking, and the diagnostic of how far deals fall from the true market). Game-day starters are still chosen by true overall (item 22), and so are the ledger's cut-down ranking (`ledger.py`) and the autopilot's use of the market rating for ranking the free-agent list when no club asks; those are the remaining reads of true ability. Measured with these settings on six leagues of 36 seasons: all 15 fairness bands pass (win-percentage spread 0.193), recalls 2.04 a year, CEO turnover 9.5 percent a year (5.1 recall, 1.3 lost term election, 3.2 retirement), head coach 26 percent, coordinators 32 to 33 percent, general manager 21 percent. One quirk kept: a quarterback's price curve starts above the minimum, so no quarterback is ever a minimum-price fill-in unless her rounded overall is under 56.
35. Coach judgement (Edition 8.36): the five numbers in the Squads rule are placeholders. Because the error is unbiased, a club's strength is unchanged on average and only shuffles between starters of nearly equal rating, so the effect is small and falls hardest on young players with few seasons and on clubs with a weak head coach or coordinator; a staff that is wrong costs its club a little in a given game and nothing in the long run, as the owner intended. The judged rating is not shown to the story layer.
36. Top-quarter ranking (Edition 8.37): the placeholder that stands for a top-quarter share of a club's snaps is now the club's top quarter by her coaches' judged overall, taken at the league-year turn before the year changes. Counting real snaps from the engine would replace it and is the better long-term basis; judged overall is a stand-in that at least does not read true ability.
37. Aging against the NFL (Edition 8.38, owner's rulings of 2026-10-26: league rates follow the NFL's except where the DFL differs by design, and work does not stop to ask about questions the NFL settles). Measured over 18 league years, 48 clubs: the share of rostered players aged 30 or more fell from 25 percent to 15 percent after this edition (the NFL's is about 11 percent for offensive linemen, 21 percent for defensive linemen, 8 percent for defensive backs and under 5 percent for running backs; the DFL's are 17, 20, 10 and 8 with upgrade signings in place). The average age of a rostered player is 26.0 (NFL 26.6) and the average career 5.2 years on a roster (NFL 3.3 by a different count). Quarterbacks still run young (25.3 against the NFL's 28.4); upgrade signings did not close it, because the best free-agent quarterbacks are not 5 points better than the club's starter and the older ones cost more than the drafted rookies who fill the backup slots. The quarterback age gap, the offensive line's older-than-NFL profile and the shorter-than-NFL age of running backs are left as measured gaps to tune once the owner sees them (item 39). The decline rates, the wear numbers, the long snapper's peak of 31 and the NFL benchmarks are placeholders.
38. Upgrade signings (built in Edition 8.39): free agency alone fills only template shortfalls, so at positions the draft oversupplies (quarterback above all) better veterans stayed in the pool while younger, lower-rated players started. Camp now has the upgrade step in §11. The margin, the limit of three and the starters counted are placeholders; the result is measured in Edition 8.39's record in `docs/FAIRNESS.md`.
39. Gaps against the NFL (Edition 8.41, owner's ruling that rates follow the NFL; no owner decision needed). Third pass: fewer quarterbacks drafted (draft weight 3 to 2), so veterans fill the quarterback slots; quarterbacks peak at 30 and decline slowly; offensive linemen decline faster (1.9 times the base curve) and running backs faster still (3.3); the long snapper peaks at 33. Measured over 18 league years, 48 clubs, share of rostered players aged 30 or more against the NFL's: offensive line 13 percent (NFL about 11), defensive line 21 (21), defensive backs 10.5 (8.5), running backs 6 (under 5), quarterbacks 23. Mean age of all rostered players 26.0 (NFL 26.6); quarterbacks 26.4 (NFL 28.4) and long snappers 28.0 (30.7) are still young. What remains is mostly supply: free agency and the draft put young players into slots a veteran fills in the NFL. Every rate here is a placeholder.
40. Recall rate re-tuned (Edition 8.40, owner's ruling of 2026-10-24: about two recalls a year): the aging and upgrade changes of Editions 8.38 and 8.39 moved the league to 1.1 recalls a year at the old threshold of 27 (more roster turnover steadies the clubs' results). On one league the threshold gave 1.08 a year at 27, 1.72 at 31 and 2.97 at 36, so 33 was chosen for about 2.1. The threshold is a placeholder and interacts with parity: re-check it whenever club results change.
41. Trade volume (Edition 8.42, owner's ruling that rates follow the NFL): measured at about 10 trades a year across the league under the old settings, about 6 times too quiet per club. On one league of 14 years, all clubs proposing gave 32 a year, four tries gave 71 and ten tries gave 109 (with the stuck-at-bottom band failing at ten). Four was kept. Draft-day trades, which are most of the NFL's count, are still only the pick sweeteners inside player deals; a pick-for-pick trade (moving up or down the order) is not built and would add to the volume without any change to the rest.
42. Recall rate re-tuned again (Edition 8.43): with trade volume at the NFL's rate (Edition 8.42) clubs' results are less settled and recalls ran 2.5 a year with CEO turnover 10.1 percent at the threshold of 33, so the threshold went to 30, which gave 1.56 recalls a year (six leagues); 32 is the interpolation to about two (Edition 8.44). The recall rate moves about a third of a recall a year per point of threshold; the threshold is a placeholder and moves with every change to club results.
43. Penalties (Edition 8.46, owner's ruling that rates follow the NFL): the catalogue's counts are the NFL's accepted penalties per game by type in 2023 (Pro Football Reference, 272 games). The draw rate per play is a placeholder fitted by `tools/calibrate_penalties.py` so that accepted counts match; the engine gives 5.94 penalties and 50 yards per team per game (NFL 5.94 and 49). Kicking-play fouls are shared among special-teams holding and blocks in the back on a builder's split of the NFL's special-teams total, which the source does not break down. Which player commits a foul is a position-weighted draw, not yet tied to the players' attributes (the spatial engine will tie holding and pass interference to who is beaten). The two-minute warning is the NFL's rule added where §10 is silent (change on request). Offsetting fouls, fouls on tries and fouls after a scoring play are not modeled (`docs/ENGINE_COVERAGE.md`).
44. Box scores and the play table (Edition 8.47): in the play-level engine the credit for who tackled, sacked, intercepted or defended a play is a draw by position group (run: line 27 percent, linebackers 50, defensive backs 23; pass catch: 4, 36, 60; sack: 72, 22, 6; interception: 2, 18, 80; pass defended: 6, 24, 70), weighted by the defender's tackling, pass-rush, ball-skill or coverage attribute, with an assist on 30 percent of runs and 20 percent of catches and a shared sack on 12 percent of sacks. These are the builder's placeholders; the spatial engine replaces the draw with who actually made the play. Returns are credited to the squad's fastest elusive back, receiver or corner, with kickoff return yards taken as the starting spot plus five; the spatial engine will play returns on the field. Longest play, red-zone and fourth-quarter splits, and per-quarter team statistics are not in the box score yet.
45. Spatial engine (Edition 8.48): every number is a placeholder in `data/engine/spatial/` until calibrated to the bands (`data/engine/BANDS.csv`) and to play-level targets (completion rate by depth, sack and pressure rates, yards per carry and its spread, tackle and fumble rates, snap-to-whistle times). The play catalogue and coverage list are the builder's selection from common NFL concepts, not from the owner's earlier material (the snapshot is not in the repository); the owner may add or remove entries by editing the data files. Personnel groupings other than three receivers, one back and one tight end, nickel and dime packages, fatigue and substitution, challenges and replay, and kicking plays on the field are not built (Edition 8.49); item 47 lists them and what each would add.
46. Spatial engine calibration (Edition 8.49, measured; no owner decision needed to continue; the game-level result below is superseded by Edition 8.52, item 50). Snap level (`tools/calibrate_spatial.py`, 8,000 snaps drawn from the situations that games produce, over four founding leagues): all run and pass targets inside their bands except the pass success rate (41 percent against 43 to 51), the share of runs gaining 3 to 4 yards (32 percent against 13 to 25) and completions of 11 to 20 yards (19 percent against 19 to 33, at the edge). Game level (`tools/calibrate_engine.py --spatial`, 1,500 games, seed 14): 17 of the 24 bands pass, among them points (24.0), completion rate, interceptions, sack rate, rushing (4.39 a carry), fumbles, penalties, field goals, time of possession, overtime (5.9 percent), the tie rate (0.9 percent; the play-level engine's is 1.4 to 1.8) and the home win rate (53.6 percent). Seven do not: first downs (16.0 against 18 to 24), third-down conversion (33 percent against 36 to 44), punts (6.5 against 3.4 to 5.0), passing yards per attempt (7.8 against 6.5 to 7.6), plays per game (59.8 against 60 to 68), the spread of the final margin (15.7 against 11 to 15.5) and injuries per team (0.37 against 0.40 to 1.4, which follows from the plays). They share one cause: the spatial engine's yardage is more boom-and-bust than the NFL's (runs cluster at 3 to 4 yards, passes at tiny gains and long ones), so yards per play are right and first downs are too few. A later change to third-and-long calls moved third-down conversion to 34.8 percent and first downs to 16.3 in 1,000 games (seed 15) without bringing either inside the band. The engine is also sensitive to the league's average ratings: one founding league's average receivers, quarterbacks and pass rushers can differ from another's by a point or two, which moves passing yards per attempt by 0.3 to 0.7; the fit therefore uses four founding leagues, and a single league can still sit outside a band that the average meets. The play-level engine passes all 24 bands, which is why it stays the default. Closing the gap needs a structural change to how the spatial engine produces middling gains (second-level tackling and run-defender fits), not more parameter fitting.
47. Spatial engine extensions not built (Edition 8.49; the roadmap's Phase 3 definition does not require them, and each would add realism to the 2D view rather than change the league's results): offensive personnel other than three receivers, one back and one tight end; nickel and dime defensive packages (the defense is always four linemen, three backers and four defensive backs, with up to two extra rushers); fatigue and substitution (the fatigue input exists and is held at full); kicks, punts and returns as plays on the field (they stay play-level); challenges and replay with the officials' error model; injuries from contact in a play (they stay a draw per play); penalties tied to what the 22 players do, and the out-of-position penalty of item 8; the share of run tackles made by linemen (about 3 percent in the engine against about 27 in the NFL, because an engaged lineman can reach a carrier only within an arm's length). The owner may ask for any of these as a later slice.
48. Decided (Edition 8.50, owner's ruling of 2026-10-27): the spatial engine becomes the default only when it passes all 24 bands; the animation is a view of the engine's trace and sits on top of it (section 16). Done in Edition 8.52 (item 50): the seven missed bands are closed, the Phase 4 fairness measurement was re-run with the 22-player engine, and the default is switched.
49. Trace archive (Edition 8.51): the archive format (one file per game, or one per season, with an index by game and snap), the storage service and its cost are not chosen; the trace itself and its decoder exist (`dfl.engine.spatial.trace`). Decide with the website work.
50. 22-player engine as the default (Edition 8.52, measured). Game level, `tools/calibrate_engine.py --spatial --procs 2`: 24 of 24 bands pass at the default check (seed 1, 1,000 games) and at seeds 14 and 15 (1,500 games each). Founding leagues with stronger offenses overrun a few caps by a hair: seed 16 completion rate 0.674 and yards per attempt 7.63 (limits 0.67 and 7.6), seed 17 completion 0.675, yards per attempt 7.65 and yards per carry 4.95 (limit 4.8); the play-level engine also misses two or three bands on some seeds, but the spread is wider here, because the field engine is more sensitive to a league's average ratings. How the seven misses closed: run defenders fill at a spread of depths and blockers start with a spread of leverage; passes carry an accuracy error that grows with distance and a catch penalty on long throws; defenders turn with a delay; a receiver keeps a share of her speed after the catch; a quarterback on third down wants his target past the first-down marker; first-down calls no longer boost deep concepts; the pass-concept mix is weighted toward the plays that move the chains (play-action succeeded 35 percent of the time and was sacked 12 percent, quick slants 64 percent and 0.5); the spatial engine has its own injury rate and a fumble rate near the NFL's; the physics were refit three times (`tools/fit_spatial.py`). Phase 4 fairness with the 22-player engine (`tools/fairness.py run`, two leagues of 12 seasons, first 3 dropped; fewer seasons than the 36 of the play-level measurement because a season takes about 12 minutes): no band fails. The two bands that the game engine drives most are where they were with the play-level engine: win-percentage spread 0.206 (0.207 before; limit 0.21) and close-game share 0.415 (0.423; limits 0.35 to 0.55); year-to-year correlation 0.281 passes; the rest are UNCLEAR or NO DATA for want of seasons. Open: a full 36-season, six-league fairness run (about 35 hours of computing); making the engine less sensitive to a league's average ratings so that seeds 16 and 17 pass too; keeping traces for league games, which depends on item 49.
51. Extensions and postseason injury replacements (Edition 8.55, L4): included in scope by the owner on 2026-10-30; concise rules that prevent postseason roster upgrading and any bypass of contract and cap restrictions are proposed in `docs/OWNER_RULINGS_NEEDED.md` and are not built until approved. Restructuring a future year's base salary is deferred.
