#!/usr/bin/env python3
"""Export the public website feed from a league file, and publish it atomically.

  export_public_feed.py LEAGUE_FILE --out FEED_ROOT [--league-name NAME] [--keep N] [--use-league-code | --engine-dir DIR]

What it does, in order, and what it never does:
  1. makes a consistent copy of the league file (SQLite online backup: safe while the league runner is writing) in a temporary directory;
  2. opens the COPY read-only in effect (no healing snapshot, no archive) and builds the public feed from its last committed event;
     if some finished season's Coronation score was never recorded in the league (seasons before Edition 8.58) and the league has an
     archive, it makes a second consistent copy of the ARCHIVE (after the league copy, so the archive is never behind it), checks that the pair
     belongs together, and recovers a score only from a verified, matching archived game; otherwise the score is shown as not recorded;
  3. publishes the feed under FEED_ROOT/releases/<release>/ and then replaces FEED_ROOT/latest.json atomically (src/dflsite/publish.py),
     holding the feed's publisher lock so two exporters never publish at once;
  4. deletes the copies.
The live league file is opened only by SQLite's backup reader (and the archive likewise). Nothing here advances, saves or alters the league, and a failed export leaves the
previous release live.

If the league was written by different engine code than this checkout, the league refuses to open (that protects the league). Use
`--use-league-code` to read it under the code recorded inside the file, or `--engine-dir DIR` for a tree made by `league_store.py extract-code`.
Prints one JSON object with timings, sizes and the release name.
"""
import argparse
import json
import os
import resource
import shutil
import sqlite3
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("league")
    ap.add_argument("--out", required=True, help="the feed root (served as /feed/)")
    ap.add_argument("--league-name", default="DFL Development League")
    ap.add_argument("--keep", type=int, default=6, help="releases to keep (older ones are also kept for one hour after being superseded)")
    ap.add_argument("--wait", type=float, default=0, help="seconds to wait for another exporter to finish (default 0: exit 75 at once if one is running)")
    ap.add_argument("--skip-if-current", action="store_true", help="do nothing (beyond the status heartbeat) if the league has committed no event the live release lacks")
    ap.add_argument("--runner-status", help="the runner's runner_status.json, for the public status file (default: next to the league file)")
    ap.add_argument("--no-status", action="store_true", help="do not write feed/status.json")
    ap.add_argument("--allow-older", action="store_true", help="publish even if the league has fewer events than the live release (after restoring an older league on purpose)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--use-league-code", action="store_true", help="read the league under the code recorded in the league file")
    g.add_argument("--engine-dir", help="a directory holding src/dfl and data/ (from extract-code) to read the league with")
    a = ap.parse_args(argv)
    engine_dir = a.engine_dir
    scratch = tempfile.mkdtemp(prefix="dfl-export-")
    try:
        if a.use_league_code:
            engine_dir = os.path.join(scratch, "engine")
            _extract(a.league, engine_dir)
        sys.path.insert(0, os.path.join(ROOT, "src"))           # dflsite always comes from this checkout
        if engine_dir:
            sys.path.insert(0, os.path.join(os.path.abspath(engine_dir), "src"))     # dfl (the engine) from the league's own code
        from dfl import persist
        from dflsite import feed, publish, snapshot
        from dflsite import status as ST
        t0 = time.time()
        with publish.PublisherLock(a.out, a.wait) as lock:           # one exporter at a time, from before the snapshot until after the pointer moves
            status_path = os.path.join(a.out, ST.FILE)
            previous = ST.read(status_path)
            runner = ST.read(a.runner_status or os.path.join(os.path.dirname(os.path.abspath(a.league)), ST.RUNNER_FILE))
            progress = {"league": None, "published": ST.published_events(a.out), "release": publish._live_release(a.out)}
            try:
                progress["league"] = ST.league_events(a.league)
                if a.skip_if_current and progress["published"] is not None and progress["league"] <= progress["published"]:
                    if not a.no_status:
                        ST.update(a.out, progress["league"], progress["published"], progress["release"], True, seconds=round(time.time() - t0, 2), runner=runner, previous=previous)
                    print(json.dumps({"skipped": True, "events": progress["league"], "published_events": progress["published"], "release": progress["release"]}, sort_keys=True))
                    return 0
                return _export(a, scratch, lock, t0, persist, feed, publish, snapshot, ST, previous, runner, progress)
            except BaseException as e:
                if not a.no_status:
                    try:
                        ST.update(a.out, progress["league"], progress["published"], progress["release"], False, error_type=type(e).__name__, runner=runner, previous=previous)
                    except OSError:
                        pass
                raise
    except Exception as e:                                                    # a failed export changes nothing that is live
        if type(e).__name__ == "PublishBusy":                                 # another exporter is running: not an error, the next run will do
            print(json.dumps({"busy": True, "detail": str(e)}), file=sys.stderr)
            return 75
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}), file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _export(a, scratch, lock, t0, persist, feed, publish, snapshot, ST, previous, runner, progress):
    """The export proper (formerly the body of main). Returns the exit status."""
    copy = os.path.join(scratch, "league.db")
    persist.backup(a.league, copy)
    t1 = time.time()
    r = persist.Runner.open(copy, heal=False, with_archive=False)
    t2 = time.time()
    warnings, arch, archive_used = [], None, False
    try:
        league_id = r.meta.get("league_id") or "unknown"
        fp = (r.meta.get("fingerprint") or "")[:12]
        ed, _ = feed.spec_edition()
        need = feed.seasons_needing_archive(r.u)
        if need and r.meta.get("archive"):
            arch, w = _archive_pair(persist, snapshot, a.league, copy, r.meta, scratch)
            warnings += w
        elif need:
            warnings.append(f"seasons {need} have no recorded Coronation score and this league has no archive: shown as not recorded")
        archive_used = arch is not None
        files = feed.build(r.u, league_id, a.league_name, engine=f"DFLv2 code {fp}, rules summary for spec {ed}", archive=arch)
        events = r.seq
    finally:
        r.close()
        if arch is not None:
            arch.close()
    t3 = time.time()
    res = publish.publish(files, a.out, keep=a.keep, lock=lock, allow_older=a.allow_older)
    t4 = time.time()
    total = sum(v["bytes"] for v in files["manifest.json"]["files"].values())
    seconds = round(t4 - t0, 2)
    if not a.no_status:
        ST.update(a.out, events, ST.published_events(a.out), res["release"], True, seconds=seconds, published_now=bool(res["published"]), runner=runner, previous=previous)
    print(json.dumps({"release": res["release"], "published": res["published"], "pruned": res["pruned"], "events": events, "year": files["manifest.json"]["year"],
                      "files": len(files), "feed_bytes": total, "archive_used": archive_used, "warnings": warnings,
                      "seconds": {"copy": round(t1 - t0, 2), "open": round(t2 - t1, 2), "build": round(t3 - t2, 2), "publish": round(t4 - t3, 2), "total": round(t4 - t0, 2)},
                      "peak_memory_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024}, sort_keys=True))
    return 0


def _archive_pair(persist, snapshot, league, league_copy, meta, scratch):
    """Snapshot the archive that belongs to `league` (after the league copy was made) and open it. Returns (archive or None, warnings). An archive that
    is missing, unreadable, belongs to another league, or does not match the league's journal is not used: the scores it would have supplied are
    shown as not recorded, and the reason is reported. Nothing is guessed."""
    from dfl import archive as A
    src = persist._resolve_archive(league, meta["archive"]["file"])
    copy = os.path.join(scratch, "league.db.archive")
    try:
        snapshot.snapshot_archive(src, copy)
        chk = persist.archive_check(league_copy, copy)
        bad = [k for k in ("absent", "unreadable", "foreign") if chk[k]] + [k for k in ("missing", "mismatched") if chk[k]]
        if bad or not chk["base_ok"]:
            return None, ["the archive does not match the league (" + ", ".join(bad or ["base_ok"]) + "): not used"]
        return A.Archive.open(copy), []
    except (snapshot.SnapshotError, A.ArchiveError, sqlite3.Error, OSError, ValueError) as e:
        return None, [f"the archive could not be used ({type(e).__name__}: {e})"]


def _extract(league, dest):
    """Extract the league's recorded code in a separate process, so this process never imports this checkout's engine first."""
    import subprocess
    p = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "tools", "league_store.py"), "extract-code", league, dest], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("could not extract the league's code: " + (p.stderr or p.stdout)[-300:])
    return dest


if __name__ == "__main__":
    sys.exit(main())
