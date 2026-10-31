#!/usr/bin/env python3
"""Build the staging deployment package from COMMITTED code.

  build_staging_package.py --seed-feed FEED_ROOT --out DIR [--allow-dirty]

Writes DIR/dfl-staging-package-<id>.tar.gz (and a .sha256 beside it). Inside, one folder holds:
  repo/          the committed repository at HEAD (git archive: exactly the tracked files, nothing from a working directory)
  seed-feed/     a verified feed (releases/<release>/ and latest.json) so the staging site shows a league the moment it is installed
  PACKAGE.json   id, commit, branch, spec edition and hash, seed release, build time
  RUNBOOK.md     docs/STAGING_RUNBOOK.md as committed
  SHA256SUMS     a checksum of every file above (install_staging.sh refuses a package that does not match)
Refuses a working tree with uncommitted changes (the package must be what the commit says) unless --allow-dirty (marked in PACKAGE.json).
The package contains no league file, no secrets and no credentials.
"""
import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))


def git(*args):
    return subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True, check=True).stdout


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seed-feed", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--from-worktree", action="store_true", help="(tests) take the files of the working tree, tracked or not, instead of HEAD; implies --allow-dirty")
    a = ap.parse_args(argv)
    from dflsite import feed, publish
    if a.from_worktree:
        a.allow_dirty = True
    dirty = bool(git("status", "--porcelain").strip())
    if dirty and not a.allow_dirty:
        print(json.dumps({"error": "the working tree has uncommitted changes; commit first (or --allow-dirty, which the package will say)"}), file=sys.stderr)
        return 1
    seed = publish.read_release(a.seed_feed, verify=True)               # a damaged seed feed never gets into a package
    rel = seed["manifest.json"]["release"]
    commit = git("rev-parse", "HEAD").strip()
    branch = git("rev-parse", "--abbrev-ref", "HEAD").strip()
    ed, h = feed.spec_edition()
    built = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    pid = f"{built}-{commit[:10]}" + ("-dirty" if dirty else "")
    work = tempfile.mkdtemp(prefix="dflpkg")
    top = os.path.join(work, f"dfl-staging-{pid}")
    os.makedirs(top)
    if a.from_worktree:
        import shutil as _sh
        for rp in git("ls-files", "--cached", "--others", "--exclude-standard").splitlines():
            if os.path.exists(os.path.join(ROOT, rp)):
                os.makedirs(os.path.dirname(os.path.join(top, "repo", rp)), exist_ok=True)
                _sh.copy2(os.path.join(ROOT, rp), os.path.join(top, "repo", rp))
    else:
        arch = subprocess.run(["git", "-C", ROOT, "archive", "--format=tar", "--prefix=repo/", "HEAD"], capture_output=True, check=True).stdout
        with tarfile.open(fileobj=io.BytesIO(arch)) as t:
            t.extractall(top, filter="data")
    os.makedirs(os.path.join(top, "seed-feed", "releases"))
    import shutil
    shutil.copytree(os.path.join(a.seed_feed, "releases", rel), os.path.join(top, "seed-feed", "releases", rel))
    shutil.copy(os.path.join(a.seed_feed, "latest.json"), os.path.join(top, "seed-feed", "latest.json"))
    shutil.copy(os.path.join(ROOT, "docs", "STAGING_RUNBOOK.md"), os.path.join(top, "RUNBOOK.md"))
    meta = {"id": pid, "commit": commit, "branch": branch, "dirty": dirty, "built_at": built, "spec_edition": ed, "spec_hash": h,
            "seed_release": rel, "seed_simulation_date": seed["manifest.json"].get("simulation_date"),
            "what": "DFLv2 staging site: static viewer + seed feed. No league file, no secrets, no credentials. Not a production deployment."}
    with open(os.path.join(top, "PACKAGE.json"), "w") as f:
        json.dump(meta, f, indent=1, sort_keys=True)
    sums = []
    for dirpath, dirs, names in os.walk(top):
        dirs.sort()
        for n in sorted(names):
            p = os.path.join(dirpath, n)
            rp = os.path.relpath(p, top)
            if rp == "SHA256SUMS":
                continue
            with open(p, "rb") as f:
                sums.append(f"{hashlib.sha256(f.read()).hexdigest()}  {rp}")
    with open(os.path.join(top, "SHA256SUMS"), "w") as f:
        f.write("\n".join(sums) + "\n")
    os.makedirs(a.out, exist_ok=True)
    out = os.path.join(a.out, f"dfl-staging-package-{pid}.tar.gz")
    with tarfile.open(out, "w:gz") as t:
        t.add(top, arcname=f"dfl-staging-{pid}")
    with open(out, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    with open(out + ".sha256", "w") as f:
        f.write(f"{digest}  {os.path.basename(out)}\n")
    shutil.rmtree(work, ignore_errors=True)
    print(json.dumps({"package": out, "sha256": digest, "id": pid, "commit": commit, "files": len(sums), "bytes": os.path.getsize(out), "seed_release": rel}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
