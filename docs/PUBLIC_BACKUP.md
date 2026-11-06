# Public-folder backup and restore (milestone M8a)

*Bounded-memory and locking changes added 2026-11-06 (sections 3a, 4, 7 and 8); originally written the same day on branch `backend/public-backup-2026-11-06`, from `7668245230c2692fd81430fbd7bb6114a01121d3`. Everything here was built and tested locally on scratch copies. Nothing was deployed, nothing on the Droplet (the running rehearsal included) was contacted, no retention or deletion tool was written or run, and no frontend file changed.*

Tools: `tools/public_backup.py` (public folder, private store), `tools/recovery_set.py` (league + store + public as one unit), `tools/measure_public_backup.py` (real-size measurements), `tools/engine_comparison.py` (separate: `docs/ENGINE_COMPARISON_REPRODUCTION.md`). Tests: `tests/test_public_backup.py` (50), `tests/test_recovery_set.py` (18), `tests/test_backup_resume.py` (6), `tests/test_backup_resume_real.py` (1, opt-in), with `tests/published_bytes.py` (the shared "previously published bytes" check) and `tests/fixtures/public_backup_v1` (a backup made by the previous version of the tool). Also `tools/rootlock.py` (the builder-root lock).

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

## 3a. Bounded memory, temporary disk, and one operation per name

**Inventory and manifest processing now use bounded memory; the closure check's whole-index loading is a remaining scaling limit.** Before this change `create` kept a dict of every file (about 1.65 KB per file) and built the whole manifest in memory; `verify` and `restore` loaded the whole manifest and several per-file dicts (about 1.0 KB per file). Now:

* The inventory (what is on disk, what has been copied with its SHA-256, what the manifest says) lives in a temporary **SQLite index** read and written in batches of 2,000 rows with an 8 MB page cache. No list or dict of every file exists. Problems are counted but only the first 50 are kept (`n_problems` is the true count).
* The manifest is **written as a stream** (digest computed as it goes) and **read as a stream** by a small incremental parser, whatever its whitespace or key order. A damaged or truncated manifest is still refused: the parser checks syntax, the format and `complete` markers, that nothing follows the end, every file entry's shape (relative path without `..`, size, 64-hex SHA-256), no path listed twice, and the digest.
* **Format and digest are unchanged** (version 1; `sha256` of the canonical JSON of `{files, profile, snapshot.{quiescent,cumulative}}`), so every backup made by the earlier tool still verifies and restores (test with a committed fixture made by the earlier tool).
* Measured (synthetic trees of small files with no index documents): create, verify and restore peak at 30 to 34 MB from 29,000 to 472,000 files. That is a bound on the **file-count-dependent part only**.

**What is not bounded: the reference closure loads whole-index documents.** The closure check parses `playback/index.json`, `broadcast/replays/index.json` and `broadcast/honors/index.json` as whole JSON documents (plus one release manifest at a time, and builds a name-to-entry dict of the playback index and a set of replay ids). Their size follows the number of games and events, not the number of files. Measured with `tools/measure_public_backup.py closure` (synthetic playback index and replay library of N games each, `docs/PUBLIC_BACKUP_MEASUREMENTS_2026-11-06.json`, key `closure_index_documents`):

| Games listed | Index documents on disk | `create` peak | `verify` peak |
|---|---|---|---|
| 2,000 | 0.6 MB | 25 MB | 25 MB |
| 8,000 | 2.4 MB | 36 MB | 32 MB |
| 32,000 | 9.6 MB | 70 MB | 70 MB |
| 128,000 | 38.7 MB | 195 MB | 194 MB |

Above about 8,000 games the peak grows by roughly **1.3 KB per listed game** (about four times the on-disk size of the documents, Python's overhead). The league lists about 400 games a season, so ten seasons would be about 4,000 games and a negligible 3 to 5 MB; the limit matters only far beyond the planned horizon (more than 30,000 games, a few dozen seasons). Making the closure stream those documents too was **not** done: it would change closure code that every restore depends on, for a size the league does not reach. Until then, "bounded memory" in this document means the inventory and the manifest.

**The price is temporary disk**, reported in every result as `index_bytes`: about 0.27 KB per file at create (inside `BACKUP.partial`, removed before the commit; a failed or killed backup keeps it with the rest of the evidence) and about 0.24 KB per file for `verify`/`restore` (in `--scratch`, default the system temporary folder, removed afterwards). Point `--scratch` at a real disk, not at RAM (`/tmp` is tmpfs on some hosts).

**One operation per name.** `create`, `restore` and `quarantine` (and the recovery-set commands) take an operating-system lock, without waiting, on a hidden file `.NAME.lock` beside the backup (`NAME` without any `.partial` or `.restoring`). A second attempt is refused with "in progress" and **touches nothing**: it cannot write into, replace, `--discard-partial` or quarantine the `.partial` of a backup that is running. The lock is released by the system when its holder ends, however it ends (a `kill -9` included), so a stale `.partial` from a dead process can be quarantined at once and the lock file never has to be cleaned up (and is never deleted by a tool: deleting it would let two holders lock two different files). A recovery set takes the set's lock first and its components run under it. Limits: it is an advisory `flock` among these tools, on a local filesystem; a person running `rm`, or a tool on another host over NFS, is not stopped by it.

## 3b. Disk-space preflight and a full disk (M8b)

**Preflight** (`tools/backup_space.py`; run automatically by `public_backup.py create|verify|restore` and `recovery_set.py create` unless `--no-preflight`; also standalone and read-only):

```
python3 -I tools/backup_space.py create  --src PUBLIC --out BACKUP [--allow-ram]
python3 -I tools/backup_space.py verify  BACKUP [--scratch DIR] [--allow-ram]
python3 -I tools/backup_space.py restore BACKUP DEST [--scratch DIR] [--allow-ram]
```

* **Estimate per filesystem.** Files, manifest and working index are estimated separately from the source's file count, allocated bytes (`st_blocks`, so 2 KB files count as 4 KB) and mean path length (index about 2 x path + 170 bytes a file at create, 2 x path + 130 at verify and restore; manifest about path + 127; checked against real runs within 20% for short and long paths by `tests/test_backup_space.py`). Amounts for paths that share a filesystem (`st_dev`) are added: a create writes its files, manifest and index under `BACKUP.partial`, one filesystem; a restore adds the destination's files to the scratch index when `--scratch` is on the same filesystem, and does not when it is elsewhere. A margin (10% of the data, 25% of the index, 8 MB fixed) is added to what must be free.
* **Existing evidence counts.** `.partial` and `.restoring` folders and the `partial-quarantine` folder beside the destination are listed with their sizes. They are never counted as free space and nothing deletes them; the report gives `attempts_that_fit` (free space divided by the size of one attempt), and warns when only one fits, because a failed attempt leaves a `.partial` of up to the full size, kept. A stale `.partial` of the same name is reported as blocking (quarantine it first; its space stays used); a name whose lock is held is reported as "in progress", not as stale.
* **RAM is refused by default.** A destination or scratch folder on `tmpfs` or `ramfs` (read from `/proc/self/mountinfo`, longest mount point wins, so a disk mounted inside a tmpfs counts as disk) fails the preflight with the reason: the working index exists to move memory to disk, and a backup in RAM is lost at the next reboot. `--allow-ram` accepts it and records a warning.
* **It is an estimate, not a reservation.** Another process can fill the disk after the check, and the estimate can be wrong for unusual trees. The preflight does not allocate or lock space. The library functions (`create`, `verify`, `restore`) do not run it by themselves, so code that calls them directly (the tests, `recovery_set`'s inner calls) is not refused on a RAM-backed `/tmp`.

**A full disk during the run** (`ENOSPC`, quota or file-size limit, including SQLite's "database or disk is full"):

* `create` stops with "the disk is full": nothing is committed, `BACKUP.partial` is kept with what was written and its working index, already valid backups are not touched, nothing is deleted, and the message says how to quarantine and retry. No path under the final name appears.
* `restore` returns `ok: false, stage: copy`: no destination is created, `DEST.restoring` is kept, the backup is untouched. `quarantine` now also accepts `*.restoring` (a rename, so a retry under the same name is possible without deleting anything).
* `verify` whose scratch is full reports it as a problem and removes its working folder; the backup is untouched.
* `quarantine` needs no space (a rename; its note file is best effort on a full disk, `note_written` says so). This was a real defect: on a genuinely full filesystem it moved the folder and then crashed writing the note.
* Retry: free space or choose another destination; quarantine the unfinished folder; run again. Tested with a real small tmpfs (genuine `ENOSPC`; skipped, and reported as skipped, where `mount` is not allowed) and with a simulated failure half way through a copy.

## 4. Verifying and restoring

```
python3 -I tools/public_backup.py verify  BACKUP [--scratch DIR]   # every file vs manifest (size, SHA-256), no extra files, manifest digest, then the closure
python3 -I tools/public_backup.py restore BACKUP DEST [--scratch DIR]   # verify, restore into a NEW (absent or empty) directory, verify DEST again
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

`tools/recovery_set.py create --root R --public P --out SET [--note TEXT]` takes the three **in this order: public folder, then private store, then league**, each complete before the next one starts (this is the code's order, in `recovery_set._create`; `test_the_capture_order_is_public_then_store_then_league` checks it from outside), into `SET/{public,store,league}`, and writes `SET/RECOVERY_SET.json` last (committed through `SET.partial`, like a backup). `verify SET [--deep]` and `restore SET DEST` check and rebuild it. Restore gives `DEST/league/{league.db, league.db.archive, ctl/}`, `DEST/store`, `DEST/public`, a layout the builder and daemon accept without change (the restored league is re-pointed at `league.db.archive`, the name the live layout uses; `league_backup` itself writes `archive.db`).

**Why that order gives a set that can be continued.** With the builder excluded (below), the one thing that keeps moving while a set is taken is the daemon, and it writes two places: the public folder, and, in the store, the viewers' clock (`store/clock.json`, the truth) and the season hand-over record (`store/carry.json`). Whatever is captured later has seen more of the daemon's work. Taking the public folder first and the store second means the store's clock and hand-over record are never *behind* what is public: restored, the daemon finds a clock at least as far on as everything already published and re-derives whatever is missing or behind from the store on its first tick (a hand-over that fell between the two captures leaves `carry.json` in the store and nothing of season 2 in the public folder; tested, and the resumed tree equals an uninterrupted run's). The league and the store do not move at all once the builder is excluded, so their relative order does not matter; rule S1 ties them. The **reverse** order is not recoverable: a public folder captured after the store can show results (even a whole next season) that the stored clock has not reached yet. In the test that deliberately takes the store first, the restored viewers' clock came back **1,600 s behind** results that were already public, and rule S3 refuses such a set.

The rules, recomputed from the component manifests by `verify` (not trusted from the descriptor):

* **S1** the league's event count equals the builds' last event, or exceeds it by one (an event fired but not yet built; the builder accepts exactly this).
* **S2** no event is public that is not built.
* **S3** no public playback, replay-library or Honors entry belongs to a later season than the season on air in the store (a public folder newer than its store cannot be continued).
* **S4** the store backup and the public backup are each closure-consistent and the store backup was quiet; the components are exactly the ones the descriptor recorded (a different, valid backup swapped in is reported).

**The builder is excluded while a set is taken (a lock, not a status check).** Every command that changes a builder root (`broadcast_build.py init`, `step`, `next-season`, `continuous`) takes the root's exclusive lock `ROOT/.builder.lock` (`tools/rootlock.py`, an operating-system `flock`, taken without waiting), and so does `recovery_set.py create`, from before the first event count is read until the set is committed or has failed. A builder that is running when the set is started is refused at once ("the builder is not idle") and nothing is copied; a builder started at any moment during the set (tested after the first count, while the public folder is copied, while the store is copied, and after the league copy) exits 75 with "busy: recovery set … holds the builder lock" and changes nothing. A killed holder leaves nothing behind (the operating system drops the lock). `broadcast_build.py status`, the daemon and every check are not blocked. A league runner that is running (`league/ctl/runner.lock` held) also refuses the set. A status check alone could race with a build that starts a moment later; the lock cannot. The event-count comparison before and after the copy is still made, as a second line of defence, for anything that moves the league without the lock (a league runner that is already running is refused at the start through `runner.lock`; a process that changes the league without either lock is not prevented, only detected).

What a restored set gives (tested with the real engine and builder): the builder reports the same status, fires the next event and reaches the **same league digest** as the original doing the same; the restored daemon carries on publishing from the restored store and public folder, nothing already public changes, and the next event appears in `latest.json` when its time comes.

Existing league recovery material (`league_backup`, rollback copies, `dfl-backup-*.tar`) is unchanged and still valid on its own for the league; the set adds the two broadcast components and the rules that tie them to a league state. Which rollback copies to keep remains the owner's decision (`docs/STORAGE_RETENTION.md` R6).

### Procedure (not to be run against the running rehearsal or production by this work)

1. Builder idle; note the league's event count. 2. `recovery_set.py create` to a new folder on another disk or host. 3. `recovery_set.py verify SET --deep`. 4. Periodically prove it: `recovery_set.py restore SET /scratch/new` on a scratch host, run `broadcast_build.py status --root /scratch/new` and `public_backup.py check --src /scratch/new/public`. 5. Keep the set until a newer one has been verified and restored once.

### Stale `.partial` folders: quarantine, never delete, then retry

A backup or set that failed, or whose process was killed, leaves `NAME.partial`. It is never valid (no tool will verify or restore it, even if its manifest happens to be complete), and it blocks a retry under the same name ("`NAME.partial` exists"). It may be evidence of why the run failed, so it is moved aside, whole, not removed:

```
python3 -I tools/public_backup.py quarantine /backups/public-2026-11-06.partial        # for a public or store backup
python3 -I tools/recovery_set.py  quarantine /backups/set-2026-11-06.partial           # for a recovery set
```

* It is a **rename on the same filesystem**, into `partial-quarantine/` beside the backups (or `--to DIR` on the same filesystem), under the name `<UTC stamp>-<original name>` (it still ends in `.partial`, so it can never pass for a backup). Nothing is copied or deleted; a cross-filesystem move is refused. A short `QUARANTINE_NOTE.json` (original path, time, file count, bytes, whether it had a manifest) is the only file added.
* Only a folder whose name ends in `.partial` is accepted. A finished backup or set is never moved.
* Then retry the `create` under the original name. The quarantined folder is untouched by the retry and by every tool.
* Do **not** use `public_backup.py create --discard-partial` when the cause matters: that option deletes the stale folder. It exists for scripted retries where the evidence is not wanted. It, like `quarantine`, is refused while another operation holds the name (section 3a), so neither can touch the `.partial` of a backup that is still running.
* Look at the stale folder before throwing it away: `ls`, its `BACKUP_MANIFEST.json` (absent means the run died before the end), the failure message of the run. Removing quarantined folders is a separate, deliberate decision for the owner; no tool here does it.
* A refused set (a rule such as S3 failing) leaves its `.partial` too, and it can be large (a full copy of all three components): check the disk before the retry.

### Resuming from a restored set

After `restore`, the restored root runs exactly as the original did: `broadcast_build.py step` continues the league (same digest as the original doing the same; tested with the real engine), and the daemon, pointed at the restored `store` and `public`, resumes publication. The daemon treats the time between the backup and the restore as **downtime**: the viewers' clock stands still at the end of its last lease (at most `LEASE_S`, 90 s, past the last heartbeat in the backup) and continues from there, so a restore hours or days later does not make the show skip ahead or replay. Starting the builder and the daemon on a restored root is an operator step on a new host; nothing here does it.

## 6. What the tests show (small synthetic data; not resource figures)

`tests/test_backup_space.py`, 17 tests (estimates within 20% for short and long paths; mount-table parsing with escapes; a tmpfs scratch or destination refused with the reason and allowed on request, also through both command lines; low-space refusal that writes nothing; per-filesystem sums; evidence listed and never counted as free; a name in use reported as in progress; full disk during create, restore and verify on a real small tmpfs and simulated; valid backups untouched; evidence kept byte for byte through quarantine; the retry succeeds) and `tests/test_public_backup.py`, 50 tests (28 original behaviour tests, the 5 quarantine tests below, and the bounded-memory, manifest and locking tests listed in 6a), built on synthetic and short real-engine publications in temporary folders:

* **Round trip:** byte-identical names, bytes and modes; an HTTP server over the restored folder (original removed) serves a replay, a live recording, a release and an Honors part by their original URLs, and every index entry loads; the daemon continues publishing from the restored folder and the resulting tree equals a control run.
* **While publishing:** the daemon advanced from inside the backup; a season hand-over during the backup (10 ticks of 8 s); a folder that never goes quiet is refused with `--require-quiescent` and otherwise gives a closure-valid backup marked `quiescent: false`.
* **Interrupted:** an exception half way, a failure just before the commit, a `.partial` with a complete manifest, a real `SIGKILL` of the CLI half way, a restore that dies half way. None leaves anything valid.
* **Damage:** the cases listed in section 4, plus a damaged *source* (not backed up), a symbolic link, a non-empty destination, an existing backup.
* **Private state:** the store profile, and a full restore of store and public with the originals deleted, publish identically afterwards; the store closure catches a missing game or release.

`tests/test_recovery_set.py`, real engine (13 events), 17 tests: create/verify/restore and carry on; **the capture order**; **builder exclusion** (a builder started right after the status check, while the public folder is copied, while the store is copied and after the league copy is refused with exit 75 and the set stays valid; a running builder refuses the set before anything is written; a killed holder leaves no lock; every changing builder command takes the lock while `status` does not; a running league runner refuses the set; the builder is free again afterwards); a failure before the commit; damaged or swapped components; an edited league manifest; the rules unit test; **a stale set `.partial` quarantined and the retry succeeds**; **a hand-over written by the daemon during the set is not mistaken for builder movement** (a defect the real-engine hand-over test found: the set used to refuse itself).

`tests/test_public_backup.py` also has 5 **quarantine** tests (a stale `.partial` blocks a retry and the error says how to quarantine it; the folder is moved whole and unchanged, the retry succeeds, the evidence is untouched; two quarantines never collide; only `.partial` folders are accepted and a finished backup is never moved; the command line).

`tests/test_backup_resume.py`, 5 tests, synthetic two-season store with the real guarded daemon and the real publication code: **non-quiescent public backup + store backup, a control copy, originals deleted, restore, then control and restored copy resumed in lock step** (same downtime, same builder steps), checking at every checkpoint that the public trees are equal file for file and byte for byte, the viewers' clock is equal and never jumps, no result is public before its release position (checked against the plan), and **every file that was public when the originals were lost has exactly the same bytes afterwards, on the control and on the restored copy** (`tests/published_bytes.py`: single-pointer controls, live manifests and the append-only indexes are the only things the daemon is designed to replace; an index must keep its earlier entries as an exact prefix; nothing is normalised; a unit test shows the check fails on a changed, removed or reordered file). Scenarios: ordinary play with the builder behind and finishing two events after the restore; three downtimes (0 s, 40 s, 2 h); a season hand-over inside the public copy with the builder then finishing the next season; a hand-over between the public and store captures; and the reverse capture order, which the rules refuse and which really rewinds the clock.

`tests/test_backup_resume_real.py` is the **real-engine** version of the hand-over (opt-in: it needs a finished first season in `DFL_SEASON1_ROOT`, about 6 GB and 15 minutes, and is reported as *skipped* without it): the real builder builds season 2's first 13 events, the real daemon runs season 1 to a minute before the hand-over, a recovery set is taken while the daemon crosses the end of the season inside the (non-quiescent) public copy, the originals are deleted, the set is restored, and the real builder fires two more events on the control and on the restored copy (equal league digests) while both daemons run on to the release of those events (equal trees, no jump, nothing early, earlier files unchanged).

**The one comparison exception, and its limit.** The two events the builder fires *after* the restore (events 61 and 62) were built by two builders at two different moments, once on the control and once on the restored copy, and the builder stamps `generated_at` into each new release's manifest. So, and only so, for `releases/<new release>/manifest.json` of an event built after the restore, the two copies are compared with that one top-level field removed (it must be present in both); `latest.json` (whose `manifest_sha256` is the digest of that manifest) is compared by its `release` name. Every other file of a new release, and **every** file anywhere else, is compared byte for byte. No previously published file is ever normalised: the 13 season-2 events built before the backup keep their manifests, `generated_at` included, byte for byte even when they only became public after the restore, and the strict "previously published bytes" check above covers everything public at the crash. An earlier version of this test normalised `generated_at` in every JSON file of any release of event 60 or higher; that was too wide and was removed.

### 6a. Bounded memory and concurrency tests

Written first and run against the previous implementation (`8fa89d7`) to see them fail: the memory test (peak of a fresh process at 12,000 and 48,000 files, each of create, verify, restore, growth must stay under 12 MB: the previous tool grew 57, 42 and 39 MB), the scratch and `index_bytes` tests, the index-left-in-an-unfinished-backup test, the lock tests (a second `create`, a `create --discard-partial`, and `quarantine`, each while a backup is running, must be refused and leave the active `.partial` byte-identical; five processes starting together must make exactly one valid backup; after a `kill -9` the name is free and the stale `.partial` can be quarantined; two restores to one destination) and the recovery-set variant (a second `create`, a `quarantine` and a `restore` of a set name while that set is being made are refused and its `.partial` is unchanged). Added with them, passing from the start because they pin behaviour that must not change: every layout of one manifest (compact, indented, sorted) verifies and restores with the parser reading 7 bytes at a time; a manifest cut at any of a dozen points, or followed by garbage, is never valid; the digest equals the earlier tools' definition; paths that escape the destination, repeated paths and more than 50 damaged files are handled; and the committed version-1 backup verifies and restores.

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

**A refusal the real data produced.** The first set attempt used the comparison's builder root, where the daemon had never run, so the store had no `carry.json` while the public folder (made from a hard-linked copy) already showed season 2. Rule S3 refused it ("the public copy is newer than the store"), correctly. The set was then taken from the copy that carries the daemon's `carry.json`. A refused `create` leaves `SET.partial` behind (it is never valid); it is kept as evidence with `recovery_set.py quarantine` and then the set is retried (section 5, *Stale `.partial` folders*).

**How time, memory and temporary disk grow with the file count (synthetic trees; not the real folder).** `tools/measure_public_backup.py scale` makes, backs up, verifies and restores trees of 2 KB files with realistic path lengths (800 to a folder), one size after another, each tool run as its own process. Peak memory is that process's `VmHWM`, runtime its wall time, temporary disk the working index the tool reports. Reported separately because they behave differently:

*Before* (the dict-based tool, measured earlier the same day on the same host): create peak 67 / 115 / 211 / 409 / 799 MB and verify/restore peak 49 / 82 / 150 / 240 / 463 MB at 29,000 / 59,000 / 118,000 / 236,000 / 472,000 files, with a slope of about 1.65 KB per file for create and 1.0 KB for verify and restore, and no temporary disk.

*After* (the SQLite-index tool):

| Files | `create` peak memory | `verify` peak | `restore` peak | wall: create / verify / restore | temporary disk: create / verify and restore |
|---|---|---|---|---|---|
| 29,000 | 31.6 MB | 30.1 MB | 30.4 MB | 2.2 s / 0.8 s / 2.6 s | 7.9 / 6.9 MB |
| 59,000 | 31.6 MB | 33.4 MB | 33.6 MB | 4.4 s / 1.8 s / 5.0 s | 16.0 / 14.0 MB |
| 118,000 | 30.6 MB | 34.3 MB | 34.4 MB | 10.0 s / 3.4 s / 10.2 s | 32.0 / 28.1 MB |
| 236,000 | 30.7 MB | 34.3 MB | 34.4 MB | 20.2 s / 7.2 s / 20.9 s | 64.0 / 56.4 MB |
| 472,000 | 30.7 MB | 34.2 MB | 34.4 MB | 44.6 s / 14.4 s / 70.6 s | 127.9 / 113.0 MB |

* **Peak memory is flat in the file count**: 30.4 to 34.4 MB at every size, a sixteenfold range, for all three steps (the fixed part is the interpreter, SQLite and one batch). These trees contain no index documents; the closure's whole-index loading adds to it (section 3a). At 472,000 files that is 31 MB against 799 MB for `create` and 34 MB against 463 MB for `verify` and `restore`.
* **Temporary disk grows linearly** and is now the cost: about 0.27 KB per file at create and 0.24 KB per file at verify and restore (128 MB and 113 MB at 472,000 files). It is a working file in the backup's own `.partial` (create) or in `--scratch` (verify and restore).
* **Runtime is not worse** here (create 45 s at 472,000 files; the earlier tool took 34 s on a warmer cache, so there is no claim of a speed-up or a slow-down: wall times on this shared host vary with the page cache and the other work, and the two series were not taken back to back; the 118,000-file create took 22 s before and 10 s after for the same reason). Time still follows the number of files and the disk. Not measured: the same steps on the Droplet's disk with a cold cache.
* **Not re-measured:** the real 117,707-file two-season folder (202 MB create before): its source tree was no longer on this host and regenerating it takes hours. The synthetic series predicted it within 5% before the change, which is the only evidence that the synthetic shape carries over; the real folder also has index documents that the synthetic one does not: about 800 games listed, i.e. roughly 1 MB by the measured slope, so its figure should be a few MB above 31 MB and is expected to stay far below 202 MB. This is an expectation, not a measurement. The real-engine hand-over test (below) runs the new code on a real public folder, and passes, but it does not record memory.

**Estimates for four real seasons (not measured).** With about 235,000 files (an extrapolation from two seasons), memory for all three steps would be about 31 to 34 MB (the measured series is flat across 29,000 to 472,000 files) plus the closure's index documents, and the temporary disk about 60 MB for create and 55 MB for verify or restore (from the measured slope). The time is not extrapolated; re-measure on the Droplet.

## 8. Limitations

* **Four real seasons are not available locally.** Only two real seasons exist on this host (the scripted comparison tree, built by the real builder and daemon). Production's four seasons live on the Droplet and were not touched. Everything said about four seasons is an estimate and is labelled as one (section 7).
* **Resuming is proven on a synthetic two-season store (always run) and on one real-engine hand-over (opt-in, needs a finished first season; one seed, one tick pattern, one 300 s downtime).** A restore on a different host, with the real web server in front, and a restore of a set taken in the middle of a real week of play (rather than across the hand-over) were not rehearsed.
* **The builder exclusion works among the tools that take the lock** (every changing `broadcast_build.py` command and `recovery_set.py create`) and the league runner's own lock. A program that changes the league or the store without either (for example a hand-run `sqlite3` session, or a runner started with a different control directory) is not prevented; the event-count comparison before and after the copy would detect a change of the league or the builds but not other edits. The lock is not inherited by the league runner that the builder starts, so a builder killed while its runner is still working could leave the runner going with the builder lock free (the runner's own lock then refuses a set).
* Hard links, modification times and owners are not preserved (section 2).
* `quiescent: false` backups exist by design when publication never pauses; the closure guarantees the backup is usable and that no index names a missing file, but it is an assembled snapshot, not one instant. A recovery set requires its store component to be quiet.
* There is no way to take a consistent set while the builder is mid-event: the set waits for the builder to finish (it is refused until then), by design.
* The tool verifies content and references; it does not check that a web server serves the restored folder with the right headers (tested over a plain `http.server` only), and it does not restore host configuration.
* Backup, verify and restore are single-threaded Python and read every file; they are I/O-bound (section 7). Time figures come from one host and one cache state.
* **Temporary disk instead of memory** (section 3a): about 0.27 KB per file, in the backup's `.partial` or in `--scratch`. If the scratch folder is in RAM (`/tmp` as tmpfs) the memory saving is lost; there is no check for that. A file name that is not valid UTF-8 is refused at create (public folder names are ASCII by construction; the earlier tool would have recorded such a name escaped).
* **Whole-document indexes are a scaling limit** (section 3a): the reference closure still loads the playback, replay and Honors indexes whole, about 1.3 KB of peak memory per listed game (measured, synthetic). Negligible at the planned size (about 400 games a season), 195 MB at 128,000 games.
* **The lock is advisory and local** (section 3a): it stops these tools from colliding, not a person with `rm`, and it is not a distributed lock. A backup killed with the working index present leaves it in `BACKUP.partial` (it can be hundreds of MB at 472,000 files) until the owner removes the quarantined folder.
* **No SQLite corruption protection:** the index has no journal and no fsync on purpose (it is disposable). A crash or full disk during a run makes that run fail or its `.partial` unusable; it never makes a committed backup wrong, because the manifest and the files are what verify checks.
* No scheduling, rotation, off-host transfer, encryption or retention of backups was built. Where backups are kept and for how long is the owner's decision. Quarantined `.partial` folders accumulate until the owner removes them.
