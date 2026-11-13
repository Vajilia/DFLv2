# Getting evidence off a disposable machine without transcribing it (2026-11-12)

**Status (2026-11-13): the first gate PASSED on the real service (section below). The transfer prerequisite is met for the tool and the bucket; a new key is still needed for the session.** (Original text: the transfer is the first gate of the next session; no validation machine is created until it has passed.) The tool is built and tested locally against a fake object store (`tools/vtest/export_evidence.py`; `tests/test_vtest_export_evidence.py`, 48 tests including AWS's published signature vector, and mutation-checked); it has **never run against the real service**, so a successful private round trip is the gate, not an assumption. Nothing has been created at the provider and no credential exists yet.

**Decision of the owner, 2026-11-12:** if the round trip cannot be demonstrated, stop before provisioning. Console transcription is not the fallback.

## Why

Copying archives through the machine's web console by hand consumed whole sessions and could not be repeated (smoke-2 and smoke-3 were lost that way: only checksums were recorded). The next session has seven copy points. Transcription is not an option for them.

## The method

One **private, evidence-only bucket** the owner creates, one **key limited to that bucket**, one command on the machine, and a download through the provider's own web page.

| Step | Who | What |
|---|---|---|
| 1 (before the session) | owner | In the provider's control panel create a Space named `dfl-vtest-evidence-<date>`: private, file listing restricted, no CDN. Create an access key **limited to that one Space** (read and write; not account-wide). Keep the key in the password manager. It never goes in chat, GitHub, a log or a file in the repository. |
| 2 (at each copy point) | owner, at the machine's console | `sudo python3 tools/vtest/export_evidence.py upload --archive <file> --bucket dfl-vtest-evidence-<date> --region <region>`; the tool asks for the key id and the secret with hidden input. Several `--archive` flags use one prompt. |
| 3 | the tool | Checks the bucket name, the region and every file **before** it asks for the key (only `.tar.gz` files made by `vtest.py archive`, at most 512 MB; only buckets named `dfl-vtest-evidence-*`, so production and its backups can never be a target). Uploads each archive with one signed PUT to `evidence/<name>`, uploads `<name>.sha256` beside it, reads the object back (size and stored hash must match), and checks with an **unsigned** request that the object is **not** publicly readable (HTTP 200 there is a failure). Prints the SHA-256. |
| 4 | owner | Downloads the archive and its `.sha256` file from the Space's page and attaches both in chat. |
| 5 | Claude | Computes the SHA-256 of the attached archive, compares it with the `.sha256` file **and** with the line the tool printed on the machine (the owner reads out the first 12 characters: an out-of-band check of the transfer itself), then checks the archive against its manifest. |
| 6 (after the session) | owner | Revokes the key and removes the objects once Claude has confirmed every archive verified. Destroying the machine is the owner's act, after that. |

**What the tool never does:** write, print or keep a credential (hidden prompts only; not read from the environment or any file; held in memory for one run); set an ACL; list, delete or overwrite anything; use anything but the standard library. Claude never sees the key.

## The first gate: a harmless round trip, before any machine exists

`export_evidence.py prove --bucket dfl-vtest-evidence-<tag> --region <region>` is a single file that needs nothing else (the owner's Chromebook can run it). It asks for the key at a hidden prompt, then:

1. makes a **harmless test archive** (a note and random filler; nothing from any machine) and uploads it;
2. reads it back (size and stored hash);
3. **downloads it again with a signed request and compares the SHA-256**;
4. confirms that an **anonymous** request for the object, for its hash file and for the **bucket listing** is refused;
5. confirms the key is **limited**: a service-level bucket listing with the key must be refused (or show only this bucket). A key that sees other buckets fails the check; other bucket names are counted, never printed;
6. prints the test archive's SHA-256 and which object to download.

Then the owner **downloads that object through the provider's website on the Chromebook** and attaches it; Claude compares its SHA-256 with the printed one. Both halves are required: the tool's own download proves the key path, the website download proves the owner's path.

The gate passes only if every line says PASS **and** the owner's website download matches. Anything else: stop before provisioning; fix locally with a test first; repeat the gate. The tool never deletes, so the test objects are removed by the owner in the web page.

The production recovery key is never used or copied. The evidence bucket is separate from every production and backup bucket, and its name must start with `dfl-vtest-evidence-` (the tool refuses any other).

**This workspace cannot run the gate:** its outbound policy refuses the provider's storage hosts (HTTP 403 at the proxy; not worked around), and a hidden prompt needs a terminal the owner can type or paste into. The gate therefore runs on the owner's own computer (or another terminal the owner controls), never in a place where Claude would see the key.

## What was assumed, and what is now known

1. **Provider keys limited to one bucket: confirmed in the provider's current documentation (2026-11-12).** Access keys can be "Limited access" (per-bucket, each bucket Read, or Read/Write/Delete) or "Full access" (all buckets, including listing them). The secret is shown **once**; a key is revoked by deleting it or by removing all its bucket permissions. Limited keys cannot be used on a bucket that has a bucket policy, so none is set. The documentation does not state which S3 operations each level allows, nor the default privacy of a new Space: the gate **tests** both (the delete permission that comes with Read/Write/Delete is why the key is for the evidence bucket only and is deleted after the session).
2. That the control panel lets the owner download an object from a private Space (it is expected to; this is the owner's own authenticated page, not a public link).
3. That the machine's console accepts typing or pasting into a hidden prompt well enough for a 20 and a 40 character key. If pasting fails, ten minutes of typing per copy point is the cost; the prompt count is already minimised (one per copy point, not per file).
4. That the signature scheme the provider accepts is the standard one the tool implements (checked against AWS's own test vector, not against the provider).
5. That an unsigned request to a private object gets 403 (a different answer, such as 404, is reported as "not confirmed" and fails the run: the owner then checks the bucket's privacy by hand).

## Gate result: PASSED on the real service (2026-11-13, 01:30 PST)

Run by the owner, from his Chromebook's Linux terminal, with a Read/Write/Delete key limited to one Space (`dfl-vtest-evidence-20261113`, region sfo3, Standard storage, no CDN, file listing restricted, no bucket policy), using the tool at harness revision `fe433f2` (SHA-256 of the file `0d692765...ccda0`):

* upload, read-back, authenticated download with matching SHA-256, anonymous refusal of the object, of its hash file and of the bucket listing (all HTTP 403), and the key-scope check (a service-level bucket listing with the key: HTTP 403): **every check PASS**; the tool counted the key as 20 and 43 characters and removed 0 stray characters;
* the owner downloaded the test archive (`transfer-proof-6529dbdd.tar.gz`, 197031 bytes, a note and filler) through the provider's website on his Chromebook and attached it in chat; its SHA-256 `d21f4144c834d98f7a6c3de2d81c5b3f4cd2156ec5ad729c4c2fdc53c5e28452` equals the hash the tool printed and the stored `.sha256` file.

What the gate taught (all fixed or recorded):
* A key created in the control panel defaults to **Read** only; a Read key cannot upload. The key for the session must be Read/Write/Delete on the evidence Space alone.
* The secret is shown **once**; create the key last, immediately before use.
* Pasting into a terminal can add hidden marker codes; the tool now removes them and reports lengths only (the lengths exposed two earlier mis-pastes: a 7-character "key id" and a 20-character "secret").
* On a current Chromebook (containerless Linux), a folder shared with Linux appears at `/mnt/shared/MyFiles/Downloads`, not `/mnt/chromeos/...`.
* The owner chose to show the key in chat and screenshots. **Any key shown that way is treated as exposed and is deleted.** The session uses a **new** key.

Still true: the key has not been entered at the validation machine's console (no machine exists); the production recovery key was never used; the test objects stay in the bucket until the owner removes them (the tool never deletes).

## What the gate does not prove

* That the key can be entered at the **validation machine's** console prompt (pasting may not work there). The setup step exports the setup record first for exactly that reason; if entry fails, stop and preserve evidence on the machine; do not transcribe archives.
* That the machine's network can reach the provider (the standard rules allow outbound HTTPS from the validation machine; V0 records it).
