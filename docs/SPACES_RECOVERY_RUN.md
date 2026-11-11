# Guarded recovery-set run: private Spaces upload and first restore drill

`tools/recovery_run/dfl-recovery-run.sh` builds a verified DFL recovery set, uploads it **privately** to a DigitalOcean Space, then downloads it and
proves it restores. It reads production files and writes only to its own run folder and to new objects in the bucket. It never deletes, overwrites,
or starts/stops a service. Nothing in this repository contains a credential or the bucket name (`export BUCKET=...`).

Status: rehearsed locally on synthetic data against a fake S3 (`tools/recovery_run/rehearsal/`: 128 passed). **First real drill passed on 2026-11-11** on the production droplet against a real private Space (`docs/evidence/SPACES_RECOVERY_DRILL_2026-11-11.md`): same host only; host-loss recovery and restore-point replay are not proven, and the Space's ACLs were never read.

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
* **upload**: new unique prefix `sets/<run id>/`, never overwritten. Every object is uploaded with `--acl-private`; for `set.tar` and `set.tar.sha256` the privacy checks below pass **before**
  `UPLOAD-COMPLETE.json` is written (and again for each marker): authenticated retrieval returns exactly the local bytes (streamed through SHA-256, nothing written to disk), and an anonymous GET is denied on every working origin endpoint.
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

## Privacy checks and the ACL (changed after the first production attempt, 2026-11-11)
Per object (the canary and every backup object): (1) uploaded with `--acl-private`; (2) the ACL is read when Spaces allows it, and a public grant fails the run; (3) **authenticated retrieval** returns
exactly the local bytes, so the object exists and a later 403 is not just "no such key"; (4) an **anonymous GET** is denied (401/403) on **every** working origin endpoint.

A bucket-scoped Spaces key is **not allowed to read ACLs**: `s3cmd info` shows the object's metadata and then the ACL read returns `403 AccessDenied`. That one, specific refusal is tolerated:
the object's metadata read must have worked, no ACL line may have been shown, and the only error must be `S3 error: 403 (AccessDenied)`. Anything else fails the stage: bad credentials
(`SignatureDoesNotMatch`, `InvalidAccessKeyId`), a refused metadata read, a missing object, a network error, any other status. When the ACL could not be read the log and the markers say exactly
*"ACL inspection unavailable; privacy verified by authenticated retrieval and anonymous probes."* and never describe the ACL itself as verified. Each run records the per-object outcome in `acl-inspection.txt`.

## Credentials never reach a log
`check` validates the shape of the credential file (names, an access key of 20 capitals/digits, a plausible secret length) and prints only line numbers and the kind of problem, never a value.
Every `s3cmd` call runs through a wrapper that holds its stderr in memory, replaces s3cmd's "Ignoring invalid line in ...: <the line>" warning (which quotes the offending line, possibly the secret) with a fixed sentence,
masks the access key and secret values wherever they appear, and only then passes the text on.

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
remote object, public objects, a lying ACL view, the ACL-read-only refusal and every near miss (bad credentials, unknown key, refused metadata read, missing object, network error, other 403/500, public object with an unreadable ACL, different bytes), credential text in logs (malformed file, noisy client), stage order, a second process racing a running stage, artifact hash/size/manifest mismatches (including a hash equal in first 8 and last 4 digits only), CDN on/off.

## Not proven by the rehearsal
Real Spaces behaviour beyond what was observed on 2026-11-11 (the ACL-read refusal above), the ACL of a large multipart upload, anonymous listing denial, outbound access from the server, production-scale timing, host-loss recovery, restore-point replay.
