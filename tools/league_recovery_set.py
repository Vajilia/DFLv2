#!/usr/bin/env python3
"""The recovery set of an upgraded league: what must be kept, and a restore test that uses nothing else. Run with python3 -I.

  league_recovery_set.py create SET_DIR --league ACTIVE_DB --original ORIGINAL_DB     build the set (SET_DIR must be new or empty)
  league_recovery_set.py verify SET_DIR                                                every file against the manifest, the files against each other
  league_recovery_set.py restore-test SET_DIR WORK_DIR [--replay-from SEQ] [--require-sources-absent]
                                                                                       restore everything from the set alone, into WORK_DIR, and check it

Why a set. A league that was moved to new code with `league_store.py upgrade` continues the old file's sequence numbers and hash chain, but the new
file starts from ONE snapshot at the base event and holds only the journal rows after it. So the new league file alone cannot replay the league
from its beginning. The games themselves are safe in the archive (the new archive is the old one, extended). The earlier restore points and journal
rows 1..base exist only in the old league file. See docs/LEAGUE_RECOVERY_SET.md.

The set (all paths relative to SET_DIR):
  RECOVERY_SET.json            the manifest: every file's size and SHA-256, both files' identities, how they link, the code tree, what each part preserves
  active/                      a `league_backup.py create` folder of the live league: league.db, archive.db (the archive of seasons 1..N), its manifest
  original-141/league.db       the old league file, byte for byte (its SHA-256 is the one the new file's lineage records)
  code/engine-tree.tar.gz      src/, data/ and tools/ of the tree that runs the active league (its code fingerprint is checked against the file's)
The OLD code (the code that wrote the old file) is not a separate item: every league file carries the exact source it was written with in its `code`
table, and the restore test extracts and runs it from there. The old file's own archive (a subset of the new one) is not needed: the restore test
checks the old journal against the NEW archive.

restore-test uses only SET_DIR and WORK_DIR: the code it runs is unpacked from the set, every subprocess runs `python3 -I` with a scrubbed environment,
and with --require-sources-absent it fails if any path the league used on its original server still exists on this machine, so a pass proves
independence from those paths. Exit 0 only if every step passed. This is a tool, not engine code: it changes no league fingerprint."""
import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

FORMAT, VERSION, MANIFEST = "dfl-recovery-set", 1, "RECOVERY_SET.json"
ACTIVE, ORIGINAL, CODE = "active", "original-141", "code"
TREE = f"{CODE}/engine-tree.tar.gz"
TREE_DIRS = ("src", "data", "tools")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def comp(role, set_dir, rel):
    p = os.path.join(set_dir, rel)
    return {"role": role, "file": rel, "size": os.path.getsize(p), "sha256": sha256_file(p)}


def read_json(path):
    with open(path) as f:
        return json.load(f)


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def _quiet(path):
    """A league file must have no un-checkpointed write-ahead log (a stopped league has none), or a byte copy would miss what is in it."""
    wal = path + "-wal"
    if os.path.exists(wal) and os.path.getsize(wal):
        raise SystemExit(f"{path} has a write-ahead log with data in it: stop the runner (a clean stop empties it) before copying the file byte for byte")


def _identity(path):
    from dfl import persist
    db = persist.connect(path, readonly=True)
    try:
        meta = persist.read_meta(db)
        head = db.execute("SELECT seq, digest, chain FROM journal ORDER BY seq DESC LIMIT 1").fetchone()
        first = db.execute("SELECT MIN(seq), COUNT(*) FROM journal").fetchone()
        snaps = [(s, c) for s, c in db.execute("SELECT seq, checkpoint FROM snapshots ORDER BY seq")]
    finally:
        db.close()
    base = meta.get("base") or {"seq": 0, "chain": persist.GENESIS}
    return {"league_id": meta.get("league_id"), "fingerprint": meta.get("fingerprint"), "base": base, "lineage": meta.get("lineage") or [],
            "head": {"seq": head[0], "digest": head[1], "chain": head[2]} if head else {"seq": base["seq"], "digest": None, "chain": base["chain"]},
            "journal_rows": first[1], "first_journal_seq": first[0], "checkpoint_snapshots": [s for s, c in snaps if c], "other_snapshots": [s for s, c in snaps if not c]}


def _archive_counts(path):
    import sqlite3
    db = sqlite3.connect(f"file:{os.path.abspath(path)}?immutable=1", uri=True)
    try:
        return {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("batches", "games", "traces", "records")}
    finally:
        db.close()


def _pack_tree(dest, root):
    def keep(ti):
        return None if "__pycache__" in ti.name or ti.name.endswith(".pyc") else ti
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with tarfile.open(dest, "w:gz") as t:
        for d in TREE_DIRS:
            if not os.path.isdir(os.path.join(root, d)):
                raise SystemExit(f"the code tree {root} has no {d}/ folder")
            t.add(os.path.join(root, d), arcname=d, filter=keep)
            


def _unpack_tree(tar_path, dest):
    os.makedirs(dest)
    with tarfile.open(tar_path) as t:
        for m in t.getmembers():
            if os.path.isabs(m.name) or ".." in m.name.split("/"):
                raise SystemExit(f"unsafe path in the code tree: {m.name}")
        t.extractall(dest, filter="data")


def _tidy(set_dir):
    """Opening a SQLite file in WAL mode, even read-only, can leave empty -wal/-shm files beside it. They hold nothing; they are not part of the set."""
    for dirpath, _, files in os.walk(set_dir):
        for n in files:
            p = os.path.join(dirpath, n)
            if n.endswith("-shm") or (n.endswith("-wal") and os.path.getsize(p) == 0):
                os.remove(p)


def _git(root, *args):
    try:
        return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, timeout=30).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


# ---------------------------------------------------------------------------------------------------------------- create
def create(set_dir, league, original, code_tree=None):
    import league_backup as LB
    from dfl import persist
    if os.path.exists(set_dir) and os.listdir(set_dir):
        raise SystemExit(f"{set_dir} is not empty; a recovery set never goes into an existing folder")
    for p in (league, original):
        if not os.path.isfile(p):
            raise SystemExit(f"{p} does not exist")
    _quiet(original)
    os.makedirs(set_dir, exist_ok=True)
    act = LB.create(league, os.path.join(set_dir, ACTIVE), note="recovery set: the active league and its archive")
    orig_dir = os.path.join(set_dir, ORIGINAL)
    os.makedirs(orig_dir)
    shutil.copyfile(original, os.path.join(orig_dir, "league.db"))                # byte for byte: its SHA-256 is what the new file's lineage records
    ai, oi = _identity(os.path.join(set_dir, ACTIVE, LB.LEAGUE)), _identity(os.path.join(orig_dir, "league.db"))
    problems = []
    if not ai["lineage"]:
        problems.append("the active league has no lineage: it was not made by an upgrade, so it needs no second file (an ordinary backup is enough)")
    else:
        link = ai["lineage"][-1]
        if link.get("source_file_sha256") != sha256_file(os.path.join(orig_dir, "league.db")):
            problems.append("the original file is not the file the active league was upgraded from (its SHA-256 differs from the lineage's)")
        if link.get("from_seq") != oi["head"]["seq"] or link.get("from_chain") != oi["head"]["chain"]:
            problems.append("the original file's head differs from the event and chain the active league continues")
    if oi["league_id"] != ai["league_id"]:
        problems.append("the two files are different leagues")
    if oi["fingerprint"] == ai["fingerprint"]:
        pass                                                                       # a same-code upgrade is allowed; the originating code is then simply the same
    tree = os.path.abspath(code_tree or ROOT)
    fp = persist.fingerprint(tree)
    if fp != ai["fingerprint"]:
        problems.append(f"the code tree {tree} ({fp[:12]}) is not the code that runs the active league ({str(ai['fingerprint'])[:12]}): name the tree that runs it with --code-tree")
    sp = persist.verify(os.path.join(orig_dir, "league.db"))
    problems += [f"original file: {x}" for x in sp]
    c = persist.archive_check(os.path.join(orig_dir, "league.db"), archive_file=os.path.join(set_dir, ACTIVE, LB.ARCHIVE))
    if c["absent"] or c["unreadable"] or c["foreign"] or not c["base_ok"] or c["missing"] or c["mismatched"]:
        problems.append("the active archive does not hold exactly what the original file's journal committed")
    if problems:
        shutil.rmtree(set_dir, ignore_errors=True)
        raise SystemExit("recovery set NOT created:\n  " + "\n  ".join(problems))
    _pack_tree(os.path.join(set_dir, TREE), tree)
    components = [comp("active-manifest", set_dir, f"{ACTIVE}/{LB.MANIFEST}"), comp("active-league", set_dir, f"{ACTIVE}/{LB.LEAGUE}"),
                  comp("active-archive", set_dir, f"{ACTIVE}/{LB.ARCHIVE}"), comp("original-league", set_dir, f"{ORIGINAL}/league.db"),
                  comp("code-tree", set_dir, TREE)]
    manifest = {
        "format": FORMAT, "version": VERSION, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "components": components,
        "active": {"head": ai["head"], "fingerprint": ai["fingerprint"], "journal_first_seq": ai["first_journal_seq"], "journal_rows": ai["journal_rows"],
                   "base": ai["base"], "checkpoint_snapshots": ai["checkpoint_snapshots"], "archive_counts": act["archive_counts"]},
        "original": {"head": oi["head"], "fingerprint": oi["fingerprint"], "journal_first_seq": oi["first_journal_seq"], "journal_rows": oi["journal_rows"],
                     "checkpoint_snapshots": oi["checkpoint_snapshots"], "other_snapshots": oi["other_snapshots"]},
        "link": {"active_lineage_source_file_sha256": ai["lineage"][-1]["source_file_sha256"], "continues_event": ai["base"]["seq"], "chain": ai["base"]["chain"]},
        "code": {"tree_fingerprint": fp, "tree_git_commit": _git(tree, "rev-parse", "HEAD"), "tree_dirty": bool(_git(tree, "status", "--porcelain")),
                 "originating_code": f"carried inside each league file (code table): {oi['fingerprint']} in the original, {ai['fingerprint']} in the active file"},
        "source_paths": sorted({os.path.abspath(p) for p in (league, original, league + ".archive", original + ".archive", os.path.dirname(os.path.abspath(league)),
                                                                os.path.dirname(os.path.abspath(original)), tree)}),
        "preserves": {
            "game_history": "every game, play and recorded player movement of seasons 1..N: the active archive (a superset of the original's archive, checked row by row against the original journal)",
            "state_history": f"the league's state at every year end and every event: the original file holds journal rows 1..{oi['head']['seq']} and restore points {oi['checkpoint_snapshots']} + {oi['other_snapshots']}; "
                             f"the active file holds only rows after event {ai['base']['seq']}",
        },
    }
    _tidy(set_dir)
    write_json(os.path.join(set_dir, MANIFEST), manifest)
    return manifest


# ---------------------------------------------------------------------------------------------------------------- verify
def verify(set_dir):
    try:
        return _verify(set_dir)
    finally:
        _tidy(set_dir)


def _verify(set_dir):
    import league_backup as LB
    problems = []
    mpath = os.path.join(set_dir, MANIFEST)
    if not os.path.isfile(mpath):
        return {"problems": [f"{MANIFEST} is missing"], "manifest": None}
    m = read_json(mpath)
    if m.get("format") != FORMAT or m.get("version") != VERSION:
        return {"problems": [f"{MANIFEST} is not a {FORMAT} v{VERSION} manifest"], "manifest": None}
    roles = set()
    for c in m["components"]:
        roles.add(c["role"])
        rel = c["file"]
        if os.path.isabs(rel) or ".." in rel.split("/"):
            problems.append(f"manifest entry {rel!r} points outside the set")
            continue
        p = os.path.join(set_dir, rel)
        if not os.path.isfile(p):
            problems.append(f"{c['role']} file {rel} is missing")
        elif os.path.getsize(p) != c["size"] or sha256_file(p) != c["sha256"]:
            problems.append(f"{c['role']} file {rel} does not match the manifest (damaged or replaced)")
    for need in ("active-league", "active-archive", "original-league", "code-tree", "active-manifest"):
        if need not in roles:
            problems.append(f"the set has no {need}")
    if not problems:
        res = LB.verify(os.path.join(set_dir, ACTIVE))
        problems += [f"active backup: {x}" for x in res["problems"]]
    return {"problems": problems, "manifest": m}


# ---------------------------------------------------------------------------------------------------------------- restore test
# Each script runs as `python3 -I -c SCRIPT CODE_DIR ...` against ONE code tree out of the set, never against the tree this tool sits in.
_RESTORE_ACTIVE = r"""
import sys, json
code, backup, dest = sys.argv[1:4]
sys.path.insert(0, code + "/src")
from dfl import persist
r = persist.restore_set(backup, dest)                    # refuses a code change, so it only succeeds under the code recorded in the file
out = {"events": r.seq, "chain": r.chain, "fingerprint": r.meta["fingerprint"]}
r.close()
print(json.dumps(out))
"""
_STATIC_ORIGINAL = r"""
import sys, json
code, db, arch = sys.argv[1:4]
sys.path.insert(0, code + "/src")
from dfl import persist
c = persist.archive_check(db, archive_file=arch)
print(json.dumps({"problems": persist.verify(db), "archive": {k: (len(v) if isinstance(v, list) else v) for k, v in c.items()}}, default=str))
"""
_OPEN_ORIGINAL = r"""
import sys, json
code, db = sys.argv[1:3]
sys.path.insert(0, code + "/src")
from dfl import persist
r = persist.Runner.open(db, heal=False, with_archive=False)       # the one call every format-3 version provides; replays from the newest restore point
out = {"events": r.seq, "chain": r.chain, "fingerprint": r.meta["fingerprint"]}
try:
    out["digest"] = persist.journal_digest(r.u)
except Exception:
    out["digest"] = None
r.close()
print(json.dumps(out))
"""
_REPLAY_FROM = r"""
import sys, json, sqlite3
code, db, keep = sys.argv[1], sys.argv[2], int(sys.argv[3])
sys.path.insert(0, code + "/src")
from dfl import persist
d = sqlite3.connect(db)
d.execute("DELETE FROM snapshots WHERE seq > ?", (keep,))
d.commit()
left = [r[0] for r in d.execute("SELECT seq FROM snapshots ORDER BY seq")]
d.close()
r = persist.Runner.open(db, heal=False, with_archive=False)
out = {"snapshots_left": left, "from": max(left), "events": r.seq, "chain": r.chain}
try:
    out["digest"] = persist.journal_digest(r.u)
except Exception:
    out["digest"] = None
r.close()
print(json.dumps(out))
"""


def _run(cmd, cwd, timeout=None):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": cwd, "LANG": "C.UTF-8"}
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)


def _last_json(text):
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


def restore_test(set_dir, work_dir, replay_from=None, require_sources_absent=False):
    steps = []

    def step(name, ok, detail=None):
        steps.append({"step": name, "ok": bool(ok), "detail": detail})
        return ok

    def tail(r):
        return (r.stdout + r.stderr)[-500:]

    v = verify(set_dir)
    if not step("the set verifies against its manifest", not v["problems"], v["problems"] or None):
        return {"ok": False, "steps": steps}
    m = v["manifest"]
    present = [p for p in m.get("source_paths", []) if os.path.exists(p)]
    if require_sources_absent:
        if not step("the league's original server paths are not available on this machine", not present, present or None):
            return {"ok": False, "steps": steps}
    else:
        step("original server paths on this machine (a pass does not prove independence from them)", True, present or "none")
    if os.path.exists(work_dir) and os.listdir(work_dir):
        raise SystemExit(f"{work_dir} is not empty")
    os.makedirs(work_dir, exist_ok=True)
    work = os.path.abspath(work_dir)
    sp = lambda *p: os.path.join(os.path.abspath(set_dir), *p)                      # noqa: E731
    wp = lambda *p: os.path.join(work, *p)                                          # noqa: E731
    py = [sys.executable, "-I"]
    # 1. the code that runs the active league comes out of the set; every later step runs under a tree taken from the set
    _unpack_tree(sp(TREE), wp("code-active"))
    from_set = _run(py + ["-c", "import sys; sys.path.insert(0, sys.argv[1] + '/src'); from dfl import persist; print(persist.fingerprint(sys.argv[1]))", wp("code-active")], work)
    step("the code tree in the set is the code recorded in the active league file", from_set.stdout.strip() == m["active"]["fingerprint"], from_set.stdout.strip()[:16] or tail(from_set))
    # 2. restore the active league and its archive from the set only; opening the result runs the file's own consistency checks
    r = _run(py + ["-c", _RESTORE_ACTIVE, wp("code-active"), sp(ACTIVE), wp("restored-active")], work)
    got = _last_json(r.stdout) or {}
    want = m["active"]["head"]
    if not step("the active league and archive restore and open under the code from the set", r.returncode == 0, tail(r) if r.returncode else None):
        return {"ok": False, "steps": steps}
    step("restored active league is at the recorded event and chain", got.get("events") == want["seq"] and got.get("chain") == want["chain"], {"events": got.get("events"), "chain": got.get("chain")})
    counts = _archive_counts(wp("restored-active", "archive.db"))
    step("restored archive holds every batch, game, trace and record the set recorded", counts == m["active"]["archive_counts"], counts)
    # 3. the original league file: its own code comes out of the file itself
    os.makedirs(wp("restored-original"))
    shutil.copyfile(sp(ORIGINAL, "league.db"), wp("restored-original", "league.db"))
    ex = _run(py + ["-c", "import sys; sys.path.insert(0, sys.argv[1] + '/src'); from dfl import persist; print(persist.extract_code(sys.argv[2], sys.argv[3]))",
                    wp("code-active"), wp("restored-original", "league.db"), wp("code-original")], work)
    ok = ex.returncode == 0 and ex.stdout.strip() == m["original"]["fingerprint"]
    if not step("the originating code is extracted from the original file and reproduces its fingerprint", ok, tail(ex) if not ok else ex.stdout.strip()[:16]):
        return {"ok": False, "steps": steps}
    oh = m["original"]["head"]
    r = _run(py + ["-c", _STATIC_ORIGINAL, wp("code-active"), wp("restored-original", "league.db"), wp("restored-active", "archive.db")], work)
    st = _last_json(r.stdout) or {}
    a = st.get("archive") or {}
    step("the original file is sound: integrity, journal continuity and hash chain, snapshot checksums, recorded code", r.returncode == 0 and not st.get("problems"), st.get("problems") or (tail(r) if r.returncode else None))
    step("the original file holds journal events 1..%d" % oh["seq"], m["original"]["journal_first_seq"] == 1 and m["original"]["journal_rows"] == oh["seq"], {"first": m["original"]["journal_first_seq"], "rows": m["original"]["journal_rows"]})
    step("the NEW archive holds exactly what the original journal committed (the game history is preserved)",
         not (a.get("absent") or a.get("unreadable") or a.get("foreign") or a.get("missing") or a.get("mismatched")) and a.get("base_ok") is True, a)
    r = _run(py + ["-c", _OPEN_ORIGINAL, wp("code-original"), wp("restored-original", "league.db")], work)
    o = _last_json(r.stdout) or {}
    step("the original file opens and recovers under its own code, to its recorded event and chain",
         r.returncode == 0 and o.get("events") == oh["seq"] and o.get("chain") == oh["chain"] and (o.get("digest") in (None, oh.get("digest")) or oh.get("digest") is None), o or tail(r))
    # 4. the two files are one history
    step("the active league continues the original's event and chain", m["link"]["continues_event"] == oh["seq"] and m["link"]["chain"] == oh["chain"], m["link"])
    step("the active file's lineage names this exact original file by SHA-256", m["link"]["active_lineage_source_file_sha256"] == sha256_file(wp("restored-original", "league.db")), None)
    # 5. optionally: recovery from an earlier restore point, which only the original file offers
    if replay_from is not None:
        shutil.copytree(wp("restored-original"), wp("replay"))
        t0 = time.time()
        r = _run(py + ["-c", _REPLAY_FROM, wp("code-original"), wp("replay", "league.db"), str(replay_from)], work)
        p = _last_json(r.stdout) or {}
        ok = r.returncode == 0 and p.get("events") == oh["seq"] and p.get("chain") == oh["chain"] and (o.get("digest") is None or p.get("digest") == o.get("digest"))
        step(f"recovery from restore point {replay_from} replays the journal to event {oh['seq']} and reproduces its chain and state digest", ok,
             {"seconds": round(time.time() - t0, 1), "from": p.get("from"), "snapshots_left": p.get("snapshots_left")} if ok else tail(r))
    return {"ok": all(s["ok"] for s in steps), "steps": steps}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create")
    p.add_argument("set_dir")
    p.add_argument("--league", required=True)
    p.add_argument("--original", required=True)
    p.add_argument("--code-tree", help="the folder (with src/, data/, tools/) that runs the active league; default: the tree this tool is in")
    p = sub.add_parser("verify")
    p.add_argument("set_dir")
    p = sub.add_parser("restore-test")
    p.add_argument("set_dir")
    p.add_argument("work_dir")
    p.add_argument("--replay-from", type=int, default=None)
    p.add_argument("--require-sources-absent", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "create":
        m = create(a.set_dir, a.league, a.original, a.code_tree)
        print(json.dumps({"created": a.set_dir, "components": [(c["file"], c["size"]) for c in m["components"]], "active_head": m["active"]["head"]["seq"],
                          "original_head": m["original"]["head"]["seq"]}))
        return 0
    if a.cmd == "verify":
        res = verify(a.set_dir)
        print(json.dumps({"problems": res["problems"]}))
        return 1 if res["problems"] else 0
    res = restore_test(a.set_dir, a.work_dir, a.replay_from, a.require_sources_absent)
    for s in res["steps"]:
        print(("PASS  " if s["ok"] else "FAIL  ") + s["step"] + ("" if s["detail"] in (None, "none") else "   " + json.dumps(s["detail"], default=str)[:300]))
    print(json.dumps({"ok": res["ok"], "steps": len(res["steps"]), "failed": [s["step"] for s in res["steps"] if not s["ok"]]}))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
