#!/usr/bin/env python3
"""Resource measurements of tools/public_backup.py on a REAL-SIZE public folder (docs/PUBLIC_BACKUP.md section 7). Separate from the unit tests, which use small synthetic data.

  measure_public_backup.py quiet --public P --work W [--profile public]
      create / verify / restore of an idle folder; the restored tree compared with the source byte for byte and by mode. W must be new (it is the only place written).
  measure_public_backup.py live --root R --work W [--start-after-games 250] [--stagger 0]
      the real daemon (broadcast_multiseason_check.py --through-end, virtual time) publishes into W/public from the private root R (a COPY, never a production root) while a
      backup of W/public is taken; afterwards every backed-up file that the final tree also has must be byte-identical to it, and the backup's own closure was checked by `create`.

Each child is run with os.wait4 so wall time, CPU time and peak RSS are those of that one process. Prints one JSON report (also --report FILE). Exit 0 if every check passed.
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
        rep[name] = dict(r, result={k: j.get(k) for k in ("ok", "files", "bytes", "bytes_verified", "quiescent", "passes", "seconds", "n_problems", "closure")})
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
    a = ap.parse_args(argv)
    return quiet(a) if a.cmd == "quiet" else live(a)


if __name__ == "__main__":
    sys.exit(main())
