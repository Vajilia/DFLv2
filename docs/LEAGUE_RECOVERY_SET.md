# The recovery set of an upgraded league

Written 2026-11-03. Applies to the league that was upgraded from event 141 to new code (`league-141-original` → active `league`) and to any later
`league_store.py upgrade`. Tool: `tools/league_recovery_set.py`. Test: `tests/test_league_recovery_set.py`.

## 1. The dependency, in one paragraph

An upgrade continues the old file's event numbers and hash chain, but the new league file begins with **one** snapshot (the base checkpoint at the base
event) and holds **only the journal rows after it**. So the active league file alone cannot be replayed from the start of the league, and it has none of
the earlier restore points. Those exist only in the old file. The games themselves are not at risk: the new archive is the old archive extended.

For this league (measured 2026-11-03, league stopped at event 188):

| | `league-141-original/league.db` | active `league/league.db` |
|---|---|---|
| journal rows | events 1..141 (141 rows) | events 142..188 (47 rows) |
| restore points (snapshots) | 0, 47, 94, 141 (year-end checkpoints) and 138–140 (ordinary) | 141 (base checkpoint), 188 (checkpoint), 185–187 (ordinary) |
| code that wrote it | fingerprint `a8fc26342e9f…` | fingerprint `efaa7167832713d7…` |
| size | 19,513,344 bytes | 17,518,592 bytes |

The active file records the file it was upgraded from by SHA-256, head event (141) and head chain (`731a6f68…`). **The recorded SHA-256 is
`ea7130cc657a18e7c1cbad7a7053a11380c45ceab18d9fee40e360c66c9148e6`, which is the file `/srv/dflv2/league-v2-upgrade-input/league.db` (a copy of the 141
league made 2026-11-02 19:30, the file the upgrade actually read), not the file kept as `league-141-original/league.db`, which hashes to
`6dabe5c3bd133f3296b6cb57eebf0959fb44f485b995cfa08221026c565290c8`.** Both are 19,513,344 bytes and hold the same recorded history (every journal row,
every restore-point checksum and every recorded code version compare equal, checked 2026-11-03 with `same_history`); only the SQLite page layout differs,
as it does between any file and a backup-rewritten copy of it. An earlier draft of this note wrongly said the lineage named `league-141-original`.

## 2. Two different things "history" can mean

| | Preserved **game history** | Complete **replay / recovery history** |
|---|---|---|
| What it is | every game, play and recorded player movement of seasons 1..N | the league's state at every event, the ability to rebuild any state by replaying the journal from a restore point, and the exact code that produced it |
| Held by | the **archive** alone (`archive.db`): 1,600 games, 189,201 traces, 4 season records for seasons 1–4 | journal rows 1..141 and restore points 0/47/94/141 **in the original file**, plus rows 142..188 in the active file |
| Enough to | show every past game and replay it in the viewer, rebuild History and Hall pages | recover the league to any past restore point, re-run any stretch of the league, prove the whole chain from event 1 to the head |
| Survives loss of the original file? | yes | **no**: events 1..141 and the earlier restore points are gone for good |

Losing `league-141-original` costs no game and no public page. It does cost the ability to re-run or audit the league's first 141 events.
The archive is checked row by row against the original journal (every event 1..141 present, same record count and digest), so the two views agree.

## 3. The complete backup set

All of these, together. Anything less is a partial backup.

1. **Active league file** `league.db` (events 142..188 and the live state).
2. **Active archive** `archive.db` (seasons 1..N; a superset of the original's archive; required by the active file for events after 141 and checked against both journals).
3. **The exact file the upgrade read**, byte for byte: for this league `league-v2-upgrade-input/league.db`. Its SHA-256 must equal the one in the active file's lineage; that is how the restore test knows it is the right file and not just another file for the same league. Copy it only when no runner has it open (`-wal` empty or absent).
   **Plus `league-141-original/league.db`** as an *equivalent* copy: the tool checks that it holds the same journal rows, restore-point checksums and code records as the exact file (not the same bytes). Keep both: the exact file proves the link, the preserved file is the one that was kept as the original and may be the only one that survives a clean-up of the input folder.
4. **The code that runs the active league**: `src/`, `data/`, `tools/` of a tree whose code fingerprint equals the active file's (`efaa7167832713d7…`, engine release `7e59cbbaea23b0de353545c64a6f5c39cffe774d`).
5. **The code that wrote the original**: needs no separate item. Every league file carries the exact source it was written with in its `code` table, and the restore test extracts it from the original file and runs the original's replay under it. Keep the original file and this comes with it.
6. **The manifest** `RECOVERY_SET.json`: size and SHA-256 of every file above, both files' heads, the link between them, the code fingerprint and the git commit of the tree.

Not part of the set:

- The original's own archive (`league-141-original/archive.db`, 423,510,016 bytes). It is a verified subset of the new archive (every row present and identical), so it is redundant. Keep it if space allows; the set does not need it.
- Public feeds, the site, playback exports. They are regenerated from the archive and the league.
- Scratch copies, review copies and `repo-releases`. Useful, not required.

Building it (the tool refuses a wrong pairing, a league that was not upgraded, a code tree that does not match, and an existing folder):

```
python3 -I tools/league_recovery_set.py create SET_DIR \
    --league /srv/dflv2/league/league.db \
    --original /srv/dflv2/league-v2-upgrade-input/league.db \
    --equivalent /srv/dflv2/league-141-original/league.db \
    --code-tree /opt/dflv2-src/7e59cbbaea23b0de353545c64a6f5c39cffe774d
python3 -I tools/league_recovery_set.py verify SET_DIR
```

`create` copies the active pair with the same consistent online copy `league_backup.py` uses (league first, archive second); stop the runner before copying the original.
Copy `SET_DIR` off the droplet. It is about 0.6 GB for this league (565 MB archive + 17.5 MB active + 2 × 19.5 MB original + 0.3 MB of code), mostly archive.

## 4. The restore test

```
python3 -I tools/league_recovery_set.py restore-test SET_DIR WORK_DIR --replay-from 94 --require-sources-absent
```

It uses only `SET_DIR` and `WORK_DIR`. Every code path it runs comes out of the set; every subprocess is `python3 -I` with a scrubbed environment. In order:

1. The set verifies against its manifest (every file's size and SHA-256).
2. With `--require-sources-absent`, none of the paths the league used on its original server exists on this machine. This is what makes a pass mean "independent of the original server paths". Without the flag a pass is reported as not proving independence.
3. The code tree in the set reproduces the fingerprint recorded in the active league file.
4. The active league and archive restore and open under the code from the set (the restore refuses a code change, and re-checks league against archive); head event and chain equal the manifest; the archive holds every batch, game, trace and record recorded.
5. The original's code is extracted from the original file and reproduces its own fingerprint.
6. The original file is sound (SQLite integrity, journal continuity and chain from event 1, snapshot checksums, recorded code), holds events 1..141, and the **new** archive holds exactly what its journal committed. This is the "game history is preserved" check.
7. The original opens and recovers under its own code to its recorded head and chain.
8. Each equivalent copy holds the same recorded history as the exact original (it reports whether the bytes are also identical). The active file continues the original's event and chain, and its lineage names the exact original file by SHA-256.
9. With `--replay-from SEQ`: restore points after `SEQ` are removed from a scratch copy of the original and the journal is replayed forward under the original's code; the result must reproduce the original's head chain and state digest. This is the check that earlier restore points are usable, and the one thing the active file cannot do on its own.

Exit status is 0 only if every step passed. Unit tests (`tests/test_league_recovery_set.py`) build a small real league, upgrade it, make the set, move the server folder away,
and run the restore test; they also cover a missing or damaged original, code tree or archive, a swapped original under a repaired manifest, the wrong pairing, a non-upgraded league, and a code tree that does not match.

## 5. What this does and does not protect

- Protects: the active league, all game history, the original file's journal 1..141 and its restore points, and the two code versions, as long as the set itself is kept and copied off the droplet.
- A pass proves the set restores and the history is consistent. It does not prove the set is stored safely. As of this writing the set lives wherever it is put; a copy that exists only on the droplet is not a backup of the droplet.
- If the original file is lost and no set exists, the league keeps running and every game stays viewable; only events 1..141 can no longer be replayed or audited, and nothing can restore a point earlier than 141.
- **Do not delete `league-141-original` or `league-v2-upgrade-input` or their place in backups** until a recovery set holding both has been copied off the droplet and passed the restore test. Any future upgrade adds another link: keep each superseded league file in the set.

## 6. Real run on the production league's files (2026-11-03)

Built and tested on the droplet from **copies** of the real files; nothing in `/srv/dflv2` was written to, the league stayed stopped at event 188, and the
copies are kept under `/root/rs-test/` (`src/` the copies, `set/` the recovery set, `work/` what the restore test produced).
Tool revision `8e21e11` (experiment branch `experiment/contract-memo-cache`), engine tree `/opt/dflv2-src/7e59cbbaea23b0de353545c64a6f5c39cffe774d`.

1. Build (`create`, 3 min 22 s on the 1-vCPU droplet): `--league` copy of the active league + archive, `--original` copy of `league-v2-upgrade-input/league.db`
   (SHA-256 `ea7130cc…`, equal to the lineage's), `--equivalent` copy of `league-141-original/league.db`. The set is 0.6 GB: active `league.db` 17,518,592 B,
   `archive.db` 565,198,848 B, original 19,513,344 B, equivalent 19,513,344 B, code tree 306,239 B.
   **The first attempt, with `league-141-original` named as the original, was refused**: its SHA-256 (`6dabe5c3…`) is not the lineage's. That is how the
   difference in section 1 was found.
2. Original paths unavailable: the restore test ran inside a private mount namespace (`unshare -m`) with empty tmpfs mounted over `/srv/dflv2`, `/opt/dflv2-src`
   and the folder holding the copies (checked from inside: 0 entries in each, `league` not visible), with `--require-sources-absent`, which also checks every path
   recorded in the manifest. The running system outside the namespace was not affected.
3. Result: **15 of 15 steps passed** (`ok: true`). In particular:
   - the set verified against its manifest; the code tree in it reproduced fingerprint `efaa7167832713d7…`;
   - the active pair restored and opened under that code at event 188, chain `124a6408…`; the restored archive held 128 batches, 1,600 games, 189,201 traces, 4 season records;
   - the original's code (`a8fc26342e9f…`) was extracted from the original file and the file verified (integrity, journal events 1..141 with their chain, snapshot checksums);
   - the new archive matched the original journal exactly (0 missing, 0 mismatched; 32 later batches beyond event 141, as expected);
   - the original opened under its own code to event 141, chain `731a6f68…`, state digest `02c2858e…`;
   - `league-141-original` holds the same recorded history as the exact upgrade input (byte-identical: no);
   - the lineage names the exact original by SHA-256;
   - **recovery from restore point 94**: restore points after 94 were removed from a scratch copy and the journal replayed forward under the original's code to event 141 (1,559.7 s), reproducing the original's chain and state digest.

Not covered by this run: replay from restore points 0 and 47 (same mechanism, longer: about 3× and 2× as long), and anything after a restore of the *active* file's later journal from a restore point (the active file has only its own base 141 and 188). The set is also still on the droplet: it has not been copied anywhere else.
