#!/usr/bin/env python3
"""An isolated demonstration of scheduled, synchronized, spoiler-protected game broadcasts (docs/BROADCAST_PLAN.md). It touches nothing of the league or the site.

  broadcast_demo.py prepare --store S --public P --slot-s N (--window START_S:PATH[,PATH...])... [--feed-root F --reveal-feed-at SEC]
  broadcast_demo.py start|pause|resume|status --store S --public P
  broadcast_demo.py run --store S --public P [--port N] [--tick 1.0]

`prepare` reads finished `dfl-public-playback` game files (a COPY of the archive's replays), builds the private store (segments, endings, full replays) and an empty
public tree. `run` is the daemon: it holds one lock, keeps the clock's lease, releases what the clock allows and serves the public tree (and the demo page at /demo/)
read-only over HTTP. Kill it with any signal at any time: the next `run` records the gap as a pause and carries on (docs/BROADCAST_PLAN.md, "Downtime").
"""
import argparse
import fcntl
import http.server
import json
import os
import posixpath
import shutil
import signal
import socketserver
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dflsite import broadcast as B  # noqa: E402

WEB = os.path.join(ROOT, "tools", "broadcast_demo_web")


def prepare(store, public, windows, slot_s, feed_root=None, reveal_at=None):
    if os.path.exists(os.path.join(store, "schedule.json")):
        raise SystemExit("the store already holds a schedule; use a fresh directory")
    os.makedirs(os.path.join(public, "broadcast"), exist_ok=True)
    wins = []
    for i, spec in enumerate(windows):
        start, paths = spec.split(":", 1)
        games = []
        for p in paths.split(","):
            with open(p, encoding="utf-8") as f:
                g = json.load(f)
            rec = B.store_game(store, g, slot_s)
            games.append({"gid": g["gid"], "away": g["teams"]["away"]["abbr"], "home": g["teams"]["home"]["abbr"], "segments": rec["segments"],
                          "length_s": rec["length_s"], "speed": rec["speed"]})
        wins.append({"label": "demo window %d" % (i + 1), "start_s": float(start), "slot_s": slot_s, "games": games})
    pub, priv = B.make_schedule(wins)
    B.put(os.path.join(store, "schedule.json"), priv)
    B.put(os.path.join(public, "broadcast", "schedule.json"), pub)
    reveals = []
    if feed_root:
        # the week's feed release is built privately and revealed when the broadcast reaches `reveal_at`; the pointer goes last
        priv_feed = os.path.join(store, "feed")
        shutil.copytree(feed_root, priv_feed)
        for d in sorted(os.listdir(os.path.join(priv_feed, "releases"))):
            reveals.append({"at_s": float(reveal_at), "src": "feed/releases/" + d, "dst": "feed/releases/" + d})
        reveals.append({"at_s": float(reveal_at), "src": "feed/latest.json", "dst": "feed/latest.json", "last": True, "replace": True})
    B.put(os.path.join(store, "reveals.json"), {"reveals": reveals})
    return {"windows": len(wins), "games": sum(len(w["games"]) for w in wins), "reveals": len(reveals)}


class Handler(http.server.BaseHTTPRequestHandler):
    public = None
    server_version = "dfl-broadcast-demo"

    def log_message(self, *a):
        pass

    def _file(self):
        path = posixpath.normpath(self.path.split("?", 1)[0])
        if ".." in path.split("/") or any(part.startswith(".") for part in path.split("/") if part):
            return None
        if path == "/demo" or path.startswith("/demo/"):
            rel = path[len("/demo"):].lstrip("/") or "index.html"
            root = WEB
        else:
            rel, root = path.lstrip("/"), self.public
        full = os.path.join(root, rel)
        if not os.path.isfile(full) or os.path.commonpath([os.path.realpath(full), os.path.realpath(root)]) != os.path.realpath(root):
            return None
        return full

    def do_GET(self, head=False):
        full = self._file()
        if full is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        with open(full, "rb") as f:
            raw = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html" if full.endswith(".html") else "text/javascript" if full.endswith(".js") else "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not head:
            self.wfile.write(raw)

    def do_HEAD(self):
        self.do_GET(head=True)

    def _no(self):
        self.send_response(405)
        self.send_header("Content-Length", "0")
        self.end_headers()
    do_POST = do_PUT = do_DELETE = do_PATCH = _no


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def run(store, public, port, tick, lease=B.LEASE_S):
    lock = open(os.path.join(store, "broadcast.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another broadcast process holds the lock", file=sys.stderr)
        return 75
    bc = B.Broadcaster(store, public, lease)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *a: stop.set())
    signal.signal(signal.SIGINT, lambda *a: stop.set())
    srv = None
    if port:
        Handler.public = public
        srv = Server(("127.0.0.1", port), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    first = True
    while not stop.is_set():
        r = bc.tick()
        if first:
            print(json.dumps(dict(r, started=True)), flush=True)
            first = False
        stop.wait(tick)
    if srv:
        srv.shutdown()
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["prepare", "start", "pause", "resume", "status", "run"])
    ap.add_argument("--store", required=True)
    ap.add_argument("--public", required=True)
    ap.add_argument("--slot-s", type=float, default=960)
    ap.add_argument("--window", action="append", default=[])
    ap.add_argument("--feed-root")
    ap.add_argument("--reveal-feed-at", type=float)
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--tick", type=float, default=1.0)
    ap.add_argument("--lease-s", type=float, default=B.LEASE_S)
    a = ap.parse_args(argv)
    clock = B.Clock(a.store, a.public)
    if a.cmd == "prepare":
        print(json.dumps(prepare(a.store, a.public, a.window, a.slot_s, a.feed_root, a.reveal_feed_at)))
    elif a.cmd == "start":
        clock.start(time.time(), lease=a.lease_s)
    elif a.cmd == "pause":
        clock.pause(time.time())
    elif a.cmd == "resume":
        clock.resume(time.time(), a.lease_s)
    elif a.cmd == "status":
        st = clock.state()
        print(json.dumps({"state": st, "position": clock.position(time.time())}, sort_keys=True))
    else:
        return run(a.store, a.public, a.port, a.tick, a.lease_s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
