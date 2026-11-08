# The failed rehearsal's evidence (preserved in place; nothing on the Droplet was changed)

Read through the Droplet web console (Claude in Chrome, the owner's signed-in session) on Sun 2026-11-08 ~07:36-07:40 UTC with read-only commands (`cat`, `ls`, `sha256sum`, `stat`, `grep`, `systemctl show/list-*`). Nothing was created, moved, edited,
released, resumed, restarted or deleted. (The console login itself writes the usual login records, `last`/`wtmp`; no rehearsal file is involved.)
Authoritative copies stay where they are, in `/srv/dflv2-rehearsal/monitor/` (owner `dflv2`). This file is the record of what they were when read. Values below are transcribed from the console text.

## The trip
- `TRIPPED` (123 bytes, written 2026-11-07 14:52:55.219 UTC): `{"at": 1794063174.7056286, "detail": "daemon memory 578 MB of a 629 MB limit", "kind": "memory_high", "source": "monitor"}`
- `trips.jsonl` has the same single line (same sha256). `watch.log` line 1860: `{"at": 1794063174.7, "detail": "daemon memory 578 MB of a 629 MB limit", "violation": "memory_high"}` — the ONLY violation in the log.
- First-violation evidence `evidence/20261107T145255-memory_high.json` (167 bytes): `{"at": 1794063175.220937, "detail": "daemon memory 578 MB of a 629 MB limit", "evidence": {"current": 578011136, "max": 629145600}, "kind": "memory_high"}`.
  **This is the only memory data the monitor kept at the trip: two numbers. There is no breakdown.**
- Trip snapshot `evidence/trip-20261107T145254Z-memory_high/evidence.json` (1270 bytes): the same two numbers, `pause_file: false`, the public clock `{"anchor_pos": 0.0, "anchor_wall": 1794007433.2934203, "epoch": 1, "lease_until": 1794063260.1726136,
  "ready_until": 55743.8797917366, "state": "running"}`, the public status (`checked_at` 2026-11-07T14:52:51Z, `league_events` 22, `published_events` 22, release `e00022-647eaf6cf8`, runner `running`, source `broadcast`) and the store clock (same values as the public clock).
  `public_listing.txt.gz` (171,201 bytes): the listing of the public folder at the trip.

## sha256 (read 2026-11-08 ~07:38 UTC)
| file | bytes | sha256 |
|---|---|---|
| TRIPPED | 123 | f99b0104edee6a0e9bcdf9e145ddf51a838c0adbc98b24bde8bb50226f5f97c6 |
| trips.jsonl | 123 | f99b0104edee6a0e9bcdf9e145ddf51a838c0adbc98b24bde8bb50226f5f97c6 |
| evidence/20261107T145255-memory_high.json | 167 | 0b62d9343fb77f1089e6b1e94c99e08b13c0c905d33b26e8c80a4c67bb0ed6bc |
| evidence/trip-20261107T145254Z-memory_high/evidence.json | 1270 | 380c62e8b293c19c1a22ee9a2598c23736f2f7a02354ad91b366d576baac4a4c |
| evidence/trip-20261107T145254Z-memory_high/public_listing.txt.gz | 171201 | 2770170e61d74ee8bf84d52cf694557f6174a31c4f470fc94e9351859f1429d7 |
| watch.log | 668,251 at 07:37:57 UTC, **still growing** (one heartbeat line per 30 s) | not a fixed hash; line 1860 is the violation. DO NOT truncate, rotate or clear it. |

## State when read
- `dfl-rehearsal.service` and `dfl-rehearsal-monitor.service`: active, `NRestarts=0`, started 2026-11-06 23:23:53 UTC. The monitor's heartbeat is 1 s old; `dfl-rehearsal-build.service` is `active (exited)`.
- Clock: `paused`, position 55,743 s of 172,800 (32.3 %); the heartbeat lines show `pos 55742.9` repeating. 152 of 400 games in both the replay library and the playback index.
- Disk 24 GB free of 48 GB (51 %), inodes 8 %. Host memory 42 % (`free`: 472 MB used, 1,495 MB available of 1,967 MB).

## The daemon's cgroup, read while PAUSED (not at the trip) — `tests/fixtures/cgroup_v2_droplet_2026-11-08/` has the files
- `memory.max` 629,145,600 (600 MiB MemoryMax), `memory.high` max, `memory.current` 269,832,192, `MemoryPeak` 578,273,280.
- `memory.events`: low 0, high 0, **max 0**, oom 0, oom_kill 0, oom_group_kill 0 — cumulative since the unit started 31 hours earlier.
- `memory.pressure`: some/full avg10, avg60, avg300 all 0.00 and **total=0** for both — the group never lost a microsecond to memory stalls. (The host-wide `/proc/pressure/memory` total: some 37.8 s, full 21.4 s since boot.)
- `memory.stat` (now): anon 22.4 MB, file 202.5 MB (of which dirty 24 KB, writeback 0, mapped 0, shmem 0), kernel 45.0 MB (slab_reclaimable 44.8 MB). `pgscan_kswapd` 126,336 and `pgscan_direct` 0: the kernel's background reclaim has been trimming the group's cache, direct reclaim never was needed.

## DigitalOcean's own graph (Insights, 1 day)
Memory (% of the Droplet) is flat at about 24 % from 00:00 to 22:00 PST on Nov 7, with no step or climb around 06:52 PST (the trip). The graph's definition of "used" was not verified; 24 % of 2 GB is about 480 MB, which is what `free` shows as used (472 MB) next to 1.5 GB of cache.
Disk write activity shows a drop at about 06:55 on the same axis (assumed to be the browser's local time, PST), which fits the daemon pausing.

## Production, read-only, same session
League at event 188: `feed/status.json` `league_events` 188, `published_events` 188, release `e00188-cf545fbf7b`, runner `stopped` (`limit_reached`, `until_events` 188, last event 2026-11-03T07:48:28Z).
`feed/latest.json` release `e00188-cf545fbf7b` (generated 2026-11-03T07:49:06Z, manifest sha256 1e3565b007ba5dd680792a4eb0c15bd3cda11ecb7d416211fc2308d53fd5cc95), `releases/` newest three: e00186-21a29ba676, e00187-b3d2953215, e00188-cf545fbf7b.
`league/league.db` 17,518,592 bytes, mtime 2026-11-03 07:48:28; `league.db-wal` 0 bytes; `events.jsonl` last record `seq 188`, key `year_close`. `dflv2-league.service`: inactive (dead), `Result=success`, inactive since 2026-11-03 07:48:29 UTC, enabled; no `dflv2-league.timer` exists.
Timers running: `dfl-feed-export.timer` (about once a minute: it rewrites `feed/status.json` and takes `league.db-shm`, which is why those two files change; it published no new release: `latest.json` still names e00188) and `dfl-site-probe.timer` (every 5 minutes).
Files newer than the rehearsal start under `/srv/dflv2`: `feed/status.json`, `feed/.publish.lock`, `league/league.db-shm` — consistent with the feed-export timer (an inference from the unit description, not tested); no new release directory and no change to `league.db`, its `-wal` or `events.jsonl`.
