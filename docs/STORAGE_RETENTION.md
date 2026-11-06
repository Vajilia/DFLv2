# Storage retention for a recurring broadcast (design and dry-run only)

Branch `backend/multiseason-continuity-2026-11-06`. **Nothing is deleted, moved or rewritten by anything described here.** `tools/storage_inventory.py` is read-only (it has no code that removes or changes a file; a test asserts it) and every rule below is a proposal that needs the owner's go-ahead before any action is built. No automatic deletion exists or is proposed to run unattended.

## 1. What accumulates (measured)

One real season (400 games, 47 events, seed 20261105, disposable copy; `du`, MB):

| Where | Item | MB / season | Grows with seasons? | Notes |
|---|---|---|---|---|
| private | `league/league.db` | 9 to 11 | no (journal 1 row per event; **5 rolling snapshots**, not one per event) | recovery source |
| private | `league/league.db.archive` | 145 | yes, linear | the permanent game history |
| private | `store/feed/releases` + pointers | 44 to 56 | yes | 48 releases, ~0.9 MB each |
| private | `store/playback/games` | 237 to 251 | yes | full replays; the exporter's copy |
| private | `store/games/<gid>/` | 615 to 652 | yes | live recordings (~1.5 MB a game) **plus another copy of the replay** |
| private | `store/honors`, plans, builds, carry | ~1 | slightly | |
| public | `playback/games` | 251 | yes | published replay URLs, immutable |
| public | `broadcast/replays` | 251 | yes | **the same bytes** as `playback/games` (a second copy) |
| public | `broadcast/live/<gid>/` | 403 | yes | head, 5-second segments, end |
| public | `releases/` | 49 | yes | site history (standings, rosters, ...) |
| public | `broadcast/honors` | 1 | slightly | |

Roughly **1.1 GB private and 0.95 GB public per season, about 2.0 GB per season in all**, linear. With the Droplet at 47 GB and about 44 % used on 2026-11-06, that is a dozen seasons before the disk is full, before the 6 GB floor the rehearsal guard uses; the daemon's `--max-public-gb` cap (3 GB in the rehearsal) must become a per-season policy (about 1 GB a season published). *Checkpoints* in the sense of league snapshots do not accumulate (5 rows); the growth is the archive and the broadcast's derived copies. Per-event build time with two seasons in the archive showed no growth (median 2.9 s with games in season 1, 3.2 s for a 20-game event in season 2); ten-season behaviour is not measured.

Operator-owned items outside the broadcast, listed so nobody mistakes them for broadcast storage: rollback copies (`/root/dfl-rollback/*`, nine on 2026-11-06), `dfl-backup-*.tar`, evidence folders, the rehearsal quarantine, the 249 MB rehearsal package and its unpacked copy, rebuilt stores (`/srv/dflv2-rehearsal-build`). The inventory does not scan them.

## 2. Proposed rules (each needs a decision before it is built)

| # | Rule | Class | Reclaims / season | Safe because | Needs |
|---|---|---|---|---|---|
| R1 | **Never remove:** league file, archive, WAL, plans, builds, `carry.json`, Honors parts, every public release, every `playback/games` file, the cumulative indexes, `latest.json` | permanent | 0 | these are the game history, the published replay URLs (immutable, cached for a year) and the recovery source | nothing |
| R2 | **Private cache after hand-over (revised 2026-11-06):** for a season listed in `carry.json`, the private live-recording folders `store/games/<gid>/` and the intermediate private releases that have an identical public copy (not the season's last) may be moved to quarantine once each has a **verified public copy** (byte equality, to be added before any action). **Not** the private `store/playback/games/*` replay export: see section 5, it cannot be re-made from the archive | `cache_after_handover` | ~0.6 GB private (the live recordings, 615 to 652 MB of which the replay copy is 0.25 GB) plus 34 MB of releases | live recordings are re-made byte for byte from the replay file (25 of 25 sampled equal, 31 MB); the daemon never reads them after the hand-over (tested: the tree published afterwards is identical with them gone) | a verified backup pair (league + archive) taken after the season closed **and a verified backup of the public folder** (section 5); the builder not running on that season; **scope the exporter to the season being built** so the next export does not recreate the files |
| R3 | **One public copy of each replay:** make `broadcast/replays/<name>` a hard link to `playback/games/<name>` | `duplicate_public` | 0.25 GB public | same bytes, same URLs; nothing disappears | a small daemon change (link instead of copy); backups must preserve hard links (`tar` and `rsync -H` do) |
| R4 | **Old live recordings:** `broadcast/live/<gid>/` of a final game older than the newest *K* seasons (proposed K = 2) | `old_live` | 0.4 GB public | its replay stays; only the "watch it as it was broadcast" files go | **a frontend decision** (does a Watch link to an old final game need the live files? `test_site_broadcast` covers a final replay for the season on air only) |
| R5 | **Pruning the feed keep-list** (`export_public_feed --keep 200`): unchanged and not proposed to change; at 47 releases a season it covers four seasons of private releases | none | 0 | | nothing |
| R6 | Operator backups: keep the last three rollback copies plus one per season boundary, only after a restore test of the one that stays | operator | (not measured) | | the owner; never automatic |

Conditions every future removal action must meet (the inventory's classes already encode the first two): (1) the season is in `carry.json`; (2) the public copy exists and equals the private one; (3) a verified backup pair is newer than the season's close; (4) disk-floor and mid-broadcast checks pass; (5) a manifest of removed paths with hashes is written first and the action moves, not deletes, into a quarantine folder (as `deploy/rehearsal/quarantine_rehearsal.sh` does) and only a later, separate, explicit step purges the quarantine.

## 3. Dry-run inventory

`python tools/storage_inventory.py --root R [--public P] [--keep-live-seasons 2] [--list] [--json]` (the report is standard output; the program writes no file)

On the real two-season copy (season 1 handed over; season 2 built to event 60; the public folder after the virtual-time run), 2026-11-06:

| Class | MB | Files |
|---|---|---|
| `cache_after_handover` (private, season 1) | 777.6 | 58,820 |
| `duplicate_public` (replay twins) | 261.7 | 424 |
| `old_live` | 0 (only two seasons; K = 2) | 0 |
| `permanent` | 479 | 4,405 |
| `in_use` (season 2, season 1's recent public live recordings, unverified) | 318 | 63,331 |
| **total scanned** | **1,836** | |

That table was taken before the correction in section 5 (it counted the private replay export as a cache). **After the correction, on the two *complete* real seasons** (season 1 handed over, season 2 built to event 94 and published through the year close; league files not in this copy):

| Class | MB | Files |
|---|---|---|
| `cache_after_handover` | 530.9 | 58,420 |
| `duplicate_public` | 489.8 | 800 |
| `permanent` | 846.5 | 8,875 |
| `in_use` | 1,238.0 | 167,439 |
| **total scanned** | **3,105** | |

Before the hand-over the same inventory of the finished season-1 root lists **0 MB removable** (nothing is behind the clock yet), which is the point of the condition in R2. Several synthetic seasons were exercised by `tests/test_storage_inventory.py` (a small synthetic store with three seasons): only seasons behind the carry record appear as candidates and no path of the season on air ever does.

## 4. What must be preserved (checked by the inventory's classes and tests)

* **Permanent game history:** the archive and league file are `permanent` and appear in no candidate list.
* **Published replay URLs:** every `playback/games` file is `permanent` (the site links them; they are immutable, one year of cache). R3 keeps the URL and the bytes. The private export in `store/playback/games` is permanent too (section 5).
* **Recovery:** the daemon needs, after a hand-over, only the season on air (plan, builds, its games, releases) and `carry.json`; the league needs its own file and archive. R2 never touches either; its candidates are all re-derivable (live recordings) or have an identical public copy (releases).

## 5. What can be reconstructed, what must stay, what recovery depends on (read-only evidence, 2026-11-06)

All measured on scratch copies; nothing in any league, store or public folder of value was changed. Tools: `tools/storage_reconstruct_check.py` (writes only to a new scratch folder), `tests/test_recovery_dependencies.py`, `tools/league_backup.py`.

| Item | Can it be re-made? | Evidence |
|---|---|---|
| **Live recordings** `store/games/<gid>/` (head, segments, end, manifest, replay copy) | **Yes, byte for byte**, from the replay file with the daemon's own `store_game` | 25 of 25 games sampled across the season (31 MB): every file equal |
| **Replay files** `store/playback/games/*` = public `playback/games/*` = `broadcast/replays/*` | **No.** Re-running `export_playback.py` against the archive and the *final* feed gave 220 of 400 different files (other bytes, hence **other content-hashed names, i.e. other URLs**): the exporter takes rosters and names from the feed release it is given, and players who left a roster by the end of the year are missing (`player 1240` instead of a name in the play text). 180 were identical. | `storage_reconstruct_check.py` on the finished real season |
| **Private feed releases** (48 a season) | 41 of 48 are byte-identical to the public `releases/` copy; **7 are not public** (6 MB; why was not investigated) | same tool, `--public` |
| **League state at an earlier event** | Not from the final file (5 rolling snapshots only); by deterministic replay from the seed (about 35 minutes a season, digest-identical, section 3 of `BROADCAST_CONTINUITY.md`) | twin digests |
| **Public files of an earlier season** (live folders, releases, Honors, replays) | **Not by the daemon.** After the hand-over the daemon does not re-create them: with the public folder lost, 421 of about 700 synthetic files were missing afterwards, and the cumulative indexes still listed the earlier season with no file behind three of its entries (dangling links) | `tests/test_recovery_dependencies.py` |
| **After the hand-over, are the earlier season's private caches read?** | No | same file: moving all of them away changes nothing published afterwards |
| **League + archive** | Yes: `league_backup.py create` → `verify --deep` → `restore` on the event-94 league: **deep digest identical** to the original (`485b49f5…`, 94 events), about 4 minutes, 568 MB for the pair | run on the free-running twin |

**What must remain permanent** (so R1 stands, with a stronger reason): the league file and its archive; the **published replay files** (they cannot be re-made identically, and their names are the public URLs); every public release; the plans, builds, `carry.json` and Honors parts; and the **public folder itself** as the only home of the earlier seasons' live recordings, Honors files and indexes.

**What recovery depends on**
1. *League:* a verified backup pair (league + archive) is sufficient; or the seed plus the same code (deterministic replay) for any earlier state.
2. *Broadcast, season on air:* `plan.json`/`builds.json`, the season's `store/games`, releases and `carry.json`; a restarted daemon repairs its own public folder from these (tested at many boundaries).
3. *Broadcast, earlier seasons:* **only the public folder** (and a backup of it). This is the finding that changes the plan: R2 (and any future retention action) must be preceded by a verified backup of the public folder, because the daemon cannot repair it, and removing the private caches would then remove the only other source. The private replay export is a second source for the replay files; the live recordings could still be re-made from it.
4. *Not a dependency:* the engine code, `data/` or `site/` for replay of the broadcast.

Consequences for the plan: R2's size is about 0.65 GB private a season, not 0.9; a **public-folder backup (with hard links preserved, if R3 is adopted) becomes an essential milestone before any retention action**; and a `repair` tool that re-materialises an earlier season's public files from the private caches would be the alternative to backups (not built, and it would need the caches R2 proposes to remove).

## 6. Not decided here

Whether to keep old live recordings (R4), the value of K, whether private caches should be rebuilt on demand or kept for a fixed number of seasons, and when a rollback copy may be dropped, are owner decisions. The implementation order is in `docs/COMPLETION_ROADMAP_2026-11-06.md`.
