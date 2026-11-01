#!/usr/bin/env python3
"""A read-only fingerprint of the league, its archive and what is published, to prove that nothing changed across a backup, a restore test, an
analysis or a controlled restart.

  python3 -I tools/cap_state.py fingerprint --league LEAGUE_DB --feed FEED_DIR [--ctl CTL_DIR] [--recompute] [--out FILE]
  python3 -I tools/cap_state.py diff BEFORE.json AFTER.json         exit 1 if any STABLE field differs

STABLE fields are the facts that must not move: the journal's head (event number, key, state digest, hash chain) and length, the league
and archive ids, the archive's record counts and its content digest (ids and body hashes of every game, trace and record), the published
release name, manifest hash, content hash and event count, the file list of the release with each file's SHA-256, and the runner log's
last event. INFORMATIONAL fields (times, process ids, file sizes, snapshot rows, the runner's status) are reported and compared but a
difference is only noted. `--recompute` loads the newest snapshot, replays nothing, and checks that its state digest equals the journal's
digest at the same event (so the stored state is what the journal committed)."""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))


def ro(path):
    return sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True)


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def file_sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def fingerprint(league, feed, ctl=None, recompute=False):
    from dfl import archive as A, persist
    out = {"stable": {}, "info": {}}
    st, info = out["stable"], out["info"]
    db = ro(league)
    try:
        meta = persist.read_meta(db)
        head = db.execute("SELECT seq, key, digest, chain, n_records, records_digest FROM journal ORDER BY seq DESC LIMIT 1").fetchone()
        n = db.execute("SELECT COUNT(*) FROM journal").fetchone()[0]
        snaps = db.execute("SELECT seq, checkpoint, sha256, size FROM snapshots ORDER BY seq").fetchall()
        st["league_id"] = meta.get("league_id")
        st["journal"] = {"events": n, "head_seq": head[0], "head_key": head[1], "head_digest": head[2], "head_chain": head[3]}
        info["snapshots"] = [{"seq": s, "checkpoint": c, "sha256": h[:12], "size": z} for s, c, h, z in snaps]
        newest = db.execute("SELECT seq, payload FROM snapshots ORDER BY seq DESC LIMIT 1").fetchone() if recompute else None
    finally:
        db.close()
    if recompute and newest:
        u = persist.loads(newest[1])
        info["recompute"] = {"snapshot_seq": newest[0], "state_digest_equals_journal_head": (newest[0] == head[0] and persist.journal_digest(u) == head[2])
                             if newest[0] == head[0] else f"newest snapshot is event {newest[0]}, journal head {head[0]}; not comparable"}
    apath = persist._resolve_archive(league, meta["archive"]["file"]) if meta.get("archive") else None
    if apath and os.path.exists(apath):
        adb = ro(apath)
        try:
            one = lambda q: adb.execute(q).fetchone()[0]
            st["archive"] = {"games": one("SELECT COUNT(*) FROM games"), "traces": one("SELECT COUNT(*) FROM traces"), "records": one("SELECT COUNT(*) FROM records"),
                             "batches": one("SELECT COUNT(*) FROM batches"), "last_batch": one("SELECT MAX(seq) FROM batches")}
        finally:
            adb.close()
        st["archive"]["content_digest"] = A.digest(apath)
        info["archive_bytes"] = os.path.getsize(apath)
    info["league_bytes"] = os.path.getsize(league)
    latest = read_json(os.path.join(feed, "latest.json"))
    if latest:
        st["published"] = {k: latest.get(k) for k in ("release", "manifest_sha256", "content_hash") if k in latest}
        rel = latest.get("release") or latest.get("path")
        man = read_json(os.path.join(feed, "releases", str(rel).split("/")[-1], "manifest.json")) if rel else None
        if man:
            st["published"]["content_hash"] = man.get("content_hash")
            st["published"]["events_done"] = man.get("events_done")
            st["published"]["files"] = {k: v.get("sha256") if isinstance(v, dict) else v for k, v in sorted(man.get("files", {}).items())}
            st["published"]["manifest_file_sha256"] = file_sha(os.path.join(feed, "releases", str(rel).split("/")[-1], "manifest.json"))
    status = read_json(os.path.join(feed, "status.json"))
    if status:
        info["feed_status"] = {k: status.get(k) for k in ("published_events", "league_events", "export_ok", "updated_at", "runner") if k in status}
    if ctl:
        rs = read_json(os.path.join(ctl, "runner_status.json")) or {}
        info["runner_status"] = {k: rs.get(k) for k in ("state", "seq", "limit_reached", "until_events", "last_event", "last_event_at", "updated_at", "pid", "error")}
        info["paused_marker"] = os.path.exists(os.path.join(ctl, "PAUSED"))
        ev = os.path.join(ctl, "events.jsonl")
        if os.path.exists(ev):
            lines = open(ev).read().splitlines()
            st["runner_log"] = {"lines": len(lines), "last_seq": json.loads(lines[-1])["seq"] if lines else None}
    try:
        sh = subprocess.run(["systemctl", "show", "dflv2-league.service", "-p", "ActiveState,SubState,MainPID,NRestarts,ExecMainStatus,ExecMainExitTimestamp"],
                            capture_output=True, text=True, timeout=10).stdout
        info["service"] = dict(l.split("=", 1) for l in sh.splitlines() if "=" in l)
    except Exception:
        pass
    return out


def flat(d, p=""):
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(flat(v, f"{p}{k}."))
        else:
            out[f"{p}{k}"] = v
    return out


def main(argv):
    if argv and argv[0] == "fingerprint":
        opt = {argv[i]: argv[i + 1] for i in range(1, len(argv) - 1, 2) if argv[i].startswith("--") and argv[i] not in ("--recompute",)}
        fp = fingerprint(opt["--league"], opt["--feed"], opt.get("--ctl"), "--recompute" in argv)
        text = json.dumps(fp, indent=1, sort_keys=True)
        if "--out" in opt:
            with open(opt["--out"], "w") as f:
                f.write(text + "\n")
        print(text if "--out" not in opt else f"wrote {opt['--out']}: head {fp['stable']['journal']['head_seq']}, release {fp['stable'].get('published', {}).get('release')}")
        return 0
    if len(argv) == 3 and argv[0] == "diff":
        a, b = (json.load(open(p)) for p in argv[1:])
        fa, fb = flat(a["stable"]), flat(b["stable"])
        bad = sorted(k for k in set(fa) | set(fb) if fa.get(k) != fb.get(k))
        for k in bad:
            print(f"STABLE field differs: {k}: {fa.get(k)!r} -> {fb.get(k)!r}")
        ia, ib = flat(a["info"]), flat(b["info"])
        note = sorted(k for k in set(ia) | set(ib) if ia.get(k) != ib.get(k))
        for k in note:
            print(f"info differs: {k}: {ia.get(k)!r} -> {ib.get(k)!r}")
        print(f"{len(fa)} stable fields compared: {'IDENTICAL' if not bad else str(len(bad)) + ' DIFFER'}; {len(note)} informational difference(s)")
        return 1 if bad else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
