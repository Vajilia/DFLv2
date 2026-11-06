# Test and measurement record, 2026-11-06 (bounded-memory pass of the public backup)

Branch `backend/public-backup-2026-11-06`, from `8fa89d7330033ad0d7a055e55feb183602ac3baf`. Local only; the Droplet and the running rehearsal were not touched. Completed runs, failed runs and runs that never started are kept apart.

## Completed, passed
| Run | Result | Exit |
|---|---|---|
| `tests.test_public_backup` (final code, run alone) | 50 tests OK in 268.8 s (includes the bounded-memory differential test, 10 manifest-streaming tests, 5 concurrency tests, the version-1 fixture test) | 0 |
| `tests.test_backup_resume` | 6 tests OK (5 resume scenarios and the published-bytes check test); run together with the 50 above: 56 tests, see "Failed once" | n/a |
| `tests.test_recovery_set` + `tests.test_broadcast_build` + `tests.test_multiseason` | 46 tests OK in 662 s (includes the new simultaneous-attempts test on one set name and the builder-lock tests) | 0 (reported OK) |
| `tests.test_backup_resume_real` with `DFL_SEASON1_ROOT=/tmp/season/R1` (real engine, real builder and daemon, season hand-over inside a non-quiescent copy) | 1 test OK in 650 s; started 2026-11-06T13:35:35Z, ended 13:46:26Z | **0** (`/tmp/bm/real4.exit`) |
| `tools/measure_public_backup.py scale` at 29,000 / 59,000 / 118,000 / 236,000 / 472,000 files | all 15 steps ok (`docs/PUBLIC_BACKUP_MEASUREMENTS_2026-11-06.json`, key `scale_synthetic_bounded`) | 0 |

Full output of the real-engine run, unedited:

```
test_a_set_taken_across_a_real_hand_over_resumes_publication_and_building (tests.test_backup_resume_real.RealHandOver.test_a_set_taken_across_a_real_hand_over_resumes_publication_and_building) ... ok

----------------------------------------------------------------------
Ran 1 test in 649.954s

OK
```

## Failed once, then fixed and rerun
* First combined run (`test_public_backup` + `test_backup_resume`, 56 tests): 55 passed, 1 failed: `Concurrency.test_many_processes_starting_together_make_exactly_one_valid_backup` expected every refused process to say "in progress", but a process that started after the winner had finished was (correctly, safely) refused with "already exists". A test-expectation flaw, not a code defect; the assertion now accepts either refusal. The `Concurrency` class was then run 4 times (5 tests each, all OK) and the whole `test_public_backup` module once more (the 50-test OK above).
* First real-engine run (700 s): all checks of the lockstep passed, then my new assertion on "newly built releases" failed (12 releases listed, expected 2). Cause: I had counted every release not yet public at the crash as new; 11 of them are season-2 events built before the backup (event 61 is the one built after). Per the owner's instruction they are now compared byte for byte, `generated_at` included; only releases of events built after the restore (61 and 62) have `generated_at` removed, and only from `releases/<event>/manifest.json`. See `docs/PUBLIC_BACKUP.md` section 6.

## Never started or not completed
* Two real-engine launches never ran a test: `real2` stopped at import with an IndentationError that my edit had introduced; `real3` (launched 05:25 PST after the fix) left no process and an empty log, cause unknown. Neither produced a result. The passing run above is a fresh launch that was confirmed started (process alive, test name in the log) before waiting.
* `measure_public_backup.py quiet` on the real 117,707-file folder and on the store was queued after the scale run but its source trees (`/tmp/ms/EC/...`) no longer exist on this host: both exited immediately with rc 1 and measured nothing. The real folder was **not** re-measured after the change.
* Not run in this pass: the frontend/site test suites (no frontend change was made), a full repository test run, any test against the Droplet or a production backup.

## M8b pass (preflight, full-disk handling, documentation correction)
| Run | Result | Exit |
|---|---|---|
| `tests.test_backup_space` + `tests.test_public_backup` + `tests.test_recovery_set` + `tests.test_backup_resume` + `tests.test_broadcast_build`, one job, final code (`d481f9c`) | **100 tests OK, 0 skipped** (the real small-tmpfs full-disk tests ran; 915 s) | 0 |
| `tools/measure_public_backup.py closure` (index-document size 0.6 to 38.7 MB, 2,000 to 128,000 games) | all steps ok; peak 25 to 195 MB (about 1.3 KB per listed game above 8,000 games) | 0 |

Failures found on the way, all fixed: (1) an earlier focused run (85 tests, exit 1) had 3 failures: the new CLI preflight called an *active* backup's `.partial` "stale" instead of "in progress" (nothing was touched, the message was wrong); the preflight now checks the name's lock first; (2) the real full-filesystem test found that `quarantine` moved a `.partial` and then crashed writing its note file on a full disk; the note is now best effort. The real-engine hand-over test was **not rerun** in this pass (no change touched the code it exercises beyond `create`'s unchanged happy path); its last result is the passing run above (exit 0, `d13462d`-era code).

## Runbook rehearsal (added later on 2026-11-06)

`tools/backup_runbook_rehearsal.py` executed `docs/BACKUP_RUNBOOK.md` end to end on a scratch copy: **completed, exit 0, 34 of 34 expectations held** (third run; the first stopped on a bug in the driver itself, the second passed with two checks later found too weak). Transcript: `docs/BACKUP_RUNBOOK_REHEARSAL_2026-11-06.md`. Skipped: none. Never started: a run on the Droplet or a real production backup (not permitted). No unit test suite was re-run for this step (no change to `tools/public_backup.py`, `tools/backup_space.py` or `tools/recovery_set.py`). One wording defect found and recorded, not fixed (finding 1 of the transcript).
