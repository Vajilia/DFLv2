# Watch now and the 48-hour timetable: implementation handoff

Branch: `frontend/watch-now-48h`. Integrates broadcast prototype `61d7612` with frontend `e4c8d32` on an isolated development branch. No main/release branch changed. No production or Droplet access, deployment, league advancement or backup changes performed. The engine/data trees are unchanged from the deployed engine release.

## What is concrete

- `tools/broadcast_timetable.py`: 172,800 active seconds, 106 nominal 16-minute windows, 400 games, a 49-minute offseason, with deliberate results/viewing breaks. `apply_overruns` shifts later segments without compressing anything.
- `src/dflsite/broadcast.py`: normal-speed timelines, nested schema checks, future postseason participant announcement gates, and the publication horizon that prevents an interrupted publisher from exhausting an uncovered lease.
- `site/assets/broadcast.js` and `broadcast.css`: Watch now lists, one server-derived monotonic clock, selected-game-only segment fetching, contiguous loading, externally timed animation, game switching, rewind within available coverage, Jump to live, and transition to completed replays.
- `site/assets/playback.js`: a small live presentation mode using the existing decoder/field renderer. Ordinary replay callers keep their existing behavior.
- `DFL_CONFIG.broadcastOnly`: safe isolated frontend mode which never reads the engine's feed/status/full-replay index. The final all-pages broadcast feed is not yet integrated.
- `tools/build_watch_preview.py`: prepares a local site/public tree from the checked-in public replay fixture under fresh season-901 identities. Its private source is a sibling outside the served tree. It refuses to overwrite an existing output. `--run` advances only the isolated broadcast, never a league.

The clock's old lease-only mode remains available internally for synthetic release-rule tests which jump hundreds of seconds between calls. `Broadcaster` defaults to guarded publication, and the CLI uses that default. Publication guard behavior has separate failure/recovery regressions; a production daemon must never opt out.

## Reproduce

```
python tools/broadcast_timetable.py --json
python -m unittest discover -s tests -p 'test_broadcast*.py'
python -m unittest discover -s tests -p test_site_broadcast.py
python -m unittest discover -s tests -p test_site_replay_links.py
python -m unittest discover -s tests -p test_site_home.py
python -m unittest discover -s tests -p test_site_recovery.py
python -m unittest discover -s tests -p test_site_static.py
```

Browser tests require Playwright and Chromium. A missing browser is not a passing acceptance run. New browser tests use temporary files and fresh identities/narrative/results, not the already-published production season. They check direct unreleased-file requests, lack of feed requests, late join, refresh, local calendar-clock skew, canvas motion/pause, bounded seeking, game switching, final replay, publication expiry and missing server time.

For a local demonstration (use a new absolute folder):

```
python tools/build_watch_preview.py --out /absolute/new/watch-preview --run
```

Serve only that output folder with the supported development preview, never its sibling `watch-preview-private`. Open `#/watch`. Do not point the tool at `/srv/dflv2`, production feed paths or a league. The example uses the public fixture and has two simultaneous games; it is not a whole-season rehearsal. A live-reload development server may reload on JSON updates; pause the isolated publisher when inspecting a stable screenshot.

## Before anyone deploys

This branch is a working local viewing prototype, **not an unattended season broadcaster**. The full-season private snapshot builder, overrun-aware daemon/reveal coordination, weekly public feed/status integration and Honors feed remain unbuilt. The tested overrun function is not yet wired to the daemon's live schedule. Nothing should resume the league at 188 on the strength of this branch.

The next bounded milestone is a fresh, privately built season with all public paths owned by the broadcast layer; force an overrun and a publisher outage, then test all results-bearing pages and guessed URLs. Do not publish future playoff participants, full replay files or future feed snapshots in advance. Keep the current website/demo and verified recovery set intact.

Buffered whole-play segments still permit network inspection of the current play and starts in its bucket. This is explicitly described in `BROADCAST_PLAN.md`; it is not zero-spoiler or frame-perfect synchronization. Real-phone and full-season acceptance remain outstanding.

## Validation record

Results are recorded below after completion of the focused checks. No full engine suite is claimed.

- Broadcast/timetable/publication guard: 32 tests pass (24 existing broadcast tests, 4 timetable tests, 4 new guard tests).
- Watch now browser tests: 6 pass, including fresh unreleased data/direct URLs, two viewers/refresh, motion/pause/seek, game switching/final, expiry and missing server time. Desktop/390px covered where applicable; not a physical-phone check.
- Existing replay/control tests: 18 pass. Homepage: 6 pass. Recovery: 2 pass.
- Static site: 19 pass, 1 fails on the previously reproduced `team/ALA` overflow at 390px in Chromium 134 / Playwright 1.51. This broader suite is not fully green; the existing layout issue is not called fixed.
- Total: 83 passing checks and that 1 known static layout failure. No engine-suite or 48-hour/full-season acceptance claim.
- Syntax, Python compilation and git whitespace checks pass. No diff under engine/data.
- The isolated supported browser preview displayed the broadcast clock, selected game, scoreboard, field, rewind and Jump to live. Development live reload refreshed on changing JSON, so it was paused for stable visual inspection; automated canvas-motion checks used the test server. Preview and its local publisher were stopped after QA.
