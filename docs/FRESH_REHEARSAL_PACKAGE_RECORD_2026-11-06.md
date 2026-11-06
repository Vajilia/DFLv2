# Fresh rehearsal: package record (2026-11-06)

Companion to `docs/FRESH_REHEARSAL_2026-11-06.md`. Written after the package was built, because the package pins the commit that holds the scripts, so this record cannot be inside it. **Nothing was installed, switched or started on the Droplet.**

| Item | Value |
|---|---|
| Package commit (everything in the package) | `d2e66bb6cf4781aed60436ac55cb5d0e7b940408` on `backend/monitor-tmp-race-2026-11-06` (pushed; `git ls-remote` equals it) |
| Monitor fix inside it | `5118701` (supersedes `1c50450`); `tools/rehearsal_check.py` SHA-256 `f3066c71ac32dfe85f764b46b7fcbd315a9ae545b80bad4ef919d5cfc9fc7319` |
| Package file | `dfl-rehearsal-20261106T162030Z-d2e66bb6cf.tar`, 249,733,120 bytes |
| Package SHA-256 | `b56b112643019e806f81986ccfd6ebd310eeffa0ad025826aeb99c413b4f5a6c` |
| Built with | `tools/build_rehearsal_package.py --commit d2e66bb6… --root /tmp/season/R1 --out … --force-overrun w041:450` (47 events, 400 games, 106 windows, plan 172,800 s; new random canary) |
| Where it is now | the build container only (`/tmp/season/pkgF/`); not uploaded anywhere. A rebuild is not byte-identical (new canary), so upload THIS file |

Checks made on the built package: tar checksum OK; `SHA256SUMS` inside all OK; `PACKAGE.json` commit equals the commit above; no file in `repo/` has a second hard link.

## Scratch rehearsal of `fresh_run.sh` with this real package (not the Droplet)

A scratch tree with stub `systemctl`/`journalctl`, a fake "failed run" (private, feed, monitor with a latch), a fake old code directory:

* `preserve` dry run changed nothing; `--apply` moved the three folders under a mode-700 timestamped folder, wrote `FAILED_RUN.txt` and a manifest that re-verifies (`MANIFEST OK`).
* `prepare --apply`: new code directory created with no hard links and no inode shared with the package or the old tree; the old tree untouched; the **real 15-check pre-flight passed** (47 of 47 events built, 400 of 400 games, canary present, no earlier trip); both units render with the new commit; the env file holds the past deadline. A second `prepare` refuses (directory exists).
* `start --apply`: wrote deadline = now + 54 h, started monitor then daemon (stubs), and printed the deadline. In the scratch the daemon is a stub, so the clock line was empty; the real clock is not exercised.

Not done: nothing here ran the real daemon from the package on a 1 vCPU host, and `tests/test_rehearsal_package.py` / `test_rehearsal_deploy.py` / the full suite were not run.
