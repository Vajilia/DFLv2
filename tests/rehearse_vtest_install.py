#!/usr/bin/env python3
"""Rehearse the real `vtest.py install` on THIS machine without touching it, then run V0's static checks over the result.

  sudo python3 tests/rehearse_vtest_install.py [--out DIR]      (root, Linux, no running systemd)

It enters a private mount namespace in which /etc, /srv and /opt are overlays whose writable layer lives in --out, so every folder, file, unit and user the installer creates
is visible to the installer and to the checks and is gone from the real file system when the process ends (the writable layers stay in --out as the evidence). It then runs the REAL
`render.install` (the pinned tree from `git archive`, verified blob by blob; the real unit rendering; the real fixture build; the real ownership step) with systemctl stubbed (a build
machine has no systemd to talk to), and judges the result with the SAME functions V0 uses: service user, layout, code read-only, access as the service user, units (sandbox and
lifecycle design). It also checks that the rendered units, the environment file and the unit's own paths agree with each other.

What it proves: the installer's configuration and path construction on a clean Linux root, and that V0's static checks would pass on what it produced.
What it does not prove: anything that needs a running systemd, a cgroup or the kernel's OOM killer (V0.process, V0.marker, V0.durability and stages V1 to V4).
Refuses to run on a machine that has a running systemd (PID 1) or any DFL production path.
Lives in tests/ (not tools/vtest/) on purpose: a file added to the harness folder changes the harness revision that every recorded run is tied to.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools", "vtest"))

OVERLAID = ("/etc", "/srv", "/opt")


def refuse(msg):
    print("REFUSING: " + msg, file=sys.stderr)
    raise SystemExit(2)


def preconditions():
    if os.geteuid() != 0:
        refuse("run as root (it creates a mount namespace and a user inside it)")
    if os.path.isdir("/run/systemd/system"):
        refuse("this machine runs systemd: the rehearsal is for a build machine. Use the real installer on the disposable VM instead.")
    for p in ("/srv/dflv2", "/srv/dflv2-rehearsal", "/etc/dflv2", "/opt/dflv2-src", "/srv/dfl-vtest", "/opt/dfl-vtest-src", "/etc/dfl-vtest"):
        if os.path.exists(p):
            refuse("%s exists: this is not a clean build machine" % p)
    for tool in ("unshare", "mount", "useradd", "setpriv", "git"):
        if not shutil.which(tool):
            refuse("%s is not installed" % tool)


def enter_namespace(out):
    """Re-run this script inside `unshare -m` with overlays; the child does the work."""
    for i, d in enumerate(OVERLAID):
        os.makedirs("%s/upper%s" % (out, d), exist_ok=True)
        os.makedirs("%s/work%s" % (out, d), exist_ok=True)
    mounts = " && ".join("mount -t overlay overlay -o lowerdir=%s,upperdir=%s/upper%s,workdir=%s/work%s %s" % (d, out, d, out, d, d) for d in OVERLAID)
    cmd = "set -e; %s; exec %s %s --inside --out %s" % (mounts, sys.executable, os.path.abspath(__file__), out)
    return subprocess.call(["unshare", "-m", "--propagation", "private", "sh", "-c", cmd])


def inside(out):
    import common as C
    import perms as P
    import render as RN
    import stage_v0 as V0

    rows = []

    def add(cid, ok, detail):
        rows.append((cid, "pass" if ok else "fail", detail))
        print("%-4s %-52s %s" % ("PASS" if ok else "FAIL", cid, detail))

    # the operator's part (README, setup): the dedicated user and the disposable marker; the harness itself never creates either
    subprocess.run(["useradd", "--system", "--no-create-home", "--shell", "/usr/sbin/nologin", C.SERVICE_USER], check=True)
    open(C.MARKER, "w").close()

    class Machine(C.Env):
        """The real file system (overlaid); only systemctl is stubbed, because there is no systemd here."""
        def __init__(self):
            self.systemctl_calls = []

        def sc(self, verb, *args, timeout=60):
            self.systemctl_calls.append((verb,) + args)
            return 0, "", ""

    env = Machine()
    pin = C.read_pin(C.REPO)
    log = RN.install(env, C.REPO, pin=pin, user=C.SERVICE_USER)
    add("install.completes", True, "RN.install finished for pin %s as user %s" % (pin[:12], C.SERVICE_USER))
    add("install.starts_nothing", all(v in ("daemon-reload",) for v, *_ in env.systemctl_calls), "systemctl verbs used: %s" % sorted({v for v, *_ in env.systemctl_calls}))
    add("install.record_written", os.path.isfile(C.STATE + "/install.json") and json.load(open(C.STATE + "/install.json"))["pin"] == pin, "%s/install.json names the pin" % C.STATE)
    tree = RN.tree_dir(pin)
    ps = P.layout_paths(tree)

    # configuration and path construction
    for k in ("daemon", "monitor", "site"):
        p = "%s/%s" % (C.UNIT_DIR, C.UNIT[k])
        add("unit.%s.installed" % k, os.path.isfile(p), p)
    import re
    production = ("/srv/dflv2", "/etc/dflv2", "/opt/dflv2-src", "/var/www", "/var/backups/dfl", "/root/.config")
    for k in ("daemon", "monitor", "site"):
        text = open("%s/%s" % (C.UNIT_DIR, C.UNIT[k])).read()
        add("unit.%s.no_placeholders" % k, not re.search(r"__[A-Z_]+__", text), "no __X__ placeholder is left")
        body = " ".join(l for l in text.splitlines() if l[:1] != "#")
        toks = re.findall(r"(?<![\w.:/-])(/[\w./@-]+)", re.sub(r"https?://\S+", " ", body))
        prod = sorted({t for t in toks if t.startswith(production)})
        add("unit.%s.names_no_production_path" % k, not prod, "production paths named: %s" % (prod or "none"))
        outside = sorted({t for t in toks if t.startswith(("/srv/", "/opt/", "/etc/")) and not t.startswith((C.BASE, C.SRC_BASE, C.ETC))})
        add("unit.%s.srv_opt_etc_paths_are_the_harnesses" % k, not outside, "paths under /srv, /opt, /etc outside the harness folders: %s" % (outside or "none"))
        for tok in sorted(set(re.findall(r"(%s/[\w./-]+\.py)" % re.escape(tree), text))):
            add("unit.%s.script_exists:%s" % (k, tok[len(tree) + 1:]), os.path.isfile(tok), tok)
    site = open("%s/%s" % (C.UNIT_DIR, C.UNIT["site"])).read()
    add("unit.site.runs_this_checkouts_site_server", RN.HARNESS_DIR() + "/site_server.py" in site and os.path.isfile(RN.HARNESS_DIR() + "/site_server.py"), "the loopback site unit runs the harness file %s/site_server.py (it is not part of the pinned tree)" % RN.HARNESS_DIR())
    envtext = open(C.ETC + "/broadcast.env").read()
    names = dict(l.split("=", 1) for l in envtext.splitlines() if "=" in l)
    tpl_env = open(os.path.join(tree, RN.ENV_TEMPLATE)).read()
    tpl_names = {l.split("=", 1)[0] for l in tpl_env.splitlines() if re.match(r"[A-Z_]+=", l)}
    add("env.keys_are_the_templates", set(names) <= tpl_names, "keys written %s; not in the template: %s" % (sorted(names), sorted(set(names) - tpl_names) or "none"))
    for k in ("daemon", "monitor"):
        text = open("%s/%s" % (C.UNIT_DIR, C.UNIT[k])).read()
        m = re.search(r"(?m)^EnvironmentFile=-?(\S+)", text)
        add("unit.%s.environment_file_is_the_one_written" % k, bool(m) and m.group(1) == C.ETC + "/broadcast.env", "EnvironmentFile=%s" % (m.group(1) if m else None))
    # every folder V0 judges exists after install, and the ownership step ran on the installer's own key set
    for p, _, _ in P.layout(ps):
        add("folder.exists:" + p, os.path.exists(p), "created by the installer")

    # the retry path found on 2026-11-11: a plain second install is refused and says what to do; a dry-run teardown lists everything and changes nothing
    before = sorted(os.listdir(C.SRC_BASE)), os.path.getmtime(C.STATE + "/install.json")
    try:
        RN.install(env, C.REPO, pin=pin, user=C.SERVICE_USER)
        refused, said = False, "a second install ran"
    except C.Unsafe as e:
        refused, said = True, str(e)
    add("retry.second_install_is_refused", refused and "teardown" in said, said[:160])
    add("retry.second_install_changed_nothing", before == (sorted(os.listdir(C.SRC_BASE)), os.path.getmtime(C.STATE + "/install.json")), "pinned tree list and install record unchanged")
    lines = []
    RN.teardown(env, include_evidence=False, yes=False, echo=lines.append)
    add("retry.teardown_dry_run_lists_and_keeps", any(l.startswith("remove tree") and l.endswith(C.PROBE) for l in lines) and not any(l.startswith("remove tree") and l.endswith(C.EVID) for l in lines) and any("dry run" in l for l in lines) and os.path.isdir(C.PROBE), "%d lines; the probe folder is listed for removal, the evidence folder is not, the dry run says so, nothing was removed" % len(lines))

    # V0's static checks, with the same functions the stage uses
    facts = V0.user_facts(env, C.SERVICE_USER)
    for r in P.judge_service_user(facts):
        add(r[0], r[1] == "pass", r[2])
    expected = P.layout(ps)
    for r in P.judge_layout(P.observe([p for p, _, _ in expected]), expected, C.SERVICE_USER):
        add(r[0], r[1] == "pass", r[2])
    bad = P.tree_writable_by_others(tree)
    add("V0.layout.code_read_only", not bad, "%d paths in the pinned tree are writable by group or other" % len(bad))
    must_write = [C.ROOT_DIR + "/store", C.PUBLIC, C.GUARD]
    must_not = [C.BASE, C.ROOT_DIR, C.WEB_DIR, tree, C.ETC, C.EVID, C.STATE, C.LOADDIR, C.PROBE]
    probes = {}
    for p in must_write + must_not:
        env.service_user = C.SERVICE_USER
        rc, o, e = env.operator(["/usr/bin/python3", "-I", "-c", V0.ACCESS_SNIPPET, p])
        probes[p] = (o or "").strip() or ("rc %s %s" % (rc, (e or "").strip()[:60]))
    for r in P.judge_access(probes, must_write, must_not):
        add(r[0], r[1] == "pass", r[2])
    for k in ("daemon", "monitor"):
        text = open("%s/%s" % (C.UNIT_DIR, C.UNIT[k])).read()
        tpl = open("%s/deploy/supervisor/dfl-broadcast-%s.service" % (tree, k)).read()
        probs = P.sandbox_declared(tpl, text)
        if k == "daemon":
            probs += RN.lifecycle_problems(tpl, text)
        add("V0.units." + k, not probs, "; ".join(probs) or "sandbox directives and lifecycle hooks as in the pinned template")

    failed = [r for r in rows if r[1] != "pass"]
    summary = {"pin": pin, "checks": len(rows), "failed": [r[0] for r in failed], "deviations": log["deviations"]}
    json.dump({"summary": summary, "rows": rows}, open(os.path.join(out, "rehearsal-result.json"), "w"), indent=1)
    print("\n%d checks, %d failed%s" % (len(rows), len(failed), (": " + ", ".join(r[0] for r in failed)) if failed else ""))
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", help="folder for the overlays' writable layers and the result (default: a new temporary folder)")
    ap.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.inside:
        return inside(a.out)
    preconditions()
    out = os.path.abspath(a.out) if a.out else tempfile.mkdtemp(prefix="dfl-install-rehearsal-")
    if os.path.exists(out) and os.listdir(out):
        refuse("%s is not empty" % out)
    os.makedirs(out, exist_ok=True)
    print("rehearsal layers and result: %s" % out)
    return enter_namespace(out)


if __name__ == "__main__":
    sys.exit(main())
