# Four spec rulings (Ed 8.52 §20 items 4, 8, 9, 14): RULED 2026-10-29, now in Ed 8.53 (Ed 8.54 adds only the Bowl-incomplete marker)

Written 2026-10-29. Each entry gives the exact unresolved question, what the spec says, what the code does today, the options, the builder's recommended default, and what the default does to play. None of them blocks the live clock; each is cheap to change later because the values live in data files or one function. Where a number comes from a simulation it is labelled as indicative, not as calibrated.

How to answer: for each item say **Approve** (the recommended default becomes the ruling and goes into the next edition of the spec) or **Change** with the alternative.

---

## Superseded by the owner's rulings of 2026-10-30 04:01 (Edition 8.55)

Three statements below were true when they were written and are no longer current. They are kept only because the questions are kept as asked.
- **Item 9, order of the cascade.** Strength of schedule no longer ranks ahead of point differential. The league-wide cascade is now: overall wins; overall win %; division wins and win % (only if every tied club shares a division); conference wins and win % (only if every tied club shares a conference); **point differential; strength of schedule; strength of victory**; the seeded draw. The figures quoted under item 9 for how often each step separates a group were measured on the old order and are not repeated for the new one.
- **Item 14, the Coronation venue vote (15 June) and its announcement (4 July) no longer exist.** The Coronation has one permanent neutral venue, Amethyst Aloha Stadium, owned and renovated by the DFL; there is no vote, no rotation and no 40-year rule. The year has 47 dated events, not 49. The nominal home finalist is designated after Week 4 from the conferences' pooled interconference record (§3).
- **Playoffs.** Six clubs a conference (four division winners and two wild cards), seeds 1 and 2 on byes, a reseeded divisional round; ten playoff games a year, not twelve (§3).

---

## Rulings recorded 2026-10-29 (the questions below are kept as asked; where a statement in them is superseded it is marked)

The owner approved the recommended defaults with clarifications. Nothing was broadened beyond them. They are in spec Ed 8.53 (§2, §3, §7, §11, §12, §20 items 4, 8, 9, 14).

| Item | Approved | Clarification and where it stands |
| --- | --- | --- |
| 4 Bowl | Next-ranked, medically available replacements from the same conference's four Ambassador clubs, count not rerun | Exhausted position ballot: the place is still filled by a new distinct player (reassigning a rostered player cannot fill a place); permanent position, emergency game assignment and the one exception to positional minimums are kept apart (§7). Rings go to the final 53 present and medically ready at kickoff; a snap is not required. **Completed in Edition 8.56 (2026-10-30, Step 8): ballots, counts, final 53, lock and rings are specified (§7) and built; the replacement and ring rules are the ruled ones, the rest are builder's readings listed at the end of this file for ratification.** |
| 8 Rosters | 48 game-day active (47 with fewer than 8 offensive linemen); 2 elevations a game; 3 a player a season; 4-game IR minimum; 8 designated returns | Inactive players may replace injured players before the game-day roster locks; none enters from the inactive list during a game (the line "injuries are replaced from the inactive five first" below is corrected accordingly). Emergency list now files coverage for every position (up to four names, nearest positions first, dressed healthy players only), is applied as injuries happen, and cannot put one player in two simultaneous positions: built and tested (`tests/test_emergency.py`, 13 tests). The missing out-of-position penalty stays an engine requirement (§20 item 2). |
| 9 Standings | Higher SOS/SOV ranks higher; no decisions = 0; cascade unchanged | The last step is one stable seeded priority per club per season, the same in every scope (the statement 9(c) below that the draw is seeded by scope is superseded). Ordinary standings rank best first; draft order is the exact reverse. Built and tested (`tests/test_standings.py`, class `SeasonDrawPriority`). |
| 14 Calendar | Listed offseason dates, checked against the leadership, contract-expiry and 31 December honours rules | The Coronation is 25–31 December, so 1 January follows it the next morning only when it is on 31 December (the claim below that it always does is corrected); the days between are empty except for the year close (tested for every year 2000–2299, and through three simulated years with gaps of 0, 1 and 2 days). The ledger itself now rejects trades in January, after the close, in the postseason and through December; earlier saved state is settled from the events fired (`tests/test_trading_window.py`, including an upgrade of a file written by the earlier code). |

---

## Item 4. What happens when an Ambassador Bowl roster is short at a position?

**Exact question.** §7 says the Bowl rosters are 53 places per conference chosen by ranked ballots (Droop quota, seeded ties), "drawn from that conference's four clubs", that rosters lock one simulated hour before kickoff, and that "a shortfall is a validation failure repaired before play." It does not say how. When a position is below its required minimum at the lock (a selected player is injured or otherwise unavailable, or the four clubs between them cannot supply the minimum), what fills the place?

**Ambassador eligibility the spec already fixes (any repair must keep all of it).**
- Only the 8 ambassador clubs take part (§3, §7): one per division, so each conference has exactly four. The Western All-Stars come from the four Western ambassadors and the Eastern All-Stars from the four Eastern ones. No active club (there are 40) supplies a player.
- Each side has 53 places, of which 48 are the required positional minimums (§11: K1 P1 QB3 HB3 or HB2+FB1 WR5 TE2 LT2 LG2 C1 RG2 RT2 LE2 RE2 DT3 ROLB2 MLB3 LOLB2 CB5 FS2 SS2 LS1) and 5 are discretionary (§11).
- Each side's head coach and three coordinators come from the conference's best Tour club (§7). Repair concerns players only.
- Players are ranked by ballot; every candidate is ranked (§7), so a full order exists for every position.
- Tour Rings go to "the winners' final 53 present at kickoff" and the four Bowl coaches (§7).
- §11 also says a club must field 11 medically available legal players or forfeit, and that when no medically available player remains at a position the emergency list (at least one name for every position, including K, P, LS and QB, filed before kickoff) supplies a dressed player who plays out of position.

**What the code did before Edition 8.56 (superseded; see "Step 8" at the end of this file).** The ballots, the Droop count, the 53-place roster and the lock were not built. The Bowl side was a composite: the best healthy players at each position across the conference's four ambassador clubs, up to four a position, chosen at game time (`all_star_side`). So no shortfall can arise yet, and nothing needs repairing, but the Bowl roster is not the spec's roster either.

**Options.**
- **A (recommended).** The replacement for an unavailable selected player is the next-ranked candidate at that position, from the final order of the count that chose the roster, who is (1) under contract to one of that conference's four ambassador clubs at the lock, (2) medically available at the lock, (3) not already one of the 53. The count is not rerun. Repair runs once, at the lock, position for position, so each side stays at 53 and the 48 required minimums are restored first. If the ballot order for a position is exhausted, a dressed player from a neighbouring position, from the same four clubs, goes on the emergency list for it (§11) and plays out of position. If a side still cannot field 11 medically available legal players, it forfeits.
- **B.** Sign free agents or draw players from outside the four clubs. Fills any gap, but breaks "drawn from that conference's four clubs" and the ambassador-only eligibility above, and raises cap and contract questions for a one-game roster.
- **C.** Play short, and forfeit only if fewer than 11 legal players. Simplest, but a Bowl with no kicker or no quarterback decides itself.

**Recommended default: A.**

**Gameplay consequences of A.**
- The Bowl is always played by people from that conference's four ambassador clubs, which is what the Bowl is for. No active-club player, free agent or outside money enters.
- A replacement is by construction a player the voters ranked lower, so an injury at the lock makes the side slightly weaker at that position, never stronger than the vote intended. Several injuries at one position (say, three at quarterback in a four-club pool) can reach the emergency list.
- An injury after the lock (during the final simulated hour or the game) is not repaired: it is handled as in any game, by the emergency list.
- Until the out-of-position penalty exists (§20 item 2 is open; the engine today lets an emergency player use her own attributes), an out-of-position emergency player is not penalised, which flatters rare emergencies. That is an engine item, not a Bowl rule.
- Tour Rings go to "the winners' final 53 present at kickoff": a repaired roster is the 53 that count, so a replacement who plays wins a ring and the injured player she replaced does not.
- Deterministic and reproducible: no draw is needed except for equal ballot ties, which are already seeded.

---

## Item 8. Are the NFL-inherited fill-in numbers the DFL's numbers?

**Exact question.** §11 uses four NFL defaults without owner confirmation: (a) game-day active roster 48, or 47 with fewer than 8 offensive linemen; (b) at most two practice-squad elevations a game; (c) at most three elevations per player per season; (d) injured reserve minimum of four games, with at most eight players per club per season designated to return. Confirm each as the DFL rule, or give the DFL value.

**What the code does today.** All four are built exactly as written (`ledger/rules.py`: `ACTIVE_LIMIT` 48, `ACTIVE_LIMIT_FEW_OL` 47, `ELEVATIONS_PER_GAME` 2, `ELEVATIONS_PER_PLAYER` 3, `IR_MIN_GAMES` 4, `IR_RETURNS_MAX` 8). They are single constants; changing one changes nothing else in the code.

**Who may be an emergency player (what §11 says, and what is built).**
- *Spec (§11).* Before every kickoff each club files an emergency list with at least one name for every position, including K, P, LS and QB. If no medically available player remains at a position during a game, the list supplies a dressed player for it, who plays out of position and is subject to the engine's out-of-position penalty. Nothing can be signed during a game. A club that cannot field 11 medically available legal players forfeits.
- *Eligibility as built (`ledger/gameday.py`).* The emergency player must be on that club's game-day active list (the 48, or 47 with fewer than 8 offensive linemen, including any elevated practice-squad players), so she is dressed and medically available when the list is filed. She is the best-rated active player at the first neighbouring position in a fixed order that has anyone dressed, e.g. K: P then QB; P: K then QB; LS: C then TE; QB: WR then HB; HB: FB then WR; FB: HB then TE; WR: HB, TE, CB; TE: WR, FB, RT; LT: LG then RT; LG: LT then C; C: LG then RG; RG: RT then C; RT: RG then LT; LE: RE then DT; RE: LE then DT; DT: LE then RE; ROLB: LOLB then MLB; MLB: ROLB then LOLB; LOLB: ROLB then MLB; CB: FS then SS; FS: SS then CB; SS: FS then CB. If no neighbour is dressed, the best-rated active player at any position. These neighbour orders are the builder's choice, not the owner's, and are in one table.
- *Known gap to the spec.* Today a name is filed only for a position where nobody at all is dressed; the spec asks for a name for every position, so that a position emptied by an injury during the game has its replacement already named. Closing it is routine (file the list for all positions) and does not change any rule; it is not done yet.
- *Out-of-position penalty.* Still open (§20 item 2): the engine lets an emergency player use her own attributes.

**Recommended default: confirm all four as built.** Reasons: they are consistent with the DFL's own choices around them (53 on the roster, a 16-man practice squad, 48 required places), and no DFL document asks for different values.

**Gameplay consequences.**
- *48 active of 53*: five players are inactive each week. Every club must choose which five; depth at a position matters because injured players are replaced, before the game-day roster locks, from the inactive five first and then by elevation (corrected 2026-10-29: no inactive player enters during a game). A lower number would make depth more valuable, a higher number would make the bench irrelevant.
- *Two elevations a game, three a player a season*: the practice squad can fill at most two gaps a week, and any one player can only do it three times. With 16 on the squad this never starves a club in normal weeks, but a club with four or more injuries at one position in one week runs out and falls to the emergency list. Raising the limits makes the practice squad a free extension of the roster; lowering them makes injuries more punishing for thin clubs.
- *Four-game IR minimum, eight returns*: an injury expected to last four or more games frees a roster place at once but the player cannot play for at least four games; shorter injuries keep the place occupied, which is the real cost. Eight returns a season caps how often a club can use IR as a revolving door.
- Indicative figures from the 22-player engine, one simulated league year (seed 1, 776 club game days built): no club was short at any position, no game needed an emergency-list player, nobody forfeited, and no practice-squad player was elevated (0 of 776); there were 131 injured-reserve moves, 105 players on injured reserve at year close and 42 of about 3,400 rostered players out at year close. So today none of the four limits actually binds in a normal year; confirming them costs nothing, and they only start to matter if injury rates, roster depth or the out-of-position penalty change. One year, one seed: treat as a sanity check, not a calibration.
- Note the one unfixed engine item next to these: the out-of-position penalty (§20 item 2).

---

## Item 9. Are the standings assumptions right?

**Exact question.** §20 item 9 lists three assumptions to confirm: (a) a higher strength of schedule, and a higher strength of victory, rank higher; (b) a club with no decisions has a win % of 0; (c) the seeded draw is the last resort and is rerun from a season-and-scope seed. (§3 already writes (a) and (b) into the tiebreak cascade; item 9 asks the owner to confirm they were meant.)

**What the code does today.** Exactly as §3 says: win % = W ÷ (W + L), zero decisions counted as 0; strength of schedule = opponents' combined W ÷ (W + L); strength of victory the same over opponents beaten; higher ranks higher at both steps; the draw is seeded by the season and the scope being ranked (division, conference winners, wild-card pool, draft, Tour), and a tied group is sorted once by the whole cascade with no restart.

**Recommended default: confirm all three as built.**

**Gameplay consequences.**
- *(a) Direction of strength of schedule.* "Higher ranks higher" rewards the club that earned the same record against stronger opposition, as the NFL does. The opposite choice would reward the easier schedule. This is the item that matters in practice: in 400 simulated seasons with random club strengths (indicative; real clubs will differ), 14 groups of clubs per season were level on wins, and strength of schedule was the step that finally separated the whole group in about 35% of them (conference wins about 33%, division wins about 30%, conference win % about 1%, point differential about 0.5%, division win % about 0.3%). Strength of victory and the seeded draw never decided one. Because the exile decision, the seeds and the draft order all run through the cascade, this single direction decides who is exiled or seeded in a real share of seasons.
- *(b) No decisions = 0.* A club whose games were all ties would rank level with a winless club instead of at 0.5. With about 1.5% ties and 18 games this is effectively impossible in a regular season (and in a 7-game Tour); it is a rule for a case that does not occur. Changing it to 0.5 would be safe but changes nothing in play.
- *(c) Seeded draw as last resort.* It is reproducible (same season, same scope, same order) and, in the simulation above, was never reached. The one side effect: because the draw was seeded by scope, two clubs separated only by the draw could be ordered one way in the division and the other way in the wild-card pool (superseded 2026-10-29: one priority per club per season now).

---

## Item 14. Are the offseason dates right?

**Exact question.** §2 says the offseason dates "are provisional and live in `data/OFFSEASON.csv`." Confirm these as the DFL's dates:

| Event | Date as built |
|---|---|
| League year turns (contracts expire, retirements, cap enforced) | 1 January |
| Leadership hiring window | 1–31 January |
| Trading opens; free agency opens | 1 February |
| Draft round 1 (last Thursday of April); rounds 2–3 next day; rounds 4–7 and compensatory picks the day after | late April |
| Camp opens (preseason ceiling of 90 applies) | 1 June |
| Coronation venue vote | 15 June |
| Final cut to 53 and 16 (Tuesday), waivers clear (Wednesday) | the days before week 1 (week 1 is Saturday 1 July in 2000; because the calendar is anchored on the Coronation, in other years it falls between 24 June and 1 July) |

**What the code does today.** Built exactly as listed; the calendar fires events in this order, and the dates can be edited in the one data file without touching code.

**Recommended default: confirm as built.**

**Gameplay consequences.**
- The year is split roughly in half: about 175 to 182 days of offseason (1 January to the day before week 1) and about 183 to 191 of season, depending on the year. The Coronation is the last Sunday of the year (25–31 December) and the league year turns on 1 January, the next morning only if the Coronation is on 31 December (corrected 2026-10-29), so there is **no gap** between the final game and contract expiry: players whose contracts end with the year are in the pool on 1 January, a month before free agency opens on 1 February. In the built calendar nothing is signed or traded in that month; only leadership hiring runs. That is the biggest practical effect of these dates and is deliberate in the NFL pattern; it is worth the owner knowing.
- Free agency opens about 12 weeks before the draft, so clubs shape needs in free agency first and draft second. Moving the draft earlier would make it the main source of need-filling and shrink the free-agency market's role.
- Camp opens three to four weeks (20 to 26 days, depending on the year) before the final cut and the upgrade signings happen in that window; the cap timing rule (top-count until the final cut) lasts exactly that long. A shorter camp shortens the window in which clubs sign and then cut down.
- The venue vote on 15 June falls 6 to 12 days before the cut and 10 to 16 days before the season; moving it only moves a headline.
- Because the real-time clock is deferred, none of this fixes how long any date lasts in real time. If the owner later wants the offseason to take a different share of the real season (the ≈50-hour target is provisional) that is a pacing choice, not a change to these dates.

---

## Ledger questions arising from Step 7 verification (2026-10-30): RULED 2026-10-30 04:01 (Edition 8.55)

The four questions below were answered in the owner's message of 2026-10-30 04:01. Status of each:

**L1. Waiver claim order: RETAINED, provisionally.** The order built in Step 7 stays (weeks 1 to 4 last season's draft order; from the paid week after league week 4 the clubs at home by the current standings, worst record first; each Ambassador keeps her own prior draft place). The ruling asked for one complete worked example of all 48 clubs, and for "prior draft slot" to be defined: **it is the club's own slot in last season's draft order, never a traded pick.** A pick is a (year, round, original club) token, the slot it takes depends on its original club alone, and the waiver code reads only the list of clubs, so trading picks cannot transfer waiver priority. The example, generated from a real second season with all 48 clubs before and after the Week 4 transition and with a pick swap that moves nothing, is `docs/WAIVER_PRIORITY_EXAMPLE.md` (tool: `tools/show_waiver_example.py`); tests: `tests/test_waiver_priority.py` (`PickTradesMoveNothing`, `WorkedExample`).

**L2. Split release: APPROVED as built.** The release year's own bonus portion stays in that year; only the future portions divide 50/50 between that year and the next (`tests/test_step7_rules.py`, `SplitReleaseL2`, and the independent oracle).

**L3. First-round option: IMPLEMENTED as a club decision.** A first-round contract is now four years carrying the club's option on a fifth at $35. *Who decides:* the club's general manager, by a light deterministic rule (no random numbers): exercise when the market price of the overall the club believes the player has (its own view, which already carries the GM's evaluation) is at least the option salary; otherwise decline. *Deadline:* the opening of Draft Round 1 in the league year of the player's fourth contract year (the last Thursday of April), before the first pick. *Financial consequences:* exercising adds one covered year at $35, **guaranteed in full**, so it counts at once toward the viability invariant for that year and a club that could not carry it is refused (and so declines); the year costs nothing in the current year's cap. Declining (or missing the deadline) lets the contract end with the year, and the player is a free agent a year earlier. *Record:* every decision is saved in the ledger (`Ledger.option_log`: year, club, player, decision, salary, the year covered, the guarantee, and the reason). The full guarantee is the one detail the owner did not specify; it is one constant (`rules.R1_OPTION_GUARANTEED`) and costs nothing to reverse. Tests: `tests/test_fifth_year_option.py`.

**L4. Extensions and postseason injury replacements: INCLUDED, PROPOSED RULES BELOW, NOT YET BUILT.** Restructuring a future year's base salary is **deferred**: the code restructures the current year's base only, and the spec sentence "current or future year" is annotated accordingly in Edition 8.55. Nothing below is implemented until the owner approves it.

### L4 proposed rules (for approval)

**Extensions (§11: "two completed seasons and mutual consent").**
1. *Who and when.* A club may extend a player on its roster or injured reserve who has completed at least two credited seasons and whose contract ends with the current league year, from the day Draft Rounds 4 to 7 finish until the Friday before numbered week 9 (the trade window's close). Never in January, the postseason or the camp.
2. *Mutual consent* is the existing negotiation: the club's price for the player must not exceed what the club will pay (`willing`) and the player's ask must not exceed it. No consent, no extension, and nothing is forced.
3. *The new years begin after the old contract ends.* The extension is a new contract of one to five years that starts the next league year; nothing about the current year changes. This stops an extension from being used to rewrite the current cap hit. Its signing bonus is prorated only over the extension's own years.
4. *Checks.* The viability invariant and cap timing apply to every year the extension covers, as for any signing. At most one extension per player per league year.
5. *Anti-bypass.* A player the club acquired this league year by trade or waiver claim cannot be extended in that year. An extended contract may not be restructured until the year it begins. Rookie contracts are extended on the same terms once their fourth year is under way (the fifth-year option stays a separate decision).
6. *Record.* Every extension is saved (club, player, years, salary, bonus, guarantees, date).

**Postseason injury replacements (§11: "Postseason-only replacements pay one salary-week equivalent").**
1. *When a replacement is allowed.* Only from the Wild Card round to the Coronation, only for a club still playing, and only one for one: a club may sign a replacement only for a named player on its 53 who is out for the rest of the postseason (the club's medical ruling). That player goes to injured reserve for the postseason and cannot return, even if he heals. A club with no such player can sign no one.
2. *Same position.* The replacement must have the injured player's permanent position. This stops a replacement being used to change the shape of the roster.
3. *No upgrading.* The replacement may not rate higher, by the club's own view, than the player he replaces. Players released by any club after the regular season's last roster lock, and anyone on waivers, are not eligible for any club until the new league year. Ambassador clubs and the Bowl rosters are outside this rule (§7 governs them).
4. *Order of supply.* The club's own practice squad first (promotion, as in the regular season); free agents only when no practice-squad player of that position is available.
5. *Contract.* The minimum salary, one year, expiring at the year's close, no bonus, no guarantee, no extension, not tradable. It costs **one salary-week equivalent** ($5 ÷ 22 of cap) however many postseason games he plays. Viability and cap timing apply as for any signing.
6. *Freeze.* From the trade close until the new league year opens, no trades, waiver claims, restructures or extensions; the only permitted signings are these replacements and the practice-squad promotions already allowed.
7. *Limits and record.* At most eight replacements a club per postseason. The roster stays at or below 53 and the practice squad at or below 16. Replacements earn no compensatory-pick value. Each is saved with the player he replaced and the round.

**Decisions needed from the owner.** (a) Approve, change or reject each numbered rule above, in particular extensions rule 1 (window) and postseason rule 3 (the no-upgrade test). (b) Whether extended players should have a bargaining discount or premium (none is proposed). (c) Whether the postseason replacement cost should be a constant rather than one salary-week ($5 ÷ 22), as now written.

*What each does to play if approved as written:* extensions give clubs a way to keep players past the end of a contract, so fewer good players reach free agency and the market thins; postseason replacements keep a contender playable after injuries without letting it improve itself.


## Step 8 (2026-10-30, Edition 8.56): Bowl ballots and final 53 — built; readings to ratify

Built from your outline (per-position ranked ballots, every candidate ranked, Droop quota `floor(8/(places+1))+1`, seeded ties, 48 positional + 5 discretionary places, eight head-coach voters, lock one hour before kickoff) plus the replacement and ring rules you ruled on 2026-10-29. Full text: spec §7 items 1–9. Everything below is a builder's reading you have not ruled on; each can change without touching the others.

| # | Reading | Alternative |
|---|---|---|
| a | All eight head coaches vote on **both** conferences, once, right after the last Tour round | each votes only on the other conference, or on her own |
| b | Candidates: the four clubs' 53-player rosters; not injured reserve, not the practice squad | include injured reserve |
| c | A ballot ranks by the voter's coaches' judged overall (the same estimate that picks their own starters); **no lean towards her own club's players** | add a home-club bias, or rank by true overall |
| d | The backfield counts as three halfbacks, so a fullback can only win a discretionary place | HB 2 + FB 1 as an alternative positional line |
| e | The 5 discretionary places: one count over everyone not already elected, ranked by judged overall alone (overalls treated as comparable across positions) | a quota per position group, or an owner-chosen weighting |
| f | The count is the weighted inclusive Gregory transfer (surplus passes on in proportion), exact fractions | unweighted/random transfer, or no transfer (top-N by first preferences) |
| g | "The order of the original ballot" used for replacements = the order of the count (elected, then reverse elimination) | a fixed overall ranking by total votes |
| h | Players who joined the four clubs after the vote are never chosen (not on the ballot) | allow them from the free-agent/roster pool |
| i | All 53 are dressed in the Bowl | the normal 48/47 game-day limit |
| j | Rings go to the whole locked roster (all medically available an hour before kickoff) and the four Bowl coaches | exclude anyone who did not take a snap |

Not claimed: that the vote picks the players a real league would. The judged-overall error model is a placeholder. In the one placeholder year examined (seed 1, Season 1) the lock needed no repair; the repair and exhausted-ballot rules are exercised by tests with deliberate damage (`tests/test_bowl.py`).
