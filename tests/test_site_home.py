"""What the home page says in each part of the league year: preseason, regular season, playoffs, and a completed season.

Each case builds a small variant of the frozen v1 feed (a completed Season 1) with the season, schedule and playoff files set to that
phase, then loads the real page in a real browser. The checks are on what a visitor reads, not on how the page is built.
"""
import copy
import hashlib
import json
import os
import shutil
import socketserver
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from dflsite import feed, publish  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except Exception:                                                  # pragma: no cover
    sync_playwright = None

FIXTURE = os.path.join(HERE, "fixtures", "feed_v1")


def write_variant(root, change):
    """Write a feed under root whose files are the fixture's after change(files); returns nothing. Hashes and the pointer are rebuilt."""
    data = publish.read_release(FIXTURE)
    files = {k: copy.deepcopy(v) for k, v in data.items() if k != "manifest.json"}
    change(files)
    rel = "e00001-variant"
    os.makedirs(os.path.join(root, "releases", rel))
    index = {}
    for name, v in files.items():
        os.makedirs(os.path.dirname(os.path.join(root, "releases", rel, name)), exist_ok=True)
        raw = feed.canonical(v).encode()
        with open(os.path.join(root, "releases", rel, name), "wb") as f:
            f.write(raw)
        index[name] = {"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    m = copy.deepcopy(data["manifest.json"])
    sn = files["season.json"]
    m.update(files=index, release=rel, season=sn["season"], year=sn["year"])      # the manifest names the season the page treats as current
    raw = feed.canonical(m).encode()
    with open(os.path.join(root, "releases", rel, "manifest.json"), "wb") as f:
        f.write(raw)
    with open(os.path.join(root, "latest.json"), "w") as f:
        json.dump({"format": feed.FORMAT, "format_version": 1, "release": rel, "manifest_sha256": hashlib.sha256(raw).hexdigest(),
                   "content_hash": m["content_hash"], "generated_at": m["generated_at"]}, f)
    os.makedirs(os.path.join(root, "playback"))
    with open(os.path.join(root, "playback", "index.json"), "w") as f:
        json.dump({"format": "dfl-public-playback", "format_version": 1, "kind": "index", "league_id": None, "games": []}, f)


def unplayed(g):
    g["status"] = "scheduled"
    g["home_score"] = g["away_score"] = None


def not_started(files):
    files["playoffs.json"].update(status="not started", champion=None, rounds=[], seeds={}, coronation=None, bowl=None)


def preseason(files):
    s = files["season.json"]
    s.update(phase="offseason", complete=False, champion=None, postseason_started=False, regular_season_complete=False, events_done=2,
             regular_weeks={"played": 0, "total": 19}, status_text="Offseason: the 2000 league year is under way.",
             last_event={"date": "2000-02-01", "index": 2, "key": "x", "label": "Free agency opens"}, next_event={"date": "2000-03-01", "index": 3, "key": "y", "label": "Training camp opens"})
    for g in files["schedule.json"]["games"]:
        unplayed(g)
    not_started(files)


def regular_season(files):
    s = files["season.json"]
    s.update(phase="regular season", complete=False, champion=None, postseason_started=False, regular_season_complete=False, events_done=12,
             regular_weeks={"played": 4, "total": 19}, status_text="Regular season: 4 of 19 weeks played.",
             next_event={"date": "2000-08-01", "index": 13, "key": "y", "label": "Week 5"})
    for g in files["schedule.json"]["games"]:
        if g["kind"] != "regular" or g["week"] > 4:
            unplayed(g)
    not_started(files)


def seeded(files):
    s = files["season.json"]
    s.update(phase="postseason", complete=False, champion=None, postseason_started=True, regular_season_complete=True, events_done=30,
             status_text="Postseason: the playoff field is set.")
    for g in files["schedule.json"]["games"]:
        if g["kind"] != "regular":
            unplayed(g)
    files["playoffs.json"].update(status="seeded", champion=None, rounds=[], coronation=None, bowl=None)


def in_progress(files):
    seeded(files)
    files["playoffs.json"]["status"] = "in progress"


def coronation_played(files):
    """The Coronation has a winner but the league year has not closed yet."""
    s = files["season.json"]
    s.update(phase="postseason", complete=False, postseason_started=True, regular_season_complete=True, events_done=46, next_event={"date": "2000-12-31", "index": 47, "key": "year_close", "label": "League year closes"},
             status_text="Postseason: the Diamond Coronation has been played.")


def completed(files):
    pass


CASES = {
    "preseason": preseason, "regular season": regular_season, "playoffs seeded": seeded, "playoffs in progress": in_progress,
    "coronation played": coronation_played, "completed season": completed,
}


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
class HomePage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import preview_site
        cls.tmp = tempfile.mkdtemp()
        cls.servers, cls.ports = [], {}
        for name, change in CASES.items():
            root = os.path.join(cls.tmp, name.replace(" ", "_"))
            write_variant(root, change)
            handler = type("H", (preview_site.Handler,), {"feed_root": root, "site_root": preview_site.SITE})
            socketserver.TCPServer.allow_reuse_address = True
            srv = socketserver.TCPServer(("127.0.0.1", 0), handler)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            cls.servers.append(srv)
            cls.ports[name] = srv.server_address[1]
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        for srv in cls.servers:
            srv.shutdown()
            srv.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def home(self, case):
        page = self.browser.new_page(viewport={"width": 1000, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{self.ports[case]}/#/")
        page.wait_for_selector("#app h1", timeout=15000)
        text = page.inner_text("#app")
        links = page.eval_on_selector_all("#app a", "els => els.map(e => [e.textContent, e.getAttribute('href')])")
        champ = page.query_selector("#app .champ")
        champ_text = champ.inner_text() if champ else None
        page.close()
        self.assertEqual(errors, [], case)
        return text, links, champ_text

    def test_preseason_shows_no_playoffs_and_no_champion(self):
        text, links, champ = self.home("preseason")
        self.assertIn("offseason: the 2000 league year is under way.", text.lower())
        self.assertIsNone(champ)
        self.assertEqual([t for t, h in links if h == "#/playoffs" and "bracket" in t], [])
        self.assertNotIn("under way →", text)                           # no playoff line at all before the field is set
        self.assertNotIn("is complete", text)

    def test_regular_season_shows_progress_and_no_playoff_claims(self):
        text, links, champ = self.home("regular season")
        self.assertIn("regular season: 4 of 19 weeks played.", text.lower())
        self.assertIn("4 / 19", text)
        self.assertIsNone(champ)
        self.assertEqual([t for t, h in links if "bracket" in t], [])
        self.assertNotIn("is complete", text)

    def test_playoffs_seeded_says_the_field_is_set_not_that_games_are_under_way(self):
        text, links, champ = self.home("playoffs seeded")
        self.assertIn("The playoff field is set: see the bracket →", [t for t, h in links])
        self.assertNotIn("bracket is under way", text)
        self.assertIsNone(champ)

    def test_playoffs_in_progress_says_under_way_and_names_no_champion(self):
        text, links, champ = self.home("playoffs in progress")
        self.assertIn("The playoff bracket is under way →", [t for t, h in links])
        self.assertIsNone(champ)
        self.assertNotIn("is complete", text)

    def test_a_played_coronation_names_the_champion_before_the_year_closes(self):
        text, links, champ = self.home("coronation played")
        self.assertIn("DIAMOND CORONATION CHAMPION", champ)
        self.assertNotIn("is complete", champ)                          # the league year is not closed yet
        self.assertIn("The Diamond Coronation has been played: see the bracket →", [t for t, h in links])
        self.assertNotIn("under way", text)

    def test_a_completed_season_shows_the_champion_and_completed_status(self):
        text, links, champ = self.home("completed season")
        self.assertIn("DIAMOND CORONATION CHAMPION · 2000", champ)
        self.assertIn("Season 1 is complete.", champ)
        self.assertIn("the 2000 league year is complete.", text.lower())
        self.assertIn("See the final playoff bracket and the Diamond Coronation result →", [t for t, h in links])
        self.assertNotIn("under way", text)
        self.assertNotIn("bracket is under way", text)


if __name__ == "__main__":
    unittest.main()
