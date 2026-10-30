# Durable saving, restart recovery and backup (Phase 6, storage half)

> **Correction of 2026-10-29 (second, after the persistence verification work).** Earlier versions of this document said the journal's digest and `deep_digest` covered the league's state. Neither did. Until commit `030b51a` the exhaustive digest was blind to most random generators and unstable for identical input (a memory-address defect, reproduced); the journal's own digest (`quick_digest`, save schema 3 and earlier) never covered more than the ledger, the cursors, **two of the six generators** and a few books. Save schema 4 replaces it with one complete state digest (below). Every statement below that a restored league was "identical by journal digest" is true of schema 4 and of the soaks re-run for it, and was **not** established by the schema-3 journal. The intermittent import mismatch is still OPEN: the digest defect is a demonstrated mechanism that could produce it, but attribution to the original uninstrumented failure is unproven, and persistence is not described as fully verified.

Built 2026-10-29 (Step 3, accepted provisionally by the owner, subject to later independent review); compatibility and upgrade added the same day (C11, save schema 2). Code: `src/dfl/persist.py`, `src/dfl/neutral.py`, `Universe.step_event` in `src/dfl/year.py`, command line `tools/league_store.py`, tests `tests/test_persist.py` and `tests/test_upgrade.py`. The permanent history is in a separate file: `docs/ARCHIVE.md`. The live clock and pacing (the other half of Phase 6) are deferred until saving and archiving are complete; the roughly 50-hour season is provisional.

## What is saved, and when

The league is one `Universe`. Its unit of work is one **calendar event** (49 a year: a game week, a Tour round, a draft day, free-agency opening, the year close, and so on). `Universe.step_event()` fires the next event; `play_year()` is now just `step_event()` until the year closes, and gives exactly the results it always did.

A league file is one SQLite file with four tables:

| Table | Holds |
| --- | --- |
| `meta` | save-schema version (`format`, now 4; formats 1 to 3 were development builds), the state-digest algorithm it was written with (`digest_algorithm`), the league's random `league_id` (shared with its archive), the fingerprint of the source and data files that may continue the league, how the file was made, the lineage of files it was upgraded from, the sequence number and chain value it starts from (`base`), the seed and creation arguments, the snapshot cadence, the name of its archive |
| `journal` | one row per committed event: sequence number, year, events fired so far, event key, the complete state digest of the league after the event (below), the count and digest of the event's archive records, and a hash chain over all rows so far |
| `snapshots` | zlib-compressed pickle of the whole league at a sequence number, with its own SHA-256 |
| `code` | the source and data files (zip, with a per-file manifest) of every code version that has written to the file |

## Guarantees

1. **An event is all or nothing.** The event runs in memory. Only when it has finished are its archive records written (idempotently; see `docs/ARCHIVE.md`) and then one SQLite transaction writes its journal row, its snapshot (when due) and the pruning of old snapshots. If the event raises, if the commit raises, or if the process is killed at any instant, the file stands at the previous event. After a failure the runner throws away its half-changed league and reloads from the file.
2. **Restart recovery.** `Runner.open` loads the newest snapshot that passes its checksum, then replays the journal after it by firing the same events again and comparing the digest after each event with the journal's. The league is deterministic (seeded streams, no clock, no network, no language model), so replay reproduces the original exactly; a mismatch raises `ReplayDivergence`. If the newest snapshot is damaged the next older one is used and replay covers the longer gap. If none is good, `Unrecoverable` is raised and nothing is guessed.
3. **Seeded reproducibility is preserved.** A saved league carries every random generator's state (six: the league's, and the world's, development, character, coaching and governance generators). The tests compare a run that was saved, killed and restored (including in a different process with different hash randomisation) with an uninterrupted run by the journal digest after every event. They are equal.

## The state digest (save schema 4)

`persist.journal_digest(u)` is `neutral.deep_digest(u)`: one SHA-256 over **everything reachable from the universe**, taken after every event and checked when replay recovers a league. It is the same function the state documents use, so there is a single definition of "the same state".

- **Covered:** every attribute of the universe, the world, the ledger and every player, the character cards, the club views, the season run, the resolver and its squads; all six random generators, each by its complete state (the 624 words, the position and the cached Gaussian), each at its own place in the graph, so exchanging two generators' states is noticed.
- **Order.** Dicts and lists count in order (code iterates them, so order is state). Sets count as sets: their iteration order depends on hashing, and the code must not rely on it. A different dict order across processes would be a real hazard and is reported as a mismatch (it was checked under four hash seeds).
- **Excluded, each with a reason** (`neutral.EXCLUDED`): `_units` (squad unit-rating cache, recomputed from the cards), `_memo` (contract release-charge cache, recomputed from the contract's own fields), `dir` (where this machine keeps the data tables; the tables themselves are covered by the code fingerprint), `archive_buffer` and `archive` (the per-event hand-off to the permanent archive, covered instead by the journal's per-event record count and digest and by the archive's own chain). `tests/test_state_digest.py` replaces each attribute of the universe, world, ledger, resolver and season run with a probe and fails if the digest does not change, unless the name is listed as excluded, in which case it fails if it *does* change.
- **Outside the universe, so outside the digest:** the code and data (the file's code fingerprint), the archive of games (above), and the seed and construction arguments (the file's `meta`). A property of the process, such as `PYTHONHASHSEED`, is not state; nothing in a league may depend on it.
- **Cost.** About 0.3 to 0.6 s per event with the placeholder engine (the ledger, 4,600 characters and their cards dominate), comparable to the event itself; it is the price of checking everything after every event rather than part of it.
- **Versioning.** `persist.FORMAT_DIGESTS` ties each save schema to its digest algorithm (`neutral.DIGEST_VERSION`, now 2) and an assertion at import refuses to let the two drift apart. A file records its algorithm in `meta.digest_algorithm`. State documents carry `digest_algorithm` in their manifest (document version 2); version 1 documents are refused by name.

Process-wide settings. A parity profile (`src/dfl/parity.py`) used to rewrite `rules.CAP` and `draft.RETURNER_BLOCK` for the whole process, and a restored league did not re-apply its profile. That let a second league alter the first (demonstrated: with the first league on the shipped rules, creating a second on the `none` profile changed the first league's state from its fifth event), and made a non-default league unrecoverable by replay in a fresh process. The settings now belong to the league: the cap on its ledger (`Ledger.cap`), the returner rule on the universe and its season run (`returner_block`), the priority clubs on its world. `parity.settings(name)` only returns values. What remains process-wide is read-only data: the engine's parameter tables, the spatial engine's tables, the analytics tables and the character tables, all loaded from the repository's own data files and identified by the code fingerprint; they are not league configuration, and one tool (`tools/calibrate_spatial.py`) and one test change them inside their own process only.
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

**Save schema (`format`).** Can this code read the file's tables at all? This code opens format 4. A file with another schema is refused with `UnsupportedFormat` and a message that says which and what to do.
- **Formats 1 and 2** were early development builds and never held a league of record: recreate from the seed, or open the file with the checkout that wrote it. There is deliberately no converter.
- **Format 3** (development; commit `030b51a` and before) is not opened or continued here, because its journal digests are algorithm 1 (partial) and this code writes and checks algorithm 2; mixing them in one journal would make replay verification meaningless. It is not damaged and not modified. `compat` reports it as `upgradable`; `status` and `verify` still read it; and `upgrade OLD NEW --run-originating-code` carries the league forward: the file's own recorded code exports it (with the parity profile it was created under applied first, which that code did not do by itself), this code imports it, the imported state is compared by value with the export, and NEW is a format-4 file continuing OLD's event numbers. The attributes an older state could not carry (`cap`, `returner_block`) are supplied by adapters from the league's profile name and named in the lineage (`adapted_attributes`); everything else must match the exporter's digest exactly, so no `--accept-state-change` is needed and a real difference is still refused (`tests/test_format3_upgrade.py`, which runs against the exact source of the schema-3 commit, vendored as a test fixture).
- **A newer format** needs newer code.

**Code identity (`fingerprint`).** Is this the exact source and data that wrote the league? Snapshots are pickles and replay is only exact under the same rules, so a file opens for use only when the fingerprint matches; otherwise `CodeMismatch` names the files that differ and the way forward. `compat` reports both verdicts without loading anything. `status` and `verify` work under any code.

**Originating versions are kept.** Every file carries, in its `code` table, the source and data that wrote it (about 0.2 MB compressed) and a per-file manifest. `extract-code` writes them out byte for byte (the fingerprint is re-checked), so the old league can always be run, and every game replayed, under the code that played it. The archive stores the `rule_version` and engine of each game as well.

**Upgrade path.** `upgrade OLD NEW --run-originating-code`:
1. hashes OLD and works on a copy (OLD is never modified; its hash is checked again at the end);
2. extracts the code recorded in the copy and runs the export **under that code**, in a separate process (this executes code that came from the file, so it is opt-in);
3. the export is a neutral state document (`neutral.py`): typed JSON that keeps exact fractions, ordered dicts, shared references and every random generator's full state, and does not depend on any class layout;
4. the document is imported under the current code into NEW, and the rebuilt league is compared **by value** with the digest taken at export; a difference is `StateMismatch` and nothing is written, unless the owner says the shape change was intended (`--accept-state-change`, recorded in the lineage);
5. NEW continues OLD's event numbers and hash chain, and records in `meta.lineage` where it came from: the old code's fingerprint, format, rule version, last event, chain value, the SHA-256 of the old file and of the state document;
6. the archive carries on in place; a new epoch is appended to it.

Export alone is not a migration. A state document is only as good as the import that is proved against it, and the proof is the by-value comparison above. Exact reproduction holds **within** a code version: across an upgrade the past is preserved (OLD, its code, the archive) and the future is whatever the new code does. Where a release changes the shape of the state on purpose, `neutral.RENAMES` (empty) and `neutral.ADAPTERS` are the two places to say how; no migration framework exists beyond them. `ADAPTERS` holds two things so far: the ledger's trading window (2026-10-29), which older state does not record and which is settled from the events the league has fired, and the league-owned parity settings (above), settled from the profile name. Attributes an adapter supplies are left out of the by-value comparison with the exporter's digest (the exporter could not have included them) and are listed in the lineage as `adapted_attributes`; everything else is compared exactly, and `accept_state_change` remains for an intended change that adapters do not cover.

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
