# Diamond Football League v2 (DFLv2)

This repository is the **development home** for the Diamond Football League's rebuilt simulation engine. It is **not production-ready** and must not be deployed as a replacement for the live site yet.

## Project concept

A persistent, female-only, 48-franchise football universe featuring autonomous management, players, an evidence-based spatial game engine, eight-year rotating division scheduling beds, exile/return mechanics, and a read-only narrative layer.

## Current engineering status (2026-10-29)

The simulation core is built in this repository and tested; the website, live clock and release pipeline are not. See `PROJECT_COMPLETION_PLAN.md` for what is verified, provisional, missing and unresolved, and `docs/BUILD_STATUS.md` for the build record.

- **Authority:** `docs/spec/DFL_Design_Document_Ed8.59.md` (hash-stamped). The spec wins over everything, including v1.
- **Built:** league layer (standings, rotation, exile, playoffs, Tour, Bowl, Coronation), roster and cap ledger, the 27-week calendar and year loop, character creator, a play-level engine and a 22-player spatial engine (the league default), the people layer (scouting, souls, demands, coaches, governance, meeting rooms, analytics and halls), trades, the draft, a fairness harness, durable saving (SQLite) with a tested upgrade path for code releases, and a permanent archive of every game and every 22-player snap's trace (`docs/PERSISTENCE.md`, `docs/ARCHIVE.md`).
- **Not built:** the live clock and pacing, the website and animation viewer, the narrative layer, and the release pipeline.
- **Determinism:** deterministic rules, weighted decisions and seeded randomness only. No language model takes part in any simulation decision.
- **Pacing target:** about 50 real hours per season (owner ruling, Edition 8.7; non-binding). **Provisional:** the live clock is deferred until saving and archiving are complete (owner, 2026-10-29), so this figure is not a design constraint yet.
- Character meters and internal assessments are engine-only; any narrative layer reads observable evidence, is template-driven and must never influence the engine.

## Import status

There is no source snapshot. DFLv2 was rebuilt from the specification, and the earlier import notes (`BETA_STATUS.md`, `SOURCE_IMPORT_STATUS.md`) are superseded.

## Milestone 1 (league layer) — see `docs/BUILD_STATUS.md`

```
python3 -I -m unittest discover -s tests          # 350+ tests (about 12 minutes)
python3 -I tools/run_seasons.py --seasons 100     # multi-season run, validates every season's tables
python3 tools/validate.py data/season1 --doc docs/spec/DFL_Design_Document_Ed8.59.md
```
The specification lives in `docs/spec/` and wins over any v1 behaviour.

## Website (development preview) — see `docs/PUBLIC_FEED_FORMAT.md` and `docs/WEBSITE_DEPLOYMENT_PLAN.md`
```
python3 -I tools/export_public_feed.py league.db --out feed/      # publish the newest committed state of a league as a public feed (reads a copy)
python3 -I tools/preview_site.py --feed feed/ --port 8080          # look at it: http://127.0.0.1:8080/  (read-only server)
python3 -I tools/preview_site.py --feed feed/ --single-file preview.html   # one self-contained page, opens by double-click
python3 -I tools/verify_deployment.py http://127.0.0.1:8080 --profile local     # check a running copy from the outside
python3 -I tools/build_staging_package.py --seed-feed feed/ --out out/       # the staging deployment package (committed code only)
```
Nothing is deployed. Staging at preview.diamondfootballleague.com is prepared but **not installed**: see `docs/STAGING_RUNBOOK.md`. The exporter and site live outside `src/dfl` so changing them never invalidates a league file.

## Production safety

Existing production site: https://diamondfootballleague.com

Original repository: https://github.com/Vajilia/DFL

The original repository and production deployment must not be changed by this beta initialization. DFL v1 historical results are disposable for an eventual clean installation, but the newly developed engine's test archives should be retained for verification until a release candidate passes.
