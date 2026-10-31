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

## Not decided here

The pace (`DFL_PACE` in `/etc/dflv2/league.env`, read by `run_league.sh`) is a temporary development value only. The service ships with a conservative default (3600 s) and the league paused; `DFL_UNTIL` can cap the total events so the league stops by itself for review. The final season pacing is not set by any file in this repository.
