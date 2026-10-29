# Independent-review handoff at commit `9211b28`

Written 2026-10-29 for an independent reviewer. Repository `Vajilia/DFLv2`, branch `audit/completion-plan`. **Nothing is merged or deployed.** This supersedes the status lines of `docs/REVIEW_HANDOFF_STEPS_3_4.md` (whose architecture section is still accurate and should be read with this).

## 0. Status in one paragraph

Saving, restart recovery, backup/restore, the upgrade path, the permanent archive and archive consistency (Steps 3 and 4) are built and tested. One failure is **open and unexplained at the reviewed commit**: a single `StateMismatch` on an unchanged state in one full test run (section 2). A mechanism that reproduces it has since been found in the exhaustive state digest and fixed in later commits, but the original failure was not instrumented, so **persistence is not described as fully verified**. The Bowl's ballots and final-53 construction are **incomplete** (not built, not fully specified). Live-clock pacing is deferred. Step 7 (roster, contract and transaction verification) is next.

## 1. What is at `9211b28` and what came after

| Commit | Content |
| --- | --- |
| `1317f3b` | Step 3: durable saving, restart recovery, atomic event processing, backup/restore |
| `dd9ef8e` | C11 save-schema/code separation and upgrade path; Step 4 permanent archive |
| `a1e0fe7` | Archive consistency at open (save schema 3) |
| `f49ede1` | Step 5: season controller and calendar verification; stale game dates after Season 1 fixed |
| `c87c6aa` | Rulings document: day counts |
| **`9211b28`** | **Owner rulings of 2026-10-29 (items 4, 8, 9, 14; spec Ed 8.53): ledger-kept trading window and its saved-state compatibility, emergency list for every position, one season draw priority; Step 6 schedule and rotation tests** |
| after `9211b28` (`git log 9211b28..HEAD`) | Digest fix and failure diagnostics (section 2), Bowl marked incomplete (Ed 8.54), worked rotation example, this handoff |

Review the checkpoint with `git diff 4232e22 9211b28 -- src tools tests docs`. Review what followed with `git diff 9211b28 HEAD`. A reviewer who wants the failure as it stood should check out `9211b28` and run `tests/test_digest.py` from the later commit against it (four of its six tests fail there).

## 2. The open item: intermittent upgrade/import digest mismatch

**Observed.** One full run (`python3 -I -m unittest discover -s tests`, 470 tests, random hash seed, 2,159 s): `test_upgrade.RoundTrip.test_other_ways_an_import_is_refused` raised `StateMismatch: ... state digest 48a454e22593 against 3cc48c8e1d54`. The assertion is that a state decoded from an export has the same exhaustive digest as the state exported. It passed alone, under 19 hash seeds, together with the new test modules before it, and in a second full run (483 tests). No report or log of the failing process exists beyond that message.

**Mechanism found (reproduced).** `neutral.deep_digest` memoised by object address and let temporaries die: `Random.getstate()` returns a new tuple; after it was freed the next generator's tuple could occupy the same address and inherit its digest. Evidence:
- Blindness: two lists holding different generators digest equal; advancing five of the six generators of a real league left the digest unchanged (`tests/test_digest.py`, four failures on `9211b28`).
- Instability: decoding one exported document 200 times under artificial heap fragmentation gave 10 digests different from the original on `9211b28` and 0 after the fix (90 more copies across other fragmentation seeds: 2 and 0). Reproduce with `python3 -I tools/diag_digest_stability.py --iterations 200 --seed 4` (add `--src <older checkout>/src` to run it against `9211b28`); `tests/test_digest.py::DoesNotDependOnMemoryLayout` is the synthetic version that always runs.

**Not proved.** That this produced the one observed failure. It is the only mechanism found that does, and a spurious mismatch on unchanged state is what the failure was.

**Diagnostics now in place (after `9211b28`).** On any import refusal `persist.mismatch_report` records expected and actual digests; digest of each part of the league (two levels) and each generator's state on both sides, with the differing names; document differences against a fresh encoding; three fresh decodes (`digest_is_unstable` if identical input digests differently); code fingerprint, Python, numpy, SQLite, zlib versions; hash seed and randomisation flag, pid, cwd; the tests run before it in order (`tests/_diag.py`) and module-level settings (`R.CAP`, parity profile, production cache, `random` state, temp dir). `DFL_DIAG_DIR=<dir>` writes it as JSON; it is attached to the exception as `.diagnostics`.

**What the defect weakens.** Earlier claims that "the exhaustive digest was identical" (restore soaks, upgrade, archive restore) did not prove all random states identical. State preservation was also checked by other means (every generator's state compared by value in `test_upgrade`; leagues then play on identically; the journal's quick digest) but the exhaustive check was not the guarantee described. The journal's quick digest covers only `u.rng` and `world.rng`, two of six generators (a question for the reviewer; widening it changes the save schema).

**Other checks done.** Shared mutable state: `parity.apply()` rewrites `ledger.rules.CAP` and `draft.RETURNER_BLOCK` on every `Universe(...)` and a decoded or restored league does not re-apply its profile (harmless for the shipped "tight" profile, latent for the fairness test-bed profiles; not changed). Temporary files: every relevant test uses its own `mkdtemp`; no collisions found. Unordered iteration: persistence code sorts what it hashes or writes; no use of `hash()`. Repeat testing was bounded (above), and the full suite was run three times in all.

**To close.** Reviewer confirms mechanism and fix; the full suite and restore soaks pass on the fixed code (results in section 4); no recurrence in later runs.

## 3. Rulings of 2026-10-29 as implemented

Details in `docs/OWNER_RULINGS_NEEDED.md` and `PROJECT_COMPLETION_PLAN.md` section 13; spec Ed 8.53 (Ed 8.54 adds only the Bowl-incomplete marker).
- Item 4 (Bowl): replacement and ring rules written; **INCOMPLETE: ballots and final-53 construction not built and not fully specified**; the Bowl plays composite All-Star sides.
- Item 8 (rosters): values confirmed; inactive players replace injured players only before the roster locks; emergency list covers every position, adapts as injuries happen, no player in two positions; **the out-of-position penalty is not built**.
- Item 9 (standings): one seeded priority per club per season in every scope; draft is the exact reverse.
- Item 14 (calendar): dates approved; year close and 1 January gap stated correctly; **the ledger itself keeps the trading window**; older saved state is settled from the fired events and an upgrade across it requires `accept_state_change`.
- Step 6: rotation and schedules verified for 48 seasons (13 tests). **Finding, no change made:** with a club's seed fixed, the bed rotation never changes its opponents (it changes order, kickoff slots, home/away). Worked example and the exact spec rule: `docs/SCHEDULE_ROTATION_EXAMPLE.md`. The owner has not decided whether to change this.

## 4. Reproducing the evidence

From the repository root, Python 3.13 with numpy.

```
python3 -I -m unittest discover -s tests                       # whole suite, 35 to 40 minutes
DFL_DIAG_DIR=out/diag python3 -I -m unittest discover -s tests # same, writing mismatch reports if one occurs
python3 -m unittest tests.test_digest                          # the digest defect and its regression tests
python3 -I tools/diag_digest_stability.py --iterations 200 --seed 4   # bounded stability loop (exit 1 if any copy digests differently)
python3 -I tools/validate.py data/season1 --doc docs/spec/DFL_Design_Document_Ed8.54.md   # 75 checks
python3 -I tools/run_ledger_soak.py --years 3 --seed 1
python3 -I tools/run_restore_soak.py --years 1 --seed 1 --kills 4 --play-level
python3 -I tools/run_restore_soak.py --years 1 --seed 1 --kills 4          # 22-player engine, about 40 minutes
python3 -I tools/show_rotation_example.py                      # the worked rotation example
```
Do not edit `src/dfl` or `data/` while a test run or soak that reopens league files is in progress (the file records the code's fingerprint).

Results on the fixed code: 
- Full suite on the fixed code with `DFL_DIAG_DIR` set: **491 tests, all pass** (2,306 s); no mismatch report was written. That is the third full run; the first (470 tests, before the fix) is the one with the failure.
- Validator against Ed 8.54: 75 of 75.
- Ledger soak, 3 years: PASS, final ledger digest `9fdf159d…` (unchanged).
- Restore soak, 1 year, play-level engine, 4 deaths: PASS; journal digests identical at all 49 events; exhaustive digest `674c9fdc61becbfb` identical across uninterrupted, resumed and restored runs; archive digest identical. (The exhaustive digest value differs from the earlier `49352a78…` because the digest was corrected.)
- Restore soak, 1 year, 22-player engine, 3 deaths: PASS (2,307 s); journal digests identical at all 49 events; exhaustive digest `08fe4c7a0c2d1dd9` identical across the three runs; archive digest identical.
- Bounded stability loop (`tools/diag_digest_stability.py --iterations 100 --seed 4`): fixed code 0 of 100 differ; `9211b28` code 1 of 100 (10 of 200 and 2 of 90 in the earlier loops).

## 5. Not verified, or verified only partly

1. The cause of the original failure (section 2); the restore soaks and full-league soak on the 22-player engine were not re-run for every change after `9211b28` unless listed in section 4.
2. The Bowl ballots and final-53 (not built). The out-of-position penalty (not built).
3. Real storage faults, authentication, cross-machine behaviour, scale (100-year soak not run), concurrency across machines, upgrade across a real previous release (only a modified copy of the code and one genuine earlier-code case, the trading window): see `docs/REVIEW_HANDOFF_STEPS_3_4.md` section 5.
4. Real-time pacing: deferred by instruction. The ≈50-hour season remains provisional.
5. Seeds 16 and 17 of engine calibration; the full fairness measurement.

## 6. Where a reviewer should look first

1. `neutral.deep_digest` and `persist.mismatch_report`: is the fix complete (any other cache keyed by address or any other digest that can differ for equal states)? `persist.quick_digest`: is covering two of six generators acceptable?
2. `Runner._commit`, `Runner.open`, `archive_check` (unchanged since the Steps 3–4 handoff).
3. `neutral.ADAPTERS` for the trading window and `persist.upgrade` with `accept_state_change`.
4. `Ledger.trade_open` / `open_trading` / `close_trading` and their call sites in `year.py` and `ledger/world.py` (`step_year`).
5. `standings.priority` and its callers in `league.py`.
6. `ledger/gameday.emergency_list` and `engine/squad.py` (`emergency_for`).

## 7. Next stage

Step 7: roster, contract and transaction verification (ledger rules in spec §11 and §12, tested independently of the ledger's own tests). Live pacing remains deferred.
