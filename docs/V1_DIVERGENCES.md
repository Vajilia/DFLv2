# v1 (`Vajilia/DFL`) versus Design Document Edition 8.6

Phase 0 audit, 2026-10-22. Method: **source read only**, at v1 `main` commit `84ce78f`. No v1 code was executed and none of its tests were run, so every row is a reading of source, not a test result. The v1 files read: `engine/rules.py`, `tiebreak.py`, `standings.py`, `schedule.py`, `rosters.py`, `positions.py`, `economy.py`, `calendar_dfl.py`. Not yet compared (needs a closer read): draft and lottery mechanics, waivers and trades in detail, rookie scale, trade deadline, injuries, CEO and fan systems, the schedule's slot-point balance.

**Rule of this audit.** The owner ruled that what is decided in the Design Document takes priority over anything salvaged from v1. So the default action is *Adopt spec*. A row is *Ask* only where the spec is silent or ambiguous, or where it conflicts with an older owner statement recorded in v1.

| ID | Area | Spec 8.6 | v1 | Action |
| --- | --- | --- | --- | --- |
| V1-01 | Tied games | A tie is not played in the standings; it is played for statistics (§3) | A tie counts as half a win in win % (`standings.py`) | Adopt spec |
| V1-02 | Tiebreak cascade | Wins, win %, division and conference wins and win %, strength of schedule, point differential, strength of victory, seeded draw; a tied group is sorted once | NFL procedures: head-to-head, common games, combined ranking, touchdowns, coin toss, plus a restart rule for three or more clubs (`tiebreak.py`). v1's header records the owner's 2026-10-14 change (strength of schedule before victory; no conference step in the division list), which is a partial move toward the spec | Adopt spec; retire `tiebreak.py` |
| V1-03 | Schedule model | Season 1 template relabelled by (bed, seed); eight-season division rotation (§3, §6) | Tier slots: `schedule.py` describes a random slot template mapped onto teams, tiers earned from last division finish | Adopt spec. DFLv2's bed-rotation module already matches the spec. The work log says the skeleton changed on 2026-10-12, so confirm what v1 actually runs |
| V1-04 | Salary cap | Cap $1,000, floor $800, reserve $25, minimum salary $5 (§11) | Cap $100M, floor 90%, bank up to $125M, minimum-salary scale 0.29–0.43 | Adopt spec for cap, floor and minimum. **Ask** about banking (below) |
| V1-05 | Practice squad | 8 | 16, at most 6 veterans (NFL 2025) | **Ask** |
| V1-06 | Positions | 20 position labels with left and right line spots; 48 required places (§11) | 11 position groups; roster counts sum to 53; game-day minimums sum to 34 (`positions.py`) | Adopt spec; the position model must be rebuilt |
| V1-07 | Long snapper | Required, 1 | None found | Adopt spec |
| V1-08 | Game-day roster | 48, or 47 with fewer than 8 offensive linemen | 48 only; no 47 variant found by search | Adopt spec |
| V1-09 | Emergency fill-ins | Emergency list per position, out-of-position play (§11) | Not found in `rosters.py` | Adopt spec |
| V1-10 | Elevations and IR | 2 elevations a game, 3 a player, IR minimum 4 games | Same, plus a limit of 8 IR returns a season | Aligned; **Ask** whether to add the 8-return limit to the spec |
| V1-11 | Offseason count | Silent | Only the 51 highest cap numbers count until the cut (NFL) | Default to NFL; add to spec once confirmed |
| V1-12 | Franchise and transition tags, restricted free agency | Removed (§11) | Implemented (steps 6e–6i, `RFA_SEASONS`, `RFA_MATCH_DAYS`) | Adopt spec; do not port |
| V1-13 | Waiver priority | Last season's draft order until four league weeks finish | Three weeks | Adopt spec |
| V1-14 | Coronation venue | Fan vote in June, announced July 4, 40-year window; finalist at home if its venue wins | Neutral site (`FINAL_IS_NEUTRAL_SITE`) | Adopt spec |
| V1-15 | Ambassador Tour dates | Calendar weeks 1, 4, 8, 11, 15, 18, 22; Bowl at C−7; one venue per round | Weeks 1–7; Bowl in week 8 | Adopt spec |
| V1-16 | Wild-card eligibility of a division's last-place club | Not excluded: "three best other clubs" (§3) | Excluded (`FIFTH_PLACE_WILD_CARD_ELIGIBLE = False`) | **Ask** |
| V1-17 | Season length in real time | About 55 hours in §0 arithmetic | `REAL_WEEKS_PER_SEASON = 2`; owner statements elsewhere of 168 hours, about 50, 48 | **Ask** (roadmap Phase 6) |
| V1-18 | Fairness bands | Not in the spec | 15 bands in `rules.py`, accepted by the owner as working limits | Port as the Phase 4 starting point; re-derive on the real engine |
| V1-19 | Decision points | Legal-option enumeration required (§11, roadmap) | `decisions.py`: bounded legal options, guard and fallback, choice logs | Port the design after review |
| V1-20 | Regression tooling | Validator and tests | Tiered fast and slow checks, golden fingerprints | Port the idea |

## Questions for the owner
1. **Cap banking (V1-04).** v1 lets unused room carry over, with a ceiling of $125M against a $100M cap. The spec is silent. Keep banking? Note the spec's numbers are not v1's divided by 100,000: the spec's minimum salary is 0.5% of the cap, v1's is 0.29%.
2. **Practice squad size (V1-05).** The spec says 8 and v1 says 16 (the NFL's current size). Which?
3. **Last-place wild card (V1-16).** A division's last-place club is exiled. Can it still take a wild card if its record is among the best non-winners? It almost never matters, but the code must say.
4. **IR return limit (V1-10).** Add the NFL-style limit of 8 returns a season to the spec?
5. **Season length (V1-17).** One number, so pacing can be built once.
