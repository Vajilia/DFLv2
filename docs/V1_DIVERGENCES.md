# v1 (`Vajilia/DFL`) versus Design Document Edition 8.6

Phase 0 audit, 2026-10-22. Method: **source read only**, at v1 `main` commit `84ce78f`. No v1 code was executed and none of its tests were run, so every row is a reading of source, not a test result. The v1 files read: `engine/rules.py`, `tiebreak.py`, `standings.py`, `schedule.py`, `rosters.py`, `positions.py`, `economy.py`, `calendar_dfl.py`. Not yet compared (needs a closer read): draft and lottery mechanics, waivers and trades in detail, rookie scale, trade deadline, injuries, CEO and fan systems, the schedule's slot-point balance.

**Rule of this audit.** The owner ruled that what is decided in the Design Document takes priority over anything salvaged from v1. So the default action is *Adopt spec*. A row is *Ask* only where the spec is silent or ambiguous, or where it conflicts with an older owner statement recorded in v1.

| ID | Area | Spec 8.6 | v1 | Action |
| --- | --- | --- | --- | --- |
| V1-01 | Tied games | A tie is not played in the standings; it is played for statistics (§3) | A tie counts as half a win in win % (`standings.py`) | Adopt spec |
| V1-02 | Tiebreak cascade | Wins, win %, division and conference wins and win %, strength of schedule, point differential, strength of victory, seeded draw; a tied group is sorted once | NFL procedures: head-to-head, common games, combined ranking, touchdowns, coin toss, plus a restart rule for three or more clubs (`tiebreak.py`). v1's header records the owner's 2026-10-14 change (strength of schedule before victory; no conference step in the division list), which is a partial move toward the spec | Adopt spec; retire `tiebreak.py` |
| V1-03 | Schedule model | Season 1 template relabelled by (bed, seed); eight-season division rotation (§3, §6) | Tier slots: `schedule.py` describes a random slot template mapped onto teams, tiers earned from last division finish | Adopt spec. DFLv2's bed-rotation module already matches the spec. The work log says the skeleton changed on 2026-10-12, so confirm what v1 actually runs |
| V1-04 | Salary cap | Cap $1,000 (the $800 floor was removed in Ed 8.8), reserve $25, minimum salary $5 (§11) | Cap $100M, floor 90%, bank up to $125M, minimum-salary scale 0.29–0.43 | Adopt spec. Owner ruled 2026-10-22: no banking (Ed 8.7). v1's 125 ceiling and 90% floor are retired |
| V1-05 | Practice squad | 8 | 16, at most 6 veterans (NFL 2025) | Resolved: owner ruled 16 (Ed 8.7); v1 is right here |
| V1-06 | Positions | 20 position labels with left and right line spots; 48 required places (§11) | 11 position groups; roster counts sum to 53; game-day minimums sum to 34 (`positions.py`) | Adopt spec; the position model must be rebuilt |
| V1-07 | Long snapper | Required, 1 | None found | Adopt spec |
| V1-08 | Game-day roster | 48, or 47 with fewer than 8 offensive linemen | 48 only; no 47 variant found by search | Adopt spec |
| V1-09 | Emergency fill-ins | Emergency list per position, out-of-position play (§11) | Not found in `rosters.py` | Adopt spec |
| V1-10 | Elevations and IR | 2 elevations a game, 3 a player, IR minimum 4 games | Same, plus a limit of 8 IR returns a season | Resolved: owner ruled yes; the 8-return limit is in Ed 8.7 |
| V1-11 | Offseason count | Silent | Only the 51 highest cap numbers count until the cut (NFL) | Resolved by Ed 8.9 (§11): the cap is enforced all year and, before the final cut, counts a club's dead money plus its highest 69 contracts (53 + 16), the NFL top-51 method adapted |
| V1-12 | Franchise and transition tags, restricted free agency | Removed (§11) | Implemented (steps 6e–6i, `RFA_SEASONS`, `RFA_MATCH_DAYS`) | Adopt spec; do not port |
| V1-13 | Waiver priority | Last season's draft order until four league weeks finish | Three weeks | Adopt spec |
| V1-14 | Coronation venue | Fan vote in June, announced July 4, 40-year window; finalist at home if its venue wins | Neutral site (`FINAL_IS_NEUTRAL_SITE`) | Adopt spec |
| V1-15 | Ambassador Tour dates | Calendar weeks 1, 4, 8, 11, 15, 18, 22; Bowl at C−7; one venue per round | Weeks 1–7; Bowl in week 8 | Adopt spec |
| V1-16 | Wild-card eligibility of a division's last-place club | Not excluded: "three best other clubs" (§3) | Excluded (`FIFTH_PLACE_WILD_CARD_ELIGIBLE = False`) | Resolved: owner ruled no; v1's exclusion is right and is now in Ed 8.7 |
| V1-17 | Season length in real time | About 55 hours in §0 arithmetic | `REAL_WEEKS_PER_SEASON = 2`; owner statements elsewhere of 168 hours, about 50, 48 | Resolved: target about 50 hours, non-binding (Ed 8.7). Build pacing in Phase 6 |
| V1-18 | Fairness bands | Not in the spec | 15 bands in `rules.py`, accepted by the owner as working limits | Port as the Phase 4 starting point; re-derive on the real engine |
| V1-19 | Decision points | Legal-option enumeration required (§11, roadmap) | `decisions.py`: bounded legal options, guard and fallback, choice logs | Port the design after review |
| V1-21 | Returning-ambassador lottery | Equal-chance seeded lottery for first-round slots 1–8; lottery identity follows the pick through trades (§12) | Weighted: 36 balls, 8 for the worst record down to 1 (`LOTTERY_WEIGHTS`) | Adopt spec; confirm with the owner when the draft is built (Phase 2) |
| V1-22 | CEO recall cadence | Recall threshold sentiment 20, CEO election every 3 seasons (§0) | Recall vote of one division a year on an 8-year cycle, 40% approval threshold | Adopt spec when governance is built (Phase 5) |
| V1-20 | Regression tooling | Validator and tests | Tiered fast and slow checks, golden fingerprints | Port the idea |

## Owner rulings, 2026-10-22
1. Cap room is not bankable.
2. Practice squad: 16.
3. A division's last-place club cannot take a wild card.
4. IR returns: yes, a limit of 8 per club per season.
5. Season length: about 50 hours, as a target; missing it is not a problem.

The snapshot zip does not exist for this project; DFLv2 is rebuilt from the specification.
