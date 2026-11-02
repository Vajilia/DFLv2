"""tools/cap_state.py (fingerprint and diff) and tools/analyze_memory.py (snapshot memory report), on a small real league.

Proved: two fingerprints of an untouched league are identical; one more committed event changes the stable fields and `diff` says so (exit 1); a
fingerprint is read-only (the files are byte-identical afterwards); `--recompute` confirms the newest snapshot's state digest equals the journal's;
the memory report names the contract release-charge memo and the yearly view cache, and the pickled size without the memo is smaller."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _diag  # noqa: F401,E402
from dfl import persist  # noqa: E402

TABLES = os.path.join(ROOT, "data", "season1")
STATE = os.path.join(ROOT, "tools", "cap_state.py")
MEM = os.path.join(ROOT, "tools", "analyze_memory.py")
FEED = os.path.join(ROOT, "tests", "fixtures", "feed_v1")


def sha(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class Tools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="dflcap")
        cls.db = os.path.join(cls.tmp, "l.db")
        cls.r = persist.Runner.create(cls.db, TABLES, seed=1, snapshot_every=1, spatial=False)
        for _ in range(3):
            cls.r.step()

    @classmethod
    def tearDownClass(cls):
        cls.r.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def fp(self, name, *extra):
        out = os.path.join(self.tmp, name)
        p = subprocess.run([sys.executable, "-I", STATE, "fingerprint", "--league", self.db, "--feed", FEED, "--out", out, *extra], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return out

    def test_same_state_same_fingerprint_and_read_only(self):
        before = (sha(self.db), sha(self.db + ".archive"))
        a, b = self.fp("a.json", "--recompute"), self.fp("b.json")
        p = subprocess.run([sys.executable, "-I", STATE, "diff", a, b], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("IDENTICAL", p.stdout)
        self.assertEqual(before, (sha(self.db), sha(self.db + ".archive")))
        with open(a) as f:
            self.assertIs(json.load(f)["info"]["recompute"]["state_digest_equals_journal_head"], True)

    def test_the_recompute_flag_may_come_before_the_other_options(self):
        """Found on the server 2026-11-02: with --recompute first, --out was ignored (options were read in pairs)."""
        out = os.path.join(self.tmp, "c.json")
        p = subprocess.run([sys.executable, "-I", STATE, "fingerprint", "--recompute", "--league", self.db, "--feed", FEED, "--out", out], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue(os.path.exists(out), p.stdout)
        with open(out) as f:
            self.assertIn("recompute", json.load(f)["info"])

    def test_one_more_event_is_reported(self):
        a = self.fp("c.json")
        self.r.step()
        b = self.fp("d.json")
        p = subprocess.run([sys.executable, "-I", STATE, "diff", a, b], capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)
        self.assertIn("STABLE field differs: journal.head_seq", p.stdout)

    def test_memory_report(self):
        p = subprocess.run([sys.executable, "-I", MEM, "snap", self.db, "0"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        d = json.loads(p.stdout)
        m = d["contract_memo"]
        self.assertGreater(m["contracts"], 1000)
        # The snapshot was loaded in a fresh process: every contract's release-charge cache must be empty (the cache is derived data and is never
        # written to a snapshot), so there is nothing for a save to drop and the pickle is the same size with and without it.
        self.assertEqual((m["with_memo"], m["entries"], m["max_entries_per_contract"]), (0, 0, 0))
        self.assertEqual(m["pickled_mb_with_memo"], m["pickled_mb_without_memo"])
        self.assertIn("lru_caches", d)
        self.assertGreater(d["pickled_mb"], 1)


if __name__ == "__main__":
    unittest.main()
