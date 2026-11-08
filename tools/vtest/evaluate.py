"""Pure helpers for judging a stage from its recorded samples. No I/O, no clock: everything a check decides can be re-derived from the files in the evidence folder.

A sample record is what sampler.py writes: {"t", "exists", "readable", "sample": {...read_cgroup_memory...}, "pinned", "reclaimable", "stat": {...}, "events": {...}, "procs"}.
"""
import re


def readable(recs):
    return [r for r in recs if r.get("readable")]


def limit(recs):
    for r in readable(recs):
        mx = r["sample"].get("max")
        if mx:
            return mx
    return None


def median(xs):
    xs = sorted(xs)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def crossing_time(recs, frac, key="pinned"):
    """Time of the first sample whose `key` is above frac x the limit, or None."""
    mx = limit(recs)
    if not mx:
        return None
    for r in readable(recs):
        if r[key] > frac * mx:
            return r["t"]
    return None


def seconds_at_or_above(recs, frac, key="current"):
    """Seconds spent with `key` at or above frac x the limit (each sample counts for the time to the next, at most 5 s)."""
    rr = readable(recs)
    mx = limit(rr)
    if not mx:
        return 0.0
    total = 0.0
    for a, b in zip(rr, rr[1:]):
        v = a["sample"]["current"] if key == "current" else a[key]
        if v >= frac * mx:
            total += min(5.0, b["t"] - a["t"])
    return total


def peak(recs, key):
    v = [r[key] if key in ("pinned", "reclaimable") else r["sample"][key] for r in readable(recs)]
    return max(v) if v else None


def event_delta(recs, key):
    """Increase of a memory.events counter across the records (0 if the cgroup was recreated in between: a new cgroup starts from 0, so the sum of increases is used)."""
    total, prev = 0, None
    for r in readable(recs):
        v = (r.get("events") or {}).get(key)
        if v is None:
            continue
        if prev is not None and v >= prev:
            total += v - prev
        elif prev is not None and v < prev:
            total += v                                         # a new cgroup: everything it counted is new
        prev = v
    return total


def stat_delta(recs, key):
    total, prev = 0, None
    for r in readable(recs):
        v = (r.get("stat") or {}).get(key)
        if v is None:
            continue
        if prev is not None:
            total += v - prev if v >= prev else v
        prev = v
    return total


def pressure_peak(recs, kind, field):
    """Largest `some`/`full` avg10/avg60 seen (None if the pressure file was never available)."""
    v = []
    for r in readable(recs):
        p = (r["sample"].get("pressure") or {}).get(kind) or {}
        if field in p and p.get("total") is not None:
            v.append(p[field])
    return max(v) if v else None


def missing_seconds(recs):
    return sum(1 for r in recs if not r.get("exists", True))


def moved(a, b, tol=1.5):
    """True if two positions (seconds) differ by more than tol: the clock advanced."""
    return a is not None and b is not None and abs(b - a) > tol


def summarize_watch(lines):
    """Counts from the monitor's log: violations, heartbeats, and how many heartbeats carry a self-consistent `mem` object."""
    hb = [l for l in lines if l.get("heartbeat")]
    mem = [l["mem"] for l in hb if isinstance(l.get("mem"), dict)]
    ok = [m for m in mem if all(k in m for k in ("current", "anon", "file", "kernel")) and abs(m["current"] - (m["anon"] + m["file"] + m["kernel"])) <= (1 << 20)]
    return {"violations": [l for l in lines if "violation" in l], "heartbeats": len(hb), "mem_lines": len(mem), "mem_consistent": len(ok)}


def loader_summary(recs, now, alive):
    """What a loader says it did: ground truth for the checks."""
    ticks = [r for r in recs if r.get("event") in ("tick", "at_target", "end")]
    out = {"alive": alive, "records": len(recs), "last_age_s": (now - ticks[-1]["t"]) if ticks else None, "rss_peak": max([r.get("rss", 0) for r in ticks] or [0])}
    if ticks:
        for key in ("bytes_read", "allocated"):
            if key in ticks[-1]:
                out[key + "_last"] = ticks[-1][key]
                cut = ticks[-1]["t"] - 60
                old = [r for r in ticks if r["t"] <= cut]
                out[key + "_last_60s"] = ticks[-1][key] - (old[-1][key] if old else 0)
    return out


_KILLED = re.compile(r"Killed process (\d+) \(([^)]*)\)")


def _fields(line):
    return dict(kv.split("=", 1) for kv in line.split("oom-kill:", 1)[1].replace(" ", ",").split(",") if "=" in kv)


def parse_oom_victims(kernel_log, unit_name):
    """Processes the kernel's OOM killer killed in the cgroup of `unit_name`, from kernel-log text -> [{"pid": int, "comm": str, "line": str}].
    A line that names a different cgroup is ignored. A log that names no victim gives []: the victim is then UNKNOWN, which callers must not read as 'the daemon' or 'the load'."""
    out, seen, rejected = [], set(), set()
    for line in (kernel_log or "").splitlines():
        pid = comm = None
        if "oom-kill:" in line:
            f = _fields(line)
            cg = f.get("task_memcg", "") + " " + f.get("oom_memcg", "")
            if cg.strip() and unit_name not in cg:
                if f.get("pid", "").isdigit():
                    rejected.add(int(f["pid"]))
                continue
            if f.get("pid", "").isdigit():
                pid, comm = int(f["pid"]), f.get("task", "")
        else:
            m = _KILLED.search(line)
            if m:
                pid, comm = int(m.group(1)), m.group(2)
        if pid is not None and pid not in seen and pid not in rejected:
            seen.add(pid)
            out.append({"pid": pid, "comm": comm, "line": line.strip()[:300]})
    return out


def classify_victims(victims, daemon_pids, loader_pid):
    """-> {"daemon": [...], "loader": [...], "other": [...]} by pid. daemon_pids: the pids in the daemon's cgroup BEFORE the load was added."""
    d = {"daemon": [], "loader": [], "other": []}
    dp = {int(x) for x in daemon_pids}
    for v in victims or []:
        if v["pid"] == loader_pid:
            d["loader"].append(v)
        elif v["pid"] in dp:
            d["daemon"].append(v)
        else:
            d["other"].append(v)
    return d
