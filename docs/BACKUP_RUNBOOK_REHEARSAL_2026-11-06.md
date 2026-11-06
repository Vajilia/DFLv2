# Backup runbook rehearsal, 2026-11-06

Executes `docs/BACKUP_RUNBOOK.md` end to end on an isolated local scratch copy with `python3 tools/backup_runbook_rehearsal.py --work /tmp/rbr` (committed, rerunnable; needs root for the two small loop-mounted ext4 disks and the tmpfs). **Exit code 0; 34 of 34 expectations held.** Scratch league only (seed 4242, 13 events, 24 games, public folder written by the real daemon code in virtual time). No Droplet, no production directory, no network, no rehearsal contact.

## Summary

| Runbook part | What was run | Result |
|---|---|---|
| Preflight | `backup_space.py create`, and the set preflight inside `recovery_set.py create` | passed with room; refused (exit 1) on tmpfs and on a 47 MB real disk, **nothing written either time** |
| Backup | `recovery_set.py create` (4.2 s, 79 MB) | exit 0 |
| Verification | `recovery_set.py verify --deep` | exit 0; unfinished `.partial` never verifies (exit 1) |
| Insufficient space | RAM-backed refusal, then too-small real disk | both exit 1 with the numbers; target folders stayed empty |
| Disk full while copying | `create --no-preflight` into the small disk | exit 1, "the disk is full ... nothing was committed ... already valid backups were not touched"; `set.partial` kept, no `set`; earlier `set1` byte-identical afterwards (content digest of every file) and still verifies |
| Quarantine | `recovery_set.py quarantine set.partial` | exit 0; a rename plus `QUARANTINE_NOTE.json`; **every evidence file has the same size and content hash as before the move** (2,922 files, 33,666,012 bytes); the name is free again; a new preflight on that disk reports the quarantine as kept evidence with 0 attempts that fit |
| Retry | `create` on a second real disk (237 MB), preflight on, then `verify --deep` | both exit 0 |
| Restore | originals moved aside, `recovery_set.py restore` (8.0 s) | exit 0; builder `status` shows 13 events and 24 games; league digest equal to the original; `public_backup.py check` clean; **public folder (997 files, 12,048,735 bytes) and private store (4,142 files) byte-identical to the source**; the restored builder fires event 14 and reaches the same league digest as the original's builder |

Estimate against reality (one data point, tiny): the set preflight estimated 96,588,804 bytes before margins; the finished set occupies 95,232,000 allocated bytes (82,936,157 bytes of content). The estimate was 1.4 % above the allocation. The working index (removed before the commit) was not sampled at its peak here; its estimate and the measured temporary disk at scale are in `docs/PUBLIC_BACKUP.md` sections 3b and 7.

## Findings

1. **The full-disk message points at the wrong folder for a set.** The error ends "move the unfinished folder aside with `public_backup.py quarantine .../set.partial/store.partial`". Quarantining that inner folder would leave `set.partial` in place and keep blocking the name. The runbook step (quarantine the outer `SET.partial` with `recovery_set.py quarantine`) is the one that worked. **Fixed afterwards** (test first: `RecoverySetFullDiskMessage` in `tests/test_backup_space.py` failed on the old wording for both the public and the store part, passes now): the set-level wrapper `_component` in `tools/recovery_set.py` re-reports a full disk naming the outer `SET.partial` and `recovery_set.py quarantine`. The transcript below was produced **before** the fix and still shows the old wording; it was not re-run.
2. **Lock files stay behind** (`.set.lock` beside the set). Intended (they are never deleted; the lock is released when the process ends) and now stated in the runbook; they are not backups and are skipped by every check.
3. **My first run of the driver stopped on my own bug** (the digest path): not a defect in the backup tools. A second run passed; I then tightened two checks that were too weak (the quarantine evidence comparison had passed vacuously, and the restored builder step was not echoed) and ran again: the transcript below is that third run.

## Limits of this rehearsal

* 79 MB and 13 events, not a season (about 2 GB). Timing and size at season scale are in `docs/PUBLIC_BACKUP.md` section 7 (synthetic) and are not re-measured on real data.
* One filesystem type (ext4 on loop files) and tmpfs; no network filesystem, no hard links (R3), no concurrent daemon during the restore.
* Not run on the Droplet, its disk or a real production backup (not permitted).
* The step "point the daemon and builder at the restored folder and go live" is outside the runbook and was not rehearsed (only the builder and the daemon in virtual time on a restored folder, covered by `tests/test_recovery_set.py`).

## Transcript (actual commands and results)

> **Annotation added after commit `8d0e9b3` (the transcript itself is unchanged):** the transcript was recorded on commit `aa56f42` and is kept as the historical evidence. The full-disk error message in step 6 (it names the inner `set.partial/store.partial` and `public_backup.py quarantine`) is the wording that **predates `8d0e9b3`**, which changed it to name the outer `SET.partial` and `recovery_set.py quarantine`. This run was not repeated after the fix.
Run started 2026-11-06 14:46:04Z (UTC) by `tools/backup_runbook_rehearsal.py --work /tmp/rbr`. Commands are exactly as run; output longer than 1800 characters is cut here and kept in full in the scratch folder.

## 1. Build the scratch copy (a disposable league, never a production directory)

A fresh isolated league (seed 4242), 13 events built by the real builder, and a public folder published by the real daemon code in virtual time. Folder: `/tmp/rbr`.

```
$ /usr/bin/python3 /home/claude/dflv2-integration/tools/broadcast_build.py init --root /tmp/rbr/orig/root --seed 4242
{"root": "/tmp/rbr/orig/root", "genesis": "e00000-a90ffff86c", "events_in_plan": 47, "continuous": false}
[exit 0, expected 0, 2.4 s]
```

```
$ /usr/bin/python3 /home/claude/dflv2-integration/tools/broadcast_build.py step --root /tmp/rbr/orig/root --events 13
{"at": "2026-11-06T14:46:07Z", "games": 0, "honors": [], "key": "offseason_open", "release": "e00001-dc8a2c6752", "seconds": {"feed": 0.4, "games": 0.0, "honors": 0.0, "league": 0.6}, "seq": 1}
{"at": "2026-11-06T14:46:08Z", "games": 0, "honors": [], "key": "hiring_open", "release": "e00002-ff63317b64", "seconds": {"feed": 0.4, "games": 0.0, "honors": 0.0, "league": 0.6}, "seq": 2}
{"at": "2026-11-06T14:46:09Z", "games": 0, "honors": [], "key": "hiring_close", "release": "e00003-7b19c72f8d", "seconds": {"feed": 0.4, "games": 0.0, "honors": 0.0, "league": 0.6}, "seq": 3}
{"at": "2026-11-06T14:46:11Z", "games": 0, "honors": [], "key": "trading_open", "release": "e00004-05d9001dbc", "seconds": {"feed": 0.5, "games": 0.0, "honors": 0.0, "league": 1.7}, "seq": 4}
{"at": "2026-11-06T14:46:13Z", "games": 0, "honors": [], "key": "free_agency_open", "release": "e00005-79f4461165", "seconds": {"feed": 0.4, "games": 0.0, "honors": 0.0, "league": 1.0}, "seq": 5}
{"at": "2026-11-06T14:46:15Z", "games": 0, "honors": [], "key": "draft_round_1", "release": "e00006-d78512bd01", "seconds": {"feed": 0.5, "games": 0.0, "honors": 0.0, "league": 1.3}, "seq": 6}
{"at": "2026-11-06T14:46:16Z", "games": 0, "honors": [], "key": "draft_rounds_2_3", "release": "e00007-dcc83c351e", "seconds": {"feed": 0.5, "games": 0.1, "honors": 0.0, "league": 1.1}, "seq": 7}
{"at": "2026-11-06T14:46:18Z", "games": 0, "honors": [], "key": "draft_rounds_4_7", "release": "e00008-409a31a8e5", "seconds": {"feed": 0.5, "games": 0.0, "honors": 0.0, "league": 1.1}, "seq": 8}
{"at": "2026-11-06T14:46:19Z", "games": 0, "honors": [], "key": "camp_open", "release": "e00009-13abb4dcaa", "seconds": {"feed": 0.5, "games": 0.1, "honors": 0.0, "league": 0.8}, "seq": 9}
{"at": "2026-11-06T14:46:22Z", "games": 0, "honors": [], "key
[... 705 more characters; full output in logs/02.txt of the scratch folder]
[exit 0, expected 0, 64.9 s]
```

**check - the daemon published into the scratch public folder: PASS** latest.json: e00011-c3debfb0b3

```
$ /usr/bin/python3 /home/claude/dflv2-integration/tools/broadcast_build.py status --root /tmp/rbr/orig/root
{"season": 1, "events_built": 13, "games_built": 24, "honors": []}
[exit 0, expected 0, 0.1 s]
```

League digest before the backup: `{"deep": "0a918eb715ba5217b2ef69f3d1957ae3bed481ad83ed08decdab94c5518b8703", "events": 13, "journal": "0a918eb715ba5217b2ef69f3d1957ae3bed481ad83ed08decdab94c5518b8703"}`

Source public folder: 997 files, 12048735 bytes. Source private store: 4142 files, 55025266 bytes. Allocated size of the three parts: public 14200832, store 65155072.


## 2. Preflight (an estimate, not a reservation)

```
$ /usr/bin/python3 /home/claude/dflv2-integration/tools/backup_space.py create --src /tmp/rbr/orig/public --out /tmp/rbr/disk/public-only
{"estimate": {"data": 14327808, "files": 997, "index": 253396, "manifest": 168572}, "estimate_only": "an estimate, not a reservation: other writers can still fill the disk; the tools handle a full disk safely", "filesystems": [{"attempts_that_fit": 403, "estimated_bytes": 14749776, "free_bytes": 9951510528, "fstype": "ext4", "items": [{"bytes": 14327808, "path": "/tmp/rbr/disk/public-only", "what": "of backed-up files"}, {"bytes": 168572, "path": "/tmp/rbr/disk/public-only", "what": "of manifest"}, {"bytes": 253396, "path": "/tmp/rbr/disk/public-only", "what": "of working index inside public-only.partial (removed before the commit)"}], "kept_evidence": [], "kept_evidence_bytes": 0, "mount": "/", "required_bytes_with_margin": 24651370}], "ok": true, "operation": "create", "problems": [], "warnings": []}
[exit 0, expected 0, 0.0 s]
```


## 3. Create the recovery set (preflight runs inside `create`)

```
$ /usr/bin/python3 /home/claude/dflv2-integration/tools/recovery_set.py create --root /tmp/rbr/orig/root --public /tmp/rbr/orig/public --out /tmp/rbr/disk/set1 --note 'runbook rehearsal'
{"league": {"dir": "league", "events": 13, "journal_head": {"chain": "8490801c1ef3ebbd1a5fe0f3b624b99f65da126fa4473ce29fe0e468a9d8e2af", "key": "tour_round_1", "seq": 13}, "manifest_sha256": "9689b3d71b91bff267a23efe8dda249d75cdff787b519f11a17ef5b61688175e"}, "ok": true, "public": {"dir": "public", "highest_public_season": 1, "manifest_digest": "746a43f30a2a40377ec76a53143145469d3d68e8235697c8eb85267f82040d01", "public_events": 11, "quiescent": true}, "seconds": 4.5, "set": "/tmp/rbr/disk/set1", "store": {"built_events": 13, "dir": "store", "manifest_digest": "12ca9193ebb8c240dc793ad00a62deb406fe5fae8c355b308b7799a32cd163bc", "quiescent": true, "season_on_air": 1}}
[exit 0, expected 0, 4.6 s]
```

Size of set1: 82936157 bytes (79 MB), allocated on disk 95232000 bytes; the preflight estimated 96,588,804 bytes before margins (see the estimate printed by the refusals in step 5).


## 4. Verify (deep: every file re-hashed, reference closure, the set rules S1-S4)

```
$ /usr/bin/python3 /home/claude/dflv2-integration/tools/recovery_set.py verify /tmp/rbr/disk/set1 --deep
{"components": {"league": {"dir": "league", "events": 13, "journal_head": {"chain": "8490801c1ef3ebbd1a5fe0f3b624b99f65da126fa4473ce29fe0e468a9d8e2af", "key": "tour_round_1", "seq": 13}, "manifest_sha256": "9689b3d71b91bff267a23efe8dda249d75cdff787b519f11a17ef5b61688175e"}, "public": {"dir": "public", "highest_public_season": 1, "manifest_digest": "746a43f30a2a40377ec76a53143145469d3d68e8235697c8eb85267f82040d01", "public_events": 11, "quiescent": true}, "store": {"built_events": 13, "dir": "store", "manifest_digest": "12ca9193ebb8c240dc793ad00a62deb406fe5fae8c355b308b7799a32cd163bc", "quiescent": true, "season_on_air": 1}}, "ok": true, "problems": [], "set": "/tmp/rbr/disk/set1"}
[exit 0, expected 0, 1.8 s]
```

**check - creating and verifying changed neither the league, the store nor the public folder: PASS** league digest and store digest equal to step 1 (the daemon was not running; the public folder is compared below after restore)


## 5. Refusals: RAM-backed storage and too little space (nothing is written)

A tmpfs (RAM-backed) large enough for the set, mounted at `/tmp/rbr/ram`.

```
$ /usr/bin/python3 /home/claude/dflv2-integration/tools/recovery_set.py create --root /tmp/rbr/orig/root --public /tmp/rbr/orig/public --out /tmp/rbr/ram/set
{"estimate": {"league_bytes": 15020032, "public": {"data": 14327808, "files": 997, "index": 253396, "manifest": 168572}, "store": {"data": 65396736, "files": 4142, "index": 1001128, "manifest": 674528}}, "estimate_only": "an estimate, not a reservation: other writers can still fill the disk; the tools handle a full disk safely", "filesystems": [{"attempts_that_fit": 4, "estimated_bytes": 96588804, "free_bytes": 564727808, "fstype": "tmpfs", "items": [{"bytes": 94744576, "path": "/tmp/rbr/ram/set", "what": "of backed-up files (public, store, league)"}, {"bytes": 843100, "path": "/tmp/rbr/ram/set", "what": "of manifests"}, {"bytes": 1001128, "path": "/tmp/rbr/ram/set", "what": "of the largest working index"}], "kept_evidence": [], "kept_evidence_bytes": 0, "mount": "/tmp/rbr/ram", "required_bytes_with_margin": 114786461}], "ok": false, "operation": "recovery set create", "problems": ["/tmp/rbr/ram/set is on tmpfs, which is memory (RAM), not disk: 90.4 MB of backed-up files (public, store, league); 823.3 KB of manifests; 977.7 KB of the largest working index would be written there. A backup in RAM is lost at the next reboot, and a working index in RAM gives back the memory the index exists to save. Choose a folder on a real disk (--scratch DIR for the index), or pass --allow-ram if you really mean it."], "warnings": []}
[exit 1, expected 1, 0.1 s]
```

**check - nothing was written to the tmpfs: PASS** listing: []

A small real disk (ext4 on a loop file, not RAM): 47 MB, mounted at `/tmp/rbr/small`; the set needs about 79 MB.

```
$ df -h /tmp/rbr/small
Filesystem      Size  Used Avail Use% Mounted on
/dev/loop0       41M   24K   37M   1% /tmp/rbr/small
[exit 0, expected 0, 0.0 s]
```

```
$ /usr/bin/python3 /home/claude/dflv2-integration/tools/recovery_set.py create --root /tmp/rbr/orig/root --public /tmp/rbr/orig/public --out /tmp/rbr/small/set
{"estimate": {"league_bytes": 15020032, "public": {"data": 14327808, "files": 997, "index": 253396, "manifest": 168572}, "store": {"data": 65396736, "files": 4142, "index": 1001128, "manifest": 674528}}, "estimate_only": "an estimate, not a reservation: other writers can still fill the disk; the tools handle a full disk safely", "filesystems": [{"attempts_that_fit": 0, "estimated_bytes": 96588804, "free_bytes": 38502400, "fstype": "ext4", "items": [{"bytes": 94744576, "path": "/tmp/rbr/small/set", "what": "of backed-up files (public, store, league)"}, {"bytes": 843100, "path": "/tmp/rbr/small/set", "what": "of manifests"}, {"bytes": 1001128, "path": "/tmp/rbr/small/set", "what": "of the largest working index"}], "kept_evidence": [], "kept_evidence_bytes": 0, "mount": "/tmp/rbr/small", "required_bytes_with_margin": 114786461}], "ok": false, "operation": "recovery set create", "problems": ["not enough free space on the filesystem of /tmp/rbr/small/set (/tmp/rbr/small): about 109.5 MB needed including a safety margin (90.4 MB of backed-up files (public, store, league); 823.3 KB of manifests; 977.7 KB of the largest working index), 36.7 MB free. Nothing was written."], "warnings": []}
[exit 1, expected 1, 0.1 s]
```

**check - nothing was written to the small disk by the refusal: PASS** listing: []


## 6. The disk fills during copying (preflight is an estimate: here it is bypassed on purpose)

`--no-preflight` stands for 'the estimate was right when made, then something else used the space'. The valid set `set1` on the other disk must stay valid; the unfinished set stays as evidence.

```
$ /usr/bin/python3 /home/claude/dflv2-integration/tools/recovery_set.py create --root /tmp/rbr/orig/root --public /tmp/rbr/orig/public --out /tmp/rbr/small/set --no-preflight
{"ok": false, "problems": ["the disk is full while writing /tmp/rbr/small/set.partial/store ([Errno 28] No space left on device): nothing was committed. /tmp/rbr/small/set.partial/store.partial is kept as evidence (what was written so far, its working index included), already valid backups were not touched, and nothing was deleted. Free space or choose another destination, move the unfinished folder aside with `public_backup.py quarantine /tmp/rbr/small/set.partial/store.partial`, and retry."]}
[exit 1, expected 1, 3.0 s]
```

**check - the unfinished set is kept as evidence and no set was published: PASS** listing: ['.set.lock', 'set.partial']

Evidence before quarantine: 2922 files, 33666012 bytes, digest `76ddbc7de4960996`.

```
$ /usr/bin/python3 /home/claude/dflv2-integration/tools/recovery_set.py verify /tmp/rbr/small/set.partial
{"ok": false, "problems": ["/tmp/rbr/small/set.partial is an unfinished recovery set: incomplete, not valid"]}
[exit 1, expected 1, 0.0 s]
```

```
$ /usr/bin/python3 /home/claude/dflv2-integration/tools/recovery_set.py verify /tmp/rbr/disk/set1 --deep
{"components": {"league": {"dir": "league", "events": 13, "journal_head": {"chain": "8490801c1ef3ebbd1a5fe0f3b624b99f65da126fa4473ce29fe0e468a9d8e2af", "key": "tour_round_1", "seq": 13}, "manifest_sha256": "9689b3d71b91bff267a23efe8dda249d75cdff787b519f11a17ef5b61688175e"}, "public": {"dir": "public", "highest_public_season": 1, "manifest_digest": "746a43f30a2a40377ec76a53143145469d3d68e8235697c8eb85267f82040d01", "public_events": 11, "quiescent": true}, "store": {"built_events": 13, "dir": "store", "manifest_digest": "12ca9193ebb8c240dc793ad00a62deb406fe5fae8c355b308b7799a32cd163bc", "quiescent": true, "season_on_air": 1}}, "ok": true, "problems": [], "set": "/tmp/rbr/disk/set1"}
[exit 0, expected 0, 1.8 s]
```

**check - set1 is byte-identical to before the failed attempt: PASS** digest d9c06613b990d079


## 7. Quarantine the unfinished set (a rename; nothing is deleted)

```
$ /usr/bin/python3 /home/claude/dflv2-integration/tools/recovery_set.py quarantine /tmp/rbr/small/set.partial
{"bytes": 33666012, "files": 2922, "note_written": true, "ok": true, "quarantined": "/tmp/rbr/small/partial-quarantine/20261106T144726Z-set.partial"}
[exit 0, expected 0, 0.1 s]
```

**check - the name is free (set.partial is gone) and exactly one quarantine folder exists: PASS** quarantined: /tmp/rbr/small/partial-quarantine/20261106T144726Z-set.partial

```
$ ls -la /tmp/rbr/small/partial-quarantine/20261106T144726Z-set.partial
total 20
drwxr-xr-x 4 root root 4096 Nov  6 06:47 .
drwxr-xr-x 3 root root 4096 Nov  6 06:47 ..
-rw-r--r-- 1 root root  247 Nov  6 06:47 QUARANTINE_NOTE.json
drwxr-xr-x 3 root root 4096 Nov  6 06:47 public
drwxr-xr-x 3 root root 4096 Nov  6 06:47 store.partial
[exit 0, expected 0, 0.0 s]
```

**check - the evidence is the same bytes: same files, sizes and content hashes as before the move (the note file excluded): PASS** before {'files': 2922, 'bytes': 33666012, 'sha256': '76ddbc7de4960996d9be4eca36456968ede4e485ac86a396c706624e961638ed'}, after {'files': 2922, 'bytes': 33666012, 'sha256': '76ddbc7de4960996d9be4eca36456968ede4e485ac86a396c706624e961638ed'}

```
$ /usr/bin/python3 /home/claude/dflv2-integration/tools/recovery_set.py create --root /tmp/rbr/orig/root --public /tmp/rbr/orig/public --out /tmp/rbr/small/set
{"estimate": {"league_bytes": 15020032, "public": {"data": 14327808, "files": 997, "index": 253396, "manifest": 168572}, "store": {"data": 65396736, "files": 4142, "index": 1001128, "manifest": 674528}}, "estimate_only": "an estimate, not a reservation: other writers can still fill the disk; the tools handle a full disk safely", "filesystems": [{"attempts_that_fit": 0, "estimated_bytes": 96588804, "free_bytes": 0, "fstype": "ext4", "items": [{"bytes": 94744576, "path": "/tmp/rbr/small/set", "what": "of backed-up files (public, store, league)"}, {"bytes": 843100, "path": "/tmp/rbr/small/set", "what": "of manifests"}, {"bytes": 1001128, "path": "/tmp/rbr/small/set", "what": "of the largest working index"}], "kept_evidence": [{"bytes": 40300544, "files": 2923, "kind": "quarantine folder", "path": "/tmp/rbr/small/partial-quarantine"}], "kept_evidence_bytes": 40300544, "mount": "/tmp/rbr/small", "required_bytes_with_margin": 114786461}], "ok": false, "operation": "recovery set create", "problems": ["not enough free space on the filesystem of /tmp/rbr/small/set (/tmp/rbr/small): about 109.5 MB needed including a safety margin (90.4 MB of backed-up files (public, store, league); 823.3 KB of manifests; 977.7 KB of the largest working index), 0 bytes free; 38.4 MB of it is already held by unfinished folders that are kept as evidence and are never counted as free. Nothing was written."], "warnings": ["kept on disk: /tmp/rbr/small/partial-quarantine (quarantine folder, 38.4 MB, 2923 files)"]}
[exit 1, expected 1, 0.1 s]
```


## 8. Retry on a disk with room (a second real ext4 disk, preflight on)

```
$ /usr/bin/python3 /home/claude/dflv2-integration/tools/recovery_set.py create --root /tmp/rbr/orig/root --public /tmp/rbr/orig/public --out /tmp/rbr/roomy/set --note 'retry after full disk'
{"league": {"dir": "league", "events": 13, "journal_head": {"chain": "8490801c1ef3ebbd1a5fe0f3b624b99f65da126fa4473ce29fe0e468a9d8e2af", "key": "tour_round_1", "seq": 13}, "manifest_sha256": "e1fd5e76da3793a1184d7f87ac039a01adcb332ef8cefd07aa897abc41d86185"}, "ok": true, "public": {"dir": "public", "highest_public_season": 1, "manifest_digest": "746a43f30a2a40377ec76a53143145469d3d68e8235697c8eb85267f82040d01", "public_events": 11, "quiescent": true}, "seconds": 6.6, "set": "/tmp/rbr/roomy/set", "store": {"built_events": 13, "dir": "store", "manifest_digest": "12ca9193ebb8c240dc793ad00a62deb406fe5fae8c355b308b7799a32cd163bc", "quiescent": true, "season_on_air": 1}}
[exit 0, expected 0, 6.7 s]
```

```
$ /usr/bin/python3 /home/claude/dflv2-integration/tools/recovery_set.py verify /tmp/rbr/roomy/set --deep
{"components": {"league": {"dir": "league", "events": 13, "journal_head": {"chain": "8490801c1ef3ebbd1a5fe0f3b624b99f65da126fa4473ce29fe0e468a9d8e2af", "key": "tour_round_1", "seq": 13}, "manifest_sha256": "e1fd5e76da3793a1184d7f87ac039a01adcb332ef8cefd07aa897abc41d86185"}, "public": {"dir": "public", "highest_public_season": 1, "manifest_digest": "746a43f30a2a40377ec76a53143145469d3d68e8235697c8eb85267f82040d01", "public_events": 11, "quiescent": true}, "store": {"built_events": 13, "dir": "store", "manifest_digest": "12ca9193ebb8c240dc793ad00a62deb406fe5fae8c355b308b7799a32cd163bc", "quiescent": true, "season_on_air": 1}}, "ok": true, "problems": [], "set": "/tmp/rbr/roomy/set"}
[exit 0, expected 0, 1.9 s]
```


## 9. Restore into a new folder with the originals gone

The original root and public folder were moved aside (not deleted) to `/tmp/rbr/orig-moved-aside`, as if the host were lost.

```
$ /usr/bin/python3 /home/claude/dflv2-integration/tools/recovery_set.py restore /tmp/rbr/roomy/set /tmp/rbr/restored
{"dest": "/tmp/rbr/restored", "league": "/tmp/rbr/restored/league/league.db", "ok": true, "public": "/tmp/rbr/restored/public", "root": "/tmp/rbr/restored"}
[exit 0, expected 0, 8.7 s]
```


## 10. Checks on the restored copy

```
$ /usr/bin/python3 /home/claude/dflv2-integration/tools/broadcast_build.py status --root /tmp/rbr/restored
{"season": 1, "events_built": 13, "games_built": 24, "honors": []}
[exit 0, expected 0, 0.1 s]
```

League digest after restore: `{"deep": "0a918eb715ba5217b2ef69f3d1957ae3bed481ad83ed08decdab94c5518b8703", "events": 13, "journal": "0a918eb715ba5217b2ef69f3d1957ae3bed481ad83ed08decdab94c5518b8703"}`

**check - league digest equals the original's: PASS** 

```
$ /usr/bin/python3 /home/claude/dflv2-integration/tools/public_backup.py check --src /tmp/rbr/restored/public
{"n_problems": 0, "ok": true, "problems": []}
[exit 0, expected 0, 0.1 s]
```

**check - restored public folder is byte-identical to the source: PASS** 997 files, 12048735 bytes, digest 66a1bc520004967b

**check - restored private store is byte-identical to the source: PASS** 

```
$ /usr/bin/python3 /home/claude/dflv2-integration/tools/broadcast_build.py step --root /tmp/rbr/restored --events 1
{"at": "2026-11-06T14:48:22Z", "games": 20, "honors": [], "key": "week_2", "release": "e00014-d97f56ee5c", "seconds": {"feed": 0.5, "games": 3.6, "honors": 0.0, "league": 32.9}, "seq": 14}
[exit 0, expected 0, 37.1 s]
```

```
$ /usr/bin/python3 /home/claude/dflv2-integration/tools/broadcast_build.py step --root /tmp/rbr/orig-moved-aside/root --events 1
{"at": "2026-11-06T14:49:00Z", "games": 20, "honors": [], "key": "week_2", "release": "e00014-d97f56ee5c", "seconds": {"feed": 0.5, "games": 3.6, "honors": 0.0, "league": 33.9}, "seq": 14}
[exit 0, expected 0, 38.0 s]
```

**check - the restored builder carried on: event 14 gives the same league digest as the original: PASS** events 14


## Result

| step | result | |
|---|---|---|
| init scratch league | exit 0 (expected 0) | PASS |
| build 13 events | exit 0 (expected 0) | PASS |
| the daemon published into the scratch public folder | latest.json: e00011-c3debfb0b3 | PASS |
| builder status (before) | exit 0 (expected 0) | PASS |
| preflight, public folder alone | exit 0 (expected 0) | PASS |
| create set1 | exit 0 (expected 0) | PASS |
| verify set1 --deep | exit 0 (expected 0) | PASS |
| creating and verifying changed neither the league, the store nor the public folder | league digest and store digest equal to step 1 (the daemon was not running; the public folder is compared below after re | PASS |
| create on tmpfs refused by default | exit 1 (expected 1) | PASS |
| nothing was written to the tmpfs | listing: [] | PASS |
| df small disk | exit 0 (expected 0) | PASS |
| create on the small disk refused (insufficient space) | exit 1 (expected 1) | PASS |
| nothing was written to the small disk by the refusal | listing: [] | PASS |
| create runs out of disk | exit 1 (expected 1) | PASS |
| the unfinished set is kept as evidence and no set was published | listing: ['.set.lock', 'set.partial'] | PASS |
| an unfinished set never verifies | exit 1 (expected 1) | PASS |
| the earlier valid set1 still verifies | exit 0 (expected 0) | PASS |
| set1 is byte-identical to before the failed attempt | digest d9c06613b990d079 | PASS |
| quarantine set.partial | exit 0 (expected 0) | PASS |
| the name is free (set.partial is gone) and exactly one quarantine folder exists | quarantined: /tmp/rbr/small/partial-quarantine/20261106T144726Z-set.partial | PASS |
| list the quarantined evidence | exit 0 (expected 0) | PASS |
| the evidence is the same bytes: same files, sizes and content hashes as before the move (the note file excluded) | before {'files': 2922, 'bytes': 33666012, 'sha256': '76ddbc7de4960996d9be4eca36456968ede4e485ac86a396c706624e961638ed'}, | PASS |
| the name is usable again, but the preflight still refuses the small disk (quarantined evidence is not counted as free) | exit 1 (expected 1) | PASS |
| retry create on the roomy disk | exit 0 (expected 0) | PASS |
| verify the retried set --deep | exit 0 (expected 0) | PASS |
| restore the set | exit 0 (expected 0) | PASS |
| builder status on the restored copy | exit 0 (expected 0) | PASS |
| league digest equals the original's |  | PASS |
| public_backup.py check (reference closure) on the restored public folder | exit 0 (expected 0) | PASS |
| restored public folder is byte-identical to the source | 997 files, 12048735 bytes, digest 66a1bc520004967b | PASS |
| restored private store is byte-identical to the source |  | PASS |
| the restored builder builds event 14 | exit 0 (expected 0) | PASS |
| control: the original builds event 14 | exit 0 (expected 0) | PASS |
| the restored builder carried on: event 14 gives the same league digest as the original | events 14 | PASS |

**34 of 34 expectations held; 0 failed.**
