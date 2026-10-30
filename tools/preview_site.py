#!/usr/bin/env python3
"""Look at the website on this computer, using a real exported feed.

  preview_site.py --feed FEED_ROOT [--port 8080]          serve site/ at / and FEED_ROOT at /feed/ (read-only; GET and HEAD only)
  preview_site.py --feed FEED_ROOT --single-file OUT.html  write one self-contained page with the feed embedded (opens by double-click, no server)

The server cannot change anything: it answers GET and HEAD from two directories and refuses every other method. It never runs the league.
"""
import argparse
import http.server
import json
import os
import socketserver
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "site")
sys.path.insert(0, os.path.join(ROOT, "src"))


class Handler(http.server.SimpleHTTPRequestHandler):
    feed_root = None

    def translate_path(self, path):
        path = path.split("?", 1)[0].split("#", 1)[0]
        parts = [p for p in os.path.normpath("/" + path.lstrip("/")).split("/") if p and p != ".."]
        if parts and parts[0] == "feed":
            return os.path.join(self.feed_root, *parts[1:])
        return os.path.join(SITE, *parts) if parts else os.path.join(SITE, "index.html")

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _refuse(self):
        self.send_error(405, "read-only")
    do_POST = do_PUT = do_DELETE = do_PATCH = _refuse

    def log_message(self, *a):
        pass


def single_file(feed_root, out):
    from dflsite import publish
    data = publish.read_release(feed_root)
    rel = data["manifest.json"]["release"]
    with open(os.path.join(feed_root, publish.POINTER), encoding="utf-8") as f:
        embed = {"feed/latest.json": json.load(f)}
    for name, value in data.items():
        embed[f"feed/releases/{rel}/{name}"] = value
    rd = lambda *p: open(os.path.join(SITE, *p), encoding="utf-8").read()
    html = rd("index.html")
    html = html.replace('<link rel="stylesheet" href="assets/base.css">', "<style>" + rd("assets", "base.css") + "</style>")
    html = html.replace('<link rel="stylesheet" href="assets/site.css">', "<style>" + rd("assets", "site.css") + "</style>")
    blob = json.dumps(embed, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    html = html.replace('<script src="assets/app.js"></script>', "<script>window.DFL_EMBED=" + blob + ";</script><script>" + rd("assets", "app.js").replace("</script", "<\\/script") + "</script>")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    return os.path.getsize(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed", required=True)
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--single-file")
    a = ap.parse_args(argv)
    if a.single_file:
        print(json.dumps({"written": a.single_file, "bytes": single_file(a.feed, a.single_file)}))
        return 0
    Handler.feed_root = os.path.abspath(a.feed)
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", a.port), Handler) as srv:
        print(f"http://127.0.0.1:{a.port}/  (feed: {Handler.feed_root})", flush=True)
        srv.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
