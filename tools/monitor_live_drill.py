#!/usr/bin/env python3
"""Local LIVE drill of the rehearsal monitor next to a REAL daemon process, from an independent copy of the code. Scratch only: no Droplet, no network, no production path.

  monitor_live_drill.py --work DIR [--rev REV] [--expect-sha256 HEX] [--minutes N]

What it does (all under DIR, which must be new):
  1. `git archive REV src tools` unpacked into DIR/tree: an independent copy (no hard links, nothing shared with the repository checkout). Its tools/rehearsal_check.py is hashed.
  2. A small synthetic season (tests/test_season.py Rig: three games, a real plan) is stored in DIR/root/store.
  3. Two real processes started from DIR/tree: the monitor (`rehearsal_check.py watch`, passes every 1 s / 3 s) and the daemon (`broadcast_run.py run --guard`), in real time.
  4. Phase A (clean): the daemon publishes (status.json every second, releases, live segments); nothing may be reported. A sampler counts how often a `.tmp-` file of the daemon is
     visible, i.e. how often the monitor's listing could have raced a rename.
  5. Phase B: a deliberate STALE temporary file is planted: the monitor must report `stale_tmp`; as before the fix it also trips (the file is not one the position allows). The
     documented recovery (remove the cause, `release --reason`, `resume --guard`) is then run for real and the broadcast position must be preserved.
  6. Phase C: an UNAUTHORIZED FUTURE file (a release the position does not allow yet) is planted: the monitor must report `early_or_unknown_file`, write TRIPPED, and the daemon must pause.
Exit 0 only if every expectation holds. `monitor_error` anywhere is a failure.
"""
import argparse
import glob
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def log_lines(path):
    out = []
    if os.path.exists(path):
        for line in open(path):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work", required=True)
    ap.add_argument("--rev", default="HEAD")
    ap.add_argument("--expect-sha256", help="the sha256 tools/rehearsal_check.py must have in the unpacked tree")
    ap.add_argument("--minutes", type=float, default=4.5, help="how long the clean phase A lasts counted from the daemon start (the first live window opens at 200 s)")
    a = ap.parse_args(argv)
    work = os.path.abspath(a.work)
    if os.path.exists(work) and os.listdir(work):
        raise SystemExit("%s is not empty" % work)
    os.makedirs(work, exist_ok=True)
    tree, run = os.path.join(work, "tree"), os.path.join(work, "root")
    os.makedirs(tree)
    rev = subprocess.run(["git", "-C", ROOT, "rev-parse", a.rev], capture_output=True, text=True, check=True).stdout.strip()
    arch = subprocess.Popen(["git", "-C", ROOT, "archive", rev, "src", "tools"], stdout=subprocess.PIPE)
    subprocess.run(["tar", "-x", "-C", tree], stdin=arch.stdout, check=True)
    arch.wait()
    results, notes = [], []

    def check(label, ok, detail=""):
        results.append((label, bool(ok), detail))
        print("%s: %s %s" % ("PASS" if ok else "FAIL", label, detail), flush=True)
        return ok

    rc = os.path.join(tree, "tools", "rehearsal_check.py")
    got = sha256(rc)
    nlinks = max(os.stat(os.path.join(d, f)).st_nlink for d, _, fs in os.walk(tree) for f in fs)
    check("independent tree: no hard links", nlinks == 1, "max link count %d" % nlinks)
    if a.expect_sha256:
        check("rehearsal_check.py in the tree is the pinned file", got == a.expect_sha256, got[:16])
    notes.append("revision %s; tools/rehearsal_check.py sha256 %s" % (rev, got))

    sys.path[:0] = [os.path.join(ROOT, "src"), os.path.join(ROOT, "tools"), os.path.join(ROOT, "tests")]
    from test_season import Rig
    from dflsite import broadcast as B
    B.FSYNC = True
    rig = Rig(run, slot=300)
    store, public = rig.store, os.path.join(work, "pub")             # the public folder is NOT inside the root (the daemon refuses that)
    monitor_dir = os.path.join(work, "monitor")
    os.makedirs(monitor_dir)
    wlog = os.path.join(monitor_dir, "watch.log")
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    mon = subprocess.Popen([PY, "-I", rc, "watch", "--root", run, "--public", public, "--log", wlog, "--evidence", os.path.join(monitor_dir, "evidence"), "--guard", monitor_dir,
                            "--every", "1", "--full-every", "3", "--min-free-gb", "0", "--max-public-gb", "3"],
                           stdout=open(os.path.join(work, "monitor.out"), "w"), stderr=subprocess.STDOUT, env=env)
    time.sleep(2)
    dm = subprocess.Popen([PY, "-I", os.path.join(tree, "tools", "broadcast_run.py"), "run", "--root", run, "--public", public, "--guard", monitor_dir, "--tick", "1"],
                          stdout=open(os.path.join(work, "daemon.out"), "w"), stderr=subprocess.STDOUT, env=env)
    t_start = time.time()
    seen = {"tmp_visible": 0, "samples": 0}
    stop = threading.Event()

    def sampler():                                                     # how often could a listing have raced a rename? (read-only)
        names = [os.path.join(public, ".tmp-status.json-%d" % dm.pid)]
        while not stop.is_set():
            seen["samples"] += 1
            if any(os.path.exists(n) for n in names):
                seen["tmp_visible"] += 1
            for d in (os.path.join(public, "broadcast"),):
                try:
                    if any(n.startswith(".tmp-") for n in os.listdir(d)):
                        seen["tmp_visible"] += 1
                except OSError:
                    pass
            time.sleep(0.002)
    th = threading.Thread(target=sampler, daemon=True)
    th.start()
    try:
        # ---- phase A
        end_a = t_start + a.minutes * 60
        while time.time() < end_a and mon.poll() is None and dm.poll() is None:
            time.sleep(2)
        lines = log_lines(wlog)
        hb = [l for l in lines if l.get("heartbeat")]
        viol = [l for l in lines if l.get("violation")]
        check("monitor and daemon still running after the clean phase", mon.poll() is None and dm.poll() is None, "monitor %s daemon %s" % (mon.poll(), dm.poll()))
        check("clean phase: full passes logged and all ok", len(hb) >= 10 and all(l.get("ok") for l in hb), "%d heartbeats, positions %s..%s" % (len(hb), hb[0].get("pos") if hb else None, hb[-1].get("pos") if hb else None))
        check("clean phase: no violation of any kind, no monitor_error", not viol, "violations: %s" % [(v["violation"], v.get("detail", "")[:80]) for v in viol[:5]])
        check("the live window was open (public files grew past the offseason)", bool(hb) and hb[-1].get("public_files", 0) > 20 and (hb[-1].get("pos") or 0) > 220, "last full pass %s" % (hb[-1] if hb else None))
        notes.append("race exposure: a daemon temporary file was visible in %d of %d samples (2 ms apart) during the run" % (seen["tmp_visible"], seen["samples"]))
        # ---- phase B: stale temporary file
        stale = os.path.join(public, "broadcast", ".tmp-4242-stale-injected.json")
        with open(stale, "w") as f:
            f.write("{}")
        os.utime(stale, (time.time() - 600, time.time() - 600))
        t_b = time.time()
        got_stale = False
        while time.time() - t_b < 20:
            if any(l.get("violation") == "stale_tmp" for l in log_lines(wlog)):
                got_stale = True
                break
            time.sleep(0.5)
        check("deliberate stale temporary file is reported as stale_tmp", got_stale, "after %.1f s" % (time.time() - t_b))
        # A stale temporary file is BOTH a logged `stale_tmp` and, in the full pass, a file the position does not allow (`early_or_unknown_file`, spoiler class): this is the
        # behaviour the monitor had before the fix and it is kept (docs/BROADCAST_REHEARSAL.md lists stale_tmp as "logged only", which understates it).
        t_b2 = time.time()
        while time.time() - t_b2 < 15 and not os.path.exists(os.path.join(monitor_dir, "TRIPPED")):
            time.sleep(0.5)
        try:
            trb = json.load(open(os.path.join(monitor_dir, "TRIPPED")))
        except (OSError, ValueError):
            trb = {}
        check("the stale temporary file also trips the monitor, as it did before the fix", trb.get("kind") == "early_or_unknown_file" and ".tmp-4242-stale-injected.json" in json.dumps(trb), "kind %s" % trb.get("kind"))
        time.sleep(3)
        check("the daemon paused behind that latch", os.path.exists(os.path.join(store, "PAUSE")))

        def clock_status():
            r = subprocess.run([PY, "-I", os.path.join(tree, "tools", "broadcast_run.py"), "status", "--root", run], capture_output=True, text=True, env=env)
            return json.loads(r.stdout)
        st_b = clock_status()
        pos_b = st_b.get("position_now")
        # ---- recovery drill: the documented procedure (fix the cause, release with a reason, resume with --guard); the broadcast position must be preserved
        os.unlink(stale)
        rel_r = subprocess.run([PY, "-I", rc, "release", "--guard", monitor_dir, "--reason", "live drill: planted stale temporary file removed, evidence kept"], capture_output=True, text=True, env=env)
        check("release of the latch with a reason", rel_r.returncode == 0 and not os.path.exists(os.path.join(monitor_dir, "TRIPPED")), (rel_r.stdout + rel_r.stderr).strip()[:160])
        res_r = subprocess.run([PY, "-I", os.path.join(tree, "tools", "broadcast_run.py"), "resume", "--root", run, "--guard", monitor_dir], capture_output=True, text=True, env=env)
        check("resume with --guard", res_r.returncode == 0 and not os.path.exists(os.path.join(store, "PAUSE")), (res_r.stdout + res_r.stderr).strip()[:160])
        time.sleep(6)
        st_r = clock_status()
        pos_r = st_r.get("position_now")
        check("the broadcast position was preserved across the pause (no jump forward, nothing replayed)", pos_b is not None and pos_r is not None and 0 < pos_r - pos_b < 20, "paused at %.1f, %.1f s later %.1f" % (pos_b or -1, 6.0, pos_r or -1))
        lines_r = log_lines(wlog)
        new_viol = [l for l in lines_r if l.get("violation") and l.get("at", 0) > time.time() - 5]
        check("no new finding after the resume", not new_viol, str([(v["violation"]) for v in new_viol]))
        # ---- phase C: unauthorized future file
        future = sorted(glob.glob(os.path.join(store, "feed", "releases", "*", "manifest.json")))[-1]
        rel0 = os.path.relpath(future, os.path.join(store, "feed"))
        target = os.path.join(public, rel0)
        pos_now = (log_lines(wlog)[-1].get("pos") if log_lines(wlog) else None)
        allowed_now = os.path.exists(target)
        notes.append("future file: %s (already public before the injection: %s; position %s)" % (rel0, allowed_now, pos_now))
        # C1: a file the daemon manages (a future release it will place later). The daemon repairs it within a tick, so this is informational: how long does a planted copy last?
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(os.path.join(store, "feed", rel0), target)
        t1 = time.time()
        while os.path.exists(target) and time.time() - t1 < 10 and not os.path.exists(os.path.join(monitor_dir, "TRIPPED")):
            time.sleep(0.1)
        notes.append("C1: a planted copy of a managed future file %s after %.1f s (daemon self-heal or monitor trip)" % ("was removed" if not os.path.exists(target) else "stayed", time.time() - t1))
        if os.path.exists(os.path.join(monitor_dir, "TRIPPED")):
            notes.append("C1 tripped the monitor already; the C2 check below is then not independent")
        # C2: an unauthorized future file of a name the daemon does not manage (what a bug that published early would leave): it stays, so the monitor must find it
        leak = os.path.join(public, "playback", "games", "results-leak.json")           # (inside a future release folder the daemon removes it again within a tick, as C1 shows)
        leak_rel = os.path.relpath(leak, public)
        tripped, attempts = False, 0
        t_c = time.time()
        os.makedirs(os.path.dirname(leak), exist_ok=True)
        shutil.copyfile(future, leak)
        attempts = 1
        while time.time() - t_c < 30 and not tripped:
            if os.path.exists(os.path.join(monitor_dir, "TRIPPED")):
                tripped = True
            else:
                time.sleep(0.25)
        tr = {}
        if tripped:
            try:
                tr = json.load(open(os.path.join(monitor_dir, "TRIPPED")))
            except (OSError, ValueError):
                pass
        check("unauthorized future file trips the monitor (early_or_unknown_file) and names that file", tripped and tr.get("kind") == "early_or_unknown_file" and "results-leak.json" in json.dumps(tr),
              "kind %s after %.1f s" % (tr.get("kind"), time.time() - t_c))
        time.sleep(3)
        check("the daemon paused behind the latch", os.path.exists(os.path.join(store, "PAUSE")))
        try:
            stj = json.load(open(os.path.join(public, "status.json")))
        except (OSError, ValueError):
            stj = {}
        check("status.json says paused", (stj.get("runner") or {}).get("state") == "paused", str((stj.get("runner") or {}).get("state")))
        check("trip evidence folder written", bool(glob.glob(os.path.join(monitor_dir, "evidence", "trip-*-early_or_unknown_file"))))
    finally:
        stop.set()
        for p in (dm, mon):                                           # daemon first, then the monitor (stopping the monitor under a running daemon would trip it)
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
                try:
                    p.wait(10)
                except subprocess.TimeoutExpired:
                    p.kill()
    lines = log_lines(wlog)
    errs = [l for l in lines if l.get("violation") == "monitor_error"]
    check("no monitor_error in the whole run", not errs, "%s" % [e.get("detail") for e in errs[:3]])
    kinds = sorted({l["violation"] for l in lines if l.get("violation")})
    notes.append("violation kinds logged: %s" % kinds)
    md = ["# Live drill of the patched rehearsal monitor (scratch)\n"] + ["* " + n for n in notes] + ["", "| expectation | result | detail |", "|---|---|---|"]
    md += ["| %s | %s | %s |" % (l.replace("|", "/"), "PASS" if ok else "**FAIL**", d.replace("|", "/")[:200]) for l, ok, d in results]
    open(os.path.join(work, "drill.md"), "w").write("\n".join(md) + "\n")
    bad = [r for r in results if not r[1]]
    print("\n%d of %d expectations held" % (len(results) - len(bad), len(results)))
    print("record:", os.path.join(work, "drill.md"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
