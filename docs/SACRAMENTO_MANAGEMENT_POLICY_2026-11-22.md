# Sacramento management policy: affordable roster upgrades (work record, 2026-11-22)

Follow-up to `docs/SACRAMENTO_CAP_ADVANTAGE_2026-11-22.md`, authorized 2026-11-22 03:30 PST, on `feature/sacramento-cap-2026-11-22` from `82f7123`. Local code, tests and documentation only. The cap multiplier stays 1.15 and is not retuned. Production, the viewing league and the frozen validation configuration are not touched. Every league used here is disposable, on seeds reserved for testing; no outcome is reported.

## 1. Why management stops recruiting (diagnosis; mechanism counts only)

Measured on disposable leagues (8 test seeds, 2-3 league years each, Sacramento at 1.15, engine off) by instrumenting the free-agency loop and the camp upgrade routine. Counts are club-years.

| Cause | What it is | Sacramento (12) | Other clubs (564) |
|---|---|---|---|
| Insufficient spending appetite | the spending floor is `cap - reserve - 90`; it scales with the club's own cap, so the 1.15 club's floor is 150 higher | 2 stops at the floor | 299 (53%) |
| Roster-size constraint | roster plus practice squad reaches the preseason ceiling less the 9 places held for picks and compensatory signings | **7** | 84 (15%) |
| No worthwhile player | the position drawn at random has no free agent the club rates above 60 and the loop **ends** instead of trying another position | 2 | 166 (29%) |
| Iteration limit | 40 passes; failed negotiations count as passes | 1 | 15 (3%) |
| Refusal to replace a weaker incumbent | see below | - | - |

* **Spending appetite is not the limit.** Sacramento's payroll at the end of free agency was 67-92% of its 1,150 cap and at camp about 79% on average, so there was room the whole time. Raising the floor would only buy more of what the loop already buys.
* **Roster size is the main stop for Sacramento.** The ceiling is a rule and is preserved.
* **Free agency never replaces an incumbent.** It fills shortages of the template and, once those are filled, draws a position at random and signs the best free agent there without comparing her to the player already holding the place: 53 of 96 such signings (55%) were players the club itself rated below its incumbent starter. Only the camp routine (`upgrade`) compares, and it is limited to 3 signings and to the single best free agent at each position. On average 5.75 positions had a free agent the club rated at least 5 points (the ordinary margin) above its weakest starter at camp, and the ordinary routine reached its limit of 3 in 5 of 12 camps.
* **So:** the unused room is mostly a procedure and roster-slot limit, not a lack of money and not a lack of worthwhile players. The policy therefore works at camp, where a signing is also a replacement (the cut that follows sheds whoever the club values least), and does not touch the free-agency loop.

## 2. The smallest policy (implemented)

An opt-in, per-club set, default empty. A club in the set runs `World.upgrade_more` after the ordinary `upgrade` at camp, and nothing else changes for it.

* **Considered:** at every starter position (the ordinary `STARTERS` table), including positions that are already filled, every free agent whose value (the club's own view of her overall with age counted, `World.value`) beats the club's weakest starter there.
* **Worthwhile (uses the club's own uncertainty):** the gap must be at least the ordinary upgrade margin (5) **and** at least the standard deviation of the comparison, the root-sum-square of the two estimates' errors (`World.view_sd`, the error the club's `view` is drawn with: credited seasons, its general manager's evaluation, own player or not). About one standard error, 5 to 7 points in practice. The rule is fixed here and is not tuned.
* **Affordable, evaluated before committing:** the price she accepts (`negotiate`) plus the dead money that releasing the displaced starter would create beyond the salary it relieves must fit under the club's own cap less the reserve. A refused or failed candidate is not tried again.
* **One signing at a time, biggest gap first**, recomputing the weakest starter each time. It ends when nothing qualifies (doing nothing is the correct result) or the roster has no room. The roster ceiling, contract rules, numbering, cut-down to 53 and 16, and cap check at the final cut are the ordinary ones.
* **Information:** only the club's views, values, quotes and the ledger. No hidden rating, no development, no future results. A test reads the routine's source for any such access and checks that nothing under `engine/` knows the policy.
* **Persistence and timing:** `World.upgrade_policy` is a class default (`None`) set on the instance only when a club is named, so a league without it has identical state and digest. A default league's digest after 120 events (engine on) is identical between `4a1f26e` and this code. Set at creation (`upgrade_policy=["SAC"]`, recorded in the file's `universe_args`) or by an offseason step (`Runner.configure_upgrade_policy`, recorded in `meta["mgmt_config"]`, re-applied on replay, refused unless between league years). CLI: `league_store.py init --upgrade-policy SAC`, `configure-policy`.
* The cap multiplier and the policy are independent settings.

## 3. General defects found (documented, not fixed)

1. **Founding can fail on some seeds.** Creating a league on seed 7771413 raises `Rejected: sign: breaks the cap for Tampa Bay` while founding (practice-squad signing, `World._found`), on the baseline code `4a1f26e` too. Frequency: 1 of 60 test seeds screened (7771400-7771459), about 2%; all 32 seeds of the comparison below create. A launch seed must be checked by creating the league first.
2. **The free-agency loop ends early.** It stops the first time the randomly chosen position has no free agent above 60 (`break` rather than another position): 29% of club-years.
3. **Free agency signs at filled positions without comparing to the incumbent** (55% of those signings were below the incumbent by the club's own view), spending roster slots and cap.
4. **Failed negotiations use up the 40-pass allowance.** Minor (3% of club-years).
5. The ordinary camp upgrade looks only at the best free agent per position and signs at most 3.

None of these is changed here. 2 to 5 are the reason the policy has room to matter; fixing any of them changes every club and needs its own decision and validation.

## 4. Tests

`tests/test_upgrade_policy.py` (27 tests): default state and digest unchanged and equal to the baseline commit; naming then clearing is the default; the policy never runs without the option; only the named club is touched; only worthwhile (margin and uncertainty), affordable signings happen and each is checked against the numbers at the moment of signing; nothing qualifying changes nothing; the uncertainty rule is exercised both ways; cap and roster-room limits stop it; every club is legal after the cuts; no hidden-information access; creation, restore, offseason step, in-season refusal, recovery from an older snapshot, export/import and the CLI.

## 5. Comparison method (registered before the run; committed before it starts)

* **Arms**, each from the same creation: **A** default league; **B** Sacramento cap 1.15; **C** Sacramento cap 1.15 plus the policy. Engine on, spatial off (the fast mode; a stated limit), parity profile `tight`, four league years.
* **Seeds:** the first 32 seeds from 7770101 whose league can be created (screened by creating the league with no option; a founding failure is independent of every option and skips the seed). 96 leagues, about 55 minutes on two processes. One execution; no second run; no change of method, multiplier or policy after seeing results.
* **Measures** (Sacramento only, four years, paired by seed):
  1. Legality: in every league, no club over its own cap at the final cut, no roster above 53 or practice squad above 16, Sacramento's limit exactly 1.15 x base in B and C, every other club's the base.
  2. Policy firing: share of seeds in which C has at least one policy signing.
  3. Starter strength before games (true ratings of Sacramento's starters after the final cut; a measurement that management never sees), C minus B: are the upgrades real rather than chasing noise?
  4. Payroll at the final cut, C minus B: is the room used?
  5. Regular-season win percentage, C minus A (the whole advantage) and C minus B (the policy's part), over years in which Sacramento is an active club in both arms (an exiled club has no record); at least 75% of seeds need a valid year.
  6. In C: Sacramento's title share and the share of its seasons at .500 or worse.
* **Statistics:** paired means with 90% bootstrap intervals (10,000 resamples, seed 20261122).
* **Decision rule, in this order:**
  1. **Evidence insufficient** if legality fails, or fewer than 75% of seeds are valid, or the interval of C minus A win percentage is wider than 0.10.
  2. **Ineffective** if the policy does not use the room for worthwhile upgrades: it fires in fewer than 75% of seeds, or the strength interval (C minus B) or the payroll interval (C minus B) does not lie above zero.
  3. **Excessive** if the C minus A win-percentage gain is 0.10 or more, or Sacramento wins more than 20% of titles in C.
  4. **Ineffective** if the upper end of the C minus A win-percentage interval is below +0.01.
  5. **Useful advantage** (useful and fallible) if the gain is at least +0.03 with the interval above zero and the policy criteria pass, and Sacramento remains fallible: title share at most 20% and at least 10% of its seasons at .500 or worse.
  6. Otherwise **evidence insufficient**.
* **What the owner sees:** the verdict word and one sentence on each of: rules held, room used for worthwhile upgrades, uncertainty remains. Numbers stay in a private artifact outside the repository.
* **Practical limit:** 32 simulated leagues of up to four seasons can show whether the policy gives an advantage that is noticeable on average and whether it produces certain outcomes. They cannot show that any particular future season will end a particular way, and they cannot exclude rare outcomes. The fast engine mode shows direction and rough size, not exact odds.
* **Disclosed before the run:** a two-league dry run of the tool on other test seeds (7770900, 7770901; discarded) to check the pipeline; it showed the policy firing in 1 of 6 leagues in a first year, which is why the uncertainty rule is stated openly as the likely limit on how often it fires.

## 6. Status

(updated below after the run)
