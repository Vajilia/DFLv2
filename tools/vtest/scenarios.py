"""What every stage does with the machine: units, the guard folder, loaders, the sampler. Shared by the four stage modules; every action goes through the Env facade."""
import json
import os
import re
import sys
import time

from common import EVID, GUARD, PUBLIC, ROOT_DIR, LOADDIR, SLICE, UNIT, HERE, StageStop, sha256_file, jsonl_read

MUTABLE = ("latest.json", "status.json", "clock.json", "schedule.json", "index.json", "manifest.json")        # the no-store group: these change by design
PYTHON = "/usr/bin/python3"


class Proc:
    """A loader or sampler the harness started."""
    def __init__(self, popen, log, name):
        self.p, self.log, self.name = popen, log, name

    def alive(self):
        return self.p.poll() is None

    def pid(self):
        return self.p.pid

    def stop(self, wait=10):
        if self.alive():
            self.p.terminate()
            try:
                self.p.wait(timeout=wait)
            except Exception:
                self.p.kill()
        return self.p.returncode

    def records(self):
        return jsonl_read(self.log)


class Ctx:
    def __init__(self, env, rec, cfg, tree, stage_dir):
        self.env, self.rec, self.cfg, self.tree, self.dir = env, rec, cfg, tree, stage_dir
        self.procs = []
        os.makedirs(stage_dir, exist_ok=True)

    # ---- time
    def now(self):
        return self.env.now()

    def wait(self, pred, timeout, every=1.0):
        """-> (value or None, seconds waited): the first truthy result of pred() within timeout."""
        t0 = self.now()
        while True:
            v = pred()
            if v:
                return v, self.now() - t0
            if self.now() - t0 >= timeout:
                return None, self.now() - t0
            self.env.sleep(every)

    # ---- units
    def cg(self, k):
        return "%s/%s" % (SLICE, UNIT[k])

    def show(self, k, props=("ActiveState", "SubState", "MainPID", "NRestarts", "Result", "ActiveEnterTimestampMonotonic")):
        rc, out, _ = self.env.sc("show", UNIT[k], *["-p" + p for p in props])
        d = {}
        for l in out.splitlines():
            if "=" in l:
                a, b = l.split("=", 1)
                d[a] = int(b) if b.isdigit() else b
        return d

    def active(self, k):
        return self.show(k, ("ActiveState",)).get("ActiveState") == "active"

    def start(self, *ks):
        for k in ks:
            self.env.sc("start", UNIT[k])
        v, _ = self.wait(lambda: all(self.active(k) for k in ks), 60)
        if not v:
            raise StageStop("units did not become active: %s" % ", ".join(k for k in ks if not self.active(k)))

    # ---- the guard folder and the broadcast
    def latch(self):
        p = GUARD + "/TRIPPED"
        if not self.env.exists(p):
            return None, None
        try:
            return json.loads(self.env.read(p)), sha256_file(p)
        except ValueError:
            return {"unreadable": True}, sha256_file(p)

    def paused(self):
        return self.env.exists(ROOT_DIR + "/store/PAUSE")

    def broadcast_status(self):
        rc, out, _ = self.env.run([PYTHON, "-I", self.tree + "/tools/broadcast_run.py", "status", "--root", ROOT_DIR])
        try:
            return json.loads(out)
        except ValueError:
            return {}

    def position(self):
        return self.broadcast_status().get("position_now")

    def heartbeat_age(self):
        try:
            return self.now() - os.path.getmtime(GUARD + "/heartbeat")
        except OSError:
            return None

    def watch_lines(self):
        return jsonl_read(GUARD + "/watch.log")

    def public_inventory(self, immutable_only=True):
        out = {}
        for d, _, names in os.walk(PUBLIC):
            for n in names:
                if immutable_only and (n in MUTABLE or n.endswith((".tmp", ".part"))):
                    continue
                p = os.path.join(d, n)
                try:
                    out[os.path.relpath(p, PUBLIC)] = (os.path.getsize(p), sha256_file(p))
                except OSError:
                    pass
        return out

    def partial_files(self):
        return [os.path.relpath(os.path.join(d, n), PUBLIC) for d, _, ns in os.walk(PUBLIC) for n in ns if n.endswith((".tmp", ".part")) or n.startswith(".tmp")]

    def release_and_resume(self, reason):
        """The documented two-step procedure. -> (release rc, resume rc, outputs)."""
        r1 = self.env.run([PYTHON, "-I", self.tree + "/tools/rehearsal_check.py", "release", "--guard", GUARD, "--reason", reason, "--evidence", GUARD + "/evidence"])
        r2 = self.env.run([PYTHON, "-I", self.tree + "/tools/broadcast_run.py", "resume", "--root", ROOT_DIR, "--guard", GUARD])
        return r1[0], r2[0], (r1[1] + r1[2] + r2[1] + r2[2])[-400:]

    def ensure_baseline(self, reason="vtest: reset to the baseline between scenarios"):
        """Units active and no latch. If a latch exists it is cleared the documented way (release + resume), never by deleting it."""
        for k in ("daemon", "monitor"):
            self.env.sc("reset-failed", UNIT[k])           # the start-limit counter must not carry over from earlier scenarios
        self.start("site", "daemon", "monitor")
        lat, _ = self.latch()
        if lat:
            self.wait(lambda: (self.heartbeat_age() or 999) < 20, 60)
            r1, r2, out = self.release_and_resume(reason)
            if r1 or r2:
                raise StageStop("could not clear the latch the documented way (release rc %s, resume rc %s): %s" % (r1, r2, out))
        self.wait(lambda: not self.paused(), 30)
        v, _ = self.wait(lambda: (self.heartbeat_age() or 999) < 20, 60)
        if not v:
            raise StageStop("the monitor's heartbeat is not fresh")

    # ---- helpers that start things
    def sampler(self, k, name, seconds=0):
        out = os.path.join(self.dir, name + ".jsonl")
        p = self.env.popen([sys.executable, os.path.join(HERE, "sampler.py"), "--cgroup", self.cg(k), "--out", out, "--tree", self.tree] + (["--seconds", str(seconds)] if seconds else []))
        pr = Proc(p, out, name)
        self.procs.append(pr)
        return pr

    def loader(self, kind, k, name, **kw):
        log = os.path.join(self.dir, name + ".jsonl")
        argv = [sys.executable, os.path.join(HERE, "loaders.py"), kind, "--cgroup", self.cg(k), "--log", log]
        for a, b in kw.items():
            argv += ["--" + a.replace("_", "-"), str(b)]
        pr = Proc(self.env.popen(argv), log, name)
        self.procs.append(pr)
        return pr

    def dataset(self, nbytes):
        free = self.env.free_bytes(LOADDIR)
        if free < nbytes + (2 << 30):
            raise StageStop("not enough free disk for a %d MB dataset (free %d MB, 2 GB kept in reserve)" % (nbytes >> 20, free >> 20))
        rc, out, err = self.env.run([sys.executable, os.path.join(HERE, "loaders.py"), "dataset", "--dir", LOADDIR, "--bytes", str(nbytes)], timeout=1800)
        if rc:
            raise StageStop("dataset creation failed: " + err[-200:])

    def limit_of(self, k):
        v = (self.env.read(self.cg(k) + "/memory.max") or "").strip()
        return int(v) if v.isdigit() else None

    def stop_procs(self):
        for p in self.procs:
            p.stop()

    def journal(self, k, since_epoch):
        rc, out, _ = self.env.run(["journalctl", "-u", UNIT[k], "--since", "@%d" % int(since_epoch), "--no-pager", "-o", "cat"], timeout=60)
        return out

    def daemon_pids(self):
        return [int(x) for x in (self.env.read(self.cg("daemon") + "/cgroup.procs") or "").split() if x.isdigit()]

    def oom_adj_of(self, pids):
        """{pid: oom_score_adj or None}"""
        out = {}
        for p in pids:
            v = (self.env.read("/proc/%d/oom_score_adj" % p) or "").strip()
            out[p] = int(v) if v.lstrip("-").isdigit() else None
        return out

    def prefer_daemon_as_oom_victim(self):
        """Test-only: make the processes now inside the daemon's disposable cgroup the OOM killer's preferred victims (oom_score_adj 1000), so the daemon, not the injected load, is what the kernel kills.
        Raises only; only pids of that cgroup; the loader is not touched (it is not in the cgroup yet and keeps the default); undone by the restart. -> {pid: {"ok", "readback"}}"""
        res = {}
        for p in self.daemon_pids():
            ok, err = self.env.oom_adjust(p, 1000)
            res[p] = {"ok": ok, "errno": err, "readback": self.oom_adj_of([p])[p]}
        return res

    def kernel_log(self, since_epoch):
        """The kernel log since a time (read-only): where the OOM killer names its victim. '' when it cannot be read (the victim is then reported as unknown)."""
        rc, out, _ = self.env.run(["journalctl", "-k", "--since", "@%d" % int(since_epoch), "--no-pager", "-o", "cat"], timeout=60)
        if not (out or "").strip():
            rc, out, _ = self.env.run(["dmesg"], timeout=30)
        return out or ""

    def mark(self, name, **data):
        """An event line in the stage's own timeline file."""
        with open(os.path.join(self.dir, "timeline.jsonl"), "a") as f:
            f.write(json.dumps(dict(data, event=name, t=round(self.now(), 3)), sort_keys=True) + "\n")


def latest_loader_tick(recs):
    ticks = [r for r in recs if r.get("event") in ("tick", "at_target", "end")]
    return ticks[-1] if ticks else None


def produce_plateau_latch(ctx, name, rate=5e6, wait_s=300):
    """Make the monitor write a real memory latch: an anonymous loader in the daemon's cgroup held at 93% of MemoryMax. -> (latch or None, loader summary).
    The loader is stopped as soon as the latch exists. Used by V4 (V2a does the same with more observation)."""
    import evaluate as E
    mx = ctx.limit_of("daemon")
    smp = ctx.sampler("daemon", name + "_cgroup")
    ctx.env.sleep(10)
    base = E.median([r["pinned"] for r in E.readable(smp.records())]) or 0
    target = int(0.93 * mx - base)
    ld = ctx.loader("anon", "daemon", name + "_anon", target_bytes=target, rate_bps=rate)
    lat, _ = ctx.wait(lambda: ctx.latch()[0] or (None if ld.alive() else "DEAD"), target / rate + wait_s, every=2.0)
    ld.stop()
    ctx.env.sleep(5)
    smp.stop()
    latch, _ = ctx.latch()
    return latch, E.loader_summary(ld.records(), ctx.now(), False)
