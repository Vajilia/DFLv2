# Capped 48-hour broadcast rehearsal — package, server rules, rollback (FOR REVIEW)

Status: **prepared, not installed, not started.** Nothing here touches production (`/srv/dflv2`, the website, the league at event 188, the daily timers, the
backups). Every step that changes the server is a separate step you approve.

Candidate: `971fa5b` (broadcast integration + status fix + release label) plus the backend rehearsal tooling in this branch
(`backend/rehearsal-package-2026-11-05`). The tooling adds **no** change to `site/` and none to the simulation, season or broadcast logic;
the only code in the daemon's path that changed is `tools/broadcast_run.py` (two opt-in safety flags, below). Check with
`git diff 971fa5b HEAD -- site src` (empty).

## 1. What the rehearsal is for, and what would count as success

One real-time run of a complete, never-broadcast 47-event season (106 windows, 400 games, ~48 h nominal) on the real server software, to learn what the virtual-time
tests cannot: real Caddy cache behaviour, a real daemon surviving real hours, restarts and pauses, disk and memory over two days, and real phones.

It is **not** a launch. The season is synthetic (seed 20261105), served from a separate host name, and nothing from it can reach the production league.

**Pass** = all of: the monitor logs zero findings; `rehearsal_check.py http` passes at start, mid-run and end; every drill in section 8 behaves as written; the
daemon runs to the deadline without a manual fix; memory stays under the cap; public folder stays under 3 GB; production fingerprint unchanged (section 9).
**Abort** = any "early file" or "future URL served" finding (a spoiler-class failure), the daemon exiting with an error twice in an hour, disk under 6 GB,
or anything touching production. Abort means `systemctl stop`, keep the logs, report.

## 2. The caps (all enforced by code or unit settings, not by discipline)

| Cap | Value | Enforced by |
|---|---|---|
| Wall-clock | deadline = start + 54 h (48 h plan + 6 h for drill pauses). At the deadline the daemon **pauses and exits 0**; systemd does not restart it | `broadcast_run.py --deadline`; env file ships with a deadline **in the past**, so an accidental `start` pauses at once (rehearsed: pauses at 0.2 s, exit 0) |
| Public folder size | 3 GB (a full season is expected to be far below; measured in virtual time before) | `--max-public-gb 3`, checked every 5 min; exit 70, no restart |
| Daemon memory | 600 MB | `MemoryMax=600M` |
| Monitor memory | 400 MB | `MemoryMax=400M` |
| Free disk | ≥ 6 GB at install; monitor flags < 5 GB | `install_rehearsal.sh`, `rehearsal_check.py` |
| Writes | daemon may write only `private/` and `feed/` | `ProtectSystem=strict`, `ReadWritePaths` |
| Timers | none are created | no `.timer` unit ships; units are installed **disabled** |
| League | never mounted, never read; the package contains no league database | package builder refuses `.db`/`.archive`, clock, lock, PAUSE |
| Concurrency | one daemon per store | flock; second start exits 75 |

## 3. Layout (everything new; nothing existing is edited except one added Caddy file)

```
/opt/dflv2-src/<40-hex commit>/        pinned code (new directory, never overwritten)
/srv/dflv2-rehearsal/private/store/    the unreleased season (plan, builds, games, honors, playback) — NOT served
/srv/dflv2-rehearsal/feed/             the daemon's public folder, served at /feed/
/srv/dflv2-rehearsal/site/             copy of site/ from the pinned commit
/srv/dflv2-rehearsal/monitor/          watch.log, evidence/ (kept for review)
/etc/dflv2/rehearsal.env               REHEARSAL_DEADLINE=...
/etc/systemd/system/dfl-rehearsal.service, dfl-rehearsal-monitor.service      (installed, not enabled)
/etc/caddy/conf.d/dflv2-rehearsal.caddy                                       (one new file; host rehearsal.diamondfootballleague.com)
```

The installer refuses any rehearsal folder that is, is inside, or contains `/srv/dflv2`, `/var/www`, or `/`, requires "rehearsal" in the folder name, and
refuses an existing folder (nothing is overwritten). The units run as `dflv2`, the user production's export services already use (`deploy/website/dfl-*-export.service`); the installer creates it as a no-login system user only if it is missing.

## 4. Server cache and directory rules (Caddy)

File: `deploy/rehearsal/Caddyfile.rehearsal.template`. Same security headers and CSP as the production template. Rules (from `docs/BROADCAST_FRONTEND_CONTRACT.md`):

| Kind | Header |
|---|---|
| `latest.json`, `status.json`, `playback/index.json`, `broadcast/clock.json`, `broadcast/schedule.json`, `broadcast/replays/index.json`, `broadcast/honors/index.json`, `broadcast/live/<game>/manifest.json` | `Cache-Control: no-store` |
| Files that **exist** under `releases/`, `playback/games/`, `live/<game>/{head,sNNNN,end}.json`, `replays/<file>`, `honors/sNNN/<part>.json` | `public, max-age=31536000, immutable` |
| Anything else | `no-cache` |
| A file that does not exist yet | 404 with `no-cache` (never immutable: Caddy's `file` matcher makes the immutable header apply only to files that exist) |
| Directory URLs | no listing (`file_server` without `browse`) |
| Names starting with `.` | 404 |
| POST/PUT/DELETE/PATCH | 405 |
| `private/` | not under the served root at all |
| Time source | Caddy's `Date` header on every response (the viewer's clock offset) |

**Unvalidated risk (the largest one in this package):** I have no Caddy binary in my workspace, so this block has never been parsed by Caddy. The installer runs
`caddy validate` before reloading and puts the previous file back if it fails (exit 6). The header-ordering and `file`-matcher behaviour are proven only by
`rehearsal_check.py http` against a stand-in server in tests, then by the real check on the server (step 6.7). Treat the first Caddy validate/reload as part of the rehearsal.

## 5. Preparing the package (done here, nothing sent)

```
python3 tools/build_rehearsal_package.py --commit <final commit> --root /tmp/season/R1 --out OUT --force-overrun w041:450
```

- Refuses a store that was ever broadcast, any database file, and anything failing the pre-flight (all 47 events built, 400 games in windows, honors parts, ~48 h plan, no clock).
- `--force-overrun w041:450` gives window w041 a 450 s nominal slot (≈ 345 s real overrun), so the rehearsal passes through **one genuine real-time overrun**
  (later items shift by 345 s). Only the package's copy of `plan.json` changes.
- Output: one `.tar` (~250 MB) and a `.sha256`. Inside: `repo/` (the commit's own files via `git archive`), `store.tar.gz`, `PACKAGE.json`, `SHA256SUMS`.

Rehearsed here against a scratch tree with stand-in `caddy`/`systemctl` and the real package: checksum OK → rollback-copy gate → DNS gate → disk → code → store →
13 pre-flight checks pass → units installed (not enabled) → Caddy validate + reload (stub) → "Nothing is running". Accidental start with the installed (past) deadline: pauses, exit 0.
Refusal paths are in `tests/test_rehearsal_deploy.py` (7 tests): damaged package, production paths, existing folder, no recent rollback copy, wrong DNS, failed pre-flight (exit 5, Caddy and systemd untouched), uninstall guards.

## 6. Install — only after you approve (each step reversible; stop at any failure)

1. **Upload** the `.tar` and `.sha256` to `/root/` on the Droplet; `sha256sum -c`; `tar -xf`. Read `PACKAGE.json` (commit, store facts, caps).
2. **Inspect only** (read-only): `sh deploy/website/inspect_server.sh` and `inspect_dns.sh` from the unpacked `repo/`.
3. **DNS:** add an `A` record `rehearsal` → the Droplet's IPv4 at IONOS (manual, yours). Wait until `getent hosts rehearsal.diamondfootballleague.com` shows it. The installer refuses to go on otherwise.
4. **Rollback copy:** `sh repo/deploy/website/snapshot_config.sh`, plus a DigitalOcean snapshot. Save the production fingerprint:
   `python3 -I repo/tools/cap_state.py fingerprint --league /srv/dflv2/league/<db> --feed /srv/dflv2/feed --out /root/rehearsal-before.json` (read-only) and
   `python3 repo/tools/verify_deployment.py https://diamondfootballleague.com --production https://diamondfootballleague.com --save-baseline /root/prod-baseline.json`.
5. **Install:** `sh repo/deploy/rehearsal/install_rehearsal.sh <unpacked package dir>` (as root). It stops with a distinct exit code at the first failed gate (2 package/paths, 3 no rollback copy, 4 DNS, 5 pre-flight, 6 Caddy). It never starts or enables anything.
6. **Prove the server rules before the daemon runs:** `python3 -I /opt/dflv2-src/<commit>/tools/rehearsal_check.py http --url https://rehearsal.diamondfootballleague.com --before-start`
   (every feed URL must be 404 with no-cache, no listing, 405 on writes, no private names). Re-run production verification: `verify_deployment.py … --baseline /root/prod-baseline.json` must show no change.
7. **Stop here for your go.** Nothing is running; the site at the rehearsal host shows the viewer with no feed.

## 7. Start — a separate approval

1. Choose the start time (suggest a morning, so the first hours are watched and the Bowl/Honors points fall in waking hours; see 8 for landmarks).
2. Set the deadline in `/etc/dflv2/rehearsal.env`: `REHEARSAL_DEADLINE=<start + 54 h, UTC ISO>` (the pre-flight rejects > 60 h or in the past).
3. `systemctl start dfl-rehearsal dfl-rehearsal-monitor` (start only; never `enable`). Daemon starts at position 0; the first window is at 1 h 10 min.
4. First-hour checklist: `broadcast_run.py status --root /srv/dflv2-rehearsal/private` shows `running` and an advancing position; `https://…/feed/status.json` shows `source: broadcast`, `moving: true`; `watch.log` has no findings; `rehearsal_check.py http` (without `--before-start`, with `--store`) passes; open the site on a laptop and a phone: header reads "Release prepared …".
5. If a start happens by accident with the past deadline: the daemon pauses at position ≈ 0.2 s and exits 0. Nothing is lost; set the real deadline, `broadcast_run.py resume`, start again.

## 8. Drills during the run (each has an expected result; landmarks shift by the total paused time, so read the position first)

Position 0 is the daemon's first start. Nominal landmarks: first window 1.17 h; **w020 at 9.53 h** (09:31); **w041 (forced overrun) at 18.54 h**; **Bowl ceremony and rings at 46.90 h**, Coronation 47.33 h, **Honors at 47.50 h** (Tiara +0, halls +10 min, fan-entrant +20 min), plan end 48.0 h. These are the boundaries the frontend was checked at in virtual time. The w041 forced overrun adds 345 s to everything after it; every drill pause adds its own length.

| Drill | How | Expected |
|---|---|---|
| Pause mid-game (≈ w020) | `broadcast_run.py pause --root …` | `status.json` says `paused` within ~3 s on an open page; live ticker freezes at the same play; `resume` continues at the same play; no file appears early |
| Pause between windows | same, in a gap | same; schedule slides by the paused time |
| Real overrun (w041) | nothing to do | window ends 345 s late; later items shift; no 404 loops; monitor silent |
| Hard kill | `systemctl kill -s KILL dfl-rehearsal` (the daemon only; the monitor keeps running) | `Restart=on-failure` restarts in 5 s; clock resumes from where it stopped (it never runs ahead of real time during the gap); no stale `.tmp-` files; exit 75 never happens (single instance) |
| Phone | real phone on cellular + Wi-Fi at a boundary (a game start, a ring, Tiara) | page keeps time, no spoiler, "Release prepared" label (no age), replays link only after the window |
| Monitor | `tail -f watch.log` | zero lines; any line is a finding |

Not claimed: zero spoilers. Whole-play segments can still be fetched by someone who guesses their URL once released; the guessed-future-URL sample must always 404, which is what the monitor checks.

## 9. During and after: acceptance and proof production is unchanged

- Daily: `watch.log` empty; `evidence/` empty; `du -sh /srv/dflv2-rehearsal/feed` well under 3 GB; `systemctl status` shows the daemon `active`, memory under cap.
- At the deadline the daemon logs `{"deadline":true}`, stays paused, exits 0. The monitor keeps running until you stop it. Run `rehearsal_check.py http` one last time.
- Production proof: `cap_state.py fingerprint …` again and `cap_state.py diff /root/rehearsal-before.json /root/rehearsal-after.json` (exit 0); `verify_deployment.py … --baseline` (no change); event 188 and the release name unchanged; the league service and timers exactly as before.
- Keep `/srv/dflv2-rehearsal/monitor/` for review.

## 10. Rollback, from lightest to heaviest

1. **Stop the run:** `systemctl stop dfl-rehearsal dfl-rehearsal-monitor`. Pause is also safe: `broadcast_run.py pause`. Data stays.
2. **Take it out, keep the data for inspection:** `sh /opt/dflv2-src/<commit>/deploy/rehearsal/uninstall_rehearsal.sh` — removes both units, removes the Caddy file (copy kept in `/root/dfl-rollback/removed/`), validates and reloads Caddy (puts the block back and exits 5 if Caddy then rejects the rest of the config).
3. **Take it out completely:** the same with `--purge` — also deletes `/srv/dflv2-rehearsal`, the code directory this install added, and `/etc/dflv2/rehearsal.env`. It refuses any path that is or is inside `/srv/dflv2`, `/var/www`, `/`, or lacks "rehearsal" in its name.
4. **Remove the DNS record** at IONOS (manual).
5. **Restore the configuration** if something unexpected changed: `sh deploy/website/rollback.sh --config /root/dfl-rollback/<timestamp>`; last resort, the DigitalOcean snapshot.

Production (website, feed, league, backups) is not touched by 1–4; the uninstaller does not reference those paths except to refuse them.

## 11. Evidence policy for failures

- The monitor keeps the **first** full evidence file of every violation kind (file lists, positions, headers) and logs every later one as one JSON line; nothing is overwritten by reruns.
- The unidentified `test_site_broadcast` failure (seen once under CPU saturation in the previous task, not reproduced in 6 reruns) **stays open**. a loop here (outside the repository) runs the test repeatedly under CPU load and keeps the full output of any failing run. If it fails again, the exact assertion and full output are reported unedited.

## 12. Decisions needed from you

1. **Hostname:** a public `rehearsal.diamondfootballleague.com` (needs an IONOS record; anyone who finds the URL can watch a synthetic season; the page is `noindex`) versus no DNS and viewing through an SSH tunnel (safer, but phones cannot be tested). I recommend the hostname.
2. **Include the forced real-time overrun (w041)?** Recommended: yes — it is the one boundary virtual time cannot fully prove — but it means the run is not the pure nominal schedule.
3. **Start time** and who watches the first hour and the drills.
4. **Accept the unvalidated Caddy block as part of the rehearsal**, with automatic revert.

## 13. Known risks and limits

- Caddy block not validated by a real Caddy until the install (section 4).
- The package pins code including the rehearsal tooling; the daemon itself is the same as in `971fa5b` except the opt-in `--deadline`/`--max-public-gb` flags.
- Real-time behaviour over 54 h is exactly what is untested; that is why it is capped and watched.
- The synthetic season does not exercise multi-year carry-over, the league's real cadence, or the live league at 188.
- Phone performance is not measured by any tool here.
- The monitor reads the same private store the daemon uses; if its own Oracle disagreed with the daemon because of a shared bug, both would be wrong in the same way. Mitigation: the Oracle is `broadcast_season_check.py`, written independently of the daemon's materializer, and verified in virtual time to agree.
