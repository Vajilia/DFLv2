# Public-folder backup and restore (milestone M8a)

*Written 2026-11-06 on branch `backend/public-backup-2026-11-06`, from `7668245230c2692fd81430fbd7bb6114a01121d3`. Everything here was built and tested locally on scratch copies. Nothing was deployed, nothing on the Droplet (the running rehearsal included) was contacted, no retention or deletion tool was written or run, and no frontend file changed.*

Tools: `tools/public_backup.py` (public folder, private store), `tools/recovery_set.py` (league + store + public as one unit), `tools/measure_public_backup.py` (real-size measurements), `tools/engine_comparison.py` (separate: `docs/ENGINE_COMPARISON_REPRODUCTION.md`). Tests: `tests/test_public_backup.py` (33), `tests/test_recovery_set.py` (16), `tests/test_backup_resume.py` (5), `tests/test_backup_resume_real.py` (1, opt-in). Also `tools/rootlock.py` (the builder-root lock).

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
* Do **not** use `public_backup.py create --discard-partial` when the cause matters: that option deletes the stale folder. It exists for scripted retries where the evidence is not wanted.
* Look at the stale folder before throwing it away: `ls`, its `BACKUP_MANIFEST.json` (absent means the run died before the end), the failure message of the run. Removing quarantined folders is a separate, deliberate decision for the owner; no tool here does it.
* A refused set (a rule such as S3 failing) leaves its `.partial` too, and it can be large (a full copy of all three components): check the disk before the retry.

### Resuming from a restored set

After `restore`, the restored root runs exactly as the original did: `broadcast_build.py step` continues the league (same digest as the original doing the same; tested with the real engine), and the daemon, pointed at the restored `store` and `public`, resumes publication. The daemon treats the time between the backup and the restore as **downtime**: the viewers' clock stands still at the end of its last lease (at most `LEASE_S`, 90 s, past the last heartbeat in the backup) and continues from there, so a restore hours or days later does not make the show skip ahead or replay. Starting the builder and the daemon on a restored root is an operator step on a new host; nothing here does it.

## 6. What the tests show (small synthetic data; not resource figures)

`tests/test_public_backup.py`, 28 tests (plus the 5 quarantine tests below), built on synthetic and short real-engine publications in temporary folders:

* **Round trip:** byte-identical names, bytes and modes; an HTTP server over the restored folder (original removed) serves a replay, a live recording, a release and an Honors part by their original URLs, and every index entry loads; the daemon continues publishing from the restored folder and the resulting tree equals a control run.
* **While publishing:** the daemon advanced from inside the backup; a season hand-over during the backup (10 ticks of 8 s); a folder that never goes quiet is refused with `--require-quiescent` and otherwise gives a closure-valid backup marked `quiescent: false`.
* **Interrupted:** an exception half way, a failure just before the commit, a `.partial` with a complete manifest, a real `SIGKILL` of the CLI half way, a restore that dies half way. None leaves anything valid.
* **Damage:** the cases listed in section 4, plus a damaged *source* (not backed up), a symbolic link, a non-empty destination, an existing backup.
* **Private state:** the store profile, and a full restore of store and public with the originals deleted, publish identically afterwards; the store closure catches a missing game or release.

`tests/test_recovery_set.py`, real engine (13 events), 16 tests: create/verify/restore and carry on; **the capture order**; **builder exclusion** (a builder started right after the status check, while the public folder is copied, while the store is copied and after the league copy is refused with exit 75 and the set stays valid; a running builder refuses the set before anything is written; a killed holder leaves no lock; every changing builder command takes the lock while `status` does not; a running league runner refuses the set; the builder is free again afterwards); a failure before the commit; damaged or swapped components; an edited league manifest; the rules unit test; **a stale set `.partial` quarantined and the retry succeeds**.

`tests/test_public_backup.py` also has 5 **quarantine** tests (a stale `.partial` blocks a retry and the error says how to quarantine it; the folder is moved whole and unchanged, the retry succeeds, the evidence is untouched; two quarantines never collide; only `.partial` folders are accepted and a finished backup is never moved; the command line).

`tests/test_backup_resume.py`, 5 tests, synthetic two-season store with the real guarded daemon and the real publication code: **non-quiescent public backup + store backup, a control copy, originals deleted, restore, then control and restored copy resumed in lock step** (same downtime, same builder steps), checking at every checkpoint that the public trees are equal file for file and byte for byte, the viewers' clock is equal and never jumps, no result is public before its release position (checked against the plan), and every file public before the backup is unchanged. Scenarios: ordinary play with the builder behind and finishing two events after the restore; three downtimes (0 s, 40 s, 2 h); a season hand-over inside the public copy with the builder then finishing the next season; a hand-over between the public and store captures; and the reverse capture order, which the rules refuse and which really rewinds the clock.

`tests/test_backup_resume_real.py` is the **real-engine** version of the hand-over (opt-in: it needs a finished first season in `DFL_SEASON1_ROOT`, about 6 GB and 15 minutes, and is reported as *skipped* without it): the real builder builds season 2's first 13 events, the real daemon runs season 1 to a minute before the hand-over, a recovery set is taken while the daemon crosses the end of the season inside the (non-quiescent) public copy, the originals are deleted, the set is restored, and the real builder fires two more events on the control and on the restored copy (equal league digests) while both daemons run on to the release of those events (equal trees, no jump, nothing early, earlier files unchanged).

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

**How time and memory grow (measured on synthetic trees; not the real folder).** Peak memory depends on the **number of files**, not on their bytes (files are streamed in 1 MB chunks; the tool keeps one small record per file and, at the end of `create`, the manifest text built from them). Doubling the *data* therefore does not double the memory by itself: it does so only if the file count doubles, and then not exactly, because there is a fixed part. To see the shape instead of assuming it, `tools/measure_public_backup.py scale` makes, backs up, verifies and restores trees of 2 KB files with realistic path lengths (800 to a folder), one size after another, each tool run as its own process:

| Files | `create` peak | `verify` peak | `restore` peak | `create` wall |
|---|---|---|---|---|
| 29,000 | 67 MB | 49 MB | 51 MB | 4 s |
| 59,000 | 115 MB | 82 MB | 85 MB | 9 s |
| 118,000 | 211 MB | 150 MB | 154 MB | 22 s |
| 236,000 | 409 MB | 240 MB | 240 MB | 14 s |
| 472,000 | 799 MB | 463 MB | 463 MB | 34 s |

Each doubling of the file count multiplied the `create` peak by 1.7, 1.8, 1.9 and 1.95: close to linear above a fixed part of about 15 to 20 MB, with a slope of about 1.65 KB per file for `create` and 1.0 KB for `verify` and `restore`. The real two-season folder (117,707 files) measured 202 MB for `create`, within 5% of the synthetic figure for the same file count (211 MB), which supports using the synthetic series for this tool, but it is one data point. The wall times are **not** a scaling law: they vary with what the page cache held (the 236,000-file `create` was faster than the 118,000-file one) and with the disk.

**Estimates for four real seasons (not measured; each depends on an assumption).** Memory follows the file count. If a season keeps adding about 58,900 public files (the figure from two seasons only; it depends on the number of games and the length of the live recordings), four seasons are about 235,000 files, and the series above gives a `create` peak of about 400 MB and about 240 MB for `verify` and `restore`. If the count were 50% higher the peak would be about 600 MB. Time is not extrapolated from this host: the idle two-season figures (53 s `create`, 82 s `restore` for 117,707 files) are the only real ones, they scale with file count and disk speed, and they should be re-measured on the Droplet's own disk, with a cold cache, before anyone plans a backup window.

## 8. Limitations

* **Four real seasons are not available locally.** Only two real seasons exist on this host (the scripted comparison tree, built by the real builder and daemon). Production's four seasons live on the Droplet and were not touched. Everything said about four seasons is an estimate and is labelled as one (section 7).
* **Resuming is proven on a synthetic two-season store (always run) and on one real-engine hand-over (opt-in, needs a finished first season; one seed, one tick pattern, one 300 s downtime).** A restore on a different host, with the real web server in front, and a restore of a set taken in the middle of a real week of play (rather than across the hand-over) were not rehearsed.
* **The builder exclusion works among the tools that take the lock** (every changing `broadcast_build.py` command and `recovery_set.py create`) and the league runner's own lock. A program that changes the league or the store without either (for example a hand-run `sqlite3` session, or a runner started with a different control directory) is not prevented; the event-count comparison before and after the copy would detect a change of the league or the builds but not other edits. The lock is not inherited by the league runner that the builder starts, so a builder killed while its runner is still working could leave the runner going with the builder lock free (the runner's own lock then refuses a set).
* Hard links, modification times and owners are not preserved (section 2).
* `quiescent: false` backups exist by design when publication never pauses; the closure guarantees the backup is usable and that no index names a missing file, but it is an assembled snapshot, not one instant. A recovery set requires its store component to be quiet.
* There is no way to take a consistent set while the builder is mid-event: the set waits for the builder to finish (it is refused until then), by design.
* The tool verifies content and references; it does not check that a web server serves the restored folder with the right headers (tested over a plain `http.server` only), and it does not restore host configuration.
* Backup, verify and restore are single-threaded Python and read every file; they are I/O-bound (section 7). Time figures come from one host and one cache state.
* No scheduling, rotation, off-host transfer, encryption or retention of backups was built. Where backups are kept and for how long is the owner's decision. Quarantined `.partial` folders accumulate until the owner removes them.
