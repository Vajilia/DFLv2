# Roster and cap ledger (Phase 1)

Code: `src/dfl/ledger/` (`rules`, `positions`, `contracts`, `ledger`, `gameday`, and a **placeholder** `world` used only to drive the soak).
Tests: `tests/test_ledger.py`, `tests/test_ledger_determinism.py`. Soak: `tools/run_ledger_soak.py`.
Authority: Design Document Ed 8.7, sections 11, 12, 17. Where the document is silent, the interpretation below is mine and is listed so the owner can overrule it. Nothing here changes the document.

## What is enforced
- Money is exact (`Fraction`). Cap $1,000, floor $800, reserve $25, minimum salary $5, **no banking** (a club's limit is the same every year; unused room is lost).
- Rosters: 53 + practice squad 16 (at most 6 veterans); preseason ceiling 75 combined; 48 required places by position (incl. long snapper); IR minimum 4 games, at most 8 returns per club per season.
- Game day: 48 active (47 with fewer than 8 healthy OL), 2 elevations per game, 3 per player, a named emergency player for any position with nobody left. A club forfeits only if fewer than 11 healthy players exist.
- Contracts: steady/rising/declining, 1-5 years, guarantees, prorated bonuses (up to 5 years), rookie scale (4 fixed years, R1 option $35), release charges, once-per-year split release, trade (receiver assumes unpaid base and guarantees, never the bonus), waiver claim.
- Viability invariant, two-tier transactions, reserve/commit with whole rollback, deterministic digest.

## Interpretations (please confirm or overrule)
- **A1 Strict "dead money".** Viability counts what the club would owe *if every contract were released today*, per future year: guarantees, earned base, every unpaid bonus accelerated onto the year of release, dead money and floor supplements. Stricter than counting only dead money already on the books. Effect: committed money is capped at 735 in every year, so a club cannot stack bonuses it could not survive cutting.
- **A2 Pro-rated replacement cost.** In the current year the "48 x minimum" replacement cost is pro-rated to the paid weeks still to come, since earned salary is already counted.
- **A3 Floor supplement is outside the gate.** The cut-down tops a club up to $800 by a guaranteed supplement. That is applied by the league, not requested by the club, so it is not a transaction the gate can refuse. It can push a club past the 735 limit in rare cases (the soak shows 169 supplements in 576 club-years, none breaking the cap).
- **A4 Drift is not frozen.** Earned salary accrues weekly, so a club can drift over the limit through time alone. A move is rejected only if it leaves a year over the limit **and** raises that year's committed money. A club over the limit can still do things that help.
- **A5 Overage.** A club may be over the cap in-season (two-tier rule); the cut to 53 refuses to proceed until it is cleared. The document gives no upper bound on overage. Soak peak: about 106.
- **A6 Practice-squad pay.** Practice-squad players carry ordinary contracts and count against the cap like anyone else. The document does not say otherwise.
- **A7 Restructure** converts base salary into a bonus prorated over the remaining years (never below the minimum salary). The document does not define it.

## Soak result
100 years, 48 clubs, seed 1: PASS (see `docs/BUILD_STATUS.md` for figures and digest `49a0b5eb38da...`). Regenerate with `python3 -I tools/run_ledger_soak.py --years 100 --seed 1 --report out/ledger_soak.md` (about 35 minutes).

## Not evidence for
The world driving the soak (player ratings, aging, prices, trades, injuries) is a placeholder. It shows the rules cannot jam or be broken; it says nothing about market realism (open item 3).
