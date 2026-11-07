#!/usr/bin/env python3
"""Measure the three long-running processes together on ONE CPU and a 2 GB memory limit, with the REAL engine (docs/UNATTENDED_OPERATION.md, "Measurements").

  measure_combined.py --source ROOT --work DIR --config baseline|default|tuned [--minutes 6] [--cpu 0] [--mem-gb 2]
                      [--kill-daemon-at MIN --restart-after SECONDS] [--out FILE]

SOURCE is a private root whose first season is completely built (it is only COPIED, about 1 GB; nothing in it is changed). In the copy it starts, as separate processes pinned to
one CPU and, where this host allows it, inside a memory cgroup of --mem-gb (cgroup v1 here; the target is a systemd host, where the units' MemoryMax does the same):

  monitor     tools/rehearsal_check.py watch   (spoiler checks, resources, the guard)
  daemon      tools/broadcast_run.py run       (the broadcast clock and the only writer of the public folder), one JSON line per tick on stdout
  supervisor  tools/broadcast_supervisor.py run (baseline: not started), which runs the REAL builder: next-season for season 2, then one real engine event after another

configs:  baseline = monitor and daemon only;  default = all three, no priorities;  tuned = the supervisor (and so every builder step) at nice 15 and a CPU weight of 20 against
100, the setting the unit template proposes (the CPU weight is emulated here with cgroup v1 cpu.shares 205 against 1024).

What it records, as JSON: per process the peak resident memory (the kernel's own figure for the process AND everything it ran, from wait4); the memory cgroup's high-water mark
(which includes page cache, as MemoryMax does); the daemon's tick spacing (nominally 1.0 s: the time between its output lines; a late tick is a late publication) as percentiles
and the worst gaps; the builder's throughput (events built per minute, and the time between events); CPU seconds used by each process; and, with --kill-daemon-at, what the monitor
and the broadcast did when the daemon was killed with SIGKILL and started again. Nothing here is a test of correctness: the correctness runs are in tests/.
"""
import argparse
import json
import os
import shutil
import signal
import statistics
import subprocess
import sys
import threading
import time

import psutil

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
CG = "/sys/fs/cgroup"


def cg_write(path, value):
    with open(path, "w") as f:
        f.write(str(value))


def cg_read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


class Group:
    """A cgroup v1 memory (and cpu) group for one process tree. Absent on a host without them: the run still records RSS."""

    def __init__(self, name, parent_limit=None, shares=None, limit=None):
        self.name = name
        self.limit = limit
        self.mem = os.path.join(CG, "memory", "dflmeasure", name)
        self.cpu = os.path.join(CG, "cpu", "dflmeasure", name)
        self.ok = False
        try:
            if parent_limit is not None:
                parent = os.path.dirname(self.mem)
                os.makedirs(parent, exist_ok=True)
                cg_write(os.path.join(parent, "memory.use_hierarchy"), 1)
                cg_write(os.path.join(parent, "memory.limit_in_bytes"), parent_limit)
            os.makedirs(self.mem, exist_ok=True)
            os.makedirs(self.cpu, exist_ok=True)
            for d in (self.mem, os.path.dirname(self.mem)):
                cg_write(os.path.join(d, "memory.max_usage_in_bytes"), 0)                   # a group left by an earlier run keeps its high-water mark until reset
            if shares:
                cg_write(os.path.join(self.cpu, "cpu.shares"), shares)
            if limit:
                cg_write(os.path.join(self.mem, "memory.limit_in_bytes"), limit)
            self.ok = True
        except OSError:
            pass

    def enter(self):
        """Called in the child between fork and exec."""
        if self.ok:
            for d in (self.mem, self.cpu):
                try:
                    cg_write(os.path.join(d, "cgroup.procs"), os.getpid())
                except OSError:
                    pass

    def remove(self):
        for d in (self.mem, self.cpu):
            try:
                os.rmdir(d)
            except OSError:
                pass

    def peak(self):
        v = cg_read(os.path.join(self.mem, "memory.max_usage_in_bytes"))
        return int(v) if v and v.isdigit() else None

    def pressure(self):
        """How often the group hit its limit (the kernel then reclaims page cache first) and whether anything was killed."""
        out = {"limit_mb": round(self.limit / 1e6) if self.limit else None, "failcnt": cg_read(os.path.join(self.mem, "memory.failcnt"))}
        oc = (cg_read(os.path.join(self.mem, "memory.oom_control")) or "").replace("\n", "; ")
        out["oom_control"] = oc
        return out

    def cache_and_rss(self):
        out = {}
        s = cg_read(os.path.join(self.mem, "memory.stat")) or ""
        for ln in s.splitlines():
            k, _, v = ln.partition(" ")
            if k in ("rss", "cache", "total_rss", "total_cache", "total_mapped_file"):
                out[k] = int(v)
        return out


class Proc:
    def __init__(self, name, argv, group, cpu, nice=0, stdout=False):
        self.name, self.argv, self.group = name, argv, group
        self.cpu, self.nice = cpu, nice
        self.p = None
        self.ru = None
        self.lines = []
        self.stdout = stdout
        self.t_start = None

    def start(self):
        def pre():
            os.sched_setaffinity(0, {self.cpu})
            if self.nice:
                os.nice(self.nice)
            self.group.enter()
        self.p = subprocess.Popen(self.argv, stdout=subprocess.PIPE if self.stdout else subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True, preexec_fn=pre, start_new_session=True)
        self.t_start = time.time()
        if self.stdout:
            threading.Thread(target=self.read_lines, daemon=True).start()

    def read_lines(self):
        for ln in self.p.stdout:
            self.lines.append(time.time())

    def stop(self, sig=signal.SIGTERM, wait=30):
        """-> rusage of the process and everything it waited for (ru_maxrss is the largest resident set of any of them)."""
        if self.p is None or self.p.poll() is not None:
            return self.ru
        self.p.send_signal(sig)
        ru = None
        deadline = time.time() + wait
        while time.time() < deadline:
            pid, status, ru = os.wait4(self.p.pid, os.WNOHANG)
            if pid:
                self.p.returncode = os.waitstatus_to_exitcode(status)
                self.ru = ru
                return ru
            time.sleep(0.2)
        self.p.kill()
        pid, status, ru = os.wait4(self.p.pid, 0)
        self.p.returncode = os.waitstatus_to_exitcode(status)
        self.ru = ru
        return ru


def tree_rss(pid):
    try:
        p = psutil.Process(pid)
        total, n = 0, 0
        for q in [p] + p.children(recursive=True):
            try:
                total += q.memory_info().rss
                n += 1
            except psutil.Error:
                pass
        return total, n
    except psutil.Error:
        return 0, 0


def tree_cpu(pid):
    try:
        p = psutil.Process(pid)
        t = 0.0
        for q in [p] + p.children(recursive=True):
            try:
                c = q.cpu_times()
                t += c.user + c.system + c.children_user + c.children_system
            except psutil.Error:
                pass
        return t
    except psutil.Error:
        return 0.0


def pct(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def builds_count(root):
    best = 0
    d = os.path.join(root, "store", "seasons")
    if os.path.isdir(d):
        for n in os.listdir(d):
            if len(n) == 4:
                try:
                    with open(os.path.join(d, n, "builds.json")) as f:
                        best = max(best, len(json.load(f)["events"]) + 1000 * int(n[1:]))
                except (OSError, ValueError):
                    pass
    return best


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--config", choices=("baseline", "default", "tuned"), required=True)
    ap.add_argument("--minutes", type=float, default=6.0)
    ap.add_argument("--cpu", type=int, default=0)
    ap.add_argument("--mem-gb", type=float, default=2.0)
    ap.add_argument("--kill-daemon-at", type=float, default=0.0, help="minute at which the daemon is killed with SIGKILL")
    ap.add_argument("--restart-after", type=float, default=120.0, help="seconds later it is started again")
    ap.add_argument("--limit-daemon-mb", type=float, default=0.0, help="hard memory limit of the daemon's group (the unit's MemoryMax)")
    ap.add_argument("--limit-monitor-mb", type=float, default=0.0)
    ap.add_argument("--limit-supervisor-mb", type=float, default=0.0, help="covers the supervisor AND the builder steps it runs")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    work = os.path.abspath(a.work)
    if os.path.exists(work):
        sys.exit("--work must not exist: %s" % work)
    os.makedirs(work)
    root, public, guard = (os.path.join(work, n) for n in ("root", "public", "guard"))
    t0 = time.time()
    shutil.copytree(a.source, root, symlinks=True)
    open(os.path.join(root, "store", "continuous"), "w").close()
    os.makedirs(public)
    os.makedirs(guard)
    copy_s = time.time() - t0
    os.sched_setaffinity(0, {c for c in range(os.cpu_count()) if c != a.cpu} or {a.cpu})        # this sampler stays off the measured CPU
    limit = int(a.mem_gb * 1024 ** 3)
    tuned = a.config == "tuned"
    mb = lambda v: int(v * 1024 * 1024) if v else None      # noqa: E731
    g_mon = Group("monitor", parent_limit=limit, shares=1024, limit=mb(a.limit_monitor_mb))
    g_dae = Group("daemon", shares=1024, limit=mb(a.limit_daemon_mb))
    g_sup = Group("supervisor", shares=205 if tuned else 1024, limit=mb(a.limit_supervisor_mb))
    tools = lambda n: os.path.join(HERE, n)       # noqa: E731
    cap = ["--max-public-gb-per-season", "1.25", "--public-base-gb", "0.25"]
    mon = Proc("monitor", [PY, "-I", tools("rehearsal_check.py"), "watch", "--root", root, "--public", public, "--log", os.path.join(work, "watch.log"), "--evidence", os.path.join(work, "evidence"),
                           "--guard", guard, "--every", "10", "--full-every", "30", "--min-free-gb", "1"] + cap, g_mon, a.cpu)
    dae_argv = [PY, "-I", tools("broadcast_run.py"), "run", "--root", root, "--public", public, "--guard", guard, "--verbose"] + cap
    dae = Proc("daemon", dae_argv, g_dae, a.cpu, stdout=True)
    sup = Proc("supervisor", [PY, "-I", tools("broadcast_supervisor.py"), "run", "--root", root, "--public", public, "--floor-gb", "1", "--step-margin-gb", "0.2", "--season-gb", "0.5",
                              "--inode-floor", "1000", "--season-inodes", "20000", "--interval", "10"] + ["--public-cap-per-season-gb", "1.25", "--public-base-gb", "0.25"],
               g_sup, a.cpu, nice=15 if tuned else 0)
    procs = [mon, dae] + ([] if a.config == "baseline" else [sup])
    samples = []
    built_series = []
    events = []
    stop = threading.Event()
    killed = {}

    def sampler():
        while not stop.wait(1.0):
            now = time.time()
            row = {"t": round(now - t0_run, 1)}
            for p in procs:
                if p.p is not None and p.p.poll() is None:
                    rss, n = tree_rss(p.p.pid)
                    row[p.name] = {"rss_mb": round(rss / 1e6, 1), "procs": n}
            row["built"] = builds_count(root)
            samples.append(row)
            if not built_series or built_series[-1][1] != row["built"]:
                built_series.append((now, row["built"]))

    try:
        t0_run = time.time()
        mon.start()
        time.sleep(3)
        dae.start()
        if a.config != "baseline":
            sup.start()
        th = threading.Thread(target=sampler, daemon=True)
        th.start()
        end = t0_run + a.minutes * 60
        while time.time() < end:
            time.sleep(1)
            el = (time.time() - t0_run) / 60
            if a.kill_daemon_at and "killed" not in killed and el >= a.kill_daemon_at:
                pos = None
                try:
                    with open(os.path.join(root, "store", "clock.json")) as f:
                        pos = json.load(f)
                except (OSError, ValueError):
                    pass
                os.kill(dae.p.pid, signal.SIGKILL)
                os.wait4(dae.p.pid, 0)
                killed["killed"] = time.time()
                killed["clock_at_kill"] = pos
                events.append({"t": round(time.time() - t0_run, 1), "event": "daemon killed (SIGKILL)"})
            if killed.get("killed") and "restarted" not in killed and time.time() - killed["killed"] >= a.restart_after:
                dae.lines.append(None)
                dae.start()
                killed["restarted"] = time.time()
                events.append({"t": round(time.time() - t0_run, 1), "event": "daemon started again"})
        stop.set()
        th.join(timeout=3)
        cpu_s = {p.name: round(tree_cpu(p.p.pid), 1) for p in procs if p.p.poll() is None}
        rus = {}
        for p in reversed(procs):
            ru = p.stop()
            rus[p.name] = round(ru.ru_maxrss / 1024.0, 1) if ru else None                      # kilobytes on Linux -> MB (binary)
        try:
            with open(os.path.join(work, "watch.log")) as f:
                mlog = [json.loads(x) for x in f if x.strip()]
        except OSError:
            mlog = []
        viol = [m for m in mlog if "violation" in m]
        gaps = []
        ticks = [x for x in dae.lines if x is not None]
        for x, y in zip(ticks, ticks[1:]):
            gaps.append(y - x)
        # the gap across the kill/restart is the outage, not a late tick
        if killed.get("killed"):
            gaps = [g for g in gaps if g < a.restart_after - 5]
        built_start = built_series[0][1] if built_series else 0
        steps = [(y[0] - x[0]) for x, y in zip(built_series, built_series[1:]) if y[1] == x[1] + 1]
        minutes = a.minutes
        peaks = {"monitor_cgroup_mb": g_mon.peak(), "daemon_cgroup_mb": g_dae.peak(), "supervisor_cgroup_mb": g_sup.peak()}
        parent = os.path.join(CG, "memory", "dflmeasure")
        peak_all = cg_read(os.path.join(parent, "memory.max_usage_in_bytes"))
        out = {
            "config": a.config, "minutes": a.minutes, "cpu": "1 (pinned to cpu %d of %d)" % (a.cpu, os.cpu_count()), "mem_limit_gb": a.mem_gb, "copy_s": round(copy_s, 1),
            "memory_cgroup": g_mon.ok,
            "peak_rss_mb": rus,
            "peak_rss_sum_mb": round(sum(v for v in rus.values() if v), 1),
            "cgroup_peak_mb": {k: (round(v / 1e6, 1) if v else None) for k, v in peaks.items()},
            "cgroup_parent_peak_mb": round(int(peak_all) / 1e6, 1) if peak_all and peak_all.isdigit() else None,
            "cgroup_limits": {n: g.pressure() for n, g in (("monitor", g_mon), ("daemon", g_dae), ("supervisor", g_sup))},
            "cgroup_cache_and_rss_end": {n: g.cache_and_rss() for n, g in (("monitor", g_mon), ("daemon", g_dae), ("supervisor", g_sup))},
            "cpu_seconds": cpu_s, "cpu_share_pct_of_one_cpu": {k: round(100.0 * v / (a.minutes * 60), 1) for k, v in cpu_s.items()},
            "daemon_ticks": {"count": len(ticks), "gap_s": {"median": round(statistics.median(gaps), 3) if gaps else None, "p99": round(pct(gaps, 0.99), 3) if gaps else None, "p999": round(pct(gaps, 0.999), 3) if gaps else None,
                                                             "max": round(max(gaps), 3) if gaps else None, "over_1.5s": sum(1 for g in gaps if g > 1.5), "over_3s": sum(1 for g in gaps if g > 3.0)}},
            "builder": {"events_built": (built_series[-1][1] - built_start) if built_series else 0, "events_per_minute": round(((built_series[-1][1] - built_start) / minutes), 2) if built_series else 0,
                        "seconds_between_events": {"median": round(statistics.median(steps), 1) if steps else None, "max": round(max(steps), 1) if steps else None}, "count": len(steps)},
            "monitor": {"violations": [{k: v for k, v in m.items() if k in ("violation", "detail", "at")} for m in viol][:10], "violation_count": len(viol),
                        "heartbeats": sum(1 for m in mlog if m.get("heartbeat")), "trips": os.path.exists(os.path.join(guard, "TRIPPED"))},
            "events": events, "killed": {k: (round(v - t0_run, 1) if isinstance(v, float) else v) for k, v in killed.items()},
            "public_gb_end": round(sum(os.path.getsize(os.path.join(d, n)) for d, _, ns in os.walk(public) for n in ns) / 1e9, 3),
            "disk_free_gb_end": round(shutil.disk_usage(work).free / 1e9, 1),
        }
        try:
            with open(os.path.join(root, "supervisor", "health.json")) as f:
                h = json.load(f)
            out["supervisor_health_end"] = {"status": h["status"], "problems": [p["code"] for p in h["problems"]], "builder": h["builder"]["state"], "built": h["builder"]["built"], "season": h["builder"]["season"]}
        except (OSError, ValueError):
            pass
        text = json.dumps(out, indent=1, sort_keys=True)
        print(text)
        if a.out:
            with open(a.out, "w") as f:
                f.write(text + "\n")
        return 0
    finally:
        stop.set()
        for p in procs:
            try:
                if p.p is not None and p.p.poll() is None:
                    p.stop(wait=5)
            except Exception:
                pass
        for g in (g_mon, g_dae, g_sup):
            g.remove()
        for d in (os.path.join(CG, "memory", "dflmeasure"), os.path.join(CG, "cpu", "dflmeasure")):
            try:
                os.rmdir(d)
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
