#!/usr/bin/env python3
"""Read-only retention evidence: which private caches of a finished season can be re-made byte for byte from what must stay (docs/STORAGE_RETENTION.md).

  storage_reconstruct_check.py --root R --scratch D [--sample 24] [--public P] [--report FILE]

R is a private root whose season is completely built (`store/`, `league/league.db.archive`). Nothing under R or P is written, moved or deleted: every re-made file goes under D,
which must be new. It re-runs the exporter (tools/export_playback.py, which opens only the archive and the feed) into D, then:
  * compares every re-exported replay file with store/playback (byte for byte). A difference is a FINDING, not a failure of the tool: the exporter takes rosters from the
    feed release it is given, so a replay re-made from a later feed is not the file that was published (and its content-addressed name changes)
  * re-builds the live recording of a spread sample of games from the PRIVATE replay file (the daemon's own store_game) and compares every file with store/games/<gid>/
  * with --public: compares each private feed release that is also public (releases/<name>) byte for byte, and lists the private releases that are NOT public (these can only
    be re-made by replaying the whole league deterministically)
Prints the counts and bytes of what is reconstructible and what is not. Exit 0 when everything that was compared is equal.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dflsite import broadcast as B  # noqa: E402


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def files(root):
    out = {}
    for d, _, names in os.walk(root):
        for n in names:
            p = os.path.join(d, n)
            out[os.path.relpath(p, root)] = p
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", required=True)
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--sample", type=int, default=24)
    ap.add_argument("--public")
    ap.add_argument("--report")
    a = ap.parse_args(argv)
    root = os.path.abspath(a.root)
    store = os.path.join(root, "store")
    league = os.path.join(root, "league", "league.db")
    if os.path.exists(a.scratch) and os.listdir(a.scratch):
        raise SystemExit("%s must be new" % a.scratch)
    os.makedirs(a.scratch, exist_ok=True)
    scratch = os.path.abspath(a.scratch)
    if scratch.startswith(root + os.sep) or (a.public and scratch.startswith(os.path.abspath(a.public) + os.sep)):
        raise SystemExit("the scratch folder must be outside the root and the public folder")
    problems, out = [], {}
    t0 = time.time()
    pb = os.path.join(scratch, "playback")
    p = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "export_playback.py"), "--archive", league + ".archive", "--feed", os.path.join(store, "feed"), "--out", pb],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit("export failed: " + (p.stderr or p.stdout)[-400:])
    out["export_s"] = round(time.time() - t0, 1)
    # 1. the replay files and the index
    orig = files(os.path.join(store, "playback"))
    new = files(pb)
    only_orig = sorted(set(orig) - set(new))
    only_new = sorted(set(new) - set(orig))
    same = diff = 0
    nbytes = 0
    for rel, path in orig.items():
        if rel in new:
            if sha(path) == sha(new[rel]):
                same += 1
                nbytes += os.path.getsize(path)
            else:
                diff += 1
    # a re-exported replay has another content hash in its name, so "different" files show up as one name only in each tree
    out["playback"] = {"files_private": len(orig), "files_reexported": len(new), "names_equal_and_bytes_equal": same, "names_equal_bytes_different": diff,
                       "names_only_in_private": len(only_orig), "names_only_in_reexport": len(only_new), "bytes_reproduced": nbytes,
                       "bytes_not_reproduced": sum(os.path.getsize(orig[r]) for r in only_orig)}
    out["playback_not_reproduced_examples"] = only_orig[:3]
    # 2. live recordings for a spread sample
    builds = B.get(os.path.join(store, "builds.json"))
    gids = sorted(builds["games"])
    step = max(1, len(gids) // max(1, a.sample))
    pick = gids[::step][:a.sample]
    if gids and gids[-1] not in pick:
        pick.append(gids[-1])
    tmp_store = os.path.join(scratch, "store")
    n_ok = n_bad = nb = 0
    for gid in pick:
        e = builds["games"][gid]["entry"]
        raw = open(os.path.join(store, "playback", e["file"]), "rb").read()            # the published replay itself (not the re-export)
        game = json.loads(raw)
        slot = int(json.load(open(os.path.join(store, "plan.json")))["params"]["slot_s"]) if os.path.exists(os.path.join(store, "plan.json")) else 960
        rec = B.store_game(tmp_store, game, slot, replay_name=os.path.basename(e["file"]))
        with open(os.path.join(tmp_store, "games", B.safe(gid), "replay.json"), "wb") as f:
            f.write(raw)
        a_dir, b_dir = os.path.join(store, "games", B.safe(gid)), os.path.join(tmp_store, "games", B.safe(gid))
        fa, fb = files(a_dir), files(b_dir)
        if sorted(fa) != sorted(fb):
            n_bad += 1
            problems.append("live recording of %s: file sets differ (%d vs %d)" % (gid, len(fa), len(fb)))
            continue
        bad = [rel for rel in fa if sha(fa[rel]) != sha(fb[rel])]
        if bad:
            n_bad += 1
            problems.append("live recording of %s: %s differ" % (gid, bad[:3]))
        else:
            n_ok += 1
            nb += sum(os.path.getsize(x) for x in fa.values())
    out["live_recordings_sampled"] = {"games_sampled": len(pick), "games_equal": n_ok, "games_different": n_bad, "bytes_equal": nb, "games_total": len(gids)}
    # 3. the private feed releases
    rel_dir = os.path.join(store, "feed", "releases")
    mine = sorted(os.listdir(rel_dir)) if os.path.isdir(rel_dir) else []
    pub = {}
    if a.public and os.path.isdir(os.path.join(a.public, "releases")):
        pub = {n: 1 for n in os.listdir(os.path.join(a.public, "releases"))}
    eq = ne = 0
    eq_bytes = ne_bytes = 0
    not_public = []
    for name in mine:
        d = os.path.join(rel_dir, name)
        size = sum(os.path.getsize(x) for x in files(d).values())
        if name in pub:
            fa, fb = files(d), files(os.path.join(a.public, "releases", name))
            if sorted(fa) == sorted(fb) and all(sha(fa[k]) == sha(fb[k]) for k in fa):
                eq += 1
                eq_bytes += size
            else:
                ne += 1
                problems.append("private release %s differs from its public copy" % name)
        else:
            not_public.append(name)
            ne_bytes += size
    out["private_feed_releases"] = {"total": len(mine), "equal_to_public": eq, "bytes_equal_to_public": eq_bytes, "different_from_public": ne,
                                    "not_public": len(not_public), "bytes_not_public": ne_bytes, "public_checked": bool(a.public), "not_public_examples": not_public[:3]}
    res = {"ok": not problems, "problems": problems[:20], "result": out, "wall_s": round(time.time() - t0, 1)}
    if a.report:
        with open(a.report, "w") as f:
            json.dump(res, f, indent=1, sort_keys=True)
    print(json.dumps(res, sort_keys=True))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
