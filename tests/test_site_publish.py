"""Atomic publication (src/dflsite/publish.py): a reader sees the old release or the new one, whole, whatever happens in between.

Covers: crash points around the pointer swap; a failure while a release is being written; a reader running while releases are published; a
damaged, truncated or foreign feed; a non-public feed refused; idempotence and pruning; the exporter command against a real league file (the
league file is not changed, a failed export leaves the old release live).
"""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import feedlab  # noqa: E402
from dfl import persist  # noqa: E402
from dflsite import feed, publish  # noqa: E402


class Crash(Exception):
    pass


def boom(point):
    def hook(p):
        if p == point:
            raise Crash(p)
    return hook


class Atomic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _, out, _ = feedlab.stages()
        cls.a, cls.b, cls.c = out["w6"], out["wild_card"], out["bowl"]

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="dflfeed-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def live(self):
        return publish.read_release(self.root)["manifest.json"]["release"]

    def test_publish_then_read_back_every_file_verified(self):
        r = publish.publish(self.a, self.root)
        self.assertTrue(r["published"])
        data = publish.read_release(self.root)
        self.assertEqual(set(data), set(self.a))
        self.assertEqual(data["manifest.json"]["release"], self.a["manifest.json"]["release"])
        self.assertEqual(feed.canonical(data["standings.json"]), feed.canonical(self.a["standings.json"]))
        with open(os.path.join(self.root, "latest.json")) as f:
            ptr = json.load(f)
        self.assertEqual((ptr["format"], ptr["format_version"], ptr["release"]), (feed.FORMAT, 1, self.a["manifest.json"]["release"]))

    def test_publishing_the_same_release_again_changes_nothing(self):
        publish.publish(self.a, self.root)
        r = publish.publish(self.a, self.root)
        self.assertFalse(r["published"])
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "releases"))), [self.a["manifest.json"]["release"]])

    def test_republishing_an_existing_release_with_a_new_timestamp_keeps_the_pointer_true_to_the_disk(self):
        """Found on the Droplet 2026-10-31: the release directory existed (same content), the new manifest differed only in generated_at, and the pointer
        carried the sha of a manifest that was never written."""
        import copy
        import hashlib
        publish.publish(self.a, self.root)
        again = copy.deepcopy(self.a)
        again["manifest.json"]["generated_at"] = "2099-01-01T00:00:00+00:00"
        r = publish.publish(again, self.root)
        self.assertFalse(r["published"])
        with open(os.path.join(self.root, "latest.json")) as f:
            ptr = json.load(f)
        with open(os.path.join(self.root, "releases", ptr["release"], "manifest.json"), "rb") as f:
            raw = f.read()
        self.assertEqual(ptr["manifest_sha256"], hashlib.sha256(raw).hexdigest(), "the pointer names the manifest bytes that are on disk")
        self.assertEqual(ptr["generated_at"], json.loads(raw)["generated_at"])
        self.assertNotEqual(ptr["generated_at"], "2099-01-01T00:00:00+00:00")
        publish.read_release(self.root)                                   # and the whole release still verifies through the pointer

    def test_a_release_name_that_exists_with_other_content_is_refused(self):
        import copy
        publish.publish(self.a, self.root)
        other = copy.deepcopy(self.a)
        other["manifest.json"]["content_hash"] = "0" * 64
        with self.assertRaises(publish.PublishError):
            publish.publish(other, self.root)
        self.assertEqual(self.live(), self.a["manifest.json"]["release"])

    def test_a_crash_before_the_pointer_swap_leaves_the_old_release_live_and_whole(self):
        publish.publish(self.a, self.root)
        old = self.a["manifest.json"]["release"]
        for point in ("release_written", "pointer_written"):
            with self.assertRaises(Crash):
                publish.publish(self.b, self.root, crash_hook=boom(point))
            self.assertEqual(self.live(), old, point)
            publish.read_release(self.root, verify=True)
        publish.publish(self.b, self.root)                                   # the next publication completes normally and cleans up
        self.assertEqual(self.live(), self.b["manifest.json"]["release"])
        self.assertEqual([n for n in os.listdir(self.root) if n.startswith(".") and n != publish.LOCK], [])

    def test_a_crash_after_the_pointer_swap_leaves_the_new_release_live_and_whole(self):
        publish.publish(self.a, self.root)
        with self.assertRaises(Crash):
            publish.publish(self.b, self.root, crash_hook=boom("pointer_replaced"))
        self.assertEqual(self.live(), self.b["manifest.json"]["release"])

    def test_a_failure_while_writing_a_release_leaves_no_partial_release(self):
        publish.publish(self.a, self.root)
        real = publish._write
        count = {"n": 0}

        def flaky(path, raw):
            count["n"] += 1
            if count["n"] == 20:
                raise OSError("disk full")
            return real(path, raw)
        publish._write = flaky
        try:
            with self.assertRaises(OSError):
                publish.publish(self.b, self.root)
        finally:
            publish._write = real
        names = os.listdir(os.path.join(self.root, "releases"))
        self.assertEqual(names, [self.a["manifest.json"]["release"]])          # no half-written release, not even a temporary directory
        self.assertEqual(self.live(), self.a["manifest.json"]["release"])

    def test_an_interrupted_temporary_directory_is_cleaned_by_the_next_publication(self):
        publish.publish(self.a, self.root)
        os.makedirs(os.path.join(self.root, "releases", ".tmp-leftover-xyz"))
        open(os.path.join(self.root, "releases", ".tmp-leftover-xyz", "x"), "w").write("partial")
        publish.publish(self.b, self.root, stale_tmp=0)                # (the age limit is lifted here: the leftover was just made)
        self.assertFalse([n for n in os.listdir(os.path.join(self.root, "releases")) if n.startswith(".")])

    def test_a_reader_running_during_publications_always_gets_one_whole_release(self):
        publish.publish(self.a, self.root)
        stop, bad, seen = threading.Event(), [], set()

        def reader():
            while not stop.is_set():
                try:
                    d = publish.read_release(self.root)
                    rel = d["manifest.json"]["release"]
                    seen.add(rel)
                    if d["season.json"]["events_done"] != d["manifest.json"]["events_done"]:
                        bad.append(("mixed", rel))
                except publish.FeedError as e:
                    bad.append(str(e))
        t = threading.Thread(target=reader)
        t.start()
        try:
            for f in (self.b, self.c, self.a, self.b, self.c):
                publish.publish(f, self.root, keep=3, grace=0, allow_older=True)
        finally:
            stop.set()
            t.join()
        self.assertEqual(bad, [])
        self.assertGreaterEqual(len(seen), 2)

    def test_prune_keeps_the_newest_and_never_the_live_one(self):
        for f in (self.a, self.b, self.c):
            publish.publish(f, self.root, keep=10)
        self.assertEqual(len(os.listdir(os.path.join(self.root, "releases"))), 3)
        publish.publish(self.a, self.root, keep=1, grace=0, allow_older=True)           # the live one is the oldest by name; it must survive
        names = os.listdir(os.path.join(self.root, "releases"))
        self.assertIn(self.a["manifest.json"]["release"], names)
        self.assertLessEqual(len(names), 2)

    def test_a_feed_that_is_not_public_is_refused_and_nothing_is_written(self):
        bad = copy.deepcopy(self.a)
        bad["rosters/ALA.json"]["players"][0]["ovr"] = 80
        with self.assertRaises(publish.PublishError):
            publish.publish(bad, self.root)
        self.assertFalse(os.path.exists(os.path.join(self.root, "latest.json")))

    def test_a_manifest_that_does_not_match_the_bytes_is_refused(self):
        bad = copy.deepcopy(self.a)
        bad["standings.json"]["year"] = 1999                # changed after the manifest was made
        with self.assertRaises(publish.PublishError):
            publish.publish(bad, self.root)
        self.assertEqual(os.listdir(os.path.join(self.root, "releases")), [])

    def test_damage_and_foreign_feeds_are_detected(self):
        publish.publish(self.a, self.root)
        rel = os.path.join(self.root, "releases", self.a["manifest.json"]["release"])
        path = os.path.join(rel, "standings.json")
        good = open(path, "rb").read()
        open(path, "wb").write(good[:-5])                                         # truncated
        with self.assertRaises(publish.FeedError):
            publish.read_release(self.root)
        open(path, "wb").write(good.replace(b"1", b"2", 1))                       # altered, same length
        with self.assertRaises(publish.FeedError):
            publish.read_release(self.root)
        open(path, "wb").write(good)
        publish.read_release(self.root)
        os.remove(path)
        with self.assertRaises(publish.FeedError):
            publish.read_release(self.root)
        open(path, "wb").write(good)
        ptr = os.path.join(self.root, "latest.json")
        raw = open(ptr).read()
        open(ptr, "w").write(raw[:20])                                             # truncated pointer
        with self.assertRaises(publish.FeedError):
            publish.read_release(self.root)
        p = json.loads(raw)
        p["format_version"] = 2
        open(ptr, "w").write(json.dumps(p))
        with self.assertRaises(publish.FeedError):
            publish.read_release(self.root)
        p["format_version"], p["format"] = 1, "something-else"
        open(ptr, "w").write(json.dumps(p))
        with self.assertRaises(publish.FeedError):
            publish.read_release(self.root)


class Exporter(unittest.TestCase):
    """The command-line exporter against a real durable league file."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="dflexport-")
        cls.db = os.path.join(cls.dir, "league.db")
        r = persist.Runner.create(cls.db, feedlab.TABLES, seed=feedlab.SEED, archive=False, engine=False)
        for _ in range(14):
            r.step()
        cls.events = r.seq
        r.close()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def run_export(self, out, *extra):
        return subprocess.run([sys.executable, "-I", os.path.join(ROOT, "tools", "export_public_feed.py"), self.db, "--out", out, *extra], capture_output=True, text=True)

    def test_export_publishes_and_leaves_the_league_file_untouched(self):
        import hashlib
        sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
        before = (sha(self.db), persist.status(self.db)["chain"], sorted(os.listdir(self.dir)))
        out = os.path.join(self.dir, "feed1")
        p = self.run_export(out)
        self.assertEqual(p.returncode, 0, p.stderr)
        res = json.loads(p.stdout)
        self.assertEqual(res["events"], self.events)
        self.assertTrue(res["published"])
        after = (sha(self.db), persist.status(self.db)["chain"], sorted(os.listdir(self.dir)))
        self.assertEqual(before[0], after[0])
        self.assertEqual(before[1], after[1])
        self.assertEqual([n for n in after[2] if n != "feed1"], before[2])                  # no stray files beside the league
        data = publish.read_release(out)
        self.assertEqual(data["manifest.json"]["league"]["id"], persist.status(self.db)["league_id"])
        self.assertEqual(data["manifest.json"]["events_done"], len(feedlab_events(data)))
        again = self.run_export(out)
        self.assertEqual(json.loads(again.stdout)["published"], False)                       # nothing new committed: same release

    def test_a_failed_export_leaves_the_previous_release_live(self):
        out = os.path.join(self.dir, "feed2")
        self.assertEqual(self.run_export(out).returncode, 0)
        live = publish.read_release(out)["manifest.json"]["release"]
        missing = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "tools", "export_public_feed.py"), os.path.join(self.dir, "nope.db"), "--out", out],
                                 capture_output=True, text=True)
        self.assertEqual(missing.returncode, 1)
        self.assertIn("error", json.loads(missing.stderr))
        self.assertEqual(publish.read_release(out)["manifest.json"]["release"], live)


def feedlab_events(data):
    s = data["season.json"]
    return range(s["events_done"])


if __name__ == "__main__":
    unittest.main()
