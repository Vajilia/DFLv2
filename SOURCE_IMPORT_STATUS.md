# Source import checkpoint (2026-10-20)

> **SUPERSEDED (2026-10-29).** This checkpoint asked for a source snapshot (`DFL-vBeta_source_snapshot_2026-10-20.zip`) to be imported. That snapshot does not exist for this project and was never imported; DFLv2 was rebuilt from the specification (owner ruling, 2026-10-22, `docs/V1_DIVERGENCES.md`). Current status: `PROJECT_COMPLETION_PLAN.md` and `docs/BUILD_STATUS.md`. The text below is kept only as history. Production safety rules still stand: do not deploy, and do not change the live site or `Vajilia/DFL`.


**Partial import only. Do not deploy this repository.**

Imported directly from the prepared DFL beta archive:
- `src/dfl/__init__.py`
- `src/dfl/schedule_bed_rotation.py`

Previously initialized: `README.md`, `BETA_STATUS.md`, `.gitignore`.

The full prepared package is `DFL-vBeta_source_snapshot_2026-10-20.zip` (339 entries, 1,386,785 uncompressed bytes) in the ChatGPT conversation. The archive has **not** been fully imported to GitHub, and it is not equivalent to a runnable deployed engine.

Local verification in the complete development workspace:
`PYTHONPATH=src:tests:. pytest -q tests/test_schedule_bed_rotation.py` -> **3 passed**.

The complete original DFL repository and production site are unchanged. Prioritize importing remaining package files and verifying in a clean clone before any release.
