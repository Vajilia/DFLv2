"""A temporary publication error must heal by itself in the viewer, and an error answer must never be cached as if it were a good release.

Found on the Droplet 2026-10-31: the first live release was briefly unreadable (HTTP 403, wrong folder permissions), the web server sent that 403 with the
same `immutable` caching headers as a good release, and the browser kept replaying it for a viewer even after the files were fixed.
"""
import http.server
import os
import shutil
import socketserver
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
import preview_site                                              # noqa: E402
from test_site_static import FIXTURE                             # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except Exception:                                                # pragma: no cover
    sync_playwright = None


class Flaky(preview_site.Handler):
    """Answers the first `fail` requests for a release's manifest with a 403 that carries the immutable-release caching headers."""
    fail = 0
    seen = []

    def do_GET(self):
        self._bad = False
        if self.path.split("?")[0].endswith("/manifest.json") and Flaky.fail > 0:
            Flaky.fail -= 1
            self._bad = True
            Flaky.seen.append(403)
            self.send_response(403)
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path.split("?")[0].endswith("/manifest.json"):
            Flaky.seen.append(200)
        super().do_GET()

    def end_headers(self):
        if getattr(self, "_bad", False):
            http.server.BaseHTTPRequestHandler.end_headers(self)       # keep the immutable header
        else:
            super().end_headers()


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
class Recovery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        shutil.copytree(FIXTURE, os.path.join(cls.tmp, "f"))
        Flaky.feed_root = os.path.join(cls.tmp, "f")
        Flaky.site_root = preview_site.SITE
        socketserver.TCPServer.allow_reuse_address = True
        cls.srv = socketserver.TCPServer(("127.0.0.1", 0), Flaky)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.srv.shutdown()
        cls.srv.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def open(self, fail, retry_ms=None):
        Flaky.fail, Flaky.seen = fail, []
        ctx = self.browser.new_context()                            # a fresh browser cache each time
        page = ctx.new_page()
        if retry_ms:
            page.add_init_script(f"window.DFL_RETRY_MS = {retry_ms};")
        page.goto(f"http://127.0.0.1:{self.port}/#/")
        return ctx, page

    def test_one_bad_answer_heals_in_the_same_load(self):
        ctx, page = self.open(1)
        try:
            page.wait_for_selector("#app h1", timeout=15000)
            self.assertNotIn("could not be read", page.inner_text("#app"))
            self.assertEqual(Flaky.seen[:2], [403, 200])
        finally:
            ctx.close()

    def test_a_longer_outage_heals_by_itself_without_a_reload(self):
        ctx, page = self.open(3, retry_ms=400)                     # the first load fails twice over (the read and its one retry), the page keeps trying
        try:
            page.wait_for_selector("#app .err", timeout=15000)
            self.assertIn("Trying again automatically", page.inner_text("#app"))
            page.wait_for_selector("#app h1", timeout=20000)
            self.assertNotIn("could not be read", page.inner_text("#app"))
            self.assertEqual(Flaky.seen[-1], 200)
        finally:
            ctx.close()


if __name__ == "__main__":
    unittest.main()
