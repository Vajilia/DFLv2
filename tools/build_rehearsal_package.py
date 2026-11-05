#!/usr/bin/env python3
"""Build the broadcast REHEARSAL package: pinned code + a finished private store, one file with a checksum. Read-only on the store; nothing is installed or sent.

  build_rehearsal_package.py --commit SHA --root R --out DIR [--note TEXT] [--force-overrun KEY:SLOT_S ...]

R is a private root made by tools/broadcast_build.py (every event built, never broadcast). The package holds:
  repo/            `git archive SHA` of src, tools, site, deploy, docs and requirements.txt (the commit's own files, not the working tree)
  store.tar.gz     R/store (plan.json, builds.json, feed/, games/, honors/, playback/). No league database, no clock, no PAUSE file, no lock: refused if present
  PACKAGE.json     commit, store facts (events, games, windows, plan length, hashes), default caps
  SHA256SUMS       every file above; the whole package is also one .tar with a .sha256 beside it
--force-overrun KEY:SLOT_S gives a window a shorter NOMINAL slot than its game needs (as tools/broadcast_season_check.py does in virtual time), so a real-time rehearsal
passes through one real overrun. The games are the real ones; only the plan's slot for that window changes, in the package's copy of plan.json (R is not touched).
The league itself (league/ in R) is never included: the daemon does not need it and it is the only place the engine's unfinished state lives.
"""
import argparse
import datetime
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from dflsite import broadcast as B  # noqa: E402
from dflsite import season as S  # noqa: E402
import rehearsal_check as RC  # noqa: E402

CODE_PATHS = ["src", "tools", "site", "deploy", "docs", "requirements.txt"]
FORBIDDEN_IN_STORE = ("clock.json", "clock_log.jsonl", "daemon.lock", S.PAUSE)
DEFAULT_CAPS = {"deadline_hours_after_start": 54, "max_public_gb": 3, "min_free_gb": 6, "memory_max": "600M"}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def git(*args, cwd=ROOT, binary=False):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)
    return r.stdout if binary else r.stdout.decode().strip()


def store_problems(store):
    bad = [n for n in FORBIDDEN_IN_STORE if os.path.exists(os.path.join(store, n))]
    dbs = []
    for d, _, names in os.walk(store):
        dbs += [os.path.join(d, n) for n in names if n.endswith((".db", ".db-wal", ".db-shm", ".sqlite", ".archive"))]
    return bad, dbs


def build(commit, root, out, note="", force=()):
    commit = git("rev-parse", "--verify", commit + "^{commit}")
    store = os.path.join(os.path.abspath(root), "store")
    bad, dbs = store_problems(store)
    if bad:
        raise SystemExit("the store has been broadcast or is running (%s): a rehearsal needs a store that never was" % bad)
    if dbs:
        raise SystemExit("the store holds league database files (%s): refusing" % dbs[:3])
    scratch_public = tempfile.mkdtemp(prefix="rehearsal-preflight-")
    try:
        res = RC.preflight(root, os.path.join(scratch_public, "feed"), min_free_gb=1.0)
    finally:
        shutil.rmtree(scratch_public, ignore_errors=True)
    failed = [w for ok, w in res if not ok]
    if failed:
        raise SystemExit("the store does not pass the pre-flight:\n  " + "\n  ".join(failed))
    plan, builds = B.get(os.path.join(store, S.PLAN)), B.get(os.path.join(store, S.BUILDS))
    forced = {}
    for item in force:
        key, slot = item.split(":")
        forced[key] = int(slot)
    for it in plan["items"]:
        if it["kind"] == "window" and it["key"] in forced:
            it["slot_s"] = forced.pop(it["key"])
            forced[it["key"] + "="] = it["slot_s"]
    if any(not k.endswith("=") for k in forced):
        raise SystemExit("no such window: %s" % [k for k in forced if not k.endswith("=")])
    view = S.materialize(plan, builds)
    overruns = {w["key"]: w["overrun_s"] for w in view.windows if w["key"] + "=" in forced}
    if any(v <= 0 for v in overruns.values()) or len(overruns) != len(forced):
        raise SystemExit("a forced slot does not cause an overrun (the game fits in it): %s" % overruns)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pid = "dfl-rehearsal-%s-%s" % (stamp, commit[:10])
    pkg = os.path.join(os.path.abspath(out), pid)
    os.makedirs(pkg)
    # code: exactly the commit's files
    repo = os.path.join(pkg, "repo")
    os.makedirs(repo)
    tar_bytes = git("archive", "--format=tar", commit, *CODE_PATHS, binary=True)
    with tempfile.NamedTemporaryFile(suffix=".tar") as t:
        t.write(tar_bytes)
        t.flush()
        with tarfile.open(t.name) as tf:
            tf.extractall(repo, filter="data")
    # store: one compressed file, with the top directory `store/`
    with tarfile.open(os.path.join(pkg, "store.tar.gz"), "w:gz", compresslevel=3) as tf:
        tf.add(store, arcname="store", filter=lambda ti: None if ti.name == "store/" + S.PLAN else ti)
        raw = json.dumps(plan, sort_keys=True, separators=(",", ":")).encode() if forced else open(os.path.join(store, S.PLAN), "rb").read()
        ti = tarfile.TarInfo("store/" + S.PLAN)
        ti.size, ti.mtime, ti.mode = len(raw), os.path.getmtime(os.path.join(store, S.PLAN)), 0o644
        tf.addfile(ti, io.BytesIO(raw))
    meta = {
        "id": pid, "format": "dfl-rehearsal-package", "format_version": 1, "commit": commit, "built_at": stamp, "note": note,
        "store": {"events": len(builds["events"]), "games": len(builds["games"]), "windows": len(view.windows), "plan_total_s": plan["total_s"],
                  "league_id": builds.get("league_id"), "honors_parts": sorted(builds.get("honors", {})),
                  "plan_sha256": hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest() if forced else sha256(os.path.join(store, S.PLAN)),
                  "forced_overruns": {k.rstrip("="): {"slot_s": v, "overrun_s": overruns[k.rstrip("=")]} for k, v in forced.items()}, "builds_sha256": sha256(os.path.join(store, S.BUILDS))},
        "caps": DEFAULT_CAPS,
        "contains": ["repo/ (pinned code)", "store.tar.gz (private store)"], "does_not_contain": ["league database", "league archive", "clock", "any production file"],
    }
    with open(os.path.join(pkg, "PACKAGE.json"), "w") as f:
        json.dump(meta, f, indent=1, sort_keys=True)
        f.write("\n")
    lines = []
    for d, _, names in os.walk(pkg):
        for n in sorted(names):
            p = os.path.join(d, n)
            rel = os.path.relpath(p, pkg)
            if rel != "SHA256SUMS":
                lines.append("%s  %s" % (sha256(p), rel))
    with open(os.path.join(pkg, "SHA256SUMS"), "w") as f:
        f.write("\n".join(sorted(lines, key=lambda l: l.split("  ", 1)[1])) + "\n")
    tar_path = pkg + ".tar"
    with tarfile.open(tar_path, "w") as tf:
        tf.add(pkg, arcname=pid)
    with open(tar_path + ".sha256", "w") as f:
        f.write("%s  %s\n" % (sha256(tar_path), os.path.basename(tar_path)))
    shutil.rmtree(pkg)
    return tar_path, meta


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--commit", required=True)
    ap.add_argument("--root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--note", default="")
    ap.add_argument("--force-overrun", action="append", default=[], metavar="KEY:SLOT_S")
    a = ap.parse_args(argv)
    path, meta = build(a.commit, a.root, a.out, a.note, a.force_overrun)
    print(json.dumps({"package": path, "bytes": os.path.getsize(path), "sha256": sha256(path), "commit": meta["commit"], "store": meta["store"]}, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
