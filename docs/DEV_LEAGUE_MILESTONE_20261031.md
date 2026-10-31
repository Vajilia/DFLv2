# Development league milestone, 2026-10-31: what was built, measured and found

Deployed revision on the Droplet: **0971fb6431afbf15b330afff743b235fdb0b9d56** (recorded in `/srv/dflv2/DEPLOYED_REVISION.json`; `verify_revision.py`: ALL MATCH; `verify_deployment.py`: 33 PASS, 0 FAIL, 0 WARN against the production baseline).
The development league is installed **PAUSED**. Nothing advances until the owner approves a pace (below). Production (Caddy, dfl-live, dfl-control, dfl-chronicle) was not touched; the rollback copy `/root/dfl-rollback/20261031T065334Z` and the transfer branch `staging-package-5a6bf27` are kept.

## Built
* `tools/league_runner.py` — the one runner (flock; a second one exits 75), `PAUSED` file, `--pace`, `--until-events`, clean stop on SIGTERM, `runner_status.json`.
* `tools/league_control.py` — pause / resume / status.
* `src/dflsite/status.py`, `export_public_feed.py --skip-if-current` — `feed/status.json` heartbeat every minute, public-safe.
* The page shows: simulation date, last published update, and a health line (running / paused / stalled / last update failed / runner error / runner not responding).
* `deploy/website/`: `dflv2-league.service`, `run_league.sh` (+ `/etc/dflv2/league.env`), one-minute exporter timer, `install_league.sh` (league created once, paused).
* Tests: `test_league_runner.py` (real processes: kill -9, death inside a commit, pause/resume, pace, single instance), `test_site_status.py` (browser), deploy and publisher tests.

## Found during the deployment, and fixed
| Finding | Fix |
|---|---|
| (earlier four, see `STAGING_DEPLOYMENT_RECORD_20261031.md`) tar warning aborted the snapshot; seed unreadable under /root; Caddy second `root` ignored; verifier crash on a non-JSON pointer | fixed, regression tests |
| Snapshot every 8 events made one export replay a game week: **183 s** | snapshot after every event (default); export now ~5 s while a game week runs |
| **Pointer `manifest_sha256` matched no file** when an existing release was re-published with a new timestamp (caught by `verify_deployment.py`) | `publish.py` now points at the manifest bytes on disk; regression tests fail without the fix |

## Measured on the Droplet (1 vCPU, 2 GB)
| | |
|---|---|
| Game week event (`week_N`) | 82 s alone, 91 s with exporter and page requests running; 86 % of one CPU; peak 150–183 MB |
| Tour round | 17–23 s |
| Offseason events | 0.4–2.4 s |
| Playoff / Bowl / Coronation events | **not yet measured** (they come after week 19) |
| One season | 47 events: 19 weeks + 7 tour rounds + 4 playoff events + 17 others; estimated **33–36 min** of compute |
| Export (idle or while a week runs) | ~5 s wall, ~1.3 s CPU, ~100 MB |
| Page and production site while a week runs | 0.04–0.12 s per request (production alone: same range) |
| Disk growth | ~8–9 MB per game week (permanent archive), so about 170 MB per season; feed releases ~0.8 MB each, pruned |
| Idle (paused runner + timer) | memory in use 671 MB vs 615 MB before; disk 7 %; CPU ~0 |
| Kill -9 at a random moment, restart, on the Droplet | chain after resume equals the never-interrupted league's (9 events) |

## Proposed temporary development pace (for approval; **not enabled**)
Pace = least time between the *starts* of two events. One season is 47 events.
| Pace | One season takes | Seasons per day | Disk per day | Runner busy |
|---|---|---|---|---|
| 5 min | 3.9 h | ~6 | ~1 GB | ~15 % |
| **15 min (proposed)** | 11.8 h | ~2 | ~340 MB | ~5 % |
| 60 min | 47 h | ~0.5 | ~85 MB | ~1 % |
Proposal: **15 minutes, capped at 141 events (3 seasons, about 1.5 days)** so it stops by itself and the numbers can be reviewed before it runs longer. This is a development value only; it says nothing about final season pacing, and it is unrelated to the unbuilt real-time kickoff clock (a whole week's games appear together).
To enable after approval: `printf 'DFL_PACE=900\nDFL_UNTIL=141\n' > /etc/dflv2/league.env; systemctl restart dflv2-league; python3 -I /srv/dflv2/repo/tools/league_control.py resume`.

## Limits and open items
* Playoff, Bowl and Coronation event times, the season rollover, and long-run memory are unmeasured; the capped run is the way to find out.
* If engine code changes in a later deployment, the league refuses to open (the fingerprint protects it): redeploying code that changes `src/dfl` or `data/` means starting a new development league, or an explicit decision.
* `/srv/dflv2/league-old-snap8` (12 MB) is the first, discarded development league (snapshot every 8 events); safe to delete.
* The Droplet still reports "System restart required"; not rebooted, as instructed. The unexplained parity shift and the `StateMismatch` item remain open and tracked.
