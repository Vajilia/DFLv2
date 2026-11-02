"""Moving an existing league to the code with the bounded contract cache (a changed code fingerprint) goes through the explicit upgrade path, on a COPY.

The league file records the fingerprint of the code that wrote it and refuses to open under different code. The cache change alters src/dfl, so a league
written before it (whose snapshots carry the full, unbounded cache) is not opened by the new code; it is carried over with `persist.upgrade`, which never
writes to the original. Proved here with a league written by the code as it was before the change (tests/fixtures/contracts_before_memo.py):

  * the new code refuses the old file and says why (nothing is changed by the refusal);
  * the old file really holds caches, and the upgraded copy opens with every cache empty;
  * the original league file and archive are byte-identical afterwards; the archive history in the copy is untouched by the move;
  * the upgraded copy continues the same sequence numbers and hash chain, records where it came from, and passes deep verification;
  * for the events that follow, the upgraded league and the league left running under the OLD code agree on every journal row: event key, state digest,
    hash chain, archive record count and archive records digest. History is preserved and the future is identical, not merely compatible.
"""
import gc
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _diag  # noqa: F401,E402
from dfl import archive, persist  # noqa: E402

BEFORE = os.path.join(ROOT, "tests", "fixtures", "contracts_before_memo.py")
K = 20           # events written by the old code (past the first game weeks; a snapshot is due at 20)
M = 10           # events fired afterwards by both the upgraded league and the old-code league


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def old_tree(dest):
    """src/dfl and data of this tree, with contracts.py exactly as it was before the cache change: the code that wrote the existing league."""
    os.makedirs(dest)
    shutil.copytree(os.path.join(ROOT, "src", "dfl"), os.path.join(dest, "src", "dfl"), ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(os.path.join(ROOT, "data"), os.path.join(dest, "data"))
    with open(BEFORE, encoding="utf-8") as f:
        lines = f.read().split("\n")
    with open(os.path.join(dest, "src", "dfl", "ledger", "contracts.py"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines[2:]))                       # drop the two fixture comment lines: the file is then byte-identical to the old module


def run_in_tree(tree, code):
    full = f"import sys; sys.path.insert(0, {tree + '/src'!r}); from dfl import persist\n{code}"
    p = subprocess.run([sys.executable, "-I", "-c", full], capture_output=True, text=True)
    if p.returncode:
        raise AssertionError(p.stderr[-2000:])
    return p.stdout


COUNT_MEMOS = "import gc; print(sum(1 for o in gc.get_objects() if type(o).__name__ == 'Contract' and o.__dict__.get('_memo')))"


def rows(path, after=0):
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return db.execute("SELECT seq, key, digest, chain, n_records, records_digest FROM journal WHERE seq > ? ORDER BY seq", (after,)).fetchall()
    finally:
        db.close()


def memo_contracts(u):
    seen, stack, n, total = set(), [u], 0, 0
    while stack:
        o = stack.pop()
        if id(o) in seen or isinstance(o, (str, bytes, int, float, bool, type(None))):
            continue
        seen.add(id(o))
        if type(o).__name__ == "Contract":
            total += 1
            n += 1 if o.__dict__.get("_memo") else 0
        elif isinstance(o, dict):
            stack.extend(o.keys()); stack.extend(o.values())
        elif isinstance(o, (list, tuple, set, frozenset)):
            stack.extend(o)
        elif hasattr(o, "__dict__"):
            stack.extend(vars(o).values())
    return n, total


class UpgradeACopy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="dflmemoup")
        cls.tree = os.path.join(cls.tmp, "oldcode")
        old_tree(cls.tree)
        cls.orig = os.path.join(cls.tmp, "trial")            # stands for the existing trial league: league.db and its archive
        os.makedirs(cls.orig)
        cls.league = os.path.join(cls.orig, "league.db")
        run_in_tree(cls.tree, f"r = persist.Runner.create({cls.league!r}, {cls.tree + '/data/season1'!r}, seed=1, snapshot_every=5, spatial=False); "
                              f"r.run_events({K}); r.close()")
        cls.old_fp = persist.compatibility(cls.league)["file_fingerprint"]
        cls.original_hashes = {n: sha256_file(os.path.join(cls.orig, n)) for n in sorted(os.listdir(cls.orig))}
        cls.in_old_file = int(run_in_tree(cls.tree, f"r = persist.Runner.open({cls.league!r}, heal=False)\n{COUNT_MEMOS}").split()[-1])
        # the copy that is upgraded, and a second copy that is left to run on under the old code as the reference
        cls.work = os.path.join(cls.tmp, "copy_for_upgrade")
        cls.ref = os.path.join(cls.tmp, "copy_for_reference")
        for d in (cls.work, cls.ref):
            shutil.copytree(cls.orig, d)
        cls.upgraded = os.path.join(cls.work, "league_v2.db")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def originals_intact(self):
        now = {n: sha256_file(os.path.join(self.orig, n)) for n in sorted(os.listdir(self.orig))}
        self.assertEqual(now, self.original_hashes)

    def test_1_the_new_code_refuses_the_old_file_and_says_why_and_changes_nothing(self):
        self.assertNotEqual(self.old_fp, persist.fingerprint())
        c = persist.compatibility(self.league)
        self.assertEqual((c["schema"], c["code"]), ("ok", "differs"))
        self.assertIn("src/dfl/ledger/contracts.py", c["changed_files"]["modified"])
        with self.assertRaises(persist.CodeMismatch) as cm:
            persist.Runner.open(self.league)
        self.assertIn("upgrade", str(cm.exception).lower())
        with self.assertRaises(persist.CodeMismatch):
            persist.upgrade(self.league, os.path.join(self.orig, "x.db"))        # running the file's own recorded code is opt-in
        self.originals_intact()

    def test_2_the_old_file_really_holds_caches(self):
        self.assertGreater(self.in_old_file, 1000, "the snapshot written by the old code carries release-charge caches (otherwise the upgrade proves nothing)")

    def test_3_the_upgraded_copy_keeps_the_history_the_originals_and_goes_on_identically(self):
        archive_before = archive.digest(self.work + "/league.db.archive")
        rep = persist.upgrade(os.path.join(self.work, "league.db"), self.upgraded, run_originating_code=True)
        self.originals_intact()
        self.assertEqual(rep["original_sha256"], self.original_hashes["league.db"])
        self.assertEqual((rep["from_fingerprint"], rep["to_fingerprint"], rep["events"], rep["code_changed"]), (self.old_fp, persist.fingerprint(), K, True))
        self.assertEqual(rep["chain"], rows(self.league)[-1][3])                       # the same hash chain, continued
        self.assertEqual(archive.digest(self.work + "/league.db.archive"), archive_before)      # the move rewrote no history

        n = persist.Runner.open(self.upgraded)
        try:
            self.assertEqual(n.seq, K)
            lin = n.meta["lineage"][-1]
            self.assertEqual((lin["from_fingerprint"], lin["from_seq"], lin["code_changed"], lin["state_changed"]), (self.old_fp, K, True, False))
            self.assertEqual(lin["source_file_sha256"], self.original_hashes["league.db"])
            self.assertEqual(persist.journal_digest(n.u), rows(self.league)[-1][2])    # the upgraded state is the old state, by value
            with_memo, contracts = memo_contracts(n.u)
            self.assertGreater(contracts, 1000)
            self.assertEqual(with_memo, 0, "after the upgrade every contract's cache is empty")
            n.run_events(M)
        finally:
            n.close()

        run_in_tree(self.tree, f"r = persist.Runner.open({os.path.join(self.ref, 'league.db')!r}, heal=False); r.run_events({M}); r.close()")
        mine, theirs = rows(self.upgraded, K), rows(os.path.join(self.ref, "league.db"), K)
        self.assertEqual(len(mine), M)
        self.assertEqual(mine, theirs, "the upgraded league and the league left on the old code agree on every journal row after the move")
        self.assertEqual(persist.verify(self.upgraded, deep=True), [])
        self.assertEqual(archive.verify(self.work + "/league.db.archive"), [])
        a = archive.Archive.open(self.work + "/league.db.archive")
        try:
            epochs = json.loads(a.db.execute("SELECT value FROM meta WHERE key = 'epochs'").fetchone()[0])
        finally:
            a.close()
        self.assertEqual([e["fingerprint"] for e in epochs], [self.old_fp, persist.fingerprint()])     # both code epochs are on record
        self.originals_intact()


if __name__ == "__main__":
    unittest.main()
