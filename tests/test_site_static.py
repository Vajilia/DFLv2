"""The website itself (site/), the preview server, and feed compatibility.

Static checks on the page code (no dynamic HTML from data, nothing fetched from outside, every referenced file exists); the preview server is
read-only; a frozen version-1 feed (tests/fixtures/feed_v1) still reads, still passes the current schemas and still renders in a real browser;
a feed from a future major version is refused, and a future minor version (extra files, extra fields) is read by ignoring what it does not know.
"""
import copy
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from dflsite import feed, publish  # noqa: E402

SITE = os.path.join(ROOT, "site")
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "feed_v1")


def text(*p):
    with open(os.path.join(SITE, *p), encoding="utf-8") as f:
        return f.read()


class Static(unittest.TestCase):
    def test_no_dynamic_html_or_script_evaluation(self):
        js = text("assets", "app.js")
        for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "setTimeout(\"", "javascript:"):
            self.assertNotIn(bad, js, bad)

    def test_nothing_is_fetched_from_outside(self):
        for name in (("index.html",), ("assets", "app.js"), ("assets", "site.css"), ("assets", "base.css")):
            t = text(*name)
            self.assertEqual(re.findall(r"https?://[^\s\"')]+", t), [], name)
            self.assertNotIn("@import", t)

    def test_every_referenced_file_exists_and_the_page_reads_only_the_feed(self):
        html = text("index.html")
        for ref in re.findall(r'(?:href|src)="(assets/[^"]+)"', html):
            self.assertTrue(os.path.exists(os.path.join(SITE, ref)), ref)
        js = text("assets", "app.js")
        self.assertEqual(re.findall(r"fetch\(([^)]*)\)", js), ["url, fresh ? { cache: 'no-store' } : {}", "'assets/map/map.json', { cache: 'no-store' }"])   # the feed, and the site's own map description
        self.assertIn("var BASE = 'feed/';", js)
        self.assertNotIn("POST", js)
        self.assertNotIn("method:", js)

    def test_the_page_carries_the_development_label_and_is_not_indexed(self):
        html = text("index.html")
        self.assertIn("Development league", html)
        self.assertIn('name="robots" content="noindex"', html)
        for route in ("schedule", "standings", "playoffs", "teams", "calendar", "history", "rules", "map"):
            self.assertIn(f'data-route="{route}"', html)

    def test_it_refuses_a_feed_version_it_does_not_know(self):
        js = text("assets", "app.js")
        self.assertIn("VERSION = 1", js)
        self.assertIn("o.format_version !== VERSION", js)


class Preview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import socketserver
        import preview_site
        cls.mod = preview_site
        preview_site.Handler.feed_root = FIXTURE
        preview_site.Handler.site_root = preview_site.SITE
        socketserver.TCPServer.allow_reuse_address = True
        cls.srv = socketserver.TCPServer(("127.0.0.1", 0), preview_site.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def get(self, path, method="GET"):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method, data=b"x" if method in ("POST", "PUT", "PATCH") else None)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, b""

    def test_serves_the_site_and_the_feed(self):
        self.assertEqual(self.get("/")[0], 200)
        self.assertEqual(self.get("/assets/app.js")[0], 200)
        code, body = self.get("/feed/latest.json")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["format"], feed.FORMAT)

    def test_it_can_only_read(self):
        for m in ("POST", "PUT", "PATCH", "DELETE"):
            self.assertEqual(self.get("/feed/latest.json", m)[0], 405, m)
        self.assertEqual(self.get("/assets/new.txt", "PUT")[0], 405)

    def test_it_cannot_be_walked_out_of_its_directories(self):
        for p in ("/../README.md", "/feed/../../README.md", "/feed/%2e%2e/%2e%2e/README.md", "/%2e%2e/src/dfl/persist.py"):
            code, body = self.get(p)
            self.assertNotIn(b"Diamond", body[:200])
            self.assertNotIn(b"import", body[:200])
        self.assertEqual(self.get("/feed/../../../etc/passwd")[0], 404)


class Compatibility(unittest.TestCase):
    def test_the_frozen_v1_feed_reads_and_verifies(self):
        data = publish.read_release(FIXTURE)
        m = data["manifest.json"]
        self.assertEqual((m["format"], m["format_version"]), ("dfl-public-feed", 1))
        self.assertEqual(m["league"]["name"], "Frozen v1 fixture")
        self.assertEqual(len([k for k in data if k.startswith("rosters/")]), 3)

    def test_the_current_schemas_still_accept_the_frozen_feed(self):
        data = publish.read_release(FIXTURE)
        self.assertEqual(feed.check_public(data), [], "a field the v1 feed had was removed or renamed, or a new field became required: that needs a new "
                         "format version (or the new field must be Opt)")

    def test_the_fixture_has_not_been_edited(self):
        with open(os.path.join(FIXTURE, "latest.json"), "rb") as f:
            ptr = json.load(f)
        import hashlib
        with open(os.path.join(FIXTURE, "releases", ptr["release"], "manifest.json"), "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), ptr["manifest_sha256"])

    def test_the_current_exporter_still_writes_every_field_the_v1_feed_had(self):
        # a built feed (here: the fixture re-sealed) keeps the same file set; exporter output must be a superset
        data = publish.read_release(FIXTURE)
        for name in ("season.json", "calendar.json", "notices.json", "teams.json", "schedule.json", "standings.json", "playoffs.json", "history.json",
                     "venues.json", "rules.json", "manifest.json"):
            self.assertIn(name, data)
            self.assertIn(kind := feed.kind_of(name), feed.SCHEMAS)
        for name in data:
            if name.startswith("rosters/"):
                self.assertEqual(set(data[name]), set(feed.SCHEMAS["roster"]))

    def test_a_future_major_version_is_refused(self):
        tmp = tempfile.mkdtemp()
        try:
            root = os.path.join(tmp, "f")
            shutil.copytree(FIXTURE, root)
            p = json.load(open(os.path.join(root, "latest.json")))
            p["format_version"] = 2
            json.dump(p, open(os.path.join(root, "latest.json"), "w"))
            with self.assertRaises(publish.FeedError):
                publish.read_release(root)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_future_minor_version_reads_by_ignoring_what_it_does_not_know(self):
        tmp = tempfile.mkdtemp()
        try:
            data = publish.read_release(FIXTURE)
            files = {k: copy.deepcopy(v) for k, v in data.items() if k != "manifest.json"}
            files["season.json"]["a_future_field"] = {"x": 1}                      # an added field in a known file
            files["extras.json"] = {"future": True}                                # an added file
            index, root = {}, os.path.join(tmp, "f")
            rel = "e99999-future"
            os.makedirs(os.path.join(root, "releases", rel))
            for name, v in files.items():
                os.makedirs(os.path.dirname(os.path.join(root, "releases", rel, name)), exist_ok=True)
                raw = feed.canonical(v).encode()
                open(os.path.join(root, "releases", rel, name), "wb").write(raw)
                import hashlib
                index[name] = {"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
            m = copy.deepcopy(data["manifest.json"])
            m.update(files=index, release=rel, format_minor=7)
            raw = feed.canonical(m).encode()
            open(os.path.join(root, "releases", rel, "manifest.json"), "wb").write(raw)
            import hashlib
            json.dump({"format": feed.FORMAT, "format_version": 1, "release": rel, "manifest_sha256": hashlib.sha256(raw).hexdigest(),
                       "content_hash": m["content_hash"], "generated_at": m["generated_at"]}, open(os.path.join(root, "latest.json"), "w"))
            got = publish.read_release(root)
            self.assertEqual(got["season.json"]["a_future_field"], {"x": 1})
            self.assertIn("extras.json", got)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


try:
    from playwright.sync_api import sync_playwright
except Exception:                                                  # pragma: no cover
    sync_playwright = None


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
class Browser(unittest.TestCase):
    """The real page in a real browser, reading the frozen v1 feed through the preview server."""

    @classmethod
    def setUpClass(cls):
        import socketserver
        import preview_site
        preview_site.Handler.feed_root = FIXTURE
        preview_site.Handler.site_root = preview_site.SITE
        socketserver.TCPServer.allow_reuse_address = True
        cls.srv = socketserver.TCPServer(("127.0.0.1", 0), preview_site.Handler)
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

    def visit(self, width, routes):
        page = self.browser.new_page(viewport={"width": width, "height": 900})
        problems, requests = [], []
        page.on("console", lambda m: problems.append(m.text) if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: problems.append(str(e)))
        page.on("request", lambda r: requests.append(r.url))
        seen = {}
        for r in routes:
            page.goto(f"http://127.0.0.1:{self.port}/#/{r}")
            page.wait_for_selector("#app h1, #app .err", timeout=15000)
            seen[r] = (page.inner_text("#app"), page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth"))
        banner = page.inner_text("#devbanner")
        page.close()
        return seen, problems, requests, banner

    ROUTES = ["", "schedule", "schedule/tour", "standings", "playoffs", "teams", "team/ALA", "calendar", "history", "rules", "map", "team/NOPE"]

    def test_every_page_renders_without_errors_on_desktop_and_phone(self):
        for width in (1200, 390):
            seen, problems, requests, banner = self.visit(width, self.ROUTES)
            self.assertEqual(problems, [], width)
            self.assertIn("development league", banner.lower())
            for r, (t, fits) in seen.items():
                self.assertNotIn("could not be shown", t, r)
                self.assertNotIn("could not be read", t, r)
                self.assertTrue(fits, f"{r} overflows at {width}px")
            self.assertTrue(all(u.startswith(f"http://127.0.0.1:{self.port}/") for u in requests), "the page requested something outside the preview server")

    def test_pages_show_the_league(self):
        seen, _, _, _ = self.visit(1200, self.ROUTES)
        self.assertIn("complete", seen[""][0].lower())
        self.assertIn("atlantic", seen["standings"][0].lower())
        self.assertIn("amethyst aloha stadium", seen["playoffs"][0].lower())
        self.assertIn("ambassador bowl", seen["playoffs"][0].lower())
        self.assertIn("season 1", seen["history"][0].lower())
        self.assertIn("alabama thunder", seen["team/ALA"][0].lower())
        self.assertIn("team not found", seen["team/NOPE"][0].lower())
        self.assertIn("map", seen["map"][0].lower())
        self.assertIn("design document edition", seen["rules"][0].lower())

    def test_the_official_map_is_shown(self):
        page = self.browser.new_page(viewport={"width": 1200, "height": 900})
        page.goto(f"http://127.0.0.1:{self.port}/#/map")
        page.wait_for_selector("img.dflmap", timeout=15000)
        self.assertTrue(page.evaluate("document.querySelector('img.dflmap').naturalWidth > 1000"))
        self.assertIn("official diamond football league map", page.get_attribute("img.dflmap", "alt").lower())
        self.assertIn("30 october 2026", page.inner_text("#app").lower())
        self.assertNotIn("has not been added", page.inner_text("#app"))
        page.close()

    def test_rosters_show_no_hidden_columns(self):
        page = self.browser.new_page(viewport={"width": 1200, "height": 900})
        page.goto(f"http://127.0.0.1:{self.port}/#/team/ALA")
        page.wait_for_selector("#app table")
        heads = " ".join(page.eval_on_selector_all("th", "els => els.map(e => e.textContent)")).lower()
        page.close()
        for bad in ("overall", "rating", "ovr", "potential", "trait", "grade"):
            self.assertNotIn(bad, heads)
        self.assertIn("cap hit", heads)

    def test_a_bad_colour_or_markup_in_the_feed_is_inert(self):
        tmp = tempfile.mkdtemp()
        try:
            root = os.path.join(tmp, "f")
            shutil.copytree(FIXTURE, root)
            ptr = json.load(open(os.path.join(root, "latest.json")))
            rel = os.path.join(root, "releases", ptr["release"])
            teams = json.load(open(os.path.join(rel, "teams.json")))
            teams["teams"][0]["colors"][0]["hex"] = "red;background:url(http://evil.example/x)"
            teams["teams"][0]["full_name"] = "<img src=x onerror=window.__pwned=1>"
            teams["teams"][0]["city"] = "<script>window.__pwned=1</script>"
            raw = json.dumps(teams).encode()
            open(os.path.join(rel, "teams.json"), "wb").write(raw)
            m = json.load(open(os.path.join(rel, "manifest.json")))
            import hashlib
            m["files"]["teams.json"].update(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
            mraw = json.dumps(m).encode()
            open(os.path.join(rel, "manifest.json"), "wb").write(mraw)
            ptr["manifest_sha256"] = hashlib.sha256(mraw).hexdigest()
            json.dump(ptr, open(os.path.join(root, "latest.json"), "w"))
            import preview_site
            old = preview_site.Handler.feed_root
            preview_site.Handler.feed_root = root
            try:
                page = self.browser.new_page()
                reqs = []
                page.on("request", lambda r: reqs.append(r.url))
                page.goto(f"http://127.0.0.1:{self.port}/#/teams")
                page.wait_for_selector("#app h1")
                self.assertIsNone(page.evaluate("window.__pwned"))
                self.assertTrue(all("evil.example" not in u for u in reqs))
                self.assertIn("<img src=x", page.inner_text("#app"))             # shown as plain text
                page.close()
            finally:
                preview_site.Handler.feed_root = old
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
