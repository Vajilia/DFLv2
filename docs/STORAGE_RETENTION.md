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
| R2 | **Private cache after hand-over:** for a season listed in `carry.json`, the private `store/games/<gid>/`, `store/playback/games/*` and its intermediate private releases (not the season's last) may be removed once each has a **public copy of the same size** (to be upgraded to byte-equality before any action) | `cache_after_handover` | ~0.9 GB private | the daemon no longer reads them after the hand-over (the carry record keeps what it needs); they are re-made byte for byte by `export_playback.py` and `broadcast_build.py` from the archive (rebuild determinism was verified on 2026-11-05: a rebuilt season equals the original except three fields that legitimately differ) | a verified backup pair (league + archive) taken after the season closed; the builder not running on that season; **scope the exporter to the season being built** so the next export does not recreate the deleted private files |
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

Before the hand-over the same inventory of the finished season-1 root lists **0 MB removable** (nothing is behind the clock yet), which is the point of the condition in R2. Several synthetic seasons were exercised by `tests/test_storage_inventory.py` (a small synthetic store with three seasons): only seasons behind the carry record appear as candidates and no path of the season on air ever does.

## 4. What must be preserved (checked by the inventory's classes and tests)

* **Permanent game history:** the archive and league file are `permanent` and appear in no candidate list.
* **Published replay URLs:** every `playback/games` file is `permanent` (the site links them; they are immutable, one year of cache). R3 keeps the URL and the bytes.
* **Recovery:** the daemon needs, after a hand-over, only the season on air (plan, builds, its games, releases) and `carry.json`; the league needs its own file and archive. R2 never touches either; its candidates are all re-derivable.

## 5. Not decided here

Whether to keep old live recordings (R4), the value of K, whether private caches should be rebuilt on demand or kept for a fixed number of seasons, and when a rollback copy may be dropped, are owner decisions. The implementation order is in `docs/COMPLETION_ROADMAP_2026-11-06.md`.
