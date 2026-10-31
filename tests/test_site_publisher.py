"""Publication under concurrency, consistent snapshots under a live commit, and the viewer's recovery (Edition 8.58 milestone).

Proved here, with real processes and a real browser where that is what the claim is about:
  * one publisher at a time: a second publisher is refused at once and touches nothing, in particular not the first one's temporary directory;
    a publisher killed with SIGKILL leaves no stale lock, and the next one cleans up only what is old;
  * a release is pruned only after the grace period, never while live; the feed does not move backwards;
  * the exporter's snapshot is taken with SQLite's online backup while a real event commit is in progress (blocked inside its transaction, and
    again after the archive batch is durable but before the league commit), and the pair (league, archive) is consistent;
  * the exporter command exits 75 and changes nothing when another exporter holds the lock;
  * a slow viewer whose release is pruned recovers by itself, with no mixed pages.
"""
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import feedlab  # noqa: E402
from dfl import archive as A, persist  # noqa: E402
from dflsite import feed, publish, snapshot  # noqa: E402

TABLES = os.path.join(ROOT, "data", "season1")
EXPORT = os.path.join(ROOT, "tools", "export_public_feed.py")

WRITER = r'''
import os, sys, time
sys.path.insert(0, %(src)r)
from dfl import persist
league, point, reached, go, events = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])
def hook(p):
    if p == point:
        open(reached, "w").write(p)
        deadline = time.time() + 120
        while not os.path.exists(go) and time.time() < deadline:
            time.sleep(0.02)
r = persist.Runner.open(league, heal=False)
r.crash_hook = hook
for _ in range(events):
    r.step()
open(reached + ".done", "w").write(str(r.seq))
r.close()
''' % {"src": os.path.join(ROOT, "src")}


def touch(path, text="x"):
    with open(path, "w") as f:
        f.write(text)


def jload(path):
    with open(path) as f:
        return json.load(f)


def wait_for(path, seconds=60):
    end = time.time() + seconds
    while not os.path.exists(path):
        if time.time() > end:
            raise AssertionError(f"timed out waiting for {path}")
        time.sleep(0.02)


# ------------------------------------------------------------------------------------------------------------------- publishing
class OnePublisher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _, out, _ = feedlab.stages()
        cls.a, cls.b, cls.c = out["w6"], out["wild_card"], out["bowl"]

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="dflpub")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def rel_dir(self):
        return os.path.join(self.root, "releases")

    def test_a_second_publisher_is_refused_at_once_and_does_not_touch_the_first_ones_temporary_directory(self):
        seen = {}

        def hold(point):                                    # the first publisher is part-way: its temporary directory exists
            if point == "lock_held":
                seen["entered"] = True
                inner = {}
                try:
                    publish.publish(self.b, self.root)       # a second publisher, same thread, while the lock is held
                except publish.PublishBusy as e:
                    inner["busy"] = str(e)
                seen["second"] = inner
        # the lock is taken by publish(); a second call (own lock attempt) must fail at once
        publish.publish(self.a, self.root, crash_hook=hold)
        self.assertIn("busy", seen["second"])
        self.assertEqual(os.listdir(self.rel_dir()), [self.a["manifest.json"]["release"]])
        self.assertEqual(jload(os.path.join(self.root, "latest.json"))["release"], self.a["manifest.json"]["release"])

    def test_processes_publish_one_after_another_and_the_loser_changes_nothing(self):
        """Process 1 blocks inside its publication (temporary directory written, pointer not moved). Process 2 is refused. Process 1's directory survives."""
        code = ("import sys, os, time, pickle\n"
                f"sys.path.insert(0, {os.path.join(ROOT, 'src')!r}); sys.path.insert(0, {os.path.join(ROOT, 'tests')!r})\n"
                "from dflsite import publish\nimport json\n"
                "files = pickle.load(open(sys.argv[1], 'rb')); root = sys.argv[2]\n"
                "def hook(p):\n"
                "    if p == 'release_written':\n"
                "        open(sys.argv[3], 'w').write('x')\n"
                "        end = time.time() + 120\n"
                "        while not os.path.exists(sys.argv[4]) and time.time() < end: time.sleep(0.02)\n"
                "publish.publish(files, root, crash_hook=hook)\n")
        import pickle
        fa, fb = os.path.join(self.root, "a.pkl"), os.path.join(self.root, "b.pkl")
        for obj, path in ((self.a, fa), (self.b, fb)):
            with open(path, "wb") as f:
                pickle.dump(obj, f)
        root = os.path.join(self.root, "feed")
        reached, go = os.path.join(self.root, "reached"), os.path.join(self.root, "go")
        p1 = subprocess.Popen([sys.executable, "-I", "-c", code, fa, root, reached, go])
        try:
            wait_for(reached)
            before = sorted(os.listdir(os.path.join(root, "releases")))
            lock_info = jload(os.path.join(root, publish.LOCK))
            self.assertEqual(lock_info["pid"], p1.pid)
            with self.assertRaises(publish.PublishBusy):
                publish.publish(self.b, root, stale_tmp=0)            # even with the age limit lifted, a live publisher's work is not touched
            self.assertEqual(sorted(os.listdir(os.path.join(root, "releases"))), before)
            self.assertFalse(os.path.exists(os.path.join(root, "latest.json")))
        finally:
            touch(go, "go")
            p1.wait(timeout=120)
        self.assertEqual(p1.returncode, 0)
        self.assertEqual(publish.read_release(root)["manifest.json"]["release"], self.a["manifest.json"]["release"])
        publish.publish(self.b, root)                                # now free: the second publisher succeeds
        self.assertEqual(publish.read_release(root)["manifest.json"]["release"], self.b["manifest.json"]["release"])

    def test_a_publisher_killed_mid_publication_leaves_no_stale_lock_and_its_debris_is_removed_only_when_old(self):
        code = ("import sys, os, time, pickle\n"
                f"sys.path.insert(0, {os.path.join(ROOT, 'src')!r})\n"
                "from dflsite import publish\n"
                "files = pickle.load(open(sys.argv[1], 'rb'))\n"
                "def hook(p):\n"
                "    if p == 'pointer_written':\n"
                "        open(sys.argv[3], 'w').write('x'); time.sleep(120)\n"
                "publish.publish(files, sys.argv[2], crash_hook=hook)\n")
        import pickle
        root = os.path.join(self.root, "feed")
        publish.publish(self.a, root)
        fb, reached = os.path.join(self.root, "b.pkl"), os.path.join(self.root, "reached")
        with open(fb, "wb") as f:
            pickle.dump(self.b, f)
        p = subprocess.Popen([sys.executable, "-I", "-c", code, fb, root, reached])
        wait_for(reached)
        os.kill(p.pid, signal.SIGKILL)
        p.wait()
        leftovers = [n for n in os.listdir(root) if n.startswith(".latest.json.tmp-")]
        self.assertTrue(leftovers)                                                    # the dead publisher's pointer temp file
        self.assertEqual(jload(os.path.join(root, "latest.json"))["release"], self.a["manifest.json"]["release"])   # old pointer intact
        os.makedirs(os.path.join(root, "releases", ".tmp-dead-1"))
        publish.publish(self.c, root)                                                 # no stale lock: this succeeds at once
        self.assertTrue(os.path.isdir(os.path.join(root, "releases", ".tmp-dead-1")), "debris younger than the limit must be left alone")
        publish.publish(self.c, root, stale_tmp=0)
        self.assertFalse(os.path.exists(os.path.join(root, "releases", ".tmp-dead-1")))
        self.assertFalse([n for n in os.listdir(root) if n.startswith(".latest.json.tmp-")])
        self.assertEqual(publish.read_release(root)["manifest.json"]["release"], self.c["manifest.json"]["release"])

    def test_a_failed_publication_removes_only_its_own_directory(self):
        publish.publish(self.a, self.root)
        other = os.path.join(self.rel_dir(), ".tmp-someone-else-1")
        os.makedirs(other)
        real = publish._write
        calls = []

        def flaky(path, raw):
            calls.append(path)
            if len(calls) == 3:
                raise OSError("disk full")
            return real(path, raw)
        publish._write = flaky
        try:
            with self.assertRaises(OSError):
                publish.publish(self.b, self.root)
        finally:
            publish._write = real
        self.assertTrue(os.path.isdir(other))
        self.assertEqual([n for n in os.listdir(self.rel_dir()) if n.startswith(".tmp-") and n != ".tmp-someone-else-1"], [])

    def test_prune_waits_out_the_grace_period_and_never_removes_the_live_release(self):
        for f in (self.a, self.b, self.c):
            publish.publish(f, self.root, keep=10)
        publish.publish(self.c, self.root, keep=1)                        # default grace: nothing is old enough
        self.assertEqual(len(os.listdir(self.rel_dir())), 3)
        old = time.time() - 7200
        for n in os.listdir(self.rel_dir()):
            os.utime(os.path.join(self.rel_dir(), n), (old, old))
        r = publish.publish(self.c, self.root, keep=1)
        self.assertEqual(sorted(r["pruned"]), sorted([self.a["manifest.json"]["release"], self.b["manifest.json"]["release"]]))
        self.assertEqual(os.listdir(self.rel_dir()), [self.c["manifest.json"]["release"]])

    def test_the_feed_never_moves_backwards_unless_told_to(self):
        publish.publish(self.c, self.root)
        with self.assertRaises(publish.PublishError) as cm:
            publish.publish(self.a, self.root)
        self.assertIn("backwards", str(cm.exception))
        self.assertEqual(publish.read_release(self.root)["manifest.json"]["release"], self.c["manifest.json"]["release"])
        publish.publish(self.a, self.root, allow_older=True)
        self.assertEqual(publish.read_release(self.root)["manifest.json"]["release"], self.a["manifest.json"]["release"])

    # ---- installing a shipped seed feed
    def test_a_seed_feed_is_installed_whole_verified_and_never_backwards(self):
        src, dest = os.path.join(self.root, "seed"), os.path.join(self.root, "dest")
        publish.publish(self.b, src)
        r = publish.install_release(src, dest)
        self.assertEqual(r, {"release": self.b["manifest.json"]["release"], "installed": True})
        self.assertEqual(publish.read_release(dest)["manifest.json"]["release"], self.b["manifest.json"]["release"])
        self.assertEqual(publish.install_release(src, dest)["installed"], False)                # repeating changes nothing
        older = os.path.join(self.root, "older")
        publish.publish(self.a, older)
        with self.assertRaises(publish.PublishError):
            publish.install_release(older, dest)
        self.assertEqual(publish.read_release(dest)["manifest.json"]["release"], self.b["manifest.json"]["release"])

    def test_a_damaged_seed_feed_is_refused_before_anything_is_written(self):
        src, dest = os.path.join(self.root, "seed"), os.path.join(self.root, "dest")
        publish.publish(self.b, src)
        rel = self.b["manifest.json"]["release"]
        target = os.path.join(src, "releases", rel, "standings.json")
        with open(target, "ab") as f:
            f.write(b" ")
        with self.assertRaises(publish.FeedError):
            publish.install_release(src, dest)
        self.assertFalse(os.path.exists(os.path.join(dest, "latest.json")))
        self.assertEqual([n for n in os.listdir(os.path.join(dest, "releases"))] if os.path.isdir(os.path.join(dest, "releases")) else [], [])

    def test_the_seed_command_line(self):
        src, dest = os.path.join(self.root, "seed"), os.path.join(self.root, "dest")
        publish.publish(self.b, src)
        tool = os.path.join(ROOT, "tools", "seed_feed.py")
        r = subprocess.run([sys.executable, "-I", tool, src, dest], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(json.loads(r.stdout)["installed"])
        with publish.PublisherLock(dest):
            r = subprocess.run([sys.executable, "-I", tool, src, dest], capture_output=True, text=True)
        self.assertEqual(r.returncode, 75)

    def test_waiting_publishers_take_turns(self):
        order, errors = [], []

        def go(f, name):
            try:
                publish.publish(f, self.root, wait=30, allow_older=True, crash_hook=lambda p: order.append((name, p)) if p in ("lock_held", "pointer_replaced") else None)
            except Exception as e:                               # pragma: no cover
                errors.append(e)
        ts = [threading.Thread(target=go, args=(f, n)) for f, n in ((self.a, "a"), (self.b, "b"), (self.c, "c"))]
        for t in ts:
            t.start()
        for t in ts:
            t.join(60)
        self.assertEqual(errors, [])
        # each publisher's lock_held is followed by its own pointer_replaced before anyone else's lock_held: never interleaved
        for i in range(0, len(order), 2):
            self.assertEqual(order[i][1], "lock_held")
            self.assertEqual(order[i + 1], (order[i][0], "pointer_replaced"))
        self.assertEqual(len(order), 6)
        publish.read_release(self.root)


# ------------------------------------------------------------------------------------------------------------------- snapshots under a live commit
class LiveCommit(unittest.TestCase):
    """A real league with its archive, a real runner process committing an event, and the exporter looking at it meanwhile."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="dflsnap")
        cls.league = os.path.join(cls.tmp, "league.db")
        r = persist.Runner.create(cls.league, TABLES, seed=1, snapshot_every=5, spatial=False)
        for _ in range(40):
            r.step()
            if r.archive.db.execute("SELECT COUNT(*) FROM games").fetchone()[0]:
                break
        cls.start_seq = r.seq
        r.close()
        cls.writer = os.path.join(cls.tmp, "writer.py")
        touch(cls.writer, WRITER)
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def hold_commit_at(self, point):
        """Start a runner process that blocks at `point` inside the commit of the next event; returns (process, go-file path)."""
        type(self).n += 1
        reached, go = os.path.join(self.tmp, f"reached{self.n}"), os.path.join(self.tmp, f"go{self.n}")
        p = subprocess.Popen([sys.executable, "-I", self.writer, self.league, point, reached, go, "1"])
        wait_for(reached, 120)
        return p, reached, go

    def head(self, path):
        db = persist.connect(path, readonly=True)
        try:
            return db.execute("SELECT MAX(seq) FROM journal").fetchone()[0]
        finally:
            db.close()

    def pair(self, name):
        """Snapshot league then archive the way the exporter does, into a fresh directory. Returns (league copy, archive copy, archive_check)."""
        d = os.path.join(self.tmp, name)
        os.makedirs(d)
        lg, ar = os.path.join(d, "league.db"), os.path.join(d, "league.db.archive")
        persist.backup(self.league, lg)                                    # verified online backup
        snapshot.snapshot_archive(os.path.join(os.path.dirname(self.league), "league.db.archive"), ar)
        return lg, ar, persist.archive_check(lg, ar)

    def finish(self, p, go):
        touch(go, "go")
        p.wait(timeout=180)
        self.assertEqual(p.returncode, 0)

    def commit_while_snapshotting(self, point):
        before = self.head(self.league)
        p, reached, go = self.hold_commit_at(point)
        try:
            lg, ar, chk = self.pair("during_" + point)
            # the writer is parked inside the commit: the snapshot is the last COMMITTED event, whole and verified, never the half-written one
            self.assertEqual(self.head(lg), before)
            self.assertEqual(persist.verify(lg), [])
            self.assertEqual((chk["absent"], chk["unreadable"], chk["foreign"], chk["missing"], chk["mismatched"], chk["base_ok"]),
                             (False, None, False, [], [], True))
            # the archive batch is durable before the league commit, so the archive may be one batch ahead, never behind
            self.assertEqual(chk["ahead"], [before + 1])
        finally:
            self.finish(p, go)
        self.assertEqual(self.head(self.league), before + 1)
        lg2, ar2, chk2 = self.pair("after_" + point)
        self.assertEqual(self.head(lg2), before + 1)
        self.assertEqual((chk2["missing"], chk2["mismatched"], chk2["ahead"]), ([], [], []))
        # the earlier snapshot is untouched by the commit that finished afterwards
        self.assertEqual(self.head(lg), before)

    def test_snapshot_pair_while_a_commit_is_inside_its_transaction(self):
        self.commit_while_snapshotting("journal_written")

    def test_snapshot_pair_after_the_archive_batch_is_durable_and_before_the_league_commit(self):
        self.commit_while_snapshotting("archive_written")

    def test_a_snapshot_refuses_what_is_not_the_expected_kind_of_file(self):
        d = tempfile.mkdtemp(dir=self.tmp)
        bogus = os.path.join(d, "x.db")
        sqlite3.connect(bogus).executescript("CREATE TABLE t (a); INSERT INTO t VALUES (1);")
        with self.assertRaises(snapshot.SnapshotError):
            snapshot.snapshot_archive(bogus, os.path.join(d, "copy"))
        self.assertEqual([n for n in os.listdir(d) if n != "x.db"], [])
        with self.assertRaises(snapshot.SnapshotError):
            snapshot.snapshot_sqlite(os.path.join(d, "nope.db"), os.path.join(d, "copy"))

    def test_the_exporter_command_works_during_a_commit_and_then_follows_it(self):
        feed_root = os.path.join(self.tmp, "feed_during")
        before = self.head(self.league)
        p, reached, go = self.hold_commit_at("journal_written")
        try:
            r1 = subprocess.run([sys.executable, "-I", EXPORT, self.league, "--out", feed_root, "--use-league-code"], capture_output=True, text=True, timeout=600)
            self.assertEqual(r1.returncode, 0, r1.stderr)
            res1 = json.loads(r1.stdout)
            self.assertEqual(res1["events"], before)                 # the committed state, not the event in flight
        finally:
            self.finish(p, go)
        r2 = subprocess.run([sys.executable, "-I", EXPORT, self.league, "--out", feed_root, "--use-league-code"], capture_output=True, text=True, timeout=600)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        res2 = json.loads(r2.stdout)
        self.assertEqual(res2["events"], before + 1)
        self.assertNotEqual(res1["release"], res2["release"])
        got = publish.read_release(feed_root)
        self.assertEqual(got["manifest.json"]["events_done"], got["season.json"]["events_done"])
        self.assertEqual(got["manifest.json"]["release"], res2["release"])
        self.assertIn(res1["release"], os.listdir(os.path.join(feed_root, "releases")))      # the earlier release is still there for slow readers

    def test_the_exporter_exits_75_and_changes_nothing_when_another_exporter_holds_the_lock(self):
        feed_root = os.path.join(self.tmp, "feed_busy")
        with publish.PublisherLock(feed_root):
            r = subprocess.run([sys.executable, "-I", EXPORT, self.league, "--out", feed_root, "--use-league-code"], capture_output=True, text=True, timeout=600)
        self.assertEqual(r.returncode, 75, r.stderr)
        self.assertTrue(json.loads(r.stderr.strip().splitlines()[-1])["busy"])
        self.assertFalse(os.path.exists(os.path.join(feed_root, "latest.json")))
        self.assertFalse(os.path.exists(os.path.join(feed_root, "releases")) and os.listdir(os.path.join(feed_root, "releases")))


# ------------------------------------------------------------------------------------------------------------------- score recovery from a real archive
class RealArchiveRecovery(unittest.TestCase):
    """Old seasons get their Coronation score back only from a real, checksummed, matching archived game."""

    @classmethod
    def setUpClass(cls):
        cls.u, _, _ = feedlab.stages()
        cls.tmp = tempfile.mkdtemp(prefix="dflrec")
        # a real archived game record (from a real league run) is re-labelled as the Coronation of the stage league's season 1
        src = persist.Runner.create(os.path.join(cls.tmp, "src.db"), TABLES, seed=1, snapshot_every=5, spatial=False)
        for _ in range(40):
            src.step()
            if src.archive.db.execute("SELECT COUNT(*) FROM games").fetchone()[0]:
                break
        gid = src.archive.games()[0][0]
        cls.real = src.archive.game(gid)
        src.close()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def old_rep(self):
        import copy
        rep = copy.copy(self.u.reports[0])
        rep["result"] = {k: v for k, v in rep["result"].items() if k not in ("coronation", "result_revision")}
        return rep

    def archive_with(self, name, mutate=None):
        rep = self.old_rep()
        r = rep["result"]
        away = next(c for c in r["champions"].values() if c != r["coronation_home"])
        g = dict(self.real)
        g.update(gid="S001/coronation", season=rep["season"], kind="coronation", home=r["coronation_home"], away=away, neutral=True,
                 score=[27, 20] if rep["winner"] == r["coronation_home"] else [20, 27], winner=rep["winner"])
        if mutate:
            mutate(g)
        path = os.path.join(self.tmp, name + ".archive")
        a = A.Archive.open_or_create(path, {"fingerprint": "t", "how": "test", "seq": 0, "league_id": "L"})
        a.put_batch(1, a.prepare([g]))
        return a, rep

    def test_a_matching_archived_game_gives_back_its_score(self):
        a, rep = self.archive_with("ok")
        self.assertEqual(feed.coronation_score(self.u, rep, a), ("27-20", "archive"))
        a.close()

    def test_a_game_whose_sides_or_winner_disagree_with_the_season_report_is_not_used(self):
        for name, mut in (("kind", lambda g: g.update(kind="regular")), ("winner", lambda g: g.update(winner="Nowhere")),
                          ("flip", lambda g: g.update(score=list(reversed(g["score"])))), ("home", lambda g: g.update(home="Nowhere"))):
            a, rep = self.archive_with("bad_" + name, mut)
            self.assertEqual(feed.coronation_score(self.u, rep, a), (None, "unavailable"), name)
            a.close()

    def test_the_whole_history_file_uses_the_archive_only_where_the_report_lacks_a_score(self):
        a, rep = self.archive_with("hist")
        saved = self.u.reports[0]
        self.u.reports[0] = rep
        try:
            hist = feed.history_file(self.u, 2001, a)["seasons"][0]
            plain = feed.history_file(self.u, 2001)["seasons"][0]
        finally:
            self.u.reports[0] = saved
            a.close()
        self.assertEqual((hist["score"], hist["score_source"]), ("27-20", "archive"))
        self.assertEqual((plain["score"], plain["score_source"]), (None, "unavailable"))
        self.assertEqual(feed.history_file(self.u, 2001)["seasons"][0]["score_source"], "recorded")


# ------------------------------------------------------------------------------------------------------------------- the viewer, in a real browser
try:
    from playwright.sync_api import sync_playwright
except Exception:                                                  # pragma: no cover
    sync_playwright = None


def tiny_png():
    import struct
    import zlib

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"\x00\x28\x44\x3a"                                       # one pixel, filter 0, RGB
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
class Viewer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import socketserver
        import preview_site
        cls.ps = preview_site
        _, out, _ = feedlab.stages()
        cls.a, cls.b = out["w6"], out["wild_card"]
        cls.tmp = tempfile.mkdtemp(prefix="dflview")
        cls.site = os.path.join(cls.tmp, "site")
        shutil.copytree(os.path.join(ROOT, "site"), cls.site)
        cls.feed = os.path.join(cls.tmp, "feed")
        preview_site.Handler.feed_root = cls.feed
        preview_site.Handler.site_root = cls.site
        socketserver.TCPServer.allow_reuse_address = True
        cls.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), preview_site.Handler)
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

    def setUp(self):
        shutil.rmtree(self.feed, ignore_errors=True)
        shutil.copy(os.path.join(ROOT, "site", "assets", "map", "map.json"), os.path.join(self.site, "assets", "map", "map.json"))
        for n in os.listdir(os.path.join(self.site, "assets", "map")):
            if n not in ("map.json", "README.txt"):
                os.remove(os.path.join(self.site, "assets", "map", n))

    def url(self, route=""):
        return f"http://127.0.0.1:{self.port}/#/{route}"

    def test_the_header_shows_the_simulation_date_and_the_last_published_update(self):
        publish.publish(self.b, self.feed)
        page = self.browser.new_page()
        page.goto(self.url())
        page.wait_for_selector("#status .simdate")
        text = page.inner_text("#status")
        page.close()
        self.assertIn("Simulation date", text)
        self.assertIn("Last published update", text)
        self.assertIn(str(self.b["manifest.json"]["year"]), text)

    def test_a_release_pruned_under_a_slow_viewer_is_recovered_without_mixing_releases(self):
        publish.publish(self.a, self.feed)
        ra, rb = self.a["manifest.json"]["release"], self.b["manifest.json"]["release"]
        page = self.browser.new_page()
        requests = []
        page.on("request", lambda r: requests.append(r.url))
        page.goto(self.url())
        page.wait_for_selector("#app h1")
        self.assertIn(ra, page.inner_text("#foot"))
        # the league moves on and the old release is pruned (grace lifted here; in service it is an hour) while this viewer is still on it
        r = publish.publish(self.b, self.feed, keep=1, grace=0)
        self.assertIn(ra, r["pruned"])
        self.assertFalse(os.path.exists(os.path.join(self.feed, "releases", ra)))
        requests.clear()
        page.evaluate("location.hash = '#/team/ALA'")                   # needs rosters/ALA.json, which this viewer has not fetched
        page.wait_for_function("document.querySelector('#foot') && document.querySelector('#foot').textContent.indexOf(%r) >= 0" % rb, timeout=15000)
        page.wait_for_selector("#app table, #app .err", timeout=15000)
        body = page.inner_text("#app")
        self.assertNotIn("could not be shown", body)
        self.assertGreaterEqual(len(body), 200)
        self.assertIn("Switched to the newest release", page.inner_text("#status"))
        after_recovery = [u for u in requests if "/feed/releases/" in u and "/manifest.json" not in u]
        self.assertTrue(after_recovery)
        self.assertTrue(all(f"/{rb}/" in u or f"/{ra}/" in u for u in after_recovery))
        self.assertTrue(any(f"/{rb}/rosters/ALA.json" in u for u in after_recovery))
        # further navigation reads only the new release
        requests.clear()
        page.evaluate("location.hash = '#/standings'")
        page.wait_for_selector("#app table")
        self.assertTrue(all(f"/{ra}/" not in u for u in requests if "/feed/releases/" in u))
        page.close()

    def test_a_file_really_missing_from_the_live_release_is_reported_not_retried_forever(self):
        publish.publish(self.b, self.feed)
        page = self.browser.new_page()
        count = []
        page.on("request", lambda r: count.append(r.url) if r.url.endswith("/rosters/ALA.json") else None)
        page.goto(self.url())
        page.wait_for_selector("#app h1")
        os.remove(os.path.join(self.feed, "releases", self.b["manifest.json"]["release"], "rosters", "ALA.json"))
        page.evaluate("location.hash = '#/team/ALA'")
        page.wait_for_selector("#app .err", timeout=15000)
        self.assertIn("could not be shown", page.inner_text("#app"))
        self.assertLessEqual(len(count), 3)
        page.close()

    def test_the_map_page_shows_the_supplied_map_and_nothing_when_there_is_none(self):
        publish.publish(self.b, self.feed)
        page = self.browser.new_page()
        page.goto(self.url("map"))
        page.wait_for_selector("#app .mapslot p")
        self.assertIn("has not been added", page.inner_text("#app"))
        self.assertEqual(page.locator("img.dflmap").count(), 0)
        d = os.path.join(self.site, "assets", "map")
        with open(os.path.join(d, "dfl-map.png"), "wb") as f:
            f.write(tiny_png())
        touch(os.path.join(d, "map.json"), json.dumps({"file": "dfl-map.png", "alt": "Test map", "caption": "A test caption"}))
        page.goto(self.url("map"))
        page.reload()
        page.wait_for_selector("img.dflmap", timeout=15000)
        self.assertTrue(page.evaluate("document.querySelector('img.dflmap').naturalWidth > 0"))
        self.assertEqual(page.get_attribute("img.dflmap", "alt"), "Test map")
        self.assertIn("A test caption", page.inner_text("#app"))
        touch(os.path.join(d, "map.json"), json.dumps({"file": "../../index.html"}))            # a path is not accepted as a file name
        page.reload()
        page.wait_for_selector("#app .mapslot p", timeout=15000)
        self.assertEqual(page.locator("img.dflmap").count(), 0)
        self.assertIn("has not been added", page.inner_text("#app"))
        page.close()
