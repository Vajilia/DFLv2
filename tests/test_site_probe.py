"""tools/probe_site.py against a local stand-in site: a good site, a failing one and two sites that disagree about the release."""
import contextlib
import http.server
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import probe_site  # noqa: E402


def site(release="e00001-aaaaaaaaaa", ok=True, status_code=200):
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            routes = {"/": b"<html>DFL</html>", "/feed/latest.json": json.dumps({"release": release}).encode(),
                      "/feed/status.json": json.dumps({"checked_at": now, "export": {"ok": ok}, "league_events": 1, "published_events": 1, "runner": {"state": "running"}}).encode()}
            if self.path not in routes or (self.path == "/feed/status.json" and status_code != 200):
                self.send_response(status_code if self.path == "/feed/status.json" else 404)
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(routes[self.path])
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]


class Probe(unittest.TestCase):
    def test_a_good_site_is_recorded_ok_with_its_release_and_a_fresh_heartbeat(self):
        srv, url = site()
        self.addCleanup(srv.shutdown)
        r = probe_site.probe(url)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["release"], "e00001-aaaaaaaaaa")
        self.assertLess(abs(r["heartbeat_age_s"]), 5)

    def test_a_failing_export_or_missing_route_is_a_failure_and_a_dead_site_does_not_raise(self):
        srv, url = site(ok=False)
        self.addCleanup(srv.shutdown)
        self.assertFalse(probe_site.probe(url)["ok"])
        srv2, url2 = site(status_code=500)
        self.addCleanup(srv2.shutdown)
        self.assertEqual(probe_site.probe(url2)["status"]["code"], 500)
        dead = probe_site.probe("http://127.0.0.1:9")
        self.assertFalse(dead["ok"])

    def test_the_log_and_summary_report_failures_and_disagreeing_sites(self):
        a, ua = site("e00001-aaaaaaaaaa")
        b, ub = site("e00002-bbbbbbbbbb")
        self.addCleanup(a.shutdown)
        self.addCleanup(b.shutdown)
        log = os.path.join(tempfile.mkdtemp(), "probe.jsonl")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(probe_site.main(["--log", log, ua, ub]), 0)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            probe_site.main(["--summary", log])
        s = json.loads(buf.getvalue())
        self.assertEqual((s["probes"], s["failure_count"]), (2, 0))
        self.assertEqual(len(s["disagreements"]), 1)


if __name__ == "__main__":
    unittest.main()
