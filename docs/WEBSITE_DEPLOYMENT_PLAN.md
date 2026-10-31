# Website deployment plan (prepared 2026-10-30, staging package added the same evening; nothing is deployed, nothing is merged)

> **Staging first.** The step-by-step procedure for `preview.diamondfootballleague.com` (inspection, DNS, rollback copy, install, verification,
> rollback) is `docs/STAGING_RUNBOOK.md`, and its package is built by `tools/build_staging_package.py`. The production hostname is untouched by it.
> What changed in the design since the first version of this plan: one publisher at a time (an `flock` held from before the snapshot until after
> the pointer moves), pruning only after a one-hour grace period, a viewer that recovers by itself when its release has been pruned, snapshots of the
> league and its archive by SQLite's online backup, Coronation scores recorded permanently with verified archive recovery for earlier seasons, and a
> Map page that shows the official DFL map (alignment of 30 October 2026). Sections 1 to 7 below are the original plan and are still the plan for cutover.

Scope: put the DFLv2 viewer (`site/` plus a published feed) on the existing Droplet at https://diamondfootballleague.com/, replacing the
proof-of-concept site, **after the owner has reviewed the local preview and says go**. Until then this document is a plan and the scripts in
`deploy/website/` are untested on the real server.

## 1. What is known and what is not

Given by the owner: website https://diamondfootballleague.com/; old repository Vajilia/DFL; DigitalOcean Droplet 608448246, Ubuntu 24.04,
1 CPU, 2 GB RAM, 50 GB disk; deployment documentation describing Caddy, `/opt/dfl`, `/var/www/dfl` and a separate live runner.

Read from the old repository (`deploy/` in Vajilia/DFL, read-only clone): Caddy serves `/var/www/dfl` as static files with gzip and noindex headers; `/api/*`
(the Pause/Resume switch) is behind `basic_auth` and proxied to `dfl-control` on 127.0.0.1:8081; three systemd units `dfl-live`, `dfl-control`,
`dfl-chronicle` run the old simulation from `/opt/dfl`.

**Not verified.** This session has no access to the Droplet (no SSH, no DigitalOcean credentials), so the live Caddyfile, the unit files, the
disk and memory in use, the domain's DNS and the current contents of `/var/www/dfl` were not seen. The plan below is written against the old
repository's documentation and must be checked against the server first: run `deploy/website/inspect_server.sh` there (read-only, masks password hashes)
and send the output back. Anything that differs changes this plan before it changes the server.

## 2. Architecture

```
league runner (separate, not part of this milestone)  ──writes──▶  league.db       (never read by the website)
                                                                      │  copy (SQLite online backup)
tools/export_public_feed.py  (timer, Nice 15, ≤900 MB)  ────────────▶ feed/releases/<release>/…  then feed/latest.json (atomic)
                                                                                         │ static files
Caddy  ──serves──▶  /           site/current → site/releases/<id>/   (index.html, assets/)
                    /feed/*     feed/…                                (GET and HEAD only)
```

- The website process is Caddy serving files. There is no application server, no API, no write path; any method but GET, HEAD or OPTIONS is answered 405.
- The league file is opened only by the exporter, on a **copy** made by SQLite's online backup, with no healing snapshot. The live league is never touched
  (tested: the league file's SHA-256 and chain are identical before and after an export). When some finished season's Coronation score was never recorded
  (seasons before Edition 8.58) and the league has a game archive, the exporter takes a second online-backup copy of the **archive after** the league copy
  (the runner writes the archive batch before it commits the event, so the archive is never behind the league), checks the pair with the league's own
  `archive_check`, and reads a score only from a checksummed archived game that matches the season report; otherwise the score is shown as not recorded.
  Both snapshots were tested while a real runner process was parked inside its commit (journal row written, uncommitted; and archive batch durable, league
  not yet committed): the copy was the last committed event, whole, and the pair consistent.
- **One publisher.** Exporter runs and any other writer take an exclusive lock on `feed/.publish.lock` first; a second one exits 75 at once and changes
  nothing (the systemd unit treats 75 as success). The operating system releases the lock when its holder dies, even by `kill -9`. Leftover temporary
  directories are removed only under the lock and only if older than ten minutes; a failing publisher removes only its own. Tested with real processes.
- **Pruning** keeps the newest six releases and anything younger than an hour, never the live one. A viewer whose release was pruned anyway re-reads
  `latest.json`, switches wholly to the live release and redraws (browser-tested); if the live release itself lacks the file, it shows the error and does not loop.
- The feed never moves backwards (a release with fewer events than the live one is refused) unless `--allow-older` is given after restoring an older league on purpose.
- The old `/api/*` control endpoints, `dfl-control`, `dfl-live` and `dfl-chronicle` have no counterpart in the new site. They stay as they are until cutover and are stopped, not deleted, afterwards.
- The site is published as `noindex` (header and meta) until the owner says it is public.

Directory layout on the Droplet (new, beside the old; nothing existing is moved):

```
/srv/dflv2/repo/       checkout of Vajilia/DFLv2 at the deployed commit (read-only to the web)
/srv/dflv2/site/       releases/<id>/ and current -> releases/<id>   (deploy/website/install_site.sh)
/srv/dflv2/feed/       latest.json, releases/                        (written only by the exporter)
/srv/dflv2/league/     league.db                                     (written only by the league runner)
```

## 3. Steps (each one reversible; none done yet)

1. **Inspect.** Run `inspect_server.sh` on the Droplet; compare with section 1; amend this plan.
2. **Rollback copy.** Run `snapshot_config.sh` (as root). It copies, never moves, `/etc/caddy`, the `dfl-*` units and the web root into `/root/dfl-rollback/<time>/` with checksums.
   Also record a DigitalOcean snapshot of the Droplet from the control panel (free of server load; owner action).
3. **Create** user `dflv2`, the directories above, and a checkout of the repository at the approved commit.
4. **Stage first, beside the live site.** Add a second Caddy site block (a staging hostname, or the Droplet's IP on port 8088 if there is no spare hostname)
   from `Caddyfile.template`; `caddy validate`; reload. The old site continues to answer on the real hostname throughout.
5. **First feed.** `refresh.sh /srv/dflv2/league/league.db` once by hand; look at the staging site on a phone and a laptop against the local preview.
6. **Timer.** Install `dfl-feed-export.service` and `.timer` (every 5 minutes; Nice 15, idle I/O, 900 MB memory cap, read-only access to the league, write access to `feed/` only).
7. **Cutover (owner's go).** Replace the real hostname's site block with the new one (the old block kept commented in the file). `caddy validate && systemctl reload caddy`.
8. **After cutover.** Watch memory and load for a day; stop (do not delete) the old units once the owner agrees.

## 4. Rollback

| Situation | Action | Time |
|---|---|---|
| A bad site build | `rollback.sh --site` (switches `site/current` to the previous release) | instant |
| A bad feed release | `rollback.sh --feed <older release>` (releases are kept; the last 6 stay) | instant |
| The exporter fails | nothing to do: the previous release stays live; the failure is in the journal (`journalctl -u dfl-feed-export`) | none |
| The new Caddy config is wrong | `rollback.sh --config /root/dfl-rollback/<time>` (verifies checksums, restores, validates, reloads) | seconds |
| Everything | restore the DigitalOcean snapshot taken in step 2 | minutes; loses anything written since |

The proof-of-concept site's teams and history are expendable (owner's words); the rollback copy exists so that a bad cutover can be undone, not to preserve that data.

## 5. Refresh path (how the site follows development)

One command, safe at any moment, safe to repeat: `deploy/website/refresh.sh LEAGUE_FILE` (the timer runs exactly this). It makes a copy of the league file, reads its newest
committed event, builds and checks the feed, publishes it atomically, and prunes old releases. The page itself re-checks `latest.json` every minute and redraws when the release changes.
On a development laptop: `python3 tools/export_public_feed.py league.db --out feed/ && python3 tools/preview_site.py --feed feed/`.
If the engine code changed since the league file was written, the league refuses to open (that protects it); add `--use-league-code` and the exporter reads the file with the code recorded inside it.
(The exporter lives in `src/dflsite`, outside `src/dfl`, because the league fingerprints `src/dfl` and `data/`: changing website code never locks a league file out.)

## 6. Measurements (this sandbox: 2 shared cores, busy with a 70-minute test run, so timings are pessimistic; sizes are exact)

| Item | Figure |
|---|---|
| Feed per release (58 files, league at 2001 week 8) | 860 KB uncompressed, about 110 KB gzipped |
| Largest files | schedule 81 KB (5.6 KB gz), teams, history 8 KB, a roster about 15 KB (2.4 KB gz) |
| Releases kept | 6, about 5.6 MB on disk (limit 50 GB) |
| Site code | index 1.5 KB, app.js 31 KB (8.9 KB gz), CSS 14.6 KB (4.5 KB gz); no images, no fonts, no third-party requests |
| First page load (home) | about 20 KB gzipped over the wire |
| Export, build and checks | 0.12 to 0.4 s |
| Export, publish | 0.1 to 0.4 s |
| Export, copy of the league file (12 MB) | 0.06 to 0.13 s |
| Export, opening the copy | 1.2 s when the newest snapshot is current; 42 to 154 s when events had to be replayed on a busy machine (each engine event is a game week) |
| Export, peak memory | 205 to 217 MB (the Droplet has 2 GB; the unit caps it at 900 MB) |
| Test suite for the website | 52 tests, about 60 s |

Reading the table: the cost of an export is dominated by opening the league copy, which replays the events since the last snapshot. League snapshots are written every 4 events (this league), so the
worst case is the cost of three engine events. On a Droplet with one core also running a league, expect the exporter's busy time to be seconds to a couple of minutes per run; it runs at the lowest CPU and
I/O priority and a run that finds nothing new publishes nothing. **These figures are from the sandbox, not the Droplet, and have to be re-measured there** (step 5 prints them as JSON).

## 7. Decisions for the owner (none blocks the preview)

1. A staging hostname (or accept port 8088 on the Droplet's IP) for step 4.
2. Whether the new site goes on the real hostname at cutover or on a new one.
3. Who runs the server steps: they need shell access to the Droplet, which this session does not have.
4. ~~The official DFL map~~ Added (received 2026-10-30 20:56, alignment of 30 October 2026, 4800x4400 PNG, `site/assets/map/dfl-map.png`); the Map page shows it and links the full-size image.
5. ~~Coronation scores for every season~~ Done in Edition 8.58 (see `docs/PUBLIC_FEED_FORMAT.md`, Known limits).
