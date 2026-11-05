# Backend broadcast integration (season mode)

Branch `backend/broadcast-integration-2026-11-04`, from ChatGPT's `frontend/watch-now-48h` at `ece0f7e`. **Local implementation and tests only.** Nothing was deployed, no timer or unit was activated or changed, no server configuration was touched, nothing was merged to `main`, the production league was not opened (it stays stopped at event 188), and `site/`, the frontend tests, `src/dfl/` and `data/` are unchanged. Every league used below is a fresh isolated development league made in a temporary folder.

The decisions in `docs/BROADCAST_PLAN.md` stand: about 48 active viewing hours including a 49-minute offseason; games at normal speed in nominal 16-minute windows; pauses, downtime and overruns extend the finish and never shorten later breaks; kickoff groups and calendar order are preserved; weekly standings and roster updates; Honors presents only implemented, recorded public awards; and the clock never advances beyond the coverage that exists.

## 1. What was built

| Piece | File | Job |
|---|---|---|
| Season plan | `tools/broadcast_plan.py` | Turns ChatGPT's unchanged timetable into `plan.json`: 106 windows (kickoff groups), the blocks between them, which window every regular-season and Tour game belongs to, which events each block reveals (the 47 events of a year, each exactly once, in the engine's own order, checked against `dfl.calendar`). |
| Private builder | `tools/broadcast_build.py` | Fires a fresh isolated league **one event at a time** with the league runner, and for each event makes, in a private root: a feed release and its pointer, the full replay and the live recording of every game of the event, and (at the Ambassador Bowl and the year close) the honours parts. It never receives the public folder. |
| Season daemon | `src/dflsite/season.py`, `tools/broadcast_run.py` | The only writer of the public folder. Applies overruns, announces postseason participants, reveals everything a results block owns, writes `status.json`, holds the clock at the edge of what is built, pauses on request, and repairs the folder after a crash. |
| Honors | `src/dflsite/honors.py` | Reads the engine's recorded Tour Rings, Tiaras, halls and fan entrant from a private copy of the league and writes allowlisted public documents; says plainly what does not exist. |
| Season check | `tools/broadcast_season_check.py` | Runs a whole built year in virtual time with forced overruns, pauses, publish failures, a restart and a slow builder, and checks the public folder every second and at every boundary. |
| Changes to ChatGPT's module | `src/dflsite/broadcast.py` | Overrun-aware public schedule; grouped reveals; held-clock logging; a faster reconcile for a daemon that ticks all day. All 32 existing broadcast tests pass unchanged. |

`tools/broadcast_timetable.py`, its tests and everything under `site/` are exactly ChatGPT's.

## 2. How a season runs

```
 fresh league ──runner, 1 event at a time──▶ PRIVATE ROOT R                 PUBLIC FOLDER P (served)
                                              league/   store/               written only by the daemon
                                              feed/ (one release + pointer per event)
                                              playback/ games/ honors/ ──────▶ copied when the clock reaches it
                                              plan.json builds.json
```

```
python tools/broadcast_build.py init   --root R --seed N      # fresh league, plan, event-0 release
python tools/broadcast_build.py step   --root R [--events K]  # fire and build the next events (all 47 by default)
python tools/broadcast_run.py   run    --root R --public P    # the daemon (P must be new or made by this daemon)
python tools/broadcast_run.py   pause|resume|status --root R
```

The builder may run ahead of the broadcast by any amount; the daemon only ever sees an event when everything belonging to it is on disk (`builds.json` is replaced last). If the builder is behind, the **horizon** (the first instant something is not yet built) caps the clock's `ready_until`; the broadcast stands still there, `status.json` says paused, and the stand-still is logged once as `coverage_wait` when the data arrives. Nothing is skipped or shown early.

### What becomes public, and when (broadcast time, shifted by overruns)

| Public path | Released | Source |
|---|---|---|
| `latest.json`, `releases/<release>/` (standings, history, rosters, playoff pairings, calendar, notices) | the last event of each results block, at the block's reveal time (recap start; an offseason or break event at its end; a ceremony at its start). Weeks with a Tour round are revealed together. The directory first, the pointer last, only the newest due pointer is ever placed. | private `feed/` |
| `status.json` | rewritten by the daemon from **broadcast** progress: events whose results are public, the clock's state. It never carries the league's, the builder's or the horizon's progress. | daemon |
| `playback/index.json`, `playback/games/*` (the site's ordinary replay links) | with the results block that contains the game's event | private `playback/` |
| `broadcast/clock.json`, `broadcast/schedule.json` | from the first tick; the schedule lists windows beginning within six hours (and all that have begun) | daemon |
| `broadcast/live/<game>/` | head and first segment at kickoff, then one segment per five seconds, `end.json` after the last play | private `games/` |
| `broadcast/replays/index.json`, `broadcast/replays/*` | when a game's broadcast has ended | private `games/` |
| `broadcast/honors/index.json`, `broadcast/honors/s001/<part>.json` | Tour Rings at the Bowl ceremony; at the Honors show: Tiara at +0, halls at +10 min, fan entrant at +20 min (the `year_close` release at +25 min) | private `honors/` |

The release of the league **before** the broadcast began (event 0 here) is placed at position 0, so the site has a state to show.

### Overruns

A window lasts `max(nominal slot, longest game in it + viewer delay)`. The excess shifts every later window and block by the same number of seconds; later breaks keep their length; nothing is accelerated or skipped. The shift is **private until it can be seen**: the public schedule moves later windows only once a slot has expired with a game still live, and a window that has begun never moves. Postseason, Bowl and Coronation participants stay out of the schedule until the previous results block has been revealed (`announce_s`).

### Failure behaviour

| Event | What happens |
|---|---|
| Daemon killed (even `kill -9`) | The clock stops by itself when its lease or published coverage runs out. The next daemon logs the gap as `downtime`, resumes at the position the viewers stopped at, and the first tick repairs the whole public folder. No catch-up. |
| Publish step fails (disk full, I/O error) | The tick raises before the clock's `ready_until` moves, so viewers can never run past what was published; `status.json` reports the failure; the daemon retries every second and logs one JSON line per failure on stderr. |
| Builder behind | See above: held at the horizon, logged once. |
| `PAUSE` file | The clock stands still; `resume` removes the file. |

## 3. Honors

Recorded by the engine and published: **Tour Rings** (the winning Bowl side's final 53 who were ready at kickoff and the four Bowl coaches), **Tiaras** (the Coronation winner's final 53 and its CEO, GM, head coach and three coordinators), the **59 halls** and the yearly **fan entrant**. Player entries carry the roster id, name, position and club; staff carry role and club and no id. In a league's first years the halls are legitimately empty (a player needs a Tiara and five credited seasons) and there is no fan entrant; the documents say so and give the rule.

Not implemented by the engine and listed in `broadcast/honors/index.json` as unavailable: season MVP, All-League teams, Rookie of the Year, Coach of the Year, statistical-leader awards, and halls for staff. Hall values (season scores) and fan vote tallies are internal and not published. Nothing is invented.

The format is `dfl-broadcast-honors` version 1 (new, additive, under `broadcast/`). The ordinary feed's allowlist and forbidden-key list are unchanged: awards still never enter a feed release.

## 4. Verification record

Run on this branch's code (builder seed 20261105, fresh isolated league in a temporary folder; production untouched).

| Check | Result |
|---|---|
| Whole-year virtual-time check (`tools/broadcast_season_check.py`, 400 games, 41 releases, 59,290 public files) | **0 violations.** 612,471 per-second viewer-coverage checks, 658 guessed-URL probes through a static server, 1,030 full-tree scans for premature results and private tokens, public tree compared with an independent oracle after every boundary, reveal times recomputed independently. |
| Forced overruns (w002, w041, w070, w104, w105 at a 450 s slot) | Overruns of 294, 345, 328, 249 and 202 s (1,418 s total); every later window and block shifted by exactly that amount; later breaks kept their length; the year ended at 174,218 s (48.4 h) of broadcast time. |
| Scenarios | pause in the middle of a game (30 min); pause between windows (10 min); release copy failing for 240 s; live-segment writes failing for 90 s in a game; builder behind at event 34 (held at the horizon, one `coverage_wait`); daemon killed mid-game and restarted as a new process after 700 s (one `downtime`, resumed at the viewers' position). Clock log: 2 pause, 2 resume, 2 publish_failure, 1 coverage_wait, 1 downtime. |
| Determinism | A free-running league with the same seed and the builder-driven league have identical journal and deep digests after all 47 events (`league_store.py digest`). |
| Real-time CLI smoke | Second daemon refused (exit 75); pause held the position for 10 s; resume; `kill -9` and restart after 30 s logged one downtime. |
| Real browser over the finished season (ChatGPT's site, Chromium) | Home, schedule, playoffs, history, `team/ALA` and Watch now at 1280 px and 390 px: no console or page errors, no HTTP errors, no horizontal overflow. |
| Unit tests | `test_season`, `test_honors`, `test_broadcast_plan`, `test_broadcast_build` (32 tests, real engine) pass. |
| Frontend and broadcast checks (`test_broadcast`, `_guard`, `_timetable`, `test_site_broadcast`, `_replay_links`, `_home`, `_recovery`, `_static`) | 84 tests pass on this branch. The `team/ALA` 390 px overflow ChatGPT reported did not occur in this run. |
| Unchanged | `site/`, frontend tests, `tools/broadcast_timetable.py`, `src/dfl/`, `data/`. |

Commands:

```
python -m unittest tests.test_season tests.test_honors tests.test_broadcast_plan tests.test_broadcast_build
python -m unittest tests.test_broadcast tests.test_broadcast_guard tests.test_broadcast_timetable tests.test_site_broadcast tests.test_site_replay_links tests.test_site_home tests.test_site_recovery tests.test_site_static
python tools/broadcast_build.py init --root R --seed 20261105 && python tools/broadcast_build.py step --root R
python tools/broadcast_season_check.py --root R --public P --scan-every 900 --report rep.json     # about 19 min
```

## 5. Limits and remaining gaps

- **Network peeking is still possible.** Segments hold whole plays, including their complete movement arrays and result text. Anyone inspecting network traffic can read the current play and every play that starts in the same five-second release bucket (plus the three-second display delay); a long play can expose more than eight seconds. The page does not draw future plays, but this is buffered publication, not frame-level secrecy. The protocol was not redesigned. **No claim of zero spoilers is made.**
- Server Date has one-second resolution; synchronization is not frame-perfect, and a reveal can reach a viewer up to a few seconds before or after the clock passes it.
- One league year. The honors index and the playback index start empty; a second year's plan does not yet carry the first year's honors index forward (a seed file for `honors-index` and `playback-index` is the straightforward next step).
- The builder computes the whole year before it can be broadcast (about 35 minutes of compute for a real year on this machine) and a year's private store is about 0.9 GB. Disk-space management for several years is not built.
- Old releases and replay files stay in the public folder (about 0.8 MB per release); no pruning is built. `generated_at` in a pointer shows when a release was computed, which is earlier than its reveal; it is not a result.
- A production server must serve `latest.json`, `status.json` and `broadcast/clock.json` and `schedule.json` uncached, must not list directories, and must serve nothing from the private root. None of that is configured here.
- Real-phone testing and a real-time (not virtual-time) 48-hour rehearsal have not been done.
