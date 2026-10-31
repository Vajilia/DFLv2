"""The website's health line (stale / error / paused / running), in a real browser.

`health()` is a pure function of feed/status.json and the clock, so most cases are checked by calling it with a chosen `now`; two end-to-end cases serve a
real feed with and without a status.json and read what the page shows.
"""
import json
import os
import shutil
import socketserver
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from dflsite import status as ST  # noqa: E402

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "feed_v1")
try:
    from playwright.sync_api import sync_playwright
except Exception:                                                  # pragma: no cover
    sync_playwright = None

NOW = 1_800_000_000          # an arbitrary fixed "now" (seconds) used for the pure checks


def iso(dt=0):
    return ST.now_iso(NOW + dt)


def doc(checked=0, ok=True, runner="running", runner_age=-30, err=None, due=120):
    r = None if runner is None else {"state": runner, "updated_at": iso(runner_age), "next_event_due": iso(due), "events": 5, "pace_seconds": 600, "last_event_at": iso(-300),
                                     **({"error": {"type": err, "at": iso(-10)}} if err else {})}
    return {"format": "dfl-feed-status", "format_version": 1, "checked_at": iso(checked), "league_events": 5, "published_events": 5, "release": "e00005-x",
            "export": {"ok": ok, "error": None if ok else {"type": "OperationalError", "at": iso(checked)}, "last_success_at": iso(-3600), "last_publish_at": iso(-600), "seconds": 3},
            "runner": r}


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
class Health(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import preview_site
        preview_site.Handler.feed_root = FIXTURE
        preview_site.Handler.site_root = preview_site.SITE
        socketserver.TCPServer.allow_reuse_address = True
        cls.srv = socketserver.TCPServer(("127.0.0.1", 0), preview_site.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()
        cls.page = cls.browser.new_page()
        cls.page.goto(f"http://127.0.0.1:{cls.port}/")
        cls.page.wait_for_selector("#app h1")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.srv.shutdown()
        cls.srv.server_close()

    def h(self, d):
        return self.page.evaluate("([d, now]) => window.DFL_VIEWER.health(d, now)", [d, NOW * 1000])

    def test_a_running_league_says_so_and_when_the_next_event_is_due(self):
        r = self.h(doc())
        self.assertEqual(r["level"], "ok")
        self.assertIn("Simulation running", r["text"])
        self.assertIn("next event", r["text"])

    def test_no_status_file_means_no_health_line(self):
        self.assertIsNone(self.h(None))

    def test_a_stale_heartbeat_is_a_visible_warning(self):
        r = self.h(doc(checked=-6 * 60))
        self.assertEqual(r["level"], "bad")
        self.assertIn("stalled", r["text"])
        self.assertEqual(self.h(doc(checked=-4 * 60))["level"], "ok", "four minutes is still on time")

    def test_a_failed_export_is_reported_with_the_last_good_time(self):
        r = self.h(doc(ok=False))
        self.assertEqual(r["level"], "bad")
        self.assertIn("failed", r["text"])
        self.assertIn("OperationalError", r["text"])
        self.assertIn("last good", r["text"])

    def test_paused_and_stopped_are_shown_as_not_advancing(self):
        for state in ("paused", "stopped"):
            r = self.h(doc(runner=state))
            self.assertEqual(r["level"], "paused", state)
            self.assertIn("not advancing", r["text"])

    def test_a_runner_error_is_a_visible_error(self):
        r = self.h(doc(runner="error", err="StateMismatch"))
        self.assertEqual(r["level"], "bad")
        self.assertIn("StateMismatch", r["text"])

    def test_a_runner_that_stopped_updating_is_not_responding(self):
        r = self.h(doc(runner="running", runner_age=-11 * 60))
        self.assertEqual(r["level"], "bad")
        self.assertIn("not responding", r["text"])
        self.assertEqual(self.h(doc(runner="running", runner_age=-9 * 60))["level"], "ok")

    def test_a_missing_runner_status_is_a_warning_not_silence(self):
        self.assertEqual(self.h(doc(runner=None))["level"], "warn")

    def test_the_page_shows_the_line_from_a_real_status_file_and_none_without_one(self):
        tmp = tempfile.mkdtemp()
        try:
            root = os.path.join(tmp, "f")
            shutil.copytree(FIXTURE, root)
            import preview_site
            old = preview_site.Handler.feed_root
            preview_site.Handler.feed_root = root
            try:
                p = self.browser.new_page(viewport={"width": 390, "height": 800})
                p.goto(f"http://127.0.0.1:{self.port}/")
                p.wait_for_selector("#app h1")
                self.assertEqual(p.locator(".health").count(), 0)
                ST.write_atomic(os.path.join(root, "status.json"), doc(checked=-1000000000 + int(__import__("time").time() - NOW)))   # a heartbeat from long ago
                p.goto(f"http://127.0.0.1:{self.port}/")
                p.wait_for_selector(".health")
                self.assertEqual(p.get_attribute(".health", "data-health"), "bad")
                self.assertIn("stalled", p.inner_text(".health"))
                self.assertTrue(p.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth"), "fits a phone")
                import time
                t = time.time()
                ST.write_atomic(os.path.join(root, "status.json"), {**doc(), "checked_at": ST.now_iso(t), "runner": {"state": "paused", "updated_at": ST.now_iso(t)}})
                p.goto(f"http://127.0.0.1:{self.port}/")
                p.wait_for_selector(".health.paused")
                self.assertIn("paused", p.inner_text(".health").lower())
                p.close()
            finally:
                preview_site.Handler.feed_root = old
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class VerifierCheck(unittest.TestCase):
    """verify_deployment's reading of the heartbeat: it must catch a stale or failed one and a leak of private detail."""

    def run_check(self, d, raw=None):
        import verify_deployment as V
        rep = V.Report()
        V.check_status(raw if raw is not None else json.dumps(d).encode(), rep)
        return rep

    def fresh(self):
        import time
        t = time.time()
        d = doc()
        d["checked_at"] = ST.now_iso(t)
        return d

    def test_a_healthy_heartbeat_passes(self):
        self.assertFalse(self.run_check(self.fresh()).failed())

    def test_stale_failed_leaky_or_foreign_heartbeats_fail(self):
        d = self.fresh(); d["checked_at"] = ST.now_iso(0)
        self.assertTrue(self.run_check(d).failed(), "stale")
        d = self.fresh(); d["export"]["ok"] = False
        self.assertTrue(self.run_check(d).failed(), "failed export")
        d = self.fresh(); d["runner"]["pid"] = 123
        self.assertTrue(self.run_check(d).failed(), "pid leaked")
        self.assertTrue(self.run_check(None, raw=b"<html>").failed(), "not json")


if __name__ == "__main__":
    unittest.main()
