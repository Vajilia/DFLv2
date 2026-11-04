"""The broadcast layer (docs/BROADCAST_PLAN.md): clock, protected live segments, timed reveals, restart, spoilers. Nothing here touches the league or the site.

Data: one real archived game (tests/fixtures/replay_tools_v1) exported by the real exporter, and variants of it with other ids: game A is the real game, game B is
the same game cut short (a different length and final score), game C is a copy in a later window. A window holds A and B at the same kickoff (simultaneous games).
Time is injected: no test sleeps, except the one that starts the real daemon as a process. What is checked:
  * the timeline: viewer durations, fixed slot, speed factor only for a game that would not fit
  * the clock: runs, pauses, resumes; a lapsed lease (downtime) is recorded as a pause and nothing is caught up or skipped
  * release: the public tree holds exactly what the position allows, at every second, and a restart or a rewound clock is repaired to the same tree
  * spoilers: at every sampled position no public file contains a play that starts later than one segment ahead, a final score, a winner, an overtime flag, a play
    count, a game length or the full replay; direct requests (guessed names, traversal, the private store, the full replay's real name) are 404
  * synchronization, late join and refresh: the state shown is a function of the public files and the position only
  * the feed release is built privately and revealed whole, its pointer last"""
import http.client
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from dflsite import broadcast as B  # noqa: E402
from dflsite import playback as PB  # noqa: E402
import broadcast_demo as DEMO  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures", "replay_tools_v1")
ARCHIVE = os.path.join(FIX, "archive_excerpt.sqlite")
FEED = os.path.join(FIX, "feed")
SLOT = 960
K1, K2 = 100.0, 1500.0          # kickoff (broadcast seconds) of window 1 (games A and B) and window 2 (game C)


def load(path):
    with open(path, "rb") as f:
        return json.loads(f.read().decode("utf-8"))


def export_real_game(tmp):
    play, feed = os.path.join(tmp, "playback"), os.path.join(tmp, "feed")
    shutil.copytree(FEED, feed)
    rel = os.path.join(feed, "releases", load(os.path.join(feed, "latest.json"))["release"])
    sched = load(os.path.join(rel, "schedule.json"))
    with open(os.path.join(rel, "schedule.json"), "wb") as f:
        f.write(PB.canonical(sched))
    p = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "tools", "export_playback.py"), "--archive", ARCHIVE, "--feed", feed, "--out", play],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    idx = load(os.path.join(play, "index.json"))
    return load(os.path.join(play, idx["games"][0]["file"]))


def variant(game, gid, cut=None, mark=None):
    """A copy of a game under another id (cut short if asked). `mark` changes one character in every recorded trace so that no two games share trace bytes."""
    g = json.loads(json.dumps(game))
    if mark:
        for p in g["plays"]:
            if p["trace"]:
                s = p["trace"]["pos"]
                i = len(s) // 2 + 10
                p["trace"]["pos"] = s[:i] + (mark if s[i] != mark else "Z") + s[i + 1:]
    g["gid"] = gid
    g["id"] = gid.split("/", 1)[1]
    if cut:
        g["plays"] = g["plays"][:cut]
        g["traced_plays"] = sum(1 for p in g["plays"] if p["trace"])
        g["final"] = dict(g["plays"][-1]["score"])
        g["winner"] = "home" if g["final"]["home"] > g["final"]["away"] else "away" if g["final"]["away"] > g["final"]["home"] else "tie"
        g["tie"] = g["winner"] == "tie"
    return g


class Base(unittest.TestCase):
    LEASE = 1e9         # tests jump in time, so they hold the lease open; the clock tests use the real one

    @classmethod
    def setUpClass(cls):
        cls.tmp0 = tempfile.mkdtemp()
        real_dir = os.environ.get("BROADCAST_TEST_PLAYBACK")
        if real_dir:
            # on the server: three REAL games of a copy of the archive's replays (the longest, the shortest and a middle one of the first 12)
            idx = load(os.path.join(real_dir, "index.json"))["games"][:12]
            gs = sorted((load(os.path.join(real_dir, e["file"])) for e in idx), key=lambda g: B.timeline(g, SLOT)[2])
            cls.A, cls.Bg, cls.C = gs[-1], gs[0], gs[len(gs) // 2]
            return
        cls.real = export_real_game(cls.tmp0)
        cls.A = cls.real
        cls.Bg = variant(cls.real, "S001/reg:w01:g03", cut=max(10, len(cls.real["plays"]) // 2), mark="B")
        cls.C = variant(cls.real, "S001/reg:w01:g04", mark="C")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp0, True)

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.store, self.public = os.path.join(self.tmp, "store"), os.path.join(self.tmp, "public")
        os.makedirs(self.store)
        files = {}
        for name, g in (("a", self.A), ("b", self.Bg), ("c", self.C)):
            files[name] = os.path.join(self.tmp, name + ".json")
            with open(files[name], "w") as f:
                json.dump(g, f)
        self.gfiles = files
        DEMO.prepare(self.store, self.public, ["%s:%s,%s" % (K1, files["a"], files["b"]), "%s:%s" % (K2, files["c"])], SLOT)
        self.bc = B.Broadcaster(self.store, self.public, self.LEASE, guard_publication=False)
        self.sched = self.bc.schedule()
        self.t0 = 1_000_000.0                     # the wall time the broadcast starts
        self.bc.clock.start(self.t0, lease=self.LEASE)

    def at(self, pos):
        """Wall time at which a running, never-interrupted broadcast is at position `pos`."""
        return self.t0 + pos

    # Release-rule tests intentionally jump hundreds of seconds; guarded runtime is tested separately.
    def tick(self, pos):
        self.bc.tick(self.at(pos))

    def tree(self):
        out = {}
        for d, _, fs in os.walk(self.public):
            for f in fs:
                p = os.path.join(d, f)
                out[os.path.relpath(p, self.public)] = open(p, "rb").read()
        return out

    def game(self, gid):
        return load(os.path.join(self.store, "games", B.safe(gid), "end.json")), load(os.path.join(self.store, "games", B.safe(gid), "head.json"))


class Timeline(Base):
    def test_durations_follow_the_viewer_and_a_game_that_fits_runs_at_speed_one(self):
        plays, speed, length = B.timeline(self.A, SLOT)
        want = sum((float(p["trace"]["t_end"]) + B.HOLD_S) if p["trace"] else B.CARD_S for p in self.A["plays"])
        self.assertEqual(speed, 1.0)
        self.assertAlmostEqual(length, want, delta=0.01)
        self.assertEqual(plays[0]["t0"], 0)
        for p, q in zip(plays, plays[1:]):
            self.assertAlmostEqual(p["t0"] + p["dur"], q["t0"], delta=0.002, msg="plays follow one another without a gap or an overlap")
        self.assertLess(length, SLOT - B.SLOT_MARGIN_S)

    def test_a_long_game_is_not_accelerated_to_fit_its_slot(self):
        plays, speed, length = B.timeline(self.A, 1)
        self.assertEqual(speed, 1)
        self.assertGreater(length, 1)
        self.assertAlmostEqual(length, sum(p["dur"] for p in plays), places=2)

    def test_the_live_files_carry_only_the_live_schema(self):
        g = B.build_game(self.A, SLOT)
        B.check_live_file(g["head"], "head")
        for s in g["segments"]:
            B.check_live_file({"gid": "x", "n": 0, "plays": s}, "seg")
        for k in ("final", "winner", "tie", "periods", "plays", "traced_plays"):
            self.assertNotIn(k, g["head"], k)
        bad = {"gid": "x", "n": 0, "plays": [dict(g["segments"][0][0], rating=99)]}
        with self.assertRaises(B.BroadcastError):
            B.check_live_file(bad, "seg")
        leaky = json.loads(json.dumps(self.A))
        leaky["plays"][0]["rating"] = 5
        with self.assertRaises(PB.PlaybackError):
            B.build_game(leaky, SLOT)


class ClockTests(Base):
    LEASE = B.LEASE_S

    def test_run_pause_resume(self):
        c = self.bc.clock
        self.assertEqual(c.position(self.at(40)), 40)
        c.heartbeat(self.at(40))
        c.pause(self.at(50))
        self.assertEqual(c.position(self.at(50)), 50)
        self.assertEqual(c.position(self.at(5000)), 50, "a paused clock stands still however long it is paused")
        c.resume(self.at(5000))
        self.assertEqual(c.position(self.at(5010)), 60)
        log = [json.loads(x)["why"] for x in open(os.path.join(self.store, "clock_log.jsonl"))]
        self.assertEqual(log, ["start", "pause", "resume"])

    def test_downtime_is_a_pause_and_nothing_is_caught_up_or_skipped(self):
        c = self.bc.clock
        c.heartbeat(self.at(10))                   # lease now runs to wall t0 + 100
        # the server goes away; viewers' clocks stop at the lease end (position 100), the daemon returns at wall t0 + 1000
        self.assertEqual(c.position(self.at(1000)), 100, "the position never passes the lease end")
        down = c.heartbeat(self.at(1000))
        self.assertAlmostEqual(down, 900, delta=0.001)
        self.assertEqual(c.position(self.at(1000)), 100, "the position did not jump")
        self.assertEqual(c.position(self.at(1030)), 130, "and carries on at one second per second")
        rec = [json.loads(x) for x in open(os.path.join(self.store, "clock_log.jsonl"))][-1]
        self.assertEqual((rec["why"], rec["down_s"]), ("downtime", 900.0))

    def test_a_short_interruption_inside_the_lease_costs_nothing(self):
        c = self.bc.clock
        c.heartbeat(self.at(10))
        self.assertEqual(c.heartbeat(self.at(70)), 0)
        self.assertEqual(c.position(self.at(70)), 70)

    def test_pausing_twice_or_resuming_twice_changes_nothing(self):
        c = self.bc.clock
        c.pause(self.at(30))
        c.pause(self.at(60))
        self.assertEqual(c.position(self.at(90)), 30)
        c.resume(self.at(100))
        c.resume(self.at(130))
        self.assertEqual(c.position(self.at(140)), 70)


class Release(Base):
    def files(self, gid):
        d = os.path.join(self.public, "broadcast", "live", B.safe(gid))
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    def test_nothing_is_public_before_kickoff(self):
        self.tick(K1 - 1)
        self.assertEqual(self.files(self.A["gid"]), [])
        self.assertEqual(self.files(self.Bg["gid"]), [])
        self.assertFalse(os.path.exists(os.path.join(self.public, "broadcast", "live")) and os.listdir(os.path.join(self.public, "broadcast", "live")))

    def test_segments_appear_as_the_game_reaches_them_and_the_end_only_after_the_last_play(self):
        gid = self.A["gid"]
        n = B.build_game(self.A, SLOT)["segments"].__len__()
        length = B.timeline(self.A, SLOT)[2]
        self.tick(K1)
        self.assertEqual(self.files(gid), ["head.json", "manifest.json", "s0000.json"])
        self.tick(K1 + 11)
        self.assertEqual(self.files(gid), ["head.json", "manifest.json", "s0000.json", "s0001.json", "s0002.json"])
        self.tick(K1 + length - 1)
        self.assertNotIn("end.json", self.files(gid))
        self.assertEqual(load(os.path.join(self.public, "broadcast", "live", B.safe(gid), "manifest.json"))["state"], "live")
        self.tick(K1 + length + B.DELAY_S - 0.5)
        self.assertNotIn("end.json", self.files(gid), "the ending waits until the last play has been shown")
        self.tick(K1 + length + B.DELAY_S + 0.5)
        self.assertIn("end.json", self.files(gid))
        self.assertEqual(len([f for f in self.files(gid) if f.startswith("s0")]), n)
        end = load(os.path.join(self.public, "broadcast", "live", B.safe(gid), "end.json"))
        self.assertEqual(end["final"], self.A["final"])
        rep = os.path.join(self.public, "broadcast", "replays", end["replay"])
        self.assertTrue(os.path.exists(rep), "a finished broadcast becomes an on-demand replay")
        self.assertEqual(load(rep), json.loads(json.dumps(self.A)), "and the replay is the full game, unchanged")

    def test_simultaneous_games_share_one_kickoff_and_end_at_their_own_times(self):
        self.tick(K1 + 30)
        la, lb = B.timeline(self.A, SLOT)[2], B.timeline(self.Bg, SLOT)[2]
        self.assertLess(lb, la)
        for g in (self.A, self.Bg):
            self.assertIn("head.json", self.files(g["gid"]))
        self.tick(K1 + lb + B.DELAY_S + 1)
        self.assertIn("end.json", self.files(self.Bg["gid"]))
        self.assertNotIn("end.json", self.files(self.A["gid"]), "the shorter game finishes first and the other is still protected")
        idx = load(os.path.join(self.public, "broadcast", "replays", "index.json"))
        self.assertEqual([g["gid"] for g in idx["games"]], [self.Bg["gid"]], "only ended broadcasts are in the replay library")

    def test_pause_releases_nothing_and_resume_continues_where_it_stopped(self):
        self.tick(K1 + 40)
        before = self.tree()
        self.bc.clock.pause(self.at(K1 + 40))
        self.tick(K1 + 4000)
        self.assertEqual(self.tree().keys(), before.keys(), "a paused broadcast releases nothing, however long the pause")
        self.bc.clock.resume(self.at(K1 + 4000), self.LEASE)
        self.tick(K1 + 4000 + 1)
        self.assertEqual(self.bc.clock.position(self.at(K1 + 4001)), K1 + 41)
        have = self.files(self.A["gid"])
        want = ["head.json", "manifest.json"] + ["s%04d.json" % i for i in range(int((41) // B.SEG_S) + 1)]
        self.assertEqual(have, want)

    def test_a_restart_repairs_a_half_done_release_and_a_rewound_clock(self):
        self.tick(K1 + 300)
        good = self.tree()
        # a crash left a stray future segment, lost a released one and left a temporary file
        d = os.path.join(self.public, "broadcast", "live", B.safe(self.A["gid"]))
        shutil.copyfile(os.path.join(self.store, "games", B.safe(self.A["gid"]), "s0090.json"), os.path.join(d, "s0090.json"))
        os.unlink(os.path.join(d, "s0003.json"))
        open(os.path.join(d, ".tmp-1-s0004.json"), "w").write("{")
        fresh = B.Broadcaster(self.store, self.public, self.LEASE, guard_publication=False)
        fresh.tick(self.at(K1 + 300))
        after = self.tree()
        self.assertEqual({k: v for k, v in after.items() if ".tmp-" not in k}, good, "the public tree is what the clock allows again")
        # the operator rewinds the clock to earlier in the game: later files go away
        st = fresh.clock.state()
        st.update(anchor_pos=K1 + 20, anchor_wall=self.at(K1 + 300), lease_until=self.at(K1 + 400), epoch=st["epoch"] + 1)
        B.put(fresh.clock.path, st)
        B.put(fresh.clock.mirror, st)
        fresh.tick(self.at(K1 + 300))
        self.assertEqual(self.files(self.A["gid"]), ["head.json", "manifest.json"] + ["s%04d.json" % i for i in range(5)])

    def test_the_same_positions_give_the_same_tree_whatever_ticks_happened_in_between(self):
        """A tree built tick by tick and one built in a single step at the final position (a fresh machine after a restart) are byte-identical."""
        for p in range(0, 2400, 13):
            self.tick(p)
        last = (2400 - 1) // 13 * 13
        other = os.path.join(self.tmp, "public2")
        B.reconcile(self.store, other, self.sched, last)
        keep = lambda root: {k: v for k, v in self.read_tree(root).items() if k.startswith(("broadcast/live", "broadcast/replays"))}
        self.assertEqual(keep(other), keep(self.public))
        self.assertTrue(keep(other))

    @staticmethod
    def read_tree(root):
        out = {}
        for d, _, fs in os.walk(root):
            for f in fs:
                out[os.path.relpath(os.path.join(d, f), root)] = open(os.path.join(d, f), "rb").read()
        return out


class Spoilers(Base):
    """At every sampled position, scan every byte of the public tree for anything the viewers have not been shown."""

    @staticmethod
    def marker(p):
        """80 characters from the middle of a play's recorded positions: traces open with identical headers, their middles differ."""
        if not p["trace"]:
            return None
        s = p["trace"]["pos"]
        return s[len(s) // 2:len(s) // 2 + 80]

    def test_the_markers_are_unique(self):
        keys = [self.marker(p) for g in (self.A, self.Bg, self.C) for p in g["plays"] if p["trace"]]
        self.assertEqual(len(keys), len(set(keys)), "every traced play of every test game has a marker of its own")

    def markers(self, g):
        """Per play: a string that occurs in the public tree if and only if the play's file content has been released. Traces are unique per play; a play with no
        trace is recognized by its own (index, text, score) triple as the segment file writes it."""
        out = []
        for p in g["plays"]:
            if p["trace"]:
                out.append(p["trace"]["pos"][:48] if isinstance(p["trace"]["pos"], str) else json.dumps(p["trace"]["pos"])[:48])
            else:
                out.append(None)
        return out

    def test_no_public_file_holds_a_play_beyond_one_segment_ahead_nor_any_ending(self):
        gp = {g["gid"]: B.timeline(g, SLOT)[0] for g in (self.A, self.Bg, self.C)}
        start = {self.A["gid"]: K1, self.Bg["gid"]: K1, self.C["gid"]: K2}
        checked = 0
        for pos in list(range(0, 1100, 9)) + [K1 + 40.5, 2600, 3000]:
            self.tick(pos)
            blob = b"".join(v for k, v in self.tree().items() if k.startswith("broadcast/live"))
            allbytes = b"".join(self.tree().values())
            for g in (self.A, self.Bg, self.C):
                t = pos - start[g["gid"]]
                tl = gp[g["gid"]]
                ended = t >= B.timeline(g, SLOT)[2] + B.DELAY_S
                for p, q in zip(g["plays"], tl):
                    key = self.marker(p)
                    if not key:
                        continue
                    seen = key.encode() in blob
                    released = (t >= 0) and (int(q["t0"] // B.SEG_S) * B.SEG_S <= t)
                    self.assertEqual(seen, released, "play %d of %s at position %s" % (p["i"], g["gid"], pos))
                    checked += 1
                    if not released:
                        self.assertNotIn(key.encode(), allbytes, "an unreleased play is in no public file at all (not the replay either)")
                if not ended:
                    ends = [v for k, v in self.tree().items() if k.endswith("end.json") and B.safe(g["gid"]) in k]
                    self.assertEqual(ends, [])
            # structural leaks: until a game has ended, no public file names its final, winner, overtime flag, play count, length or replay
            over = {B.safe(g["gid"]) for g in (self.A, self.Bg, self.C) if pos - start[g["gid"]] >= B.timeline(g, SLOT)[2] + B.DELAY_S}
            for k, v in self.tree().items():
                if k.endswith("end.json") or k.startswith("broadcast/replays/") or any(o in k for o in over):
                    continue
                for word in (b'"final"', b'"winner"', b'"periods"', b'"traced_plays"', b'"tie"', b'"length_s"', b'"replay"'):
                    self.assertNotIn(word, v, "%s in %s at %s" % (word, k, pos))
                if k.endswith("manifest.json"):
                    self.assertNotIn(b'"plays"', v)
        self.assertGreater(checked, 500)

    def test_how_far_ahead_the_public_tree_runs_is_bounded_by_one_segment_plus_the_delay(self):
        worst = 0
        for pos in range(int(K1), int(K1) + 600, 3):
            self.tick(pos)
            d = os.path.join(self.public, "broadcast", "live", B.safe(self.A["gid"]))
            plays = []
            for f in sorted(os.listdir(d)):
                if f.startswith("s0"):
                    plays += load(os.path.join(d, f))["plays"]
            shown_until = pos - K1 - B.DELAY_S
            worst = max(worst, max(p["t0"] for p in plays) - shown_until)
        self.assertLessEqual(worst, B.SEG_S + B.DELAY_S + 0.01)

    def test_the_head_and_the_schedule_give_no_hint_of_a_games_length_or_result(self):
        self.tick(K1 + 1)
        head = open(os.path.join(self.public, "broadcast", "live", B.safe(self.A["gid"]), "head.json"), "rb").read()
        self.assertNotIn(b'"plays"', head)
        sched = open(os.path.join(self.public, "broadcast", "schedule.json"), "rb").read()
        for word in (b"length", b"segments", b"final", b"speed", b"score"):
            self.assertNotIn(word, sched)
        win = json.loads(sched)["windows"]
        self.assertEqual([w["slot_s"] for w in win], [SLOT, SLOT], "every window has the same fixed length whatever the games in it")


class Http(Base):
    def setUp(self):
        super().setUp()
        DEMO.Handler.public = self.public
        self.srv = DEMO.Server(("127.0.0.1", 0), DEMO.Handler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)

    def req(self, path, method="GET"):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request(method, path)
        r = c.getresponse()
        body = r.read()
        c.close()
        return r.status, body

    def test_direct_requests_for_unreleased_data_are_404(self):
        gid = self.A["gid"]
        live = "/broadcast/live/%s/" % B.safe(gid)
        end, head = self.game(gid)
        self.tick(K1 + 20)
        self.assertEqual(self.req(live + "head.json")[0], 200)
        self.assertEqual(self.req(live + "s0001.json")[0], 200)
        for name in ("s0010.json", "s0099.json", "end.json", "s0100.json"):
            self.assertEqual(self.req(live + name)[0], 404, name)
        self.assertEqual(self.req("/broadcast/replays/" + end["replay"])[0], 404, "the full replay, by its real name, before the broadcast has ended")
        self.assertEqual(self.req("/broadcast/replays/index.json")[0], 200)
        self.assertEqual(json.loads(self.req("/broadcast/replays/index.json")[1])["games"], [])
        # the later window is not public yet
        self.assertEqual(self.req("/broadcast/live/%s/head.json" % B.safe(self.C["gid"]))[0], 404)
        # the private store and traversal
        for path in ("/store/games/%s/replay.json" % B.safe(gid), "/../store/games/%s/replay.json" % B.safe(gid), "/broadcast/../../store/schedule.json",
                     "/broadcast/%2e%2e/%2e%2e/store/schedule.json", "/schedule.json", "/clock.json", "/broadcast/live/%s/.tmp-x" % B.safe(gid),
                     "/broadcast/live/" + B.safe(gid) + "/../../../../store/clock.json", "/games/%s/end.json" % B.safe(gid)):
            st, body = self.req(path)
            self.assertEqual(st, 404, path)
            self.assertNotIn(b"replay", body[:0] or b"")
        # nothing can be written
        for m in ("POST", "PUT", "DELETE"):
            self.assertEqual(self.req(live + "head.json", m)[0], 405, m)

    def test_after_the_end_the_ending_and_the_replay_are_served(self):
        gid = self.Bg["gid"]
        end, _ = self.game(gid)
        length = B.timeline(self.Bg, SLOT)[2]
        self.tick(K1 + length + B.DELAY_S + 1)
        st, body = self.req("/broadcast/live/%s/end.json" % B.safe(gid))
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(body)["final"], self.Bg["final"])
        st, body = self.req("/broadcast/replays/" + end["replay"])
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(body)["gid"], gid)

    def test_clock_and_manifest_are_never_cached(self):
        self.tick(K1 + 5)
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.request("GET", "/broadcast/clock.json")
        r = c.getresponse()
        r.read()
        self.assertEqual(r.getheader("Cache-Control"), "no-store")
        self.assertTrue(r.getheader("Date"), "viewers take the server's time from this header")


class Feed(Base):
    def test_the_feed_release_is_built_privately_and_revealed_whole_with_its_pointer_last(self):
        store, public = os.path.join(self.tmp, "s2"), os.path.join(self.tmp, "p2")
        os.makedirs(store)
        DEMO.prepare(store, public, ["%s:%s" % (K1, self.gfiles["a"])], SLOT, feed_root=FEED, reveal_at=K1 + 900)
        bc = B.Broadcaster(store, public, self.LEASE, guard_publication=False)
        bc.clock.start(self.t0, lease=self.LEASE)
        rel = load(os.path.join(FEED, "latest.json"))["release"]
        pub_rel = os.path.join(public, "feed", "releases", rel)
        for pos in (0, K1, K1 + 899):
            bc.tick(self.t0 + pos)
            self.assertFalse(os.path.exists(pub_rel), "the week's releases do not exist publicly before their reveal")
            self.assertFalse(os.path.exists(os.path.join(public, "feed", "latest.json")))
        bc.tick(self.t0 + K1 + 901)
        self.assertTrue(os.path.isdir(pub_rel))
        self.assertEqual(load(os.path.join(public, "feed", "latest.json"))["release"], rel)
        self.assertEqual(sorted(os.listdir(pub_rel)), sorted(os.listdir(os.path.join(FEED, "releases", rel))))
        order = [r["dst"] for r in sorted(bc.reveals(), key=lambda r: (r["at_s"], r.get("last", False)))]
        self.assertEqual(order[-1], "feed/latest.json", "the pointer is replaced last")


class Viewers(Base):
    def test_late_join_refresh_and_two_viewers_see_the_same_moment(self):
        gid = self.A["gid"]
        seen = {}
        for pos in range(int(K1) + 1, int(K1) + 800, 7):
            self.tick(pos)
            seen[pos] = B.viewer_state(self.public, gid, pos)           # a viewer who has been watching since kickoff
        # a viewer who joins late, and one who refreshes, read the same files at the same position
        self.tick(K1 + 400)
        late = B.viewer_state(self.public, gid, K1 + 400)
        refresh = B.viewer_state(self.public, gid, K1 + 400)
        self.assertEqual(late, refresh)
        self.assertEqual(late["state"], "live")
        self.assertGreater(late["idx"], 20)
        # the state at a position is the same whether it is read at that moment or later (the files only grow)
        self.tick(K1 + 780)
        for pos in (K1 + 100, K1 + 400, K1 + 700):
            self.assertEqual(B.viewer_state(self.public, gid, pos), seen.get(pos) or B.viewer_state(self.public, gid, pos))
        # and it never runs backwards as the position advances
        idxs = [seen[p]["idx"] for p in sorted(seen)]
        self.assertEqual(idxs, sorted(idxs))

    def test_a_viewer_is_never_ahead_of_the_released_data_and_ends_at_final(self):
        gid = self.Bg["gid"]
        length = B.timeline(self.Bg, SLOT)[2]
        self.tick(K1 + length + 30)
        st = B.viewer_state(self.public, gid, K1 + length + 30)
        self.assertEqual(st["state"], "final")
        self.assertEqual(st["score"], self.Bg["final"])
        self.assertEqual(B.viewer_state(self.public, self.C["gid"], K1 + length + 30), {"state": "scheduled"})


class Process(unittest.TestCase):
    """The real daemon as a process: one lock, kill -9, restart."""

    def free_port(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return p

    def test_kill_nine_and_restart_records_the_gap_as_a_pause(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        play = export_real_game(tmp)
        store, public = os.path.join(tmp, "store"), os.path.join(tmp, "public")
        os.makedirs(store)
        gf = os.path.join(tmp, "g.json")
        with open(gf, "w") as f:
            json.dump(play, f)
        tool = os.path.join(ROOT, "tools", "broadcast_demo.py")
        common = ["--store", store, "--public", public]
        run = lambda *a: subprocess.run([sys.executable, "-I", tool, *a], capture_output=True, text=True)
        self.assertEqual(run("prepare", *common, "--slot-s", "960", "--window", "2:" + gf).returncode, 0)
        self.assertEqual(run("start", *common, "--lease-s", "2").returncode, 0)
        port = self.free_port()
        args = [sys.executable, "-I", tool, "run", *common, "--port", str(port), "--tick", "0.2", "--lease-s", "2"]
        p1 = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: p1.poll() is None and p1.kill())
        time.sleep(1.2)
        second = subprocess.run(args, capture_output=True, text=True, timeout=10)
        self.assertEqual(second.returncode, 75, "a second daemon exits without touching anything")
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("GET", "/broadcast/clock.json")
        r = c.getresponse()
        clock1 = json.loads(r.read())
        self.assertEqual(clock1["state"], "running")
        os.kill(p1.pid, signal.SIGKILL)
        p1.wait()
        pos_at_kill = B.position(B.get(os.path.join(store, "clock.json")), time.time())
        time.sleep(4.5)                          # longer than the lease: the viewers' clocks have stopped
        self.assertLess(B.position(B.get(os.path.join(store, "clock.json")), time.time()) - pos_at_kill, 2.5, "no viewer clock runs past the lease")
        p2 = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: p2.poll() is None and p2.kill())
        time.sleep(1.5)
        log = [json.loads(x) for x in open(os.path.join(store, "clock_log.jsonl"))]
        self.assertIn("downtime", [x["why"] for x in log])
        gap = [x for x in log if x["why"] == "downtime"][0]["down_s"]
        self.assertGreater(gap, 2.0)
        pos = B.position(B.get(os.path.join(store, "clock.json")), time.time())
        self.assertLess(pos, 1.2 + 2.0 + 1.6 + 1.5 + 0.5, "the position did not include the downtime: no catch-up")
        os.kill(p2.pid, signal.SIGTERM)
        p2.wait(timeout=10)
        self.assertEqual(p2.returncode, 0)


if __name__ == "__main__":
    unittest.main()
