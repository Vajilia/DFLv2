# Rehearsal trip `monitor_error: FileNotFoundError` on a `.tmp-` file (2026-11-06)

> **SUPERSEDED in part (2026-11-06, later).** (1) The fix at `1c50450` missed `status.json`'s `.tmp-status.json-<pid>` name; the corrected fix is `5118701` (monitor SHA-256 `f3066c71ac32dfe85f764b46b7fcbd315a9ae545b80bad4ef919d5cfc9fc7319`, not the `4579f23e...` hash below). (2) Section 5 (patch the monitor in place using a hard-linked copy and resume the paused run) is **not the plan any more**: hard links were rejected, and the run is `FAILED` and cannot finish before its deadline. The plan is a separate clean run from position zero: `docs/FRESH_REHEARSAL_2026-11-06.md`. The rest (cause, tests, limits) stands.


Branch `backend/monitor-tmp-race-2026-11-06`, one commit on top of the installed package commit `c28ca13696b0d8607fe92e7509cc2edbbdc07738`. **Nothing on the rehearsal host was read, changed, cleared or resumed by this work.** The rehearsal stays paused with its latch, clock, public files and logs as the trip left them.

## 1. Cause

The daemon publishes every file atomically (`src/dflsite/broadcast.py`: write `.tmp-<pid>-<name>`, then `os.replace` onto the final name). The monitor (`tools/rehearsal_check.py`) lists the public tree and then looks at what it listed. A `.tmp-` file that was listed can be renamed away before the look. `os.path.getmtime` / `getsize` then raise `FileNotFoundError`, the run loop's catch-all turns any exception into `monitor_error`, and `monitor_error` is a trip kind, so the guard paused the broadcast. Nothing was wrong with the broadcast.

Two places had the race (both reproduced locally, both fixed):

| Where | Code at `c28ca13696` | When |
|---|---|---|
| stale-debris scan, every cheap pass (about every 10 s) | `os.path.getmtime(p)` on each listed `.tmp-` name | cheap pass |
| size sum, every full pass (about every 30 s) | `os.path.getsize(...)` over the whole listing, which includes any `.tmp-` names | full pass |

**Limit of this diagnosis:** I did not read the trip's traceback on the host (the evidence is untouched). The tmp name in the report fits either place; the traceback in `monitor/evidence/trip-*-monitor_error/evidence.json` says which. The owner can paste it; the fix covers both.

**A third effect of the same cause, found while testing:** a `.tmp-` file that is merely present (not yet renamed) when the full pass lists the tree was reported as `early_or_unknown_file`, which is a **spoiler-class trip and, by the rehearsal's own rules, an abort condition**. It was masked in the observed trip only because the `getsize` error came first. It is fixed too (section 2).

Why it showed once in about 6 hours: the window is the few milliseconds between write and rename. A deliberately tight local stress (the daemon's write pattern in a loop against back-to-back passes) hit it constantly, which shows the mechanism, not the real rate.

## 2. The patch (smallest that closes it)

`tools/rehearsal_check.py` only, +41 / -5 lines; tests in `tests/test_rehearsal_check.py`.

* `is_tmp(name)`: the one definition of a daemon temporary name (`.tmp-` prefix or `.tmp` suffix, as before).
* Debris scan: `FileNotFoundError` from `getmtime` on a temporary name means it was renamed into place: skip it. Any other error (for example `PermissionError`) is not caught. A temporary file that stays and is older than 120 s is still `stale_tmp`.
* Size sum: `FileNotFoundError` is tolerated **only** for a temporary name. A vanished published file still raises (so it still ends in `monitor_error`, as before).
* `in_flight()`: a temporary file is excluded from "unexpected files" only when it is fresh (120 s or less, or already gone) **and** its final name (`.tmp-<pid>-<name>` or `.tmp-<name>`) is a name the broadcast position allows. A temporary file for a name that is not yet allowed, a stale one, and any non-temporary unknown file are still `early_or_unknown_file`.
* The run loop's `except Exception -> monitor_error` is untouched. No broad suppression was added.

Pin: patch `docs/patches/monitor-tmp-race-on-c28ca13696.patch` applies cleanly to a clean checkout of `c28ca13696` and gives `tools/rehearsal_check.py` SHA-256 `4579f23e9d734f31c9b716c46dcdf32b07acb52046495d24d9817c50c81f6115`. The file as installed must first match the old hash `e649baa458214b4af09018812bf8a9f4cf6c197dab8bd4911f2df0be9c958955` (`git show c28ca13696:tools/rehearsal_check.py | sha256sum`).

## 3. Tests (all run locally, none on the host)

Failing first: the new class `TemporaryFileRace` showed 2 errors (`FileNotFoundError` from `getmtime` and from `getsize`, with the host's file name pattern) and 1 failure (an in-flight temporary file reported as `early_or_unknown_file`). After the change:

| Check | Result |
|---|---|
| `tests/test_rehearsal_check.py` (21: 10 existing + 11 new) | OK |
| `tests/test_rehearsal_guard.py` (19: each trip kind pauses the daemon, first evidence kept, latch, release, resume refusals) | OK |
| `tests/test_broadcast_guard.py` (4) | OK |
| `tests/test_rehearsal_traversal.py` (4: private files not reachable) | OK |

Skipped: none. Not run: `test_rehearsal_package.py` and `test_rehearsal_deploy.py` (they build or inspect the package; the new package is built only if you approve), the full suite.

What the new tests assert, so the narrowing is visible: renamed after listing is no error (cheap and full pass); an in-flight temporary file of an allowed name is no finding; a stale temporary file is still `stale_tmp` and still `early_or_unknown_file`; a temporary file for a name the position does not allow is still `early_or_unknown_file`; an unknown non-temporary file is still `early_or_unknown_file`; a published file that vanishes is still an error; a `PermissionError` on a temporary file is still raised; the catch-all that produces `monitor_error` is still in the run loop.

Stress, same machine, 20 s each, real atomic rewrites of 40 allowed files against back-to-back monitor passes: old monitor 5,634 `FileNotFoundError` in 11,384 passes plus `early_or_unknown_file`; patched monitor 0 errors in 8,580 passes and no finding. (An unrealistically tight loop: it demonstrates the mechanism, not the real rate.)

**Spoiler checks:** the spoiler-class detections (early file, wrong pointer, future URL, private exposure, traversal) are covered by the existing monitor, guard and traversal tests above (all passing) plus the four narrowing tests. They are not proven against the real daemon on the host.

## 4. What this does to the rehearsal's verdict (read this before deciding)

By the rehearsal's own definition (`docs/BROADCAST_REHEARSAL.md` section 1) `COMPLETE` needs zero violation entries and no automatic pause; any violation entry or automatic pause gives `FAILED`. **This run can no longer end as `COMPLETE`**, whatever is done now. The trip is a monitor failure, not a spoiler, so it is not an abort condition; continuing is a choice with a different purpose: the remaining evidence the run gives (real-phone behaviour, load on the 1 vCPU / 2 GB host, the daemon and builder over the full 48 hours, pause and recovery in real time) is still real, and the cause is understood and fixed. A clean `COMPLETE` verdict needs a new run (a new 54-hour cap, a new approval).

## 5. Proposed deployment (monitor only; needs your approval; nothing below has been done)

Why not the installer: `install_rehearsal.sh` rewrites the environment file with a deadline in the PAST and rewrites units and the Caddy block. It must **not** be run now. The daemon unit, the environment file, Caddy, the public folder and the store are not touched at all.

A. **Record the position before touching anything** (read-only): copy `store/clock.json`, `feed/status.json`, `feed/broadcast/clock.json`, `monitor/TRIPPED`, `monitor/trips.jsonl`, the `monitor/evidence/trip-*` folder and `journalctl -u dfl-rehearsal -u dfl-rehearsal-monitor` to a new folder outside the run; write down `sha256` of the clocks and the public file count. The clock must say `paused`; the position P0 in it is what must be unchanged at the end.

B. **Stage the patched code beside the old code** (the old tree stays byte-identical):

```
cp -al /opt/dflv2-src/c28ca13696b0d8607fe92e7509cc2edbbdc07738 /opt/dflv2-src/c28ca13696b0d8607fe92e7509cc2edbbdc07738-monfix   # hard links, no extra space
cd /opt/dflv2-src/c28ca13696b0d8607fe92e7509cc2edbbdc07738-monfix
sha256sum tools/rehearsal_check.py                          # must be e649baa4...8955 (the old file)
cp tools/rehearsal_check.py /tmp/rc.old && patch -p1 --backup-if-mismatch -o /tmp/rc.new tools/rehearsal_check.py < monitor-tmp-race-on-c28ca13696.patch && mv /tmp/rc.new tools/rehearsal_check.py   # a NEW inode: never edit a hard-linked file in place
sha256sum tools/rehearsal_check.py                          # must be 4579f23e...6115
diff -rq /opt/dflv2-src/c28ca13696... .                     # exactly one difference: tools/rehearsal_check.py
```

C. **Point only the monitor at it** with a drop-in (the daemon's unit is not touched):

```
/etc/systemd/system/dfl-rehearsal-monitor.service.d/10-tmp-race.conf
[Service]
ExecStart=
ExecStart=/usr/bin/python3 -I /opt/dflv2-src/<new dir>/tools/rehearsal_check.py watch <the same arguments as the installed unit>
```

then `systemctl daemon-reload` and `systemctl restart dfl-rehearsal-monitor`. The daemon keeps running and stays paused behind its latch. The monitor is down for a few seconds; the daemon trips itself only after 120 s without a heartbeat, and the latch already exists (a later trip only appends a line to `trips.jsonl`). A restarted monitor starts its in-memory high-water marks (published events, release) from zero, so "went backwards" is not checked against the earlier run for the rest of the run: stated loss, accepted. Its first evidence file of each violation kind is kept; the earlier one is not overwritten.

D. **Verify before resuming:** the new monitor logs heartbeats with `"ok": true` and no new violation; `clock.json` is still `paused` and equal to the copy from A.

E. **Release and resume, preserving the position** (the two documented commands, each with the new code dir; never by deleting `PAUSE`):

```
python3 -I <new dir>/tools/rehearsal_check.py release --guard /srv/dflv2-rehearsal/monitor --reason "monitor_error was a .tmp- file renamed during atomic publication; monitor patched (docs/MONITOR_TMP_RACE_2026-11-06.md), evidence kept"
python3 -I <new dir>/tools/broadcast_run.py resume --root /srv/dflv2-rehearsal/private --guard /srv/dflv2-rehearsal/monitor
```

The resume continues the broadcast clock from P0 (the clock is the broadcast's own and does not advance while paused; proven in virtual time and by the pause drill, `docs/BROADCAST_REHEARSAL.md` section 8); nothing is replayed and nothing is skipped. The first status after resume must show P0's play and no file appears early.

F. **Rollback if D fails:** remove the drop-in, `daemon-reload`, restart the monitor (old code, same bug), leave the broadcast paused. Nothing else changed.

Not covered: the patched monitor is tested locally and not against the real daemon on the host; the first hours after resume are its real test.

## 6. Revised timing

Every minute of pause counts against the cap: the deadline `2026-11-08T14:18:10Z` is fixed and the broadcast needs `48 h + 345 s` of running time in total, minus the position P0 already reached. Finish time = (resume time) + (48.096 h - P0). So the latest resume that still finishes by the deadline is `14:18:10Z on Nov 8 - (48.096 h - P0)`. I do not have P0 or the time of the trip (the evidence is untouched and I did not read the host), so I cannot give the number; with the documented plan the total slack was about 5.9 h, and each hour paused uses one hour of it. **Please send me, or authorise a read-only look at, the trip time and `clock.json` position, and I will give the exact latest resume time.** Doing steps A to E takes about 10 to 15 minutes once approved.

If the slack would be exhausted, the run ends `INCOMPLETE` at the deadline, as already documented; extending the deadline is a new approval and is not proposed here.

## 7. What is not claimed

* The traceback of the actual trip has not been read; the diagnosis is from the code and a local reproduction.
* Not proven on the host: the patched monitor against the real daemon.
* The run's verdict cannot be `COMPLETE` any more (section 4).
* The earlier unexplained frontend and `test_site_broadcast.py` observations are unrelated and stay open.
