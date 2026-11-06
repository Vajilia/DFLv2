# Reproducing the season-2 engine comparison

*Written 2026-11-06 for the public-folder backup milestone. Nothing here touches a production league, a deployed host or the running rehearsal.*

## What the comparison shows, and what it does not

Two copies of the same finished first season continue through the whole of season 2:

* **A** is driven by the *builder* (`tools/broadcast_build.py`): one league event at a time, with the feed export, replay export and live-recording builds in between, and `next-season` at the year boundary.
* **B** is a *free-running twin*: the league runner (`tools/league_runner.py`) simply runs the league to the same event with no broadcast work between events.

At event 94 (the end of season 2) `tools/league_store.py digest` must give the same journal hash and the same deep hash for both. Equal means the broadcast machinery, the season hand-over and the pause between events change nothing in the league: same events, same random-number consequences, same state. It does **not** show that the public output is right (other suites do that) and it says nothing about league rules; it is a determinism check on the engine as the broadcast drives it.

With `--daemon-check` the script also runs the broadcast daemon in virtual time over the whole of season 2 on a hard-link copy of A's store (`tools/broadcast_multiseason_check.py --through-end`): season 1 byte-identical after the hand-over, nothing of season 2 public before its time, all 400 season-2 games and the four Honors parts public at the end, indexes keep both seasons.

## The command

```
python3 tools/engine_comparison.py --workdir /path/to/NEW_EMPTY_DIR \
        --year1-root /path/to/FINISHED_SEASON_1_ROOT \
        --daemon-check --report /path/to/NEW_EMPTY_DIR.json
```

Options: `--seed` (default 20261105; used only when the script makes the first season itself), `--seasons N` (compare at the end of every season up to N, default 2, minimum 2), `--report FILE`. Exit status 0 only if every digest pair is identical (and, with `--daemon-check`, the daemon check passed). The report carries the commit, the seed, every digest, the builder's `status`, the daemon-check notes and the seconds each step took.

## Fixture requirements

| Requirement | Detail |
|---|---|
| Code | the commit under test, clean; the report records `git rev-parse HEAD` |
| Year-1 root | a **completely built first season**: `broadcast_build.py status --root R` says season 1, 47 events built, 400 games; `league_store.py status R/league/league.db` says 47 events. The script refuses anything else. It is only **copied**, never written. |
| Making it | leave `--year1-root` out and the script runs `broadcast_build.py init --seed SEED` and `step` in `WORKDIR/year1` (about 35 minutes on one core, 1.1 GB). The reference fixture of 2026-11-06 was built exactly that way with seed 20261105 (`init` then `step`, no other command). |
| Work directory | must not exist or be empty. Nothing outside it (and the read of the year-1 root) is written. |
| Disk | about 1.1 GB per copy of a built season, about 1.0 GB for season 2 on top, 0.3 GB for the twin, and with `--daemon-check` about 1.8 GB for the public tree (the store copy is hard-linked): **about 4.5 GB of free space in the work directory plus the year-1 root**. Measured on the reference run: A 2.1 GB, B 0.28 GB, public 1.8 GB. |
| CPU / memory | two cores recommended (the builder and the twin run in parallel, niced); one core works and takes longer. No measurable memory need beyond a normal Python process. |
| Time | reference run on a 2-core host: 11 s to copy, **856 s** for season 2 (builder-driven and free-running in parallel), **209 s** for the daemon check; **1,080 s** in all with an existing year-1 root. |
| Network | none |
| Python | the interpreter that runs the script runs every child; standard library only |

The digest of the reference year-1 root (event 47) is `c9ebb593218caf92a3461c01f2af61834c847cebad9c6514713351817c6d5d56`. If a freshly made year-1 root has a different digest at event 47, the code or the seed differs from the reference and the later numbers are not comparable; the script prints it in the report (`year1.digest`) so this can be seen first.

## Reference result (2026-11-06, commit `7668245230c2692fd81430fbd7bb6114a01121d3`)

* season 2, event 94: builder-driven and free-running **identical**; journal and deep hash both `485b49f5d0e12d66cfb53d77ee555e15a2544e38b72242d6193c20f9845a03c0`, 94 events. This is the same digest the earlier hand-run comparison gave, so the scripted run reproduces it.
* daemon check: ok, no problems. 400 season-2 games public at the end (800 playback files and 800 replay-library entries in all), four Honors parts for season 2, 117,707 public files and 1,546,391,468 bytes at the end, season 1 unchanged at the hand-over (59,287 files).
* report file: the `engine_comparison` entry of `docs/PUBLIC_BACKUP_MEASUREMENTS_2026-11-06.json`.

## Limits

* It compares two seasons (94 events). Longer comparisons work (`--seasons 3` and up) but each further season needs about 15 minutes and 2.5 GB more, and no run beyond season 2 has been made.
* One seed. The league's own seeded determinism suites cover other seeds on shorter spans.
* It does not run the deployed code on the production host; it runs the repository's code on a scratch copy.
