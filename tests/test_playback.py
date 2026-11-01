"""Game replay: the public playback files, the exporter's encoding, and the viewer in a real browser (docs/PUBLIC_PLAYBACK_FORMAT.md).

The fixed sample in tests/fixtures/playback_v1 is a real archived 12-event spatial development league (feed/ is its published feed, playback/ the files
tools/export_playback.py made from its archive). What is checked:
  * every game file passes the public key gate, carries no rating/play-call/injury fields, matches its checksum and name, and agrees with the feed's own
    schedule on participants and final score; the running score of the last play equals the final score; every traced player is on a public roster
  * the trace encoding round-trips, the ball that is not yet placed at the first sample is marked, and a published game file can never be overwritten
  * in a browser (desktop and phone): the Replay links, the field with all 22 players and the ball, scoreboard, play list, seeking, play/pause, speed,
    plays without movement shown as event cards, a damaged trace, a corrupted or missing file, an incompatible format, switching between games,
    a feed refresh while watching, and that watching only ever reads (GET) published files and leaves them byte-identical."""
import hashlib
import json
import os
import shutil
import socketserver
import struct
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from dflsite import playback as PB  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures", "playback_v1")
PLAY = os.path.join(FIX, "playback")
FEED = os.path.join(FIX, "feed")
GID1, GID2 = "S001/reg:w01:g01", "S001/reg:w01:g02"

try:
    from playwright.sync_api import sync_playwright
except Exception:                                                  # pragma: no cover
    sync_playwright = None


def load(path):
    with open(path, "rb") as f:
        return json.loads(f.read().decode("utf-8"))


def tree_digest(root):
    h = hashlib.sha256()
    for d, _, files in sorted(os.walk(root)):
        for n in sorted(files):
            p = os.path.join(d, n)
            h.update(os.path.relpath(p, root).encode())
            with open(p, "rb") as f:
                h.update(f.read())
    return h.hexdigest()


def feed_schedule():
    ptr = load(os.path.join(FEED, "latest.json"))
    return {g["id"]: g for g in load(os.path.join(FEED, "releases", ptr["release"], "schedule.json"))["games"]}


class Files(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = load(os.path.join(PLAY, "index.json"))
        cls.games = {e["gid"]: (e, load(os.path.join(PLAY, e["file"]))) for e in cls.index["games"]}

    def test_index_and_names(self):
        self.assertEqual((self.index["format"], self.index["format_version"], self.index["kind"]), ("dfl-public-playback", 1, "index"))
        self.assertEqual(sorted(self.games), [GID1, GID2])
        for gid, (e, g) in self.games.items():
            with open(os.path.join(PLAY, e["file"]), "rb") as f:
                raw = f.read()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), e["sha256"])
            self.assertEqual(len(raw), e["bytes"])
            self.assertIn(e["sha256"][:10], e["file"], "the file name carries its own hash, so a published file never changes")
            self.assertEqual(raw, PB.canonical(g), "files are canonical JSON: the same game always gives the same bytes")

    def test_public_gate_and_no_private_fields(self):
        for gid, (e, g) in self.games.items():
            PB.check_public(g)
            def keys(o, out):
                if isinstance(o, dict):
                    for k, v in o.items():
                        out.add(k.lower())
                        keys(v, out)
                elif isinstance(o, list):
                    for v in o:
                        keys(v, out)
                return out
            names = keys(g, set())
            for bad in ("rating", "ovr", "intent", "formation", "coverage", "injury", "injuries", "salary", "contract", "play_call", "stats", "seed", "attributes"):
                self.assertFalse([k for k in names if bad in k], f"{gid}: a field named like {bad}")
            # the play-by-play may say that a player left hurt, but names no one and gives no detail
            for p in g["plays"]:
                if p["kind"] == "injury":
                    self.assertEqual(p["text"], "A player is injured and leaves the game.")
                    self.assertIsNone(p["trace"])
            text = json.dumps([dict(p, kind="", text="") for p in g["plays"]] + [g["roster"], g["teams"]]).lower()
            for bad in ("rating", "injur", "salary", "contract", "play_call"):
                self.assertNotIn(bad, text, f"{gid}: {bad}")
        bad = json.loads(json.dumps(self.games[GID1][1]))
        bad["plays"][0]["rating"] = 80
        with self.assertRaises(PB.PlaybackError):
            PB.check_public(bad)
        bad = json.loads(json.dumps(self.games[GID1][1]))
        next(p for p in bad["plays"] if p["trace"])["trace"]["intents"] = []
        with self.assertRaises(PB.PlaybackError):
            PB.check_public(bad)

    def test_participants_events_and_score_agree_with_the_feed_and_the_file(self):
        sched = feed_schedule()
        for gid, (e, g) in self.games.items():
            s = sched[e["id"]]
            self.assertEqual((g["teams"]["away"]["abbr"], g["teams"]["home"]["abbr"]), (s["away"], s["home"]))
            self.assertEqual((g["final"]["away"], g["final"]["home"]), (s["away_score"], s["home_score"]))
            self.assertEqual((e["away_score"], e["home_score"]), (s["away_score"], s["home_score"]))
            last = g["plays"][-1]["score"]
            self.assertEqual((last["away"], last["home"]), (g["final"]["away"], g["final"]["home"]), "the running score ends on the final score")
            scores = [(p["score"]["away"], p["score"]["home"]) for p in g["plays"]]
            self.assertTrue(all(a2 >= a1 and h2 >= h1 for (a1, h1), (a2, h2) in zip(scores, scores[1:])), "scores never go down")
            self.assertEqual([p["i"] for p in g["plays"]], sorted(p["i"] for p in g["plays"]))
            roster = g["roster"]
            traced = [p for p in g["plays"] if p["trace"]]
            self.assertEqual(len(traced), g["traced_plays"])
            self.assertGreater(len(traced), 50)
            self.assertLess(len(traced), len(g["plays"]), "kicks and events without recorded movement are present and have no trace")
            for p in traced:
                t = p["trace"]
                self.assertEqual(len(t["ids"]), 22)
                for pid in t["ids"]:
                    self.assertIn(str(pid), roster, f"{gid} play {p['i']}: a player not on the public roster")
                self.assertEqual(len(PB.decode_b64(t["pos"])), t["n"] * 44 * 2)
                self.assertEqual(len(PB.decode_b64(t["ball"])), t["n"] * 3 * 2)
            for p in g["plays"]:
                if not p["trace"]:
                    self.assertIn("trace", p)
                    self.assertIsNone(p["trace"], "a play with no recorded movement says so; nothing is invented")

    def test_every_public_player_is_a_name_and_number_only(self):
        for gid, (e, g) in self.games.items():
            for pid, info in g["roster"].items():
                self.assertLessEqual(set(info), {"name", "number", "pos", "side"})


def trace_container(steps, ball_rows):
    """A trace container (docs/ARCHIVE.md) with 22 players standing still at (50, 10+k) and the given ball rows."""
    head = json.dumps({"game_id": "S001/x", "play_i": 0, "steps": steps, "dt": 0.05, "roles": ["r"] * 22, "ids": list(range(22)), "events": [[2, "throw", 0, 1]], "intents": [],
                       "los": 40.0, "formation": "f", "play": "p", "coverage": "c"}).encode()
    cols = []
    for c in range(44):
        base = (5000 if c % 2 == 0 else 1000 + c * 10)
        cols += [base + (s if c == 0 else 0) for s in range(steps)]            # player 0 walks +0.01 yd/step in x
    pos = []
    for c in range(44):
        prev = 0
        for s in range(steps):
            v = cols[c * steps + s]
            pos.append(v - prev if s else v)
            prev = v
    ball = []
    for c in range(3):
        prev = 0
        for s in range(steps):
            v = ball_rows[s][c]
            ball.append(v - prev if s else v)
            prev = v
    return PB.MAGIC + struct.pack("<BI", 1, len(head)) + head + struct.pack(f"<{len(pos)}h", *pos) + struct.pack(f"<{len(ball)}h", *ball)


class Encoding(unittest.TestCase):
    def test_round_trip_sampling_and_the_unplaced_ball(self):
        steps = 11
        rows = [(0, 0, 0)] + [(4000 + 10 * s, 2667, 100) for s in range(1, steps)]
        enc = PB.encode_trace(trace_container(steps, rows), every=2)
        self.assertEqual(enc["n"], 6)                                           # steps 0,2,4,6,8,10
        self.assertAlmostEqual(enc["t_end"], 0.5)
        self.assertAlmostEqual(enc["dt"], 0.1)
        self.assertEqual(enc["ball_from"], 1, "the (0,0,0) first ball sample is marked as not recorded")
        pos = struct.unpack(f"<{44 * 6}h", PB.decode_b64(enc["pos"]))
        acc, out, prev = [], [], 0
        for s in range(6):                                                      # column 0 only
            prev += pos[s]
            out.append(prev)
        self.assertEqual(out, [5000 + k for k in (0, 2, 4, 6, 8, 10)])
        self.assertEqual(enc["events"], [[0.1, "throw", 0, 1]])
        self.assertNotIn("intents", enc)
        every3 = PB.encode_trace(trace_container(steps, rows), every=3)
        self.assertEqual(every3["n"], 5)                                        # steps 0,3,6,9 and the last, 10
        self.assertAlmostEqual(every3["t_end"], 0.5)

    def test_a_placed_ball_has_no_gap(self):
        rows = [(4000, 2667, 100)] * 4
        self.assertEqual(PB.encode_trace(trace_container(4, rows))["ball_from"], 0)

    def test_published_files_are_immutable(self):
        d = tempfile.mkdtemp()
        try:
            g = load(os.path.join(PLAY, load(os.path.join(PLAY, "index.json"))["games"][0]["file"]))
            rel, size, sha = PB.write_game(d, g)
            self.assertEqual(PB.write_game(d, g)[0], rel, "writing the same game again changes nothing")
            os.chmod(os.path.join(d, rel), 0o644)
            with open(os.path.join(d, rel), "ab") as f:
                f.write(b" ")
            with self.assertRaises(PB.PlaybackError):
                PB.write_game(d, g)
        finally:
            shutil.rmtree(d, ignore_errors=True)


def serve(feed_root, playback_root):
    import preview_site

    class H(preview_site.Handler):
        pass
    H.feed_root, H.playback_root, H.site_root = feed_root, playback_root, preview_site.SITE
    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def rewrite_index(root, fn):
    p = os.path.join(root, "index.json")
    idx = load(p)
    fn(idx)
    with open(p, "w") as f:
        json.dump(idx, f)


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
class Browser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="dflpb")
        cls.srv = serve(FEED, PLAY)
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()
        cls.servers = [cls.srv]

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        for s in cls.servers:
            s.shutdown()
            s.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def variant(self, name, edit):
        """A copy of the playback files that `edit(dir)` has changed, served by its own server -> base url."""
        d = os.path.join(self.tmp, name)
        shutil.copytree(PLAY, d)
        edit(d)
        srv = serve(FEED, d)
        self.servers.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    def open(self, url, route, width=1280, height=900):
        page = self.browser.new_page(viewport={"width": width, "height": height})
        page.problems, page.requests = [], []
        page.on("pageerror", lambda e: page.problems.append(str(e)))
        page.on("console", lambda m: page.problems.append(m.text) if m.type == "error" and "404" not in m.text else None)
        page.on("request", lambda r: page.requests.append((r.method, r.url)))
        page.goto(f"{url}/#/{route}")
        return page

    def game_url(self, gid):
        from urllib.parse import quote
        return "game/" + quote(gid, safe="")

    def watch(self, gid=GID1, url=None, width=1280, height=900):
        page = self.open(url or self.url, self.game_url(gid), width, height)
        page.wait_for_function("window.DFL_VIEWER && DFL_VIEWER.replay() && DFL_VIEWER.replay().state().loaded", timeout=20000)
        return page

    def state(self, page):
        return page.evaluate("DFL_VIEWER.replay().state()")

    def first_trace_index(self, gid=GID1):
        g = next(g for e, g in ((e, load(os.path.join(PLAY, e["file"]))) for e in load(os.path.join(PLAY, "index.json"))["games"]) if e["gid"] == gid)
        return next(i for i, p in enumerate(g["plays"]) if p["trace"] and p["kind"] in ("run", "complete")), g

    # ---- links from the schedule
    def test_schedule_links_to_replays_only_for_games_that_have_one(self):
        page = self.open(self.url, "schedule/1")
        page.wait_for_selector(".game")
        self.assertEqual(page.locator("a.replay").count(), 2)
        hrefs = page.eval_on_selector_all("a.replay", "els => els.map(e => decodeURIComponent(e.getAttribute('href')))")
        self.assertEqual(sorted(hrefs), sorted(["#/game/" + GID1, "#/game/" + GID2]))
        page.goto(f"{self.url}/#/schedule/2")
        page.wait_for_selector(".game")
        self.assertEqual(page.locator("a.replay").count(), 0)
        self.assertEqual(page.problems, [])
        page.close()

    def test_a_link_is_not_offered_when_the_result_differs_from_the_published_one(self):
        def edit(d):
            rewrite_index(d, lambda i: i["games"][0].update(home_score=i["games"][0]["home_score"] + 1))
        url = self.variant("mismatch", edit)
        page = self.open(url, "schedule/1")
        page.wait_for_selector(".game")
        self.assertEqual(page.locator("a.replay").count(), 1)
        page.close()

    # ---- the viewer
    def test_desktop_viewer_shows_the_game_and_its_plays(self):
        page = self.watch()
        i, g = self.first_trace_index()
        s = self.state(page)
        self.assertEqual(s["plays"], len(g["plays"]))
        self.assertEqual(page.locator(".pb-row").count(), len(g["plays"]))
        self.assertIn("Oklahoma City Outlaws at Green Bay Glamour".lower(), page.inner_text(".pb-title").lower())
        self.assertEqual(s["view"], "h")
        self.assertFalse(page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"))
        page.evaluate(f"DFL_VIEWER.replay().goto({i})")
        s = self.state(page)
        self.assertTrue(s["traced"])
        self.assertEqual(page.locator(".pb-card").is_visible(), False)
        # 22 players and the ball are drawn: green field only vs players -> compare pixels of the canvas with and without the traced play
        drawn = page.evaluate("""() => { const c = document.querySelector('.pb-canvas'), x = c.getContext('2d'), d = x.getImageData(0, 0, c.width, c.height).data; const seen = new Set();
                for (let k = 0; k < d.length; k += 4) seen.add(d[k] + ',' + d[k+1] + ',' + d[k+2]); return seen.size; }""")
        self.assertGreater(drawn, 20)
        self.assertEqual(page.problems, [])
        page.close()

    def test_phone_viewer_is_vertical_and_fits(self):
        page = self.watch(width=390, height=844)
        s = self.state(page)
        self.assertEqual(s["view"], "v")
        self.assertFalse(page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"))
        box = page.locator(".pb-canvas").bounding_box()
        self.assertLessEqual(box["width"], 390)
        self.assertGreater(box["height"], box["width"])
        for sel in (".pb-btn", ".pb-sel", ".pb-range"):
            for b in page.locator(sel).all():
                self.assertGreaterEqual(b.bounding_box()["height"], 40, sel)
        i, _ = self.first_trace_index()
        page.evaluate(f"DFL_VIEWER.replay().goto({i})")
        self.assertTrue(self.state(page)["traced"])
        self.assertEqual(page.problems, [])
        page.close()

    def test_pointing_at_a_player_names_them_from_the_public_roster(self):
        page = self.watch()
        i, g = self.first_trace_index()
        page.evaluate(f"DFL_VIEWER.replay().goto({i}, 0.5)")
        play = g["plays"][i]
        for k in (0, 11):                                              # the quarterback and the first defender
            x, y = page.evaluate(f"DFL_VIEWER.replay().playerAt({k})")
            page.mouse.click(x, y)
            who = page.inner_text("[data-k='who']")
            info = g["roster"][str(play["trace"]["ids"][k])]
            self.assertIn(info["name"], who)
            self.assertIn(g["teams"][info["side"]]["abbr"], who)
            self.assertIn("offense" if k < 11 else "defense", who)
        page.evaluate("DFL_VIEWER.replay().goto(0)")
        page.mouse.click(300, 300)
        self.assertEqual(page.problems, [])
        page.close()

    def test_plays_without_recorded_movement_are_shown_as_events_not_motion(self):
        page = self.watch()
        s = self.state(page)
        self.assertEqual((s["index"], s["traced"], s["kind"]), (0, False, "kickoff"))
        self.assertTrue(page.locator(".pb-card").is_visible())
        self.assertIn("No player movement was recorded", page.inner_text(".pb-card"))
        self.assertTrue(page.locator("input[aria-label='Position within the play']").is_disabled())
        page.evaluate("DFL_VIEWER.replay().play()")
        page.evaluate("DFL_VIEWER.replay().advance(0.5)")
        self.assertEqual(self.state(page)["t"], 0, "time does not run through a play with no recorded movement")
        page.evaluate("DFL_VIEWER.replay().advance(5)")                 # the card's hold time passes; the replay moves on to the next play
        self.assertEqual(self.state(page)["index"], 1)
        page.close()

    def test_seeking_playing_speed_and_keys(self):
        page = self.watch()
        i, g = self.first_trace_index()
        page.evaluate(f"DFL_VIEWER.replay().goto({i})")
        t_end = g["plays"][i]["trace"]["t_end"]
        page.fill("input[aria-label='Position within the play']", "500")
        self.assertAlmostEqual(self.state(page)["t"], t_end / 2, delta=0.01)
        page.fill("input[aria-label='Position within the play']", "1000")
        self.assertAlmostEqual(self.state(page)["t"], t_end, delta=0.01)
        page.fill("input[aria-label='Play number']", "40")
        s = self.state(page)
        self.assertEqual((s["index"], s["t"], s["playing"]), (40, 0, False))
        page.select_option("select[aria-label='Speed']", "2")
        self.assertEqual(self.state(page)["speed"], 2)
        page.click("button[aria-label='Play']")
        self.assertTrue(self.state(page)["playing"])
        page.click("button[aria-label='Pause']")
        self.assertFalse(self.state(page)["playing"])
        page.click("button[aria-label='Next play']")
        self.assertEqual(self.state(page)["index"], 41)
        page.click("button[aria-label='Previous play']")
        page.click("button[aria-label='Previous play']")
        self.assertEqual(self.state(page)["index"], 39)
        page.click(".pb-row[data-i='10']")
        self.assertEqual(self.state(page)["index"], 10)
        page.evaluate("document.activeElement.blur()")                # (space on a focused button presses that button, as usual)
        page.keyboard.press("ArrowRight")
        self.assertEqual(self.state(page)["index"], 11)
        page.keyboard.press(" ")
        self.assertTrue(self.state(page)["playing"])
        page.keyboard.press(" ")
        self.assertFalse(self.state(page)["playing"])
        page.evaluate("DFL_VIEWER.replay().goto(0)")
        page.keyboard.press("ArrowLeft")
        self.assertEqual(self.state(page)["index"], 0, "seeking before the first play stays on the first play")
        page.evaluate("DFL_VIEWER.replay().goto(100000)")
        self.assertEqual(self.state(page)["index"], s["plays"] - 1)
        page.close()

    def test_a_traced_play_runs_to_its_end_then_the_next_play_starts(self):
        page = self.watch()
        i, g = self.first_trace_index()
        t_end = g["plays"][i]["trace"]["t_end"]
        page.evaluate(f"DFL_VIEWER.replay().goto({i})")
        page.evaluate("DFL_VIEWER.replay().play()")
        page.evaluate(f"DFL_VIEWER.replay().advance({t_end} / 2)")
        self.assertAlmostEqual(self.state(page)["t"], t_end / 2, delta=0.01)
        page.evaluate(f"DFL_VIEWER.replay().advance({t_end})")
        self.assertEqual(self.state(page)["index"], i)
        page.evaluate("DFL_VIEWER.replay().advance(1.5)")
        s = self.state(page)
        self.assertEqual((s["index"], s["t"], s["playing"]), (i + 1, 0, True))
        page.close()

    def test_the_end_of_the_game_stops_and_plays_again_from_the_start(self):
        page = self.watch()
        n = self.state(page)["plays"]
        page.evaluate(f"DFL_VIEWER.replay().goto({n - 1})")
        page.evaluate("DFL_VIEWER.replay().play()")
        page.evaluate("DFL_VIEWER.replay().advance(100)")
        s = self.state(page)
        self.assertEqual((s["index"], s["playing"]), (n - 1, False))
        self.assertEqual(page.inner_text("[data-k='away']") + "-" + page.inner_text("[data-k='home']"), "16-20")
        page.click("button[aria-label='Play again']")
        self.assertEqual(self.state(page)["index"], 0)
        page.close()

    # ---- missing, damaged and incompatible data
    def test_unknown_game_and_missing_index(self):
        page = self.open(self.url, self.game_url("S001/reg:w99:g99"))
        page.wait_for_selector("#app .err")
        self.assertIn("There is no replay for this game", page.inner_text("#app"))
        page.close()
        url = self.variant("noindex", lambda d: os.remove(os.path.join(d, "index.json")))
        page = self.open(url, self.game_url(GID1))
        page.wait_for_selector("#app .err")
        self.assertIn("not available", page.inner_text("#app"))
        page.goto(f"{url}/#/schedule/1")
        page.wait_for_selector(".game")
        self.assertEqual(page.locator("a.replay").count(), 0)
        page.close()

    def test_missing_game_file_and_checksum_mismatch_are_refused_with_a_message(self):
        def edit(d):
            idx = load(os.path.join(d, "index.json"))
            os.remove(os.path.join(d, next(e for e in idx["games"] if e["gid"] == GID1)["file"]))
            e2 = next(e for e in idx["games"] if e["gid"] == GID2)
            with open(os.path.join(d, e2["file"]), "ab") as f:
                f.write(b" ")
        url = self.variant("damaged", edit)
        page = self.open(url, self.game_url(GID1))
        page.wait_for_selector("#app .err")
        self.assertIn("could not be read", page.inner_text("#app"))
        page.goto(f"{url}/#/game/{__import__('urllib.parse').parse.quote(GID2, safe='')}")
        page.wait_for_selector("#app .err")
        self.assertIn("does not match its published checksum", page.inner_text("#app"))
        self.assertEqual(page.locator("canvas").count(), 0, "nothing from a file that failed its checksum is drawn")
        page.close()

    def test_an_incompatible_format_says_so_plainly_and_hides_the_links(self):
        url = self.variant("future", lambda d: rewrite_index(d, lambda i: i.update(format_version=2)))
        page = self.open(url, self.game_url(GID1))
        page.wait_for_selector("#app .err")
        txt = page.inner_text("#app")
        self.assertIn("format version 2", txt)
        self.assertIn("needs updating", txt)
        page.goto(f"{url}/#/schedule/1")
        page.wait_for_selector(".game")
        self.assertEqual(page.locator("a.replay").count(), 0)
        page.close()
        # a game file of a newer format, behind an index that still says 1
        def edit(d):
            idx = load(os.path.join(d, "index.json"))
            e = next(e for e in idx["games"] if e["gid"] == GID1)
            g = load(os.path.join(d, e["file"]))
            g["format_version"] = 2
            raw = PB.canonical(g)
            with open(os.path.join(d, e["file"]), "wb") as f:
                f.write(raw)
            e["sha256"], e["bytes"] = hashlib.sha256(raw).hexdigest(), len(raw)
            with open(os.path.join(d, "index.json"), "w") as f:
                json.dump(idx, f)
        url = self.variant("future_game", edit)
        page = self.open(url, self.game_url(GID1))
        page.wait_for_selector("#app .err")
        self.assertIn("version 2", page.inner_text("#app"))
        page.close()

    def test_a_damaged_trace_affects_only_its_own_play(self):
        i, _ = self.first_trace_index()

        def edit(d):
            idx = load(os.path.join(d, "index.json"))
            e = next(e for e in idx["games"] if e["gid"] == GID1)
            g = load(os.path.join(d, e["file"]))
            g["plays"][i]["trace"]["pos"] = g["plays"][i]["trace"]["pos"][:40]
            raw = PB.canonical(g)
            with open(os.path.join(d, e["file"]), "wb") as f:
                f.write(raw)
            e["sha256"], e["bytes"] = hashlib.sha256(raw).hexdigest(), len(raw)
            with open(os.path.join(d, "index.json"), "w") as f:
                json.dump(idx, f)
        url = self.variant("badtrace", edit)
        page = self.watch(url=url)
        page.evaluate(f"DFL_VIEWER.replay().goto({i})")
        self.assertFalse(self.state(page)["traced"])
        self.assertIn("damaged", page.inner_text(".pb-card"))
        page.evaluate(f"DFL_VIEWER.replay().goto({i + 1})")
        self.assertEqual(page.problems, [])
        page.close()

    # ---- moving between games, refreshes, and not changing anything
    def test_switching_games_replaces_the_viewer_and_a_quick_double_switch_ends_on_the_last(self):
        page = self.watch(GID1)
        page.evaluate("window.__first = DFL_VIEWER.replay()")
        page.evaluate(f"location.hash = '#/{self.game_url(GID2)}'")
        page.wait_for_function(f"DFL_VIEWER.replay() && DFL_VIEWER.replay().state().gid === {json.dumps(GID2)}")
        self.assertFalse(page.evaluate("window.__first === DFL_VIEWER.replay()"))
        self.assertIn("las vegas invaders at new orleans", page.inner_text(".pb-title").lower())
        page.evaluate(f"location.hash = '#/{self.game_url(GID1)}'; setTimeout(function () {{ location.hash = '#/{self.game_url(GID2)}'; }}, 0)")
        page.wait_for_timeout(1500)
        self.assertEqual(self.state(page)["gid"], GID2)
        page.evaluate("location.hash = '#/standings'")
        page.wait_for_selector("#app h1")
        page.wait_for_function("DFL_VIEWER.replay() === null")
        self.assertEqual(page.locator("canvas").count(), 0)
        self.assertEqual(page.problems, [])
        page.close()

    def test_a_feed_refresh_does_not_restart_the_replay(self):
        page = self.watch()
        page.evaluate("DFL_VIEWER.replay().goto(30)")
        page.evaluate("window.__v = DFL_VIEWER.replay()")
        page.evaluate("DFL_VIEWER.render()")
        page.wait_for_timeout(800)
        self.assertTrue(page.evaluate("window.__v === DFL_VIEWER.replay()"))
        self.assertEqual(self.state(page)["index"], 30)
        self.assertEqual(page.locator(".pb-canvas").count(), 1)
        page.close()

    def test_watching_only_reads_published_files_and_changes_nothing(self):
        before = tree_digest(FIX)
        page = self.watch()
        page.evaluate("DFL_VIEWER.replay().goto(40)")
        for _ in range(3):
            page.evaluate("DFL_VIEWER.replay().play()")
            page.evaluate("DFL_VIEWER.replay().advance(2)")
            page.evaluate("DFL_VIEWER.replay().goto(0)")
        page.goto(f"{self.url}/#/{self.game_url(GID2)}")
        page.wait_for_function(f"DFL_VIEWER.replay() && DFL_VIEWER.replay().state().gid === {json.dumps(GID2)}")
        self.assertTrue(all(m == "GET" for m, u in page.requests), "the page only ever reads")
        for m, u in page.requests:
            path = u[len(self.url):]
            self.assertRegex(path, r"^/(|index\.html|assets/[^?#]+|feed/[^?#]+)(\?.*)?$")
        self.assertEqual(tree_digest(FIX), before, "repeated viewing leaves the published files byte-identical")
        page.close()

    def test_the_viewer_code_is_loaded_only_when_a_replay_is_opened(self):
        page = self.open(self.url, "standings")
        page.wait_for_selector("#app h1")
        page.wait_for_timeout(500)
        self.assertFalse(any("playback.js" in u or "playback.css" in u for _, u in page.requests))
        self.assertFalse(any("/games/" in u for _, u in page.requests), "no game file is downloaded to browse the site")
        page.goto(f"{self.url}/#/schedule/1")
        page.wait_for_selector(".game")
        page.wait_for_timeout(300)
        self.assertFalse(any("playback.js" in u for _, u in page.requests), "the schedule lists replays from the small index only")
        page.click("a.replay >> nth=0")
        page.wait_for_function("DFL_VIEWER.replay() && DFL_VIEWER.replay().state().loaded", timeout=20000)
        self.assertEqual(sum(1 for _, u in page.requests if "/feed/playback/games/" in u), 1, "exactly one game file for one game")
        page.close()


class Static(unittest.TestCase):
    def test_the_viewer_code_follows_the_site_rules(self):
        import re
        for name in ("playback.js", "playback.css"):
            with open(os.path.join(ROOT, "site", "assets", name), encoding="utf-8") as f:
                t = f.read()
            self.assertEqual(re.findall(r"https?://[^\s\"')]+", t), [], name)
            for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "javascript:", "@import", "localStorage", "XMLHttpRequest", "WebSocket", "POST"):
                self.assertNotIn(bad, t, f"{name}: {bad}")

    def test_the_viewer_does_not_touch_the_simulation(self):
        with open(os.path.join(ROOT, "site", "assets", "playback.js"), encoding="utf-8") as f:
            t = f.read()
        self.assertEqual(sorted(set(__import__("re").findall(r"fetch\(([^,)]+)", t))), ["base + entry.file"], "the viewer fetches one thing: the game file named in the index")


if __name__ == "__main__":
    unittest.main()
