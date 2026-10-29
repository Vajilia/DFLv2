# Durable saving, restart recovery and backup (Phase 6, storage half)

Built 2026-10-29. Code: `src/dfl/persist.py`, `Universe.step_event` in `src/dfl/year.py`, command line `tools/league_store.py`, tests `tests/test_persist.py`. The live clock and pacing (the other half of Phase 6) are not built.

## What is saved, and when

The league is one `Universe`. Its unit of work is one **calendar event** (49 a year: a game week, a Tour round, a draft day, free-agency opening, the year close, and so on). `Universe.step_event()` fires the next event; `play_year()` is now just `step_event()` until the year closes, and gives exactly the results it always did.

A league file is one SQLite file with three tables:

| Table | Holds |
| --- | --- |
| `meta` | file format version, a fingerprint of the source and data files that wrote it, the seed and creation arguments, the snapshot cadence |
| `journal` | one row per committed event: sequence number, year, events fired so far, event key, a digest of the league after the event, and a hash chain over all rows so far |
| `snapshots` | zlib-compressed pickle of the whole league at a sequence number, with its own SHA-256 |

## Guarantees

1. **An event is all or nothing.** The event runs in memory. Only when it has finished does one SQLite transaction write its journal row, its snapshot (when due) and the pruning of old snapshots. If the event raises, if the commit raises, or if the process is killed at any instant, the file stands at the previous event. After a failure the runner throws away its half-changed league and reloads from the file.
2. **Restart recovery.** `Runner.open` loads the newest snapshot that passes its checksum, then replays the journal after it by firing the same events again and comparing the digest after each event with the journal's. The league is deterministic (seeded streams, no clock, no network, no language model), so replay reproduces the original exactly; a mismatch raises `ReplayDivergence`. If the newest snapshot is damaged the next older one is used and replay covers the longer gap. If none is good, `Unrecoverable` is raised and nothing is guessed.
3. **Seeded reproducibility is preserved.** A saved league carries every random generator's state. The tests compare a run that was saved, killed and restored (including in a different process with different hash randomisation) with an uninterrupted run by the journal digest after every event and by `deep_digest`, an exhaustive digest of the whole object graph (every value, every random state, independent of dict and set order). They are equal.
4. **No forks.** The commit checks that the file is still at the sequence number and hash-chain head the runner last wrote. A second runner on the same file gets `ConcurrentWriter` and reloads the real state.
5. **Tamper and damage evidence.** `verify` checks SQLite's integrity check, journal continuity (no gaps), the hash chain (editing any past row breaks every later link) and every snapshot checksum. `--deep` also proves the file recovers.
6. **No lost or duplicated games.** A game week is one event, so a week either is in the file with all its games or is not in it; a crash mid-week replays the whole week deterministically. (This is the Phase 6 exit test in the roadmap: kill and restart mid-season.)

## Backup and restore

- `backup` takes a consistent copy with SQLite's online backup, while the league is running, and verifies the copy before reporting it done. It never overwrites.
- `restore` verifies a backup, copies it to a new path (never over an existing file) and opens it, which proves it recovers.
- Year-end snapshots are checkpoints and are kept for ever; ordinary snapshots keep the newest `keep` (default 3). Restoring "as of the start of a year" means restoring a backup taken then.

## Commands

```
python3 -I tools/league_store.py init   league.db --seed 1            # 22-player engine (the default); --play-level or --placeholder for speed
python3 -I tools/league_store.py run    league.db --years 1            # or --events N; every event is committed as it finishes
python3 -I tools/league_store.py status league.db
python3 -I tools/league_store.py verify league.db --deep
python3 -I tools/league_store.py backup league.db league-2001.bak
python3 -I tools/league_store.py restore league-2001.bak league-restored.db
python3 -I tools/league_store.py digest league.db                      # exhaustive digest of the recovered league
```

## Limits and open decisions (read these)

- **Tied to the code.** Snapshots are pickles, valid only for the source and data files that wrote them. The file stores a fingerprint and refuses to open under different code unless told to (`allow_code_change`), because replay under changed rules would not be exact. So *any* edit to `src/dfl` or `data/` strands existing league files. That is acceptable for development; for a league of record it needs a decision in Phase 8: either freeze code between releases, or add an export to a neutral format (JSON/CSV) and an import that is itself tested. Recommended: neutral export before the first release.
- **Integrity is not authentication.** SHA-256 and the hash chain detect damage and accidental edits, not a deliberate forger who rewrites every table consistently. The snapshot reader accepts only this package's classes and a short list of plain data types, so a hostile file cannot run code, but treat league files as trusted inputs.
- **Growth.** About 2.3 MB compressed per snapshot after two years (placeholder engine; the report list keeps every season's full result in memory). A 100-year file would hold about 100 year-end checkpoints, plausibly a few hundred megabytes. The right fix (move finished seasons' results to the trace archive, spec §20 item 49) belongs with the website work.
- **Cost.** A snapshot takes about 0.3 to 0.4 s, so `snapshot_every=1` (the default) adds roughly 20 s to a year, small beside a spatial-engine year (about 13 minutes). Larger values trade restart time for speed; correctness does not depend on it.
- **One machine, one writer.** WAL mode on a local disk. Not for network file systems. Durability is as good as the disk's `fsync`.
- **No scheduling.** Backups are taken when the operator (or the future clock process) calls `backup`; nothing schedules them yet.
- **Not covered here:** the live clock and pacing, and anything outside the `Universe` (the website, the trace archive).
