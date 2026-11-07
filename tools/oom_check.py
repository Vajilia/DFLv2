#!/usr/bin/env python3
"""Induced memory-limit kills of each recurring unit, on the real engine and a real finished season (docs/INTEGRATION_CANDIDATE_2026-11-07.md, section 11).

  oom_check.py --source ROOT --work DIR [--scenarios daemon-oom,daemon-sigkill,monitor-oom-restart,monitor-oom,builder-oom,supervisor-oom] [--cpu 0] [--out FILE]

TWO KINDS OF KILL, kept apart in the report (`kind`):
  cgroup_oom_kill   the KERNEL's memory-cgroup OOM killer: the unit's process runs in a memory group of the proposed MemoryMax (daemon 192M, monitor 512M, supervisor and builder 768M) and
                    a second process in the SAME group (the "hog") allocates until the group is over its limit. The kernel then picks a victim by itself. Lowering a process's oom_score_adj is not
                    permitted in this sandbox (no CAP_SYS_RESOURCE), so the hog cannot be made unkillable; instead the unit process that is meant to be the victim has its
                    oom_score_adj RAISED to 1000 (what `OOMScoreAdjust=1000` would do), which makes the kernel choose it before the hog. The kernel still does the killing. Evidence recorded: the group's `oom_kill` counter before and after, and the victim's wait status (killed by signal 9).
                    What is simulated is the LEAK, not the kill: no unit process here really leaks. (Under systemd the unit's group holds only the unit, so the same event happens
                    the moment the unit itself grows past MemoryMax.)
  sigkill           `kill -9` from outside, which proves a process can vanish but says nothing about the memory limit.
This host has cgroup v1 and no systemd, so SYSTEMD'S REACTION IS EMULATED by this harness and labelled so: after the kill it waits the unit's RestartSec (daemon 5 s, monitor 10 s, supervisor 30 s), terminates whatever else the unit
still has (OOMPolicy=stop, KillMode=control-group) and starts the unit's command again. Start limits (StartLimitBurst) are not exercised.

Per scenario: the three processes run for a while (stage 1 of tools/boundary_real.py puts a real finished season at 20 minutes before its end, so the broadcast is live), the public
folder is snapshotted, the kill is induced, the reaction is observed, the unit is restarted as above, and the files are checked: every earlier public file keeps its bytes
(tests/published_bytes.py), evidence files are untouched, the public folder and the store pass the backup tool's reference closure, the monitor's oracle finds no early result, and
the viewers' position never went back. Nothing is installed, deployed, sent or deleted outside the scratch folder.
"""
import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time

import psutil

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
import boundary_real as BR                         # noqa: E402
import measure_combined as MC                      # noqa: E402
import public_backup as PBK                        # noqa: E402
import published_bytes as PUB                      # noqa: E402
import rehearsal_check as RC                       # noqa: E402
from dflsite import broadcast as B, season as S    # noqa: E402

PY = sys.executable
HOG = r'''
import os, sys, time
chunks = []
step = 8 * 1024 * 1024
limit = int(sys.argv[1])
while len(chunks) * step < limit:
    b = bytearray(step)
    for i in range(0, step, 4096):
        b[i] = 1
    chunks.append(b)
    time.sleep(0.03)
time.sleep(600)
'''


def log(msg):
    print("%s  %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def oom_kills(group):
    s = MC.cg_read(os.path.join(group.mem, "memory.oom_control")) or ""
    for ln in s.splitlines():
        if ln.startswith("oom_kill ") and "disable" not in ln:
            return int(ln.split()[1])
    return None


class Scenario:
    def __init__(self, name, a, base_root, base_public):
        self.name, self.a = name, a
        self.work = os.path.join(a.work, name)
        self.root, self.public, self.guard = (os.path.join(self.work, n) for n in ("root", "public", "guard"))
        self.store = os.path.join(self.root, "store")
        self.checks, self.rep = [], {"scenario": name}
        os.makedirs(self.work)
        t = time.time()
        shutil.copytree(base_root, self.root, symlinks=True)
        shutil.copytree(base_public, self.public)
        os.makedirs(self.guard)
        self.rep["copy_s"] = round(time.time() - t, 1)
        self.samples, self.stop_sampler = [], False

    def check(self, name, ok, detail=""):
        self.checks.append({"check": name, "ok": bool(ok), "detail": detail})
        log("[%s] %s  %s%s" % (self.name, "PASS" if ok else "FAIL", name, ("  -- " + str(detail)[:300]) if detail else ""))

    # ------------------------------------------------------------------------------------------------------------------------------ processes
    def make(self):
        a = self.a
        mb = lambda v: int(v * 1024 * 1024)          # noqa: E731
        self.g = {"monitor": MC.Group("monitor", parent_limit=int(2 * 1024 ** 3), shares=1024, limit=mb(512)),
                  "daemon": MC.Group("daemon", shares=1024, limit=mb(192)),
                  "supervisor": MC.Group("supervisor", shares=205, limit=mb(768))}
        tools = lambda n: os.path.join(HERE, n)       # noqa: E731
        self.cap = ["--max-public-gb-per-season", "1.25", "--public-base-gb", "0.25"]
        self.argv = {
            "monitor": [PY, "-I", tools("rehearsal_check.py"), "watch", "--root", self.root, "--public", self.public, "--log", os.path.join(self.work, "watch.log"),
                        "--evidence", os.path.join(self.work, "evidence"), "--guard", self.guard, "--every", "10", "--full-every", "30", "--min-free-gb", "6"] + self.cap,
            "daemon": [PY, "-I", tools("broadcast_run.py"), "run", "--root", self.root, "--public", self.public, "--guard", self.guard, "--verbose"] + self.cap,
            "supervisor": [PY, "-I", tools("broadcast_supervisor.py"), "run", "--root", self.root, "--public", self.public, "--floor-gb", "8", "--step-margin-gb", "0.5", "--season-gb", "2",
                           "--interval", "30", "--public-cap-per-season-gb", "1.25", "--public-base-gb", "0.25"]}
        self.proc = {n: self.new_proc(n) for n in ("monitor", "daemon", "supervisor")}

    def new_proc(self, n):
        return MC.Proc(n, self.argv[n], self.g[n], self.a.cpu, nice={"monitor": 10, "daemon": 0, "supervisor": 15}[n], stdout=(n == "daemon"))

    def start_all(self):
        self.proc["daemon"].start()                      # the production order: daemon, then monitor, then supervisor
        time.sleep(3)
        self.proc["monitor"].start()
        self.proc["supervisor"].start()
        import threading
        threading.Thread(target=self.sampler, daemon=True).start()

    def sampler(self):
        while not self.stop_sampler:
            r = BR.clock_pos(self.root)
            if r:
                self.samples.append((r[2], r[0], r[1]))
            time.sleep(1.0)

    RESTART_SEC = {"daemon": 5.0, "monitor": 10.0, "supervisor": 30.0}          # the RestartSec= of the three unit templates

    def restart(self, n, after=None):
        """What systemd's Restart=on-failure, the unit's RestartSec= and OOMPolicy=stop would do (EMULATED): wait, terminate the rest of the unit's group, start the command again."""
        after = self.RESTART_SEC[n] if after is None else after
        time.sleep(after)
        grp = self.g[n]
        procs_file = os.path.join(grp.mem, "cgroup.procs")
        left = []
        try:
            left = [int(x) for x in open(procs_file).read().split()]
        except OSError:
            pass
        for pid in left:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        time.sleep(2)
        for pid in left:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        old = self.proc[n]
        self.proc[n] = self.new_proc(n)
        self.proc[n].start()
        return {"terminated_left_over_pids": left, "restarted_after_s": after, "emulated": "systemd Restart=on-failure RestartSec=%g + OOMPolicy=stop" % after}

    def induce(self, n, victims=None):
        """Over-fill the unit's memory group with an unkillable process until the kernel kills something. -> evidence."""
        g = self.g[n]
        before = {"oom_kill": oom_kills(g), "failcnt": MC.cg_read(os.path.join(g.mem, "memory.failcnt")), "usage_mb": round(int(MC.cg_read(os.path.join(g.mem, "memory.usage_in_bytes")) or 0) / 1e6, 1)}
        pids_before = {}
        try:
            pids_before = {int(x): psutil.Process(int(x)).name() + " " + " ".join(psutil.Process(int(x)).cmdline()[2:4]) for x in open(os.path.join(g.mem, "cgroup.procs")).read().split()}
        except (OSError, psutil.Error):
            pass
        chosen = list(pids_before) if victims is None else list(victims)
        adj_ok = {}
        for pid in chosen:
            try:
                with open("/proc/%d/oom_score_adj" % pid, "w") as f:
                    f.write("1000")                                    # raising is allowed; this is the process the kernel is to kill
                adj_ok[pid] = True
            except OSError as e:
                adj_ok[pid] = str(e)
        limit = g.limit
        hog = subprocess.Popen([PY, "-c", HOG, str(int(limit * 1.5))], preexec_fn=g.enter, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        t = time.time()
        killed_at = None
        while time.time() - t < 60:
            if (oom_kills(g) or 0) > (before["oom_kill"] or 0):
                killed_at = time.time()
                break
            time.sleep(0.2)
        time.sleep(1)
        hog.kill()
        hog.wait()
        after = {"oom_kill": oom_kills(g), "failcnt": MC.cg_read(os.path.join(g.mem, "memory.failcnt")), "limit_mb": round(limit / 1048576)}
        survivors = {}
        try:
            survivors = {int(x): True for x in open(os.path.join(g.mem, "cgroup.procs")).read().split()}
        except OSError:
            pass
        victims = [f"{pid} {name}" for pid, name in pids_before.items() if pid not in survivors]
        return {"kind": "cgroup_oom_kill", "group_oom_kill_before": before["oom_kill"], "group_oom_kill_after": after["oom_kill"], "failcnt_after": after["failcnt"], "limit_mb": after["limit_mb"],
                "usage_before_mb": before["usage_mb"], "victim_oom_score_adj_raised": adj_ok, "kernel_killed": killed_at is not None, "seconds_to_kill": round(killed_at - t, 1) if killed_at else None, "victims": victims}

    # ------------------------------------------------------------------------------------------------------------------------------ checks
    def monitor_log(self):
        try:
            with open(os.path.join(self.work, "watch.log")) as f:
                return [json.loads(x) for x in f if x.strip()]
        except OSError:
            return []

    def final_checks(self, snap, evidence_before, expected_kinds=()):
        problems = PUB.changed(snap, self.public, limit=30)
        self.check("every earlier public file keeps its bytes; indexes keep their earlier entries", not problems, problems[:3])
        ev_after = {rel: m for rel, m in BR.meta(os.path.join(self.work, "evidence")).items()} if os.path.isdir(os.path.join(self.work, "evidence")) else {}
        lost = [rel for rel, m in evidence_before.items() if ev_after.get(rel, (None,))[0:1] != m[0:1] and not rel.startswith("released/")]
        self.check("evidence files written before the kill are still there, unchanged in size", not lost, lost[:3])
        cp, cs = PBK.CLOSURE["public"](PBK.Tree(self.public)), PBK.CLOSURE["store"](PBK.Tree(self.store))
        self.check("public folder and private store pass the reference closure", not cp and not cs, list(cp)[:2] + list(cs)[:2])
        mlog = self.monitor_log()
        viol = [m for m in mlog if "violation" in m]
        kinds = sorted({m["violation"] for m in viol})
        unexpected = [k for k in kinds if k not in expected_kinds]
        self.check("the monitor reported only the violations this fault should cause %s" % (list(expected_kinds),), not unexpected, kinds)
        self.rep["monitor_violation_kinds"] = kinds
        pos = [(w, p) for w, p, _ in self.samples]
        back = [(w, p, q) for (w, p), (_, q) in zip(pos, pos[1:]) if q < p - 0.01]
        rates = [((q - p) / (w2 - w), w, p, w2, q) for (w, p), (w2, q) in zip(pos, pos[1:]) if w2 > w]
        worst = max(rates) if rates else (0, 0, 0, 0, 0)
        fast = worst[0]
        self.rep["fastest_pair"] = {"rate": round(worst[0], 3), "wall_gap_s": round(worst[3] - worst[1], 3), "position_gap_s": round(worst[4] - worst[2], 3)}
        self.check("viewers' position never went back and never ran faster than time (max %.2f/s)" % fast, not back and fast <= 1.6, back[:2])
        try:
            wa = RC.argparse.Namespace(root=self.root, public=self.public, url=None, log=os.path.join(self.work, "final.log"), evidence=os.path.join(self.work, "final-evidence"), every=1.0,
                                       full_every=1.0, min_free_gb=0.0, max_public_gb=0.0, max_public_gb_per_season=1.25, public_base_gb=0.25, max_public_ceiling_gb=0.0,
                                       guard=os.path.join(self.work, "final-guard"), canary=None, cgroup=None)
            os.makedirs(wa.guard, exist_ok=True)
            w = RC.Watch(wa)
            now = time.time()
            p = w.cheap(now)
            w.full(now, p)
            self.check("no early results: the oracle's final pass over the files finds nothing (position %.0f s)" % p, w.found == 0, w.found)
        except Exception as e:
            self.check("the oracle's final pass could run", False, "%s: %s" % (type(e).__name__, e))
        self.rep["viewer_position"] = {"samples": len(pos), "went_back": len(back), "max_rate": round(fast, 3), "last": pos[-1][1] if pos else None}

    def stop_all(self):
        self.stop_sampler = True
        for n in ("supervisor", "monitor", "daemon"):
            try:
                if self.proc[n].p is not None and self.proc[n].p.poll() is None:
                    self.proc[n].stop()
            except Exception:
                pass
        ev = BR.meta(os.path.join(self.work, "evidence")) if os.path.isdir(os.path.join(self.work, "evidence")) else {}
        self.rep["evidence"] = {"files": len(ev), "bytes": sum(m[0] for m in ev.values()), "names": sorted(ev)[:20]}
        self.rep["cgroups_end"] = {n: dict(g.pressure(), oom_kill=oom_kills(g), peak_mb=round((g.peak() or 0) / 1e6, 1)) for n, g in self.g.items()}
        for g in self.g.values():
            g.remove()
        self.rep["checks"] = self.checks
        self.rep["pass"] = all(c["ok"] for c in self.checks)

    # ------------------------------------------------------------------------------------------------------------------------------ scenarios
    def run(self):
        try:
            self.make()
            self.start_all()
            warm = 75
            time.sleep(warm)
            r0 = BR.clock_pos(self.root)
            self.check("warm-up: all three running, broadcast advancing", all(p.p.poll() is None for p in self.proc.values()) and r0 and r0[1] == "running", r0)
            snap = PUB.snapshot(self.public)
            ev0 = BR.meta(os.path.join(self.work, "evidence")) if os.path.isdir(os.path.join(self.work, "evidence")) else {}
            getattr(self, "do_" + self.name.replace("-", "_"))(snap, ev0)
        except Exception as e:
            import traceback
            self.check("scenario ran to the end", False, "%s: %s" % (type(e).__name__, e))
            traceback.print_exc()
        finally:
            self.stop_all()
        return self.rep

    def wait_for(self, cond, timeout, every=2.0):
        end = time.time() + timeout
        while time.time() < end:
            if cond():
                return True
            time.sleep(every)
        return False

    def do_daemon_oom(self, snap, ev0):
        self.daemon_kill(snap, ev0, "oom")

    def do_daemon_sigkill(self, snap, ev0):
        self.daemon_kill(snap, ev0, "sigkill")

    def daemon_kill(self, snap, ev0, how):
        d = self.proc["daemon"]
        pos_before = BR.clock_pos(self.root)
        if how == "oom":
            ev = self.induce("daemon")
            self.rep["kill"] = ev
            self.check("the kernel's memory-cgroup OOM killer killed the daemon (oom_kill counter rose, victim died by signal 9)", ev["kernel_killed"] and any("python" in v for v in ev["victims"]) and d.p.poll() == -signal.SIGKILL, ev)
        else:
            os.kill(d.p.pid, signal.SIGKILL)
            d.p.wait()
            self.rep["kill"] = {"kind": "sigkill"}
            self.check("the daemon was killed with SIGKILL from outside", d.p.returncode == -signal.SIGKILL)
        t_dead = time.time()
        time.sleep(20)                                                    # nobody renews the lease: the clock stands, the monitor notices
        pos_a = BR.clock_pos(self.root)
        time.sleep(10)
        pos_held = BR.clock_pos(self.root)
        self.check("safe hold: with the daemon dead the position stops (it moved %.1f s in the last 10 s, %.1f s since the kill)" % (pos_held[0] - pos_a[0], pos_held[0] - pos_before[0]),
                   abs(pos_held[0] - pos_a[0]) < 0.5 and pos_held[0] - pos_before[0] < 100, (pos_before, pos_a, pos_held))
        self.check("the monitor has not tripped (a dead daemon is a hold, not a spoiler)", not os.path.exists(os.path.join(self.guard, "TRIPPED")))
        self.rep["restart"] = self.restart("daemon")
        self.check("the restarted daemon is running", self.wait_for(lambda: self.proc["daemon"].p.poll() is None and BR.clock_pos(self.root)[1] == "running", 60))
        time.sleep(60)
        pos_after = BR.clock_pos(self.root)
        self.check("after the restart the position goes on from where the viewers' clocks stopped (+%.0f s)" % (pos_after[0] - pos_held[0]), pos_after[0] > pos_held[0] + 20, (pos_held, pos_after))
        log_lines = open(os.path.join(self.store, "clock_log.jsonl")).read().splitlines()
        self.rep["clock_log_tail"] = [json.loads(x) for x in log_lines[-3:]]
        self.check("the downtime is on record in the clock log", any(json.loads(x).get("why") == "downtime" for x in log_lines))
        self.final_checks(snap, ev0, expected_kinds=("daemon_silent",))

    def do_monitor_oom(self, snap, ev0):
        m, d = self.proc["monitor"], self.proc["daemon"]
        ev = self.induce("monitor")
        self.rep["kill"] = ev
        self.check("the kernel's memory-cgroup OOM killer killed the monitor (oom_kill counter rose, victim died by signal 9)", ev["kernel_killed"] and m.p.poll() == -signal.SIGKILL, ev)
        t_dead = time.time()
        pos0 = BR.clock_pos(self.root)
        # the daemon watches the monitor's heartbeat: after GUARD_STALE_S (120 s) of silence it trips itself and holds
        paused = self.wait_for(lambda: os.path.exists(os.path.join(self.guard, "TRIPPED")) or (BR.clock_pos(self.root) or (0, ""))[1] == "paused", 240, every=2)
        self.rep["seconds_until_daemon_held"] = round(time.time() - t_dead, 1)
        trip = None
        try:
            trip = json.load(open(os.path.join(self.guard, "TRIPPED")))
        except (OSError, ValueError):
            pass
        self.check("safe hold: with no monitor the daemon stops the broadcast by itself (TRIPPED written, clock paused)", paused and trip is not None, {"after_s": self.rep["seconds_until_daemon_held"], "trip": trip and {k: trip[k] for k in ("kind",) if k in trip}})
        time.sleep(15)
        p1 = BR.clock_pos(self.root)
        self.check("the position does not move while held", p1 and p1[1] == "paused" and abs(p1[0] - BR.clock_pos(self.root)[0]) < 0.5, p1)
        self.check("the daemon process is still alive (held, not dead)", d.p.poll() is None)
        ev_trip = open(os.path.join(self.guard, "TRIPPED")).read() if os.path.exists(os.path.join(self.guard, "TRIPPED")) else ""
        self.rep["restart"] = self.restart("monitor")
        time.sleep(40)
        self.check("the restarted monitor is running and has not found a violation", self.proc["monitor"].p.poll() is None and not [x for x in self.monitor_log() if "violation" in x])
        still = os.path.exists(os.path.join(self.guard, "TRIPPED"))
        self.check("the broadcast does NOT resume by itself after the monitor is back (a person must release and resume)", still and (BR.clock_pos(self.root) or (0, ""))[1] == "paused")
        r = subprocess.run([PY, "-I", os.path.join(HERE, "rehearsal_check.py"), "release", "--guard", self.guard, "--reason", "oom_check: monitor was OOM-killed (induced), restarted, nothing wrong with the broadcast",
                            "--evidence", os.path.join(self.work, "evidence")], capture_output=True, text=True)
        self.check("release keeps the trip as evidence", r.returncode == 0 and os.path.isdir(os.path.join(self.work, "evidence", "released")) and bool(os.listdir(os.path.join(self.work, "evidence", "released"))), (r.stdout + r.stderr)[-200:])
        r = subprocess.run([PY, "-I", os.path.join(HERE, "broadcast_run.py"), "resume", "--root", self.root, "--guard", self.guard], capture_output=True, text=True)
        self.check("resume works after the release", r.returncode == 0, (r.stdout + r.stderr)[-200:])
        time.sleep(45)
        p2 = BR.clock_pos(self.root)
        self.check("the broadcast is running again from the held position (+%.0f s)" % (p2[0] - p1[0]), p2[1] == "running" and p2[0] > p1[0] + 10, (p1, p2))
        released = os.listdir(os.path.join(self.work, "evidence", "released")) if os.path.isdir(os.path.join(self.work, "evidence", "released")) else []
        self.rep["evidence_released"] = released
        self.final_checks(snap, ev0, expected_kinds=("monitor_silent",))

    def do_monitor_oom_restart(self, snap, ev0):
        """The monitor is OOM-killed and its unit restarts it after RestartSec=10, well inside the daemon's 120 s patience: the broadcast must not even notice."""
        m, d = self.proc["monitor"], self.proc["daemon"]
        n0 = len([x for x in self.monitor_log() if x.get("start")])
        ev = self.induce("monitor")
        self.rep["kill"] = ev
        self.check("the kernel's memory-cgroup OOM killer killed the monitor (oom_kill counter rose, victim died by signal 9)", ev["kernel_killed"] and m.p.poll() == -signal.SIGKILL, ev)
        self.rep["restart"] = self.restart("monitor")
        time.sleep(75)
        log_ = self.monitor_log()
        self.check("the restarted monitor started again (a second `start` record) and is sending heartbeats", len([x for x in log_ if x.get("start")]) == n0 + 1 and self.proc["monitor"].p.poll() is None and log_[-1].get("heartbeat"), log_[-1:])
        self.check("the daemon never held: no TRIPPED, the clock was running at every sample since the kill", not os.path.exists(os.path.join(self.guard, "TRIPPED")) and all(st == "running" for w, p, st in self.samples if w > time.time() - 90), "")
        self.check("the daemon process kept running", d.p.poll() is None)
        self.final_checks(snap, ev0, expected_kinds=())

    def builder_state(self):
        try:
            return json.loads(subprocess.run([PY, "-I", os.path.join(HERE, "broadcast_build.py"), "status", "--root", self.root], capture_output=True, text=True).stdout)
        except ValueError:
            return {}

    def sup_health(self):
        try:
            return B.get(os.path.join(self.root, "supervisor", "health.json"))
        except (OSError, ValueError):
            return {}

    def watch_health(self):
        """Poll the supervisor's health twice a second and keep every distinct builder state seen, so a failure that is recorded and cleared again within a minute is still caught."""
        import threading
        self.health_obs = []

        def loop():
            last = None
            while not self.stop_sampler:
                h = self.sup_health()
                b = h.get("builder") or {}
                key = (b.get("state"), b.get("consecutive_failures"), (b.get("last_error") or "")[:160], b.get("next_attempt_at") is not None, h.get("status"))
                if key != last:
                    self.health_obs.append({"t": round(time.time(), 1), "state": key[0], "consecutive_failures": key[1], "last_error": key[2], "backing_off": key[3], "status": key[4]})
                    last = key
                time.sleep(0.5)
        threading.Thread(target=loop, daemon=True).start()

    def group_procs(self, n):
        out = []
        try:
            for x in open(os.path.join(self.g[n].mem, "cgroup.procs")).read().split():
                try:
                    q = psutil.Process(int(x))
                    out.append({"pid": q.pid, "ppid": q.ppid(), "cmd": " ".join(q.cmdline()[1:5])[:110]})
                except psutil.Error:
                    pass
        except OSError:
            pass
        return out

    def do_builder_oom(self, snap, ev0):
        """The OOM killer is made to pick the real engine step, not the supervisor. The supervisor must notice, back off, and carry on. (Under systemd with OOMPolicy=stop the whole unit would be
        stopped and restarted instead: that is the supervisor-oom scenario. This one shows what the supervisor itself does when only the step dies.)"""
        sup = self.proc["supervisor"]
        self.watch_health()
        busy = self.wait_for(lambda: bool(psutil.Process(sup.p.pid).children(recursive=True)), 300, every=1)
        self.check("a builder step is running under the supervisor", busy)
        built0 = BR_built(self.root)
        before = self.group_procs("supervisor")
        ev = self.induce("supervisor", victims=[q.pid for q in psutil.Process(sup.p.pid).children(recursive=True)])
        self.rep["kill"] = ev
        self.rep["group_before"] = before
        self.check("the kernel's memory-cgroup OOM killer killed the builder step, not the supervisor", ev["kernel_killed"] and sup.p.poll() is None and not any(v.split()[0] == str(sup.p.pid) for v in ev["victims"]) and bool(ev["victims"]), ev)
        time.sleep(2)
        self.rep["group_2s_after"] = self.group_procs("supervisor")
        time.sleep(100)
        self.rep["group_100s_after"] = self.group_procs("supervisor")
        obs = self.health_obs
        self.rep["health_observations"] = obs
        failed = [o for o in obs if (o["consecutive_failures"] or 0) >= 1 or o["last_error"]]
        self.check("the supervisor recorded the failed step in its health (consecutive_failures or last_error was set at some point)", bool(failed), failed[:2])
        self.check("it backed off before trying again", any(o["backing_off"] for o in obs if (o["consecutive_failures"] or 0) >= 1), "")
        ok = self.wait_for(lambda: BR_built(self.root) >= built0 + 1, 420, every=5)
        self.check("the builder builds again (events built %d -> %d)" % (built0, BR_built(self.root)), ok)
        recovered = [o for o in obs[-3:] if not o["consecutive_failures"] and not o["last_error"]]
        self.check("the failure is cleared from the health once a step succeeds", bool(recovered), obs[-1:])
        st = self.builder_state()
        self.rep["builder_status"] = {k: st.get(k) for k in ("season", "events_built", "games_built") if k in st}
        self.rep["season_folders"] = sorted(os.listdir(os.path.join(self.store, "seasons")))
        orphans = [q for q in self.rep["group_2s_after"] if q["ppid"] == 1]
        self.rep["orphans_2s_after_kill"] = orphans
        self.check("no orphaned engine process is left running after the kill (%d seen at +2 s)" % len(orphans), not [q for q in self.rep["group_100s_after"] if q["ppid"] == 1], orphans[:2])
        self.check("the daemon and the monitor were not disturbed (no violation, no trip, both running)", all(self.proc[n].p.poll() is None for n in ("daemon", "monitor")) and not os.path.exists(os.path.join(self.guard, "TRIPPED")))
        self.final_checks(snap, ev0, expected_kinds=())

    def do_supervisor_oom(self, snap, ev0):
        """The supervisor process itself is the victim (its oom_score_adj is raised so the kernel picks it)."""
        sup = self.proc["supervisor"]
        busy = self.wait_for(lambda: bool(psutil.Process(sup.p.pid).children(recursive=True)), 300, every=1)
        built0 = BR_built(self.root)
        ev = self.induce("supervisor", victims=[sup.p.pid])
        self.rep["kill"] = ev
        self.check("the kernel's memory-cgroup OOM killer killed the SUPERVISOR process (oom_kill counter rose, died by signal 9)", ev["kernel_killed"] and sup.p.poll() == -signal.SIGKILL, ev)
        left = []
        try:
            left = [int(x) for x in open(os.path.join(self.g["supervisor"].mem, "cgroup.procs")).read().split()]
        except OSError:
            pass
        self.rep["left_in_group_after_kill"] = len(left)
        self.check("the daemon and the monitor are untouched by it (separate groups)", all(self.proc[n].p.poll() is None for n in ("daemon", "monitor")))
        self.rep["restart"] = self.restart("supervisor")
        ok = self.wait_for(lambda: BR_built(self.root) >= built0 + 2, 480, every=5)
        self.check("the restarted supervisor takes the lock back and the builder builds again (events built %d -> %d)" % (built0, BR_built(self.root)), ok)
        h = self.sup_health()
        self.rep["health_end"] = {k: h.get(k) for k in ("status", "builder", "problems")}
        st = self.builder_state()
        self.rep["builder_status"] = {k: st.get(k) for k in ("season", "events_built", "games_built") if k in st}
        self.final_checks(snap, ev0, expected_kinds=())


def BR_built(root):
    return MC.builds_count(root)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--scenarios", default="daemon-oom,daemon-sigkill,monitor-oom-restart,monitor-oom,builder-oom,supervisor-oom")
    ap.add_argument("--hold-start-s", type=float, default=1200.0, help="how long before the end of season 1 the broadcast stands when the units start")
    ap.add_argument("--cpu", type=int, default=0)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    a.work = os.path.abspath(a.work)
    if os.path.exists(a.work):
        sys.exit("--work must not exist: %s" % a.work)
    os.makedirs(a.work)
    base = os.path.join(a.work, "base")
    os.makedirs(base)
    base_root, base_public = os.path.join(base, "root"), os.path.join(base, "public")
    shutil.copytree(a.source, base_root, symlinks=True)
    open(os.path.join(base_root, "store", "continuous"), "w").close()
    os.makedirs(base_public)
    plan1 = B.get(os.path.join(base_root, "store", "plan.json"))
    end1 = float(plan1["total_s"])
    B.FSYNC = False
    bc = S.SeasonBroadcaster(os.path.join(base_root, "store"), base_public, guard_publication=False)
    ff_end = end1 - a.hold_start_s
    start = time.time() - ff_end
    bc.clock.start(start, 0.0, B.LEASE_S)
    t = 0.0
    while t < ff_end:
        bc.tick(start + t)
        t += 20.0
    st = bc.clock.state()
    bc.clock._write(dict(st, ready_until=B.position(st, start + t) + B.DELAY_S), "fast_forward_end", start + t)
    B.FSYNC = True
    del bc
    log("base ready: position %.0f s of %.0f (virtual time; the units below run in real time from here)" % (ff_end, end1))
    os.sched_setaffinity(0, {c for c in range(os.cpu_count()) if c != a.cpu} or {a.cpu})
    results = []
    for name in a.scenarios.split(","):
        log("=== scenario %s ===" % name)
        sc = Scenario(name, a, base_root, base_public)
        results.append(sc.run())
        shutil.rmtree(sc.work, ignore_errors=True)         # scratch only: keeps the disk free for the next scenario (the report keeps the evidence that matters)
    report = {"scenarios": results, "pass": all(r.get("pass") for r in results)}
    if a.out:
        with open(a.out, "w") as f:
            json.dump(report, f, indent=1, sort_keys=True, default=str)
    log("RESULT: %s" % ("PASS" if report["pass"] else "FAIL"))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
