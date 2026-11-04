# A watchable season in approximately 48 active hours

Development branch: `frontend/watch-now-48h`. Based on broadcast prototype `61d7612` and frontend `e4c8d32`. **Local implementation and tests only. Nothing deployed or resumed.** The production league remains stopped at 188. No change under `src/dfl/` or `data/`.

## 1. Agreed viewing model

The target is 48 **active broadcast hours**, not a 39-hour season with outage reserve. The offseason remains 49 minutes. All 400 games, 106 kickoff groups, simultaneous games and existing calendar order are preserved. Nominal game slots are 16 minutes and playback is 1x. Deliberate weekly/results and postseason breaks bring the planned duration to 172,800 seconds. Pauses, publisher downtime and unusually long games extend the wall-clock finish; they never consume later breaks or accelerate a game.

`tools/broadcast_timetable.py --json` is the exact timetable. `--wall <ISO time>` prints a provisional wall-clock plan, valid only until a pause or overrun. The whole timetable uses broadcast seconds; daylight-saving changes do not alter elapsed durations. The Season 1 template is a proof-of-concept calendar: confirm generated seasons retain its kickoff grouping before using this tool as a permanent scheduler.

## 2. What was measured (not divided)

`tools/broadcast_measure.py`, read-only, over the 1,600 published replays (4 seasons) with the viewer's own constants (a play with movement lasts `trace.t_end + 1.2 s`,
any other play 2.6 s; speed 1). League compute times are from the league's `events.jsonl`.

| | games | min | median | p90 | p99 | max (s at 1x) |
|---|---|---|---|---|---|---|
| regular season | 1440 | 542 | 667 | 730 | 796 | 849 |
| Ambassador Tour | 112 | 583 | 670 | 743 | 793 | 802 |
| playoffs | 40 | 586 | 666 | 738 | 859 | **905** |
| Ambassador Bowl | 4 | 620 | 654 | 677 | 684 | 685 |
| Coronation | 4 | 623 | 640 | 651 | 654 | 654 |
| by window: Sat 17:30 / Sun 10:00 / Sun 13:00 / Sun 17:30 / Mon 17:30 | 104/708/484/152/104 | | 676 / 668 / 664 / 666 / 662 | | 794 / 801 / 785 / 813 / 776 | 819 / 833 / 820 / 849 / 788 |

* A game is **about 11 minutes** at 1x (median 667 s); the longest of 1,600 is 15.1 minutes. About 68 % of plays have recorded movement; the rest are cards (kickoff,
  punt, penalty, timeout, score ...) at 2.6 s each, which is why plays-times-a-constant would have been wrong.
* **Game length does not depend on the window** (medians within 14 s), so one slot length serves every window.
* League compute: a week event about 70 s, a tour round / wild card / divisional about 16-18 s, everything else 1-9 s. The league is therefore always far ahead of any
  broadcast (a week of broadcast is 110 minutes) and **needs no speed-up to keep up**.

## 3. Where the 48 hours go

| Component | Duration |
|---|---:|
| Offseason | 49 min |
| 106 game windows | 28 h 16 min |
| Weekly/postseason recap and viewing breaks | 10 h 59 min |
| Between-window gaps | 6 h 45 min |
| Break weeks without games | 16 min |
| Trading deadline | 5 min |
| Bowl/Coronation ceremonies | 20 min |
| Honors/year close | 30 min |
| **Total active time** | **48 h** |

Postseason: two windows of two games for wild card, two windows of two for divisional, two conference championships together, then Bowl and Coronation separately. These remain viewing assignments, subject to any future explicit kickoff rule. The Honors block may show only implemented, recorded public awards. The prototype does not yet export an Honors file and must not invent recipients.

`apply_overruns(segments, window_durations)` is the tested private planning utility. A duration includes all plays, the display delay and a tail. It extends the relevant window and shifts every later segment by the same excess, preserving all gaps and game groups. It never changes a game speed. The full-season daemon still needs to wire that utility to delayed announcements and dependent reveals. Do not publish result-dependent future game durations or an advance end time that reveals overtime. Until that integration is implemented, this branch is **not ready for an unattended whole-season broadcast**.

## 4. What is implemented

- The prototype clock, private store, segment release and local GET-only server.
- Normal-speed game timelines even when a nominal slot is too short.
- A publication horizon (`ready_until`) in the clock. The running position is bounded by the lease **and** by the coverage already released. A dead or failed publisher cannot consume the rest of its 90-second lease without material to show. On recovery the uncovered gap is logged as downtime; no catch-up occurs.
- Nested public-schema checks for headers, segments and endings.
- Future playoff and Coronation participants are absent from the public schedule until their announcement point (kickoff by default). A builder may set an earlier `announce_s` only when those participants have become public legitimately.
- Frontend Watch now: on-now, announced upcoming and completed games; game switching; authoritative server-time alignment; late join/refresh; pause/expired-publication freeze; released-coverage rewind and Jump to live; completed replay mode. It reuses the existing field renderer rather than a second animation implementation.
- `DFL_CONFIG.broadcastOnly = true` isolates the view: no simulation feed, status or ordinary full-replay index is read, and other routes show the broadcast view. This is the first rollout mode, not the final navigation model.

## 5. Data boundary and honest limits

All computed material belongs outside the public root. Live data comes only from the broadcast clock and released files. Endings and full replays appear after the last play. The existing historical public replay library contains completed development games, so it cannot prove secrecy for a new season. The new browser tests use fresh season-901 identities and narrative/result variants in a separate temporary private/public tree; unreleased files and ordinary feed paths return 404 there.

The segment protocol is **not frame-level spoiler protection**. A released segment contains full plays, including complete movement arrays and result text. Someone inspecting network data can inspect the rest of the current play and plays beginning in the five-second release bucket, plus the display delay. A long play can expose more than eight seconds of future movement. The page itself does not draw future plays and hides a traced play's result text until its movement has finished. Cutting traces/text into finer-grained releases is a separate improvement; do not describe this prototype as having zero peeking.

The browser's Date.now is not authoritative. It estimates server time from the HTTP Date header and elapsed monotonic time; HTTP Date has one-second resolution. Expect subsecond synchronization error, not frame-perfect synchronization. No usable Date/clock means no advancement. When a connection fails, the last valid clock stops at its publication/lease limit.

## 6. Remaining production integration

1. Build private per-event public snapshots and game records from committed league events, isolated from the old publisher. The job is not yet built.
2. The full-season daemon must apply overrun shifts, participant announcement gates and all dependent feed/award reveals coherently. The local demo is not that daemon.
3. Route every public result-bearing surface through broadcast progress: latest pointer, status, standings, history, rosters, playoff pairings, honors, replay indexes and guessed file URLs. Weekly standings/roster updates are acceptable initially. Never run the old all-results exporter into the same public namespace.
4. Populate an Honors feed only from available recorded public awards; mark missing content explicitly.
5. Pin and validate server cache rules, use private/public paths, and rehearse deployment and rollback. Nothing here changes Caddy, units, production files or backups.
6. Rehearse a complete fresh season with outages and a forced overrun, measure real-phone performance, then decide on production deployment. The production league at 188 is not a test input to modify or resume.

## 7. Tests and handoff

See `docs/WATCH_NOW_IMPLEMENTATION.md` for the commands, actual results and limits of validation. The frontend contract is `docs/BROADCAST_FRONTEND_CONTRACT.md`. Run existing replay tests too: ordinary completed-game replays retain their old behavior and independent site-only deployment remains available.
