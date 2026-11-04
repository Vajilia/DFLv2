# Broadcast: what the frontend needs (for ChatGPT, before any `site/` change)

Status: PROPOSAL. Nothing in `site/`, its tests or the frontend branch has been touched. Please review, change what does not suit the site, and say what you need
added to the data. The reference implementation of every rule below is `src/dflsite/broadcast.py` (`viewer_state`) and the protocol page `tools/broadcast_demo_web/demo.js`.

## Files (all static JSON, GET only; proposed root `/feed/broadcast/`, the demo serves them at `/broadcast/`)

| File | Cache | Meaning |
|---|---|---|
| `clock.json` | never | `{state: "running"|"paused", anchor_pos, anchor_wall, lease_until, epoch}` |
| `schedule.json` | short | `{windows: [{label, start_s, slot_s, games: [{gid, away, home}]}]}`, in broadcast seconds. No lengths, no results |
| `live/<game>/manifest.json` | never | exists from kickoff: `{gid, state: "live"|"final", segments, seg_s: 5, delay_s: 3, speed, start_s}` |
| `live/<game>/head.json` | long | like a replay file without `final`, `winner`, `tie`, `periods`, `plays`, `traced_plays` |
| `live/<game>/sNNNN.json` | long | `{gid, n, plays: [...]}`: replay-schema plays plus `t0`, `dur` (seconds after kickoff, already divided by `speed`) |
| `live/<game>/end.json` | long | only after the last play was shown: `{final, winner, tie, periods, plays (count), traced_plays, replay}` |
| `replays/index.json`, `replays/<name>` | index never; files immutable | on-demand library: ended broadcasts only; a file is the existing full replay format, unchanged |

`<game>` is the game id with `/` and `:` replaced by `-` (`S001-reg-w01-g01`). A file that is not yet released does not exist: expect **404**, which is normal, not an error.

## The one rule: what to show

1. **Position.** `pos = anchor_pos + max(0, min(now, lease_until) - anchor_wall)` if `state == "running"`, else `anchor_pos`. `now` is the *server's* time: take it from the
   `Date` header of the `clock.json` response (plus half the round trip), never from the viewer's own clock. Re-read `clock.json` at least every 15 s (the production lease
   is 90 s). If a refresh fails, keep the last clock: it stops by itself at the lease end.
2. **Game time** `tau = pos - window.start_s - manifest.delay_s`. Show the last play with `t0 <= tau`, at `(tau - t0) / dur` of the way through it (the existing viewer's
   position-in-play). `tau < 0`: "kickoff in ...". Do not play a play before its time and do not run ahead of the released data.
3. **Speed.** Always the clock's speed (1 s per second); `manifest.speed` is already folded into `t0`/`dur`, so draw the play at its own pace scaled by `dur`, not by a speed
   control. No fast-forward past live. Scrubbing back is allowed within what was released; offer "Jump to live".
4. **Ending.** When `manifest.state == "final"` and the last play has finished (`idx + 1 == end.plays` and `tau >= t0 + dur`) show the final; from then on the game is a
   replay (`end.replay`), watched on demand with the existing viewer unchanged.
5. **Paused / interrupted.** `state == "paused"`, or a clock past its lease: show "Broadcast paused", keep the screen still, resume by itself.
6. **Late join and refresh**: nothing to do. Same files, same position, same screen.

## "Watch now" needs

* **Now**: windows with `start_s <= pos < start_s + slot_s`; **Upcoming**: the next windows with a countdown to `start_s`; **Completed**: `replays/index.json`.
* Switching between simultaneous games of the window: one `manifest.json` poll per watched game (every 1 s) plus the segments it announces; load only the selected game.
* Final scores in lists only for ended games (the library). Standings, team pages and rosters keep coming from `/feed/latest.json` and change once per week.

## What would change in `site/` (yours to decide)

A new route and view, a small broadcast data module (clock + segments), and a way for the viewer to take a play and a position-in-play from it (`create(opts)` already takes
`opts.load`; the live controller would feed plays instead of a finished file). No change to the replay format or the existing replay pages.

## Questions

1. Does the viewer accept plays arriving in segments (an array that grows), or does it need a complete game object? If the latter, the demo can serve a growing `plays.json` instead.
2. Is `delay_s = 3` and `seg_s = 5` acceptable for smoothness (a segment must arrive within about 3 s of its release)? Both are parameters of the data, not of the viewer.
3. Which states need copy and design: scheduled, live, paused, interrupted, final, replay available.
