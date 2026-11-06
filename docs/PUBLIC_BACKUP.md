# Public-folder backup and restore (milestone M8a)

*Written 2026-11-06 on branch `backend/public-backup-2026-11-06`, from `7668245230c2692fd81430fbd7bb6114a01121d3`. Everything here was built and tested locally on scratch copies. Nothing was deployed, nothing on the Droplet (the running rehearsal included) was contacted, no retention or deletion tool was written or run, and no frontend file changed.*

Tools: `tools/public_backup.py` (public folder, private store), `tools/recovery_set.py` (league + store + public as one unit), `tools/measure_public_backup.py` (real-size measurements), `tools/engine_comparison.py` (separate: `docs/ENGINE_COMPARISON_REPRODUCTION.md`). Tests: `tests/test_public_backup.py` (28), `tests/test_recovery_set.py` (6).

## 1. Why this exists

`docs/STORAGE_RETENTION.md` found that the public folder is the **only** home of earlier seasons' live recordings, Honors files and indexes, that published replay files cannot be re-made from the archive (220 of 400 differ), and that the daemon never repairs an earlier season's public files. A lost or damaged public folder therefore cannot be regenerated, and every link ever handed out (`playback/games/<id>.<sha10>.json`, `broadcast/replays/<same>`, `releases/<rel>/…`, `broadcast/live/<gid>/…`, `broadcast/honors/sNNN/…`) must keep working after a restore. This is the precondition of any retention action. No retention action was built here.

## 2. What is backed up

**Public profile** (`public_backup.py create --src PUBLIC --out BACKUP`): every regular file of the public folder, byte for byte, under its own relative name: `latest.json`, `status.json`, `playback/index.json`, `playback/games/*`, `broadcast/{clock,schedule}.json`, `broadcast/honors/**`, `broadcast/replays/**`, `broadcast/live/**`, `releases/**`. The `.tmp-*` and `*.tmp` files that the daemon writes before an atomic rename are never copied (they are not published). Symbolic links in the source are refused, not followed.

**Store profile** (`--profile store`): the private broadcast store (`store/`: plan, builds, carry, per-season plan and builds, feed, games, honors, playback, clock).

The backup is a folder: `BACKUP/files/<same relative names>` plus `BACKUP/BACKUP_MANIFEST.json` (format, version, profile, per-file size and SHA-256, totals, the number of passes, whether the copy was *quiescent*, creation time, and a digest of the manifest itself). Names, bytes and the directory layout are preserved exactly; restored files get mode 0644 and directories 0755; **modification times and owners are not preserved** (restored files carry the restore time, so conditional-GET validators change once; content does not).

**Not covered:** hard links (the folder has none today; if retention action R3 is ever adopted the restored copy would duplicate those files, 0.25 GB a season, until the tool learns to preserve inodes), the host's web-server and unit files, the daemon's own scratch `--state` folder, TLS material, and anything outside the public folder and the store.

## 3. How a backup stays consistent while publication goes on

The daemon is never stopped, paused or signalled. Published replay, release and live-segment files are immutable and every other file is replaced with an atomic rename, so any single read returns one whole version. The danger is a **mix**: an index copied after a file it names was missing, or a `latest.json` that points at a release the copy does not hold. The method:

1. **Order.** Pass 1 copies the *cumulative* control files first (`latest.json` and the three indexes: they only grow), then the other small control files, then everything else. An index copied early can only name files that exist by the time the bulk is copied.
2. **Passes.** A file is copied again in a later pass only if its (inode, size, mtime) changed. A pass that finds the cumulative controls unchanged from start to end is **quiet** and ends the copy: it is a snapshot of one publication state. If publication continues, passes repeat up to `--max-passes` (default 8).
3. **Reference closure, on the copy** (never on the source): `latest.json` → its release and every file that release's manifest lists (size and SHA-256); every playback-index entry → its file with the entry's SHA-256 and the 10-hex tag in the name; every replay-library entry → its file; every Honors-index part → its file; every live manifest → head, all its segments, and the end file when final. Anything missing or different is a **failure** and the backup is not committed.
4. **Quiescent or not.** If the passes ended without a quiet pass, the backup is committed only if the closure holds, and says `"quiescent": false`: a closure-valid snapshot assembled from more than one publication state (every index entry has its file; no file is newer than anything depending on it). `--require-quiescent` refuses such a backup instead.
5. **Commit.** Everything is written under `BACKUP.partial`; the manifest (with its own digest) is written last and fsynced; then `BACKUP.partial` is renamed to `BACKUP`. An existing backup is never overwritten.

**Season hand-over during a backup** is covered because the daemon's hand-over is itself a sequence of atomic steps with a 10-second grace window (`HANDOVER_GRACE_S`, `docs/BROADCAST_CONTINUITY.md`); a backup that straddles it is either one side of it (quiet) or non-quiescent with a valid closure. Test: `test_a_hand_over_during_the_backup`.

## 4. Verifying and restoring

```
python3 -I tools/public_backup.py verify  BACKUP            # every file vs manifest (size, SHA-256), no extra files, manifest digest, then the closure
python3 -I tools/public_backup.py restore BACKUP DEST       # verify, restore into a NEW (absent or empty) directory, verify DEST again
python3 -I tools/public_backup.py check   --src PUBLIC      # read-only closure check of a live folder (no backup made)
```

* `verify` and `restore` refuse: a `.partial` folder (even one holding a complete manifest), a folder without a manifest, a truncated or edited manifest (digest), a manifest marked incomplete, a flipped byte, a truncated, missing or extra file, a missing live segment, and a backup whose manifest was rewritten to match a swapped file (the closure still fails because the playback index records the original SHA-256).
* `restore` stages into `DEST.restoring`, verifies every file as it is written and the finished tree again, and only then renames to `DEST`. A restore that fails half way leaves **no** `DEST`. It never writes into a non-empty directory and never overwrites. The original may be gone: nothing is read from it.
* Exit status 0 means every check passed. A report that is not `ok` is never a valid backup, whatever else it says.

## 5. Pairing with the private broadcast state and the league recovery set

Three things together are one recovery point:

| Component | Tool | Contents |
|---|---|---|
| League | `tools/league_backup.py` (existing) | `league.db` + its archive, the runner's event log and status, verified deep |
| Private store | `public_backup.py --profile store` | what the builder produced: plans, builds, carry, feed, private live recordings, replay export |
| Public folder | `public_backup.py --profile public` | what the daemon published |

`tools/recovery_set.py create --root R --public P --out SET [--note TEXT]` takes all three **in the order public → store → league** (so the store is never older than the public folder, and the league never older than the store) into `SET/{public,store,league}` and writes `SET/RECOVERY_SET.json` last (committed through `SET.partial`, like a backup). `verify SET [--deep]` and `restore SET DEST` check and rebuild it. Restore gives `DEST/league/{league.db, league.db.archive, ctl/}`, `DEST/store`, `DEST/public`, a layout the builder and daemon accept without change (the restored league is re-pointed at `league.db.archive`, the name the live layout uses; `league_backup` itself writes `archive.db`).

The rules, recomputed from the component manifests by `verify` (not trusted from the descriptor):

* **S1** the league's event count equals the builds' last event, or exceeds it by one (an event fired but not yet built; the builder accepts exactly this).
* **S2** no event is public that is not built.
* **S3** no public playback, replay-library or Honors entry belongs to a later season than the season on air in the store (a public folder newer than its store cannot be continued).
* **S4** the store backup and the public backup are each closure-consistent and the store backup was quiet; the components are exactly the ones the descriptor recorded (a different, valid backup swapped in is reported).

**The builder must be idle** while a set is taken, between `broadcast_build.py step` runs: it insists that the league has exactly the events it has built (or one more), so a league that is ahead of its builds cannot be continued. A set whose league event count or builds moved while it was being made is refused (`test_a_builder_that_moves_during_the_set_makes_it_invalid`). The **daemon may keep publishing**.

What a restored set gives (tested with the real engine and builder): the builder reports the same status, fires the next event and reaches the **same league digest** as the original doing the same; the restored daemon carries on publishing from the restored store and public folder, nothing already public changes, and the next event appears in `latest.json` when its time comes.

Existing league recovery material (`league_backup`, rollback copies, `dfl-backup-*.tar`) is unchanged and still valid on its own for the league; the set adds the two broadcast components and the rules that tie them to a league state. Which rollback copies to keep remains the owner's decision (`docs/STORAGE_RETENTION.md` R6).

### Procedure (not to be run against the running rehearsal or production by this work)

1. Builder idle; note the league's event count. 2. `recovery_set.py create` to a new folder on another disk or host. 3. `recovery_set.py verify SET --deep`. 4. Periodically prove it: `recovery_set.py restore SET /scratch/new` on a scratch host, run `broadcast_build.py status --root /scratch/new` and `public_backup.py check --src /scratch/new/public`. 5. Keep the set until a newer one has been verified and restored once.

## 6. What the tests show (small synthetic data; not resource figures)

`tests/test_public_backup.py`, 28 tests, built on synthetic and short real-engine publications in temporary folders:

* **Round trip:** byte-identical names, bytes and modes; an HTTP server over the restored folder (original removed) serves a replay, a live recording, a release and an Honors part by their original URLs, and every index entry loads; the daemon continues publishing from the restored folder and the resulting tree equals a control run.
* **While publishing:** the daemon advanced from inside the backup; a season hand-over during the backup (10 ticks of 8 s); a folder that never goes quiet is refused with `--require-quiescent` and otherwise gives a closure-valid backup marked `quiescent: false`.
* **Interrupted:** an exception half way, a failure just before the commit, a `.partial` with a complete manifest, a real `SIGKILL` of the CLI half way, a restore that dies half way. None leaves anything valid.
* **Damage:** the cases listed in section 4, plus a damaged *source* (not backed up), a symbolic link, a non-empty destination, an existing backup.
* **Private state:** the store profile, and a full restore of store and public with the originals deleted, publish identically afterwards; the store closure catches a missing game or release.

`tests/test_recovery_set.py`, 6 tests: create/verify/restore and carry on with the real engine (13 events), a builder that moves during the set, a failure before the commit, damaged or swapped components, an edited league manifest, and the rules unit test.

## 7. Real-size measurements

All figures here are **resource measurements on real data**, kept apart from the synthetic tests above. Data: the two real seasons of the scripted comparison (`tools/engine_comparison.py --daemon-check`, seed 20261105, event 94): public folder **117,707 files, 1,546,391,468 bytes** (1.8 GB on disk), store 117,829 files, 1,558,679,147 bytes, league + archive 284 MB. Host: 2 cores, 7 GB memory, one virtual disk shared with other work; page cache partly warm, so read-only steps (verify, compare) are faster than a cold read would be. Measured with `tools/measure_public_backup.py` (raw reports: `docs/PUBLIC_BACKUP_MEASUREMENTS_2026-11-06.json`; peak RSS from `/proc/<pid>/status` VmHWM of that one process). **This is not the Droplet and not four seasons.**

| Step (idle folder) | Files | Wall | Peak memory | Result |
|---|---|---|---|---|
| Public: `create` | 117,707 | 53 s | 202 MB | 1 pass, quiescent, closure ok |
| Public: `verify` | 117,707 | 10 s | 122 MB | 1,546,391,468 bytes re-hashed, 0 problems |
| Public: `restore` into a new folder | 117,707 | 82 s | 136 MB | compared with the source byte for byte and by mode: 0 missing, 0 extra, 0 different |
| Store: `create` / `verify` / `restore` | 117,829 | 64 s / 4 s / 64 s | 197 / 140 / 141 MB | same comparison: 0 differences |
| Recovery set (league + store + public): `create` | | 152 s | 219 MB | rules S1 to S4 hold (league 94 events = 94 built = 94 public; season on air 2) |
| Recovery set: `verify --deep` | | 58 s | 169 MB | ok |
| Recovery set: `restore` with every original deleted | | 315 s | 169 MB | builder `status` season 2, 47 events built, 400 games; **league deep digest `485b49f5…` (event 94), the same as the original**; closure of the restored public folder and store: 0 problems; 65 sampled playback files and their replay-library copies fetched over HTTP from a local server (0 hash mismatches), 800 live manifests parse |

The backup of the public folder is 1,566,432,970 bytes (the files plus a 20 MB manifest); a recovery set is about 3.9 GB on disk. Throughput is dominated by the number of files (about 2,200 a second here), not by their size.

**While the daemon publishes (real size).** The real daemon (`broadcast_multiseason_check.py --through-end`, virtual time, 20 s steps, from the same store) published the two seasons into a fresh folder while a backup started when 256 of 800 games were public. It took 27 s at 94 MB, made **8 passes without ever finding a quiet one** (`"quiescent": false`, 49,651 files, 652,079,923 bytes at the moment it was committed), the closure held, `verify` passed (4 s), and a restore of it verified and passed `check`. After the daemon finished, every immutable file in the backup (everything except the controls, the indexes and live manifests, which are replaced as publication goes on) was byte-identical to the same file in the final tree, and nothing in the backup was absent from it. Virtual time publishes far faster than the real broadcast, so this is a harsher case than production; in real time the passes are expected to find a quiet moment. The final tree of this run was the same size as the idle run (117,707 files, 1,546,391,468 bytes).

**A refusal the real data produced.** The first set attempt used the comparison's builder root, where the daemon had never run, so the store had no `carry.json` while the public folder (made from a hard-linked copy) already showed season 2. Rule S3 refused it ("the public copy is newer than the store"), correctly. The set was then taken from the copy that carries the daemon's `carry.json`. A refused `create` leaves `SET.partial` behind (it is never valid; delete it by hand before trying again, which is also what happens after a crash).

**Extrapolation to four seasons (not measured).** A season adds about 58,900 public files and 0.77 GB. Four seasons would be about 235,000 files and 3.1 GB: expect roughly twice the times above for the public folder (create about 2 minutes, restore about 3 minutes on this host) and about twice the memory (the manifest is held in memory: about 400 MB). On the Droplet's disk and with a cold cache the times will differ; rehearse there before relying on them.

## 8. Limitations

* **Four real seasons are not available locally.** Only two real seasons exist on this host (the scripted comparison tree, built by the real builder and daemon). Production's four seasons live on the Droplet and were not touched. Figures for four seasons are an extrapolation (section 7).
* Hard links, modification times and owners are not preserved (section 2).
* `quiescent: false` backups exist by design when publication never pauses; the closure guarantees the backup is usable and that no index names a missing file, but it is an assembled snapshot, not one instant. A recovery set requires its store component to be quiet.
* The set requires an idle builder; there is no way to take a consistent set while the builder is mid-event (the builder's own precondition).
* The tool verifies content and references; it does not check that a web server serves the restored folder with the right headers (tested over a plain `http.server` only), and it does not restore host configuration.
* Backup, verify and restore are single-threaded Python and read every file; they are I/O-bound (section 7).
* No scheduling, rotation, off-host transfer, encryption or retention of backups was built. Where backups are kept and for how long is the owner's decision.
