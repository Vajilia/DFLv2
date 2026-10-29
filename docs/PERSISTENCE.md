# Durable saving, restart recovery and backup (Phase 6, storage half)

Built 2026-10-29 (Step 3, accepted provisionally by the owner, subject to later independent review); compatibility and upgrade added the same day (C11, save schema 2). Code: `src/dfl/persist.py`, `src/dfl/neutral.py`, `Universe.step_event` in `src/dfl/year.py`, command line `tools/league_store.py`, tests `tests/test_persist.py` and `tests/test_upgrade.py`. The permanent history is in a separate file: `docs/ARCHIVE.md`. The live clock and pacing (the other half of Phase 6) are deferred until saving and archiving are complete; the roughly 50-hour season is provisional.

## What is saved, and when

The league is one `Universe`. Its unit of work is one **calendar event** (49 a year: a game week, a Tour round, a draft day, free-agency opening, the year close, and so on). `Universe.step_event()` fires the next event; `play_year()` is now just `step_event()` until the year closes, and gives exactly the results it always did.

A league file is one SQLite file with four tables:

| Table | Holds |
| --- | --- |
| `meta` | save-schema version (`format`, now 3; formats 1 and 2 were development builds), the league's random `league_id` (shared with its archive), the fingerprint of the source and data files that may continue the league, how the file was made, the lineage of files it was upgraded from, the sequence number and chain value it starts from (`base`), the seed and creation arguments, the snapshot cadence, the name of its archive |
| `journal` | one row per committed event: sequence number, year, events fired so far, event key, a digest of the league after the event, and a hash chain over all rows so far |
| `snapshots` | zlib-compressed pickle of the whole league at a sequence number, with its own SHA-256 |
| `code` | the source and data files (zip, with a per-file manifest) of every code version that has written to the file |

## Guarantees

1. **An event is all or nothing.** The event runs in memory. Only when it has finished are its archive records written (idempotently; see `docs/ARCHIVE.md`) and then one SQLite transaction writes its journal row, its snapshot (when due) and the pruning of old snapshots. If the event raises, if the commit raises, or if the process is killed at any instant, the file stands at the previous event. After a failure the runner throws away its half-changed league and reloads from the file.
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
python3 -I tools/league_store.py compat league.db                      # schema and code-identity verdicts
python3 -I tools/league_store.py upgrade OLD.db NEW.db --run-originating-code   # continue OLD under this code as NEW; OLD is untouched
python3 -I tools/league_store.py extract-code league.db DIR            # the source and data the file was made with
python3 -I tools/league_store.py export league.db state.gz             # neutral state document (half a migration: see below)
python3 -I tools/league_store.py backup-all league.db DIR              # league file and archive, as a consistent pair
python3 -I tools/league_store.py restore-all DIR DESTDIR
```

## Compatibility, versions and upgrade (C11)

Two questions, answered separately.

**Save schema (`format`).** Can this code read the file's tables at all? This code reads format 2. A file with another schema is refused with `UnsupportedFormat` and a message that says which: format 1 was the first development build and never held a league of record (recreate from the seed, or open it with the checkout that wrote it; there is deliberately no converter); a newer format needs newer code.

**Code identity (`fingerprint`).** Is this the exact source and data that wrote the league? Snapshots are pickles and replay is only exact under the same rules, so a file opens for use only when the fingerprint matches; otherwise `CodeMismatch` names the files that differ and the way forward. `compat` reports both verdicts without loading anything. `status` and `verify` work under any code.

**Originating versions are kept.** Every file carries, in its `code` table, the source and data that wrote it (about 0.2 MB compressed) and a per-file manifest. `extract-code` writes them out byte for byte (the fingerprint is re-checked), so the old league can always be run, and every game replayed, under the code that played it. The archive stores the `rule_version` and engine of each game as well.

**Upgrade path.** `upgrade OLD NEW --run-originating-code`:
1. hashes OLD and works on a copy (OLD is never modified; its hash is checked again at the end);
2. extracts the code recorded in the copy and runs the export **under that code**, in a separate process (this executes code that came from the file, so it is opt-in);
3. the export is a neutral state document (`neutral.py`): typed JSON that keeps exact fractions, ordered dicts, shared references and every random generator's full state, and does not depend on any class layout;
4. the document is imported under the current code into NEW, and the rebuilt league is compared **by value** with the digest taken at export; a difference is `StateMismatch` and nothing is written, unless the owner says the shape change was intended (`--accept-state-change`, recorded in the lineage);
5. NEW continues OLD's event numbers and hash chain, and records in `meta.lineage` where it came from: the old code's fingerprint, format, rule version, last event, chain value, the SHA-256 of the old file and of the state document;
6. the archive carries on in place; a new epoch is appended to it.

Export alone is not a migration. A state document is only as good as the import that is proved against it, and the proof is the by-value comparison above. Exact reproduction holds **within** a code version: across an upgrade the past is preserved (OLD, its code, the archive) and the future is whatever the new code does. Where a release changes the shape of the state on purpose, `neutral.RENAMES` and `neutral.ADAPTERS` are the two (empty) places to say how; no migration framework exists beyond them.

What the tests prove (`tests/test_upgrade.py`): the schema and code verdicts are reported apart; format 1 and a future format are rejected clearly by open, export and upgrade; export then import under the same code gives an equal exhaustive digest, equal exact fractions (every numerator and denominator), equal identifiers, equal random-generator states, and the two leagues then play eight more events with identical digests and hash chain; a tampered document is refused with nothing written; a document from other code is refused unless a new code epoch is asked for; a league written by a modified copy of the source is refused here, upgraded with the original file byte-identical, continued with digests equal to an unbroken league, and still runs under its own extracted code; the archive carries across with a second epoch.

## Limits and open decisions (read these)

- **Upgrade is not free of risk.** It is proved for behaviour-neutral code changes and for the neutral document round trip. A release that changes rules changes the league's future by design; the upgrade then needs the owner to accept the state change (`--accept-state-change`) and, if the shape of the state changed, an adapter written and tested for that release. Nothing in this repository does that yet.
- **Extracting the code means running it.** Only upgrade files you trust.

- **Integrity is not authentication.** SHA-256 and the hash chain detect damage and accidental edits, not a deliberate forger who rewrites every table consistently. The snapshot reader accepts only this package's classes and a short list of plain data types, so a hostile file cannot run code, but treat league files as trusted inputs.
- **Growth.** About 2.3 MB compressed per snapshot after two years (placeholder engine; the report list keeps every season's full result in memory). A 100-year file would hold about 100 year-end checkpoints, plausibly a few hundred megabytes. The archive now holds a season summary and every game, but the snapshot still carries each season's full result in memory (`reports[*]['result']`); trimming it is a separate, not yet made change.
- **Cost.** A snapshot takes about 0.3 to 0.4 s, so `snapshot_every=1` (the default) adds roughly 20 s to a year, small beside a spatial-engine year (about 13 minutes). Larger values trade restart time for speed; correctness does not depend on it.
- **League and archive consistency** is checked at every open (see `docs/ARCHIVE.md`, "Consistency between the league and its archive"): the journal records the count and digest of each event's archive records and is hash-chained, and an archive that lacks, differs from, or is far ahead of what the journal committed, or belongs to another league, is refused before anything is written. It detects accidents and naive edits, not a forger who rewrites both files consistently.
- **One machine, one writer.** WAL mode on a local disk. Not for network file systems. Durability is as good as the disk's `fsync`.
- **No scheduling.** Backups are taken when the operator (or the future clock process) calls `backup` or `backup-all` (use the pair form when there is an archive); nothing schedules them yet.
- **Not covered here:** the live clock and pacing (deferred), and anything outside the `Universe` (the website). The permanent game and trace history is in `docs/ARCHIVE.md`.
