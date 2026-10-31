# Development league: how it runs, how to stop it, how it recovers

Scope: the **DFLv2 development league** behind preview.diamondfootballleague.com. It is separate from the production site and its runner and shares nothing with them.
It is a test of the simulation as it is built; it may be reset.

## The pieces

| Piece | What it does | Runs as |
|---|---|---|
| `dflv2-league.service` | **The one runner.** Opens `/srv/dflv2/league/league.db`, fires one event at a time (each event is committed before the next starts), waits so events start at least `DFL_PACE` seconds apart. Takes a lock, so a second runner exits at once (status 75). | `dflv2` |
| `dfl-feed-export.timer` → `.service` | Every minute: if the league has an event the live release lacks, copy the league file safely, build the public feed from the copy, publish it as a new immutable release, then move `feed/latest.json` atomically. Either way it rewrites `feed/status.json`. | `dflv2`, low priority |
| `feed/status.json` | Heartbeat for the page and for `verify_deployment.py`: when the exporter last looked, league events vs published events, last successful export, runner state. Public-safe: no paths, no process ids, no messages. | written by the exporter |
| Caddy | Serves files only. `latest.json` and `status.json` are never cached; a published release is cached for good. | unchanged |

## What the page shows

A line under the title says one of: *Simulation running; next event about …* · *Simulation paused / stopped. The league is not advancing.* · *Updates may be stalled …* (the exporter has not reported for 5 minutes) · *The last update attempt failed (type) … last good release …* · *The simulation runner stopped with an error (type)* · *The simulation runner is not responding* (running, but silent for 10 minutes). The page also always shows the **simulation date** and **last published update**.

## Pause, resume, status

    python3 -I /srv/dflv2/repo/tools/league_control.py pause  --reason "why"   # the runner finishes the event in progress, then waits
    python3 -I /srv/dflv2/repo/tools/league_control.py resume
    python3 -I /srv/dflv2/repo/tools/league_control.py status

Pause is a file (`/srv/dflv2/league/PAUSED`): it survives restarts and reboots. `install_league.sh` creates it, so **a fresh install never advances until someone resumes**.
`systemctl stop dflv2-league` stops the process cleanly (SIGTERM: it finishes the commit in progress).

## Restart recovery (tested with real processes)

* A process killed at a random moment (`kill -9`), or dying *inside* a commit, leaves a journal the next start heals; the finished league's event chain equals the chain of a league never interrupted. So no event happens twice and no result changes (`tests/test_league_runner.py`).
* The exporter works on a copy, so a crash or a full disk during an export leaves the live release untouched.
* If the engine code on disk differs from the code that wrote the league, the league **refuses to open** (that protects it): the runner reports `error`, the page says so. Do not edit around it. Either redeploy the matching revision or start a new development league on purpose.

## Measured on the Droplet (1 vCPU, 2 GB), 2026-10-31

* A **game week** event takes about **82 s** of CPU (one process, ~150 MB peak memory); offseason events take 0.4–2.4 s; a tour round about 23 s.
* The runner archive grows about **7 MB per game week**; the league file itself is small (2 MB after 12 events).
* An export of a league whose newest snapshot is current takes about 1 s and ~70 MB. If the snapshot is several events behind, the exporter replays them: with a snapshot every 8 events one export took 183 s. **The league therefore takes a snapshot after every event** (the default; `install_league.sh` no longer overrides it).
* While the runner is busy, the low-priority exporter gets about half a CPU; the web page and the production site answered in 0.09–0.25 s throughout.

## Installing

    sh /srv/dflv2/repo/deploy/website/deploy_pinned.sh <commit>      # code + site, verified against the commit
    sh /srv/dflv2/repo/deploy/website/install_league.sh              # numpy, league (once), units; league stays PAUSED

First publication into a feed that holds the old fixed preview needs `--allow-older` once (the new league has fewer events than the seed):
`EXPORT_ARGS="--allow-older" sh /srv/dflv2/repo/deploy/website/refresh.sh /srv/dflv2/league/league.db`.

## The capped development trial

Settings live in `/etc/dflv2/league.env` (`DFL_PACE`, `DFL_UNTIL`). **`DFL_UNTIL` is required**: a missing or malformed limit makes the service refuse to start (`none` removes it on purpose). The limit counts the league's *total* committed events and is checked before every event, so restarts and reboots cannot exceed it. The pace is a minimum gap between event *starts*, kept across restarts in `runner_status.json`: after any downtime at most one event fires, then the full gap is waited; there is no catch-up. At the limit the runner publishes nothing itself, stops (exit 0, no restart) and the page says *Review limit reached*; the exporter keeps running and publishes the final committed state. Every event appends a line to `/srv/dflv2/league/events.jsonl` (seconds, runner and machine memory, file sizes): private, for review.

## Not decided here

The pace (`DFL_PACE` in `/etc/dflv2/league.env`, read by `run_league.sh`) is a temporary development value only. The service ships with a conservative default (3600 s) and the league paused; `DFL_UNTIL` can cap the total events so the league stops by itself for review. The final season pacing is not set by any file in this repository.

## Error answers and caching (found 2026-10-31)

On the first live event the new release folder was private (HTTP 403 from the web server). The server had sent that 403 with the same one-year `immutable` caching headers as a good release, so a browser kept replaying it after the files were fixed. Three layers now stop that: the publisher makes each release folder readable (`publish.py`, tested); Caddy gives the `immutable` header only to a release file that exists and can be read, so any error answer keeps `no-cache` (`Caddyfile.template`, checked by `verify_deployment.py`); and the page asks again past its cache when a read fails and, if the first load still fails, keeps retrying every 10 seconds by itself with no hard refresh (`site/assets/app.js`, `tests/test_site_recovery.py`).

## Production cutover (2026-10-31)
`diamondfootballleague.com`, `www.diamondfootballleague.com` and `143-198-130-68.sslip.io` serve the DFLv2 viewer and the same `/srv/dflv2/feed` as `preview.diamondfootballleague.com`. The old proof-of-concept (dfl-live, dfl-control, dfl-chronicle, `/var/www/dfl`, the `/api` route) is stopped, disabled and no longer routed.
- Made by `deploy/website/cutover_production.sh` (rollback copy first; the old production block leaves the main Caddyfile; a drop-in `/etc/caddy/conf.d/dflv2-production.caddy` is rendered from the same template as the preview; `caddy validate` before reload; local check; old services disabled last; any failure restores the old configuration). It writes `/srv/dflv2/PRODUCTION_CUTOVER.json`.
- Rollback: `sh /srv/dflv2/repo/deploy/website/rollback_cutover.sh <rollback copy>` (`--dry-run` first: validates the saved Caddyfile and checks the saved units). The copy is private (`/root/dfl-rollback/<stamp>`, mode 0700; it holds the old application and its configuration, including credentials, in `old_application.tgz`, never to be printed or served).
- The first live attempt (15:01Z) stopped itself and restored the old configuration: the drop-in was written 0600, so Caddy could not read it at reload although `caddy validate` (run as root) passed. Fixed in df9d416 with a test.
- Stale rules after the cutover (found and fixed 2026-10-31): the release name is `e<events>-<first 10 hex of a hash over every file>`, and the hash includes `rules.json`, so changed rules DO give a different release name. The actual defect was the exporter's skip check (`--skip-if-current`), which compared only event counts: when a deploy changed the rules summary (spec 8.58 to 8.61) while the league sat at event 12, nothing was republished until event 13. Moving the old e00013/e00073 folders aside (to `/root/feed-aside`, kept) was a workaround that was not needed for the name to differ; they can stay there.
- The fix: the exporter records (`.export_inputs.json`, a private dotfile in the feed root, never served) which rules summary, spec edition, builder code and format the live release was built with, and skips only if the league has no new event AND those are unchanged. `publish()` now reuses an existing release folder only if its file index equals the one just built and every file on disk matches it byte for byte; otherwise it refuses and leaves the pointer alone. Published releases are never rewritten.
- Cosmetic, unchanged: if identical content is re-published into an existing release, the manifest keeps its first `generated_at`, so the page's "Last published update" shows that older time.
