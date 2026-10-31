# Pending rulings, for review (2026-10-30)

None of the items below is approved or implemented as a rule unless marked. Approved 2026-10-30 and recorded in spec Edition 8.57: **all 53 selected players dress for the Ambassador Bowl** (reading i) and **an exercised first-round fifth-year option is guaranteed in full**. The wording below is copied from `docs/OWNER_RULINGS_NEEDED.md`; recommendations are mine. **Approved 2026-10-30 (second set) and recorded in spec Edition 8.58:** Tour Rings need the winning final 53 present and medically ready **at kickoff**, not merely at the lock an hour earlier (built and tested; reading j below is superseded), and the Diamond Coronation score is recorded permanently from now on.

## 1. Fullbacks and the backfield rule: reconciling reading (d) with §11

**The two texts.** §11, positional minimums: *"HB 3, or HB 2 and FB 1"*. Reading (d), as built: *"The backfield counts as three halfbacks, so a fullback can only win a discretionary place."*

**Are they inconsistent?** Not in outcome, but they differ in what a vote can produce. Every Bowl roster has three halfbacks from the positional count, which satisfies "HB 3". The "HB 2 and FB 1" alternative can arise only by repair (a third halfback unavailable and none left, so the nearest position, the fullback, fills the place). A voted roster can therefore never choose a fullback instead of a third halfback, although §11 treats the two backfields as equally legal. A fullback can still win one of the 5 discretionary places, in which case the roster carries 3 HB + FB.

**Options.**
1. *Keep (d).* Simplest; fullbacks compete only for the 5 discretionary places.
2. *Combined third backfield place (recommended).* Positional HB count of 2; then a third "backfield" count whose field is every other halfback and every fullback, one place, Droop quota 5. HB 3, or HB 2 + FB 1, then both arise by vote exactly as §11 allows. The count, the discretionary field and the repair rule otherwise do not change (the repair for an unavailable backfield player takes the next in that count's order).

**Recommendation: option 2.** It is the literal reading of §11, costs one extra small count, and changes no other rule. I have not built it.

## 2. Remaining Bowl readings (spec §7 items 1–9, §20 item 4)

| # | Reading (exact wording) | Alternative | Recommendation |
|---|---|---|---|
| a | All eight head coaches vote on **both** conferences, once, right after the last Tour round | each votes only on the other conference, or on her own | Accept. It is the only reading under which the quota's 8 voters hold for both conferences. |
| b | Candidates: the four clubs' 53-player rosters; not injured reserve, not the practice squad | include injured reserve | Accept. Injured-reserve players are by definition not available; the lock would only replace them. |
| c | A ballot ranks by the voter's coaches' judged overall (the same estimate that picks their own starters); **no lean towards her own club's players** | add a home-club bias, or rank by true overall | Accept. A home-club bias would be a new rule; add one only if you want it. |
| d | The backfield counts as three halfbacks, so a fullback can only win a discretionary place | HB 2 + FB 1 as an alternative positional line | **Change (see the backfield reconciliation below).** |
| e | The 5 discretionary places: one count over everyone not already elected, ranked by judged overall alone (overalls treated as comparable across positions) | a quota per position group, or an owner-chosen weighting | Accept for now, then measure. The risk is that kickers, punters and long snappers (whose overalls sit on a different scale) are crowded out or over-favoured; I have not measured it. |
| f | The count is the weighted inclusive Gregory transfer (surplus passes on in proportion), exact fractions | unweighted/random transfer, or no transfer (top-N by first preferences) | Accept. It is the standard form of the count you described, and it is exact and reproducible. |
| g | "The order of the original ballot" used for replacements = the order of the count (elected, then reverse elimination) | a fixed overall ranking by total votes | Accept. It gives one order per position without rerunning the count. |
| h | Players who joined the four clubs after the vote are never chosen (not on the ballot) | allow them from the free-agent/roster pool | Accept. The ballot is frozen at the vote; choosing unballoted players would reopen it. |
| j | ~~Rings go to the whole locked roster (all medically available an hour before kickoff)~~ **Superseded by the owner's instruction of 2026-10-30, built in Edition 8.58:** rings go to the locked players who are present (still on one of the conference's four clubs' rosters) and medically ready **at kickoff**, checked immediately before the game; the roster is not reopened and the four Bowl coaches are unaffected | | Done. |

## 3. L4: extensions and postseason injury replacements (proposed, not built)

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

**Recommendations.**
- *Extensions 1 (window):* approve; the window is the one in which clubs already transact and it excludes January, the postseason and camp. *2–4:* approve. *5 (anti-bypass):* approve; it is the rule that stops an extension rewriting a cap hit or a trade being used to dodge the market. *6:* approve.
- *Postseason 1 (one for one, named player, no return):* approve. *2 (same position):* approve. *3 (no upgrading by the club's own view):* approve; note that "the club's own view" is its coaches' judged overall, an estimate with error, so a club can mis-judge and appear to upgrade; the test is on what the club believes, which is the only information it has. *4–5:* approve; the cost stays one salary-week equivalent. *6 (freeze):* approve. *7 (limits):* approve.
- *(b) bargaining discount or premium:* none, as proposed. *(c) cost as a constant:* keep $5 ÷ 22 unless you want a flat figure.
- Effect: extensions thin the free-agent market somewhat; postseason replacements keep a contender playable without letting it improve.

None of this blocks the website milestone.
