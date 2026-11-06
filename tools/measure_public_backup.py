#!/usr/bin/env python3
"""Resource measurements of tools/public_backup.py on a REAL-SIZE public folder (docs/PUBLIC_BACKUP.md section 7). Separate from the unit tests, which use small synthetic data.

  measure_public_backup.py quiet --public P --work W [--profile public]
      create / verify / restore of an idle folder; the restored tree compared with the source byte for byte and by mode. W must be new (it is the only place written).
  measure_public_backup.py live --root R --work W [--start-after-games 250]
      the real daemon (broadcast_multiseason_check.py --through-end, virtual time) publishes into W/public from the private root R (a COPY, never a production root) while a
      backup of W/public is taken; afterwards every backed-up file that the final tree also has must be byte-identical to it, and the backup's own closure was checked by `create`.

  measure_public_backup.py scale --files 29000,59000,118000,236000,472000 --work W [--file-bytes 2048]
      SYNTHETIC trees of N small files (no references, so the closure is trivially satisfied) to see how time and peak memory grow with the NUMBER of files, which is what
      they depend on. Each size is made, backed up, verified and restored in turn and deleted again. These figures are for the shape of the growth only, not for any real folder.

  measure_public_backup.py closure --games 2000,8000,32000,128000 --work W
      SYNTHETIC public trees whose playback index names N game files (and whose replay library lists the same N): the whole-index documents the reference closure loads whole.
      Shows how the peak memory of `create` and `verify` grows with the size of those documents, separately from the number of files.

Each child is measured on its own: wall time and peak RSS (VmHWM) are those of that one process; the temporary disk is the working index the tool reports (`index_bytes`). Prints one JSON report (also --report FILE). Exit 0 if every check passed.
"""
import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
BK = os.path.join(ROOT, "tools", "public_backup.py")


def run_measured(args):
    """Run a child to the end; return (rc, stdout, wall, cpu, peak_rss_mb). Peak RSS from RUSAGE_CHILDREN deltas is not per-child, so poll /proc instead."""
    t0 = time.time()
    p = subprocess.Popen([PY, "-I"] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    peak = 0
    while p.poll() is None:
        try:
            with open("/proc/%d/status" % p.pid) as f:
                for line in f:
                    if line.startswith("VmHWM:"):
                        peak = max(peak, int(line.split()[1]))
        except OSError:
            pass
        time.sleep(0.2)
    so, se = p.communicate()
    return {"rc": p.returncode, "wall_s": round(time.time() - t0, 1), "peak_rss_mb": round(peak / 1024.0, 1), "stdout": so, "stderr": se[-400:]}


def last_json(s):
    for line in reversed((s or "").strip().splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    return {}


def walk(base):
    out = {}
    for d, _, names in os.walk(base):
        for n in names:
            p = os.path.join(d, n)
            out[os.path.relpath(p, base)] = p
    return out


def same_file(a, b):
    if os.path.getsize(a) != os.path.getsize(b):
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            x, y = fa.read(1 << 20), fb.read(1 << 20)
            if x != y:
                return False
            if not x:
                return True


def du(path):
    n = b = 0
    for d, _, names in os.walk(path):
        for f in names:
            n += 1
            b += os.path.getsize(os.path.join(d, f))
    return n, b


def quiet(a):
    w = os.path.abspath(a.work)
    if os.path.exists(w) and os.listdir(w):
        raise SystemExit("%s must be new" % w)
    os.makedirs(w, exist_ok=True)
    rep = {"mode": "quiet", "source": os.path.abspath(a.public), "profile": a.profile}
    rep["source_files"], rep["source_bytes"] = du(a.public)
    bk, dest = os.path.join(w, "backup"), os.path.join(w, "restored")
    for name, args in (("create", [BK, "create", "--src", a.public, "--out", bk, "--profile", a.profile]),
                       ("verify", [BK, "verify", bk]),
                       ("restore", [BK, "restore", bk, dest])):
        r = run_measured(args)
        j = last_json(r.pop("stdout"))
        rep[name] = dict(r, temp_disk_mb=round(j.get("index_bytes", 0) / 1048576.0, 1), result={k: j.get(k) for k in ("ok", "files", "bytes", "bytes_verified", "quiescent", "passes", "seconds", "n_problems", "closure", "index_bytes")})
        if r["rc"] != 0:
            rep["ok"] = False
            print(json.dumps(rep, sort_keys=True))
            return 1
    src, dst = walk(a.public), walk(dest)
    skip = lambda rel: os.path.basename(rel).startswith(".tmp-")
    s = {k: v for k, v in src.items() if not skip(k)}
    t0 = time.time()
    missing = sorted(set(s) - set(dst))
    extra = sorted(set(dst) - set(s))
    differ = [k for k in s if k in dst and not same_file(s[k], dst[k])]
    modes = [k for k in dst if os.stat(dst[k]).st_mode & 0o777 not in (0o644, 0o755)]
    rep["compare"] = {"files": len(s), "missing": len(missing), "extra": len(extra), "differ": len(differ), "odd_modes": len(modes), "seconds": round(time.time() - t0, 1)}
    rep["backup_files"], rep["backup_bytes"] = du(bk)
    rep["ok"] = not (missing or extra or differ or modes)
    print(json.dumps(rep, sort_keys=True))
    if a.report:
        json.dump(rep, open(a.report, "w"), indent=1, sort_keys=True)
    return 0 if rep["ok"] else 1


def live(a):
    w = os.path.abspath(a.work)
    if os.path.exists(w) and os.listdir(w):
        raise SystemExit("%s must be new" % w)
    os.makedirs(w, exist_ok=True)
    pub, bk = os.path.join(w, "public"), os.path.join(w, "backup")
    rep = {"mode": "live", "root": os.path.abspath(a.root)}
    daemon = subprocess.Popen([PY, os.path.join(ROOT, "tools", "broadcast_multiseason_check.py"), "--root", a.root, "--public", pub, "--state", os.path.join(w, "state"),
                               "--step", "20", "--past", "600", "--through-end", "--report", os.path.join(w, "daemon.json")], stdout=open(os.path.join(w, "daemon.log"), "w"), stderr=subprocess.STDOUT)
    t0 = time.time()
    games = 0
    while daemon.poll() is None and games < a.start_after_games:
        time.sleep(1)
        try:
            games = len(json.load(open(os.path.join(pub, "playback", "index.json")))["games"])
        except (OSError, ValueError, KeyError):
            pass
    rep["games_public_when_the_backup_started"] = games
    rep["seconds_until_start"] = round(time.time() - t0, 1)
    r = run_measured([BK, "create", "--src", pub, "--out", bk])
    j = last_json(r.pop("stdout"))
    rep["create"] = dict(r, result=j, daemon_still_running_when_it_ended=daemon.poll() is None)
    rc = daemon.wait()
    rep["daemon_rc"] = rc
    rep["ok"] = rc == 0 and r["rc"] == 0
    if r["rc"] == 0:
        v = run_measured([BK, "verify", bk])
        rep["verify"] = dict({k: v[k] for k in ("rc", "wall_s", "peak_rss_mb")}, result=last_json(v["stdout"]))
        rep["ok"] = rep["ok"] and v["rc"] == 0
        # nothing in the backup may differ from the same file of the final tree, except the files the daemon replaces (controls and indexes, which only grow)
        files = os.path.join(bk, "files")
        final = walk(pub)
        grown = ("latest.json", "status.json", "playback/index.json", "broadcast/replays/index.json", "broadcast/honors/index.json", "broadcast/clock.json", "broadcast/schedule.json")
        n = differ = absent = 0
        for rel, p in walk(files).items():
            n += 1
            f = final.get(rel)
            if f is None:
                absent += 1
            elif rel not in grown and not rel.startswith("broadcast/live/") and not same_file(p, f):
                differ += 1
        rep["against_final_tree"] = {"backup_files": n, "absent_from_final": absent, "immutable_files_that_differ": differ}
        rep["ok"] = rep["ok"] and differ == 0
    rep["final_public_files"], rep["final_public_bytes"] = du(pub)
    print(json.dumps(rep, sort_keys=True))
    if a.report:
        json.dump(rep, open(a.report, "w"), indent=1, sort_keys=True)
    return 0 if rep["ok"] else 1


def make_tree(base, n, size):
    """n files of `size` bytes under broadcast/live/<30-character name>/sNNNN.json, 800 to a folder: path lengths like the real folder's."""
    per = 800
    body = (b'{"synthetic":"%d"}' % size).ljust(size, b' ')
    for i in range(0, n, per):
        d = os.path.join(base, "broadcast", "live", "S%03d-po-Eastern-conference-s%dv%d" % (i // per % 999, i // per % 7, i // per % 3))
        d = d + "-%d" % (i // per)
        os.makedirs(d, exist_ok=True)
        for j in range(min(per, n - i)):
            with open(os.path.join(d, "s%04d.json" % j), "wb") as f:
                f.write(body)


def scale(a):
    w = os.path.abspath(a.work)
    if os.path.exists(w) and os.listdir(w):
        raise SystemExit("%s must be new" % w)
    os.makedirs(w, exist_ok=True)
    rep = {"mode": "scale", "file_bytes": a.file_bytes, "points": []}
    import shutil
    for n in [int(x) for x in a.files.split(",")]:
        src, bk, dest = os.path.join(w, "src"), os.path.join(w, "backup"), os.path.join(w, "restored")
        t0 = time.time()
        make_tree(src, n, a.file_bytes)
        pt = {"files": n, "make_s": round(time.time() - t0, 1)}
        for name, args in (("create", [BK, "create", "--src", src, "--out", bk, "--no-fsync"]), ("verify", [BK, "verify", bk]), ("restore", [BK, "restore", bk, dest, "--no-fsync"])):
            r = run_measured(args)
            j = last_json(r.pop("stdout"))
            pt[name] = {"wall_s": r["wall_s"], "peak_rss_mb": r["peak_rss_mb"], "temp_disk_mb": round(j.get("index_bytes", 0) / 1048576.0, 1), "ok": bool(j.get("ok")) and r["rc"] == 0}
            if r["rc"]:
                rep["ok"] = False
                print(json.dumps(rep, sort_keys=True))
                return 1
        rep["points"].append(pt)
        for d in (src, bk, dest):
            shutil.rmtree(d, ignore_errors=True)
        print(json.dumps(pt, sort_keys=True), flush=True)
    rep["ok"] = True
    print(json.dumps(rep, sort_keys=True))
    if a.report:
        json.dump(rep, open(a.report, "w"), indent=1, sort_keys=True)
    return 0



def closure(a):
    """create / verify of trees with a playback index and replay library of N games each; peak memory against N."""
    import hashlib
    import shutil
    w = os.path.abspath(a.work)
    if os.path.exists(w) and os.listdir(w):
        raise SystemExit("%s must be new" % w)
    os.makedirs(w, exist_ok=True)
    rep = {"mode": "closure", "points": []}
    for n in [int(x) for x in a.games.split(",")]:
        src, bk = os.path.join(w, "src"), os.path.join(w, "backup")
        os.makedirs(os.path.join(src, "playback", "games"))
        os.makedirs(os.path.join(src, "broadcast", "replays", "games"))
        entries, lib = [], []
        for i in range(n):
            body = ('{"game":%d}' % i).encode().ljust(200, b" ")
            h = hashlib.sha256(body).hexdigest()
            name = "S001-reg-w%02d-g%05d.%s.json" % (i % 20, i, h[:10])
            for d in ("playback", os.path.join("broadcast", "replays")):
                with open(os.path.join(src, d, "games", name), "wb") as f:
                    f.write(body)
            entries.append({"file": "games/" + name, "sha256": h, "bytes": len(body), "gid": "S001/reg:w%02d:g%05d" % (i % 20, i), "home": "Team Name Home", "away": "Team Name Away"})
            lib.append({"gid": "S001/reg:w%02d:g%05d" % (i % 20, i), "replay": "games/" + name})
        json.dump({"games": entries}, open(os.path.join(src, "playback", "index.json"), "w"))
        json.dump({"games": lib}, open(os.path.join(src, "broadcast", "replays", "index.json"), "w"))
        pt = {"games": n, "index_documents_mb": round((os.path.getsize(os.path.join(src, "playback", "index.json")) + os.path.getsize(os.path.join(src, "broadcast", "replays", "index.json"))) / 1048576.0, 2)}
        for name, args in (("create", [BK, "create", "--src", src, "--out", bk, "--no-fsync", "--no-preflight"]), ("verify", [BK, "verify", bk, "--no-preflight"])):
            r = run_measured(args)
            j = last_json(r.pop("stdout"))
            pt[name] = {"wall_s": r["wall_s"], "peak_rss_mb": r["peak_rss_mb"], "ok": bool(j.get("ok")) and r["rc"] == 0}
        rep["points"].append(pt)
        shutil.rmtree(src)
        shutil.rmtree(bk, ignore_errors=True)
        print(json.dumps(pt, sort_keys=True), flush=True)
    rep["ok"] = all(p[k]["ok"] for p in rep["points"] for k in ("create", "verify"))
    print(json.dumps(rep, sort_keys=True))
    if a.report:
        json.dump(rep, open(a.report, "w"), indent=1, sort_keys=True)
    return 0 if rep["ok"] else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("quiet")
    q.add_argument("--public", required=True)
    q.add_argument("--work", required=True)
    q.add_argument("--profile", default="public")
    q.add_argument("--report")
    l = sub.add_parser("live")
    l.add_argument("--root", required=True)
    l.add_argument("--work", required=True)
    l.add_argument("--start-after-games", type=int, default=250)
    l.add_argument("--report")
    sc = sub.add_parser("scale")
    sc.add_argument("--files", default="29000,59000,118000,236000,472000")
    sc.add_argument("--file-bytes", type=int, default=2048)
    sc.add_argument("--work", required=True)
    sc.add_argument("--report")
    cl = sub.add_parser("closure")
    cl.add_argument("--games", default="2000,8000,32000,128000")
    cl.add_argument("--work", required=True)
    cl.add_argument("--report")
    a = ap.parse_args(argv)
    return {"quiet": quiet, "live": live, "scale": scale, "closure": closure}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
