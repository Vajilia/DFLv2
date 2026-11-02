#!/usr/bin/env python3
"""Record which commit the SERVED SITE is, apart from which commit the engine and tools are.

  record_site_revision.py --base /srv/dflv2 --commit SHA40 --release 20261102T101500Z --site-dir DIR --by NAME

The engine, tools and deployment scripts are one revision ($BASE/DEPLOYED_REVISION.json, written by a full deployment). The website files can be updated
alone (deploy_site_only.sh) to a different commit, so they get their own record, $BASE/SITE_REVISION.json:
  site_commit      the commit whose site/ folder is being served
  site_release     the folder under $BASE/site/releases that holds it
  site_tree_sha256 a hash of every file of that folder (path and content), so the record can be checked without git
  engine_tools_commit  the engine/tools revision that was installed when the site was (read from DEPLOYED_REVISION.json, never written here)
A copy of the record is kept for every release in $BASE/site-revisions/<release>.json, so that `rollback.sh --site` can put the matching record back.
This script writes only those files; it never touches DEPLOYED_REVISION.json, the engine, the league or the feed."""
import argparse
import hashlib
import json
import os
import re
import sys
import time


def tree_sha256(root):
    h = hashlib.sha256()
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(d for d in dn if d != "__pycache__")
        for n in sorted(fn):
            p = os.path.join(dp, n)
            h.update(os.path.relpath(p, root).replace(os.sep, "/").encode() + b"\0")
            with open(p, "rb") as f:
                h.update(hashlib.sha256(f.read()).digest())
    return h.hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default="/srv/dflv2")
    ap.add_argument("--commit", required=True)
    ap.add_argument("--release", required=True)
    ap.add_argument("--site-dir", required=True)
    ap.add_argument("--by", required=True)
    a = ap.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{40}", a.commit):
        print("STOPPED: --commit must be the full 40-character commit id", file=sys.stderr)
        return 2
    if not re.fullmatch(r"[0-9A-Za-z_.-]+", a.release):
        print("STOPPED: odd release name", file=sys.stderr)
        return 2
    engine = None
    try:
        with open(os.path.join(a.base, "DEPLOYED_REVISION.json")) as f:
            engine = json.load(f).get("commit")
    except (OSError, ValueError):
        pass
    rec = {"site_commit": a.commit, "site_release": a.release, "site_tree_sha256": tree_sha256(a.site_dir), "engine_tools_commit": engine,
           "recorded_by": a.by, "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "note": "the commit of the website files only; the engine and tools revision is DEPLOYED_REVISION.json"}
    body = json.dumps(rec, indent=1, sort_keys=True) + "\n"
    hist = os.path.join(a.base, "site-revisions")
    os.makedirs(hist, exist_ok=True)
    for path in (os.path.join(hist, a.release + ".json"), os.path.join(a.base, "SITE_REVISION.json")):
        with open(path + ".new", "w") as f:
            f.write(body)
        os.chmod(path + ".new", 0o644)
        os.replace(path + ".new", path)
    os.chmod(hist, 0o755)
    print(f"site revision recorded: {a.commit[:12]} as release {a.release}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
