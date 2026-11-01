#!/usr/bin/env python3
"""Where does the league's memory go? Read-only: it reads snapshots out of a league file (or a copy) and loads them in fresh processes.

  python3 -I tools/analyze_memory.py snap LEAGUE_DB SEQ        one fresh process: load the snapshot taken at event SEQ and report what it holds
  python3 -I tools/analyze_memory.py table LEAGUE_DB [SEQ ...] [--events events.jsonl]
                                                               one `snap` per sequence number (default: every checkpoint in the file), joined with
                                                               the runner's own measurements at the same event (events.jsonl)

For each snapshot: compressed and pickled size; this process's resident memory (VmRSS) and its high-water mark (VmHWM) before and after loading and
after a garbage collection; the deep size, in bytes, of every top-level attribute of the Universe and of the second level under the biggest ones
(each shared object is counted once, under the first attribute that reaches it, so the figures add up); object counts by type; and any
functools caches alive in the process (with their current and maximum size), which is the evidence for "no unbounded cache".

Reading the result. A snapshot is what the league IS at that event; a fresh process that has only loaded it holds no garbage and no transient
work. The runner's resident size at the same event (events.jsonl, `vmrss_mb`) minus the fresh load is what a long-lived process adds on top: freed
but not returned memory (fragmentation), cyclic garbage not yet collected, and caches. Its high-water mark (`maxrss_mb`) also contains the
transient cost of pickling, compressing and digesting the state after every event."""
import collections
import functools
import gc
import json
import os
import pickle
import subprocess
import sys
import types
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MB = 1024 * 1024


def proc_status():
    out = {}
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith(("VmRSS", "VmHWM")):
                k, v = line.split(":")
                out[k] = round(int(v.split()[0]) / 1024, 1)
    return out


def deep_size(root, seen):
    """Bytes reachable from `root` that are not already in `seen` (a set of ids). Iterative; modules, classes and functions count as zero."""
    total, stack = 0, [root]
    skip = (type, types.ModuleType, types.FunctionType, types.BuiltinFunctionType, types.MethodType)
    while stack:
        o = stack.pop()
        if id(o) in seen or isinstance(o, skip) or o is None or o is True or o is False:
            continue
        seen.add(id(o))
        total += sys.getsizeof(o, 0)
        if isinstance(o, dict):
            for k, v in o.items():
                stack.append(k)
                stack.append(v)
        elif isinstance(o, (list, tuple, set, frozenset, collections.deque)):
            stack.extend(o)
        elif type(o).__module__ == "numpy" and hasattr(o, "nbytes"):
            if getattr(o, "base", None) is not None:
                stack.append(o.base)
            if getattr(o, "dtype", None) is not None and o.dtype.hasobject:
                stack.extend(o.ravel().tolist())
        else:
            d = getattr(o, "__dict__", None)
            if d is not None:
                stack.append(d)
            for cls in type(o).__mro__:
                for s in getattr(cls, "__slots__", ()) or ():
                    if isinstance(s, str) and hasattr(o, s):
                        stack.append(getattr(o, s))
    return total


def children(o):
    """(name, value) for the first level under an object."""
    d = getattr(o, "__dict__", None)
    if isinstance(o, dict):
        return [(str(k), v) for k, v in o.items()]
    if d is not None:
        return list(d.items())
    out = []
    for cls in type(o).__mro__:
        for s in getattr(cls, "__slots__", ()) or ():
            if isinstance(s, str) and hasattr(o, s):
                out.append((s, getattr(o, s)))
    return out


def snap(league, seq):
    sys.path.insert(0, os.path.join(ROOT, "src"))
    import sqlite3
    from dfl import persist
    before = proc_status()
    db = sqlite3.connect(f"file:{os.path.abspath(league)}?mode=ro", uri=True)
    row = db.execute("SELECT sha256, size, checkpoint, payload FROM snapshots WHERE seq = ?", (seq,)).fetchone()
    db.close()
    if row is None:
        return {"seq": seq, "error": "no snapshot at this event"}
    raw = row[3]
    after_read = proc_status()
    u = persist.loads(raw)
    gc.collect()
    loaded = proc_status()
    pickled = len(zlib.decompress(raw))
    seen = set()
    attrs = []
    for name, v in sorted(children(u), key=lambda kv: kv[0]):
        n = deep_size(v, seen)
        try:
            p = len(pickle.dumps(v, protocol=5))
        except Exception:
            p = None
        attrs.append({"attr": name, "type": type(v).__name__, "deep_bytes": n, "pickle_bytes": p})
    attrs.sort(key=lambda a: -a["deep_bytes"])
    total = sum(a["deep_bytes"] for a in attrs) or 1
    second = []

    def tree(obj, path, depth):
        seen_local, rows = set(), []
        for name, c in children(obj):
            rows.append((name, c, deep_size(c, seen_local)))
        rows.sort(key=lambda r: -r[2])
        for name, c, n in rows[:10]:
            if n < 0.03 * total:
                break
            second.append({"attr": f"{path}.{name}", "type": type(c).__name__, "deep_bytes": n, "len": len(c) if hasattr(c, "__len__") else None})
            if depth < 4:
                tree(c, f"{path}.{name}", depth + 1)
    tree(u, "u", 1)
    objs = gc.get_objects()
    contracts = [o for o in objs if type(o).__name__ == "Contract" and isinstance(getattr(o, "_memo", None), dict)]
    seen_m, seen_c = set(), set()
    memo = {"contracts": len(contracts), "with_memo": sum(1 for c in contracts if c._memo), "entries": sum(len(c._memo) for c in contracts),
            "max_entries_per_contract": max((len(c._memo) for c in contracts), default=0),
            "memo_mb": round(sum(deep_size(c._memo, seen_m) for c in contracts) / MB, 2),
            "rest_of_contracts_mb": round(sum(deep_size(v, seen_c) for c in contracts for n, v in children(c) if n != "_memo") / MB, 2)}
    views = getattr(getattr(u, "world", None), "_views", None)
    views = {"entries": len(views), "mb": round(deep_size(views, set()) / MB, 2)} if isinstance(views, dict) else None
    counts = collections.Counter(type(o).__name__ for o in objs)
    type_bytes = collections.Counter()
    for o in objs:
        type_bytes[type(o).__name__] += sys.getsizeof(o, 0)
    del objs
    pk_with = len(pickle.dumps(u, protocol=5))
    for c in contracts:
        c._memo.clear()
    memo["pickled_mb_with_memo"], memo["pickled_mb_without_memo"] = round(pk_with / MB, 2), round(len(pickle.dumps(u, protocol=5)) / MB, 2)
    caches = []
    for o in gc.get_objects():
        if type(o).__name__ == "_lru_cache_wrapper":
            ci = o.cache_info()
            caches.append({"fn": getattr(o, "__qualname__", "?"), "currsize": ci.currsize, "maxsize": ci.maxsize})
    return {"seq": seq, "checkpoint": row[2], "compressed_mb": round(row[1] / MB, 2), "pickled_mb": round(pickled / MB, 2),
            "rss_start_mb": before["VmRSS"], "rss_after_read_mb": after_read["VmRSS"], "rss_after_load_mb": loaded["VmRSS"], "hwm_after_load_mb": loaded["VmHWM"],
            "deep_total_mb": round(sum(a["deep_bytes"] for a in attrs) / MB, 2), "attrs": attrs[:12], "contract_memo": memo, "world_views_cache": views, "tree": second[:40], "top_types_bytes_mb": [(k, round(v / MB, 1)) for k, v in type_bytes.most_common(8)],
            "top_types": counts.most_common(10), "objects": sum(counts.values()), "lru_caches": caches}


def runner_at(events_path, seq):
    if not events_path or not os.path.exists(events_path):
        return {}
    for line in open(events_path):
        e = json.loads(line)
        if e["seq"] == seq:
            return {"runner_resident_mb": e.get("vmrss_mb"), "runner_peak_mb": e.get("maxrss_mb"), "league_mb": round(e["league_bytes"] / MB, 1),
                    "archive_mb": round(e["archive_bytes"] / MB, 1), "key": e["key"]}
    return {}


def main(argv):
    if len(argv) >= 3 and argv[0] == "snap":
        print(json.dumps(snap(argv[1], int(argv[2])), indent=1, default=str))
        return 0
    if len(argv) >= 2 and argv[0] == "table":
        league = argv[1]
        events = None
        rest = [x for x in argv[2:]]
        if "--events" in rest:
            i = rest.index("--events")
            events = rest[i + 1]
            del rest[i:i + 2]
        seqs = [int(x) for x in rest]
        if not seqs:
            import sqlite3
            db = sqlite3.connect(f"file:{os.path.abspath(league)}?mode=ro", uri=True)
            seqs = [s for (s,) in db.execute("SELECT seq FROM snapshots WHERE checkpoint = 1 ORDER BY seq")]
            db.close()
        rows = []
        for s in seqs:
            p = subprocess.run([sys.executable, "-I", os.path.abspath(__file__), "snap", league, str(s)], capture_output=True, text=True)
            if p.returncode:
                print(f"seq {s}: {p.stderr[-400:]}", file=sys.stderr)
                continue
            r = json.loads(p.stdout)
            r.update(runner_at(events, s))
            rows.append(r)
        print("seq  compressed pickled  fresh-load  fresh-HWM | runner-resident runner-peak | resident/fresh  | deep total | top attributes (MB)")
        for r in rows:
            if "error" in r:
                print(f"{r['seq']:>4} {r['error']}")
                continue
            fresh = r["rss_after_load_mb"] - r["rss_start_mb"]
            rr = r.get("runner_resident_mb")
            top = ", ".join(f"{a['attr']} {a['deep_bytes'] / MB:.1f}" for a in r["attrs"][:5])
            print(f"{r['seq']:>4} {r['compressed_mb']:>9} {r['pickled_mb']:>7} {fresh:>10.1f} {r['hwm_after_load_mb']:>10} | {rr if rr else '-':>15} {r.get('runner_peak_mb') or '-':>11} | "
                  f"{(rr / r['rss_after_load_mb']) if rr else float('nan'):>14.2f} | {r['deep_total_mb']:>10} | {top}")
        print("\nseq  contract-memo: entries  max/contract  MB   | pickled MB with -> without memo | World._views: entries  MB")
        for r in rows:
            if "error" in r:
                continue
            m, v = r["contract_memo"], r["world_views_cache"] or {}
            print(f"{r['seq']:>4}  {m['entries']:>21} {m['max_entries_per_contract']:>13} {m['memo_mb']:>5} | {m['pickled_mb_with_memo']:>10} -> {m['pickled_mb_without_memo']:<10} | {v.get('entries', '-'):>20} {v.get('mb', '-')}")
        print(json.dumps({"detail": rows}, default=str), file=open(os.path.join(os.getcwd(), "analyze_memory_detail.json"), "w"))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
