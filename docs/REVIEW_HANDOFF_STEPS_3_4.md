# Review handoff: Steps 3 and 4 (durable saving, upgrade path, permanent archive)

For an independent reviewer. Written 2026-10-29. Nothing here is merged or deployed. Branch `audit/completion-plan` (from `milestone-1-league-layer` @ `4232e22`) in `Vajilia/DFLv2`.

> **Correction of 2026-10-29.** The exhaustive state digest cited below as proof ("identical exhaustive digest") could not see most random generators' states and could answer differently for identical input; one unexplained `StateMismatch` in a full test run is open. Read section 4 as weaker than written and see `docs/REVIEW_HANDOFF_9211b28.md` section 2 and `PROJECT_COMPLETION_PLAN.md` section 15.

## 1. Commits

| Commit | Content |
| --- | --- |
| `1317f3b` | Step 3: housekeeping pass; durable saving, restart recovery, atomic event processing, backup/restore (`persist.py`, `tools/league_store.py`) |
| `dd9ef8e` | C11 save-schema/code separation and tested upgrade path (`neutral.py`, `persist.upgrade`); Step 4 permanent archive (`archive.py`, `tools/read_archive.py`) |
| `a1e0fe7` | Archive consistency at open: the journal commits to each event's archive records; refusal of missing, mismatched, foreign and over-advanced archives (save schema 3) |

The tip of the branch adds Step 5 (calendar and season-controller verification, one defect fixed: `PROJECT_COMPLETION_PLAN.md` section 12). The tests and soaks in section 4 were run on that tip. Review the Step 3 and 4 work with `git diff 4232e22 a1e0fe7 -- src tools tests docs`. The files that matter, in reading order:

| File | Role |
| --- | --- |
| `src/dfl/persist.py` | Save file, journal, snapshots, recovery, backup/restore, archive consistency, upgrade (about 900 lines) |
| `src/dfl/archive.py` | Append-only history file, trace container, integrity, backup |
| `src/dfl/neutral.py` | Version-neutral state document used by export/import/upgrade |
| `src/dfl/year.py` | `Universe.step_event()` is the unit of work (49 events a year); `take_archive`, `season_record` |
| `src/dfl/engine/ledger_engine.py`, `src/dfl/league.py` | Capture of each game as played; game ids |
| `tools/league_store.py`, `tools/read_archive.py`, `tools/run_restore_soak.py` | Command line, standard-library-only reader, kill-and-restore soak |
| `tests/test_persist.py`, `test_upgrade.py`, `test_archive.py`, `test_archive_consistency.py` | The evidence below |
| `docs/PERSISTENCE.md`, `docs/ARCHIVE.md` | Design and limits as written for owners |

## 2. Architecture in one page

- **Two files per league.** `league.db` (what the league *is*) and `league.db.archive` (what it *did*). Both SQLite, WAL.
- **League file.** `meta`; `journal` (one row per committed event: sequence, year, events fired, key, quick digest of the state after it, hash-chain value, and the count and digest of the archive records the event produced); `snapshots` (zlib pickles read through an allow-listed unpickler, SHA-256 each); `code` (zip of the source and data that wrote the file, with a manifest and fingerprint).
- **Unit of work** is one calendar event. An event runs in memory; only when it is finished is it committed: archive records first (idempotent), then one SQLite transaction (journal row, snapshot if due, pruning). A crash at any instant leaves the file at the previous event, and the archive at most one event ahead.
- **Recovery** = newest snapshot whose checksum holds, then replay of the journal by firing the same events and comparing the digest after each (and the regenerated archive records with the journal's count and digest).
- **Concurrency**: optimistic; a second runner that finds the head moved gets `ConcurrentWriter`.
- **Code identity (C11).** Save-schema `format` is separate from code `fingerprint`. Same schema, different code: opening is refused (`CodeMismatch`); reading (`status`, `verify`, `extract-code`) still works; `upgrade OLD NEW --run-originating-code` exports under the extracted originating code in a subprocess, imports under the current code, compares by value, records lineage, continues the numbering and hash chain, and leaves the original file byte-identical. There is deliberately no migration framework (`RENAMES` is empty; `ADAPTERS` has one entry, added after this review was written, for the ledger's trading window: see `PROJECT_COMPLETION_PLAN.md` section 13).
- **Archive.** Append-only tables `batches`, `games`, `traces`, `records` with triggers refusing UPDATE and DELETE, a SHA-256 for every row, one binary trace container per simulated snap (positions at 0.05 s in hundredths of a yard, column-delta coded). Readable with `python3 -I -S tools/read_archive.py` (standard library only).
- **Archive consistency (new).** The league has a random `league_id`, also stored in the archive. The journal's hash chain covers each event's archive record count and digest. On open, `archive_check` compares every journal row with the archive's batch table; `Runner.open` refuses (`ArchiveInconsistent`, before writing anything) an archive that is missing records the journal committed, holds different ones, belongs to another league, is absent or unreadable, or is ahead by more than the one event an interrupted commit leaves. `repair_archive=True` rebuilds missing events by replay from the newest snapshot before the gap, and each rebuilt batch must equal what the journal committed. `allow_archive_ahead=True` accepts an archive of the same league that is further ahead (used by `restore-all`, because the archive is copied after the league).

## 3. Reproducing the evidence

All commands from the repository root. Python 3.13, numpy (see `requirements.txt`).

```
python3 -I -m unittest tests.test_persist tests.test_upgrade tests.test_archive tests.test_archive_consistency
python3 -I -m unittest discover -s tests                      # whole suite, about 12 to 15 minutes
python3 -I tools/validate.py data/season1                    # spec validator, 75 checks
python3 -I tools/run_restore_soak.py --years 1 --seed 1 --kills 4 --play-level --report out/restore_soak.md
python3 -I tools/run_restore_soak.py --years 1 --seed 1 --kills 4 --report out/restore_soak_22.md      # 22-player engine (default)
python3 -I tools/league_store.py init /tmp/x/league.db --seed 1 && python3 -I tools/league_store.py run /tmp/x/league.db --years 1
python3 -I tools/league_store.py verify /tmp/x/league.db --deep ; python3 -I tools/league_store.py status /tmp/x/league.db
```

Do not edit `src/dfl` or `data/` while a test run or soak that reopens league files is in progress: the file records a fingerprint of that code, so the run refuses to continue.

## 4. Tested guarantees

Each line names the test that would fail if the guarantee broke.

**Saving and recovery** (`tests/test_persist.py`)
- A league stepped event by event with a database, restarted, or killed and restored reaches the same quick and exhaustive digests as one that never stopped.
- A crash at any of the commit's points (before the archive write, after it, after the journal row, after the snapshot) leaves the file at the previous or the new event, never between.
- A damaged newest snapshot falls back to an older one and replays; an edited journal row is caught by the hash chain and by replay; two runners cannot both commit.
- Backups are online, verified before they are reported done, and never overwrite.

**Code identity and upgrade** (`tests/test_upgrade.py`)
- Unsupported schemas and different code are refused with the reason; reading still works; the originating code is recoverable from the file.
- Upgrade preserves the original file's bytes; the new league continues the numbering and chain and plays on identically to the one that never upgraded; export/import round trip preserves identifiers, exact `Fraction` money and random-number state (by-value comparison).

**Archive** (`tests/test_archive.py`)
- Capturing history does not change what the league does.
- Every simulated snap of a 22-player game has its trace; every row re-hashes; a stored trace decodes to the exact integers the engine produced; the reader runs with no DFL code and no third-party library.
- Writing a batch again changes nothing; a conflicting record is refused and nothing is written; history cannot be edited through the archive file's own triggers; damage is found by `verify`.
- League and archive back up and restore as a pair and go on identically; a restored pair's archive digest equals the uninterrupted run's.

**Archive consistency** (`tests/test_archive_consistency.py`)
- An older archive restored beside a newer league is refused, including when the gap is older than the newest snapshot; neither file is changed by the refusal; every way of opening it (including the command line) refuses.
- It can be rebuilt by replay, and the rebuilt archive equals the uninterrupted run's; a rebuild that replays to records other than the journal committed is stopped; present-but-different records are never repaired.
- A batch deleted from the middle, a changed digest, a changed count, a batch for an event that had no records, another league's archive (even with identical games), a missing file and an unreadable file are each refused.
- An archive one event ahead after an interrupted commit is accepted and made exact by the retry; an older league beside a newer archive of the same league is refused unless asked for, and then confirmed event by event; a league that diverges from an archive that is ahead is stopped.
- Editing what the journal says the archive holds breaks the hash chain; replay checks regenerated records against the journal.
- Upgrade and import refuse an inconsistent pair; a runner opened without its archive cannot commit events that make history.

**Whole-system runs on the final code** (figures in `PROJECT_COMPLETION_PLAN.md`, end of section 12): 440 tests pass; validator 75/75; 30 generated seasons validated; ledger soak and 3-year 22-player full-league soak pass; restore soaks (kill, resume, restore) pass on both engines with identical journal, league and archive digests.

## 5. Not tested, or tested only partly (assumptions)

1. **Real storage faults.** Crash tests raise exceptions at named points inside the commit and kill processes between and inside commits; they do not model torn writes, power loss during `fsync`, a full disk or silent bit rot. We rely on SQLite WAL with `synchronous=FULL` doing what it documents.
2. **Authentication.** The hash chain and per-row hashes detect accidents and naive edits. Someone who rewrites the whole file consistently (journal, chain, snapshots, archive) is not detected, and archive triggers can be dropped. Nothing here is a security boundary.
3. **Journal and archive written together.** The archive is written before the journal; the only atomicity between the two files is the ordering and idempotent retry, proven by tests with simulated crashes and by process kills, not by an exhaustive model.
4. **Format 2 files.** Schema 2 (briefly on this branch) is not read; it never held a league of record. There is no automatic upgrade from it.
5. **Cross-machine behaviour.** Python 3.13 on one Linux machine. Pickle/zlib/SQLite versions, floating point across CPUs and `PYTHONHASHSEED` other than the soak's varied values are not covered beyond the soak.
6. **Scale.** Measured: 142.8 MB of archive and 8.4 MB of league file per season (about 14 GB per 100 seasons); one-year and 30-season runs. Open-time consistency checking reads only the batch table (cheap), but nothing was measured at 100 seasons of archive; the 100-year soak was not run.
7. **Repair from an older snapshot** needs a snapshot at or before the gap. Year-end checkpoints and the first snapshot are kept for ever, ordinary snapshots only the newest three, so a gap is always repairable back to the previous year-end, but replaying a year costs minutes.
8. **Archive ahead by many events** is accepted only with an explicit flag; it is also what a backup set looks like when the league ran while the archive was being copied. The flag does not prove the archive is the *same history*; the per-event confirmation during replay does, event by event, as the league catches up.
9. **Upgrade across real code changes.** Proven with a copy of the code modified in a test; no real previous release exists yet. The by-value comparison covers the state the digest covers (`DIGEST_IGNORE` names what it skips).
10. **Concurrency** is optimistic and local; a network file system or two machines on one file is unsupported and untested.
11. **Live-clock pacing** is not part of this work (deferred).
12. **Season summaries** are stored in the archive, but the league snapshot still carries each season's full result; moving it out is a size optimisation, not a correctness item.

## 6. What a reviewer should look at first

1. `Runner._commit` and `Runner.open` in `persist.py`: ordering of archive write, journal row and snapshot; what is checked before replay.
2. `archive_check` / `_judge_archive`: whether the rule "ahead by at most the next event, otherwise refuse" is the right one, and whether `allow_archive_ahead` on `restore-all` is acceptable.
3. `neutral.py` `DIGEST_IGNORE`: what the by-value comparison does not look at.
4. `archive.Archive.prepare/put_batch/confirm_batch`: idempotence and conflict handling.
5. The soak driver `tools/run_restore_soak.py`: whether it exercises the failures you care about.
