#!/usr/bin/env python3
"""Back up a league and its archive as ONE folder with an explicit manifest, check such a folder, and restore from it. Run with python3 -I.

  league_backup.py create  LEAGUE_DB OUT_DIR [--include FILE ...] [--note TEXT]   consistent online copy (league first, archive second), then the manifest
  league_backup.py verify  BACKUP_DIR [--deep]                                     every file against the manifest, then the league, the archive, and the two together
  league_backup.py restore BACKUP_DIR DEST_DIR                                     verify, restore into an empty folder, verify the restored pair again

Why a manifest: the league file records its archive by a name that is only right beside the live league (`league.db.archive`). A backup folder holds the
two files under their own names (`league.db`, `archive.db`), so a check that looks the archive up by the recorded name reports a perfectly good backup as
"archive missing". The manifest names each component, its size and its SHA-256, and the checks here are handed the archive's path explicitly.

This is a tool, not engine code: it only calls the engine's read/verify/backup functions, so it changes no league fingerprint. Exit 0 = nothing wrong.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

FORMAT, VERSION, MANIFEST = "dfl-backup", 1, "BACKUP_MANIFEST.json"
LEAGUE, ARCHIVE, CONFIG = "league.db", "archive.db", "config"
MAX_CONFIG_BYTES = 1 << 20


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _component(role, rel, path):
    return {"role": role, "file": rel, "size": os.path.getsize(path), "sha256": sha256_file(path)}


def _identity(league_path):
    """What the league file itself says about the state it holds (read-only)."""
    from dfl import persist
    db = persist.connect(league_path, readonly=True)
    try:
        meta = persist.read_meta(db)
        row = db.execute("SELECT seq, key, chain FROM journal ORDER BY seq DESC LIMIT 1").fetchone()
    finally:
        db.close()
    base = meta.get("base", {"seq": 0, "chain": None})
    head = {"seq": row[0], "key": row[1], "chain": row[2]} if row else {"seq": base["seq"], "key": None, "chain": base.get("chain")}
    return {"league_id": meta.get("league_id"), "journal_head": head, "code_fingerprint": meta.get("fingerprint"), "league_format": meta.get("format"),
            "rule_version": meta.get("rule_version")}


def _sidecars(folder):
    return {n for n in os.listdir(folder) if n.endswith(("-wal", "-shm"))}


def _remove_new_sidecars(folder, before):
    """Opening a SQLite file in WAL mode, even read-only, can leave empty -wal/-shm files beside it. They hold nothing and would end up in a tarball."""
    for n in _sidecars(folder) - before:
        p = os.path.join(folder, n)
        if n.endswith("-shm") or os.path.getsize(p) == 0:
            os.remove(p)


def write_manifest(out_dir, manifest):
    tmp = os.path.join(out_dir, MANIFEST + ".tmp")
    with open(tmp, "w") as f:
        json.dump(manifest, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, os.path.join(out_dir, MANIFEST))


def create(league, out_dir, include=(), note=None):
    from dfl import persist
    if os.path.exists(out_dir) and os.listdir(out_dir):
        raise SystemExit(f"{out_dir} is not empty; a backup never goes into an existing folder")
    os.makedirs(out_dir, exist_ok=True)
    persist.backup_set(league, out_dir)                                       # league first, archive second: the archive is never behind the league
    _remove_new_sidecars(out_dir, set())
    lg, ar = os.path.join(out_dir, LEAGUE), os.path.join(out_dir, ARCHIVE)
    comps = [_component("league", LEAGUE, lg)]
    if os.path.exists(ar):
        comps.append(_component("archive", ARCHIVE, ar))
    if include:
        os.makedirs(os.path.join(out_dir, CONFIG), exist_ok=True)
    for src in include:
        if os.path.getsize(src) > MAX_CONFIG_BYTES:
            raise SystemExit(f"{src} is too large to be a configuration file")
        rel = f"{CONFIG}/{os.path.basename(src)}"
        shutil.copyfile(src, os.path.join(out_dir, rel))
        comps.append(_component("config", rel, os.path.join(out_dir, rel)))
    ident = _identity(lg)
    arch = None
    if os.path.exists(ar):
        from dfl import archive as A
        db = A._connect(ar, readonly=True)
        try:
            arch = {"games": db.execute("SELECT COUNT(*) FROM games").fetchone()[0], "traces": db.execute("SELECT COUNT(*) FROM traces").fetchone()[0],
                    "records": db.execute("SELECT COUNT(*) FROM records").fetchone()[0], "batches": db.execute("SELECT COUNT(*) FROM batches").fetchone()[0]}
        finally:
            db.close()
    manifest = {"format": FORMAT, "version": VERSION, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "note": note,
                "components": comps, "archive_counts": arch, **ident}
    _remove_new_sidecars(out_dir, set())
    write_manifest(out_dir, manifest)
    return manifest


def verify(backup_dir, deep=False):
    """-> {"problems": [...], "notes": [...], "manifest": {...} or None}. Nothing in the folder is changed."""
    before = _sidecars(backup_dir) if os.path.isdir(backup_dir) else set()
    try:
        return _verify(backup_dir, deep)
    finally:
        if os.path.isdir(backup_dir):
            _remove_new_sidecars(backup_dir, before)


def _verify(backup_dir, deep):
    from dfl import persist, archive as A
    problems, notes = [], []
    mpath = os.path.join(backup_dir, MANIFEST)
    if not os.path.isfile(mpath):
        return {"problems": [f"{MANIFEST} is missing: this is not a backup made by this tool, or the manifest was lost"], "notes": notes, "manifest": None}
    try:
        with open(mpath) as f:
            m = json.load(f)
    except (OSError, ValueError) as e:
        return {"problems": [f"{MANIFEST} cannot be read: {type(e).__name__}"], "notes": notes, "manifest": None}
    if not isinstance(m, dict) or m.get("format") != FORMAT or m.get("version") != VERSION or not isinstance(m.get("components"), list):
        return {"problems": [f"{MANIFEST} is not a {FORMAT} v{VERSION} manifest"], "notes": notes, "manifest": None}
    paths = {}
    for c in m["components"]:
        try:
            role, rel, size, want = c["role"], c["file"], c["size"], c["sha256"]
        except (KeyError, TypeError):
            problems.append("a manifest entry is malformed")
            continue
        if os.path.isabs(rel) or ".." in rel.split("/"):
            problems.append(f"manifest entry {rel!r} points outside the backup folder")
            continue
        p = os.path.join(backup_dir, rel)
        paths.setdefault(role, p)
        if not os.path.isfile(p):
            problems.append(f"{role} file {rel} is missing")
        elif os.path.getsize(p) != size:
            problems.append(f"{role} file {rel} has {os.path.getsize(p)} bytes, the manifest says {size}")
        elif sha256_file(p) != want:
            problems.append(f"{role} file {rel} does not match its recorded SHA-256 (damaged or replaced)")
    if "league" not in paths:
        problems.append("the manifest lists no league file")
    lg, ar = paths.get("league"), paths.get("archive")
    if lg and os.path.isfile(lg) and not any("league file" in x for x in problems):
        try:
            problems += [f"league file: {x}" for x in persist.verify(lg, deep=deep)]
            ident = _identity(lg)
            for k in ("league_id", "journal_head", "code_fingerprint"):
                if ident[k] != m.get(k):
                    problems.append(f"the league file's {k} differs from the manifest's")
        except Exception as e:                                                  # a damaged file may fail in any way; that is a finding, not a crash
            problems.append(f"league file cannot be checked: {type(e).__name__}")
    if m.get("archive_counts") is not None and not ar:
        problems.append("the manifest records an archive but lists no archive file")
    if ar and os.path.isfile(ar) and not any("archive file" in x for x in problems):
        try:
            problems += [f"archive file: {x}" for x in A.verify(ar)]
        except Exception as e:
            problems.append(f"archive file cannot be checked: {type(e).__name__}")
        if lg and os.path.isfile(lg):
            try:
                c = persist.archive_check(lg, archive_file=ar)                  # the archive is named explicitly: no guessing from the recorded name
            except Exception as e:
                problems.append(f"the league and archive cannot be compared: {type(e).__name__}")
                c = None
            if c:
                if c["absent"] or c["unreadable"]:
                    problems.append("the archive cannot be read beside this league file")
                if c["foreign"]:
                    problems.append("the archive belongs to a different league")
                if not c["base_ok"]:
                    problems.append("the archive's history differs from what the league recorded before this file began")
                if c["missing"]:
                    problems.append(f"the archive lacks records for events {c['missing'][:8]}")
                if c["mismatched"]:
                    problems.append(f"the archive's records differ from the league journal for events {c['mismatched'][:8]}")
                if c["ahead"]:
                    notes.append(f"the archive holds {len(c['ahead'])} event(s) beyond the league file's head (an interrupted commit leaves one; harmless)")
    return {"problems": problems, "notes": notes, "manifest": m}


def restore(backup_dir, dest_dir):
    from dfl import persist
    res = verify(backup_dir)
    if res["problems"]:
        return {"restored": False, "problems": res["problems"], "notes": res["notes"]}
    if os.path.exists(dest_dir) and os.listdir(dest_dir):
        raise SystemExit(f"{dest_dir} is not empty; restore never overwrites")
    r = persist.restore_set(backup_dir, dest_dir)
    seq = r.seq
    del r
    again = []
    lg, ar = os.path.join(dest_dir, "league.db"), os.path.join(dest_dir, "archive.db")
    c = persist.archive_check(lg)
    if c["absent"] or c["unreadable"] or c["foreign"] or not c["base_ok"] or c["missing"] or c["mismatched"]:
        again.append("the restored league and archive do not agree")
    again += [f"restored league: {x}" for x in persist.verify(lg)]
    m = res["manifest"]
    if seq != m["journal_head"]["seq"]:
        again.append(f"the restored league is at event {seq}, the backup recorded {m['journal_head']['seq']}")
    ident = _identity(lg)
    if ident["journal_head"] != m["journal_head"]:
        again.append("the restored league's head (event, key, chain) differs from the manifest")
    return {"restored": not again, "problems": again, "notes": res["notes"], "events": seq, "league": lg, "archive": ar if os.path.exists(ar) else None}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("league")
    c.add_argument("out")
    c.add_argument("--include", action="append", default=[])
    c.add_argument("--note")
    v = sub.add_parser("verify")
    v.add_argument("dir")
    v.add_argument("--deep", action="store_true")
    r = sub.add_parser("restore")
    r.add_argument("dir")
    r.add_argument("dest")
    a = ap.parse_args(argv)
    if a.cmd == "create":
        m = create(a.league, a.out, a.include, a.note)
        print(json.dumps({"backup": a.out, "events": m["journal_head"]["seq"], "components": [(c["file"], c["size"], c["sha256"][:16]) for c in m["components"]]}, indent=1))
        return 0
    if a.cmd == "verify":
        res = verify(a.dir, a.deep)
        print(json.dumps({"ok": not res["problems"], "problems": res["problems"], "notes": res["notes"]}, indent=1))
        return 0 if not res["problems"] else 1
    res = restore(a.dir, a.dest)
    print(json.dumps(res, indent=1))
    return 0 if res["restored"] else 1


if __name__ == "__main__":
    sys.exit(main())
