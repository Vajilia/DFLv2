# Roster and cap ledger (Phase 1)

Code: `src/dfl/ledger/` (`rules`, `positions`, `contracts`, `ledger`, `gameday`, and a **placeholder** `world` used only to drive the soak).
Tests: `tests/test_ledger.py`, `tests/test_ledger_determinism.py`. Soak: `tools/run_ledger_soak.py`.
Authority: Design Document Ed 8.14, sections 11, 12, 17. Where the document was silent, the interpretation below is the builder's; the owner delegated two of them (A1, A7) and ruled on the rest.

## What is enforced
- Money is exact (`Fraction`). Cap $1,000, reserve $25, minimum salary $5. **No salary floor** (Ed 8.8). **No banking.**
- **Cap timing (Ed 8.9, adapted from the NFL's top-51 rule).** The cap is enforced all year. Until the final cut a club's cap number is dead money plus its 69 highest cap hits (the camp bodies beyond that do not count); afterwards every contract counts. A signing, claim, trade or restructure that leaves a club over the cap *and* raises its cap number is rejected. A release is never blocked by the cap. At the final cut every club must be at or under the cap; the season cannot start with a club over.
- Practice-squad salaries count against the cap.
- **Rookie reserve (Ed 8.12, NFL rookie-pool principle).** Until a pick is made, its rookie-scale hit (rounds 8/9 at the round-7 scale) counts in the owner's cap number for that draft year; it follows a traded pick and is replaced by the real contract (the pick is spent just before the signing, so nothing is counted twice). Rookie contracts carry no guarantees, so the reserve adds nothing to committed money.
- **Pool limit (Ed 8.12).** `Player.pool_years` counts year openings spent unsigned in the pool; hiring resets it; at 4 the player is retired and deleted from the list (`Ledger.retire`, counted in `Ledger.retired`). Age retirement of free agents uses the same removal.
- Rosters: 53 + practice squad 16 (at most 6 veterans); preseason ceiling 90 combined; 48 required places by position (incl. long snapper); IR minimum 4 games, at most 8 returns per club per season.
- Game day: 48 active (47 with fewer than 8 healthy OL), 2 elevations per game, 3 per player, a named emergency player for any position with nobody left. A club forfeits only if fewer than 11 healthy players exist.
- Waivers: a released player with 4+ credited seasons goes straight to free agency (NFL vested-veteran rule); the releasing club keeps all of her dead money. Everyone else clears a one-day waiver period.
- Contracts: steady/rising/declining, 1-5 years, guarantees, prorated bonuses (up to 5 years), rookie scale (4 fixed years, R1 option $35), release charges, once-per-year split release, trade (receiver assumes unpaid base and guarantees, never the bonus), waiver claim.
- Viability invariant, reserve/commit with whole rollback, deterministic digest.

## Interpretations
- **A1 Strict "dead money" (builder's call).** Viability counts what the club would owe *if every contract were released at the start of that year*: guarantees, earned base, every unpaid bonus accelerated, dead charges. So committed money is capped at 735 in every year and a club cannot stack bonuses it could not survive cutting. Kept because it is the only reading under which "every club can always reach a legal roster" is provable.
- **A2 Pro-rated replacement cost.** In the current year the 48 x minimum replacement cost is pro-rated to the paid weeks still to come, since earned salary is already counted.
- **A4 Drift is not frozen.** Earned salary accrues weekly, so committed money can drift over the limit through time alone. A move is rejected only if it leaves a year over the limit **and** raises that year's figure.
- **A5 Release can carry a club over the cap.** The accelerated bonus may exceed the cap hit it replaces. The rule above lets the release happen; the club then cannot add cap until it is back under. (Soak under Ed 8.8: peak in-season overage about 66; under Ed 8.9 the 6-year check shows none.)
- **A7 Restructure (builder's call).** Converts base salary into a bonus prorated over the remaining years (at most five), never leaving less than the minimum salary; a normal transaction under viability and cap timing.
- **A8 The cap check uses $1,000.** The $25 reserve appears only in the viability invariant.

Retired by Ed 8.8/8.9: A3 (floor supplement exempt from the gate), A5-old (overage tolerated), A6 (practice-squad pay, now a rule).

## Soak result
100 years, 48 clubs, seed 1, Ed 8.8: PASS (figures and digest `96fec3c8c0dd...` in `docs/BUILD_STATUS.md`). Side effect worth knowing: short-handed games rose from 0.04% to 0.7% because a club at the cap cannot always sign a replacement mid-season; nobody forfeited. Regenerate with `python3 -I tools/run_ledger_soak.py --years 100 --seed 1 --report out/ledger_soak.md` (about 35 minutes).

## Not evidence for
The world driving the soak (player ratings, aging, prices, trades, injuries) is a placeholder. It shows the rules cannot jam or be broken; it says nothing about market realism (open item 3).
