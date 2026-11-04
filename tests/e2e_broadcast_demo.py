#!/usr/bin/env python3
"""Real-browser check of the broadcast protocol demo (needs Playwright + Chromium; not part of the unit-test run).

  python3 tests/e2e_broadcast_demo.py [--slot-s 150] [--no-browser] [--games DIR]

--no-browser   the viewers are small HTTP clients that do exactly what the page does (for a server without Chromium)
--games DIR    use the first game of a real playback directory (DIR/index.json) instead of the test fixture

Starts the demo daemon on a free port with a short lease, opens two browser contexts (viewer 1 from the start, viewer 2 joining late), and checks, in a real
browser: both show the same play for the same broadcast position (also against the reference viewer, computed from the public files alone); a refresh returns to the
current position; pause stops both; resume carries on from the paused position; kill -9 of the daemon freezes both at the lease end and the restart carries on
without a jump; the pages never ask for a file the server has not released. Prints one line per check and exits 1 on the first failure.
"""
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
from dflsite import broadcast as B  # noqa: E402
import test_broadcast as T  # noqa: E402
import urllib.request  # noqa: E402
import urllib.error  # noqa: E402
import email.utils  # noqa: E402

TOOL = os.path.join(ROOT, "tools", "broadcast_demo.py")
OK = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (" " + str(detail) if detail else ""), flush=True)
    OK.append(bool(cond))
    if not cond:
        raise SystemExit(1)


def sample(page):
    return page.evaluate("() => [parseFloat(document.body.getAttribute('data-pos')), parseInt(document.body.getAttribute('data-play')), document.body.getAttribute('data-state')]")


class HttpViewer:
    """The demo page's logic in Python: it knows only what the public HTTP files say."""

    def __init__(self, base):
        self.base, self.clock, self.offset, self.synced = base, None, 0.0, 0.0
        self.sched = None
        self.segs = {}
        self.head = None

    def _get(self, path):
        try:
            r = urllib.request.urlopen(self.base + path)
            return r.status, json.loads(r.read()), r.headers.get("Date")
        except urllib.error.HTTPError as e:
            return e.code, None, None
        except urllib.error.URLError:               # the server is down: the viewer keeps the clock it has (which stops at its lease end)
            return 0, None, None

    def status(self, path):
        return self._get(path)[0]

    def sync(self):
        t0 = time.time()
        st, j, date = self._get("/broadcast/clock.json")
        if st == 200:
            self.clock = j
            if date:
                self.offset = email.utils.parsedate_to_datetime(date).timestamp() + 0.5 + (time.time() - t0) / 2 - time.time()
            self.synced = time.time()

    def pos(self):
        c = self.clock
        if time.time() - self.synced > 2:
            self.sync()
            c = self.clock
            self.synced = max(self.synced, time.time() - 1.0)
        if c["state"] != "running":
            return c["anchor_pos"]
        return c["anchor_pos"] + max(0.0, min(time.time() + self.offset, c["lease_until"]) - c["anchor_wall"])

    def goto(self):
        self.sync()
        self.sched = self._get("/broadcast/schedule.json")[1]
        self.segs, self.head = {}, None

    reload = goto

    def sample(self):
        pos = self.pos()
        gid = self.sched["windows"][0]["games"][0]["gid"]
        d = "/broadcast/live/" + B.safe(gid) + "/"
        st, man, _ = self._get(d + "manifest.json")
        if st != 200:
            return [pos, -1, "scheduled"]
        if self.head is None:
            self.head = self._get(d + "head.json")[1]
        for i in range(man["segments"]):
            if i not in self.segs:
                self.segs[i] = self._get(d + "s%04d.json" % i)[1]
        plays = [p for i in sorted(self.segs) for p in self.segs[i]["plays"]]
        tau = pos - man["start_s"] - man["delay_s"]
        idx = max([i for i, p in enumerate(plays) if p["t0"] <= tau] or [-1])
        return [round(pos, 2), idx, "live"]


class BrowserViewer:
    def __init__(self, ctx, base, requested):
        self.page, self.base = ctx.new_page(), base
        self.page.on("response", lambda r: requested.append((r.url, r.status)))

    def goto(self):
        self.page.goto(self.base + "/demo/")

    def reload(self):
        self.page.reload()

    def sample(self):
        return sample(self.page)

    def status(self, path):
        return self.page.evaluate("async (p) => (await fetch(p)).status", path)


def main():
    slot = 150
    if "--slot-s" in sys.argv:
        slot = float(sys.argv[sys.argv.index("--slot-s") + 1])
    browser = "--no-browser" not in sys.argv
    tmp = tempfile.mkdtemp()
    try:
        if "--games" in sys.argv:
            d = sys.argv[sys.argv.index("--games") + 1]
            idx = json.load(open(os.path.join(d, "index.json")))
            game = json.load(open(os.path.join(d, idx["games"][0]["file"])))
        else:
            game = T.export_real_game(tmp)
        store, public = os.path.join(tmp, "store"), os.path.join(tmp, "public")
        os.makedirs(store)
        gf = os.path.join(tmp, "g.json")
        json.dump(game, open(gf, "w"))
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        common = ["--store", store, "--public", public]
        sh = lambda *a: subprocess.run([sys.executable, "-I", TOOL, *a], capture_output=True, text=True, check=True)
        sh("prepare", *common, "--slot-s", str(slot), "--window", "8:" + gf)
        sh("start", *common, "--lease-s", "6")
        args = [sys.executable, "-I", TOOL, "run", *common, "--port", str(port), "--tick", "0.3", "--lease-s", "6"]
        daemon = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = "http://127.0.0.1:%d" % port
        gid = game["gid"]
        for _ in range(50):
            try:
                urllib.request.urlopen(base + "/broadcast/clock.json")
                break
            except OSError:
                time.sleep(0.2)
        requested, pw, br = [], None, None
        if browser:
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start()
            br = pw.chromium.launch(args=["--disable-renderer-backgrounding", "--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows"])
            mk = lambda: BrowserViewer(br.new_context(), base, requested)
        else:
            mk = lambda: HttpViewer(base)
        v1 = mk()
        v1.goto()
        time.sleep(14)                       # kickoff at 8 s; viewer 1 has watched for a few seconds
        v2 = mk()                            # viewer 2 joins late (in the browser mode: in its own browser context)
        v2.goto()
        time.sleep(6)
        a, b = v1.sample(), v2.sample()
        check("both viewers are in the game", a[1] >= 0 and b[1] >= 0, (a, b))
        check("their clocks agree within half a second", abs(a[0] - b[0]) < 0.6, (a, b))
        for who, (pos, idx, st) in (("viewer 1", a), ("late joiner", b)):
            ref = B.viewer_state(public, gid, pos)
            check("%s shows the play the reference viewer computes from the public files" % who, ref["idx"] == idx, (pos, idx, ref["idx"]))
        before = v1.sample()
        v1.reload()
        time.sleep(4)
        after = v1.sample()
        ref = B.viewer_state(public, gid, after[0])
        check("after a refresh the viewer is at the current position, not at the start", after[0] > before[0] and after[1] >= before[1] and after[1] == ref["idx"], (before, after, ref["idx"]))
        sh("pause", *common)
        time.sleep(12)
        pa, pb = v1.sample(), v2.sample()
        time.sleep(4)
        pa2, pb2 = v1.sample(), v2.sample()
        check("pause stops both viewers", pa[0] == pa2[0] and pb[0] == pb2[0] and abs(pa[0] - pb[0]) < 1e-6, (pa, pa2, pb, pb2))
        sh("resume", *common, "--lease-s", "6")
        time.sleep(12)
        ra, rb = v1.sample(), v2.sample()
        check("resume continues from the paused position", 0 < ra[0] - pa[0] < 16 and abs(ra[0] - rb[0]) < 0.8, (pa, ra, rb))
        os.kill(daemon.pid, signal.SIGKILL)
        daemon.wait()
        killed_at = time.time()
        time.sleep(9)
        fa = v1.sample()
        time.sleep(3)
        fa2 = v1.sample()
        check("with the daemon dead, the clock stops at the lease end", fa[0] == fa2[0], (fa, fa2))
        daemon = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(14)
        ga, gb = v1.sample(), v2.sample()
        gap = time.time() - killed_at
        check("after the restart the position carries on from where it stopped (no catch-up)", 0 < ga[0] - fa[0] < 16 and abs(ga[0] - gb[0]) < 0.8 and ga[0] < ra[0] + gap - 6, (fa, ga, gb, gap))
        log = [json.loads(x)["why"] for x in open(os.path.join(store, "clock_log.jsonl"))]
        check("the gap is in the clock's log as downtime", "downtime" in log, log)
        if browser:
            bad = [(u, s_) for u, s_ in requested if s_ >= 400 and "favicon" not in u and "/live/" in u and not u.endswith("manifest.json") and not u.endswith("head.json")]
            check("no page asked for a data file the server had not released", not bad, bad[:3])
        d = "/broadcast/live/" + B.safe(gid) + "/"
        res = {"s0099.json": v1.status(d + "s0099.json"), "end.json": v1.status(d + "end.json"), "store": v1.status("/store/schedule.json"),
               "traversal": v1.status("/broadcast/../../store/clock.json")}
        check("direct requests for unreleased data are 404", res == {"s0099.json": 404, "end.json": 404, "store": 404, "traversal": 404}, res)
        if br:
            br.close()
            pw.stop()
        daemon.send_signal(signal.SIGTERM)
        daemon.wait(timeout=10)
    finally:
        shutil.rmtree(tmp, True)
    print("ALL %d CHECKS PASSED" % len(OK))


if __name__ == "__main__":
    main()
