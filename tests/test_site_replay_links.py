"""Replay links on the website: which games get one, and what the viewer says when the recording is thin.

A link appears only for a completed game whose season, id, clubs and score all match a published replay. Games of earlier seasons are looked up
under their own season, on the History page. The viewer is loaded only when a replay is opened. Real browser, frozen feeds, no network.
"""
import copy
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
sys.path.insert(0, HERE)

from test_site_home import write_variant  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except Exception:                                                  # pragma: no cover
    sync_playwright = None

PLAYBACK = os.path.join(HERE, "fixtures", "playback_v1", "playback")


def read(path, mode="r"):
    with open(path, mode) as f:
        return f.read()


def write(path, data):
    with open(path, "wb" if isinstance(data, bytes) else "w") as f:
        f.write(data)


INDEX = json.loads(read(os.path.join(PLAYBACK, "index.json")))
G1 = next(e for e in INDEX["games"] if e["id"] == "reg:w01:g01")        # OCO at GBP 16-20, a real archived game with some untraced plays


def season_two(files):
    """Season 2 is under way; Season 1 is history. Season 1's first playoff game is stood in by the archived OCO at GBP game, so the
    History page has a game that matches a published replay. The same id in Season 2 is a different game and must not get that link."""
    files["season.json"].update(season=2, year=2001, complete=False, champion=None, phase="regular season", status_text="Regular season: 1 of 19 weeks played.")
    files["playoffs.json"].update(status="not started", champion=None, rounds=[], seeds={}, coronation=None, bowl=None)
    hist = files["history.json"]["seasons"][0]
    g = hist["playoff_games"][0]
    g.update(id=G1["id"], away=G1["away"], home=G1["home"], away_score=G1["away_score"], home_score=G1["home_score"])
    other = [x for x in files["schedule.json"]["games"] if x["id"] == G1["id"]][0]
    other.update(away="TBB", home="POR", away_score=10, home_score=7, status="final")             # Season 2's reg:w01:g01 is another game


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
class ReplayLinks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import preview_site
        cls.tmp = tempfile.mkdtemp()
        root = os.path.join(cls.tmp, "f")

        write_variant(root, season_two)
        shutil.rmtree(os.path.join(root, "playback"))
        shutil.copytree(PLAYBACK, os.path.join(root, "playback"))
        cls.root = root
        handler = type("H", (preview_site.Handler,), {"feed_root": root, "site_root": preview_site.SITE})
        socketserver.TCPServer.allow_reuse_address = True
        cls.srv = socketserver.TCPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.port = cls.srv.server_address[1]
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.srv.shutdown()
        cls.srv.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def open(self, route, wait="#app h1"):
        page = self.browser.new_page(viewport={"width": 1000, "height": 900})
        self.errors = []
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.on("response", lambda r: self.errors.append(f"{r.status} {r.url}") if r.status >= 400 and not r.url.endswith(("/feed/status.json", "/favicon.ico")) else None)   # a heartbeat or icon this test feed does not carry is not a failure
        page.goto(f"http://127.0.0.1:{self.port}/#/{route}")
        page.wait_for_selector(wait, timeout=15000)
        return page

    def replay_links(self, page):
        page.wait_for_selector("#app .game", timeout=15000)
        return page.eval_on_selector_all("#app a.replay", "els => els.map(e => e.getAttribute('href'))")

    def test_an_older_seasons_game_on_the_history_page_gets_its_own_seasons_replay_link(self):
        page = self.open("history")
        links = self.replay_links(page)
        page.close()
        self.assertEqual(links, ["#/game/S001%2Freg%3Aw01%3Ag01", "#/game/S001%2Freg%3Aw01%3Ag02"])   # the playoff-list game, then the collapsed list of Season 1's other recorded replay
        self.assertEqual(self.errors, [])

    def test_the_same_game_id_in_the_current_season_does_not_borrow_that_replay(self):
        page = self.open("schedule/1")
        page.wait_for_selector("#app .game", timeout=15000)
        self.assertEqual(self.replay_links(page), [])                   # Season 2's reg:w01:g01 is TBB at POR, not the archived OCO at GBP game
        self.assertIn("Tampa Bay", page.inner_text("#app"))
        page.close()

    def test_a_game_whose_score_differs_from_the_recording_gets_no_link(self):
        idx = os.path.join(self.root, "playback", "index.json")
        orig = read(idx)
        try:
            d = json.loads(orig)
            for e in d["games"]:
                if e["id"] == "reg:w01:g01":
                    e["home_score"] += 1
            write(idx, json.dumps(d))
            page = self.open("history")
            self.assertEqual(self.replay_links(page), ["#/game/S001%2Freg%3Aw01%3Ag02"])     # only the unchanged game keeps its link
            page.close()
        finally:
            write(idx, orig)

    def test_no_index_means_no_links_and_a_plain_message_on_a_replay_address(self):
        idx = os.path.join(self.root, "playback", "index.json")
        orig = read(idx)
        try:
            write(idx, json.dumps({"format": "dfl-public-playback", "format_version": 1, "kind": "index", "league_id": None, "games": []}))
            page = self.open("history")
            self.assertEqual(self.replay_links(page), [])
            page.goto(f"http://127.0.0.1:{self.port}/#/game/S001%2Freg%3Aw01%3Ag01")
            page.wait_for_selector("#app .err", timeout=15000)
            self.assertIn("There is no replay for this game", page.inner_text("#app"))
            page.close()
        finally:
            write(idx, orig)

    def test_opening_an_older_seasons_replay_loads_the_viewer_with_honest_fallbacks_and_a_back_link_to_history(self):
        page = self.open("history")
        self.replay_links(page)
        page.click("#app a.replay")
        page.wait_for_selector("#app .pb-canvas", timeout=20000)
        text = page.inner_text("#app")
        self.assertIn("← History", text)
        self.assertIn("no movement recorded", text)                      # some plays of this game have no recorded movement; the list says so
        self.assertIn("Point at or tap a player", text)
        self.assertEqual(page.get_attribute("#app a.pb-back", "href"), "#/history")
        self.assertEqual([e for e in self.errors if "favicon" not in e], [])
        page.close()

    def test_a_player_missing_from_the_public_rosters_is_shown_by_number_not_invented(self):
        import hashlib
        gdir = os.path.join(self.root, "playback", "games")
        idx = os.path.join(self.root, "playback", "index.json")
        orig = read(idx)
        game = json.loads(read(os.path.join(gdir, os.path.basename(G1["file"]))))
        game["roster"] = {}                                              # as if no public roster named anyone when the file was made
        raw = json.dumps(game, separators=(",", ":")).encode()
        name = "games/S001-reg-w01-g01.noroster.json"
        write(os.path.join(self.root, "playback", name), raw)
        try:
            d = json.loads(orig)
            for e in d["games"]:
                if e["id"] == "reg:w01:g01":
                    e.update(file=name, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
            write(idx, json.dumps(d))
            page = self.open("history")
            self.replay_links(page)
            page.click("#app a.replay")
            page.wait_for_selector("#app .pb-canvas", timeout=20000)
            page.click('#app .pb-row[data-i="1"]')                       # a play that has recorded movement
            box = page.eval_on_selector("#app .pb-canvas", "c => { const r = c.getBoundingClientRect(); return [r.x, r.y, r.width, r.height] }")
            said = ""
            for fy in range(1, 20):
                for fx in range(1, 40):
                    page.mouse.move(box[0] + box[2] * fx / 40, box[1] + box[3] * fy / 20)
                    said = page.inner_text("#app .pb-who")
                    if "no public name" in said:
                        break
                if "no public name" in said:
                    break
            self.assertRegex(said, r"^player \d+ \(no public name\)")
            page.close()
        finally:
            write(idx, orig)
            os.remove(os.path.join(self.root, "playback", name))


G2 = next(e for e in INDEX["games"] if e["id"] == "reg:w01:g02")        # LVR at NOS 3-6


def entry_like(base, **fields):
    """An index entry for the rules tests: it points at a real replay file (so it opens), with the identity fields changed. The page shows what
    the file holds when it opens; these entries only exercise which links appear and where "back" goes."""
    e = dict(base)
    e.update(fields)
    return e


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
class HistoryAndBack(unittest.TestCase):
    """The History page's replay links (Coronation and the collapsed list of other recorded replays) and the viewer's back link."""

    @classmethod
    def setUpClass(cls):
        import preview_site
        cls.tmp = tempfile.mkdtemp()
        root = os.path.join(cls.tmp, "f")
        write_variant(root, lambda files: None)                          # the frozen completed Season 1: champion TOR over MON 43-22, the Coronation is in playoffs.json
        shutil.rmtree(os.path.join(root, "playback"))
        shutil.copytree(PLAYBACK, os.path.join(root, "playback"))
        cls.root, cls.idx = root, os.path.join(root, "playback", "index.json")
        cls.orig = read(cls.idx)
        handler = type("H", (preview_site.Handler,), {"feed_root": root, "site_root": preview_site.SITE})
        socketserver.TCPServer.allow_reuse_address = True
        cls.srv = socketserver.TCPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.port = cls.srv.server_address[1]
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.srv.shutdown()
        cls.srv.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def tearDown(self):
        write(self.idx, self.orig)

    def publish(self, *entries):
        d = json.loads(self.orig)
        d["games"] = list(entries)
        write(self.idx, json.dumps(d))

    def page(self, route, wait):
        page = self.browser.new_page(viewport={"width": 1000, "height": 900})
        page.goto(f"http://127.0.0.1:{self.port}/#/{route}")
        page.wait_for_selector(wait, timeout=15000)
        return page

    def coronation(self, **fields):
        base = dict(gid="S001/coronation", id="coronation", game_kind="coronation", season=1, away="MON", home="TOR", away_score=22, home_score=43)
        base.update(fields)
        return entry_like(G1, **base)

    def test_history_coronation_row_links_when_the_index_has_that_game_between_the_two_finalists(self):
        self.publish(self.coronation())
        page = self.page("history", "#app details")
        links = page.eval_on_selector_all("#app dl a.replay", "els => els.map(e => e.getAttribute('href'))")
        page.close()
        self.assertEqual(links, ["#/game/S001%2Fcoronation"])

    def test_history_coronation_row_has_no_link_when_the_score_or_clubs_differ_from_the_history(self):
        for fields in ({"home_score": 44}, {"home": "CLE"}, {"season": 2, "gid": "S002/coronation"}):
            self.publish(self.coronation(**fields))
            page = self.page("history", "#app details")
            self.assertEqual(page.eval_on_selector_all("#app dl a.replay", "els => els.length"), 0, fields)
            page.close()

    def test_history_lists_other_replays_of_a_season_collapsed_with_a_label_and_a_link(self):
        self.publish(self.coronation(), entry_like(G2))
        page = self.page("history", "#app details")
        summary = page.inner_text("#app details > details > summary")
        self.assertIn("Other recorded replays from this season (1)", summary)
        page.click("#app details > details > summary")
        text = page.inner_text("#app details > details")
        self.assertIn("Regular season, week 1", text)
        self.assertEqual(page.eval_on_selector_all("#app details > details a.replay", "els => els.map(e => e.getAttribute('href'))"), ["#/game/S001%2Freg%3Aw01%3Ag02"])
        page.close()

    def test_no_replays_for_a_season_means_no_list(self):
        self.publish()
        page = self.page("history", "#app details")
        self.assertEqual(page.eval_on_selector_all("#app details > details", "els => els.length"), 0)
        page.close()

    def test_back_goes_to_the_page_the_visitor_came_from(self):
        self.publish(self.coronation())
        for route, selector, label, href in (("playoffs", "#app .slot a.replay", "← Playoffs", "#/playoffs"),
                                             ("history", "#app dl a.replay", "← History", "#/history")):
            page = self.page(route, selector)
            page.click(selector)
            page.wait_for_selector("#app a.pb-back", timeout=20000)
            self.assertEqual(page.inner_text("#app a.pb-back"), label)
            self.assertEqual(page.get_attribute("#app a.pb-back", "href"), href)
            page.click("#app a.pb-back")
            page.wait_for_selector("#app h1", timeout=15000)
            self.assertEqual(page.evaluate("location.hash"), href)
            page.close()

    def test_a_direct_visit_to_a_replay_falls_back_by_the_kind_of_game(self):
        self.publish(self.coronation(), entry_like(G2, season=1))
        cases = (("S001%2Fcoronation", "← Playoffs"), ("S001%2Freg%3Aw01%3Ag02", "← Schedule"))
        for gid, label in cases:
            page = self.page("game/" + gid, "#app a.pb-back")
            self.assertEqual(page.inner_text("#app a.pb-back"), label, gid)
            page.close()


if __name__ == "__main__":
    unittest.main()
