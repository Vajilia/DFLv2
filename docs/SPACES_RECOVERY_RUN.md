# Guarded recovery-set run: private Spaces upload and first restore drill

`tools/recovery_run/dfl-recovery-run.sh` builds a verified DFL recovery set, uploads it **privately** to a DigitalOcean Space, then downloads it and
proves it restores. It reads production files and writes only to its own run folder and to new objects in the bucket. It never deletes, overwrites,
or starts/stops a service. Nothing in this repository contains a credential or the bucket name (`export BUCKET=...`).

Status: rehearsed locally on synthetic data against a fake S3 (`tools/recovery_run/rehearsal/`). **Not yet run against production or real Spaces.**

## What a run contains
| Part | Tool (pinned) | Why |
|---|---|---|
| League recovery set: active league + archive, the **exact event-141 upgrade input** (identified by the SHA-256 in the lineage), the preserved original, the originating code, the code that runs the active league | `tools/league_recovery_set.py` @ `0f72562bb8231b6c1af3671560953f38b4024959` | the active file alone has no history before event 142 |
| Public feed backup (`--require-quiescent`) | `tools/public_backup.py` @ `284125a40826e3a40a18badf46378a90c8ef26eb` | restorable site, with the exporter still rewriting `status.json` |
| `RECOVERY_HISTORY.md` | generated | what each piece is and how to recover it |

`set.tar` and `set.tar.sha256` (which names `set.tar`) are uploaded and downloaded under the same names, so `sha256sum -c` works on the downloaded copy.

## Stages (each runs only if the previous one finished OK; a stage never runs twice)
`check` → `canary` → `create` → `verify` → `package` → `upload` → `drill`. `all` runs them in order and stops at the first failure.

* **check**: paths, pins, credential file mode 600, disk (`free - 6 x S >= 8 GiB`), outside every web root, the league is stopped at the expected event,
  the Spaces credential can list the bucket, the CDN setting is recorded, the INDEPENDENT-mode preflight passes, and the **existing recovery-set artifact is located**:
  a regular file of exactly 622,387,200 bytes whose **full** SHA-256 is `5edea873ad660ee1dfd47cfc8773eefc9a176c5b744f2fe3e5f1c974167d133e`
  (it is searched for under `SEARCH_DIRS`, or tried at `EXISTING_ARTIFACT`; no path is assumed). Its `RECOVERY_SET.json` must equal the one in `EXISTING_SET`.
* **canary**: upload a private random canary, retrieve it **with the key** and compare its content, then confirm an **anonymous** request is denied (401/403)
  on every working origin endpoint. No public object is ever made. A probe that cannot connect fails the stage.
* **create / verify / package**: the set is built and verified, and its history is compared with the existing set (same upgrade input hash, same equivalent copy,
  same original head and restore points, same code fingerprints, same lineage, same active head). The artifact is re-hashed at `verify`.
* **upload**: new unique prefix `sets/<run id>/`, never overwritten. ACL and anonymous probes on `set.tar` and `set.tar.sha256` pass **before** `UPLOAD-COMPLETE.json` is written.
* **drill**: download, checksum, verify, restore the feed (compared with the **captured** backup, never with the moving live folder), restore-test the league set with the original
  server paths hidden (`INDEPENDENT=1`, default). Restore-point replay is off unless `REPLAY_FROM` is set (about 26 minutes on the droplet). `DRILL-VERIFIED.json` is uploaded last.

## Markers in `s3://<bucket>/sets/<run id>/`
| Object | Meaning |
|---|---|
| `UPLOAD-COMPLETE.json` | upload finished, sizes match, the data objects are private. **Not** proof the set restores. |
| `DRILL-VERIFIED.json` | the downloaded copy passed every drill check (records: same host, independent yes/no, replay not run/run, CDN state). |
| `FAILED.json` | something failed after the upload began (best effort; the run's local `FAILED` file is authoritative). |

**A usable verified set = `DRILL-VERIFIED.json` present and `FAILED.json` absent.** The old single `COMPLETE.json` no longer exists.

## Exit codes
* `STOP` (exit 1): a stage failed. The run is marked `FAILED` (the first failure is kept), the run is never continued; move it aside (`mv`, nothing is deleted) and start a new run.
* `REFUSED` (exit 3): not allowed (another process holds the lock, the run already failed, out-of-order or repeated stage). A refusal changes **nothing**: no file in the run, no log line, no bucket object.
* `status <run id>` and `locate` are read-only, take no lock and work on any run.

## CDN
The bucket's CDN is **disabled**. A hostname that does not exist can fail DNS or TLS, which proves nothing about whether an object is private, so it is recorded as disabled and **not probed**.
Authenticated retrieval plus anonymous denial are required on the working origin endpoints (virtual-host and path style). Set `CDN_ENABLED=1` to probe the CDN as well (then it must work and deny).

## Credential
One Limited Access key, scoped to this bucket only, Read/Write/Delete (the smallest level DigitalOcean offers that can upload). That key **can delete or overwrite everything in the bucket**:
bucket versioning helps recovery from overwrites and accidental deletes but does not stop a delete-capable credential from destroying history. Keep the key in a root-only 0600 file entered with hidden
prompts, never in chat, GitHub, logs or a web root, and **revoke the first-drill key after the verified drill**. The script never issues delete or overwrite commands.

## Production use (as root, one stage at a time)
```
export BUCKET=<private bucket>  EXISTING_SET=<folder of the 2026-11-03 recovery set>
export INDEPENDENT=1            # production drill: hide /srv/dflv2 and /opt/dflv2-src during the restore test (default)
                                # REPLAY_FROM stays unset (slow replay off)
R=$(./dfl-recovery-run.sh new); for s in check canary create verify package upload drill; do ./dfl-recovery-run.sh $s $R || break; done
```
Read-only discovery before anything is installed or run: `find / -xdev -type f -size 622387200c -exec sha256sum {} +` should print the full checksum above next to exactly the artifact.

## Local rehearsal
```
tools/recovery_run/rehearsal/build_rig.sh RIG_DIR        # synthetic league/feed/existing set; needs a git repo holding the pinned commits
tools/recovery_run/rehearsal/run_rehearsal.sh RIG_DIR OUT_DIR   # needs s3cmd and moto_server in a venv (VENV, default /tmp/s3v)
```
It runs the full procedure with `INDEPENDENT=1` while a loop rewrites `status.json`, then ~90 failure/regression checks: wrong upgrade input, flipped bytes, a missing feed file, unreachable Spaces, a swapped
remote object, public objects, a lying ACL view, stage order, a second process racing a running stage, artifact hash/size/manifest mismatches (including a hash equal in first 8 and last 4 digits only), CDN on/off.

## Not proven by the rehearsal
Real Spaces behaviour (status codes, the ACL of a large multipart upload, anonymous listing denial), outbound access from the server, production-scale timing, host-loss recovery, restore-point replay.
