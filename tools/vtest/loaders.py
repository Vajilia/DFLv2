#!/usr/bin/env python3
"""Controlled memory workloads for the validation stages. Each one records what it itself has done, once a second, so a check can compare the kernel's accounting with a KNOWN input.

  loaders.py dataset --dir D --bytes N                       create N bytes of random files under D, written through and dropped from the cache (run OUTSIDE any unit)
  loaders.py cache   --cgroup DIR --dir D --log F [--seconds S]   read the dataset over and over from inside the cgroup: clean file cache only, a small known anonymous footprint
  loaders.py anon    --cgroup DIR --target-bytes N --rate-bps R --log F [--hold-s S]   allocate and touch anonymous memory at a set rate, then hold

`--cgroup DIR` is a unit's cgroup directory: the loader writes its own pid to DIR/cgroup.procs before it allocates or reads anything, so the kernel charges the unit's memory limit.
`--cgroup none` is for the harness's own tests. The log's `allocated` / `bytes_read` are the ground truth; `rss` is the process's own resident size from /proc.
"""
import argparse
import json
import os
import signal
import sys
import time

PAGE = 4096


def join(cg):
    """Move this process into the cgroup. -> the cgroup path the kernel reports for us afterwards."""
    if cg and cg != "none":
        with open(os.path.join(cg, "cgroup.procs"), "w") as f:
            f.write(str(os.getpid()))
    return (open("/proc/self/cgroup").read().strip().splitlines() or ["?"])[-1]


def rss():
    try:
        return int(open("/proc/self/statm").read().split()[1]) * PAGE
    except OSError:
        return 0


class Log:
    def __init__(self, path):
        self.f = open(path, "a")

    def put(self, **kw):
        kw["t"] = round(time.time(), 3)
        self.f.write(json.dumps(kw, sort_keys=True) + "\n")
        self.f.flush()


def make_dataset(d, total, file_bytes=64 << 20):
    os.makedirs(d, exist_ok=True)
    made, n = 0, 0
    while made < total:
        p = os.path.join(d, "f%05d.bin" % n)
        size = min(file_bytes, total - made)
        if not (os.path.exists(p) and os.path.getsize(p) == size):
            with open(p, "wb") as f:
                left = size
                while left:
                    k = min(left, 1 << 20)
                    f.write(os.urandom(k))
                    left -= k
                f.flush()
                os.fsync(f.fileno())
                os.posix_fadvise(f.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
        made += size
        n += 1
    return n


def run_cache(a):
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    names = sorted(os.path.join(a.dir, n) for n in os.listdir(a.dir) if n.endswith(".bin"))
    if not names:
        print("no dataset files in %s" % a.dir, file=sys.stderr)
        return 2
    log = Log(a.log)
    log.put(event="joined", cgroup=join(a.cgroup), files=len(names), bytes=sum(os.path.getsize(n) for n in names))
    total, passes, last, t0 = 0, 0, 0.0, time.time()
    buf = bytearray(1 << 20)
    while not stop and (not a.seconds or time.time() - t0 < a.seconds):
        for n in names:
            with open(n, "rb", buffering=0) as f:
                while not stop:
                    k = f.readinto(buf)
                    if not k:
                        break
                    total += k
                    if time.time() - last >= 1.0:
                        last = time.time()
                        log.put(event="tick", bytes_read=total, passes=passes, rss=rss())
            if stop or (a.seconds and time.time() - t0 >= a.seconds):
                break
        passes += 1
    log.put(event="end", bytes_read=total, passes=passes, rss=rss())
    return 0


def run_anon(a):
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    log = Log(a.log)
    log.put(event="joined", cgroup=join(a.cgroup), target=a.target_bytes, rate=a.rate_bps)
    chunks, allocated, t0, last = [], 0, time.time(), 0.0
    hold_from = None
    while not stop:
        now = time.time()
        due = min(a.target_bytes, int((now - t0) * a.rate_bps))
        while allocated < due and not stop:
            k = min(a.chunk, a.target_bytes - allocated)
            b = bytearray(k)
            for i in range(0, k, PAGE):
                b[i] = 1                                     # touch every page: an untouched calloc'd page is not memory
            chunks.append(b)
            allocated += k
            if time.time() - last >= 1.0:
                last = time.time()
                log.put(event="tick", allocated=allocated, rss=rss(), phase="ramp")
        if allocated >= a.target_bytes and hold_from is None:
            hold_from = time.time()
            log.put(event="at_target", allocated=allocated, rss=rss())
        if hold_from is not None and a.hold_s and time.time() - hold_from >= a.hold_s:
            break
        if time.time() - last >= 1.0:
            last = time.time()
            log.put(event="tick", allocated=allocated, rss=rss(), phase="hold" if hold_from else "ramp")
        time.sleep(0.05)
    log.put(event="end", allocated=allocated, rss=rss())
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("dataset")
    p.add_argument("--dir", required=True)
    p.add_argument("--bytes", type=int, required=True)
    p = sub.add_parser("cache")
    p.add_argument("--cgroup", required=True)
    p.add_argument("--dir", required=True)
    p.add_argument("--log", required=True)
    p.add_argument("--seconds", type=float, default=0.0)
    p = sub.add_parser("anon")
    p.add_argument("--cgroup", required=True)
    p.add_argument("--target-bytes", type=int, required=True)
    p.add_argument("--rate-bps", type=float, required=True)
    p.add_argument("--hold-s", type=float, default=0.0)
    p.add_argument("--chunk", type=int, default=4 << 20)
    p.add_argument("--log", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "dataset":
        print("%d files" % make_dataset(a.dir, a.bytes))
        return 0
    return {"cache": run_cache, "anon": run_anon}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
