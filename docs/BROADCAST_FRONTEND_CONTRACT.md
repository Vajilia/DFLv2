# Broadcast frontend contract (prototype, version 1)

Branch `frontend/watch-now-48h`, based on prototype `61d7612`. No production deployment. `site/assets/broadcast.js` implements the client; `src/dflsite/broadcast.py` is the publisher/reference. Public data is GET-only. No engine imports in the browser or timetable.

## Paths and publication

Base is `DFL_CONFIG.broadcast` or `DFL_CONFIG.feed + 'broadcast/'`. For an isolated preview use `{broadcastOnly:true, broadcast:'/broadcast/'}`. This mode does not fetch the simulation feed/status or ordinary complete replay index. No new server route is installed by this branch.

| Path | Contents | Cache policy needed at deployment |
|---|---|---|
| `clock.json` | format `dfl-broadcast-clock`, state, anchor_pos, anchor_wall, lease_until, epoch; publication guard adds ready_until | no-store |
| `schedule.json` | format `dfl-broadcast-schedule`, format_version 1, windows `{label,start_s,slot_s,games:[{gid,away,home}]}` | revalidate/no-store |
| `live/<slug>/manifest.json` | gid, live/final state, number of contiguous released segments, seg_s, delay_s, speed=1, start_s | no-store |
| `live/<slug>/head.json` | public replay identity, teams, roster, sampling and field; kind=live-game; no final score or total plays | immutable |
| `live/<slug>/sNNNN.json` | gid, n, plays with `t0` and `dur` in normal-speed game seconds | immutable |
| `live/<slug>/end.json` | final, winner, tie, periods, play counts, replay filename; published only after the last play | immutable |
| `replays/index.json` | format `dfl-broadcast-replays`, format_version 1; completed broadcasts only | no-store |
| `replays/<filename>` | existing complete public replay format 1, after broadcast | immutable |

Slug replaces `/` and `:` in gid with `-`. Missing unreleased files are 404 and must never be cached as immutable. Private store and temporary files must not be served. Failed segment loads retry without jumping over a gap. Formats/manifests the client does not support are refused.

Future playoff/Coronation participants are omitted until `announce_s` (kickoff by default), not published merely because the engine already computed them. Schedule refresh is every 15 seconds; a new game's appearance can be delayed by that poll interval. Games that overrun remain on-now until actually final; slot expiry alone is not proof of completion.

## Clock and frame selection

Take server time from the HTTP Date header, plus half the measured request round trip and the half-second Date rounding correction. Advance from that sample using performance.now, never the local calendar clock. Poll clock every two seconds. Require the Date header and finite clock fields.

`limit = min(lease_until, anchor_wall + ready_until - anchor_pos)` when a publication horizon is present; otherwise the legacy demo's lease is the limit. Production must use the guarded publisher. Running position is `anchor_pos + max(0,min(server_now,limit)-anchor_wall)`; paused position is anchor_pos. Stop and show paused when the limit is reached. Downtime extends the wall-clock finish; no catch-up burst.

`tau = position - start_s - delay_s`. Select the last play whose t0 is at most tau. Show its recorded motion at `min(tau-t0, trace.t_end)`. Keep the score at the previous play while a traced snap is moving, and show “Play in progress.” until that movement finishes. Never pass future plays into the renderer. Event cards retain the no-movement fallback. The final/full replay view opens only once end.json is released and the last play's duration has elapsed.

Rewind is bounded to already released elapsed coverage and pauses the local view. Jump to live reattaches to the shared clock. No live speed/fast-forward control. On-demand final replays keep their existing play/pause, speed and seeking controls.

## Renderer integration

`DFLPlayback.create({live:true, entry:{gid}, load:()=>Promise<public-header-with-placeholder-play>})` creates a field-only renderer with replay controls/list hidden. `present(play, elapsedSeconds, previousScore)` draws the selected released play under the external clock. It does not run its own game clock. `destroy()` cancels its listeners/animation frame. Existing `create` callers without live:true are unchanged.

The app route is `#/watch` (optional selected gid in the next encoded path segment). The controller is destroyed when leaving Watch now. Selected games load their own manifests and segments; the page does not download every concurrent game.

## Limits and rollout gates

This is buffered full-play publication, not zero-peeking: network inspection can expose the whole current traced play and starts in its release bucket. Source HTTP Date accuracy is subsecond, not frame-perfect. Real-phone and full fresh-season acceptance remain required before release. Private weekly snapshot building, Honors publication and full-season overrun/reveal integration are still unbuilt; broadcastOnly mode must not be mistaken for completion of those jobs. Keep production and the stopped league unchanged.
