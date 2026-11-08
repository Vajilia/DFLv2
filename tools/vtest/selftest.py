"""Plumbing check on THIS machine with plain processes: no systemd, no cgroup limits, nothing installed. It proves the harness's pieces fit together (the pinned tree's fixture generator, the
loopback site, the daemon and the monitor started with the units' flags, the sampler, the loaders) and nothing about the memory rule. Everything lives under one temporary directory that is
printed and left in place (nothing is deleted).
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

import common as C
import evaluate as E
import sampler as SM


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main(seconds=40):
    tmp = tempfile.mkdtemp(prefix="vtest-selftest-")
    root, web, guard, ev = tmp + "/root", tmp + "/web", tmp + "/guard", tmp + "/evidence"
    public = web + "/feed"
    for d in (root, public, guard, ev):
        os.makedirs(d, exist_ok=True)
    open(web + "/index.html", "w").write("<!doctype html><title>selftest</title>\n")
    tree, port, procs, out = C.REPO, free_port(), [], []

    def check(name, ok, detail):
        out.append(ok)
        print("%s  %s: %s" % ("PASS" if ok else "FAIL", name, detail))

    code = "import sys;sys.path[:0]=[%r,%r];from test_season import Rig;from dflsite import broadcast as B;B.FSYNC=False;r=Rig(%r,slot=7200);print(r.plan['total_s'])" % (tree + "/src", tree + "/tests", root)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    check("fixture", r.returncode == 0 and int(r.stdout.strip()) > 10000, "generated season, plan length %s s" % r.stdout.strip())
    try:
        procs.append(subprocess.Popen([sys.executable, C.HERE + "/site_server.py", "--tree", tree, "--dir", web, "--port", str(port)]))
        env_flags = ["--max-public-gb-per-season", "1.25", "--public-base-gb", "0.25", "--max-public-ceiling-gb", "2"]
        procs.append(subprocess.Popen(["python3", "-I", tree + "/tools/broadcast_run.py", "run", "--root", root, "--public", public, "--guard", guard] + env_flags, stderr=open(tmp + "/daemon.err", "w")))
        time.sleep(2)
        fixture_cg = tree + "/tests/fixtures/cgroup_v2_synthetic"
        procs.append(subprocess.Popen(["python3", "-I", tree + "/tools/rehearsal_check.py", "watch", "--root", root, "--public", public, "--url", "http://127.0.0.1:%d" % port, "--log", guard + "/watch.log",
                                       "--evidence", ev, "--guard", guard, "--cgroup", fixture_cg, "--full-every", "5", "--min-free-gb", "1"] + env_flags, stderr=open(tmp + "/monitor.err", "w")))
        smp = subprocess.Popen([sys.executable, C.HERE + "/sampler.py", "--cgroup", fixture_cg, "--out", tmp + "/sampler.jsonl", "--tree", tree, "--seconds", "5"])
        procs.append(smp)
        subprocess.run([sys.executable, C.HERE + "/loaders.py", "dataset", "--dir", tmp + "/load", "--bytes", str(20 << 20)], capture_output=True)
        subprocess.run([sys.executable, C.HERE + "/loaders.py", "cache", "--cgroup", "none", "--dir", tmp + "/load", "--log", tmp + "/cache.jsonl", "--seconds", "3"], capture_output=True)
        subprocess.run([sys.executable, C.HERE + "/loaders.py", "anon", "--cgroup", "none", "--target-bytes", str(20 << 20), "--rate-bps", str(40 << 20), "--hold-s", "1", "--log", tmp + "/anon.jsonl"], capture_output=True)
        def pos():
            r = subprocess.run(["python3", "-I", tree + "/tools/broadcast_run.py", "status", "--root", root], capture_output=True, text=True)
            try:
                return json.loads(r.stdout).get("position_now")
            except ValueError:
                return None
        p0 = pos()
        time.sleep(seconds)
        p1 = pos()
        check("daemon clock advances", p0 is not None and p1 is not None and p1 - p0 > seconds * 0.5, "position %s -> %s over ~%ds" % (p0, p1, seconds))
        age = time.time() - os.path.getmtime(guard + "/heartbeat") if os.path.exists(guard + "/heartbeat") else None
        check("monitor heartbeat fresh", age is not None and age < 15, "heartbeat age %s s" % (round(age, 1) if age is not None else None))
        lines = C.jsonl_read(guard + "/watch.log")
        sw = E.summarize_watch(lines)
        check("no violations, no latch", not sw["violations"] and not os.path.exists(guard + "/TRIPPED"), "%d violation lines %s" % (len(sw["violations"]), [v.get("violation") for v in sw["violations"]][:3]))
        check("heartbeats carry the memory breakdown", sw["heartbeats"] > 0 and sw["mem_lines"] > 0 and sw["mem_consistent"] == sw["mem_lines"], "%d heartbeats, %d with mem, %d consistent" % (sw["heartbeats"], sw["mem_lines"], sw["mem_consistent"]))
        recs = C.jsonl_read(tmp + "/sampler.jsonl")
        check("sampler reads v2-shaped files", len(E.readable(recs)) >= 3 and E.limit(recs) == 640000000, "%d samples, limit %s" % (len(recs), E.limit(recs)))
        cache, anon = C.jsonl_read(tmp + "/cache.jsonl"), C.jsonl_read(tmp + "/anon.jsonl")
        ls = E.loader_summary(cache, time.time(), False)
        check("cache loader logs its ground truth", ls.get("bytes_read_last", 0) > 20 << 20, "read %s bytes" % ls.get("bytes_read_last"))
        al = E.loader_summary(anon, time.time(), False)
        check("anon loader logs its ground truth", al.get("allocated_last") == 20 << 20 and al["rss_peak"] >= 20 << 20, "allocated %s, rss peak %s" % (al.get("allocated_last"), al["rss_peak"]))
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
    print("\nselftest directory (left in place): %s" % tmp)
    print("SELFTEST %s (plumbing only: no systemd, no cgroup limits, nothing about the memory rule)" % ("OK" if all(out) else "FAILED"))
    return 0 if all(out) else 1


if __name__ == "__main__":
    sys.exit(main())
