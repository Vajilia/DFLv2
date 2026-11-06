#!/usr/bin/env python3
"""Consistent backup, verification and restore of the broadcast's PUBLIC folder (and, with --profile store, of the private broadcast store). docs/PUBLIC_BACKUP.md.

  public_backup.py create  --src DIR --out BACKUP [--profile public|store] [--max-passes 8] [--require-quiescent] [--no-fsync] [--discard-partial]
  public_backup.py verify  BACKUP [--no-closure]             every file against the manifest (size, SHA-256), no extras, then the reference closure
  public_backup.py restore BACKUP DEST                        verify, then restore into a NEW (absent or empty) directory, then verify DEST; explicit modes 0755/0644
  public_backup.py check   --src DIR [--profile ...]          read-only reference-closure check of a live folder (no backup made)

Exit 0 only when every check passed. Read-only on --src; the daemon and the builder are not stopped, paused or signalled.

Why it is consistent without stopping the daemon. Published replay, release and live-segment files are immutable and the daemon replaces every other file with an atomic rename,
so any single read returns one whole version. What can go wrong is a MIX: an index copied after a file it names was not yet copied, or the other way round. So:
  1. files are copied in passes, the cumulative control files first (latest.json, the three indexes: they only grow) then every other control file, then the rest; a file is
     copied again in a later pass only if its (inode, size, mtime) changed; a pass that finds the cumulative controls unchanged from its start to its end is QUIET and ends the
     copy (a snapshot of one publication state). If the daemon keeps publishing, the passes continue (each only copies what changed) up to --max-passes;
  2. the finished copy must satisfy the REFERENCE CLOSURE: latest -> its release and every file that release's manifest lists (size and SHA-256); every playback-index entry ->
     its file with the entry's SHA-256 (and the name's 10-hex tag); every replay-library entry -> its file; every Honors-index part -> its file; every live manifest -> head,
     all its segments, and the end file when final. A copy that does not satisfy it is never committed;
  3. if the passes ended without a quiet pass, the backup is still committed only if the closure holds and then says `"quiescent": false` (a closure-valid snapshot assembled from
     more than one publication state: every index entry has its file and no file is newer than anything that depends on it); --require-quiescent refuses such a backup.
Commit protocol: everything is written under BACKUP.partial; the manifest (with its own digest) is written last, then BACKUP.partial is renamed to BACKUP. A killed or failed
backup leaves only BACKUP.partial, which `verify` and `restore` refuse ("incomplete"); a backup directory without a valid manifest is never reported valid.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import time

FORMAT, VERSION = "dfl-public-backup", 1
MANIFEST = "BACKUP_MANIFEST.json"
FILES = "files"
CHUNK = 1 << 20

# the cumulative control files (they only grow), in the order they are copied first, per profile
CUMULATIVE = {
    "public": ["latest.json", "playback/index.json", "broadcast/replays/index.json", "broadcast/honors/index.json"],
    "store": ["builds.json", "carry.json", "feed/latest.json"],
}
# the other control files (small, replaced by atomic rename): copied before the bulk
CONTROL = {
    "public": ["status.json", "broadcast/clock.json", "broadcast/schedule.json"],
    "store": ["plan.json", "clock.json"],
}
LIVE_MANIFEST = re.compile(r"^broadcast/live/[^/]+/manifest\.json$")
SEASON_BUILDS = re.compile(r"^seasons/s\d{3}/(plan|builds)\.json$")
TMP = re.compile(r"(^|/)\.tmp-[^/]*$|\.tmp$")


class Failed(Exception):
    pass



def _slurp(path):
    with open(path, "rb") as f:
        return f.read()


def _jload(path):
    return json.loads(_slurp(path))

def sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def fsync_dir(path):
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def listing(src):
    """-> {rel: (st_ino, st_size, st_mtime_ns)} of the regular files (temporaries excluded); a symlink anywhere is an error: nothing here may be followed."""
    out, links = {}, []
    for d, dirs, names in os.walk(src, followlinks=False):
        dirs[:] = sorted(x for x in dirs if not x.startswith(".tmp-"))
        for n in sorted(names):
            p = os.path.join(d, n)
            rel = os.path.relpath(p, src).replace(os.sep, "/")
            if TMP.search(rel):
                continue
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode):
                links.append(rel)
            elif stat.S_ISREG(st.st_mode):
                out[rel] = (st.st_ino, st.st_size, st.st_mtime_ns)
        for dn in list(dirs):
            if os.path.islink(os.path.join(d, dn)):
                links.append(os.path.relpath(os.path.join(d, dn), src).replace(os.sep, "/"))
                dirs.remove(dn)
    if links:
        raise Failed("symbolic links are not followed or copied: %s" % links[:3])
    return out


def order(profile, rels):
    cum = [c for c in CUMULATIVE[profile] if c in rels]
    ctl = [c for c in CONTROL[profile] if c in rels]
    extra = sorted(r for r in rels if (profile == "public" and LIVE_MANIFEST.match(r)) or (profile == "store" and SEASON_BUILDS.match(r)))
    first = set(cum) | set(ctl) | set(extra)
    # season builds files before the bulk too (they decide what the bulk must contain)
    return cum + ctl + extra + sorted(r for r in rels if r not in first)


def cum_state(src, profile):
    out = {}
    for rel in CUMULATIVE[profile]:
        p = os.path.join(src, rel)
        try:
            out[rel] = sha_file(p)
        except FileNotFoundError:
            out[rel] = None
    if profile == "store":                         # the per-season builds files are cumulative controls of the store too
        sd = os.path.join(src, "seasons")
        if os.path.isdir(sd):
            for n in sorted(os.listdir(sd)):
                for f in ("builds.json", "plan.json"):
                    p = os.path.join(sd, n, f)
                    if os.path.exists(p):
                        out["seasons/%s/%s" % (n, f)] = sha_file(p)
    return out


def class_of(profile, rel):
    if rel in CUMULATIVE[profile]:
        return "cumulative"
    if rel in CONTROL[profile] or (profile == "public" and LIVE_MANIFEST.match(rel)) or (profile == "store" and SEASON_BUILDS.match(rel)):
        return "control"
    return "content"


# ------------------------------------------------------------------------------------------------------------------------ the reference closure
class Tree:
    """Read access to a tree whose file sizes and SHA-256 are either known (the manifest) or computed on demand."""

    def __init__(self, base, known=None):
        self.base, self.known = base, known if known is not None else {}

    def exists(self, rel):
        return (rel in self.known) if self.known else os.path.isfile(os.path.join(self.base, rel))

    def size(self, rel):
        return self.known[rel][0] if rel in self.known else os.path.getsize(os.path.join(self.base, rel))

    def sha(self, rel):
        if rel in self.known:
            return self.known[rel][1]
        return sha_file(os.path.join(self.base, rel))

    def json(self, rel):
        with open(os.path.join(self.base, rel), "rb") as f:
            return json.loads(f.read())

    def rels(self, prefix):
        if self.known:
            return sorted(r for r in self.known if r.startswith(prefix))
        out = []
        for d, _, names in os.walk(os.path.join(self.base, prefix)):
            for n in names:
                out.append(os.path.relpath(os.path.join(d, n), self.base).replace(os.sep, "/"))
        return sorted(out)


def _release_problems(t, rel_dir, out, pointer_sha=None):
    mp = rel_dir + "/manifest.json"
    if not t.exists(mp):
        out.append("release %s has no manifest" % rel_dir)
        return
    if pointer_sha and t.sha(mp) != pointer_sha:
        out.append("release %s: manifest SHA-256 differs from the pointer's" % rel_dir)
    try:
        m = t.json(mp)
    except (ValueError, OSError) as e:
        out.append("release %s: manifest unreadable (%s)" % (rel_dir, type(e).__name__))
        return
    files = m.get("files")
    if isinstance(files, dict):
        for name, rec in files.items():
            p = rel_dir + "/" + rec.get("path", name)
            if not t.exists(p):
                out.append("release %s: %s is missing" % (rel_dir, rec.get("path", name)))
            elif t.size(p) != rec.get("bytes", t.size(p)) or t.sha(p) != rec.get("sha256", t.sha(p)):
                out.append("release %s: %s differs from its manifest" % (rel_dir, rec.get("path", name)))


def closure_public(t):
    out = []
    # latest -> release
    if t.exists("latest.json"):
        try:
            latest = t.json("latest.json")
            rel = latest["release"]
            _release_problems(t, "releases/" + rel, out, latest.get("manifest_sha256"))
        except (ValueError, KeyError, OSError) as e:
            out.append("latest.json unusable (%s)" % type(e).__name__)
    # every release is complete against its own manifest
    seen = {r.split("/")[1] for r in t.rels("releases/") if r.count("/") >= 2}
    for name in sorted(seen):
        _release_problems(t, "releases/" + name, out)
    # playback index
    idx = {}
    if t.exists("playback/index.json"):
        try:
            for e in t.json("playback/index.json")["games"]:
                p = "playback/" + e["file"]
                idx[e["file"].split("/")[-1]] = e
                if not t.exists(p):
                    out.append("playback index: %s is missing" % e["file"])
                    continue
                if t.sha(p) != e["sha256"] or t.size(p) != e.get("bytes", t.size(p)):
                    out.append("playback index: %s differs from its entry" % e["file"])
                if e["file"].split(".")[-2] != e["sha256"][:10]:
                    out.append("playback index: the name of %s does not carry its SHA-256" % e["file"])
        except (ValueError, KeyError, OSError) as e:
            out.append("playback/index.json unusable (%s)" % type(e).__name__)
    # replay library
    if t.exists("broadcast/replays/index.json"):
        try:
            gids = set()
            for g in t.json("broadcast/replays/index.json")["games"]:
                p = "broadcast/replays/" + g["replay"]
                if g["gid"] in gids:
                    out.append("replay library: %s listed twice" % g["gid"])
                gids.add(g["gid"])
                if not t.exists(p):
                    out.append("replay library: %s is missing" % g["replay"])
                elif g["replay"] in idx and t.sha(p) != idx[g["replay"]]["sha256"]:
                    out.append("replay library: %s differs from the published replay" % g["replay"])
                elif g["replay"] not in idx and t.exists("playback/games/" + g["replay"]) and t.sha(p) != t.sha("playback/games/" + g["replay"]):
                    out.append("replay library: %s differs from playback/games" % g["replay"])
        except (ValueError, KeyError, OSError) as e:
            out.append("broadcast/replays/index.json unusable (%s)" % type(e).__name__)
    # honors
    if t.exists("broadcast/honors/index.json"):
        try:
            for p in t.json("broadcast/honors/index.json")["parts"]:
                if not t.exists("broadcast/honors/" + p["file"]):
                    out.append("honors index: %s is missing" % p["file"])
        except (ValueError, KeyError, OSError) as e:
            out.append("broadcast/honors/index.json unusable (%s)" % type(e).__name__)
    # live recordings
    for rel in [r for r in t.rels("broadcast/live/") if r.endswith("/manifest.json") and r.count("/") == 3]:
        d = rel.rsplit("/", 1)[0]
        try:
            m = t.json(rel)
            if not t.exists(d + "/head.json"):
                out.append("%s: head.json is missing" % d)
            for i in range(int(m["segments"])):
                if not t.exists("%s/s%04d.json" % (d, i)):
                    out.append("%s: segment %d is missing" % (d, i))
                    break
            if m.get("state") == "final" and not t.exists(d + "/end.json"):
                out.append("%s: final but end.json is missing" % d)
        except (ValueError, KeyError, OSError) as e:
            out.append("%s unusable (%s)" % (rel, type(e).__name__))
    for rel in ("status.json", "broadcast/clock.json", "broadcast/schedule.json"):
        if t.exists(rel):
            try:
                t.json(rel)
            except (ValueError, OSError):
                out.append("%s is not valid JSON" % rel)
    return out


def closure_store(t):
    out = []
    builds_files = ["builds.json"] + [r for r in t.rels("seasons/") if r.endswith("/builds.json")]
    for bf in builds_files:
        if not t.exists(bf):
            continue
        try:
            b = t.json(bf)
            for k, ev in b.get("events", {}).items():
                _release_problems(t, "feed/releases/" + ev["release"], out)
                if not t.exists(ev["pointer"]):
                    out.append("%s: pointer %s is missing" % (bf, ev["pointer"]))
            for gid, g in b.get("games", {}).items():
                d = "games/" + gid.replace("/", "-").replace(":", "-")
                for need in ("head.json", "end.json", "replay.json"):
                    if not t.exists(d + "/" + need):
                        out.append("%s: %s/%s is missing" % (bf, d, need))
                for i in range(int(g["segments"])):
                    if not t.exists("%s/s%04d.json" % (d, i)):
                        out.append("%s: %s segment %d is missing" % (bf, d, i))
                        break
                e = g["entry"]
                p = "playback/" + e["file"]
                if not t.exists(p) or t.sha(p) != e["sha256"]:
                    out.append("%s: private replay %s missing or different" % (bf, e["file"]))
            for part, rel in b.get("honors", {}).items():
                if not t.exists("honors/" + rel):
                    out.append("%s: honors %s is missing" % (bf, rel))
        except (ValueError, KeyError, OSError) as e:
            out.append("%s unusable (%s)" % (bf, type(e).__name__))
    if t.exists("carry.json"):
        try:
            c = t.json("carry.json")
            for f in ("plan.json", "builds.json"):
                if not t.exists("seasons/s%03d/%s" % (int(c["season"]), f)):
                    out.append("carry.json names season %s but seasons/s%03d/%s is missing" % (c["season"], int(c["season"]), f))
        except (ValueError, KeyError, OSError) as e:
            out.append("carry.json unusable (%s)" % type(e).__name__)
    if not t.exists("plan.json"):
        out.append("plan.json is missing")
    return out


CLOSURE = {"public": closure_public, "store": closure_store}


# ------------------------------------------------------------------------------------------------------------------------ create
def copy_file(src, dst, fsync):
    """Copy and hash in one read. Returns (sha256, size) of the bytes actually read."""
    h = hashlib.sha256()
    n = 0
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(src, "rb") as f, open(dst, "wb") as g:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
            n += len(chunk)
            g.write(chunk)
        g.flush()
        if fsync:
            os.fsync(g.fileno())
    return h.hexdigest(), n


def create(src, out, profile="public", max_passes=8, require_quiescent=False, fsync=True, discard_partial=False, hook=None, now=time.time):
    """Make the backup. `hook(event, **info)` is for tests and fault injection (events: 'file', 'pass_end', 'before_commit')."""
    src, out = os.path.abspath(src), os.path.abspath(out)
    if profile not in CLOSURE:
        raise Failed("unknown profile %r" % profile)
    if not os.path.isdir(src):
        raise Failed("%s is not a directory" % src)
    if os.path.exists(out):
        raise Failed("%s already exists: a backup is never overwritten" % out)
    part = out + ".partial"
    if os.path.exists(part):
        if not discard_partial:
            raise Failed("%s exists (an earlier backup did not finish); inspect it, or pass --discard-partial to remove that one folder" % part)
        shutil.rmtree(part)
    if os.path.commonpath([src, out]) in (src, out):
        raise Failed("the backup must not be inside the source (or the other way round)")
    os.makedirs(os.path.join(part, FILES))
    hook = hook or (lambda *a, **k: None)
    t0 = now()
    copied = {}               # rel -> {"sig": (ino,size,mtime), "sha": ..., "size": ...}
    passes = []
    quiet = False
    try:
        for n in range(1, max_passes + 1):
            before = cum_state(src, profile)
            lst = listing(src)
            ncopy = 0
            for rel in order(profile, lst):
                c = copied.get(rel)
                if c and c["sig"] == lst[rel]:
                    continue
                hook("file", rel=rel, n=len(copied), pass_no=n)
                try:
                    sha, size = copy_file(os.path.join(src, rel), os.path.join(part, FILES, rel), fsync)
                except FileNotFoundError:
                    copied.pop(rel, None)               # removed by the daemon while we were here: the next pass sees the new listing
                    continue
                copied[rel] = {"sig": lst[rel], "sha": sha, "size": size}
                ncopy += 1
            after = cum_state(src, profile)
            still = listing(src)
            for r in [r for r in copied if r not in still]:    # deleted at the source (a reveal group with nothing due): the backup mirrors the final state
                copied.pop(r)
                try:
                    os.unlink(os.path.join(part, FILES, r))
                except FileNotFoundError:
                    pass
            # QUIET pass: the cumulative controls were the same at its start and end, and what we hold of them IS that state
            quiet = before == after and all(((r not in copied) and v is None) or (r in copied and copied[r]["sha"] == v) for r, v in after.items())
            passes.append({"pass": n, "copied": ncopy, "quiet": quiet})
            hook("pass_end", pass_no=n, copied=ncopy)
            if quiet:
                break
        if require_quiescent and not quiet:
            raise Failed("the folder kept publishing for %d passes: no quiet moment; nothing was committed" % max_passes)
        known = {r: (c["size"], c["sha"]) for r, c in copied.items()}
        problems = CLOSURE[profile](Tree(os.path.join(part, FILES), known))
        if problems:
            raise Failed("the copy does not satisfy the reference closure (%d problems, first: %s); nothing was committed" % (len(problems), problems[0]))
        files = [{"path": r, "size": c["size"], "sha256": c["sha"], "class": class_of(profile, r)} for r, c in sorted(copied.items())]
        snap = {"quiescent": quiet, "passes": passes, "cumulative": after, "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0)),
                "finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now())), "seconds": round(now() - t0, 1)}
        for rel, key in (("latest.json", "latest"), ("status.json", "status"), ("broadcast/clock.json", "clock"), ("builds.json", "builds_summary")):
            if rel in copied:
                try:
                    doc = json.loads(_slurp(os.path.join(part, FILES, rel)))
                    if rel == "builds.json":
                        doc = {"events_built": len(doc.get("events", {})), "games_built": len(doc.get("games", {}))}
                    snap[key] = doc
                except (ValueError, OSError):
                    pass
        body = {"format": FORMAT, "format_version": VERSION, "profile": profile, "snapshot": snap, "totals": {"files": len(files), "bytes": sum(f["size"] for f in files)},
                "closure": {"checked": True, "problems": 0}, "files": files}
        body["manifest_digest"] = hashlib.sha256(canon({"files": files, "profile": profile, "snapshot": {k: v for k, v in snap.items() if k in ("quiescent", "cumulative")}})).hexdigest()
        body["complete"] = True
        hook("before_commit")
        mp = os.path.join(part, MANIFEST)
        with open(mp + ".tmp", "wb") as f:
            f.write(json.dumps(body, indent=0, sort_keys=True).encode())
            f.flush()
            os.fsync(f.fileno())
        os.replace(mp + ".tmp", mp)
        fsync_dir(part)
        os.replace(part, out)                           # the one atomic step: the backup exists under its name only when complete
        fsync_dir(os.path.dirname(out))
        return {"ok": True, "backup": out, "files": len(files), "bytes": body["totals"]["bytes"], "quiescent": quiet, "passes": len(passes), "seconds": snap["seconds"],
                "manifest_digest": body["manifest_digest"], "profile": profile}
    except BaseException:
        # leave BACKUP.partial where it is (evidence of an unfinished backup, refused by verify and restore); never a half backup under the final name
        raise


# ------------------------------------------------------------------------------------------------------------------------ verify / restore
def load_manifest(backup):
    if backup.rstrip("/").endswith(".partial"):
        raise Failed("%s is an unfinished backup (a .partial folder is never a valid backup, even if it holds a manifest): incomplete, not valid" % backup)
    if os.path.isdir(backup + ".partial") and not os.path.exists(backup):
        raise Failed("%s is an unfinished backup (only %s.partial exists): incomplete, not valid" % (backup, os.path.basename(backup)))
    mp = os.path.join(backup, MANIFEST)
    if not os.path.isdir(backup):
        raise Failed("%s is not a backup directory" % backup)
    if not os.path.isfile(mp):
        raise Failed("%s has no %s: incomplete, not valid" % (backup, MANIFEST))
    try:
        with open(mp, "rb") as f:
            m = json.loads(f.read())
    except ValueError:
        raise Failed("%s is not readable JSON: incomplete or damaged, not valid" % mp)
    if m.get("format") != FORMAT or m.get("complete") is not True or "files" not in m:
        raise Failed("%s is not a complete %s manifest: not valid" % (mp, FORMAT))
    want = hashlib.sha256(canon({"files": m["files"], "profile": m["profile"], "snapshot": {k: v for k, v in m["snapshot"].items() if k in ("quiescent", "cumulative")}})).hexdigest()
    if m.get("manifest_digest") != want:
        raise Failed("the manifest does not match its own digest: damaged or edited, not valid")
    return m


def verify(backup, closure=True, files_dir=None):
    """-> {"ok": bool, "problems": [...], ...}; never raises for a damaged backup, it reports it."""
    backup = os.path.abspath(backup)
    try:
        m = load_manifest(backup)
    except Failed as e:
        return {"ok": False, "problems": [str(e)], "backup": backup}
    base = files_dir or os.path.join(backup, FILES)
    problems = []
    want = {f["path"]: f for f in m["files"]}
    have = {}
    try:
        have = listing(base) if os.path.isdir(base) else {}
    except Failed as e:
        problems.append(str(e))
    for rel in sorted(set(have) - set(want)):
        problems.append("extra file not in the manifest: %s" % rel)
    nbytes = 0
    for rel, f in want.items():
        p = os.path.join(base, rel)
        if rel not in have:
            problems.append("missing: %s" % rel)
            continue
        if have[rel][1] != f["size"]:
            problems.append("size differs: %s (%d, manifest %d)" % (rel, have[rel][1], f["size"]))
            continue
        if sha_file(p) != f["sha256"]:
            problems.append("SHA-256 differs: %s" % rel)
            continue
        nbytes += f["size"]
    if closure and not problems:
        problems += CLOSURE[m["profile"]](Tree(base, {f["path"]: (f["size"], f["sha256"]) for f in m["files"]}))
    return {"ok": not problems, "problems": problems[:50], "n_problems": len(problems), "backup": backup, "profile": m["profile"], "files": len(want), "bytes_verified": nbytes,
            "quiescent": m["snapshot"].get("quiescent"), "manifest_digest": m["manifest_digest"]}


def restore(backup, dest, fsync=True, hook=None):
    backup, dest = os.path.abspath(backup), os.path.abspath(dest)
    v = verify(backup)
    if not v["ok"]:
        return {"ok": False, "stage": "verify backup", "problems": v["problems"]}
    if os.path.exists(dest) and (not os.path.isdir(dest) or os.listdir(dest)):
        return {"ok": False, "stage": "destination", "problems": ["%s exists and is not an empty directory: a restore never writes into an existing tree" % dest]}
    m = load_manifest(backup)
    stage = dest + ".restoring"
    if os.path.exists(stage):
        return {"ok": False, "stage": "destination", "problems": ["%s exists (an earlier restore did not finish); inspect it first" % stage]}
    hook = hook or (lambda *a, **k: None)
    os.makedirs(stage, mode=0o755)
    made = set()
    for i, f in enumerate(m["files"]):
        rel = f["path"]
        hook("file", rel=rel, n=i)
        dst = os.path.join(stage, rel)
        d = os.path.dirname(dst)
        if d not in made:
            os.makedirs(d, exist_ok=True)
            made.add(d)
        sha, size = copy_file(os.path.join(backup, FILES, rel), dst, fsync)
        if sha != f["sha256"] or size != f["size"]:
            return {"ok": False, "stage": "copy", "problems": ["%s changed while it was being restored" % rel]}
        os.chmod(dst, 0o644)
    for d, dirs, _ in os.walk(stage):
        os.chmod(d, 0o755)
    r = verify_tree_against(stage, m)
    if r:
        return {"ok": False, "stage": "verify restored tree", "problems": r[:20]}
    if os.path.isdir(dest):
        os.rmdir(dest)                                    # empty, checked above
    os.replace(stage, dest)
    fsync_dir(os.path.dirname(dest))
    return {"ok": True, "dest": dest, "files": len(m["files"]), "bytes": m["totals"]["bytes"], "profile": m["profile"], "manifest_digest": m["manifest_digest"]}


def verify_tree_against(base, m):
    problems = []
    have = listing(base)
    want = {f["path"]: f for f in m["files"]}
    for rel in sorted(set(have) - set(want)):
        problems.append("extra: %s" % rel)
    for rel, f in want.items():
        if rel not in have:
            problems.append("missing: %s" % rel)
        elif have[rel][1] != f["size"] or sha_file(os.path.join(base, rel)) != f["sha256"]:
            problems.append("differs: %s" % rel)
    return problems or CLOSURE[m["profile"]](Tree(base, {f["path"]: (f["size"], f["sha256"]) for f in m["files"]}))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create")
    p.add_argument("--src", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--profile", default="public", choices=sorted(CLOSURE))
    p.add_argument("--max-passes", type=int, default=8)
    p.add_argument("--require-quiescent", action="store_true")
    p.add_argument("--no-fsync", action="store_true")
    p.add_argument("--discard-partial", action="store_true")
    p = sub.add_parser("verify")
    p.add_argument("backup")
    p.add_argument("--no-closure", action="store_true")
    p = sub.add_parser("restore")
    p.add_argument("backup")
    p.add_argument("dest")
    p.add_argument("--no-fsync", action="store_true")
    p = sub.add_parser("check")
    p.add_argument("--src", required=True)
    p.add_argument("--profile", default="public", choices=sorted(CLOSURE))
    a = ap.parse_args(argv)
    try:
        if a.cmd == "create":
            r = create(a.src, a.out, a.profile, a.max_passes, a.require_quiescent, not a.no_fsync, a.discard_partial)
        elif a.cmd == "verify":
            r = verify(a.backup, closure=not a.no_closure)
        elif a.cmd == "restore":
            r = restore(a.backup, a.dest, fsync=not a.no_fsync)
        else:
            problems = CLOSURE[a.profile](Tree(os.path.abspath(a.src)))
            r = {"ok": not problems, "problems": problems[:50], "n_problems": len(problems)}
    except Failed as e:
        r = {"ok": False, "problems": [str(e)]}
    print(json.dumps(r, sort_keys=True))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
