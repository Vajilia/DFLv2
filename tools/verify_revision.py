#!/usr/bin/env python3
"""Check that what is installed on this machine is exactly one pinned commit of the repository.

  verify_revision.py --commit SHA40 [--base /srv/dflv2] [--repo-url URL] [--host preview.diamondfootballleague.com] [--snippet /etc/caddy/conf.d/dflv2-preview.caddy]

It fetches that commit afresh (nothing it is told about the installed files is trusted) and compares, byte for byte by git's own blob hashes:
  * every file of the installed code ($BASE/repo) with the commit's file, including the executable bit; missing, changed and unexpected files are listed;
  * the installed site ($BASE/site/current) with the commit's site/ directory;
  * the Caddy block with the commit's template rendered for the host;
  * $BASE/DEPLOYED_REVISION.json with the commit.
Prints PASS/FAIL lines; exits 0 only if everything matches. Needs only git and Python 3.9+. Changes nothing.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile


def git(*args, cwd=None, inp=None):
    return subprocess.run(["git", *args], cwd=cwd, input=inp, capture_output=True, check=True).stdout


def tree_blobs(work, commit, prefix=""):
    """{path: (mode, blob sha)} of the commit, paths relative to `prefix`."""
    out = git("ls-tree", "-r", "-z", commit, *([prefix] if prefix else []), cwd=work).decode("utf-8", "surrogateescape")
    res = {}
    for rec in out.split("\0"):
        if not rec:
            continue
        meta, path = rec.split("\t", 1)
        mode, kind, sha = meta.split()
        if kind == "blob" and mode in ("100644", "100755"):
            res[path[len(prefix):].lstrip("/") if prefix else path] = (mode, sha)
    return res


def hash_files(work, root, paths):
    if not paths:
        return {}
    full = [os.path.join(root, p) for p in paths]
    out = git("hash-object", "--no-filters", "--stdin-paths", cwd=work, inp=("\n".join(full) + "\n").encode("utf-8", "surrogateescape")).decode().split()
    return dict(zip(paths, out))


def compare(work, expected, root):
    present = {}
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d != "__pycache__"]
        for n in fn:
            if n.endswith(".pyc"):
                continue
            present[os.path.relpath(os.path.join(dp, n), root)] = True
    missing = sorted(p for p in expected if p not in present)
    extra = sorted(p for p in present if p not in expected)
    there = [p for p in expected if p in present]
    got = hash_files(work, root, there)
    changed = sorted(p for p in there if got.get(p) != expected[p][1])
    mode_bad = sorted(p for p in there if (expected[p][0] == "100755") != bool(os.stat(os.path.join(root, p)).st_mode & 0o111))
    return missing, extra, changed, mode_bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--commit", required=True)
    ap.add_argument("--base", default="/srv/dflv2")
    ap.add_argument("--repo-url", default="https://github.com/Vajilia/DFLv2.git")
    ap.add_argument("--host", default="preview.diamondfootballleague.com")
    ap.add_argument("--snippet", default="/etc/caddy/conf.d/dflv2-preview.caddy")
    ap.add_argument("--no-caddy", action="store_true")
    a = ap.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{40}", a.commit):
        print("FAIL --commit must be the full 40-character commit id", file=sys.stderr)
        return 2
    bad = []

    def line(ok, what, detail=""):
        print(("PASS " if ok else "FAIL ") + what + (f" -- {detail}" if detail else ""))
        if not ok:
            bad.append(what)

    work = tempfile.mkdtemp(prefix="dflrev")
    try:
        git("init", "-q", work)
        git("fetch", "-q", "--depth", "1", a.repo_url, a.commit, cwd=work)
        head = git("rev-parse", "FETCH_HEAD", cwd=work).decode().strip()
        line(head == a.commit, "the pinned commit was fetched", head[:12])
        if head != a.commit:
            return 1
        rec_path = os.path.join(a.base, "DEPLOYED_REVISION.json")
        try:
            rec = json.load(open(rec_path))
            line(rec.get("commit") == a.commit, "the recorded deployed revision is the pinned commit", f"{rec.get('commit', '?')[:12]} package {rec.get('package_id')}")
        except (OSError, ValueError) as e:
            line(False, "the recorded deployed revision is readable", str(e))
        repo = os.path.realpath(os.path.join(a.base, "repo"))
        exp = tree_blobs(work, a.commit)
        m, x, c, b = compare(work, exp, repo)
        line(not (m or c or b), f"the installed code is the commit's code ({len(exp)} files)", f"missing {m[:3]} changed {c[:3]} mode {b[:3]}" if (m or c or b) else repo)
        line(not x, "no unexpected files in the installed code", str(x[:5]) if x else "")
        site = os.path.realpath(os.path.join(a.base, "site", "current"))
        sexp = tree_blobs(work, a.commit, "site/")
        sm, sx, sc, sb = compare(work, sexp, site)
        line(not (sm or sc or sb) and not sx, f"the served site is the commit's site/ ({len(sexp)} files)", f"missing {sm[:3]} changed {sc[:3]} extra {sx[:3]}" if (sm or sc or sb or sx) else os.path.basename(site))
        if not a.no_caddy:
            tpl = git("show", f"{a.commit}:deploy/website/Caddyfile.template", cwd=work).decode()
            want = tpl.replace("__HOST__", a.host)
            try:
                have = open(a.snippet).read()
                line(have == want, "the Caddy block is the commit's template rendered for the host", a.snippet if have == want else "differs from the rendered template")
            except OSError as e:
                line(False, "the Caddy block is readable", str(e))
    except subprocess.CalledProcessError as e:
        line(False, "git", (e.stderr or b"").decode()[-200:])
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
    print(f"{'ALL MATCH' if not bad else str(len(bad)) + ' MISMATCH(ES)'}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
