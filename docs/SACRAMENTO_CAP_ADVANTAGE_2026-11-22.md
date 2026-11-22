# Sacramento cap advantage: work record (2026-11-22)

Branch `feature/sacramento-cap-2026-11-22`, started from `4a1f26e` (the Milestone 1 state). The frozen validation configuration (candidate `205f891`, harness `5a97dad`, alert release, `tools/vtest/PIN`) is **not** changed on this branch's parent branches. This branch changes engine code on purpose, so `pin-check` fails here by design; it is a separate line.

Approved by the owner (2026-11-22): the DFL is for his personal viewing; Sacramento Sting gets an intentional salary-cap advantage, proposed at 1.15, configurable, default 1.00 for every team, no other competitive rule changed.

## 1. What the cap model is (inspected, not assumed)

* One scalar, `Ledger.cap`, owned by the league (parity profiles set it; default `rules.CAP` = 1000). There is no per-team cap.
* Clubs are keyed by the **city name** (`team_city`, e.g. `Sacramento`). The canonical public identifiers are `team_id` (33) and `abbr` (`SAC`) in `data/season1/TEAMS.csv`.
* Every enforcement point reads that scalar: `overage`, the transaction wrapper `_txn` (every signing, release, trade and draft pick runs through it), the final-cut check, the viability invariant (`limit`, `viability_margin`, `_viable`), management's free-agency and upgrade signings (`budget_floor`, `cap_number + v > cap - RESERVE`), the owner/fan "spending against the cap" signal (`governance.signals`), the world audit (`limit(y)`), and the public roster feed (`cap.limit`).
* **Management has a spending target tied to the cap**: `budget_floor = cap - RESERVE - 90` ends a club's free-agency loop once its payroll reaches it, and signings must fit under `cap - RESERVE`. Both are written in terms of the cap, so they scale with a per-club cap and management can use the extra room. Prices come from market tables independent of the cap, so extra room buys more or better players at market prices; it does not change what a player costs.
* Nothing in `engine/`, the resolver or injuries reads the cap (checked by search; a test keeps it that way).
* Persistence: the league is a `year.Universe` saved by `persist.Runner` (SQLite: snapshots, a hash-chained journal of events, `meta`). The state digest covers every instance attribute, so **adding an attribute to every league would change every default league's digest**.

## 2. Smallest compatible design

1. **Ledger**: class attribute `cap_multipliers = None` (the existing pattern for `trading`, `options_closed`). A league without the option has *no instance attribute*, so its state and digest are byte-for-byte what they were. New `cap_of(cid)` = `cap * multiplier` (exact `Fraction`). The scalar `cap` stays as the league's base cap. All enforcement points above call `cap_of(cid)`; `limit(year, cid=None)` keeps its old meaning when `cid` is omitted.
2. **Setting rules** (`Ledger.set_cap_multipliers`): keys must be existing club ids; values exact (decimal string, int or Fraction; floats and booleans refused); `1.00` to `1.50` inclusive; a value of exactly 1 is not stored; an empty result removes the attribute (so a reset league equals a default league). A **decrease** is refused if the club's current cap number or committed money would exceed the new limit (it would strand existing contracts), with a message naming the club.
3. **Identification**: `Universe.configure_cap_multipliers({"SAC": "1.15"})` resolves the canonical `abbr` by **exact match** against the league's team table, then keys the ledger by that club's id. Unknown abbreviations are refused. No substring or name matching anywhere.
4. **Fixed for a season**: the Universe method refuses unless the league is between league years (`cursor is None`: the year has closed, the next has not opened). It therefore takes effect when the next year opens, before contracts, free agency and the draft. The Ledger method also refuses in-season phases.
5. **Creation-time setting**: `Universe(..., cap_multipliers={"SAC": "1.15"})` (stored with the file's construction arguments by `Runner.create`, so it is in the file's metadata).
6. **Durable offseason step**: `Runner.configure_cap_multipliers(...)` applies the step, replaces the checkpoint snapshot of the closed year in one transaction, and appends a record to the file's `meta["cap_config"]` (after which sequence number, effective year, values). `Runner.open` and `_replay` re-apply a recorded step at its sequence number (idempotent), so a fallback to an older snapshot still reproduces the journal. Backup and restore copy the whole file, so they carry both. `mismatch_report` includes the setting.
7. **Public feed**: `cap.limit` in a club's roster file shows that club's effective limit. Other files are unchanged; for a league without the option the feed is identical.
8. **Not touched**: ratings, game resolution, injuries, the draft order, market prices, every other rule.

If the engine had needed a rules change beyond this (for example per-club prices), this record would stop and recommend one approach. It does not.

## 3. Tests (all disposable; none uses a real league)

1. Default preserved: a default league's digest after N events is identical to the baseline commit `4a1f26e`'s, checked by running both code versions on the same seed.
2. Only the configured club has a non-base limit; every other club reads the base cap.
3. Same effective limit everywhere: `overage`, the transaction check, final cut, viability, management, governance and the feed all agree for the configured club and for an unconfigured one (one assertion per point, each fails if its point is reverted).
4. Invalid settings (unknown club, below 1, above 1.50, float, bool, NaN text, malformed string, name instead of abbreviation, a decrease that strands contracts) are refused with a message that says why.
5. Save/load, backup/restore and fallback-snapshot replay keep the setting and reproduce the journal.
6. In-season (mid-year) change refused at the Universe and at the Ledger.
7. Management can use the room: a disposable league where the configured club ends a free-agency period with payroll above the base cap, never above its effective cap.
8. No direct outcome bonus: nothing under `engine/` reads the cap (source check), and one game resolved from identical rosters and generator state gives an identical result with and without the multiplier.

## 4. Spoiler barrier (binding)

* Test leagues use **only** seeds 7770001 to 7770032 (and 7771001 upward for unit tests). These are reserved for testing and are never to be used for the viewing league or reused after this work.
* Outcome-bearing artifacts (per-run records, standings, champions, careers) stay in a private scratch folder outside the repository and are not reported. Nothing outcome-bearing is committed.
* The owner-facing report states only: the mechanism works, management uses the room, uncertainty remains, and the recommendation.
* The viewing league is not created, opened, advanced or inspected.

## 5. The paired comparison: method fixed before running

* **Units**: 32 pairs, one per seed 7770001-7770032. Each pair runs the same seed twice from the same creation: arm A (all clubs 1.00) and arm B (Sacramento 1.15, set at creation). Engine on, spatial off (about 18 s per simulated year measured; the broadcast-grade spatial mode is not used for this comparison and is a stated limit). Four league years per run. Parity profile `tight`; everything else default.
* **Budget**: at most 2 hours of simulation; the planned total is about 32 x 2 x 4 x 18 s = 77 min of CPU, run on two processes. One execution. No second run, no change of multiplier, no change of method after seeing results.
* **Measures** (Sacramento only, paired by seed, all four years; the founding year already includes the first offseason with the option in force):
  1. *Mechanism*: effective limits are exactly 1.15 x base for Sacramento and base for the rest; no club ever over its own limit at a final cut.
  2. *Management use*: Sacramento's mean payroll at the final cut in B minus in A, and the share of the extra 150 it filled.
  3. *Strength before games*: Sacramento's mean starter overall at the start of each regular season, B minus A (no game outcome involved).
  4. *Competitive effect*: regular-season win percentage, B minus A, over the years in which Sacramento is an active club in both arms (a club exiled the year before is an ambassador and has no regular-season record; such years are skipped for that pair, and at least 75% of pairs must have a usable year); and how often Sacramento wins the title in B (share of its seasons).
* **Decision rule (fixed now, before the comparison runs)**, applied to the paired means with a 90% paired bootstrap interval (10,000 resamples, fixed seed 20261122). *Management uses the room* means: in at least 75% of pairs Sacramento's payroll in B rises above what a base-cap club may carry (975) at some point in the first year, and the paired mean payroll difference at the final cut has an interval above zero.
  * **Evidence insufficient** if the mechanism check fails (Sacramento's limit is not exactly 1.15 x base, another club's is not base, or any club is over its own limit at a final cut), or management does not use the room, or the win-percentage interval is wider than 0.10 in total (+/-0.05), or none of the rules below applies.
  * **Adjust down** if the mean win-percentage gain is 0.10 or more, or Sacramento wins more than 20% of titles in B.
  * **Adjust up** if the interval's upper end is below +0.01 (no useful advantage).
  * **Retain 1.15** if the gain is at least +0.03 with the interval's lower end above 0, the gain is under 0.10, and the title share is at most 20%.
  * *Second amendment, also before any comparison run: all four years are scored, and exile is handled as above. A two-pair dry run of the tool on other test seeds (7770900 and 7770901, discarded) showed that a club can have no regular-season record in a year, which the first draft did not anticipate.*
  * *Amendment, made before any comparison run (the first draft required that management fill half of the extra 150 on average; that measures a payroll stock that depends on contracts already held, not whether management can use the room, so it was replaced by the test above). An exploratory run on a different test seed (7771002) informed the change; it is not part of the comparison.*
* **What the owner sees**: the verdict word, one sentence on each of the three questions (works / management uses it / uncertainty remains), and the practical limit below. Numbers stay in the private artifact.
* **Practical limit**: 32 simulated leagues of 3 scored seasons can show whether the advantage is large enough to notice on average. They cannot show that any single future season will end a particular way, and they cannot exclude rare outcomes; a finite experiment cannot prove that success is never guaranteed. The comparison uses the faster engine mode, so it shows direction and rough size, not exact odds.

## 6. Status

Design written; implementation, tests and the comparison follow in this branch. Results are recorded in `docs/PROJECT_STATE.md` as verdict only.
