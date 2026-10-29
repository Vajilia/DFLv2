# Four spec rulings needed (Ed 8.52 §20 items 4, 8, 9, 14)

Written 2026-10-29. Each entry gives the exact unresolved question, what the spec says, what the code does today, the options, the builder's recommended default, and what the default does to play. None of them blocks the live clock; each is cheap to change later because the values live in data files or one function. Where a number comes from a simulation it is labelled as indicative, not as calibrated.

How to answer: for each item say **Approve** (the recommended default becomes the ruling and goes into the next edition of the spec) or **Change** with the alternative.

---

## Item 4. What happens when an Ambassador Bowl roster is short at a position?

**Exact question.** §7 says the Bowl rosters are 53 places per conference chosen by ranked ballots from that conference's four Tour clubs, that rosters lock one simulated hour before kickoff, and that "a shortfall is a validation failure repaired before play." It does not say how. When a position is below its required minimum at the lock (a selected player is injured or unavailable, or the four clubs between them cannot supply the minimum), what fills the place?

**What the code does today.** The ballots, the Droop count, the 53-place roster and the lock are not built (people layer, later). The Bowl side is a composite: the best healthy players at each position across the conference's four Tour clubs, up to four a position, chosen at game time (`all_star_side`). So no shortfall can arise yet, and nothing needs repairing, but the Bowl roster is not the spec's roster either.

**Options.**
- **A (recommended).** Take the next-ranked healthy candidate for that position from the same conference's four clubs, using the final order of the count that chose the roster (§7 ranks all candidates, so the order exists). The count is not rerun. If the position's ballot is exhausted, use the emergency list rule of §11: a dressed player from a neighbouring position plays out of position.
- **B.** Sign free agents or draw players from outside the four clubs. Fills any gap, but breaks "drawn from that conference's four clubs" and creates cap and contract questions for a one-game roster.
- **C.** Play short, and forfeit only if fewer than 11 legal players. Simplest, but a Bowl with no kicker or no quarterback decides itself.

**Recommended default: A.**

**Gameplay consequences of A.**
- The Bowl is always played by people from that conference's four Tour clubs, which is what the Bowl is for. No outside money or talent enters.
- A replacement is by construction a player the voters ranked lower, so an injury at the lock makes the side slightly weaker at that position, never stronger than the vote intended. Several injuries at one position (say, three at quarterback in a four-club pool) can reach the emergency list.
- Until the out-of-position penalty exists (§20 item 2 is open; the engine today lets an emergency player use her own attributes), an out-of-position emergency player is not penalised, which flatters rare emergencies. That is an engine item, not a Bowl rule.
- Tour Rings go to "the winners' final 53 present at kickoff": a repaired roster is the 53 that count, so a replacement who plays wins a ring and the injured player she replaced does not.
- Deterministic and reproducible: no draw is needed except for equal ballot ties, which are already seeded.

---

## Item 8. Are the NFL-inherited fill-in numbers the DFL's numbers?

**Exact question.** §11 uses four NFL defaults without owner confirmation: (a) game-day active roster 48, or 47 with fewer than 8 offensive linemen; (b) at most two practice-squad elevations a game; (c) at most three elevations per player per season; (d) injured reserve minimum of four games, with at most eight players per club per season designated to return. Confirm each as the DFL rule, or give the DFL value.

**What the code does today.** All four are built exactly as written (`ledger/rules.py`: `ACTIVE_LIMIT` 48, `ACTIVE_LIMIT_FEW_OL` 47, `ELEVATIONS_PER_GAME` 2, `ELEVATIONS_PER_PLAYER` 3, `IR_MIN_GAMES` 4, `IR_RETURNS_MAX` 8). They are single constants; changing one changes nothing else in the code.

**Recommended default: confirm all four as built.** Reasons: they are consistent with the DFL's own choices around them (53 on the roster, a 16-man practice squad, 48 required places), and no DFL document asks for different values.

**Gameplay consequences.**
- *48 active of 53*: five players are inactive each week. Every club must choose which five; depth at a position matters because injuries are replaced from the inactive five first, then by elevation. A lower number would make depth more valuable, a higher number would make the bench irrelevant.
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
- *(c) Seeded draw as last resort.* It is reproducible (same season, same scope, same order) and, in the simulation above, was never reached. The one side effect: because the draw is seeded by scope, two clubs separated only by the draw could be ordered one way in the division and the other way in the wild-card pool. Given how rarely it is reached this is cosmetic.

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
| Final cut to 53 and 16 (Tuesday), waivers clear (Wednesday) | the days before week 1 (week 1 is Saturday 1 July in the first year) |

**What the code does today.** Built exactly as listed; the calendar fires events in this order, and the dates can be edited in the one data file without touching code.

**Recommended default: confirm as built.**

**Gameplay consequences.**
- The year is split roughly in half: about 182 days of offseason (1 January to 30 June) and about 184 of season (1 July to 31 December). The Coronation is the last Sunday of the year and the league year turns the next morning, so there is **no gap** between the final game and contract expiry: players whose contracts end with the year are in the pool on 1 January, a month before free agency opens on 1 February. In the built calendar nothing is signed or traded in that month; only leadership hiring runs. That is the biggest practical effect of these dates and is deliberate in the NFL pattern; it is worth the owner knowing.
- Free agency opens about 12 weeks before the draft, so clubs shape needs in free agency first and draft second. Moving the draft earlier would make it the main source of need-filling and shrink the free-agency market's role.
- Camp opens about four weeks (26 days) before the final cut and the upgrade signings happen in that window; the cap timing rule (top-count until the final cut) lasts exactly that long. A shorter camp shortens the window in which clubs sign and then cut down.
- The venue vote on 15 June falls twelve days before the cut and about two weeks before the season; moving it only moves a headline.
- Because the real-time clock is deferred, none of this fixes how long any date lasts in real time. If the owner later wants the offseason to take a different share of the real season (the ≈50-hour target is provisional) that is a pacing choice, not a change to these dates.
