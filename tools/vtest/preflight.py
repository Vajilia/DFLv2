"""Read-only go/no-go for an existing disposable environment. Changes nothing, creates nothing.

  vtest.py preflight [--json]

Every check is `pass`, `fail` or `warn`; the environment is READY only if no check failed. A `warn` is a stage that will report `not_run` for one check (named in the message).
Minimum resources, stated once (MIN): 1 vCPU, 1 GiB RAM with no swap, 5 GiB free disk where /srv lives. The target's size class (2 vCPU, 2 GiB) is recommended, not required.
"""
import re

from common import BASE, CGROOT, MARKER, SERVICE_USER, SLICE, PREFIX, UNIT_DIR

MIN = {"cpus": 1, "mem_gib": 1.0, "free_disk_gib": 5.0, "systemd": 243, "kernel": (5, 4)}
# Names that exist only on the machines the harness must never run on. Their presence is a hard refusal.
PRODUCTION_MARKERS = ("/srv/dflv2", "/srv/dflv2-rehearsal", "/etc/dflv2", "/var/www/dfl", "/opt/dflv2-src")
PRODUCTION_UNITS = ("dfl-broadcast-daemon.service", "dfl-broadcast-monitor.service", "dfl-supervisor.service", "dfl-supervisor-check.timer", "dfl-rehearsal.service",
                    "dfl-rehearsal-monitor.service", "dflv2-league.service", "dfl-feed-export.timer", "dfl-playback-export.timer")


def _ver(txt):
    m = re.match(r"(\d+)\.(\d+)", txt or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def preflight(env, base=BASE):
    """-> list of (id, status, detail)."""
    out = []

    def add(cid, ok, detail, warn=False):
        out.append((cid, "pass" if ok else ("warn" if warn else "fail"), detail))

    add("root", env.euid() == 0, "running as uid %s (the harness writes unit files and cgroup control files)" % env.euid())
    mk = env.exists(MARKER)
    add("disposable_marker", mk, "%s %s (an operator creates this file to declare the machine disposable; the harness never creates it)" % (MARKER, "exists" if mk else "is missing"))
    found = [p for p in PRODUCTION_MARKERS if env.exists(p)]
    rc, ufiles, _ = env.sc("list-unit-files", "--no-legend", "--no-pager")
    present = [u for u in PRODUCTION_UNITS if re.search(r"^%s\s" % re.escape(u), ufiles or "", re.M) or any(env.exists(d + "/" + u) for d in (UNIT_DIR, "/usr/lib/systemd/system", "/lib/systemd/system"))]
    add("not_production", not found and not present, "no production or rehearsal paths or units present" if not (found or present) else "REFUSING: this looks like a real DFL host: %s" % ", ".join(found + present))
    pid1 = (env.read("/proc/1/comm") or "").strip()
    add("systemd_pid1", pid1 == "systemd" and env.exists("/run/systemd/system"), "PID 1 is %r%s" % (pid1, "" if env.exists("/run/systemd/system") else ", /run/systemd/system is missing"))
    rc, state, _ = env.sc("is-system-running")
    add("systemd_state", (state or "").strip() in ("running", "degraded"), "systemctl is-system-running: %r" % (state or "").strip())
    rc, vout, _ = env.sc("--version")
    m = re.search(r"systemd (\d+)", vout or "")
    sv = int(m.group(1)) if m else 0
    add("systemd_version", sv >= MIN["systemd"], "systemd %s (OOMPolicy= needs %d or newer)" % (sv or "unknown", MIN["systemd"]))
    mounts = env.read("/proc/mounts") or ""
    cg2 = [f for f in (l.split() for l in mounts.splitlines()) if len(f) > 3 and f[1] == CGROOT and f[2] == "cgroup2"]
    add("cgroup_v2", bool(cg2) and env.exists(CGROOT + "/cgroup.controllers"), "unified cgroup v2 at %s" % CGROOT if cg2 else "%s is not cgroup2 (v1 or hybrid)" % CGROOT)
    rw = bool(cg2) and "rw" in cg2[0][3].split(",")
    add("cgroup_writable", rw, "cgroup2 mounted %s" % ("read-write" if rw else "read-only or absent (a container without cgroup delegation cannot run this)"))
    ctl = (env.read(CGROOT + "/cgroup.controllers") or "").split()
    sub = (env.read(CGROOT + "/cgroup.subtree_control") or "").split()
    add("memory_controller", "memory" in ctl and "memory" in sub, "memory controller available: %s, enabled for children: %s" % ("memory" in ctl, "memory" in sub))
    add("slice_memory", "memory" in (env.read(SLICE + "/cgroup.controllers") or "").split() or not env.exists(SLICE), "system.slice has the memory controller" if env.exists(SLICE) else "system.slice not created yet (systemd creates it)", warn=not env.exists(SLICE))
    swaps = [l for l in (env.read("/proc/swaps") or "").splitlines()[1:] if l.strip()]
    add("swap_off", not swaps, "no swap" if not swaps else "swap is on (%d devices): the rule assumes anonymous memory cannot be swapped out" % len(swaps))
    kv = _ver(env.read("/proc/sys/kernel/osrelease"))
    add("kernel", bool(kv) and kv >= MIN["kernel"], "kernel %s" % (env.read("/proc/sys/kernel/osrelease") or "unknown").strip())
    add("psi", env.exists(CGROOT + "/memory.pressure") or env.exists("/proc/pressure/memory"), "memory pressure files present" if env.exists("/proc/pressure/memory") else "no pressure files: pressure checks will be `not_run`", warn=True)
    add("memory_reclaim", bool(kv) and kv >= (5, 19), "kernel %s has memory.reclaim (V1 reclaim probes)" % ("new enough" if kv and kv >= (5, 19) else "too old for"), warn=True)
    info = env.read("/proc/meminfo") or ""
    m = re.search(r"MemTotal:\s+(\d+) kB", info)
    gib = int(m.group(1)) / 1048576 if m else 0
    add("ram", gib >= MIN["mem_gib"] * 0.9, "%.2f GiB RAM (minimum %.0f GiB)" % (gib, MIN["mem_gib"]))
    m = re.findall(r"^processor\s*:", env.read("/proc/cpuinfo") or "", re.M)
    add("cpus", len(m) >= MIN["cpus"], "%d CPU(s) (minimum %d)" % (len(m), MIN["cpus"]))
    probe = base if env.exists(base) else "/srv" if env.exists("/srv") else "/"
    free = env.free_bytes(probe) / 2**30
    add("disk", free >= MIN["free_disk_gib"], "%.1f GiB free on the filesystem of %s (minimum %.0f)" % (free, probe, MIN["free_disk_gib"]))
    rc, py, _ = env.run(["/usr/bin/python3", "-c", "import sys;print(sys.version_info[:2] >= (3,8))"])
    add("python3", rc == 0 and py.strip() == "True", "/usr/bin/python3 (the units' interpreter) is %s" % ("3.8 or newer" if py.strip() == "True" else "missing or older than 3.8"))
    for tool, why, hard in (("setpriv", "the documented commands and the probe run as the service user", True), ("strace", "V0's durability probe of the start gate and stop hook (install it before the run)", True),
                            ("chattr", "V4's immutable-guard-folder scenario (invalid, not a pass, without it)", False), ("lsattr", "V4's immutable-guard-folder scenario", False)):
        add("tool_" + tool, bool(env.which(tool)), "%s %s: %s" % (tool, "found" if env.which(tool) else "NOT found", why), warn=not hard)
    add("service_user", env.user_exists(SERVICE_USER), "the dedicated service user %r %s (the operator creates it; the harness never does)" % (SERVICE_USER, "exists" if env.user_exists(SERVICE_USER) else "does not exist"))
    rc, lu, _ = env.sc("list-units", "--all", "--no-legend", "--no-pager", PREFIX + "*")
    leftovers = [l.split()[0] for l in (lu or "").splitlines() if l.strip()]
    add("no_leftover_vtest_units", True, "dfl-vtest units already present: %s (a run continues or `teardown` clears them)" % (", ".join(leftovers) or "none"))
    return out


def ready(rows):
    return not any(s == "fail" for _, s, _ in rows)


def report(rows):
    w = max(len(c) for c, _, _ in rows)
    lines = ["%-5s %-*s  %s" % (s.upper(), w, c, d) for c, s, d in rows]
    lines.append("READY" if ready(rows) else "NOT READY: %s" % ", ".join(c for c, s, _ in rows if s == "fail"))
    return "\n".join(lines)
