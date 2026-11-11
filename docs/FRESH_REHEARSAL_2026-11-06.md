# Fresh rehearsal: preparation (2026-11-06)

**Prepared only. Nothing was switched, stopped, started, installed, purged or reconfigured on the Droplet; production stays stopped at event 188.** Branch `backend/monitor-tmp-race-2026-11-06`. The exact commit and the package built from it are in `docs/FRESH_REHEARSAL_PACKAGE_RECORD_2026-11-06.md` (it cannot be written here: the package pins the commit that contains this file).

## 1. Revision, and a correction to `1c50450`

The request named `1c50450`. **That revision is not enough and is superseded.** While preparing the run I read every temporary-file name the daemon writes (`src/dflsite/broadcast.py` put and copy, `src/dflsite/status.py`) and found one the fix did not recognise: `status.json` is rewritten every second through `.tmp-status.json-<pid>` (pid last, with an fsync before the rename). With `1c50450`, a full pass that listed that file would still report `early_or_unknown_file`, a spoiler-class trip. A failing test came first (`test_the_status_writers_temporary_name_is_in_flight_not_an_early_file`), then commit `5118701`. The fix is `5118701` plus the preparation files in the commit named in the package record.

## 2. Read-only inspection of the Droplet

**What I could and could not do.** I have no shell on the Droplet and did not log in to anything. I read only the public rehearsal pages, through the web-fetch tool (it summarises, so numbers are "about"):

| Item | Seen | Source |
|---|---|---|
| Broadcast clock | `state: paused`, epoch 2, position about 3,361.5 s, anchor wall time 2026-11-06 09:14:13Z | `/feed/broadcast/clock.json` |
| Status | runner `paused`, 11 of 11 built-and-published events, release `e00011-b019c73497`, export OK, last publish 09:14:12Z | `/feed/status.json` |
| Public pointer | `latest.json` = `e00011-b019c73497` | `/feed/latest.json` |
| Time of the pause | 09:14:13Z, **56 minutes after the clock started** (start = 09:14:13Z - 3,361.5 s = about 08:18:12Z, which equals the deadline `2026-11-08T14:18:10Z` minus 54 h) | derived |

**Not confirmed (needs a shell; I did not look and did not work around the block):** the traceback itself, `monitor/TRIPPED`, the evidence folder contents, `watch.log`, the private clock, the units' state, the free disk, and why the clock is at epoch 2 (a restart or hand-back earlier in the run; the journal will say). The diagnosis in `docs/MONITOR_TMP_RACE_2026-11-06.md` stands as a diagnosis from the code and a local reproduction, with the traceback still to be matched against it. **Please run these read-only commands as root and paste the output** (they change nothing):

```
cat /srv/dflv2-rehearsal/monitor/TRIPPED
ls -la /srv/dflv2-rehearsal/monitor/evidence/
python3 -c "import json,glob;[print(p,json.load(open(p)).get('detail'),json.load(open(p)).get('evidence',{}).get('traceback') if isinstance(json.load(open(p)).get('evidence'),dict) else '') for p in glob.glob('/srv/dflv2-rehearsal/monitor/evidence/trip-*/evidence.json')]"
tail -n 5 /srv/dflv2-rehearsal/monitor/watch.log; grep -c '"violation"' /srv/dflv2-rehearsal/monitor/watch.log
cat /srv/dflv2-rehearsal/private/store/clock.json
systemctl is-active dfl-rehearsal dfl-rehearsal-monitor; cat /etc/dflv2/rehearsal.env; df -h /srv /root; ls /opt/dflv2-src /root/rehearsal-quarantine
```

**Remaining deadline of the failed run.** Deadline `2026-11-08T14:18:10Z`. At the time of my look (2026-11-06 15:46Z) it had 46.5 h left. The broadcast still needed 48 h + 345 s - 3,361.5 s = **47.2 h** of running time. **It cannot finish before its deadline even if resumed that instant (short by about 38 minutes), and 6.5 hours of its slack were already spent paused.** By its own rules it is `FAILED` in any case (`docs/BROADCAST_REHEARSAL.md` section 1: any violation entry or automatic pause). It stays `FAILED`.

## 3. Tests

Unit tests, all run locally (nothing on the host), none skipped:

| Suite | Result |
|---|---|
| `tests/test_rehearsal_check.py` (24; 14 are the new `TemporaryFileRace` class, 3 of them failed before `5118701`) | OK |
| `tests/test_rehearsal_guard.py` (19) | OK |
| `tests/test_broadcast_guard.py` (4) | OK |
| `tests/test_rehearsal_traversal.py` (4) | OK |
| `tests/test_fresh_run.py` (12, the new start/preserve/prepare script, with recording stand-ins) | OK |

Not run: `test_rehearsal_package.py`, `test_rehearsal_deploy.py`, the full suite.

**Live drill (`tools/monitor_live_drill.py`).** The patched monitor and a REAL daemon process, in real time, both started from an independent unpacked copy of the code (`git archive` of the revision: no hard links, a different inode for every file), a synthetic season with live segments (the first live window opens at 200 s), monitor passes every 1 s and 3 s. The monitor file in the tree was checked against the pinned SHA-256 first. Result: **18 of 18 expectations held.**

* revision 51187017a5a8f0c88325acb5514788c203c3b0c3; tools/rehearsal_check.py sha256 f3066c71ac32dfe85f764b46b7fcbd315a9ae545b80bad4ef919d5cfc9fc7319
* race exposure: a daemon temporary file was visible in 98 of 123852 samples (2 ms apart) during the run
* future file: releases/e00003-0000000003/manifest.json (already public before the injection: False; position 276.1)
* C1: a planted copy of a managed future file was removed after 0.3 s (daemon self-heal or monitor trip)
* violation kinds logged: ['early_or_unknown_file', 'stale_tmp']

| expectation | result | detail |
|---|---|---|
| independent tree: no hard links | PASS | max link count 1 |
| rehearsal_check.py in the tree is the pinned file | PASS | f3066c71ac32dfe8 |
| monitor and daemon still running after the clean phase | PASS | monitor None daemon None |
| clean phase: full passes logged and all ok | PASS | 90 heartbeats, positions 0.1..267.1 |
| clean phase: no violation of any kind, no monitor_error | PASS | violations: [] |
| the live window was open (public files grew past the offseason) | PASS | last full pass {'at': 1793981835.6, 'free_gb': 9.1, 'heartbeat': True, 'ok': True, 'pos': 267.1, 'public_files': 38, 'public_gb': 0.0} |
| deliberate stale temporary file is reported as stale_tmp | PASS | after 0.5 s |
| the stale temporary file also trips the monitor, as it did before the fix | PASS | kind early_or_unknown_file |
| the daemon paused behind that latch | PASS |  |
| release of the latch with a reason | PASS | released (kept as /tmp/drill/monitor/evidence/released/20261106T161722Z-TRIPPED.json). The daemon is still PAUSED. To continue:  broadcast_run.py resume --root  |
| resume with --guard | PASS |  |
| the broadcast position was preserved across the pause (no jump forward, nothing replayed) | PASS | paused at 271.1, 6.0 s later 276.8 |
| no new finding after the resume | PASS | [] |
| unauthorized future file trips the monitor (early_or_unknown_file) and names that file | PASS | kind early_or_unknown_file after 2.3 s |
| the daemon paused behind the latch | PASS |  |
| status.json says paused | PASS | paused |
| trip evidence folder written | PASS |  |
| no monitor_error in the whole run | PASS | [] |

Things the drill showed that are worth knowing:

* **The race window is real and small:** a daemon temporary file was visible in about 0.08 % of 2 ms samples (98 of 123,852). The old monitor's listing-then-stat pattern makes that a trip eventually (the failed run hit it after 56 minutes); a tight local stress made the old monitor fail in 5,634 of 11,384 passes and the patched one in none.
* **A stale temporary file trips the broadcast** (`stale_tmp` is logged, and the same file is also an unexpected file in the full pass, so `early_or_unknown_file` fires). This is the installed monitor's behaviour, unchanged by the fix. `docs/BROADCAST_REHEARSAL.md` described it as "logged only"; that row now carries a correction note.
* **The daemon repairs a planted copy of a file it manages in 0.3 s** (here a future release); the monitor catches what the daemon does not manage. So the spoiler drill uses a file the daemon ignores (`playback/games/results-leak.json`), which tripped the monitor in 2.3 s and paused the daemon.
* **The documented recovery was run for real** (remove the cause, `release --reason`, `resume --guard`): the broadcast position went from 271.1 s to 276.7 s over the 6 s that followed: preserved, nothing skipped, no replay.

Not proven: the patched monitor against the real daemon on the 1 vCPU / 2 GB host, and over 48 hours.

## 4. Independent pinned code tree

A rehearsal package is built from the final commit with `tools/build_rehearsal_package.py --commit <commit> --root /tmp/season/R1 --out OUT --force-overrun w041:450` (the same season root and overrun as before; a new random canary). The package holds `repo/` (`git archive` of the commit), `store.tar.gz`, `PACKAGE.json` and `SHA256SUMS`; its size and SHA-256 are in the package record. On the Droplet `fresh_run.sh prepare` copies `repo/` with `cp -a` into a **new** directory `/opt/dflv2-src/<commit>`, refuses an existing directory, and refuses if any file in it has a second hard link. The preserved installation (`/opt/dflv2-src/c28ca13696...`) is never opened for writing, so no edit can reach it.

## 5. Preserving the failed run (private, timestamped, nothing deleted)

`sh fresh_run.sh preserve --apply` (the daemon stopped first, then the monitor):

* **moves** (a rename on one filesystem; nothing is copied or deleted) `/srv/dflv2-rehearsal/private`, `.../feed` and `.../monitor` to `/root/rehearsal-failed-runs/<UTC stamp>/` (mode 700, root only, outside every served path and outside `/srv/dflv2-rehearsal`);
* **copies** `rehearsal.env`, both unit files, the Caddy block, `.installed_commit`, the journal of both units, `df` and `systemctl status` into `.../config/`, `journal.txt`, `state.txt`;
* writes `FAILED_RUN.txt` (verdict FAILED and why), `MANIFEST.sha256` (SHA-256 of every file as moved), and makes the whole folder read-only;
* refuses if a unit is active, if the target exists, if the folders are missing, or if the target is on another filesystem than `/srv/dflv2-rehearsal` (a move would then copy and delete).

Untouched: `/root/rehearsal-quarantine/20261105T174305Z`, `/srv/dflv2-rehearsal/site`, the old code directories, rollback copies, backups, `/srv/dflv2`, Caddy and its block, DNS.

## 6. Proposed commands (not run)

```
# A. owner-approved read-only look (section 2), then upload the package and its .sha256 to /root
cd /root && sha256sum -c dfl-rehearsal-*-<commit12>.tar.sha256
mkdir rehearsal-pkg-<commit12> && tar -xf dfl-rehearsal-*-<commit12>.tar -C rehearsal-pkg-<commit12>
PKG=$(echo /root/rehearsal-pkg-<commit12>/dfl-rehearsal-*)
cat $PKG/PACKAGE.json | head -c 600
# B. stop (the failed run is paused behind its latch; this keeps everything)
systemctl stop dfl-rehearsal && systemctl stop dfl-rehearsal-monitor
# C. preserve (dry run, read it, then apply)
sh $PKG/repo/deploy/rehearsal/fresh_run.sh preserve
sh $PKG/repo/deploy/rehearsal/fresh_run.sh preserve --apply
( cd /root/rehearsal-failed-runs/* && sha256sum -c --quiet MANIFEST.sha256 && echo MANIFEST OK )
# D. prepare the clean run (dry run, then apply); this runs the 15-check pre-flight with the NEW code and starts nothing
sh $PKG/repo/deploy/rehearsal/fresh_run.sh prepare $PKG
sh $PKG/repo/deploy/rehearsal/fresh_run.sh prepare $PKG --apply
systemctl cat dfl-rehearsal dfl-rehearsal-monitor | grep ExecStart ; cat /etc/dflv2/rehearsal.env   # new commit in both; deadline in the PAST
# E. when you choose the moment (watch the first 90 minutes): the deadline is computed HERE, as now + 54 h, then monitor, then daemon
sh $PKG/repo/deploy/rehearsal/fresh_run.sh start --apply
```

Nothing in A to E runs the installer, edits Caddy or touches production. The rest of the rehearsal (HTTP rule checks at start, middle and end, the monitor-stop drill, the verdict) follows `docs/BROADCAST_REHEARSAL.md` sections 7 to 9 with the new commit in the paths.

## 7. Verdicts and timing

* **The first run is `FAILED` and stays so**: its monitor log has a `monitor_error` entry and an automatic pause. Nothing in this preparation changes, hides or re-labels that; it is kept whole under the timestamped folder with `FAILED_RUN.txt`.
* **The new run must earn its own result** from `rehearsal_check.py verdict` on its own folders: `COMPLETE` only with 47 of 47 events, 400 of 400 games, zero violation entries and no automatic pause, inside its own 54-hour cap. The unchanged rules apply to it, including that a trip left unattended eats the 5.9 h of slack.
* **Timing:** the broadcast needs 48 h + 345 s of running time; the cap is 54 h from the moment `start --apply` runs (the clock starts a second or two later, so the cap is a few seconds shorter than 54 h). If started at time S, the natural end is S + 48.1 h and the deadline S + 54 h. Steps A to D take about 30 to 40 minutes plus the pre-flight; the start waits for you.

## 8. Risks and limits

* The traceback is not read (section 2): if it shows a different cause, stop and revisit before starting.
* `fresh_run.sh` is tested against stand-ins and a synthetic package; its pre-flight and move steps run on the real package in a scratch tree (package record), never on the Droplet.
* The restarted monitor's high-water marks start empty, as for any new run.
* The patched monitor still trips on a stale temporary file; a hard-killed daemon sweeps its predecessor's temporary files at restart, as before.
* The synthetic drill proves the mechanism and the recovery path, not the host's behaviour over 48 hours.
