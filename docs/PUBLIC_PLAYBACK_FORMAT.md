# Public playback format and the replay viewer

Status: first milestone (2026-11-01). Built and tested locally; a static review copy (viewer plus three real games) is on the Droplet under `/feed/review/` only. Nothing is deployed to production; see "Deployment".

## Why it is separate

```
engine ──► league + archive ──► exporter (tools/export_playback.py) ──► feed/playback/ ──► browser (site/assets/playback.js)
 (results, as-played traces)      reads the archive, read-only          versioned public files   draws them; never runs or imports the simulation
```

* The **engine** produces results and as-played spatial traces (docs/ARCHIVE.md). It is not touched by anything below.
* The **exporter** converts a finished, already-published game into a public file. It reads the archive read-only, uses only the Python standard library,
  and imports nothing from the engine (`src/dflsite/playback.py`). It can be changed and deployed without touching the league.
* The **viewer** (`site/assets/playback.js` and `.css`) reads those files and draws them on a canvas. It fetches exactly one thing per game: the file named
  in the replay index. It holds no secrets, makes only GET requests, and cannot change anything.

Because the site and the exporter are outside the league's code fingerprint, editing them does not change league fingerprints, invalidate saves,
restart the runner or alter any result (checked by `tools/cap_state.py` before and after; see "Frontend-only update").

## What a public file contains, and what it never does

Copied: participants, final score, each play's period, clock, possession, down, distance, field position, kind, a one-line description built from the
recorded play, the running score, and, for a play the engine simulated snap by snap, every player's and the ball's position, with the recorded throw,
catch, handoff, tackle and fumble events. Player names, numbers and positions come from the public rosters.

Never copied: ratings, attributes, play calls, formations, coverages, player intents, box-score statistics, injury details (the play-by-play only says
that an unnamed player left the game hurt), contracts, anything private. `check_public` refuses any key outside the schema at any depth, so a field added to
the archive later cannot leak by accident. The exporter writes only games whose archive record was committed at or before the published feed's event count,
so a game that has not been revealed on the site is never exported.

Honesty rule: a play with no stored trace (kickoff, punt, kick, penalty, timeout, kneel and the like) has `"trace": null`. The viewer shows it as an event card
("No player movement was recorded for this play.") and never draws motion for it. Between two recorded samples a player is drawn on a straight line at
constant speed, and the page says so. The archive's first ball sample of each play is (0, 0, 0), meaning the ball is not placed yet; the file keeps it as
stored, `ball_from` says from which sample the ball was recorded, and the viewer does not draw the ball before that.

## Files

```
feed/playback/index.json                           the list of replayable games (small; never cached)
feed/playback/games/<gid-slug>.<sha10>.json        one game; the name carries the first 10 hex digits of the file's SHA-256, so it never changes
```

`index.json`: `{"format": "dfl-public-playback", "format_version": 1, "kind": "index", "league_id", "games": [entry...]}`; an entry is
`{id, gid, season, game_kind, file, bytes, sha256, away, home, away_score, home_score, plays, traced_plays}`. `id` is the id the public schedule uses
(`reg:w03:g07`, `po:East:wild_card:1`, `bowl`, `coronation`), `gid` the archive id (`S001/reg:w03:g07`). The site links "Replay" from a schedule row only
when season, id, participants and score all match the published schedule.

A game file (canonical JSON, keys sorted, ASCII) has: `format`, `format_version`, `kind: "game"`, `gid`, `id`, `season`, `game_kind`, `date`, `kickoff`,
`neutral`, `teams {away, home: {abbr, name, colors}}`, `final {away, home}`, `periods`, `tie`, `winner`, `roster {player id: {name, number, pos, side}}`,
`identity_note`, `sampling`, `plays`, `traced_plays`, `field`.

A play: `i`, `period`, `clock` (seconds left in the period), `off` ("home"/"away"), `dir` (+1: the offence attacks the right-hand end zone on screen; a display
convention, since the engine records no absolute direction: the home team attacks right in periods 1 and 3, left in 2 and 4), `kind`, `text`, `score` (after
the play), and for snaps `down`, `dist`, `yl`, plus `first`, `pts` where they apply, and `trace` (object or null).

A trace: `dt` (seconds between kept samples), `n` (samples), `t_end` (seconds), `los`, `roles`, `ids`, `events [[t, kind, a, b]]`, `ball_from`, and `pos` / `ball`:
base64 of little-endian int16, hundredths of a yard, **column-delta** (for each of 44 position columns, or 3 ball columns, in turn: `n` values, the first the value
at sample 0 and each later one the change since the sample before; a running sum restores them). Columns are `x, y` per slot, slots 0-10 the offence (qb, hb, te,
x, z, s, lt, lg, c, rg, rt) and 11-21 the defence (de_l, dt_l, dt_r, de_r, lb_l, mlb, lb_r, cb_l, cb_r, fs, ss). x is yards from the offence's own goal line
(0-100, end zones beyond), y is yards across the 53.33-yard field, increasing to the offence's left. Samples are every 2nd recorded step (0.1 s) plus the final
step, by default (`--every`).

## Versioning and compatibility

Viewer and files agree on `format` and `format_version` (both 1). The rules:

* A new **minor** addition (an extra key) must be ignorable by an old viewer; `check_public` is the exporter's gate, so adding a key means changing `ALLOWED`
  in the same commit that documents it here.
* A **breaking** change raises `format_version`. A viewer that meets a version it does not read says so and does nothing else: an index with another version
  shows no Replay links and the replay page says "The replay files use format version N, which this page does not read (it reads version 1). The site needs
  updating before replays can be shown."; a game file with another version shows the same kind of message for that game.
* Viewer-first order for a breaking change: ship a viewer that reads both versions, then switch the exporter, then drop the old version.
* A file that does not match its published checksum, is missing, or holds another game than the one requested is not shown (a message says why). A damaged trace
  affects only its own play, which is shown as an event with a note.

Tests: `tests/test_playback.py` (files, privacy gate, reconciliation with the published schedule, encoding round-trip, immutability, and the viewer in a real
browser: desktop and phone widths, missing data, damaged data, incompatible versions, seeking, play/pause/speed/keys, game switching, feed refresh, read-only).

## Cost (measured on real archived games, 2026-11-01)

About 580 KB per game as exported at 0.1 s sampling (167 KB gzip; Caddy already compresses), 0.1 s of CPU and about 25 MB peak memory per game; sampling every
0.15 s gives about 430 KB (138 KB gzip). A 400-game season is therefore about 65 MB gzip on disk if every game is exported. Viewing one game downloads the index
and that game only; browsing the site downloads neither the viewer code nor any game file until a replay is opened.

## Working on the site without the engine

```
python3 tools/preview_site.py --feed tests/fixtures/playback_v1/feed --playback tests/fixtures/playback_v1/playback --port 8080
```

serves the site with a fixed sample feed and two fixed sample games (a real archived 12-event development league), read-only; no engine, league or credentials
are involved. Open `http://127.0.0.1:8080/#/schedule/1` and use "Replay". Styling, layout and animation work happens in `site/assets/playback.css` and
`playback.js`; `python3 -I -m unittest discover -s tests -p test_playback.py` checks them. Regenerating the sample from an archive:
`python3 tools/export_playback.py --archive A --feed F --out O --limit 2 --every 3`.

## Frontend-only update

`deploy/website/deploy_site_only.sh <40-char commit>` installs only that commit's `site/` folder as a new site release (atomic link switch), proves it is
byte-identical to the commit, and touches nothing else (no league, runner, feed, exporter, engine code, Caddy, or restart). It is undone with
`rollback.sh --site`. `tests/test_site_only_deploy.py` checks that every file outside the site folder is byte-identical after an update and after a rollback.
On the Droplet the demonstration is run with `tools/cap_state.py fingerprint` before and after, plus `persist.compatibility`.

## Deployment (not done yet; needs approval)

1. Site files and viewer: `deploy_site_only.sh` (no league effect). Until the exporter has written an index, the replay index request returns 404 and no
   Replay link is shown; nothing else changes.
2. Exporter: a timer unit that runs `tools/export_playback.py` as the feed's service user, writing under `/srv/dflv2/feed/playback/` (needs the same
   read access to the archive as the feed exporter has, and a check that the feed publisher's retention does not remove the folder).
3. Caddy: the template now gives `/feed/playback/games/*` an immutable cache and `/feed/playback/index.json` no-store. This is a server configuration
   change and goes through the normal validate-then-reload path.

## Review copy, verification tools and known limits (2026-11-01)

* `site/assets/config.js` is empty in production. A review copy sets `window.DFL_CONFIG = {feed, playback}` so the same site code reads another feed and playback folder. Removing the copy: `rm -rf /srv/dflv2/feed/review`.
* `python3 tools/verify_playback_against_archive.py --archive A --playback P` compares every exported game with the archive independently of the exporter (participants, recorded movement, scoring events, final score); exit 0 only if all pass.
* `python3 tools/build_replay_demo.py` builds one offline HTML page (viewer, styles and chosen games inline; no network).
* Player identification: pointer or tap on a player shows name, number, position, side and role on that play, from the public file only.
* Limits: a player who has left a roster since the game appears as "player N" (names are not stored with the archive); plays without a stored trace show an event card, never invented motion.
