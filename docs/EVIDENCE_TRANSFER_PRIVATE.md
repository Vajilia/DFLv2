# Getting evidence off a disposable machine without transcribing it (2026-11-12)

**Status: built and tested locally against a fake object store; never run against a real one.** Nothing was created at any provider, no credential exists, nothing was sent. The tool is `tools/vtest/export_evidence.py`; its tests are `tests/test_vtest_export_evidence.py` (26, including AWS's published signature vector).

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

## What is assumed and not yet checked

1. **That the provider's keys can be limited to one bucket.** If they cannot, do not use this tool with an account-wide key: the machine is disposable but the account is not. Fallback: send the small `SUMMARY`/`result.json` files through the console and keep the archives on the machine until a safe method exists. That would be a worse outcome and must be decided before the session, not during it.
2. That the control panel lets the owner download an object from a private Space (it is expected to; this is the owner's own authenticated page, not a public link).
3. That the machine's console accepts typing or pasting into a hidden prompt well enough for a 20 and a 40 character key. If pasting fails, ten minutes of typing per copy point is the cost; the prompt count is already minimised (one per copy point, not per file).
4. That the signature scheme the provider accepts is the standard one the tool implements (checked against AWS's own test vector, not against the provider).
5. That an unsigned request to a private object gets 403 (a different answer, such as 404, is reported as "not confirmed" and fails the run: the owner then checks the bucket's privacy by hand).

## A first, harmless real test

Before the session, on any machine, with a small throwaway file in the export folder and a throwaway bucket: run the tool once. If step 3 fails for a reason the fake store could not show, it fails there and not at the first real copy point. This needs the owner's bucket and key; it is not part of this batch.
