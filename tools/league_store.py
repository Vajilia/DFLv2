#!/usr/bin/env python3
"""Command line for the durable league file (src/dfl/persist.py). Every command prints one JSON object; the exit status is 0 unless
the file is damaged or the command failed.

  league_store.py init   FILE [--seed N] [--snapshot-every N] [--play-level | --placeholder]
  league_store.py run    FILE (--events N | --years N) [--deep]
  league_store.py status FILE
  league_store.py verify FILE [--deep]
  league_store.py backup FILE DEST
  league_store.py restore BACKUP DEST
  league_store.py digest FILE             exhaustive digest of the recovered league
  league_store.py compat FILE             save-schema and code-identity verdicts (works under any code)
  league_store.py export FILE STATE.gz    write the league as a neutral state document (needs the exact originating code)
  league_store.py import STATE.gz DEST [--accept-code-change] [--accept-state-change] [--archive FILE]
  league_store.py upgrade OLD NEW [--run-originating-code] [--archive FILE]   continue OLD under this code as NEW; OLD is never touched
  league_store.py extract-code FILE DIR   write the source and data the file was made with
  league_store.py archive-verify | archive-stats | archive-digest ARCHIVE
  league_store.py backup-all FILE DIR     league file and archive, consistent pair
  league_store.py restore-all DIR DESTDIR

`run` also takes --die-after K and --die-inside K, which kill the process (exit status 9, no clean-up) after the Kth committed event
or in the middle of the Kth commit; they exist so the tests can prove recovery from a real process death.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl import archive, persist  # noqa: E402


def out(**kw):
    print(json.dumps(kw, sort_keys=True), flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("file")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--snapshot-every", type=int, default=1)
    p.add_argument("--keep", type=int, default=3)
    p.add_argument("--no-archive", action="store_true", help="do not keep the permanent game and trace archive")
    p.add_argument("--archive-file")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--play-level", action="store_true", help="play games with the fast play-level engine instead of the 22-player default")
    g.add_argument("--placeholder", action="store_true", help="no game engine at all (structure only; fastest)")
    p = sub.add_parser("run")
    p.add_argument("file")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--events", type=int)
    g.add_argument("--years", type=int)
    p.add_argument("--deep", action="store_true")
    p.add_argument("--die-after", type=int)
    p.add_argument("--die-inside", type=int)
    sub.add_parser("status").add_argument("file")
    p = sub.add_parser("verify")
    p.add_argument("file")
    p.add_argument("--deep", action="store_true")
    p = sub.add_parser("backup")
    p.add_argument("file")
    p.add_argument("dest")
    p = sub.add_parser("restore")
    p.add_argument("backup")
    p.add_argument("dest")
    sub.add_parser("digest").add_argument("file")
    sub.add_parser("compat").add_argument("file")
    p = sub.add_parser("export")
    p.add_argument("file")
    p.add_argument("state")
    p = sub.add_parser("import")
    p.add_argument("state")
    p.add_argument("dest")
    p.add_argument("--accept-code-change", action="store_true")
    p.add_argument("--accept-state-change", action="store_true")
    p.add_argument("--archive")
    p = sub.add_parser("upgrade")
    p.add_argument("old")
    p.add_argument("new")
    p.add_argument("--run-originating-code", action="store_true")
    p.add_argument("--accept-state-change", action="store_true")
    p.add_argument("--archive")
    p = sub.add_parser("extract-code")
    p.add_argument("file")
    p.add_argument("dir")
    for n in ("archive-verify", "archive-stats", "archive-digest"):
        sub.add_parser(n).add_argument("archive")
    p = sub.add_parser("backup-all")
    p.add_argument("file")
    p.add_argument("dir")
    p = sub.add_parser("restore-all")
    p.add_argument("dir")
    p.add_argument("dest")
    a = ap.parse_args(argv)

    try:
        if a.cmd == "init":
            kw = {"engine": False} if a.placeholder else {"spatial": not a.play_level}
            r = persist.Runner.create(a.file, os.path.join(ROOT, "data", "season1"), seed=a.seed, snapshot_every=a.snapshot_every,
                                      keep=a.keep, archive=not a.no_archive, archive_path=a.archive_file, **kw)
            out(created=a.file, events=r.seq)
        elif a.cmd == "run":
            counter = {"n": 0}

            def hook(point):
                if a.die_inside and point == "journal_written":
                    counter["n"] += 1
                    if counter["n"] == a.die_inside:
                        os._exit(9)
            r = persist.Runner.open(a.file, crash_hook=hook)
            fired = 0
            if a.events is not None:
                for _ in range(a.events):
                    r.step()
                    fired += 1
                    if a.die_after and fired == a.die_after:
                        os._exit(9)
            else:
                for _ in range(a.years):
                    fired += r.run_year()
            res = {"events": r.seq, "fired": fired, "quick": persist.quick_digest(r.u), "chain": r.chain}
            if a.deep:
                res["deep"] = persist.deep_digest(r.u)
            out(**res)
        elif a.cmd == "status":
            out(**persist.status(a.file))
        elif a.cmd == "verify":
            problems = persist.verify(a.file, deep=a.deep)
            out(ok=not problems, problems=problems)
            return 0 if not problems else 2
        elif a.cmd == "backup":
            out(backup=persist.backup(a.file, a.dest))
        elif a.cmd == "restore":
            r = persist.restore(a.backup, a.dest)
            out(restored=a.dest, events=r.seq)
        elif a.cmd == "digest":
            r = persist.Runner.open(a.file, heal=False)
            out(events=r.seq, quick=persist.quick_digest(r.u), deep=persist.deep_digest(r.u))
        elif a.cmd == "compat":
            out(**persist.compatibility(a.file))
        elif a.cmd == "export":
            out(exported=a.state, state_sha256=persist.export_state(a.file, a.state))
        elif a.cmd == "import":
            r = persist.Runner.import_state(a.state, a.dest, accept_code_change=a.accept_code_change,
                                            accept_state_change=a.accept_state_change, archive_path=a.archive)
            out(imported=a.dest, events=r.seq, chain=r.chain)
        elif a.cmd == "upgrade":
            out(**persist.upgrade(a.old, a.new, run_originating_code=a.run_originating_code,
                                  accept_state_change=a.accept_state_change, archive_path=a.archive))
        elif a.cmd == "extract-code":
            out(extracted=a.dir, fingerprint=persist.extract_code(a.file, a.dir))
        elif a.cmd == "archive-verify":
            problems = archive.verify(a.archive)
            out(ok=not problems, problems=problems)
            return 0 if not problems else 2
        elif a.cmd == "archive-stats":
            out(**archive.stats(a.archive))
        elif a.cmd == "archive-digest":
            out(digest=archive.digest(a.archive))
        elif a.cmd == "backup-all":
            out(**persist.backup_set(a.file, a.dir))
        elif a.cmd == "restore-all":
            r = persist.restore_set(a.dir, a.dest)
            out(restored=a.dest, events=r.seq)
    except (persist.PersistError, archive.ArchiveError) as e:
        out(error=type(e).__name__, message=str(e))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
