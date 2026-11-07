# Unattended operation (M6): the supervisor, its health, its runbook

Status: **candidate for review.** Built and tested on synthetic seasons and one real-engine smoke check, on a scratch copy. **Nothing is installed or activated anywhere, the Droplet and the running rehearsal were not touched, nothing sends a message, and no credential or service is involved.** Final release decisions depend on the rehearsal results (section 10).

Branch `backend/unattended-m6-2026-11-07`, based on `backend/public-backup-2026-11-06` (`284125a`: the continuity and backup work). It does **not** contain the monitor fix of `backend/monitor-tmp-race-2026-11-06`; the two branches touch different files and are independent (section 9).

## 1. What problem this closes

Before M6 nothing scheduled the builder. The daemon already did the hard, safety-critical part (it holds the clock at the first item that is not built and at the end of a season whose successor is not on air, and never skips viewing time), and the builder already resumed from its last complete event and took an exclusive lock. What was missing was a process that **keeps the builder ahead of the clock**, refuses to run twice, notices when it cannot, and says so in a form a machine can read.

## 2. What the supervisor does (`tools/broadcast_supervisor.py`, about 400 lines)

It reads the store (read-only) and, when something is due, runs **one** existing builder command as a child process:

| State of the store | What it does |
|---|---|
| The season being built is not complete | `broadcast_build.py step --events 1` |
| The season on air is complete and has no successor | `broadcast_build.py next-season`, then `step` until that season is complete |
| The successor is complete | nothing (`ahead`): **never more than one season ahead**; the disk, not the builder, is the limit |

"On air" is the season named by `store/carry.json` (the daemon's one atomic hand-over record), or season 1 if there is none. When the daemon hands over, the supervisor's next cycle sees season *n+1* on air and prepares *n+2*.

* **Duplicate builders are impossible, three ways.** A second supervisor is refused (`ROOT/supervisor/.lock`, exit 75, and it changes nothing, not even the health file). The builder keeps its own exclusive lock (`ROOT/.builder.lock`, `tools/rootlock.py`), so a manual `step`, a recovery set being taken, or an orphaned step makes the supervisor report `busy` and try again: **busy is not a failure**. The operating system drops both locks when the holder dies, however it dies.
* **Crash-safe resume.** The builder fires and records one event at a time and rewrites `builds.json` last, atomically; a kill anywhere resumes from there (existing behaviour, re-tested through the supervisor). One real defect was found and fixed: `next-season` wrote the new season's plan and then its builds file, so a process killed between the two left a season folder with a plan and no builds (the builder would have selected it, `next-season` would have refused to redo it). It now writes into `sNNN.partial` and renames the folder into place when both files are complete; a leftover `.partial` is overwritten by the next try (nothing is deleted). Failing test first: `Rollover.test_a_crash_between_the_two_files_next_season_writes_leaves_no_half_season`.
* **A slow builder holds the broadcast, it never skips time.** This is the daemon's rule, unchanged and re-proved through the supervisor's rig: with the builder allowed one action every few hundred virtual seconds, the clock never passed the horizon (the first item not built), never went back, never jumped, the viewing time that ran was shown in full (the hold took none of it away), and it went on once more was built. The supervisor **measures and reports** this (`broadcast.holding`, `lead_s`, problem `broadcast_holding`); it never touches the clock.
* **Capacity first.** Before every builder action it reads free bytes and free inodes of the volume the root is on: a step needs *floor + step margin*, preparing a season needs *floor + step margin + a season's estimate*. If they are not there it builds nothing, changes nothing, and reports `capacity_wait` / `capacity_insufficient` (critical). That is **not a failure** (no retry counter, no backoff); it looks again every cycle and goes on by itself when room appears. A disk that fills **during** a step ("No space left on device" from the builder) is classified the same way, with a 5-minute pause before the next try. The builder's own atomicity makes stopping at any point safe. The public volume, if it is a different volume, is read too and reported (`public_capacity_low`).
* **Prior seasons' public records are preserved; nothing is deleted.** The supervisor never opens the public folder for writing (it only reads `status.json`), never deletes, moves or truncates anything (a test parses its source and fails if it calls `remove`, `unlink`, `rmtree`, `rename`, `move`, `truncate` and the like; its one atomic file replace is the shared `status.write_atomic` of its own health file), and its unit mounts the public folder read-only. Through a rollover the earlier season's releases, live recordings, Honors parts and replay files were byte-identical afterwards (test). **No automatic deletion exists**; freeing space is a person's decision (retention policy is M8, needs the owner's approval and a verified backup).
* **Its own files** are only `ROOT/supervisor/health.json`, `state.json` (failure count, backoff, when a problem first appeared: losing it loses only a backoff) and `.lock`. The recovery set takes only `league/` and `store/`, so these are outside it.

It does **not** start the daemon, the monitor or any other service; does not run `init` (a person makes a root, with `init --continuous`); does not fire the league except through `broadcast_build.py`; and never touches `src/dfl`, `data`, `site` or any league rule.

## 3. Commands

```
broadcast_supervisor.py run   --root R [--public P] [options]    the service
broadcast_supervisor.py once  --root R [--public P] [options]    one cycle, prints the health document (manual use, tests)
broadcast_supervisor.py check --root R [--max-age-s 180]         exit 0 ok, 1 warning, 2 critical or stopped, 3 unknown
```

Exit codes of `run` / `once`: 0 stopped by a signal / done; 2 refused (no store); 75 another supervisor holds the lock. `run` stops on SIGTERM: it kills the builder step in flight (safe), writes `state: stopped` and `status: critical` into the health file, and exits 0.

Options (defaults chosen for the 1 vCPU / 2 GB / 50 GB Droplet and the measurements in `docs/STORAGE_RETENTION.md`; **the owner's numbers replace them**): `--floor-gb 8` (free disk the builder never goes below; keep it above the monitor's floor, 6 in the rehearsal, so building stops **before** the broadcast does), `--step-margin-gb 0.5`, `--season-gb 2.0` (about 2.0 GB a season measured: private store plus its public copy), `--inode-floor 20000`, `--step-inodes 5000`, `--season-inodes 130000` (about 59,000 public and 60,000 private files a season), `--interval 30`, `--step-timeout 3600`, `--backoff-base 60 --backoff-max 1800`, `--escalate-after 3`, `--hold-critical-s 1800`, `--stall-s 300`, `--busy-warn-s 900`, `--retry-now`. (`--builder-cmd` replaces the builder command: tests only.)

## 4. The health document (`ROOT/supervisor/health.json`)

Format `dfl-supervisor-health` 1, rewritten atomically every cycle (every 30 s when idle, back to back while building). **Private**: it names the builder's progress and the horizon, which the public `status.json` never does. Fields: `status` (`ok` | `warning` | `critical`), `state` (`running` | `stopped`), `checked_at` / `checked_at_iso`, `pid`, `started_at`, `cycle`, `problems[]` (`code`, `severity`, `detail`, `since`), and the sections `builder` (`state`, `season`, `built`, `total`, `last_action`, `consecutive_failures`, `last_success_at`, `last_error`, `next_attempt_at`), `capacity` (free bytes and inodes, the floors, `ok`, `low`, the reasons, the public volume), `broadcast` (`clock_state`, `position_s`, `horizon_s`, `horizon_why`, `lead_s`, `holding`) and `seasons` (`on_air`, `target`, `prepared_ahead`, `ahead_complete`).

Builder `state`: `building`, `ahead` (nothing to do), `busy`, `failed` (inside a backoff), `capacity_wait`.

| Problem code | Severity | Meaning | What to do |
|---|---|---|---|
| `builder_failed` | warning, critical from the 3rd consecutive failure | a builder step or `next-season` exited non-zero; it retries after 60 s, 120 s, 240 s … 30 min | read `builder.last_error`; the store is consistent (last complete event); after the cause is fixed it recovers by itself, or `once --retry-now` |
| `builder_busy_long` | warning | someone else has held the builder lock for 15 min | find the holder (`ROOT/.builder.lock` names it); a recovery set or a manual step is normal for a while |
| `capacity_insufficient` | **critical** | below the floor for the next action, or the builder reported no space; **nothing is being built** | add room (bigger volume, off-host copies, the owner's retention decision); building resumes by itself |
| `capacity_low` | warning | enough for now, not for the season after the next | plan the room before it is needed |
| `public_capacity_low` | critical | the public volume is below the floor | same; the daemon's own floor will pause the broadcast |
| `broadcast_holding` | warning, critical after 30 min | the clock is held at the prepared-content boundary: the builder is behind (or the next season is not prepared) | check the builder problems above; viewers see a hold, never a skip |
| `broadcast_paused` | warning | the clock is paused (PAUSE switch or the guard) | not the supervisor's business; see the rehearsal runbook for guard trips |
| `publication_stalled` | **critical** | the clock says running but the daemon's lease ran out or the public `status.json` is older than 5 min | the daemon is dead or stuck: unit status, journal |
| `publication_error` | critical | the public status says the last publication failed | daemon journal |
| `store_not_continuous` | warning | the store is not marked continuous; the clock would run past a season's end instead of holding | `broadcast_build.py continuous --root R` |
| `view_error` | warning | the supervisor could not compute the broadcast view | the store or a file is unreadable: investigate |
| `supervisor_stopped` | critical | written on a clean stop | start it |

`check` adds: no file, an unreadable file, or a file **not written for 180 s** is `unknown` (3), never `ok`: a dead supervisor cannot report itself. Note that the supervisor sees a stalled daemon only through the clock's lease and the public heartbeat; it is not a replacement for the rehearsal monitor's spoiler and resource checks (`tools/rehearsal_check.py`), which this branch does not include (section 9).

## 5. Service templates (not installed)

`deploy/supervisor/`: `dfl-supervisor.service` (the service), `dfl-supervisor-check.service` and `.timer` (a one-minute `check`, a oneshot that prints one JSON line to the journal; exit 1 is a warning and does not fail the unit, 2 and 3 do). Placeholders `__COMMIT__`, `__ROOT__`, `__PUBLIC__`; **no installer renders them and nothing was installed, enabled or started.** Properties of the service unit: runs as `dflv2`; `ReadWritePaths` is the root only and the **public folder is read-only**; `Nice=15`, `CPUWeight=20` (the builder runs the engine and must stay behind the daemon on one CPU); `KillMode=control-group` so a stop takes the builder step with it; `RestartPreventExitStatus=75 2`; five starts an hour. `MemoryMax=1500M` is a placeholder: **not measured** under the unit.

## 6. Operating it (a runbook for when it is installed; none of this has been run on a host)

* **Start:** the root exists (`broadcast_build.py init --continuous`), the daemon and monitor are the person's concern; start the supervisor; within one cycle `health.json` appears. First cycles on a complete season: `next-season`, then one event per cycle.
* **Stop:** `systemctl stop` (SIGTERM). Safe at any instant; `check` then returns 2 `supervisor_stopped`.
* **After a crash or reboot:** systemd restarts it (not for exit 75 or 2). It rebuilds nothing already built. A remembered backoff survives; `once --retry-now` (or a restart with `--retry-now`) skips it.
* **A manual builder step or a recovery set while it runs:** both take the builder lock; the supervisor shows `busy` and waits. Do not stop the supervisor for them.
* **Capacity wait:** nothing else to do but make room; do **not** delete public records to make it go away. A season's public copy is the only home of published replays (`docs/COMPLETION_ROADMAP_2026-11-06.md` section 2).
* **The builder fails:** read `last_error`. A deterministic engine error repeats every 30 minutes at most; it is harmless but will not clear itself. Fix it with a failing test first, as for any engine defect.
* **Disable it:** stop the unit. The broadcast carries on with whatever was prepared and holds at the boundary when that runs out.

## 7. Alert delivery: options only (nothing is sent, created or connected)

The supervisor stops at a file and an exit code. How a person is told is a separate decision; the options and their costs:

| Option | Needs | Credential / account | Sees a dead host? | Notes |
|---|---|---|---|---|
| **systemd failed unit + journal** (the check timer above) | nothing | none | no | visible with `systemctl --failed` and `journalctl`; nobody is *woken*. Zero setup. |
| **`OnFailure=` hook to a local notifier** | a notifier script the owner writes | depends on the channel | no | the hook is one line in the check unit; **not created here** |
| **Email from the host** (local MTA or a relay) | an SMTP relay | **yes** (relay account) | no | deliverability and spam folders are the usual failures |
| **Chat or push webhook** (Slack, Discord, ntfy, Pushover, …) | a webhook URL or token | **yes** | no | easy; the token must be stored with root-only permissions |
| **Off-host dead-man's switch / uptime monitor** polling the public `status.json` (and `latest.json` age) | an account with an uptime service, or a second small host running `curl` | **yes** or a second host | **yes** | the only option that notices the host being down or the network cut; it can watch only what is public (publication, not the builder or capacity) |
| **Off-host pull of `health.json`** | a read-only path (an SSH key restricted to one command, or a tiny authenticated endpoint) | **yes** | **yes** | gives the builder and capacity problems an off-host watcher; adds an exposed surface, so it needs its own review. `health.json` must **not** be put in the public folder |

A reasonable minimum, for the owner to choose (not decided here): the check timer (free) **plus** one off-host uptime check on the public `status.json`, because an on-host alert cannot report that the host is gone. The most important alerts, in order: `capacity_insufficient`, `publication_stalled`, `builder_failed` at critical, `broadcast_holding` at critical, `supervisor_stopped`.

## 8. Tests (all local; the engine, the Droplet and the rehearsal untouched)

`tests/test_broadcast_supervisor.py` (31 tests; about 35 s) drives the **real builder command line** with its engine part replaced (`tests/fake_builder.py`: the same lock, arguments, `step` loop, `next-season`, status, season selection and completeness refusals; only what one event costs, the new plan and the league's event count are synthetic) over the four-event synthetic "year" of `tests/test_multiseason.py`, with the real daemon (`SeasonBroadcaster`) in virtual time where the clock matters, and real child processes for kill, duplicate-start and SIGTERM.

| Scenario asked for | Tests |
|---|---|
| Successful rollover | a complete season gets its successor prepared and built, then nothing more (one ahead); an unfinished season is finished first; **through the daemon**: the hand-over happens, the supervisor prepares the season after, every earlier public file is still there byte-identical, finished plan and builds untouched; a crash inside `next-season` leaves no half season |
| Slow builder | the clock holds at the prepared boundary, never past it, never back, never jumps, **no viewing time skipped**, health says `broadcast_holding`, and it continues; a fast builder never holds and `lead_s` is positive |
| Builder failure | reported, retried with backoff, critical at 3, no attempt before the backoff, recovers, every event built exactly once; failure changes nothing public or finished; a failed `next-season` is reported; backoff survives a restart and `--retry-now` overrides it |
| Restart | a killed supervisor is replaced and the season is finished with each event built, never two builders at once; SIGTERM stops the supervisor and its builder step and records `stopped` |
| Duplicate startup | a second supervisor exits 75 and changes nothing (health file, builder calls); a builder held by someone else is `busy`, not a failure, `builder_busy_long` after 15 min |
| Insufficient capacity | disk below the floor: nothing built, store and public byte-identical, critical, no failure count; inodes likewise; a season must fit before the next is prepared; tight-but-enough warns and builds; a disk that fills mid-step is capacity, not failure; the real `statvfs` probe; the command line refuses below an absurd floor; a low public volume is reported while building goes on |
| Health | stalled publication (lease or heartbeat), a pause is not a stall, a failed export, store not continuous, the document's fields, the `check` exit codes (stale and missing are unknown, stopped is critical), the `check` command line |
| No deletion | a parsed-source check for delete/move/truncate calls; a long stretch with failures and recovery only ever adds files and never writes the public folder |

`tests/test_supervisor_real.py` is the **real-engine smoke check**: it needs a finished first season (`DFL_TEST_YEAR1_ROOT`, only ever copied) and is skipped otherwise. See the test record below for the run.

Test record (2026-11-07, this container, 2 vCPU):

| Run | Result |
|---|---|
| `tests.test_broadcast_supervisor` (31 synthetic scenarios) | OK, 32 s |
| `tests.test_supervisor_real` with `DFL_TEST_YEAR1_ROOT` = a copy of a finished first season (real builder and engine: `next-season` for season 2, event base 47, then 3 real events, events 48 to 50, league 50 events, season 1 plan and builds byte-identical, health `ok`) | OK, 78 s |
| `tests.test_broadcast_build tests.test_multiseason` (builder and daemon continuity, with the atomic `next-season` change) | 28 OK, 173 s |
| `tests.test_recovery_set tests.test_backup_resume` (recovery sets take the same lock) | 24 OK, 468 s |
| `git diff` of `src/dfl`, `data`, `site` against the base commit | empty |

Not run, on purpose: the full suite (about an hour; this change touches only `tools/` and adds `tests/`, `deploy/`, `docs/`), and anything on the Droplet.

## 9. Integration requirements and what this branch does not include

* **Not proven and not claimed:** the supervisor on the Droplet; with the real daemon and real engine across a **real** season boundary (that is the boundary rehearsal M5, which needs the rehearsal host free); the builder's effect on the daemon on a 1 vCPU host while both run (CPU and I/O contention is untested; the unit's `Nice`/`CPUWeight` are a guess); `MemoryMax`; a full real season built under it (the smoke check builds a season's first three events; the builder's own full-season runs are documented in `docs/BROADCAST_CONTINUITY.md`).
* **The monitor fix is not here.** `backend/monitor-tmp-race-2026-11-06` (the temporary-file race, `5118701`) and `backend/unattended-m6-2026-11-07` are independent branches; merge order is the owner's. The supervisor does not replace the monitor.
* **Per-season public cap (a capacity safeguard in the roadmap) is not implemented.** The daemon's `--max-public-gb` is fixed by its unit line; a production unit copied from the rehearsal's 3 GB would stop publishing around the end of season 3. That is a unit setting plus a rule needing the owner's number; the supervisor only reports public-volume room.
* **No automatic space recovery**, by design. Retention (M8) is a separate, approval-gated item.
* **Alerts:** section 7 is options only. Nothing is connected.
* **Installer:** none written; the templates need a rendering and install script like `install_rehearsal.sh`, to be reviewed before use.
* **The root must exist and be marked continuous** (`init --continuous`); adopting the existing league (M4) is a different builder feature and is not needed here.
* **`lead_s`** is a measure, not a promise: across a season boundary it adds the successor's built seconds to the season on air's remainder and ignores overruns not yet built.
* **The builder lock and the league runner's own lock are the only exclusion;** the supervisor assumes one builder root per host.

## 10. What still depends on the rehearsal and the owner

The running rehearsal's verdict decides whether the daemon, monitor and package are fit; this work does not change them. Before any release decision: the boundary rehearsal (M5) with the supervisor running against the real daemon, a scratch-host run of two short seasons with an injected builder failure, a daemon kill and a simulated low-disk pause with no human step (the roadmap's M6 test), the owner's capacity numbers (floor, season estimate, public cap per season) and the alert channel (section 7).
