# Replay tools: export, verification, interface and deployment preparation

Branch `release/replay-tools-2026-11-03`, started from the deployed engine release `7e59cbbaea23b0de353545c64a6f5c39cffe774d`.
Status (2026-11-03): built, tested and run on archive copies. **Nothing is deployed, merged or installed.** Production, the league (stopped at event 188), the
daughter's demo and every backup are unchanged.

This file is the only documentation this branch changes. It is written for whoever integrates the website against the exporter's output (the frontend work) and
for whoever deploys it. The viewer and the page behaviour belong to the frontend branch and its own documents.

## 1. What is in the branch, and what is not

| File | Role |
|---|---|
| `src/dflsite/playback.py` | the format: builds a public game from an archive record, the privacy gate (`check_public`), canonical JSON, file and index writers, the game-id mapping (`feed_id_for`) |
| `tools/export_playback.py` | archive + published feed -> `playback/index.json` + `playback/games/*.json` |
| `tools/verify_playback_against_archive.py` | independent check of every game in the index against the archive |
| `tools/replay_coverage.py` | which archived games have a replay, why the others do not, and whether every game the published feed lists finds its replay under the same id, clubs and score |
| `tools/replay_public_scan.py` | scan of every downloadable file for anything outside the public-data rules |
| `deploy/website/Caddyfile.template` | the two cache rules for `/feed/playback/` (same text as on the experiment branch) |
| `deploy/website/dfl-playback-export.{service,timer}` | templates for a timer-driven exporter (not installed anywhere) |
| `tests/test_replay_tools.py`, `tests/fixtures/replay_tools_v1/` | 11 focused tests on a real archived game |

`playback.py`, `export_playback.py` and `verify_playback_against_archive.py` are byte-identical to `experiment/contract-memo-cache` at `0f72562`; the first
commit of this branch only moves them onto the deployed engine release. **No existing interface was changed.** The three tools after them are new files.

Not in the branch, on purpose: `site/`, the viewer, the frontend tests and `docs/PUBLIC_PLAYBACK_FORMAT.md` (frontend branch), `tools/build_replay_demo.py` and the
`preview_site.py` change (they need the viewer files; they stay on the experiment branch).

**The engine is untouched.** The code fingerprint covers `src/dfl/` and `data/` only (`persist.code_manifest`). On this branch it is
`efaa7167832713d771942b9504f72f25edb0793af6fa6d2fa2593a8792909973`, the deployed release's. A test pins it and checks that nothing under `src/dflsite/`, `tools/` or
`deploy/` is in the manifest.

## 2. The interface the website can rely on

### Where things are

```
<site root>/feed/playback/index.json                     the list of replayable games; never cached
<site root>/feed/playback/games/<gid-slug>.<sha10>.json  one game; the name carries the first 10 hex digits of the file's SHA-256, so a published file never changes
```

On the server `<site root>/feed` is `/srv/dflv2/feed`, so the folder is `/srv/dflv2/feed/playback/`. The feed publisher only prunes `feed/releases/*` and its own
temporary files (`src/dflsite/publish.py`: `prune`, `_sweep_stale`), so it never touches `feed/playback/`. A game's URL is `/feed/playback/` + the index entry's
`file` (which already starts with `games/`). The viewer reads the index first, then exactly the one file it needs.

### The index

`{"format": "dfl-public-playback", "format_version": 1, "kind": "index", "league_id": "<feed league id>", "games": [entry, ...]}`, entries sorted by `gid`, canonical JSON
(keys sorted, ASCII, no spaces). An entry:

| Field | Meaning |
|---|---|
| `id` | the public schedule's id for the game (below). **Not unique across seasons.** The key is `(season, id)` |
| `gid` | the archive's id, `S001/reg:w03:g07`; unique |
| `season` | 1-based season number (`S001` = 1) |
| `game_kind` | `regular`, `tour`, `playoff`, `bowl`, `coronation` |
| `file`, `bytes`, `sha256` | the game file, its size and SHA-256 (the first 10 digits are in the file name) |
| `away`, `home`, `away_score`, `home_score` | club abbreviations and the final score, copied from the game file |
| `plays`, `traced_plays` | number of plays and how many of them carry recorded movement |

A game file's contents (the keys the privacy gate allows, and nothing else): `format`, `format_version`, `kind: "game"`, `gid`, `id`, `season`, `game_kind`, `date`, `kickoff`,
`neutral`, `teams {away, home: {abbr, name, colors}}`, `final {away, home}`, `periods`, `tie`, `winner`, `roster {player id: {side, name?, number?, pos?}}`, `identity_note`,
`sampling`, `plays`, `traced_plays`, `field`. A play: `i`, `period`, `clock`, `off`, `dir`, `kind`, `text`, `score` (after the play), for snaps `down`, `dist`, `yl`, optionally
`first`, `pts`, and `trace` (an object or `null`). A trace: `dt`, `n`, `t_end`, `los`, `roles`, `ids`, `events`, `ball_from`, `pos` and `ball` (base64 of little-endian int16 in
hundredths of a yard, column-delta). The complete description of the encoding is in `docs/PUBLIC_PLAYBACK_FORMAT.md` on the frontend branch (it is the same text this
exporter was built against); `src/dflsite/playback.py` is the reference.

### The game-id mapping (checked against the published feed)

| Game | Archive id (`gid`) | Public id (`id`) | Example |
|---|---|---|---|
| Regular season | `S00N/reg:wWW:gGG` | `reg:wWW:gGG` | `reg:w01:g01` |
| Ambassador Tour | `S00N/tour:rR:gGG` | `tour:rR:gGG` | `tour:r1:g01` |
| Bowl | `S00N/bowl` | `bowl` | |
| Coronation | `S00N/coronation` | `coronation` | |
| Playoff | `S00N/po:<Conference>:<round>:s<away seed>v<home seed>` | `po:<Conference>:<round>:<k>` | `S001/po:Eastern:wild_card:s5v4` -> `po:Eastern:wild_card:2` |

Conferences are `Eastern` and `Western`; rounds are `wild_card`, `divisional`, `conference`. In `s5v4` the first number is the **away** club's seed and the second the **home** club's.
`k` counts that conference's games in that round and season by **home seed, lowest first**: wild card `s6v3` is `:1` and `s5v4` is `:2`; divisional `s4v1` is `:1` and `s3v2` is `:2`.

This was checked, not assumed: `tools/replay_coverage.py` compared all **428** finished games the live feed lists (the 40 playoff games of the four seasons in `history.json`
and season 4's 388 scheduled games) with the exported index. Every one has a replay with the same season, id, clubs and score; none is under another name. So an
index entry's `id` can be used directly to look a game up in the feed. The index also carries the archive id, so a page that has only the archive's name can match on `gid`.

### What the files promise, and what may change

Promised (changing any of it is a breaking change and raises `format_version`): the paths above; the index entry fields; that a published game file never changes and its
name carries its hash; that `id` is the feed's id; that `trace` is `null` exactly when the archive holds no trace row for the play; that no key outside the schema is ever
written (`check_public` in the exporter, a separate list in `replay_public_scan.py`, a third in the verifier). A new optional key is allowed only if it is added to all three lists
and documented in the same commit.

An index or game file with another `format_version` makes the exporter stop (it will not mix formats). A viewer that meets another version says so and shows nothing
(frontend behaviour, documented there).

### Missing data: what the files say

| Situation | What you get |
|---|---|
| A game whose archive record was committed after the published feed's event count | not exported, no entry, no file. It appears after the feed catches up (the timer reruns the exporter). `replay_coverage.py` calls it `unrevealed` |
| A game not from the spatial engine | not exported (the exporter skips it silently). **None exists today.** `replay_coverage.py` names it `not_spatial` |
| A play with no stored movement (kickoff, punt, kick, penalty, timeout, kneel, injury ...) | `"trace": null`. In the four seasons: 277,017 plays, 189,201 traced, 87,816 without a trace (32%) |
| The ball before it is placed | the archive's first ball sample of a play is (0, 0, 0); the file keeps it and `ball_from` says from which sample the ball was recorded |
| A player who has left the public rosters since the game | the roster entry holds only `side`; play text says `player 1234`. **16,676 of 72,016 roster entries have no name** (details below) |
| An injury | a play of kind `injury` with the fixed text "A player is injured and leaves the game.", no player, no trace |
| A club with no public team record | the exporter stops with an error and leaves the previous index in place. Cannot happen for the 48 clubs or the two All-Star sides |
| No index yet | the file does not exist, the web server answers 404 |

**Names of players who have left a roster.** The archive stores no names; the exporter takes names, numbers and positions from the published rosters of the live feed release at the
moment it runs. A player who has since retired or been released therefore appears as `player N`. Measured on the full export:

| Season | Games | Roster entries | Without a name | Games with at least one |
|---|---|---|---|---|
| 1 | 400 | 18,013 | 7,388 (41%) | 400 |
| 2 | 400 | 17,997 | 5,383 (29%) | 400 |
| 3 | 400 | 18,009 | 3,494 (19%) | 400 |
| 4 | 400 | 17,997 | 411 (2%) | 223 |

This is a limit of what is public today, not an exporter fault. Closing it would need a new public source of names for past players (a feed file the league does not publish
yet), which is a change to the feed and needs a privacy review. **Not proposed for implementation here**; it is the one item below that the owner may want to decide.

## 3. What was exported and verified (real data)

Inputs: a copy of the production archive (`archive.db`, SHA-256 `871a0b94926cde5cee87931d1f1e8ee168101c2cf3425ce6a0d90d6c3ffc0d24`, byte-identical to the one in the recovery set
`dfl-recovery-set-20261103`) and the live feed release `e00188-cf545fbf7b`. Output went to a scratch folder on the Droplet (`/root/replay-work/playback`, not under `/srv`, not served).
Tool files used were at commit `bc01921` (identical to every later commit for the exporter and verifier).

| Check | Result |
|---|---|
| Archive games / exported | 1,600 / 1,600: per season 360 regular, 28 tour, 10 playoff, 1 bowl, 1 coronation. 0 unrevealed, 0 not spatial, 0 missing |
| Export | 290.7 s at `nice 19` on the 1 vCPU Droplet (about 0.18 s per game), peak memory 37.5 MB |
| Size | 965,186,828 bytes of game files (average 603,241 B; 168,696 B gzipped; 269.9 MB gzipped in all) plus a small index. Index SHA-256 `6ee0a76ef0c48a439b1f2c094aeebf6e5fc489deec28c049a8c130d09516ef30` |
| Independent verifier (`verify_playback_against_archive.py`) | 1,600 games, **ALL CHECKS PASSED**, 0 problems: 277,017 plays, 189,201 traced plays (= the 189,201 trace rows in the archive), 6,429,367 samples, **302,180,249 recorded values compared** with the archive, 18,276 scoring plays, 72,016 players. About 3.5 minutes |
| Completeness (`replay_coverage.py --expect-complete`) | OK. 428 of 428 feed games found under the same id, clubs and score |
| Public-data scan (`replay_public_scan.py`) | 1,600 files, nothing outside the public rules. 1,638 injury plays all carry only the fixed sentence |

The public-data scan first reported 297 texts of the form "Penalty on PHI: illegal formation, 5 yards." because of the word *formation*. That is the penalty's name, not a team's formation,
so that exact phrase is allowed and a test checks that "shotgun formation" is still refused. Nothing else was reported.

Privacy, in one place: the exporter copies only what the format lists (section 2); it never reads ratings, attributes, play calls, formations, coverages, intents, box-score statistics,
injury details, contracts or anything private from the archive (`check_public` refuses any other key at any depth); it writes only games committed at or before the published feed's
event count; names, numbers and positions come only from the published rosters. The scan above reads the downloadable files themselves, so it also catches a file that something other than the exporter put there.

## 4. Running the tools

```
python3 -I tools/export_playback.py  --archive A --feed FEED --out PLAYBACK [--every 2] [--gid ...] [--limit N]      # exit 0; prints one JSON summary
python3 -I tools/verify_playback_against_archive.py --archive A --playback PLAYBACK [--gid ...] [--json]             # exit 0 only if every game passes
python3 -I tools/replay_coverage.py   --archive A --playback PLAYBACK --feed FEED [--expect-complete] [--json]       # exit 1 on any problem (and, with the flag, on any game without a replay)
python3 -I tools/replay_public_scan.py --playback PLAYBACK [--json]                                                  # exit 1 on anything outside the public rules
python3 -I -m unittest discover -s tests -p test_replay_tools.py                                                     # 11 tests, about 4 seconds
```

All four read the archive read-only and write only the playback folder (the exporter). The exporter skips a game already in the index, writes each game file before it replaces the
index (atomically), and a failure leaves the previous index in place; files written before a failure stay (they are immutable, harmless, and `replay_coverage.py` reports them as left over).
Run it twice and the second run writes nothing and changes no byte (tested).

The tests cover: export -> verify -> coverage on a real archived game and a repeat run; an unrevealed game and a non-spatial game are not exported and are named; a failed export keeps the
previous index; an index of another format is refused; the verifier fails when one recorded position is changed even after the file is renamed and its hash repaired; the completeness check finds
a missing entry, a missing file, a leftover file, an orphan entry, a duplicate id, and a feed game that has no replay under its id or whose score differs; the playoff numbering on real ids;
a player not on the roster is unnamed; the public-data scan passes a real export and fails on a private key, a private roster field, private words in text, non-base64 positions and a stray file;
the replay tools are outside the code fingerprint.

## 5. Deployment (prepared, not done; each step needs approval)

Principles: the exporter never runs from the engine's tree (`/srv/dflv2/repo`); it runs from its own pinned checkout under `/opt/dflv2-src/<40-char commit>` (the place
`deploy_pinned.sh` already uses), so nothing the league runner executes changes and the engine fingerprint cannot move. The league can stay stopped at 188 throughout. Every step is
additive and has its own undo. Before the first step, take `tools/cap_state.py fingerprint --league /srv/dflv2/league/league.db --feed /srv/dflv2/feed --out before.json` (and again after the last; `cap_state.py diff before.json after.json` must exit 0).

Facts about the server as found on 2026-11-03 (read-only look): two Caddy snippets are rendered from the template, `/etc/caddy/conf.d/dflv2-preview.caddy` (host `preview.diamondfootballleague.com`) and
`/etc/caddy/conf.d/dflv2-production.caddy` (`diamondfootballleague.com`, `www.`, and the `sslip.io` name). Each differs from this branch's template **only** by the playback lines. The user `dflv2` owns
`/srv/dflv2/feed` and `/srv/dflv2/league`; the production archive is `/srv/dflv2/league/league.db.archive`; the disk has about 34 GB free; the full export is about 0.93 GB (an estimated 0.25 GB more per season).

1. **Rollback copy.** `sh deploy/website/snapshot_config.sh` (copies Caddy and the `dfl-*` units; prints the snapshot folder; note it).
2. **Pinned checkout of the exporter.** `git -C /opt/dflv2-src/<commit> init` / `fetch --depth 1 https://github.com/Vajilia/DFLv2.git <commit>` / `checkout --detach FETCH_HEAD` as `deploy_pinned.sh` does for its commit, then
   `git rev-parse HEAD` must print `<commit>` and `git status --porcelain` must be empty. Do **not** run `install_staging.sh`. Undo: remove that one folder.
3. **Create the folder and make the first export by hand,** while watching: `install -d -o dflv2 -g dflv2 -m 755 /srv/dflv2/feed/playback`, then as the service user,
   `runuser -u dflv2 -- nice -n 19 python3 -I /opt/dflv2-src/<commit>/tools/export_playback.py --archive /srv/dflv2/league/league.db.archive --feed /srv/dflv2/feed --out /srv/dflv2/feed/playback` (about 5 minutes).
   Then the three checks against the production archive: `verify_playback_against_archive.py`, `replay_coverage.py --expect-complete` (with `--feed /srv/dflv2/feed`), `replay_public_scan.py`, all must pass. While the league is still at event 188 and the live feed is still `e00188-cf545fbf7b`, the index must be
   byte-identical to the one in section 3 (SHA-256 `6ee0a76e...ef30`); if it is not, stop and find out why before going on. File modes must be 644 and folders 755 (Caddy reads them).
   Nothing is public yet: with the old Caddy rules the files would be served, but no page links to them until the site release in step 6. Undo: `mv /srv/dflv2/feed/playback /srv/dflv2/playback-removed-<UTC time>` (kept, not deleted).
4. **Caddy cache rules.** Render the template **of the pinned checkout** (never `/srv/dflv2/repo`, whose template predates these rules and would silently drop them; see `docs/COMBINED_RELEASE_PLAN.md`, finding 5) for each host (`sed 's/__HOST__/<host line>/'`), `diff` against the live snippet (only the playback lines may differ), copy to a scratch dir with the rest of `/etc/caddy`, `caddy validate`
   (this was done on 2026-11-03 for both snippets against a scratch copy: "Valid configuration"), then install both snippets, `caddy validate --config /etc/caddy/Caddyfile`, `systemctl reload caddy`, and `cmp` each live snippet with its rendering. Check:
   `curl -sI https://<host>/feed/playback/index.json` -> `200`, `Cache-Control: no-store`; one game URL -> `200`, `Cache-Control: public, max-age=31536000, immutable`; a name that does not exist -> `404`, `Cache-Control: no-cache` (never `immutable`).
   Undo: `sh deploy/website/rollback.sh --config <snapshot from step 1>` (restores `/etc/caddy`, validates, reloads). This step is an optimisation, not a requirement: without it the same files are served with `no-cache`.
5. **Timer (only when the league is going to advance again; while it is stopped at 188 there is nothing new to export).** Render the service with the commit (`sed 's/__COMMIT__/<commit>/'` into `/etc/systemd/system/dfl-playback-export.service`), copy the timer, `systemd-analyze verify` both
   (done on 2026-11-03 on a rendered scratch copy: no findings), `systemctl daemon-reload`, run the service once by hand (`systemctl start dfl-playback-export.service`; exit 0, nothing new written), then `systemctl enable --now dfl-playback-export.timer`.
   It runs every 2 minutes; a run with nothing new writes no game file and rewrites an index with identical content. It writes only under `feed/playback`. Undo: `systemctl disable --now dfl-playback-export.timer`, remove the two unit files, `systemctl daemon-reload`.
   Not yet tried: the unit's sandbox against the real, live archive with the league runner active (SQLite read-only access to a WAL archive while the writer runs). Try it with the runner stopped first, then once while it runs, before relying on the timer.
6. **Site.** The frontend release goes in last, with its own helper (`deploy_site_only.sh <40-char commit>`; it is a separate step owned by the frontend work). Until it is installed no page shows a Replay link. Undo: `rollback.sh --site`.
7. **After.** `cap_state.py fingerprint` again and `diff` against the first one (exit 0); `persist.compatibility` of the league file unchanged; league still stopped at 188; the demo at `/feed/review/demo/` and the review copy at `/feed/review/frontend/` still load. `verify_revision.py` must be run with `--no-caddy` after step 4: its Caddy line compares the snippet with the engine commit's own template and reports a mismatch once the playback rules are in (expected; the `cmp` of step 4 replaces it).

**Full rollback, in this order:** site (`rollback.sh --site`), timer (disable and remove units), Caddy (`rollback.sh --config <snapshot>`), then move `feed/playback` aside and remove the pinned checkout. None of it touches the league, the archive, the feed
releases or any backup. With the folder moved aside the index request answers 404 and the site shows no Replay links, which is exactly the state before any of this.

## 6. Open items for the owner and for the frontend work (reported, not acted on)

* **Unnamed players in older seasons** (section 2): a data limit; only a new public source of past players' names would change it. A decision, not a defect.
* **For the frontend:** key a game by `(season, id)`, because ids repeat across seasons. The index's `id` equals the feed's id for all 428 feed games, so a lookup under the archive's seed name (`po:Eastern:wild_card:s5v4`) is never needed against this export (`gid` carries it if a page wants it).
* **For the frontend:** about 32% of plays have `trace: null` and 23% of roster entries have only `side`; both are normal data and need the "no movement recorded" and "player N" displays that the format document describes.
* **Not verified here:** the bowl and the coronation have no `schedule`/`history` entry in the feed to compare with, so their ids (`bowl`, `coronation`) are confirmed by the verifier and the coverage check against the archive only, not against the feed.
* **Interface changes:** none proposed. If one is wanted later (for example an index field for the feed release a replay was exported from), it will be described first.
