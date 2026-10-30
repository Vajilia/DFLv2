# The permanent archive (Phase 6, history half; spec §17 and §20 item 49)

> **Correction of 2026-10-29.** Where this document relies on the exhaustive state digest (`deep_digest`), note that before the fix described in `PROJECT_COMPLETION_PLAN.md` section 15 it did not cover most random generators' states and was not stable for identical input. An intermittent import mismatch is OPEN; persistence is not described as fully verified. Also: the journal's per-event digest of that period (`quick_digest`, save schema 3 and earlier) covered only two of the six random generators and part of the world; "journal digests identical" proved less than written. Save schema 4 replaced it with a complete state digest (`docs/PERSISTENCE.md`, "The state digest"; `PROJECT_COMPLETION_PLAN.md` section 16).

Code: `src/dfl/archive.py`, capture in `src/dfl/engine/ledger_engine.py` (`game_record`) and `src/dfl/year.py` (`season_record`), reader `tools/read_archive.py`, tests `tests/test_archive.py`. Built 2026-10-29.

## What it is for

Spec §17: traces are stored permanently, as played, with the rule and engine version; animation is drawn only from stored traces; history is archived once and never copied into every hand-off. The league file (`docs/PERSISTENCE.md`) holds what the league **is** and can be rebuilt from a seed and its journal. The archive holds what the league **did**, and cannot be rebuilt by anything but replaying that history, so it is a separate file, append-only, and needs no DFL code to read.

## Files

A league `league.db` has its archive beside it (`league.db.archive` by default; the league file records the name). `init` creates both; `--no-archive` turns the archive off (the league then runs without logging or traces).

| Table | One row per | Holds |
| --- | --- | --- |
| `meta` | fact | format name `dfl-archive`, version, trace-container version, `epochs`: one entry (code fingerprint, how, event number) for each code version that has written to the file |
| `batches` | league event that produced records | event number, record count, digest of the records' ids and hashes |
| `games` | game | game id, season, kind, teams, engine, rule version, number of traces, SHA-256 of the record, the record (JSON, zlib) |
| `traces` | traced snap | game id, play index, SHA-256 and size of the container, the container (zlib) |
| `records` | season | `S001/summary`: results, standings, seeds, playoff and Tour results, Bowl, Coronation, venues, draft order (JSON, zlib) |

Game ids: `S001/reg:w03:g07` (regular season, week, game), `S001/tour:r4:g02`, `S001/po:Western:wild_card:s7v2`, `S001/bowl`, `S001/coronation`. They come from the league layer, not from the engine, so they are the same whichever engine plays.

## What a game record holds

JSON, keys sorted. `gid`, `season`, `kind` (`regular`, `tour`, `playoff`, `bowl`, `coronation`), `home`, `away`, `neutral`, `engine` (`spatial` or `play`), `rule_version` (e.g. `engine-1.2 (Ed 8.52; play-level and 22-player spatial)`), `score` [home, away], `periods`, `tie`, `winner`, `stats` (two team lines), `players` (player id → box-score line; ids are strings in JSON), `injuries`, `pen_types`, `n_traces`, and `table`: the structured play table, one entry per event (period, clock, offence, down, distance, yard line, kind, detail, the play call, the ids of the players involved, first-down and score tags). In the 22-player engine every snap that was simulated has a `call` and `has_trace: true`; non-snap events (kickoffs, punts, kicks, penalties, timeouts) have neither.

Numbers are exact (integers, and floats as Python writes them). Tuples become lists and integer dict keys become strings; nothing else is changed, and a type the archive does not know is an error rather than a silent drop.

## The trace container (version 1)

One container per snap, all integers little-endian. Everything needed to redraw the play is in it; the order of fields is fixed.

```
offset  size            content
0       4               magic "DFLT"
4       1               container version (1)
5       4               header length H (uint32)
9       H               header: UTF-8 JSON, keys sorted
9+H     steps*22*2*2    player positions, int16, column by column (see below)
...     steps*3*2       ball (x, y, height), int16, column by column
```

Both arrays are in hundredths of a yard and are stored **column-delta**: there are 44 position columns (player 0 x, player 0 y, player 1 x, ...) and 3 ball columns; for each column in turn the container holds `steps` values, the first being the column's value at step 0 and each later one being its value at that step minus its value at the step before. A reader adds them back with a running sum. This loses nothing (the reader returns the exact original integers) and halves the stored size, because a player moves a few hundredths of a yard per 0.05 s step.

Header keys: `game_id`, `play_i` (index in the game's play table), `rule_version`, `engine`, `steps`, `dt` (seconds per step, 0.05), `roles` (22 position slots in array order), `ids` (the 22 player ids in the same order), `events` ([step, kind, a, b]: engaged, shed, handoff, throw, catch, tackle ...), `intents` ([step, slot, intent, target] at the steps where a player's intent changed), `los` (line of scrimmage, yards), `formation`, `play`, `coverage`, `units`, `pos_shape`, `ball_shape`, `layout`.

Time `t` of step `k` is `k*dt`. The container is stored zlib-compressed; its identity (the SHA-256) is taken over the uncompressed bytes, so it does not depend on a zlib version.

## Integrity

- Every row carries the SHA-256 of its uncompressed content. `verify` re-reads and re-hashes every game, trace and record, parses every container, checks that each game's declared number of traces is the number stored, that every simulated snap in a 22-player game has its trace, that each batch matches the rows stored for its event, and that the append-only triggers are present.
- SQL triggers refuse `UPDATE` and `DELETE` on `batches`, `games`, `traces` and `records`. This stops accidents and ordinary tools; it is not authentication (someone with the file can drop a trigger), and `verify` reports a missing trigger.
- `digest` is one SHA-256 over every id and content hash, independent of file layout: two archives with the same digest hold the same history.

## Retry safety

The league's commit writes the event's archive records **first**, then its own journal. A crash between the two leaves the archive exactly one event ahead of the league, never behind. The league restarts, plays the event again (it is deterministic), and `put_batch` finds identical rows already stored and does nothing. If a row exists with different content the write is refused (`ArchiveConflict`) and nothing at all is written: history is never overwritten. The same check runs while the league recovers by replay (`confirm_batch`): if replaying an event does not produce the records the archive holds, recovery stops. Tests: crash between archive write and league commit; repeat of every batch; conflicting record; edit and delete refused.

## Consistency between the league and its archive

The league and its archive carry the same random `league_id`, and the league's journal records, for every event, how many archive records the event produced and a digest of them (both are inside the journal's hash chain). **On opening a league, before anything is replayed or written, `Runner.open` compares every journal row with the archive's batch table** (`persist.archive_check`; it reads only the batch table, so it is cheap). Normal continuation is refused with `ArchiveInconsistent` when:

| Situation | Result |
| --- | --- |
| The archive lacks records the journal says were committed (an older archive restored beside a newer league, a batch deleted), however old the gap, even older than the newest snapshot | refused, kind `missing`, with the first and last event |
| The archive holds different records for an event than the journal committed (changed digest or count), or holds records for an event the journal says produced none | refused, kind `mismatched` (never repairable) |
| The archive belongs to another league (different `league_id`), even if its games are identical | refused, kind `foreign` |
| The archive file is absent or cannot be read (it is never recreated empty) | refused, kind `absent` or `unreadable` |
| The archive's history from before this league file began (after an upgrade or import) is not what the file recorded | refused, kind `base` |
| The archive is **one event ahead** after an interrupted commit | accepted; the retry makes it exact |
| The archive is **further ahead** (an older league file beside a newer archive of the same league) | refused, kind `ahead`, unless `allow_archive_ahead=True`; the extra events are then confirmed one by one as the league replays them, and a league that diverges from them is stopped (`ArchiveConflict`) |

`Runner.open(path, repair_archive=True)` rebuilds missing events: it starts from the newest snapshot before the first missing event (year-end snapshots are kept for ever, so this always exists, at the cost of replaying up to a year), replays, and writes each missing batch only if its record count and digest equal what the journal committed (otherwise `ReplayDivergence`). A refusal writes nothing to either file. `verify FILE` reports the same problems; `status FILE` shows `archive_check` and `archive_ahead_by`. `backup-all` copies the league first and the archive second, so a restored pair may have an archive further ahead than one event; `restore-all` therefore accepts that, and `upgrade` and `import` refuse a pair that is not consistent. A runner opened without its archive (`with_archive=False`, used only for reading and for the originating-code export) refuses to commit an event that makes history. Tests: `tests/test_archive_consistency.py`.

**Back up and restore the pair together** (`backup-all` / `restore-all`).

## Backup and restore

`backup-all FILE DIR` copies the league file and then the archive with SQLite's online backup (safe while the league is running) and verifies both before reporting done. `restore-all DIR DEST` verifies both, writes `league.db` and `archive.db` into a new directory (never over existing files), points the league at the restored archive and opens it. A damaged archive cannot be backed up or restored as if it were fine. Proof: `tools/run_restore_soak.py` compares the archive digest of an uninterrupted run, a run killed four times and resumed, and a restored backup run to the end.

## Reading it without DFL

`python3 -I -S tools/read_archive.py` uses only the Python standard library (no numpy, no `dfl` import): `list`, `game`, `trace` (JSON frames), `season`, `verify`, `stats`. A test runs it with site-packages disabled and compares what it prints with the module's own reading and with the engine's `trace.decode` of the same play (positions identical to the hundredth of a yard).

## Across code releases

The archive format has its own version and does not depend on the league file's schema or on the code. When a league is upgraded (`docs/PERSISTENCE.md`) the new league file keeps pointing at the same archive and a new epoch is appended to its `meta`. Old games keep the `rule_version` and `engine` they were played with. A league that was forked (the old file continued after the upgrade) would write different history under the same game ids; the archive refuses the second version.

## Storage actually used

MEASURED_PLACEHOLDER

## Limits

- Not authentication (above). Not a replacement for off-machine backups.
- One writer, local disk, WAL mode (as the league file).
- Season summaries are stored but the in-memory `reports[*]["result"]` still keeps the full season result in the league snapshot; moving it out of the snapshot is a separate change (it affects snapshot size, not history).
- The trace format stores positions at 0.05 s as played. A viewer needing smoother motion interpolates; it does not change the stored history.
- Only games played from now on are archived; the archive has no tool to reconstruct earlier seasons.
