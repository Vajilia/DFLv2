"""Install the code under test and the test units on a disposable machine, and take them away again.

  install   extract the PINNED tree with `git archive`, verify every file against git's blob hashes, render the three test units from the repository's own templates,
            build the generated fixture, write the environment file. Starts nothing.
  teardown  (a person runs it, with --yes) stop and remove dfl-vtest units, the extracted tree and, with --include-evidence, the evidence. Prints before it acts.

The units are COPIES of deploy/supervisor/dfl-broadcast-{daemon,monitor}.service. `render_unit` changes only what must change to run on a stand-in layout, lists every change, and
refuses to proceed if any line that matters to the memory rule (MemoryMax, OOMPolicy, Restart*, StartLimit*, TimeoutStopSec, TasksMax, the monitor's flags) differs from the template.
"""
import difflib
import io
import json
import os
import re
import tarfile

from common import (BASE, ETC, GUARD, MARKER, PORT, PREFIX, PUBLIC, ROOT_DIR, SRC_BASE, STATE, UNIT, UNIT_DIR, WEB_DIR, EVID, LOADDIR, Unsafe, allowed_path, git, read_pin, sha256_file)

DAEMON_TEMPLATE = "deploy/supervisor/dfl-broadcast-daemon.service"
MONITOR_TEMPLATE = "deploy/supervisor/dfl-broadcast-monitor.service"
ENV_TEMPLATE = "deploy/supervisor/broadcast.env.template"
INVARIANT_KEYS = ("MemoryMax", "OOMPolicy", "TasksMax", "Restart", "RestartSec", "RestartPreventExitStatus", "TimeoutStopSec", "StartLimitIntervalSec", "StartLimitBurst", "Nice", "UMask")


def tree_dir(pin):
    return "%s/%s" % (SRC_BASE, pin[:12])


def substitutions(pin, user=None):
    """The complete list of text changes made to a template, as (old, new). Nothing else is changed."""
    t = tree_dir(pin)
    return [("/opt/dflv2-src/__COMMIT__", t), ("/opt/dflv2-src", SRC_BASE), ("/etc/dflv2/broadcast.env", ETC + "/broadcast.env"),
            ("__ROOT__", ROOT_DIR), ("__PUBLIC__", PUBLIC), ("__GUARD__", GUARD), ("https://__HOST__", "http://127.0.0.1:%d" % PORT),
            ("/sys/fs/cgroup/system.slice/dfl-broadcast-daemon.service", "/sys/fs/cgroup/system.slice/" + UNIT["daemon"]),
            ("After=dfl-broadcast-daemon.service", "After=%s %s" % (UNIT["daemon"], UNIT["site"]))]


def render_unit(template, pin, user=None):
    """-> (text, changes). `user` None: the User=/Group= lines are dropped (the unit runs as root; a recorded deviation), else they are set."""
    out, changes = [], []
    for line in template.splitlines():
        new = line
        for old, repl in substitutions(pin):
            new = new.replace(old, repl)
        if new.startswith("# TEMPLATE, NOT INSTALLED"):
            new = "# VTEST RENDERING of the pinned template, for a disposable machine only. Layout changes are listed by `vtest.py plan`."
        if re.match(r"(User|Group)=", new):
            new = ("%s=%s" % (new.split("=")[0], user)) if user else None
        if new and new.startswith("Description="):
            new = "Description=VTEST " + new[len("Description="):]
        if new and re.match(r"WantedBy=", new):
            new = None
        if new == "[Install]":
            new = None
        if new != line:
            changes.append((line, new))
        if new is not None:
            out.append(new)
    text = "\n".join(out) + "\n"
    leftover = re.findall(r"__[A-Z_]+__", text)
    if leftover:
        raise Unsafe("unreplaced placeholders in the rendered unit: %s" % sorted(set(leftover)))
    return text, changes


def invariants(template, rendered):
    """-> list of (key, template value, rendered value) that differ. Must be empty."""
    def vals(txt):
        d = {}
        for l in txt.splitlines():
            m = re.match(r"([A-Za-z]+)=(.*)$", l)
            if m and m.group(1) in INVARIANT_KEYS:
                d.setdefault(m.group(1), []).append(m.group(2))
        return d
    a, b = vals(template), vals(rendered)
    return [(k, a.get(k), b.get(k)) for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]


def flags(line):
    return re.findall(r"--[a-z-]+", line)


def exec_flags_unchanged(template, rendered):
    ta = [l for l in template.splitlines() if l.startswith("ExecStart=")]
    tb = [l for l in rendered.splitlines() if l.startswith("ExecStart=")]
    return [flags(x) for x in ta] == [flags(x) for x in tb]


def HARNESS_DIR():
    return os.path.dirname(os.path.abspath(__file__))


def site_unit(pin):
    t = tree_dir(pin)
    return ("[Unit]\nDescription=VTEST loopback site: the rules the production server is meant to give, over the generated public folder\nAfter=network.target\n\n[Service]\nType=simple\n"
            "ExecStart=/usr/bin/python3 %s/site_server.py --tree %s --dir %s --port %d\nRestart=on-failure\nRestartSec=2\nMemoryMax=128M\n" % (HARNESS_DIR(), t, WEB_DIR, PORT))


def env_file(free_gib):
    """/etc/dfl-vtest/broadcast.env: the template's numbers, with the two the machine's size forces (recorded as deviations)."""
    floor = max(1, min(6, int(free_gib // 2)))
    return ("PUBLIC_GB_PER_SEASON=1.25\nPUBLIC_BASE_GB=0.25\nPUBLIC_CEILING_GB=2\nFLOOR_BROADCAST_GB=%d\nFLOOR_BUILD_GB=%d\n" % (floor, floor + 2)), floor


def extract_tree(env, repo, pin, runner=None):
    """git archive PIN into SRC_BASE/<pin12>/ and verify every file against git. -> (ok, [problems])."""
    dest = tree_dir(pin)
    if not allowed_path(dest):
        raise Unsafe(dest)
    rc, out, err = git(repo, "ls-tree", "-r", pin)
    if rc:
        return False, ["git ls-tree failed: " + err]
    want = {}
    for l in out.splitlines():
        meta, path = l.split("\t", 1)
        want[path] = meta.split()[2]
    if os.path.exists(dest):
        return False, ["%s already exists: refusing to overwrite a pinned tree (teardown first)" % dest]
    import subprocess
    os.makedirs(dest)
    data = subprocess.run(["git", "-C", repo, "archive", "--format=tar", pin], capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        tf.extractall(dest, filter="data") if hasattr(tarfile, "data_filter") else tf.extractall(dest)
    problems = []
    for path, blob in want.items():
        p = os.path.join(dest, path)
        if os.path.islink(p):
            continue
        h = subprocess.run(["git", "hash-object", p], capture_output=True, text=True).stdout.strip()
        if h != blob:
            problems.append("%s: blob %s != %s" % (path, h[:12], blob[:12]))
    return not problems, problems or ["%d files match git's blobs for %s" % (len(want), pin[:12])]


def build_fixture(env, pin, slot=7200):
    """Generate the fixture season from the pinned tree's own rig (tests/test_season.py): no real data. A long slot makes the plan about 4.5 h of broadcast time, so the daemon
    does not reach the end of its plan during a stage. -> (rc, output)."""
    t = tree_dir(pin)
    code = ("import sys;sys.path[:0]=[%r,%r];from test_season import Rig;from dflsite import broadcast as B;B.FSYNC=False;r=Rig(%r,slot=%d);print(r.plan['total_s'])"
            % (t + "/src", t + "/tests", ROOT_DIR, slot))
    return env.run(["/usr/bin/python3", "-c", code], timeout=300)


def install(env, repo, pin=None, user=None):
    """The whole install. Starts nothing. -> dict describing what was done (also written to STATE/install.json)."""
    pin = pin or read_pin(repo)
    log = {"pin": pin, "steps": []}
    if not env.exists(MARKER):
        raise Unsafe("%s is missing: the operator must declare the machine disposable first" % MARKER)
    for d in (BASE, GUARD, PUBLIC, LOADDIR, EVID, STATE, ETC, ROOT_DIR, WEB_DIR, SRC_BASE):
        os.makedirs(d, exist_ok=True)
    ok, probs = extract_tree(env, repo, pin)
    log["steps"].append(("extract", ok, probs[:5]))
    if not ok:
        raise Unsafe("pinned tree not verified: %s" % probs[:3])
    t = tree_dir(pin)
    units = {}
    for name, tpl in (("daemon", DAEMON_TEMPLATE), ("monitor", MONITOR_TEMPLATE)):
        template = open(os.path.join(t, tpl)).read()
        text, changes = render_unit(template, pin, user)
        bad = invariants(template, text)
        if bad or not exec_flags_unchanged(template, text):
            raise Unsafe("rendered %s unit differs from the template in what matters: %s" % (name, bad or "ExecStart flags"))
        env.write("%s/%s" % (UNIT_DIR, UNIT[name]), text)
        units[name] = {"changes": changes, "diff": "\n".join(difflib.unified_diff(template.splitlines(), text.splitlines(), tpl, UNIT[name], lineterm=""))}
    env.write("%s/%s" % (UNIT_DIR, UNIT["site"]), site_unit(pin))
    free = env.free_bytes(BASE) / 2**30
    text, floor = env_file(free)
    env.write(ETC + "/broadcast.env", text)
    rc, out, err = build_fixture(env, pin)
    log["steps"].append(("fixture", rc == 0, (out or err)[-200:]))
    if rc:
        raise Unsafe("fixture build failed: " + err[-300:])
    env.write(WEB_DIR + "/index.html", "<!doctype html><title>vtest</title>\n")
    env.sc("daemon-reload")
    log["units"] = units
    log["deviations"] = ["runs as %s (User=/Group= dropped)" % ("root" if not user else user), "no [Install] section: units are never enabled", "paths, host and cgroup name changed to the dfl-vtest layout",
                         "FLOOR_BROADCAST_GB=%d (the template's 6 needs more free disk than a small disposable machine has)" % floor, "PUBLIC_CEILING_GB=2",
                         "a loopback site server unit is added (the monitor's URL checks need one)", "the fixture is generated (tests/test_season.py Rig, slot 7200), not a real season"]
    env.write(STATE + "/install.json", json.dumps(log, indent=1, default=str))
    return log


def teardown(env, include_evidence=False, yes=False, echo=print):
    """Remove the harness's own things. Prints what it will do; does nothing without yes. A person runs this, never a stage."""
    todo = []
    rc, lu, _ = env.sc("list-unit-files", "--no-legend", "--no-pager", PREFIX + "*")
    names = [l.split()[0] for l in (lu or "").splitlines() if l.strip().startswith(PREFIX)]
    for n in names:
        todo.append(("stop+remove unit", n))
    for p in (SRC_BASE, ETC, ROOT_DIR, WEB_DIR, GUARD, LOADDIR, STATE) + ((EVID,) if include_evidence else ()):
        if env.exists(p):
            todo.append(("remove tree", p))
    for kind, what in todo:
        echo("%-18s %s" % (kind, what))
    if not todo:
        echo("nothing to do")
        return 0
    if not yes:
        echo("dry run: nothing changed. Re-run with --yes. The evidence folder %s is %s." % (EVID, "INCLUDED" if include_evidence else "kept"))
        return 0
    import shutil
    for n in names:
        env.sc("stop", n)
        path = "%s/%s" % (UNIT_DIR, n)
        if allowed_path(path) and os.path.exists(path):
            os.unlink(path)
    for kind, what in todo:
        if kind == "remove tree":
            if not allowed_path(what):
                raise Unsafe(what)
            shutil.rmtree(what, ignore_errors=False)
    env.sc("daemon-reload")
    return 0
