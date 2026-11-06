# Capped 48-hour broadcast rehearsal — package, guard, server rules, rollback (FOR REVIEW)

Status: **prepared, not installed, not started.** Nothing here touches production (`/srv/dflv2`, the website, the league at event 188, the daily timers,
the backups). Every step that changes the server is a separate step you approve. No snapshot is bought, no Droplet is shut down, nothing is purged.

Candidate: `971fa5b` (broadcast integration + status fix + release label) plus the backend rehearsal tooling on branch `backend/rehearsal-package-2026-11-05`.
`git diff 971fa5b HEAD -- site src` is empty: no frontend change, no simulation, season or broadcast-library change. The only daemon change is
`tools/broadcast_run.py` (opt-in `--deadline`, `--max-public-gb`, `--guard`).

Plan agreed: public hostname `rehearsal.diamondfootballleague.com`, with the forced real-time overrun in window w041.

## 1. What counts as success

The rehearsal is one real-time run of a complete, never-broadcast 47-event season (106 windows, 400 games, ~48 h). **It passes only if it completes all 47 events and
all 400 games correctly**, and everything below holds at the end. `rehearsal_check.py verdict` (section 9) decides; it prints exactly one of:

| Result | Meaning |
|---|---|
| `COMPLETE` (exit 0) | the broadcast reached its last item; 47 of 47 events published; 400 of 400 games released with their replays; the public folder is *exactly* what the plan releases (0 missing, 0 extra files); **zero violation entries** in the monitor log; **no automatic pause** (a planned monitor-stop drill is the one exclusion); the three `http` rule checks passed (start, middle, end); production fingerprint unchanged |
| `INCOMPLETE` (exit 2) | nothing wrong so far but not finished. **Reaching the 54-hour deadline before completion is an incomplete run, not a pass.** Nothing is retro-fitted: a longer deadline means a new approval |
| `FAILED` (exit 1) | any violation entry, any automatic pause (other than the drill), or a wrong file |

Time budget: 48.0 h plan + 345 s (w041 overrun) + every pause (drills, trips, waiting for you) must fit in the 54 h wall-clock cap, i.e. about 5.9 h of slack. Time
spent paused after a trip counts against it, so a trip left unattended overnight can by itself make the run incomplete.

**Abort** (stop, keep everything, report) on: any spoiler-class trip (`early_or_unknown_file`, `future_url_served`, `pointer_wrong`, `private_exposed`); the daemon
exiting with an error twice in an hour; anything touching production.

## 2. The caps (enforced by code or unit settings, not by discipline)

| Cap | Value | Enforced by |
|---|---|---|
| Wall-clock | deadline = start + 54 h. At the deadline the daemon **pauses and exits 0**, and systemd does not restart it. The installed env file holds a deadline **in the past**, so an accidental `start` pauses at once (rehearsed: paused at 0.2 s, exit 0) | `broadcast_run.py --deadline` |
| Free disk | **6 GB everywhere.** The installer refuses unless free ≥ 6 GB **plus** the 3 GB the public folder may grow to; the pre-flight repeats it; the monitor stops the broadcast if free space falls below 6 GB | `install_rehearsal.sh`, `rehearsal_check.py preflight` / `watch --min-free-gb 6` |
| Public folder | 3 GB (a full season measured 0.78 GB / 59,290 files in virtual time) | daemon `--max-public-gb 3` (exit 70, no restart) and the monitor (stops the broadcast) |
| Daemon memory | 600 MB (`MemoryMax`); the monitor stops the broadcast at 90 % of the limit | unit + `watch --cgroup` |
| Restarts | 3 starts in an hour, then the unit stays failed | `StartLimitBurst` |
| Writes | the daemon writes only `private/`, `feed/` and `monitor/`; the monitor writes only `monitor/` | `ProtectSystem=strict`, `ReadWritePaths` |
| Timers | none are created; units are installed **disabled** | no `.timer` ships |
| League | never mounted or read; the package contains no league database | package builder refuses `.db`/`.archive`, clock, lock, PAUSE |
| Concurrency | one daemon per store | flock; a second start exits 75 |

## 3. The guard: automatic stop, evidence, explicit resumption

The monitor (`dfl-rehearsal-monitor`) compares the public folder with an independent oracle every 30 s, probes the live site, and writes one JSON line per event to
`monitor/watch.log`. **Normal entries are expected:** `{"start"}`, one `{"heartbeat": true, "ok": true, ...}` per 30 s cycle, `{"stop"}`. They are not problems. **A healthy
log is therefore never empty and contains zero lines with a `"violation"` key.**

| Kind | Examples | Effect |
|---|---|---|
| Spoiler-class | a file the position does not allow; a guessed future URL answering anything but 404; `latest.json` pointing at the wrong release; a private file reachable by path traversal | **trip** |
| Resource | free disk < 6 GB; public folder > 3 GB; daemon memory > 90 % of its limit | **trip** |
| Monitor failure | an exception in the monitor; the site unreachable for 3 cycles in a row; the monitor cannot write its latch; the monitor dies or hangs | **trip** (the monitor writes the latch; if it cannot, the *daemon* trips itself when the heartbeat goes stale) |
| Other | a missing due file, a stale temp file, wrong cache header, clock/status mismatch, release going backwards | logged as a violation (the run can no longer be `COMPLETE`) but the broadcast keeps running |

> **Correction 2026-11-06 (found by the live drill, `docs/FRESH_REHEARSAL_2026-11-06.md`):** a stale `.tmp-` file is logged as `stale_tmp` (no trip) but, in the full pass, it is also a file the broadcast position does not allow, so it **also** raises `early_or_unknown_file`, which is spoiler-class and trips the broadcast. That was the behaviour of the installed monitor and is unchanged by the temp-file fix; the row above understated it.

What a trip does, in order: (1) `monitor/evidence/trip-<time>-<kind>/` gets `evidence.json` (finding, position, store clock, public clock, status, pause flag) and
`public_listing.txt.gz` (every public file with size and time); (2) `monitor/TRIPPED` is written (the first trip of a stoppage is kept; later ones only append to
`trips.jsonl`); (3) the daemon, within one second, creates `PAUSE` and stays paused. The process stays up and `status.json` says paused, so viewers see a pause, not an error.
Nothing is deleted. Evidence is never overwritten, and there is no command here that removes it.

Detection latency: up to 30 s for the whole-tree comparison and the traversal slice (12 of ~290 paths per cycle). A trip stops *further* release within a second of
the latch; it cannot recall a file that was already public in those seconds, which is why the evidence listing exists. Monitor death: heartbeat is refreshed every 5 s
while the monitor makes progress; the daemon trips itself after 120 s without it (240 s grace after its own start).

**Explicit resumption (two commands, never automatic):**

```
# 1. read why, keep a copy of the journal with the evidence
cat /srv/dflv2-rehearsal/monitor/TRIPPED; ls /srv/dflv2-rehearsal/monitor/evidence/
journalctl -u dfl-rehearsal -u dfl-rehearsal-monitor --since "-3h" > /srv/dflv2-rehearsal/monitor/evidence/journal-$(date -u +%Y%m%dT%H%M%SZ).txt
# 2. fix the cause. A spoiler-class trip is an ABORT (section 1), not a resume.
# 3. release the latch (refused without a reason of 10+ characters, or while the monitor is not alive; the latch moves to evidence/released/, nothing is deleted)
sudo -u dflv2 python3 -I /opt/dflv2-src/<commit>/tools/rehearsal_check.py release --guard /srv/dflv2-rehearsal/monitor --reason "freed 8 GB by ..., checked listing"
# 4. resume (refused while a latch exists)
sudo -u dflv2 python3 -I /opt/dflv2-src/<commit>/tools/broadcast_run.py resume --root /srv/dflv2-rehearsal/private --guard /srv/dflv2-rehearsal/monitor
```

Removing `PAUSE` by hand or `resume` without `--guard` does not bypass it: while a latch exists the daemon puts `PAUSE` back at its next tick (at most a one-second leak).

Proven by tests (`tests/test_rehearsal_guard.py`, 18): each trip kind pauses the daemon and keeps first evidence; a violation outside the trip kinds does not stop it; the daemon
trips itself on a missing or stale heartbeat and not on a fresh one; a hung monitor stops heartbeating; a monitor that cannot write its latch exits; `release`/`resume` refusals;
the verdict. **Also run for real on a scratch install of the actual package** (real store and 400-game plan, real daemon and monitor processes, a stand-in HTTP server, `ALLOW_NON_ROOT`, nothing on the server): the monitor was killed with SIGKILL and the daemon tripped itself and paused after 120 s; `resume --guard` was refused; `release` was refused without a reason; `release --drill` then `resume --guard` brought the clock back to running; a fresh 110 s run showed 4 heartbeat lines, 0 violations and no evidence directory. That run also found a start-up race (a spurious `missing_file` in the first second), now fixed and tested. **Not proven live** until the rehearsal: a spoiler trip against the real daemon (the daemon itself removes a stray public file within a second, so it cannot be staged safely) and the
disk/memory trips on the server. The monitor-stop drill (section 8) exercises the trip → release → resume path for real.

## 4. Layout (everything new; one added Caddy file is the only edit outside it)

```
/opt/dflv2-src/<40-hex commit>/        pinned code (new directory, never overwritten)
/srv/dflv2-rehearsal/private/store/    the unreleased season, plus CANARY.txt (a random token, never to be served) — NOT under any served root
/srv/dflv2-rehearsal/feed/             the daemon's public folder; this is the feed file server's root
/srv/dflv2-rehearsal/site/             copy of site/ from the pinned commit; the site file server's root
/srv/dflv2-rehearsal/monitor/          watch.log, heartbeat, TRIPPED, trips.jsonl, drills.jsonl, evidence/ (kept for review)
/etc/dflv2/rehearsal.env               REHEARSAL_DEADLINE=...
/etc/systemd/system/dfl-rehearsal.service, dfl-rehearsal-monitor.service      (installed, not enabled)
/etc/caddy/conf.d/dflv2-rehearsal.caddy                                       (host rehearsal.diamondfootballleague.com)
```

The installer refuses any rehearsal folder that is, is inside, or contains `/srv/dflv2`, `/var/www` or `/`; requires "rehearsal" in the folder name; refuses an existing folder.
The units run as `dflv2`, the user production's export services already use; it is created as a no-login system user only if missing.

## 5. Server cache and directory rules (Caddy) — file `deploy/rehearsal/Caddyfile.rehearsal.template`

**Rooting (corrected):** the feed file server is rooted **directly at `/srv/dflv2-rehearsal/feed`**. `handle_path /feed/*` strips the `/feed` prefix, so `/feed/latest.json`
is read from `/srv/dflv2-rehearsal/feed/latest.json`. The earlier draft rooted at `/srv/dflv2-rehearsal` with the `feed/` path under it; that has been removed, so no served root has
`private/` beside or above it that a path could name. Everything else is served from `/srv/dflv2-rehearsal/site`. Explicit 404s sit in front as a second layer: names starting with a dot
(which includes any decoded `..` segment), `/private`, `/monitor`, and `CANARY*`, `PAUSE`, `daemon.lock`, `clock_log.jsonl`, `plan.json`, `builds.json` anywhere.

| Kind | Header |
|---|---|
| `latest.json`, `status.json`, `playback/index.json`, `broadcast/clock.json`, `broadcast/schedule.json`, `broadcast/replays/index.json`, `broadcast/honors/index.json`, `broadcast/live/<game>/manifest.json` | `Cache-Control: no-store` |
| Files that **exist** under `releases/`, `playback/games/`, `live/<game>/{head,sNNNN,end}.json`, `replays/<file>`, `honors/sNNN/<part>.json` | `public, max-age=31536000, immutable` |
| Anything else, and every error (404/403/405) | `no-cache` (also set by `handle_errors`) |
| A file that does not exist yet | 404, never immutable (the `file` matcher makes the immutable header apply only to existing files) |
| Directory URLs | no listing |
| POST/PUT/DELETE/PATCH | 405 |
| Time source | Caddy's `Date` header on every response |

**Validation with real Caddy — what has and has not happened.** I tried to obtain Caddy 2.11.7 (your server's version) in my workspace to validate and test this block. Both routes
were refused by the session's network policy (Go module proxy: 403; GitHub release download: not enabled), and I did not work around it. So **this block has not been parsed by
a real Caddy by me.** It is validated by the installer, on the server, with the server's own Caddy, in this order, and nothing is changed until the first two pass:

1. `caddy validate` on the rendered block **alone** — failure: exit 6, nothing installed or changed.
2. Block copied in; `caddy validate` on the whole configuration — failure: block removed, exit 6, Caddy not reloaded.
3. `systemctl reload caddy` (never restart).
4. **Live check, before the installer says INSTALLED** (`rehearsal_check.py http --before-start --canary …`, retried up to 180 s for the certificate): cache headers per file kind, no directory listing, no
   private names, 405 on writes, and **~290 path-traversal requests aimed at the private canary** — raw `../`, `%2e%2e/`, `%2E%2E/`, `..%2f`, `%2e%2e%2f`, `.%2e/`, `%2e./`, double-encoded `%252e%252e%252f`, backslash `..%5c` and `..\`, overlong UTF-8
   `%c0%ae%c0%ae/`, `....//`, `..;/`, null `..%00/`, at depth 1–3 under `/feed/`, `/feed/broadcast/` and `/`, plus private file names (plan, builds, clock, watch log, `/etc/passwd`). Anything answering 200 or returning private content fails.
   Failure: block removed, Caddy reloaded, exit 7, output kept in `monitor/install-check.log`.

**Permissions and access checks (added after the first live attempt; exit 8).** The first live install stopped at the reload (exit 6, block taken out again, production untouched): it had been started from a
shell with `umask 027`, so the Caddy snippet, the installed code and part of the viewer were readable by root only, and `caddy validate` (run as root) could not see the problem. The installer now (a) sets `umask 022`
itself and sets every mode explicitly: code and `site/` world-readable (only these two are changed recursively; they are public), `$REH` and the empty `feed/` 755, `private/` and `monitor/` **700**, owned by the service
user and never made public or recursed, unit files, env file and Caddy snippet 644; (b) proves access **as the real users** with `runuser` (root can read everything): the service user reads all the code and writes
`feed/`, `private/`, `monitor/`; Caddy's user (`systemctl show caddy -p User`, or `CADDY_USER`) reads the viewer and the snippet, runs `caddy adapt` on the whole configuration, and **cannot** read or enter `private/`,
`monitor/` or the canary. Every check walks all parent directories, so an existing unreadable parent (for example `/opt/dflv2-src` left at 750) is named in the message. A real install that cannot run a check
(missing user, no `runuser`) refuses instead of skipping. A failure before Caddy is touched changes nothing in Caddy; a snippet failure removes the block again and does not reload. Nothing outside what this install created is chmod-ed.
`tests/test_rehearsal_umask.py` runs the whole install suite under umask 027 and checks access as the real unprivileged users `www-data` and `daemon` (it needs root; it is skipped, and says so, without). Run against
the previous installer, its success test fails at the Caddy-user check.

The same traversal slice keeps running in the monitor all through the rehearsal (a spoiler-class trip if anything leaks). In tests, a confining stand-in server passes all paths and a deliberately careless one (double-decoding, no
confinement) is caught — that proves the *probe*, not Caddy. If you can supply a Caddy 2.11.7 binary (or run `caddy validate` locally), I will run the same tests against it before you approve the install.

## 6. The package (built here; nothing sent anywhere)

```
python3 tools/build_rehearsal_package.py --commit <final commit> --root /tmp/season/R1 --out OUT --force-overrun w041:450
```

- Refuses a store that was ever broadcast, any database file, an existing canary, and anything failing the pre-flight (all 47 events built, 400 games in windows, honors parts, ~48 h plan, no clock).
- Adds the private canary (`store/CANARY.txt`, `DFL-REHEARSAL-CANARY-<32 hex>`) to the package's copy of the store only.
- `--force-overrun w041:450` gives window w041 a 450 s slot (a genuine 345 s real-time overrun; everything after shifts by 345 s). Only the package's `plan.json` changes.
- Output: one `.tar` and a `.sha256`. Inside: `repo/` (the commit's own files via `git archive`), `store.tar.gz`, `PACKAGE.json`, `SHA256SUMS`.
- The exact byte size and full SHA-256 of the package are in the `.sha256` file beside it and in the review message. **Verify `sha256sum -c` before upload and again on the server.** (They cannot be printed inside this document, which is part of the commit the package pins.)

Rehearsed here against a scratch tree with stand-in `caddy`/`systemctl` and the real package: checksum → rollback-copy gate → DNS gate → Caddy-alone gate → disk → code → store → pre-flight (15 checks incl. canary and guard) →
units installed (not enabled) → Caddy validate and reload (stub) → live server check → "Nothing is running". Refusal and failure paths are in `tests/test_rehearsal_deploy.py` (12 tests): damaged package, production paths,
existing folder, no recent rollback copy, wrong DNS, Caddy rejecting the block alone (exit 6, nothing changed), failed pre-flight (exit 5), a server breaking a rule (exit 7, block taken out again), an unreachable site (exit 7),
too little disk, uninstall guards.

## 7. Install — only after you approve (each step reversible; stop at any failure)

1. **Upload** the `.tar` and `.sha256` to `/root/`; `sha256sum -c`; `tar -xf`. Read `PACKAGE.json`.
2. **Inspect only:** `sh repo/deploy/website/inspect_server.sh` and `inspect_dns.sh`.
3. **DNS:** add the `A` record `rehearsal` → the Droplet's IPv4 at IONOS (manual, yours). Wait until `getent hosts rehearsal.diamondfootballleague.com` shows it.
4. **Rollback copy (free, on the server):** `sh repo/deploy/website/snapshot_config.sh` (copies the Caddyfile and `dfl-*` units; the installer refuses without one newer than 24 h). A DigitalOcean snapshot is **optional and yours to decide; this plan neither requires nor buys one.**
   Save the production fingerprint and baseline (read-only):
   `python3 -I repo/tools/cap_state.py fingerprint --league <league db> --feed /srv/dflv2/feed --out /root/rehearsal-before.json` and
   `python3 repo/tools/verify_deployment.py https://diamondfootballleague.com --production https://diamondfootballleague.com --save-baseline /root/prod-baseline.json`.
5. **Install:** `sh repo/deploy/rehearsal/install_rehearsal.sh <unpacked package dir>` as root. Exit codes: 2 package/paths/disk, 3 no rollback copy, 4 DNS, 5 pre-flight, 6 Caddy rejected the block (nothing changed or block removed), 7 live server check failed (block removed again), 8 an access check failed as the real service users (see section 5; Caddy not reloaded). It never starts or enables a unit.
   **If an install stops half way** the installer refuses to run over the existing `/srv/dflv2-rehearsal`. Do not purge: `sh repo/deploy/rehearsal/quarantine_rehearsal.sh` moves the inactive leftovers (the rehearsal folder, the code directory named in `.installed_commit`, the two unit files, `/etc/dflv2/rehearsal.env`) into `/root/rehearsal-quarantine/<time>` (700; a rename on the same filesystem, so owners, modes and times are untouched; `manifest.tsv` records every original path, `listing.tsv.gz` every file; the moved tree is verified against that listing), refuses if a unit is active or enabled or a Caddy block is installed, deletes nothing, and `--restore <dir>` puts everything back exactly (refusing if an original path exists again). It never touches production, Caddy, the rollback copies, evidence, DNS or other packages.
6. **Confirm production unchanged:** `verify_deployment.py … --baseline /root/prod-baseline.json` shows no change.
7. **Stop here for your go.** Nothing is running.

## 8. Start, and the drills — separate approval

**Start.** Choose the start time (morning). Set `REHEARSAL_DEADLINE=<start + 54 h, UTC ISO>` in `/etc/dflv2/rehearsal.env` (the pre-flight rejects > 60 h or in the past). Start the monitor first, then the daemon:
`systemctl start dfl-rehearsal-monitor dfl-rehearsal` (never `enable`). The daemon starts at position 0; the first window is at 1 h 10 min.

First-hour checklist: `broadcast_run.py status` shows `running` and an advancing position; `https://…/feed/status.json` shows `source: broadcast`, `moving: true`; `monitor/watch.log` shows a `start` line and a **`heartbeat` line every ~30 s with `"ok": true`**
and **no `"violation"` lines** (`grep -c '"violation"' watch.log` is 0); `monitor/heartbeat` is refreshed every 5 s; `rehearsal_check.py http … --store …` passes; the page opens on a laptop and a phone with "Release prepared …".
An accidental start with the past deadline pauses at ≈ 0.2 s and exits 0; set the real deadline, `resume --guard`, start again.

Landmarks (nominal; each pause shifts them, w041's overrun adds 345 s after 18.5 h): first window 1.17 h; **w020 at 9.53 h**; trading deadline 18.10 h; **w041 at 18.54 h (forced overrun)**; **Bowl ceremony and rings 46.90 h**; Coronation 47.33 h; **Honors 47.50 h** (Tiara +0, halls +10 min, fan-entrant +20 min); done at ≈ 47.92 h.

| Drill | How | Expected |
|---|---|---|
| Pause mid-game (~w020) | `broadcast_run.py pause --root …` | `status.json` says paused within ~3 s; the live ticker freezes at the same play; `resume --guard …` continues at the same play; no file appears early |
| Pause between windows | same, in a gap | same; schedule slides by the paused time |
| Real overrun (w041) | nothing to do | window ends 345 s late; later items shift; no 404 loops; zero violations |
| Hard kill | `systemctl kill -s KILL dfl-rehearsal` | restarts in 5 s; clock resumes from where it stopped (never ahead of real time); no stale `.tmp-` files |
| **Monitor-stop (live test of the guard)** | in the first hours: `systemctl stop dfl-rehearsal-monitor` | within ~2–4 min the daemon trips itself (`TRIPPED` kind `monitor_silent`, source `daemon`) and pauses. Then start the monitor, wait for a fresh heartbeat, `release --drill --reason "planned monitor-stop drill"`, `resume --guard …`. The verdict excludes this one drill (and only a daemon-made `monitor_silent` can be a drill) |
| Phone | real phone on cellular and Wi-Fi at a boundary (a game start, a ring, Tiara) | page keeps time, no spoiler, release label with no age, replay link only after the window |

Not claimed: zero spoilers. Whole-play segments can still be fetched by someone who guesses their URL once released; the guessed-future-URL sample must always 404, which the monitor checks every cycle.

## 9. During and after: the verdict and the proof that production is unchanged

- Daily: `grep -c '"violation"' monitor/watch.log` is **0**; `ls monitor/evidence/` shows no `trip-*` directories; `du -sh feed` well under 3 GB; `df` free space above 6 GB; `systemctl status` shows the daemon active, memory under cap.
- Run `rehearsal_check.py http --url https://… --store … --canary …` at the start, around hour 24 and after the end.
- At the deadline or on completion the daemon logs `{"deadline":true}` / is paused; the monitor keeps running. Then:
  `python3 -I rehearsal_check.py verdict --root /srv/dflv2-rehearsal/private --public /srv/dflv2-rehearsal/feed --log /srv/dflv2-rehearsal/monitor/watch.log --guard /srv/dflv2-rehearsal/monitor`
  must print `RESULT: COMPLETE`. `INCOMPLETE` (including "the deadline came first") is not a pass.
  (Run against the real 400-game tree from virtual time it printed COMPLETE with 59,290 files, 0 missing, 0 extra.)
- Production proof: `cap_state.py fingerprint …` again and `cap_state.py diff /root/rehearsal-before.json /root/rehearsal-after.json` (exit 0); `verify_deployment.py … --baseline` (no change); event 188 and the release name unchanged; the league service and timers as before.
- Keep `/srv/dflv2-rehearsal/monitor/` for review.

## 10. Rollback, from lightest to heaviest

1. **Stop the run:** `systemctl stop dfl-rehearsal` first, then `dfl-rehearsal-monitor` (stopping the monitor while the daemon runs trips it after ~2 min). `broadcast_run.py pause` is also safe. Data stays.
1b. **Set aside a half-finished install without deleting anything:** `sh deploy/rehearsal/quarantine_rehearsal.sh` (reversible with `--restore`; see step 5 of section 7).
2. **Take it out, keep the data for inspection:** `sh /opt/dflv2-src/<commit>/deploy/rehearsal/uninstall_rehearsal.sh` — removes both units and the Caddy file (copy kept in `/root/dfl-rollback/removed/`), validates and reloads Caddy (puts the block back and exits 5 if Caddy then rejects the rest of the config).
3. **Take it out completely (this deletes rehearsal data, so only when you decide):** the same with `--purge` — also deletes `/srv/dflv2-rehearsal`, the code directory this install added, `/etc/dflv2/rehearsal.env`. It refuses any path that is or is inside `/srv/dflv2`, `/var/www`, `/`, or lacks "rehearsal" in its name. **Not run now.**
4. **Remove the DNS record** at IONOS (manual).
5. **Restore the configuration** if something unexpected changed: `sh deploy/website/rollback.sh --config /root/dfl-rollback/<timestamp>`; a DigitalOcean snapshot only if you have chosen to take one.

Production (website, feed, league, backups) is not touched by 1–4; the uninstaller refers to those paths only to refuse them.

## 11. Evidence policy for failures

- The monitor keeps the **first** full evidence file of every violation kind and one snapshot directory per trip; later occurrences are one JSON line each. Nothing is overwritten.
- The unidentified `test_site_broadcast` failure (seen once under CPU saturation in the previous task, not reproduced in 6 reruns, nor in 30 further runs under load) **stays open**. If it fails again the exact assertion and the full, unedited output are captured and reported.

## 12. Decisions still needed from you

1. **Approve the install** (section 7) — after you have read this document and the package details in the review message. No install until then.
2. **Start time and who watches** the first hour and the drills (including the monitor-stop drill).
3. **Decide whether to supply a Caddy 2.11.7 binary** for a pre-install test here (otherwise the first real-Caddy validation is the installer's, with automatic revert).
4. **Decide about a DigitalOcean snapshot** (optional; not bought).

## 13. Known risks and limits

- The Caddy block has not been parsed by a real Caddy by me (section 5); the installer's gates and automatic revert are the protection. `handle_errors` / `handle_path` behaviour on 2.11.7 is the likeliest place for a first-run surprise, and the live check is built to catch it.
- A spoiler trip, the disk trip and the memory trip are proven in tests but not against the real daemon on the server (only the monitor-stop path is drillable live).
- A trip cannot recall a file already public; detection is within ~30 s.
- A trip left unattended consumes the ~5.9 h slack; the run then ends `INCOMPLETE`.
- Real-time behaviour over 54 h is exactly what is untested; that is why it is capped and watched.
- The synthetic season does not exercise multi-year carry-over or the live league at 188. Phone performance is not measured by any tool here.
- The monitor reads the same private store as the daemon. Its oracle (`broadcast_season_check.py`) was written independently of the daemon's materializer and agreed with it in virtual time, but a shared misreading of the plan would be shared.
