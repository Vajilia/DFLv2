#!/usr/bin/env python3
"""Driver for the memory-rule validation harness (docs/MEMORY_MONITOR_VALIDATION_PLAN.md). Nothing here runs by itself; each command is typed by a person.

  vtest.py pin-check                      the code under test is the pinned commit; HEAD differs only in docs/harness/tests; the guard constants match the pinned tree
  vtest.py preflight [--json]             read-only: is this machine a usable, disposable environment? (changes nothing)
  vtest.py plan [V1..V4]                  print every step and the durations; executes nothing
  vtest.py install [--user U]             extract and verify the pinned tree, render the test units, build the generated fixture. Starts nothing
  vtest.py run [--stages V1,V2,V3,V4] [--smoke] [--run-id ID] [--with-reboot] [--after-reboot] [--acknowledge-stop REASON]
                                          run stages in order; STOPS at the first stage that does not pass (units and evidence are left exactly as they are)
  vtest.py status                         runs, results, any STOPPED marker
  vtest.py start-units                    start site, daemon and monitor (after a reboot, for V4 --after-reboot)
  vtest.py affected --recorded REV        which stages a change since REV invalidates (docs-only changes: none)
  vtest.py selftest                       plumbing check with plain processes on THIS machine, no systemd, no cgroup limits; proves nothing about the memory rule
  vtest.py teardown [--yes] [--include-evidence]   remove the harness's own things (a person's act; prints first; the evidence is kept unless asked)
"""
import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common as C                                                    # noqa: E402
import preflight as PF                                                # noqa: E402
import render as RN                                                   # noqa: E402

STAGE_MODULES = {}


def stage_module(name):
    import importlib
    return importlib.import_module("stage_" + name.lower())


def constants_check(repo, pin):
    """The numbers the checks compare with are the pinned tree's own. -> list of mismatches."""
    bad = []
    def blob(path):
        rc, out, _ = C.git(repo, "show", "%s:%s" % (pin, path))
        return out
    br, rc_ = blob("tools/broadcast_run.py"), blob("tools/rehearsal_check.py")
    for name, val, src in (("GUARD_STALE_S", C.GUARD_STALE_S, br), ("GUARD_GRACE_S", C.GUARD_GRACE_S, br), ("MEM_SUSTAIN_S", C.MEM_SUSTAIN_S, rc_), ("MEM_PINNED_FRAC", C.MEM_FRAC, rc_)):
        m = re.search(r"^%s\s*=\s*([0-9.]+)" % name, src, re.M)
        if not m or float(m.group(1)) != val:
            bad.append("%s: harness assumes %s, pinned tree has %s" % (name, val, m.group(1) if m else "nothing"))
    m = re.search(r'"--every".*default=([0-9.]+)', rc_)
    if not m or float(m.group(1)) != C.MON_EVERY_S:
        bad.append("monitor --every default: harness assumes %s, pinned tree has %s" % (C.MON_EVERY_S, m.group(1) if m else "nothing"))
    return bad


def cmd_pin_check(a):
    pin = C.read_pin()
    ok, msgs = C.pin_check(C.REPO, pin)
    print("pin      %s" % pin)
    print("HEAD     %s" % C.harness_rev())
    for m in msgs:
        print(("OK    " if ok else "FAIL  ") + m)
    bad = constants_check(C.REPO, pin)
    for b in bad:
        print("FAIL  " + b)
    if not bad:
        print("OK    guard and rule constants match the pinned tree")
    return 0 if ok and not bad else 1


def cmd_preflight(a):
    rows = PF.preflight(C.Env())
    if a.json:
        print(json.dumps([{"id": c, "status": s, "detail": d} for c, s, d in rows], indent=1))
    else:
        print(PF.report(rows))
    return 0 if PF.ready(rows) else 1


def estimate(mod, mode):
    d = mod.DEFAULTS[mode]
    return {"V1": d.get("baseline_s", 0) + d.get("duration_s", 0) + 300, "V2": 2 * d.get("baseline_s", 0) + 600 + d.get("timeline_s", 0) + 300, "V3": d.get("watch_s", 0) + 2 * 330 + 700,
            "V4": 6 * 150 + 400 + 2 * 400 + 200}[mod.STAGE]


def cmd_plan(a):
    names = a.stages or list(C.STAGES)
    total = {"qualifying": 0, "smoke": 0}
    for n in names:
        mod = stage_module(n)
        print("\n%s" % mod.__doc__.strip().split("\n")[0])
        for sid, text in mod.PLAN:
            print("  %-16s %s" % (sid, text))
        for mode in ("qualifying", "smoke"):
            total[mode] += estimate(mod, mode)
        print("  settings (qualifying): %s" % json.dumps(mod.DEFAULTS["qualifying"]))
        print("  settings (smoke):      %s" % json.dumps(mod.DEFAULTS["smoke"]))
    print("\nrough wall-clock: qualifying %.1f h, smoke %.1f h (a smoke run is a harness shakedown: its results are marked non-qualifying and prove nothing about the candidate)" % (total["qualifying"] / 3600, total["smoke"] / 3600))
    return 0


def cmd_install(a):
    ok, msgs = C.pin_check()
    if not ok:
        print("pin-check failed: " + "; ".join(msgs))
        return 1
    rows = PF.preflight(C.Env())
    if not PF.ready(rows):
        print(PF.report(rows))
        return 1
    log = RN.install(C.Env(), C.REPO, user=a.user)
    print(json.dumps({"pin": log["pin"], "deviations": log["deviations"]}, indent=1))
    return 0


def run_dir_state(env):
    state = os.path.join(C.EVID)
    stops = []
    for r in env.listdir(state):
        p = os.path.join(state, r, "STOPPED.json")
        if env.exists(p):
            stops.append((r, json.loads(env.read(p))))
    return stops


def cmd_run(a):
    env = C.Env()
    pin = C.read_pin()
    ok, msgs = C.pin_check(C.REPO, pin)
    bad = constants_check(C.REPO, pin)
    if not ok or bad:
        print("REFUSING: pin-check failed: " + "; ".join(msgs + bad))
        return 1
    rows = PF.preflight(env)
    if not PF.ready(rows):
        print(PF.report(rows))
        return 1
    inst = json.loads(env.read(C.STATE + "/install.json") or "{}")
    if inst.get("pin") != pin:
        print("REFUSING: not installed for pin %s (run `install`)" % pin[:12])
        return 1
    stops = run_dir_state(env)
    if stops and not a.acknowledge_stop:
        print("REFUSING: an earlier run STOPPED on a failed check: %s\nRead its evidence, then re-run with --acknowledge-stop 'reason of at least 10 characters' (recorded)." % (stops[-1],))
        return 2
    if a.acknowledge_stop:
        if len(a.acknowledge_stop.strip()) < 10:
            print("REFUSING: the acknowledgement needs a reason of at least 10 characters")
            return 2
    mode = "smoke" if a.smoke else "qualifying"
    run_id = a.run_id or time.strftime("run-%Y%m%dT%H%M%SZ", time.gmtime())
    rdir = os.path.join(C.EVID, run_id)
    os.makedirs(rdir, exist_ok=True)
    meta = {"pin": pin, "harness_rev": C.harness_rev(), "mode": mode, "qualifying": mode == "qualifying", "run_id": run_id, "kernel": (env.read("/proc/sys/kernel/osrelease") or "").strip(),
            "preflight": [{"id": c, "status": s, "detail": d} for c, s, d in rows], "deviations": inst.get("deviations"), "acknowledged_stop": a.acknowledge_stop}
    env.write(os.path.join(rdir, "run.json"), json.dumps(meta, indent=1))
    stages = [s.strip().upper() for s in a.stages.split(",")] if a.stages else list(C.STAGES)
    tree = RN.tree_dir(pin)
    results = {}
    for name in stages:
        mod = stage_module(name)
        sdir = os.path.join(rdir, name)
        rec = C.Recorder(name, sdir, meta)
        from scenarios import Ctx
        ctx = Ctx(env, rec, dict(mod.DEFAULTS[mode], mode=mode), tree, sdir)
        print("== %s (%s)" % (name, mode))
        note = None
        try:
            if name == "V4" and a.after_reboot:
                mod.run_after_reboot(ctx)
            else:
                mod.run(ctx)
        except C.StageStop as e:
            note = "stopped: %s" % e
            rec.check(name + ".stopped", "fail", note)
        except C.Unsafe as e:
            note = "REFUSED by a safety rule: %s" % e
            rec.check(name + ".safety", "fail", note)
        finally:
            ctx.stop_procs()
        res = rec.finish(note)
        results[name] = res["overall"]
        print("== %s: %s" % (name, res["overall"].upper()))
        if res["overall"] != "pass":
            env.write(os.path.join(rdir, "STOPPED.json"), json.dumps({"stage": name, "overall": res["overall"], "note": note, "failed": [c["id"] for c in rec.failed()], "at": time.time()}, indent=1))
            print("STOPPED at %s. Nothing was cleaned up: units, loaders' logs and evidence are as they were. See %s" % (name, sdir))
            env.write(os.path.join(rdir, "SUMMARY.json"), json.dumps({"results": results, "stopped_at": name, "qualifying": mode == "qualifying"}, indent=1))
            return 2
    env.write(os.path.join(rdir, "SUMMARY.json"), json.dumps({"results": results, "stopped_at": None, "qualifying": mode == "qualifying", "pin": pin, "harness_rev": meta["harness_rev"]}, indent=1))
    print("all requested stages passed: %s" % results)
    return 0


def cmd_status(a):
    env = C.Env()
    for r in env.listdir(C.EVID):
        s = env.read(os.path.join(C.EVID, r, "SUMMARY.json"))
        print(r, s.replace("\n", " ") if s else "(no summary)")
        if env.exists(os.path.join(C.EVID, r, "STOPPED.json")):
            print("   STOPPED:", (env.read(os.path.join(C.EVID, r, "STOPPED.json")) or "").replace("\n", " "))
    return 0


def cmd_start_units(a):
    env = C.Env()
    for k in ("site", "daemon", "monitor"):
        rc, out, err = env.sc("start", C.UNIT[k])
        print(k, "rc", rc, err.strip())
    return 0


def cmd_affected(a):
    r = C.affected(C.REPO, a.recorded, a.current)
    print(json.dumps(r, indent=1))
    return 0


def cmd_teardown(a):
    return RN.teardown(C.Env(), include_evidence=a.include_evidence, yes=a.yes)


def cmd_selftest(a):
    import selftest
    return selftest.main()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pin-check").set_defaults(fn=cmd_pin_check)
    p = sub.add_parser("preflight")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_preflight)
    p = sub.add_parser("plan")
    p.add_argument("stages", nargs="*")
    p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("install")
    p.add_argument("--user")
    p.set_defaults(fn=cmd_install)
    p = sub.add_parser("run")
    p.add_argument("--stages")
    p.add_argument("--smoke", action="store_true")
    p.add_argument("--run-id")
    p.add_argument("--with-reboot", action="store_true")
    p.add_argument("--after-reboot", action="store_true")
    p.add_argument("--acknowledge-stop")
    p.set_defaults(fn=cmd_run)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("start-units").set_defaults(fn=cmd_start_units)
    p = sub.add_parser("affected")
    p.add_argument("--recorded", required=True)
    p.add_argument("--current", default="HEAD")
    p.set_defaults(fn=cmd_affected)
    sub.add_parser("selftest").set_defaults(fn=cmd_selftest)
    p = sub.add_parser("teardown")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--include-evidence", action="store_true")
    p.set_defaults(fn=cmd_teardown)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
