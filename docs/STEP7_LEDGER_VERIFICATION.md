# Step 7: roster, contract and transaction verification (2026-10-30)

Scope: Design Document Ed 8.54 §0, §11 and §12 as the ledger (`src/dfl/ledger/`) implements them. Method: the document's numbers are read from its text and compared with the code; an **independent model** (`tests/ledger_oracle.py`, typed from the document, calling none of the ledger's money or list functions) recomputes cap numbers, commitments, limits and list soundness from raw fields; the ledger is then driven through long seeded random sequences of every kind of transaction, legal and illegal, and compared with the model after **every** call. Nothing here is merged or deployed.

## 1. What was checked, and how

| Check | Where | Result |
|---|---|---|
| Every number in §0/§11/§12 that the code holds (cap, reserve, minimum, comp threshold, rookie scale, option, roster/squad/ceiling/places, 69 counted, 4 vested seasons, 22 paid weeks, practice-squad veterans, 48/47/8 active, IR 4 games and 8 returns, 2 elevations and 3 per player, 5 covered and proration years, $735, margin flag, positional minimums, jersey blocks and overflow, waiver switch after 4 weeks, trade close) read from the document text and compared with `rules.py`, `numbers.py`, `positions.MINIMUMS` and the model | `tests/test_step7_spec_constants.py` (12 tests) | all agree |
| Contract money: cap hit by year, bonus charged exactly once over its proration, uncharged bonus, release charges for every year/week × split × keep-guarantees, what a receiver assumes, restructure (moves money in time without changing the total, never raises the year's hit, base ≥ minimum), rookie scale and option | `tests/test_step7_oracle.py::ContractMoney`, 3,000 seeded random contracts | all agree with the model |
| Ledger against the model after every call, on 6-club leagues for 2–3 league years: signings (cheap and expensive, roster and squad), releases (some split), trades (players and picks, some of picks not owned), restructures, IR in and out, promotions, waiver resolution, game-day lists, injuries, the draft, camp, the final cut, 22 paid weeks, year turn. Compared each time: cap number (top-69 rule before the cut, rookie reserve), commitments by year, limit by year, and list soundness (one list per player, club/where agree, contracts cover the year and obey the limits, numbers unique and inside their blocks in season, list sizes, each pick owned once, no negative dead money, waiver records match) | `tests/test_step7_oracle.py::RandomSequences` | no disagreement with the model after fixes 1 to 4 in §2 |
| A **rejected** call leaves the ledger exactly as it was (full state digest, caches aside) | same driver | holds |
| An **accepted** signing, claim, trade or restructure never leaves a club over the cap with a higher cap number than before; no accepted move leaves a year over the viability limit with a higher figure than before (§11 Cap timing, Viability invariant) | same driver | holds |
| Scenario tests for rules that need their own setup: midseason deals, claims keeping agreed years, the year turn with players on waivers, vested veterans and waivers, who keeps the guarantee and who the bonus, IR gives no cap relief | `tests/test_step7_rules.py` | pass |
| Waiver claim order | `tests/test_waiver_priority.py` (6) | pass (reading W1 below) |
| Hash-order independence of trade proposals; a placeholder year under two hash seeds | `tests/test_hash_order.py` (2) | pass; the first fails on the old key |
| Existing ledger tests (51), trading, trading window, emergency lists, camp repair | unchanged except as noted | pass |

## 2. Defects found and fixed

1. **Game-day active list could exceed the limit (§11 "Game-day active roster 48, or 47 with fewer than 8 offensive linemen").** A practice-squad player elevated to fill a position gap was added on top of a full list, giving 49 or 50 dressed. And when the 47 limit applied (fewer than 8 linemen) the list was cut from its tail, which is where elevated players sit, so a player could stay counted as elevated (using up one of her three elevations) without being dressed. Now an elevated player displaces the weakest player at a position the club holds beyond its league minimum, never one that would open a gap, and the 47 trim removes the weakest non-elevated players. Found by the random driver; tested there (every list ≤ its limit, every elevated player dressed).
2. **Waiver priority was a seeded shuffle, not the document's rule (§11 "Priority is last season's draft order until four league weeks finish, then weekly from current standings, with Tour standings in their prior draft bands").** Now `Universe.waiver_priority()` supplies it, `World.waiver_order()` uses it, and no random numbers are drawn for it. Reading W1 below. A bare `World` (ledger soak) has no standings and uses club-id order.
3. **Midseason deals could be multi-year (§11 "Midseason deals pay remaining weeks only and expire at year close").** `World.offer` produced one to five years at any week, and `Ledger.sign` accepted any length. A signing after the first game is now one year only (`Ledger.sign` rejects longer; `World.offer` offers one year). Contracts a club *takes over* by trade or waiver claim keep the years already agreed.
4. **Hardening, not reachable through the World:** `Ledger.new_year` with players still on waivers left records whose contracts had expired; a later claim would have failed on them. Anyone still on the wire when the year turns now clears to free agency.

5. **Trade proposals depended on the order Python iterates a set in (found by the 22-player restore soak, not by the model).** `trading._assets` listed a club's tradeable picks with `sorted(picks, key=(-round, -year))`. Two picks of the same round and year with different original clubs tie, and the tie was broken by the set's own order, which differs between processes (hash randomisation). It showed only once a club held two such picks, so earlier hash-seed checks and soaks did not meet it. Reproduced: from one saved state, the same event, run in separate processes, gave two different leagues (3 of 8 runs); the pick offered differed (`(2002, 1, 'Cincinnati')` or `(2002, 1, 'Dallas')`). The key now ends with the original club. `tests/test_hash_order.py` shows 8 different orders from 8 hash seeds on the old key and one on the new, and a year of the placeholder league gives identical per-event digests under two hash seeds. A restored or resumed league could have diverged from an uninterrupted one in exactly this way; whether it explains the original uninstrumented failure is **not** shown (that failure was a mismatch at import, which steps no event), and that item stays OPEN.

Each change alters simulated outcomes (random draws, claims, in-season contracts), so league files written by earlier code are carried forward with `upgrade --run-originating-code` like any other code change; they are not byte-compatible by replay.

## 3. Not built, or built narrower than the document (not changed; listed so nothing is claimed that is not there)

- **Extensions** ("Extensions require two completed seasons and mutual consent"): no extension transaction exists. Contracts end at their last year and the player enters the pool.
- **Postseason-only replacements** ("pay one salary-week equivalent"): not built; the ledger has no postseason signing rule beyond the general midseason one.
- **Restructure** is offered for the current year's base only; the document also allows a future year's base.
- **Bowl final-53**: still incomplete (§7 of the handoff).

## 4. Owner questions (added to `docs/OWNER_RULINGS_NEEDED.md` as items L1 to L4)

- **L1 / W1 Waiver priority.** The built reading: worst record claims first, as in the draft; the standings used are those of the league weeks finished before the paid week being processed (so weeks 1 to 4 use last season's draft order, week 5 onward the standings); each of the eight Tour clubs keeps the place her prior draft band gave her, and the clubs at home take the other places in standings order. In the first season there is no earlier draft, and club-id order stands in until the standings take over.
- **L2 Split release.** Built: this year's own bonus portion stays on this year; the later portions divide 50/50 between this year and next. The other reading divides the whole uncharged bonus, this year's portion included. They differ by half of this year's portion.
- **L3 First-round option.** Built: every first-round pick gets the fifth year at $35 automatically. The document says "First-round option salary $35" and does not say who decides or when.
- **L4 Unbuilt items** in §3: confirm they are wanted before they are built (extensions in particular change how clubs keep players).

## 5. Things to know about the checks themselves

- The model reads the contract **fields**, so it cannot catch an error in a field's meaning, only in the arithmetic and the rules built on it. Where the document allows two readings (split release, the rookie reserve as one of the top-69 hits) the model states which it takes and why, and the question is in §4.
- `Club._cmap` / `_cmap_week` (the committed-money cache) is still part of the state digest. It is a derived cache, and the model compares the cached figures with a fresh recomputation after every call, so it is coherent in every sequence run; but a read-only query that fills it changes the digest. Digests are taken only at event boundaries, where live and replayed runs fill it identically. Excluding it is the cleaner design and needs a digest-algorithm and save-schema change; recommended for the next format change, not made here.
- The random driver is a tester, not a league: it makes moves a club would not. Some clubs cannot fill their required places under the cap in later years and so do not start a season; this is counted and shown by the test (at least four seasons are played across the three seeds).

## 6. Rule-by-rule trace (§11 and §12, in the document's order)

V = verified against the document by a test that does not use the ledger's own money functions; C = changed in this step; U = not built; Q = owner question (§4). "model" is `tests/ledger_oracle.py`; "drive" is the random driver.

| Rule (abbreviated) | Status | Evidence |
|---|---|---|
| Roster 53 + squad 16, ≤ 6 squad veterans over 2 credited seasons, ceiling 90, 48 required places | V | constants test; model `structural_problems` in drive (sizes before and after the cut); `test_ledger` squad tests |
| Positional minimums (incl. HB 3 or HB 2 + FB 1), checked at the cut | V | constants test parses the list; `cut_down` refuses a short club (`test_ledger`); drive's cut routine needs them to start a season |
| Operate short, but must field 11 healthy players or forfeit | V | `test_ledger` forfeit test; ledger soak (0 forfeits) |
| Game-day 48 / 47, IR (4 games, 8 returns), 2 elevations a game and 3 a player, revert afterwards, signing and claims under the viability invariant, emergency lists | V, C | constants test; drive checks every list ≤ limit and every elevated player dressed (**defect 1 fixed**); `test_ledger` emergency tests; `test_emergency` |
| IR and suspension give no cap relief | V | `test_step7_rules::InjuredReserve` (model cap number unchanged) |
| No banking | V | `test_ledger::test_no_banking`; model has no carry-over term |
| Cap $1,000, reserve $25, minimum $5, comp threshold $75 | V | constants test |
| No salary floor | V | `test_ledger::test_there_is_no_salary_floor` |
| Upgrade signings at camp | not in this step | World behaviour, covered by `test_camp_repair`/ledger soak; not a ledger rule |
| Legal-move enumerator | V | `test_ledger`; ledger soak (0 legal-move failures) |
| Cap timing: top 69 until the final cut, every contract after; over-cap raise rejected; release never blocked; cut and season start refused over the cap | V | model `cap_number` compared after every call in drive; `test_ledger` cap tests |
| Practice-squad salaries count | V | model counts squad and IR contracts; `test_ledger` |
| Dead money counts; release/trade accelerates every uncharged bonus | V | `ContractMoney` (release charges, every year/week/option) |
| Viability invariant (dead ≤ $735 strict reading, current-year pro-rating, rejected only if over and raised) | V | model `committed`/`limit` after every call; drive's "no accepted move raises an over-limit figure" |
| Transactions rejected only by roster rules, viability or cap | V | drive: rejected calls are all of those kinds or illegal-by-construction (wrong club, not free); none changed the ledger |
| Restructure: current/future base into a bonus over the remaining years (≤ 5), base ≥ minimum, gated | V (current year); U (future year) | `ContractMoney` restructure test; `test_ledger::test_restructure_is_gated`; drive |
| Split release once per club per year | V, Q (L2) | `test_ledger`; model states the reading |
| Viability margin logged, flag below $50 | V | constants test; `test_ledger::test_margin_…` |
| Contract shapes; ≤ 5 covered years and proration years; bonus paid on signing, prorated evenly; receiver takes unpaid base and guarantees, never the bonus | V | `ContractMoney`; `test_step7_rules::Waivers`; drive trades |
| Removed features absent (incentives, roster bonuses, June 1, franchise tag, …) | V by absence | no code path or field for them |
| Rookie reserve until the pick is made; moves with the pick | V | model `rookie_reserve` in drive with the draft unmade; `test_ledger` reserve tests |
| Rookie scale, four fixed years, round-1 option $35 | V, Q (L3) | `ContractMoney` rookie test; constants test |
| Jersey numbers: blocks, overflow, camp relaxation, stewardship | V | constants test parses both tables; model checks uniqueness and legality in season in drive; `test_ledger` number tests (9) |
| Extensions | U | §3 |
| Salary over 22 paid weeks; midseason deals pay remaining weeks and expire at year close | V, C | `ContractMoney` (start-week share); **defect 3 fixed**; `test_step7_rules::Midseason` |
| Postseason-only replacements | U | §3 |
| Waivers: one simulated day, vested at 4 seasons skip, guarantees and bonus stay with the releasing club (bonus never moves), priority order | V, C, Q (L1) | `test_ledger`, `test_step7_rules::Waivers`, `test_waiver_priority` (**defect 2 fixed**) |
| Future-year minimum-salary reservations are feasibility checks, not charges | V | model `committed`/`limit` (not charged in `cap_number`) |
| §12 trade assets: named players and owned picks in the next three uncompleted drafts; window; no vote; over-cap raise rejected | V | `test_ledger`, `test_trading`, `test_trading_window`; drive trades (including picks not owned, which are rejected without change) |
| §12 draft order, lottery, returner block, compensatory picks | not in this step | `test_draft`, `test_standings` (Step 6 and earlier) |
