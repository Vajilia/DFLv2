#!/usr/bin/env python3
"""A once-a-second recorder of one cgroup: the evidence every stage is judged from.

  sampler.py --cgroup DIR --out FILE.jsonl --tree PINNED_TREE [--interval 1] [--seconds N]

Each line: wall time, whether the cgroup exists, the reader's normalised sample (the pinned monitor's own `read_cgroup_memory`), the pinned estimates, the whole of memory.stat,
memory.events, and the pressure files. It keeps going when the cgroup disappears (a unit restart) and picks it up again when it comes back; it never writes to the cgroup.
"""
import argparse
import importlib.util
import json
import os
import signal
import sys
import time


def load_rc(tree):
    """The monitor module of the tree under test (never the harness's own copy)."""
    for p in (os.path.join(tree, "src"), os.path.join(tree, "tools")):
        if p not in sys.path:
            sys.path.insert(0, p)
    spec = importlib.util.spec_from_file_location("rehearsal_check_under_test", os.path.join(tree, "tools", "rehearsal_check.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def keyvals(text):
    d = {}
    for l in (text or "").splitlines():
        p = l.split()
        if len(p) == 2 and p[1].lstrip("-").isdigit():
            d[p[0]] = int(p[1])
    return d


def rd(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def snapshot(rc, cg, now=None):
    t = now if now is not None else time.time()
    if not os.path.isdir(cg):
        return {"t": round(t, 3), "exists": False}
    s = rc.read_cgroup_memory(cg, os.path.join(cg, "memory.pressure"))
    if s is None:
        return {"t": round(t, 3), "exists": True, "readable": False}
    est = rc.memory_estimates(s)
    return {"t": round(t, 3), "exists": True, "readable": True, "sample": s, "reclaimable": est["reclaimable"], "pinned": est["pinned"],
            "stat": keyvals(rd(cg + "/memory.stat")), "events": keyvals(rd(cg + "/memory.events")), "procs": len((rd(cg + "/cgroup.procs") or "").split())}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cgroup", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tree", required=True)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--seconds", type=float, default=0.0)
    a = ap.parse_args(argv)
    rc = load_rc(a.tree)
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    signal.signal(signal.SIGINT, lambda *_: stop.append(1))
    end = time.time() + a.seconds if a.seconds else None
    nxt = time.time()
    with open(a.out, "a") as f:
        while not stop and (end is None or time.time() < end):
            f.write(json.dumps(snapshot(rc, a.cgroup), sort_keys=True) + "\n")
            f.flush()
            nxt += a.interval
            time.sleep(max(0.0, nxt - time.time()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
