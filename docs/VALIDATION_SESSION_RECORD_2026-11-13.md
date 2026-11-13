# Validation session record — written before any provisioning (2026-11-13)

This is the record the owner's decisions require before the first provisioning step. It fixes the configuration under test and the limits. Nothing here has been run on a validation machine yet.

## 1. Configuration under test (one configuration for V0–V5)

| Item | Value |
|---|---|
| Candidate (code under test) | `205f891` (candidate 4), pinned in `tools/vtest/PIN`; the pin gate reports "HEAD differs from the pin only in docs / harness / tests" |
| Harness revision | `a454336` (`tools/vtest`, all stage code, evidence exporter). Later commits must touch only `docs/`; check with `git diff --stat a454336..HEAD` before provisioning |
| Alert package | `alerts/` as hashed in `alerts/RELEASE_MANIFEST.json` (7 files, drop-ins included; rendered hashes for both the vtest and production names). Manifest file SHA-256 `cb38fafad6305a8332de95c296ad91c5720d7d3e335c2ae62f3ac25292a9e2ce` |
| Evidence transfer | `tools/vtest/export_evidence.py` (first gate passed on the real service 2026-11-13, see `EVIDENCE_TRANSFER_PRIVATE.md`); the key used for that gate is deleted and must never be reused |
| Earlier results | V0–V4 results from earlier configurations stay valid historical evidence for those configurations. They do not validate this one |

## 2. Verification of this configuration (all on `a454336`)

* **GitHub CI, run 43425542550: passed.** 1854 unit and property tests OK, 93 skipped (the runner is not root and has no private mounts); then the specification hash, the 30-season run, the ledger soak, the full-league soak, restart recovery and the spatial smoke all passed. Two earlier runs (on `516f696` and `276fefd`) failed on two test-only defects, fixed in `a454336` (below). This is the run that covers the final code.
* **Local, non-root, two tracked shards on clean clones:** 909 + 945 = 1854 tests, 0 failures, 0 errors, 30 skipped. Records were written by the runner itself; both shards ended `EXIT 0`.
* **Local, as root, the modules whose tests need root or a mount** (backup space, supervisor as an unprivileged user, umask, service sandbox, V0): 120 run, 0 skipped, 0 failures.
* **Not run anywhere:** three tests that need a completely built first-season league (selected by environment variable) and one that needs an uncommitted change in the working folder.
* **Defects found and fixed (tests only; none in the candidate or the alert package):** seven harness tests wrote to the real `/srv/dfl-vtest` and needed root (reproduced as a normal user first); one systemd check failed on the runner's own unit files (five tests, red first).
* **Secret scan:** all 365 commits on all local refs, added lines only: no match for key-id, token, private-key, e-mail or webhook shapes. The revoked key's secret itself cannot be searched for literally. The key is deleted at the provider and must never be reused.
* **Why the first local workers died:** processes detached with nohup/setsid are stopped by the sandbox a few minutes after a reply ends. Not memory, disk or limits. Test runs are now tracked jobs with completion records.

## 3. Limits (owner's decision, 2026-11-12)

* **Nine hours** from the moment the validation machine is created; **$2 maximum**; no automatic extension.
* The final hour is reserved for verified export and cleanup. No stage starts after hour 8.
* If failures consume the margin: stop and preserve the evidence. A second diagnosed failure ends the session.
* Machine: 1 vCPU, 1 GiB, Ubuntu 24.04, about a cent an hour at list price; the price is confirmed on the create page before approval.
* Production, rehearsal, existing backups and the production recovery key are not touched.

## 4. Key entry: how the evidence-only key stays private

What was checked locally: the uploader, driven through a real terminal with test values wrapped in paste markers, showed nothing but "key id N characters, secret M characters (removed K stray terminal characters)"; the values did not appear on the terminal, in any file under its scratch, home or temp folders, or in the program's arguments. The unit tests also check that no output, error text or object representation contains the secret.

What cannot be checked until a machine exists: whether the provider's browser console accepts a paste into a hidden prompt. So:

1. **Jeph creates the key himself**, on his own screen, Read/Write/Delete on the evidence Space only, created last, just before use. Claude does not take screenshots of, or read, the key page or the console while a secret is on screen or a secret prompt is open.
2. **Dummy check first.** On the new machine, before the real key exists, Jeph enters an obviously fake value pair at the same hidden prompt. Pass: nothing is echoed, the length line appears. Fail (echo, paste refused, anything visible): **stop the key step**; no real key is created, and the session stops under the transfer prerequisite.
3. Jeph reports only the "credentials received…" line. If a secret ever shows on a screen, the key is deleted at once and a new one is made.
4. After the session the key is deleted. A key is never reused across sessions.
