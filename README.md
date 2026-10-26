# Diamond Football League v2 (DFLv2)

This repository is the **development home** for the Diamond Football League's rebuilt simulation engine. It is **not production-ready** and must not be deployed as a replacement for the live site yet.

## Project concept

A persistent, female-only, 48-franchise football universe featuring autonomous management, players, an evidence-based spatial game engine, eight-year rotating division scheduling beds, exile/return mechanics, and a read-only narrative layer.

## Current engineering status (2026-10-20)

- A prior development archive recorded and audited all **360 founding-season regular-season spatial games**. These historical simulation records are development evidence, not files uploaded to this repository.
- A recurring schedule design handles division standings, fifth-place exile, returning franchises, and deterministic eight-year conference-bed rotation.
- Contract renewal and independent player consent have targeted tests, but the entire second-year offseason and roster construction have **not** been completed end-to-end.
- The second season's **360 genuine spatial games** have **not** been executed.
- Player development calibration remains experimental; unverified automatic ability changes must remain disabled.
- The **48-hour viewer clock** is a presentation mechanism, not a license to bypass verified simulation or archival checks.
- Character meters and internal assessments are engine-only; the narrative layer reads observable evidence and must never influence the engine.

## Import status

**IMPORTANT:** This GitHub repository has been initialized with project documentation only. The prepared source snapshot contains 339 files and is **not yet imported here**. No complete codebase, reproducible tests, or certified deployment are represented by this repository at this point.

The prepared package in the ChatGPT conversation is named `DFL-vBeta_source_snapshot_2026-10-20.zip`. The package includes source and tests but excludes SQLite historical archives. It should be inspected for secrets and temporary artifacts before import.

## Milestone 1 (league layer) — see `docs/BUILD_STATUS.md`

```
python3 -I -m unittest discover -s tests          # 24 tests
python3 -I tools/run_seasons.py --seasons 100     # multi-season run, validates every season's tables
python3 tools/validate.py data/season1 --doc docs/spec/DFL_Design_Document_Ed8.45.md
```
The specification lives in `docs/spec/` and wins over any v1 behaviour.

## Production safety

Existing production site: https://diamondfootballleague.com

Original repository: https://github.com/Vajilia/DFL

The original repository and production deployment must not be changed by this beta initialization. DFL v1 historical results are disposable for an eventual clean installation, but the newly developed engine's test archives should be retained for verification until a release candidate passes.
