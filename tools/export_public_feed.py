#!/usr/bin/env python3
"""Export the public website feed from a league file, and publish it atomically.

  export_public_feed.py LEAGUE_FILE --out FEED_ROOT [--league-name NAME] [--keep N] [--use-league-code | --engine-dir DIR]

What it does, in order, and what it never does:
  1. makes a consistent copy of the league file (SQLite online backup: safe while the league runner is writing) in a temporary directory;
  2. opens the COPY read-only in effect (no healing snapshot, no archive) and builds the public feed from its last committed event;
  3. publishes the feed under FEED_ROOT/releases/<release>/ and then replaces FEED_ROOT/latest.json atomically (src/dflsite/publish.py);
  4. deletes the copy.
The live league file is opened only by SQLite's backup reader. Nothing here advances, saves or alters the league, and a failed export leaves the
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
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("league")
    ap.add_argument("--out", required=True, help="the feed root (served as /feed/)")
    ap.add_argument("--league-name", default="DFL Development League")
    ap.add_argument("--keep", type=int, default=6, help="releases to keep")
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
        from dflsite import feed, publish
        t0 = time.time()
        copy = os.path.join(scratch, "league.db")
        persist.backup(a.league, copy)
        t1 = time.time()
        r = persist.Runner.open(copy, heal=False, with_archive=False)
        t2 = time.time()
        try:
            league_id = r.meta.get("league_id") or "unknown"
            fp = (r.meta.get("fingerprint") or "")[:12]
            ed, _ = feed.spec_edition()
            files = feed.build(r.u, league_id, a.league_name, engine=f"DFLv2 code {fp}, rules summary for spec {ed}")
            events = r.seq
        finally:
            r.close()
        t3 = time.time()
        res = publish.publish(files, a.out, keep=a.keep)
        t4 = time.time()
        total = sum(v["bytes"] for v in files["manifest.json"]["files"].values())
        print(json.dumps({"release": res["release"], "published": res["published"], "pruned": res["pruned"], "events": events, "year": files["manifest.json"]["year"],
                          "files": len(files), "feed_bytes": total, "seconds": {"copy": round(t1 - t0, 2), "open": round(t2 - t1, 2), "build": round(t3 - t2, 2),
                                                                                "publish": round(t4 - t3, 2), "total": round(t4 - t0, 2)},
                          "peak_memory_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024}, sort_keys=True))
        return 0
    except Exception as e:                                                    # a failed export changes nothing that is live
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}), file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _extract(league, dest):
    """Extract the league's recorded code in a separate process, so this process never imports this checkout's engine first."""
    import subprocess
    p = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "tools", "league_store.py"), "extract-code", league, dest], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("could not extract the league's code: " + (p.stderr or p.stdout)[-300:])
    return dest


if __name__ == "__main__":
    sys.exit(main())
