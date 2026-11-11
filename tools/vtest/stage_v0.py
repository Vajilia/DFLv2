"""V0: the setup is what the design says it is. Who the services run as, what they can touch, what systemd actually applied, that the privileged injection and kernel-log collection are separate from them,
and that the run marker is written and removed in the order the design needs. Nothing here measures the memory rule.

  V0.service_user   the dedicated user: unprivileged, no login, no extra groups
  V0.layout         owners and modes of the folders (the intended production permissions); the code is read-only to the service
  V0.access         AS the service user: it can create files in its store, the public folder and the guard folder, and nowhere else the harness or the units keep things
  V0.units          the installed units still carry the template's sandbox and the lifecycle design
  V0.kernel_log     the harness (root) collects the kernel log itself; whether the service user can is recorded (production: probably not, so an incident record says the victim is unknown)
  V0.process        the REAL daemon and monitor processes: the service user's uid and gid only, no capabilities, no_new_privs; systemd applied what the unit files say
  V0.marker         a clean stop closes the run marker; a start opens one for THIS invocation before the daemon's main process starts
  V0.durability     the real start gate and stop hook, as the service user under strace, in scratch folders: the marker is written (file, fsync, rename, fsync of the folder) and removed (unlink, fsync of
                    the folder) in the order the design needs, removed only after the incident is durable, and left alone when the stop could not be accounted for
The privileged parts of a run stay with the harness (root): loaders placed in the daemon's cgroup, the kernel log, chattr, unit control. The operator's documented commands run as the service user.
"""
import json
import os

import durability as D
import lifecycle as LC
import perms as P
from common import BASE, ETC, EVID, GUARD, LOADDIR, PROBE, PUBLIC, ROOT_DIR, SERVICE_USER, SRC_BASE, STATE, UNIT, UNIT_DIR, WEB_DIR, StageStop

STAGE = "V0"
PLAN = [("V0.service_user", "the dedicated user is unprivileged, has no login and no extra groups"),
        ("V0.layout", "owners and modes of the folders as the intended production permissions; the pinned code is read-only to the service"),
        ("V0.access", "as the service user: create an unnamed file in its store, the public folder and the guard folder (must work) and in every other folder (must be refused)"),
        ("V0.units", "the installed units carry the template's sandbox directives and the lifecycle design"),
        ("V0.kernel_log", "the harness reads the kernel log as root; whether the service user can is recorded"),
        ("V0.process", "the running daemon and monitor: service user's uid/gid only, no capabilities, no_new_privs; systemd applied the unit files' sandbox"),
        ("V0.marker", "stop the daemon (the marker must be closed), start it: the marker is open for this invocation and was written before the daemon's main process started"),
        ("V0.durability", "strace of the real start gate and stop hook in scratch folders, as the service user: six scenarios, order of write / fsync / rename / unlink / fsync of the folder")]
DEFAULTS = {"qualifying": {"settle_s": 20}, "smoke": {"settle_s": 12}}
ACCESS_SNIPPET = ("import os,sys,errno\n"
                  "try:\n"
                  "    fd=os.open(sys.argv[1], os.O_TMPFILE|os.O_WRONLY, 0o600); os.close(fd); print('ok')\n"
                  "except OSError as e:\n"
                  "    print(errno.errorcode.get(e.errno, str(e.errno)))\n")
# an unnamed file: creating it needs the same permission as creating a named one, and it leaves nothing behind (so nothing to delete in the guard folder)


def paths(tree):
    return P.layout_paths(tree)


def user_facts(env, name):
    import pwd
    try:
        pw = pwd.getpwnam(name)
    except KeyError:
        return {"name": name, "exists": False}
    rc, out, _ = env.run(["id", "-Gn", name])
    return {"name": name, "exists": True, "uid": pw.pw_uid, "gid": pw.pw_gid, "shell": pw.pw_shell, "groups": out.split() if rc == 0 else None}


def run(ctx):
    env, rec = ctx.env, ctx.rec
    inst = json.loads(env.read(STATE + "/install.json") or "{}")
    user = inst.get("user")
    ctx.mark("V0.service_user", user=user)
    if not user:
        rec.check("V0.service_user", "fail", "the machine was installed without a service user (the units run as root): run `vtest.py teardown --yes` (keeps the evidence), then `vtest.py install` (default user %s)" % SERVICE_USER, {"install_user": user})
        raise StageStop("no service user")
    facts = user_facts(env, user)
    rec.add_all(P.judge_service_user(facts))
    rec.stop_if_failed()
    uid, gid = facts["uid"], facts["gid"]
    ps = paths(ctx.tree)

    ctx.mark("V0.layout")
    expected = P.layout(ps)
    rec.add_all(P.judge_layout(P.observe([p for p, _, _ in expected]), expected, user))
    bad = P.tree_writable_by_others(ctx.tree)
    rec.check("V0.layout.code_read_only", "fail" if bad else "pass", "%d paths in the pinned tree are writable by group or other%s" % (len(bad), (": " + ", ".join(bad[:3])) if bad else ""), {"examples": bad[:10]})
    rec.stop_if_failed()

    ctx.mark("V0.access")
    must_write = [ROOT_DIR + "/store", PUBLIC, GUARD]
    must_not = [BASE, ROOT_DIR, WEB_DIR, ctx.tree, ETC, EVID, STATE, LOADDIR, PROBE]
    probes = {}
    for p in must_write + must_not:
        rc, out, err = env.operator(["/usr/bin/python3", "-I", "-c", ACCESS_SNIPPET, p])
        probes[p] = (out or "").strip() or ("rc %s %s" % (rc, (err or "").strip()[:60]))
    rec.add_all(P.judge_access(probes, must_write, must_not))
    rec.stop_if_failed()

    ctx.mark("V0.units")
    for k in ("daemon", "monitor"):
        text = env.read("%s/%s" % (UNIT_DIR, UNIT[k])) or ""
        tpl = env.read("%s/deploy/supervisor/dfl-broadcast-%s.service" % (ctx.tree, k)) or ""
        probs = P.sandbox_declared(tpl, text)
        if k == "daemon":
            import render as RN
            probs += RN.lifecycle_problems(tpl, text)
        rec.check("V0.units.%s" % k, "fail" if probs else "pass", "; ".join(probs) or "sandbox directives and (daemon) lifecycle hooks as in the pinned template", {"problems": probs})
    rec.stop_if_failed()

    ctx.mark("V0.kernel_log")
    restrict = (env.read("/proc/sys/kernel/dmesg_restrict") or "").strip() or "unknown"
    rc, out, _ = env.operator(["dmesg"])
    can = rc == 0 and bool((out or "").strip())
    if not can:
        rc, out, _ = env.operator(["journalctl", "-k", "-n", "1", "--no-pager"])
        can = rc == 0 and bool((out or "").strip()) and "No entries" not in out and "not seeing" not in out
    lines = ctx.kernel_log(env.now() - 6 * 3600).splitlines()
    rec.add_all(P.judge_kernel_log(restrict, can, len(lines)))
    rec.stop_if_failed()

    ctx.mark("V0.process")
    ctx.ensure_baseline("vtest V0: baseline")
    for k in ("daemon", "monitor"):
        pid = ctx.show(k, ("MainPID",)).get("MainPID")
        st = env.read("/proc/%s/status" % pid) if pid else None
        if not st:
            rec.check("V0.process.%s" % k, "fail", "no main process to inspect", None)
            continue
        rec.add_all(P.judge_process(k, P.parse_proc_status(st), uid, gid))
        rc, out, _ = env.sc("show", UNIT[k], *["-p" + x for x in P.SANDBOX_SCALARS + P.SANDBOX_PATHS])
        rec.add_all(P.judge_applied_sandbox(k, env.read("%s/%s" % (UNIT_DIR, UNIT[k])) or "", P.parse_show(out), user))
    rec.stop_if_failed()

    ctx.mark("V0.marker")
    env.sc("reset-failed", UNIT["daemon"])
    env.sc("stop", UNIT["daemon"])
    ctx.wait(lambda: not ctx.active("daemon"), 60)
    closed = ctx.run_marker()
    rec.check("V0.marker.closed_by_a_clean_stop", "pass" if closed["state"] == "absent" else "fail", "marker after a clean `systemctl stop`: %s" % closed["state"], {"marker": closed})
    env.sc("start", UNIT["daemon"])
    ctx.wait(lambda: ctx.active("daemon"), 60)
    env.sleep(2)
    rc, out, _ = env.sc("show", UNIT["daemon"], "-pInvocationID", "-pExecMainStartTimestampMonotonic", "-pExecStartPre")
    sh = P.parse_show(out)
    started = LC.wall_time_of_monotonic_us(int(sh["ExecMainStartTimestampMonotonic"]) if (sh.get("ExecMainStartTimestampMonotonic") or "").isdigit() else None, env.now(), env.monotonic())
    rec.add_all(P.judge_marker_before_start(ctx.run_marker(), sh.get("InvocationID"), started, P.parse_exec_pre_status(out)))
    rec.stop_if_failed()

    ctx.mark("V0.durability")
    unit_text = env.read("%s/%s" % (UNIT_DIR, UNIT["daemon"])) or ""
    os.makedirs(PROBE, exist_ok=True)
    for spec in D.SCENARIOS:
        status, detail, data = D.run_scenario(env, spec, unit_text, PROBE, user)
        rec.check("V0.durability.%s" % spec[0], status, detail, data)
    rec.check("V0.durability.scope", "info", "this shows the ORDER of the program's own system calls (write, fsync, rename, unlink, fsync of the folder); it does not show that the disk honoured them (a power cut on a running "
              "machine cannot be tested; V4's hard-reset phase shows only that the marker survives it)", None, False)
