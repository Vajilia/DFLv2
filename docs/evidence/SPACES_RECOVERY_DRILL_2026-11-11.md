# First recovery drill against real Spaces: result record (2026-11-11)

Written after the run, from the run's own log and the bucket listing. **No credential, key identifier or secret appears here.** The Space's name is not a secret; its contents are private.

| Item | Value |
|---|---|
| Tool | `tools/recovery_run/dfl-recovery-run.sh` at commit `eb05ac9a3060f5516b6e13b92a2131c4a8cf7d97` (`backend/spaces-recovery-run-2026-11-11`), script SHA-256 `d99c10e4ef0f6986f80dfcbd522b453eb16d13620361282aeea9afe9b38710c9`; previous pin `9173eadb5294640e6b7603f16e0d044b57b39d7a` kept installed beside it |
| Run id | `20261111T121833Z-752289` |
| Where it ran | the production droplet (same host as the live league and site), as root, under `systemd-run --scope -p MemoryMax=700M -p CPUQuota=70% nice -n 19 ionice -c3` |
| Bucket | `dfl-recovery-sets-sfo3` (SFO3, private, CDN disabled), prefix `sets/20261111T121833Z-752289/` |
| Stages | `check`, `canary`, `create`, `verify`, `package`, `upload`, `drill`: all OK; `status` reads `DRILL-VERIFIED (uploaded, downloaded, restored and tested)` |
| Timings (about) | create 5 min, verify 11 min, package 33 s, upload 1.5 min (including the read-back), drill 21 min (12:40:14Z to 13:01:27Z) |
| Objects | `set.tar` 2,573,783,040 bytes; `set.tar.sha256` 74 bytes; `UPLOAD-COMPLETE.json` 995 bytes; `DRILL-VERIFIED.json` 1,400 bytes. No `FAILED.json`. |
| What the drill showed | the uploaded set was downloaded back under the same names, passed `sha256sum -c`, and restored in an isolated folder with `/srv/dflv2` and `/opt/dflv2-src` hidden from the restore test (private mount namespace); the restored feed was byte-identical to the backup captured before upload and to the downloaded copy |
| Privacy checks | every backup object uploaded `--acl-private`; authenticated retrieval returned exactly the local bytes (streamed through SHA-256); an anonymous GET was denied (401/403) on every origin endpoint (virtual-host and path style). **The ACL itself was never inspected:** a bucket-scoped key cannot read ACLs (`403 AccessDenied`), so the run recorded "ACL inspection unavailable; privacy verified by authenticated retrieval and anonymous probes." Only that specific refusal is tolerated; any other error, a readable ACL with a public grant, a missing object, a bad credential or an anonymously readable object fails the run (`tools/recovery_run/rehearsal/failtests.sh`, section P). |
| Resources | memory available stayed about 1.45 to 1.5 GB (the 700 MB cap was never reached; no out-of-memory event); free disk fell from about 25 GB to about 12 GB while the drill copies were on disk |
| Production afterwards | league stopped at event 188, feed `e00188-cf545fbf7b`; `dflv2-league` and `dfl-rehearsal` inactive; Caddy, the rehearsal monitor and the feed-export and site-probe timers unchanged |

## What this does NOT prove
* **Host loss.** Everything ran on the same host as the thing being protected. Recovery onto a different machine from the bucket alone was not exercised.
* **Restore-point replay** (`REPLAY_FROM`) was not run.
* **The Space's ACLs** were not read (above); privacy was shown by behaviour, not by inspecting the grants.
* Nothing is scheduled: no recurring upload, no pruning, no lifecycle rule. That stays off until the owner decides.

## Incidents during the day, kept as evidence (nothing deleted)
1. A first attempt (`failed-20261111T112508Z-749722`) failed on a malformed credential file; s3cmd's warning printed one line of the file into the log and the screen. **That key was revoked and replaced by the owner.** The log is kept on the droplet as evidence and still contains that (now revoked) secret line; it must not be copied anywhere.
2. A second attempt (`failed-20261111T114257Z-750711`) failed in the canary stage because the bucket-scoped key cannot read ACLs. Diagnosed with separate `s3cmd` calls; patched narrowly (commit `eb05ac9`) with the owner's approval; regression tests first.
3. The script now validates the credential file's shape (prints line numbers and lengths only), scrubs s3cmd's output in memory, and never prints raw parser errors.

## Open
* The temporary upload key (`dfl-recovery-uploader`) must be revoked by the owner in the Spaces Access Keys page, and its credential file on the droplet removed. Until then the key is live. (Status at the time of writing: **not yet confirmed revoked**.)
* The drill copies (about 13 GB) and the two failed-run folders remain on the droplet's disk.
