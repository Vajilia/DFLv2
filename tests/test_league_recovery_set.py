"""tools/league_recovery_set.py: the recovery set of an UPGRADED league, and a restore test that uses nothing but that set.

A league moved to new code by `league_store.py upgrade` continues the old file's event numbers and hash chain, but the new file starts from one snapshot
and holds only the journal rows after it. These tests build a real (small) league, upgrade it, make the set, DELETE every path the league ever used
(both league files, both archives, the code tree), and then restore from the set alone."""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, TOOLS)
import league_recovery_set as RS  # noqa: E402
from test_league_backup import advance, new_league, py  # noqa: E402


def copy_tree(dest):
    for d in RS.TREE_DIRS:
        shutil.copytree(os.path.join(ROOT, d), os.path.join(dest, d), ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def snapshots(path):
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [(s, c) for s, c in db.execute("SELECT seq, checkpoint FROM snapshots ORDER BY seq")]
    finally:
        db.close()


def journal_range(path):
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return db.execute("SELECT MIN(seq), MAX(seq), COUNT(*) FROM journal").fetchone()
    finally:
        db.close()


class RecoverySet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp(prefix="dflrs")
        cls.home = os.path.join(cls.d, "server")                       # stands for the league's original server paths
        os.makedirs(os.path.join(cls.home, "tree"))
        copy_tree(os.path.join(cls.home, "tree"))
        cls.old = new_league(cls.home, "old.db", 5, 14, placeholder=False)       # real engine: events from 13 record games
        cls.new = os.path.join(cls.home, "new.db")
        cls.new_archive = cls.new + ".archive"
        shutil.copyfile(cls.old + ".archive", cls.new_archive)           # the new archive is the old one, extended
        r = py(os.path.join(TOOLS, "league_store.py"), "upgrade", cls.old, cls.new, "--archive", cls.new_archive)
        assert r.returncode == 0, r.stdout + r.stderr
        advance(cls.new, 20)
        cls.tree = os.path.join(cls.home, "tree")
        cls.set = os.path.join(cls.d, "set")
        RS.create(cls.set, cls.new, cls.old, code_tree=cls.tree)
        cls.old_first_snapshot = snapshots(cls.old)[0][0]
        cls.old_head = journal_range(cls.old)[1]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def test_the_active_league_alone_cannot_replay_from_the_beginning_and_the_original_can(self):
        first, last, rows = journal_range(self.new)
        self.assertEqual((first, rows), (self.old_head + 1, last - self.old_head))          # the new file's journal starts after the old head
        self.assertEqual(snapshots(self.new)[0][0], self.old_head)                           # and its oldest restore point is the old head
        self.assertEqual(journal_range(self.old)[:3:2], (1, self.old_head))                  # the old file holds events 1..head
        self.assertLess(self.old_first_snapshot, self.old_head)                              # and restore points before it

    def test_the_manifest_names_every_part_and_says_what_each_preserves(self):
        m = RS.read_json(os.path.join(self.set, RS.MANIFEST))
        self.assertEqual([c["role"] for c in m["components"]], ["active-manifest", "active-league", "active-archive", "original-league", "code-tree"])
        self.assertEqual(m["original"]["head"]["seq"], self.old_head)
        self.assertEqual(m["link"]["continues_event"], self.old_head)
        self.assertEqual(m["link"]["active_lineage_source_file_sha256"], RS.sha256_file(self.old))
        self.assertEqual(m["code"]["tree_fingerprint"], m["active"]["fingerprint"])
        self.assertEqual(sorted(m["preserves"]), ["game_history", "state_history"])
        self.assertEqual(RS.verify(self.set)["problems"], [])

    def test_the_new_archive_holds_everything_the_original_journal_committed(self):
        old_counts, new_counts = RS._archive_counts(self.old + ".archive"), RS._archive_counts(os.path.join(self.set, "active", "archive.db"))
        self.assertGreater(old_counts["games"], 0, "the test league must have recorded games")
        for k in ("batches", "games", "traces"):
            self.assertGreaterEqual(new_counts[k], old_counts[k], k)

    def test_restore_from_the_set_alone_with_the_original_paths_gone(self):
        # a private copy of the set; then every path the league used is made unavailable (the server folder is renamed away)
        s = os.path.join(self.d, "set-copy")
        shutil.copytree(self.set, s)
        gone = self.home + ".gone"
        os.rename(self.home, gone)
        try:
            work = os.path.join(self.d, "work")
            res = RS.restore_test(s, work, replay_from=self.old_first_snapshot, require_sources_absent=True)
            failed = [x for x in res["steps"] if not x["ok"]]
            self.assertEqual(failed, [], json.dumps(failed, indent=1, default=str)[:1500])
            self.assertTrue(res["ok"])
            names = [x["step"] for x in res["steps"]]
            self.assertTrue(any("restore point" in n for n in names) and any("SHA-256" in n for n in names) and any("game history is preserved" in n for n in names))
            # everything the test produced is under the work folder, and the restored pair is a working league (it can take one more event)
            r = py(os.path.join(work, "code-active", "tools", "league_runner.py"), os.path.join(work, "restored-active", "league.db"), "--until-events", str(RS.read_json(os.path.join(s, RS.MANIFEST))["active"]["head"]["seq"] + 1))
            self.assertEqual(r.returncode, 0, r.stderr[-600:])
        finally:
            os.rename(gone, self.home)

    def test_the_restore_test_refuses_to_claim_independence_while_the_original_paths_exist(self):
        res = RS.restore_test(self.set, os.path.join(self.d, "work-present"), require_sources_absent=True)
        self.assertFalse(res["ok"])
        self.assertIn("not available", res["steps"][-1]["step"])

    def test_a_set_without_the_original_file_does_not_verify(self):
        s = os.path.join(self.d, "set-no-original")
        shutil.copytree(self.set, s)
        os.remove(os.path.join(s, "original-141", "league.db"))
        self.assertTrue(any("original-league" in p and "missing" in p for p in RS.verify(s)["problems"]))
        res = RS.restore_test(s, os.path.join(self.d, "work-no-original"))
        self.assertFalse(res["ok"])

    def test_a_damaged_original_or_code_tree_is_caught_by_its_hash(self):
        for rel in ("original-141/league.db", "code/engine-tree.tar.gz", "active/archive.db"):
            s = os.path.join(self.d, "set-bad-" + os.path.basename(rel))
            shutil.copytree(self.set, s)
            with open(os.path.join(s, rel), "r+b") as f:
                f.seek(200)
                b = f.read(1)
                f.seek(200)
                f.write(bytes([b[0] ^ 1]))
            probs = RS.verify(s)["problems"]
            self.assertTrue(any("does not match the manifest" in p or "does not match its recorded" in p for p in probs), (rel, probs))

    def test_a_manifest_repaired_over_a_swapped_original_still_fails_the_link_check(self):
        other = new_league(self.d, "other.db", 77, 14, placeholder=False)                    # a different league
        s = os.path.join(self.d, "set-swapped")
        shutil.copytree(self.set, s)
        shutil.copyfile(other, os.path.join(s, "original-141", "league.db"))
        m = RS.read_json(os.path.join(s, RS.MANIFEST))
        for c in m["components"]:
            if c["role"] == "original-league":
                p = os.path.join(s, c["file"])
                c["size"], c["sha256"] = os.path.getsize(p), RS.sha256_file(p)
        RS.write_json(os.path.join(s, RS.MANIFEST), m)
        res = RS.restore_test(s, os.path.join(self.d, "work-swapped"))
        self.assertFalse(res["ok"])
        self.assertTrue(any("SHA-256" in x["step"] and not x["ok"] for x in res["steps"]), [x["step"] for x in res["steps"] if not x["ok"]])

    def test_create_refuses_the_wrong_original_a_non_upgrade_and_an_existing_folder(self):
        other = new_league(self.d, "other2.db", 78, 14, placeholder=False)
        with self.assertRaises(SystemExit) as e:
            RS.create(os.path.join(self.d, "set-wrong"), self.new, other, code_tree=self.tree)
        self.assertIn("different league", str(e.exception))
        self.assertFalse(os.path.exists(os.path.join(self.d, "set-wrong")), "a refused set leaves nothing behind")
        with self.assertRaises(SystemExit) as e:
            RS.create(os.path.join(self.d, "set-plain"), other, self.old, code_tree=self.tree)
        self.assertIn("no lineage", str(e.exception))
        with self.assertRaises(SystemExit) as e:
            RS.create(self.set, self.new, self.old, code_tree=self.tree)
        self.assertIn("not empty", str(e.exception))

    def test_create_refuses_a_code_tree_that_does_not_run_the_active_league(self):
        t = os.path.join(self.d, "tree-changed")
        copy_tree(t)
        with open(os.path.join(t, "src", "dfl", "persist.py"), "a") as f:
            f.write("\n# changed\n")
        with self.assertRaises(SystemExit) as e:
            RS.create(os.path.join(self.d, "set-tree"), self.new, self.old, code_tree=t)
        self.assertIn("not the code that runs the active league", str(e.exception))


if __name__ == "__main__":
    unittest.main()
