#!/usr/bin/env python3
"""Disk-space preflight for the backup tools (M8b). docs/PUBLIC_BACKUP.md section 3b.

  backup_space.py create  --src DIR --out BACKUP [--scratch DIR] [--allow-ram]
  backup_space.py verify  BACKUP [--scratch DIR] [--allow-ram]
  backup_space.py restore BACKUP DEST [--scratch DIR] [--allow-ram]

Read-only. It answers, before anything is written: how much disk will this need, on which filesystem, is there room (counting what is already there), and is any of it in RAM.
  * The peak need is estimated per FILESYSTEM (st_dev): paths that share one are added together (a backup's files, its manifest and its working index all live under BACKUP.partial;
    a restore's files and the scratch index share a filesystem when --scratch is beside DEST, and are then added).
  * Existing `.partial` folders, `.restoring` folders and `partial-quarantine` folders are evidence the tools never delete. They are reported with their sizes, are never counted as
    free space (the operating system's free figure already excludes them), and `attempts_that_fit` says how many attempts of this size fit in what is free: a failed attempt leaves
    a `.partial` of up to the full size, kept.
  * A destination or scratch folder on tmpfs/ramfs is REFUSED unless --allow-ram: the working index is meant to move memory to disk, and a backup in RAM is gone at the next reboot.
  * It is an ESTIMATE, not a reservation. Other writers may fill the disk in between; the tools themselves handle a full disk safely (docs section 3b), so passing the preflight
    never replaces that.
The figures come from the measured series in docs/PUBLIC_BACKUP.md (index about 2 x path length + 170 bytes a file at create, 2 x + 130 at verify and restore; manifest about path length + 127)
and are checked against real runs by tests/test_backup_space.py.
Exit 0 only when the preflight passed.
"""
import argparse
import json
import os
import re
import sys
import tempfile

RAM_FS = ("tmpfs", "ramfs")
MARGIN_DATA, MARGIN_INDEX, FIXED_MARGIN = 0.10, 0.25, 8 << 20
BLOCK = 4096
TMP = re.compile(r"(^|/)\.tmp-[^/]*$|\.tmp$")


# ------------------------------------------------------------------------------------------------------------------------ what is there
def tree_stats(path):
    """-> files, apparent bytes, allocated bytes (st_blocks, what the disk really uses), directories and the mean length of the relative paths. A missing path is empty."""
    files = nbytes = alloc = dirs = chars = 0
    if not os.path.isdir(path):
        return {"files": 0, "bytes": 0, "allocated": 0, "dirs": 0, "mean_path": 0.0}
    for d, dn, names in os.walk(path, followlinks=False):
        dirs += 1
        dn[:] = [x for x in dn if not x.startswith(".tmp-")]
        for n in names:
            p = os.path.join(d, n)
            rel = os.path.relpath(p, path).replace(os.sep, "/")
            if TMP.search(rel):
                continue
            try:
                st = os.lstat(p)
            except OSError:
                continue
            files += 1
            nbytes += st.st_size
            alloc += st.st_blocks * 512
            chars += len(rel)
    return {"files": files, "bytes": nbytes, "allocated": alloc, "dirs": dirs, "mean_path": (chars / files) if files else 0.0}


def estimate(stats, op):
    """Disk the operation will write for a tree like `stats`: files, manifest and working index, each separately (no margin)."""
    n, pl = stats["files"], stats["mean_path"]
    data = stats["allocated"] + stats["dirs"] * BLOCK
    manifest = n * (pl + 127)
    index = n * (2 * pl + (170 if op == "create" else 130))
    return {"files": n, "data": int(data), "manifest": int(manifest), "index": int(index)}


# ------------------------------------------------------------------------------------------------------------------------ where it goes
def _unescape(s):
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), s)


def mounts(mountinfo="/proc/self/mountinfo"):
    """[(mount point, filesystem type)] from a mountinfo file (a file path or its text)."""
    text = open(mountinfo).read() if os.path.exists(mountinfo) else mountinfo
    out = []
    for line in text.splitlines():
        left, sep, right = line.partition(" - ")
        f = left.split()
        if sep and len(f) >= 5:
            out.append((_unescape(f[4]), right.split()[0]))
    return out


def nearest_existing(path):
    p = os.path.abspath(path)
    while not os.path.exists(p):
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return p


def mount_of(path, table):
    real = os.path.realpath(os.path.abspath(path))
    best = ("/", "?")
    for mp, fs in table:
        if (real == mp or real.startswith(mp.rstrip("/") + "/") or mp == "/") and len(mp) >= len(best[0]):
            best = (mp, fs)
    return best


def is_ram_backed(path, table=None):
    return mount_of(path, mounts() if table is None else table)[1] in RAM_FS


# ------------------------------------------------------------------------------------------------------------------------ evidence already on disk
def kept_evidence(*names):
    """Unfinished folders that stay on disk, whatever happens: for each given NAME (a backup or restore destination), NAME.partial and NAME.restoring, and every `*.partial` /
    `*.restoring` beside it and the `partial-quarantine` folder beside it. -> [{"path", "kind", "bytes", "files"}]"""
    seen, out = set(), []
    for name in names:
        parent = os.path.dirname(os.path.abspath(name))
        cands = [os.path.abspath(name) + s for s in (".partial", ".restoring")]
        if os.path.isdir(parent):
            cands += [os.path.join(parent, x) for x in sorted(os.listdir(parent)) if x.endswith((".partial", ".restoring"))]
            cands.append(os.path.join(parent, "partial-quarantine"))
        for c in cands:
            if c in seen or not os.path.isdir(c):
                continue
            seen.add(c)
            st = tree_stats(c)
            kind = "quarantine folder" if c.endswith("partial-quarantine") else ("unfinished restore" if c.endswith(".restoring") else "unfinished backup")
            out.append({"path": c, "kind": kind, "bytes": st["allocated"], "files": st["files"]})
    return out


# ------------------------------------------------------------------------------------------------------------------------ the verdict
def _fmt(n):
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%d %s" % (n, unit)) if unit == "bytes" else ("%.1f %s" % (n, unit))
        n /= 1024.0


def _free(path):
    s = os.statvfs(nearest_existing(path))
    return s.f_bavail * s.f_frsize


def evaluate(needs, evidence_names=(), allow_ram=False, table=None, free=_free, note=None):
    """needs: [{"path", "what", "bytes", "margin"}]; paths on one filesystem are summed. -> the report."""
    table = mounts() if table is None else table
    groups = {}
    for need in needs:
        dev = os.stat(nearest_existing(need["path"])).st_dev
        g = groups.setdefault(dev, {"device": dev, "paths": [], "items": [], "required": 0, "estimate": 0})
        g["paths"].append(need["path"])
        g["items"].append({"what": need["what"], "path": need["path"], "bytes": int(need["bytes"])})
        g["estimate"] += int(need["bytes"])
        g["required"] += int(need["bytes"] + need.get("margin", 0))
    ev = kept_evidence(*evidence_names)
    problems, warnings, fss = [], [], []
    for dev, g in groups.items():
        mp, fstype = mount_of(g["paths"][0], table)
        avail = free(g["paths"][0])
        here = [e for e in ev if os.stat(nearest_existing(e["path"])).st_dev == dev]
        kept = sum(e["bytes"] for e in here)
        fit = int(avail // g["required"]) if g["required"] else 0
        fss.append({"mount": mp, "fstype": fstype, "free_bytes": avail, "estimated_bytes": g["estimate"], "required_bytes_with_margin": g["required"], "items": g["items"],
                    "kept_evidence": here, "kept_evidence_bytes": kept, "attempts_that_fit": fit})
        what = "; ".join("%s %s" % (_fmt(i["bytes"]), i["what"]) for i in g["items"])
        if fstype in RAM_FS:
            msg = ("%s is on %s, which is memory (RAM), not disk: %s would be written there. A backup in RAM is lost at the next reboot, and a working index in RAM gives back the "
                   "memory the index exists to save. Choose a folder on a real disk (--scratch DIR for the index), or pass --allow-ram if you really mean it." % (
                       g["paths"][0], fstype, what))
            (warnings if allow_ram else problems).append(("(allowed by --allow-ram) " if allow_ram else "") + msg)
        if avail < g["required"]:
            problems.append("not enough free space on the filesystem of %s (%s): about %s needed including a safety margin (%s), %s free%s. Nothing was written." % (
                g["paths"][0], mp, _fmt(g["required"]), what, _fmt(avail),
                "; %s of it is already held by unfinished folders that are kept as evidence and are never counted as free" % _fmt(kept) if kept else ""))
        elif fit < 2:
            warnings.append("room for only one attempt on %s: a failed attempt leaves a .partial of up to %s that is kept as evidence, after which the next attempt needs more space "
                            "(free it elsewhere or use another destination; nothing here deletes anything)." % (mp, _fmt(g["required"])))
    for e in ev:
        warnings.append("kept on disk: %s (%s, %s, %d files)" % (e["path"], e["kind"], _fmt(e["bytes"]), e["files"]))
    return {"ok": not problems, "problems": problems, "warnings": warnings, "filesystems": fss,
            "estimate_only": "an estimate, not a reservation: other writers can still fill the disk; the tools handle a full disk safely" + (" (" + note + ")" if note else "")}


def _need(path, what, b, margin_rate, fixed=0):
    return {"path": path, "what": what, "bytes": b, "margin": int(b * margin_rate) + fixed}


def preflight_create(src, out, allow_ram=False, table=None, free=_free, discard_partial=False):
    out = os.path.abspath(out)
    st = tree_stats(src)
    e = estimate(st, "create")
    needs = [_need(out, "of backed-up files", e["data"], MARGIN_DATA, FIXED_MARGIN), _need(out, "of manifest", e["manifest"], MARGIN_DATA),
             _need(out, "of working index inside %s.partial (removed before the commit)" % os.path.basename(out), e["index"], MARGIN_INDEX)]
    r = evaluate(needs, [out], allow_ram, table, free)
    r["operation"], r["estimate"] = "create", e
    if os.path.exists(out):
        r["problems"].append("%s already exists: a backup is never overwritten" % out)
    if os.path.isdir(out + ".partial") and not discard_partial:
        r["problems"].append("%s exists (an earlier backup did not finish): quarantine it first (public_backup.py quarantine); its space stays in use" % (out + ".partial"))
    r["ok"] = not r["problems"]
    return r


def preflight_verify(backup, scratch=None, allow_ram=False, table=None, free=_free):
    scratch = os.path.abspath(scratch or tempfile.gettempdir())
    e = estimate(tree_stats(os.path.join(backup, "files")), "verify")
    r = evaluate([_need(scratch, "of working index (removed afterwards)", e["index"], MARGIN_INDEX, FIXED_MARGIN)], [], allow_ram, table, free)
    r["operation"], r["estimate"] = "verify", e
    return r


def preflight_restore(backup, dest, scratch=None, allow_ram=False, table=None, free=_free):
    dest = os.path.abspath(dest)
    scratch = os.path.abspath(scratch or tempfile.gettempdir())
    e = estimate(tree_stats(os.path.join(backup, "files")), "restore")
    needs = [_need(dest, "of restored files (staged in %s.restoring)" % os.path.basename(dest), e["data"], MARGIN_DATA, FIXED_MARGIN),
             _need(scratch, "of working index (removed afterwards)", e["index"], MARGIN_INDEX)]
    r = evaluate(needs, [dest], allow_ram, table, free)
    r["operation"], r["estimate"] = "restore", e
    if os.path.isdir(dest + ".restoring"):
        r["problems"].append("%s exists (an earlier restore did not finish): quarantine it first (public_backup.py quarantine); its space stays in use" % (dest + ".restoring"))
    r["ok"] = not r["problems"]
    return r


def preflight_set(public, store, league_files, out, allow_ram=False, table=None, free=_free):
    """A recovery set: the public folder, the store and the league files, one after the other (their working indexes do not coexist: the larger one counts)."""
    out = os.path.abspath(out)
    sp, ss = tree_stats(public), tree_stats(store)
    ep, es = estimate(sp, "create"), estimate(ss, "create")
    league = 0
    for f in league_files:
        try:
            league += os.stat(f).st_blocks * 512
        except OSError:
            pass
    data = ep["data"] + es["data"] + league
    needs = [_need(out, "of backed-up files (public, store, league)", data, MARGIN_DATA, FIXED_MARGIN), _need(out, "of manifests", ep["manifest"] + es["manifest"], MARGIN_DATA),
             _need(out, "of the largest working index", max(ep["index"], es["index"]), MARGIN_INDEX)]
    r = evaluate(needs, [out], allow_ram, table, free)
    r["operation"], r["estimate"] = "recovery set create", {"public": ep, "store": es, "league_bytes": league}
    if os.path.exists(out) or os.path.isdir(out + ".partial"):
        r["problems"].append("%s or %s.partial exists: a set is never overwritten; quarantine an unfinished one first" % (out, out))
    r["ok"] = not r["problems"]
    return r


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--src", required=True)
    c.add_argument("--out", required=True)
    v = sub.add_parser("verify")
    v.add_argument("backup")
    r = sub.add_parser("restore")
    r.add_argument("backup")
    r.add_argument("dest")
    for p in (c, v, r):
        p.add_argument("--scratch")
        p.add_argument("--allow-ram", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "create":
        res = preflight_create(a.src, a.out, a.allow_ram)
    elif a.cmd == "verify":
        res = preflight_verify(os.path.abspath(a.backup), a.scratch, a.allow_ram)
    else:
        res = preflight_restore(os.path.abspath(a.backup), a.dest, a.scratch, a.allow_ram)
    print(json.dumps(res, sort_keys=True))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
