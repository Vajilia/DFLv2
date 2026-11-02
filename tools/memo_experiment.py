#!/usr/bin/env python3
"""Run a small real league for N events under ONE source tree and record, per event, what must not change and what we want to measure.
Used to compare two trees (before / after a cache change) event by event, on scratch copies only: it creates its own league files in --work and
never opens a deployed league. `--spatial 1` plays the games with the 22-player spatial engine (what the league runs); the default 0 uses the older
play-by-play engine, which is much faster and was what the first 141-event comparison used.

  python3 -I tools/memo_experiment.py run  --src TREE --work DIR --out FILE.jsonl --events N [--seed 7] [--sample 10] [--spatial 0|1]
  python3 -I tools/memo_experiment.py diff A.jsonl B.jsonl         exit 1 if any DECISION field differs between the two runs

DECISION fields (must be identical between trees): per event the journal's state digest, hash chain, archive record count and records digest, the
event key, and the digest of the universe's stateful random stream; at the end the archive's content digest and the states of every random.Random
object reachable from the universe (by attribute path). MEASURED fields (compared only as numbers): seconds per event, stored snapshot size (compressed), resident and
peak memory, and, at sampled events, how many contracts hold a release-charge memo and how many entries they hold in all."""
import gc
import hashlib
import json
import os
import random
import sqlite3
import sys
import time


def status():
    out = {}
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith(("VmRSS", "VmHWM")):
                k, v = line.split(":")
                out[k] = round(int(v.split()[0]) / 1024, 1)
    return out


def sha(x):
    return hashlib.sha256(repr(x).encode()).hexdigest()[:16]


def universe_randoms(root):
    """Every random.Random reachable from the universe, by attribute path, with a short hash of its state. (A scan of all objects in the process
    would also count leftover temporaries that the garbage collector has not yet freed, which differ between runs for reasons unrelated to the league.)"""
    found, seen, stack = {}, set(), [("u", root)]
    while stack:
        path, o = stack.pop()
        if id(o) in seen or isinstance(o, (str, bytes, int, float, bool, type(None))):
            continue
        seen.add(id(o))
        if isinstance(o, random.Random):
            found[path] = sha(o.getstate())
        elif isinstance(o, dict):
            stack.extend((f"{path}[{k!r}]", v) for k, v in o.items() if isinstance(k, (str, int, tuple)))
        elif isinstance(o, (list, tuple, set, frozenset)):
            stack.extend((f"{path}[{i}]", v) for i, v in enumerate(o if not isinstance(o, (set, frozenset)) else sorted(o, key=repr)))
        elif hasattr(o, "__dict__"):
            stack.extend((f"{path}.{k}", v) for k, v in vars(o).items())
    return found


def run(src, work, out, events, seed, sample, spatial=False):
    sys.path.insert(0, os.path.join(src, "src"))
    from dfl import archive as A, persist
    from dfl.ledger.contracts import Contract
    tables = os.path.join(src, "data", "season1")
    os.makedirs(work, exist_ok=True)
    path = os.path.join(work, "league.db")
    r = persist.Runner.create(path, tables, seed=seed, snapshot_every=1, spatial=spatial)
    rows = []
    try:
        for n in range(1, events + 1):
            t0 = time.time()
            key = r.step()
            secs = time.time() - t0
            db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                j = db.execute("SELECT seq, key, digest, chain, n_records, records_digest FROM journal ORDER BY seq DESC LIMIT 1").fetchone()
                snap = db.execute("SELECT size FROM snapshots ORDER BY seq DESC LIMIT 1").fetchone()
            finally:
                db.close()
            row = {"seq": j[0], "key": j[1], "digest": j[2], "chain": j[3], "n_records": j[4], "records_digest": j[5],
                   "rng": sha(r.u.rng.getstate()), "seconds": round(secs, 3), "snapshot_bytes": snap[0] if snap else None, **status()}
            if n % sample == 0 or n == events:
                cs = [o for o in gc.get_objects() if type(o) is Contract]
                row["contracts"] = len(cs)
                row["memo_contracts"] = sum(1 for c in cs if c.__dict__.get("_memo"))
                row["memo_entries"] = sum(len(c.__dict__.get("_memo") or ()) for c in cs)
                row["memo_max"] = max((len(c.__dict__.get("_memo") or ()) for c in cs), default=0)
            rows.append(row)
            print(f"event {n}/{events} {key} {secs:.1f}s", file=sys.stderr, flush=True)
        final = {"final": True, "archive_digest": A.digest(path + ".archive"), "random_states": universe_randoms(r.u)}
    finally:
        r.close()
    with open(out, "w") as f:
        for row in rows + [final]:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    print(f"{len(rows)} events ({'spatial 22-player' if spatial else 'non-spatial'} engine) from {src}: last {rows[-1]['key']}, head {rows[-1]['digest'][:12]}, snapshot {rows[-1]['snapshot_bytes']} B, "
          f"rss {rows[-1].get('VmRSS')} MB, peak {rows[-1].get('VmHWM')} MB, {sum(x['seconds'] for x in rows):.1f} s")


DECISION = ("seq", "key", "digest", "chain", "n_records", "records_digest", "rng")


def diff(a, b):
    ra = [json.loads(x) for x in open(a)]
    rb = [json.loads(x) for x in open(b)]
    bad = 0
    if len(ra) != len(rb):
        print(f"different lengths: {len(ra)} vs {len(rb)}")
        bad += 1
    for x, y in zip(ra, rb):
        if x.get("final"):
            for k in ("archive_digest", "random_states"):
                if x.get(k) != y.get(k):
                    print(f"final {k} differs"); bad += 1
            continue
        for k in DECISION:
            if x.get(k) != y.get(k):
                print(f"event {x['seq']}: {k} differs"); bad += 1
    n = len([x for x in ra if not x.get("final")])
    print(f"{n} events compared on {len(DECISION)} decision fields each, plus final archive digest and random states: {'IDENTICAL' if not bad else str(bad) + ' DIFFER'}")
    return 1 if bad else 0


def main(argv):
    if argv and argv[0] == "run":
        o = dict(zip(argv[1::2], argv[2::2]))
        run(os.path.abspath(o["--src"]), os.path.abspath(o["--work"]), o["--out"], int(o["--events"]), int(o.get("--seed", 7)), int(o.get("--sample", 10)), o.get("--spatial", "0") == "1")
        return 0
    if len(argv) == 3 and argv[0] == "diff":
        return diff(argv[1], argv[2])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
