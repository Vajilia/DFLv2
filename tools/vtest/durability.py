"""Does the run marker reach the disk in the right order? A probe of the REAL start gate and stop hook, as the service user, under strace, in a scratch folder (never over the live guard folder).

What this shows: the order of the program's own system calls: the marker is written to a temporary file, the file is flushed (fsync), renamed into place, and the FOLDER is flushed (fsync) before the gate
returns, so that the daemon starts only over a marker that was asked to be durable; the hook removes the marker only after the incident's latch was flushed (or, for a stop that was not an OOM, after
systemd said so), and flushes the folder after the removal; and a stop that could not be accounted for leaves the marker alone.
What it does NOT show: that the filesystem, the virtual disk or the host really made the data durable when asked. That cannot be tested on a machine that keeps running; the hard-reset phase of V4 shows
only that the marker is still there after the power was cut, which is weak evidence (a hypervisor's cache usually survives a guest's power cut).
Pure parsing and judging here; `run_scenario` is the one function that executes anything.
"""
import json
import os
import re
import shlex

TRACE = "openat,open,write,fsync,fdatasync,rename,renameat,renameat2,link,linkat,unlink,unlinkat"
LINE = re.compile(r'^(?:\[pid\s+\d+\]\s+|\d+\s+)?(\w+)\((.*)\)\s+=\s+(-?\d+|\?)(?:<([^>]*)>)?(?:\s+(E[A-Z0-9]+))?')
QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')
FDPATH = re.compile(r'\d+<([^>]+)>')
MARKER, LATCH = "RUN_OPEN", "TRIPPED"


def parse_strace(text):
    """-> list of events {"i", "op", "paths", "ok", "errno", "ret"}. Only the calls named in TRACE. Quoted paths are taken as written (the probe uses plain ASCII paths)."""
    events = []
    for line in (text or "").splitlines():
        m = LINE.match(line.strip())
        if not m:
            continue
        name, args, ret, retpath, errno = m.groups()
        if name not in TRACE.split(","):
            continue
        ok = ret not in ("-1", "?") and not errno
        q = QUOTED.findall(args)
        if name in ("write", "fsync", "fdatasync"):
            f = FDPATH.search(args)
            paths = [f.group(1)] if f else []
            op = "fsync" if name in ("fsync", "fdatasync") else "write"
        elif name in ("openat", "open"):
            paths, op = q[:1], "open"
            if ok and retpath and not paths:
                paths = [retpath]
        elif name in ("rename", "renameat", "renameat2"):
            paths, op = q[:2], "rename"
        elif name in ("link", "linkat"):
            paths, op = q[:2], "link"
        else:
            paths, op = q[:1], "unlink"
        events.append({"i": len(events), "op": op, "paths": paths, "ok": ok, "errno": errno, "ret": ret, "line": line.strip()[:160]})
    return events


def _find(events, pred, start=0):
    for e in events[start:]:
        if e["ok"] and pred(e):
            return e["i"]
    return None


def _norm(p):
    return p.rstrip("/")


def judge_marker_written(events, guard):
    """The marker came into being whole and durable: write to a temporary file < fsync that file < rename onto RUN_OPEN < fsync the guard folder. -> problems."""
    g = _norm(guard)
    final = "%s/%s" % (g, MARKER)
    r = _find(events, lambda e: e["op"] == "rename" and len(e["paths"]) == 2 and _norm(e["paths"][1]) == final)
    if r is None:
        return ["no successful rename onto %s" % final]
    tmp = events[r]["paths"][0]
    w = _find(events[:r], lambda e: e["op"] == "write" and e["paths"] == [tmp])
    if w is None:
        return ["the temporary file %s was not written before the rename" % tmp]
    f = _find(events[:r], lambda e: e["op"] == "fsync" and e["paths"] == [tmp], w)
    p = []
    if f is None:
        p.append("the temporary file was renamed into place without an fsync of its contents")
    d = _find(events, lambda e: e["op"] == "fsync" and len(e["paths"]) == 1 and _norm(e["paths"][0]) == g, r + 1)
    if d is None:
        p.append("the guard folder was not fsynced after the rename: the marker's NAME is not durable")
    return p


def judge_marker_removed(events, guard):
    """Removal is durable: unlink RUN_OPEN < fsync the guard folder."""
    g = _norm(guard)
    final = "%s/%s" % (g, MARKER)
    u = _find(events, lambda e: e["op"] == "unlink" and e["paths"] and _norm(e["paths"][0]) == final)
    if u is None:
        return ["the marker was not removed"]
    d = _find(events, lambda e: e["op"] == "fsync" and len(e["paths"]) == 1 and _norm(e["paths"][0]) == g, u + 1)
    return [] if d is not None else ["the guard folder was not fsynced after the marker was removed"]


def judge_removed_after_record(events, guard):
    """For an OOM: the latch is linked into place and the folder flushed BEFORE the marker is removed (the incident is durable before the stop counts as accounted for)."""
    g = _norm(guard)
    l = _find(events, lambda e: e["op"] == "link" and len(e["paths"]) == 2 and _norm(e["paths"][1]) == "%s/%s" % (g, LATCH))
    if l is None:
        return ["the latch was not linked into place"]
    d = _find(events, lambda e: e["op"] == "fsync" and len(e["paths"]) == 1 and _norm(e["paths"][0]) == g, l + 1)
    if d is None:
        return ["the guard folder was not fsynced after the latch was linked"]
    u = _find(events, lambda e: e["op"] == "unlink" and e["paths"] and _norm(e["paths"][0]) == "%s/%s" % (g, MARKER))
    if u is None:
        return ["the marker was not removed after the OOM was recorded"]
    if u < d:
        return ["the marker was removed (event %d) before the latch was durable (fsync, event %d)" % (u, d)]
    return judge_marker_removed(events, guard)


def judge_replaced_after_latch(events, guard):
    """The gate that finds a marker still open latches FIRST (link, fsync of the folder) and only then writes the new marker over the old one."""
    g = _norm(guard)
    l = _find(events, lambda e: e["op"] == "link" and len(e["paths"]) == 2 and _norm(e["paths"][1]) == "%s/%s" % (g, LATCH))
    if l is None:
        return ["the unaccounted stop was not latched"]
    d = _find(events, lambda e: e["op"] == "fsync" and len(e["paths"]) == 1 and _norm(e["paths"][0]) == g, l + 1)
    if d is None:
        return ["the guard folder was not fsynced after the latch was linked"]
    r = _find(events, lambda e: e["op"] == "rename" and len(e["paths"]) == 2 and _norm(e["paths"][1]) == "%s/%s" % (g, MARKER))
    if r is None:
        return ["no new marker was written"]
    if r < d:
        return ["the old marker was replaced (event %d) before the latch was durable (event %d)" % (r, d)]
    return judge_marker_written(events, guard)


def judge_marker_untouched(events, guard):
    """A stop that was not accounted for must not remove or replace the marker."""
    g = _norm(guard)
    final = "%s/%s" % (g, MARKER)
    bad = [e for e in events if e["ok"] and ((e["op"] == "unlink" and e["paths"] and _norm(e["paths"][0]) == final) or (e["op"] == "rename" and len(e["paths"]) == 2 and _norm(e["paths"][1]) == final))]
    return ["the marker was removed or replaced: %s" % bad[0]["line"]] if bad else []


# ---------------------------------------------------------------------------------------------------------------------------------- the commands, taken from the unit file
def probe_command(unit_text, key, scratch, program_map=None):
    """The unit's own ExecStartPre= (key 'ExecStartPre') or ExecStopPost= line as an argv, with the folders pointed at a scratch layout: --root, --public, --guard, --cgroup. Systemd's prefix characters are
    dropped. `program_map`: {prefix: replacement} for the script path (a test checkout instead of the extracted tree)."""
    line = [l for l in (unit_text or "").splitlines() if l.startswith(key + "=")]
    if not line:
        raise ValueError("the unit has no %s=" % key)
    argv = shlex.split(line[0].split("=", 1)[1].lstrip("-+!@:"))
    new = {"--root": scratch["root"], "--public": scratch["public"], "--guard": scratch["guard"], "--cgroup": scratch["cgroup"]}
    out, i = [], 0
    while i < len(argv):
        a = argv[i]
        out.append(a)
        if a in new and i + 1 < len(argv):
            out.append(new[a])
            i += 1
        i += 1
    for k, v in (program_map or {}).items():
        out = [x.replace(k, v) if x.startswith(k) else x for x in out]
    return out


SCENARIOS = [
    # name, which hook, SERVICE_RESULT, oom_kill counter, marker open beforehand, guard writable, judge, expected exit
    ("gate_first_start", "ExecStartPre", None, 0, False, True, judge_marker_written, 0),
    ("gate_finds_an_open_marker", "ExecStartPre", None, 0, True, True, judge_replaced_after_latch, 0),
    ("stop_that_was_not_an_oom", "ExecStopPost", "exit-code", 0, True, True, judge_marker_removed, 0),
    ("stop_after_an_oom_recorded", "ExecStopPost", "oom-kill", 1, True, True, judge_removed_after_record, 0),
    ("stop_after_an_oom_not_recorded", "ExecStopPost", "oom-kill", 1, True, False, judge_marker_untouched, "nonzero"),
    ("stop_without_a_result_from_systemd", "ExecStopPost", None, 0, True, True, judge_marker_untouched, "any"),
]


def judge_scenario(name, rc, events, judge, guard, want_rc):
    """-> (status, detail, data)."""
    problems = []
    if not events:
        return "not_run", "INCOMPLETE: strace recorded no calls (is strace installed and allowed to trace?)", {"rc": rc}
    if want_rc == 0 and rc != 0:
        problems.append("exit status %s, expected 0" % rc)
    if want_rc == "nonzero" and rc == 0:
        problems.append("exit status 0: the failure to record was not reported")
    problems += judge(events, guard)
    return ("fail" if problems else "pass"), ("; ".join(problems) if problems else "order of system calls as designed"), {"rc": rc, "calls": [e["line"] for e in events if e["ok"]][-14:]}


def run_scenario(env, spec, unit_text, base, service_user, program_map=None, timeout=60):
    """Execute one scenario for real: build the scratch layout (as root, owned by the service user), run the unit's own command as that user under strace, judge. -> (status, detail, data)."""
    import pwd
    name, key, result, oom, marker_open, writable, judge, want_rc = spec
    pw = pwd.getpwnam(service_user)
    d = os.path.join(base, name)
    sc = {"root": d + "/root", "public": d + "/public", "guard": d + "/guard", "cgroup": d + "/cgroup"}
    os.makedirs(d, exist_ok=True)
    for p in (sc["root"] + "/store", sc["public"], sc["guard"], sc["cgroup"]):
        os.makedirs(p, exist_ok=True)
    with open(sc["cgroup"] + "/memory.events", "w") as f:
        f.write("low 0\nhigh 0\nmax 0\noom 0\noom_kill %d\n" % oom)
    if marker_open:
        with open(sc["guard"] + "/" + MARKER, "w") as f:
            json.dump({"opened_at": 1.0, "boot_id": "scratch-boot", "invocation_id": "scratch-run", "pid": 1}, f)
    for p in (sc["root"] + "/store", sc["public"], sc["guard"]):
        for dd, dirs, files in os.walk(p):
            for n in [dd] + [os.path.join(dd, x) for x in dirs + files]:
                os.chown(n, pw.pw_uid, pw.pw_gid)
    os.chmod(sc["guard"], 0o700 if writable else 0o500)
    for p in (d, sc["root"], sc["cgroup"]):
        os.chmod(p, 0o755)
    argv = probe_command(unit_text, key, sc, program_map)
    log = d + "/strace.log"
    envv = ["env", "-i", "PATH=/usr/bin:/bin", "INVOCATION_ID=probe-" + name]
    if result:
        envv.append("SERVICE_RESULT=" + result)
    cmd = ["strace", "-f", "-yy", "-s", "64", "-o", log, "-e", "trace=" + TRACE] + env.as_service(envv + argv)
    rc, out, err = env.run(cmd, timeout=timeout)
    if rc == 127:
        return "not_run", "INCOMPLETE: strace is not installed (the operator installs it before the run: README, setup)", {"stderr": err[-200:]}
    text = env.read(log) or ""
    status, detail, data = judge_scenario(name, rc, parse_strace(text), judge, sc["guard"], want_rc)
    data["scratch"] = d
    data["stderr"] = (err or "")[-300:]
    return status, detail, data
