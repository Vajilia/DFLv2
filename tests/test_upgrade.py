"""C11: league files and the code that wrote them (src/dfl/persist.py, src/dfl/neutral.py).

Two different questions are tested apart. SAVE SCHEMA: can this code read the file's tables at all? CODE IDENTITY: is this the exact source and
data that wrote the league, so that continuing it reproduces what the original code would have done? Then the upgrade path: a league written
by other code is carried to the current code through a neutral state document, the original file untouched, and the result is proved by
value: state, identifiers, exact (Fraction) money, every random generator's state, and the way both leagues go on afterwards.
"""
import fractions
import gzip
import hashlib
import json
import os
import random
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl import archive, neutral, persist  # noqa: E402
from dfl.year import Universe  # noqa: E402

TABLES = os.path.join(ROOT, "data", "season1")
K = 12                    # events before the move: past the first game weeks' set-up, with money, contracts and draw state in play
MORE = 8                  # events fired afterwards by both leagues


def read(p):
    with open(p, "rb") as f:
        return f.read()


def write(p, b):
    with open(p, "wb") as f:
        f.write(b)


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def walk(obj):
    """Every object reachable from `obj` once (dicts, sequences, sets, __dict__ and __slots__)."""
    seen, stack, out = set(), [obj], []
    while stack:
        o = stack.pop()
        if isinstance(o, fractions.Fraction):          # a value, not a container: counted at every place it occurs
            out.append(o)
            continue
        if id(o) in seen:
            continue
        seen.add(id(o))
        out.append(o)
        if isinstance(o, dict):
            stack.extend(o.keys())
            stack.extend(o.values())
        elif isinstance(o, (list, tuple, set, frozenset)):
            stack.extend(o)
        elif hasattr(o, "__dict__") or hasattr(type(o), "__slots__"):
            if hasattr(o, "__dict__"):
                stack.extend(o.__dict__.values())
            for k in type(o).__mro__:
                for sl in getattr(k, "__slots__", ()) or ():
                    if hasattr(o, sl):
                        stack.append(getattr(o, sl))
    return out


def fractions_in(u):
    return sorted((f.numerator, f.denominator) for f in walk(u) if isinstance(f, fractions.Fraction))


def rngs_in(u):
    return sorted(hashlib.sha256(repr(r.getstate()).encode()).hexdigest() for r in walk(u) if isinstance(r, random.Random))


def journal_rows(path):
    db = sqlite3.connect(path)
    try:
        return db.execute("SELECT seq, key, digest, chain FROM journal ORDER BY seq").fetchall()
    finally:
        db.close()


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="dflupgrade")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.tmp, f"{self._testMethodName}_{name}.db")

    def league(self, name="old", events=K):
        r = persist.Runner.create(self.path(name), TABLES, seed=1, snapshot_every=5, engine=False)
        r.run_events(events)
        return r


class Compatibility(Base):
    def test_schema_and_code_are_reported_apart(self):
        r = self.league()
        r.close()
        c = persist.compatibility(self.path("old"))
        self.assertEqual((c["schema"], c["code"], c["usable"]), ("ok", "exact", True))
        db = sqlite3.connect(self.path("old"))
        db.execute("UPDATE meta SET value = ? WHERE key = 'fingerprint'", (json.dumps("1" * 64),))
        db.commit()
        db.close()
        c = persist.compatibility(self.path("old"))
        self.assertEqual((c["schema"], c["code"], c["usable"]), ("ok", "differs", False))       # readable tables, but not the same code

    def test_a_file_from_other_code_is_refused_with_the_reason_and_the_way_forward(self):
        r = self.league()
        r.close()
        src = os.path.join(self.tmp, "othercode")
        modified_tree(src)
        run_in_tree(src, f"r = persist.Runner.create({self.path('x')!r}, {src + '/data/season1'!r}, seed=1, engine=False); r.run_events(3); r.close()")
        with self.assertRaises(persist.CodeMismatch) as cm:
            persist.Runner.open(self.path("x"))
        msg = str(cm.exception)
        self.assertIn("src/dfl/engine/params.py", msg)             # names the file that differs
        self.assertIn("upgrade", msg)                              # and what to do
        self.assertEqual(persist.verify(self.path("x")), [])       # verify and status still work under any code
        self.assertEqual(persist.status(self.path("x"))["events"], 3)

    def test_unsupported_schemas_are_rejected_clearly_never_guessed_at(self):
        for name, fmt, words in (("v1", 1, "schema 1"), ("v99", 99, "newer")):
            with self.subTest(fmt):
                r = self.league(name, events=2)
                r.close()
                db = sqlite3.connect(self.path(name))
                db.execute("UPDATE meta SET value = ? WHERE key = 'format'", (json.dumps(fmt),))
                db.commit()
                db.close()
                self.assertEqual(persist.compatibility(self.path(name))["schema"], "unsupported")
                for call in (lambda: persist.Runner.open(self.path(name)),
                             lambda: persist.upgrade(self.path(name), self.path(name + "_new")),
                             lambda: persist.export_state(self.path(name), self.path(name) + ".gz")):
                    with self.assertRaises(persist.UnsupportedFormat) as cm:
                        call()
                    self.assertIn(words, str(cm.exception))
                self.assertFalse(os.path.exists(self.path(name + "_new")))


class RoundTrip(Base):
    """export -> import under the same code: nothing may change, and both leagues must go on identically."""

    def test_export_import_preserves_state_identifiers_money_and_random_streams(self):
        r = self.league()
        a_digest, a_quick = persist.deep_digest(r.u), persist.quick_digest(r.u)
        fr, rg = fractions_in(r.u), rngs_in(r.u)
        ids = (sorted(r.u.L.players), sorted(r.u.L.clubs), sorted(r.u.world.cards))
        self.assertGreater(len(fr), 0, "the test needs real exact money in the state")
        self.assertGreater(len(set(d for _, d in fr)), 1, "and some that is not whole")
        self.assertGreater(len(rg), 1)
        r.close()
        doc = self.path("old") + ".state.gz"
        persist.export_state(self.path("old"), doc)
        n = persist.Runner.import_state(doc, self.path("new"), source_file_sha256=sha256_file(self.path("old")))
        self.assertEqual(persist.deep_digest(n.u), a_digest)                                # every value, by value
        self.assertEqual(persist.quick_digest(n.u), a_quick)
        self.assertEqual(fractions_in(n.u), fr)                                             # exact money: same numerators and denominators
        self.assertTrue(all(type(f) is fractions.Fraction for f in walk(n.u) if isinstance(f, fractions.Fraction)))
        self.assertEqual(rngs_in(n.u), rg)                                                  # every generator's full state
        self.assertEqual((sorted(n.u.L.players), sorted(n.u.L.clubs), sorted(n.u.world.cards)), ids)      # identifiers
        self.assertEqual(n.u.L.digest(), Universe_digest_from_file(self.path("old")))       # the ledger's own digest
        n.close()

    def test_the_document_is_deterministic_and_loads_to_the_same_document(self):
        r = self.league(events=6)
        r.close()
        a, b = self.path("old") + ".a.gz", self.path("old") + ".b.gz"
        persist.export_state(self.path("old"), a)
        persist.export_state(self.path("old"), b)
        self.assertEqual(read(a), read(b))                      # same bytes every time
        doc = neutral.loads_doc(read(a))
        self.assertEqual(doc["manifest"]["seq"], 6)
        self.assertEqual(doc["manifest"]["fingerprint"], persist.fingerprint())

    def test_both_leagues_go_on_exactly_alike_with_one_continuous_hash_chain(self):
        r = self.league()
        base_chain, base_seq = r.chain, r.seq
        r.close()
        doc = self.path("old") + ".state.gz"
        persist.export_state(self.path("old"), doc)
        n = persist.Runner.import_state(doc, self.path("new"))
        self.assertEqual((n.seq, n.chain), (base_seq, base_chain))                        # the numbering and the chain carry on
        o = persist.Runner.open(self.path("old"))
        o.run_events(MORE)
        n.run_events(MORE)
        old_rows, new_rows = journal_rows(self.path("old")), journal_rows(self.path("new"))
        self.assertEqual(new_rows, old_rows[K:])                                          # same events, digests and chain links
        self.assertEqual([x[0] for x in new_rows], list(range(K + 1, K + MORE + 1)))
        self.assertEqual(persist.deep_digest(o.u), persist.deep_digest(n.u))
        self.assertEqual(persist.verify(self.path("new"), deep=True), [])
        o.close()
        n.close()

    def test_a_state_that_does_not_match_its_digest_is_refused_and_nothing_is_written(self):
        r = self.league(events=6)
        r.close()
        doc = self.path("old") + ".state.gz"
        persist.export_state(self.path("old"), doc)
        raw = json.loads(gzip.decompress(read(doc)))
        raw["manifest"]["deep_digest"] = "0" * 64                                         # as if the import had changed something
        bad = self.path("old") + ".bad.gz"
        write(bad, gzip.compress(json.dumps(raw).encode()))
        with self.assertRaises(persist.StateMismatch):
            persist.Runner.import_state(bad, self.path("new"))
        self.assertFalse(os.path.exists(self.path("new")))
        n = persist.Runner.import_state(bad, self.path("accepted"), accept_state_change=True)    # allowed only when asked, and recorded
        self.assertTrue(n.meta["lineage"][-1]["state_changed"])
        n.close()

    def test_other_ways_an_import_is_refused(self):
        r = self.league(events=4)
        r.close()
        doc = self.path("old") + ".state.gz"
        persist.export_state(self.path("old"), doc)
        persist.Runner.import_state(doc, self.path("new")).close()
        with self.assertRaises(persist.PersistError):                                     # never over an existing file
            persist.Runner.import_state(doc, self.path("new"))
        raw = json.loads(gzip.decompress(read(doc)))
        raw["version"] = 99
        future = self.path("old") + ".future.gz"
        write(future, gzip.compress(json.dumps(raw).encode()))
        with self.assertRaises(neutral.NeutralError):                                     # a document format this code does not read
            persist.Runner.import_state(future, self.path("other"))
        raw["version"] = 1
        raw["manifest"]["fingerprint"] = "2" * 64
        foreign = self.path("old") + ".foreign.gz"
        write(foreign, gzip.compress(json.dumps(raw).encode()))
        with self.assertRaises(persist.CodeMismatch):                                     # state from other code: a new code epoch must be asked for
            persist.Runner.import_state(foreign, self.path("other2"))
        self.assertFalse(os.path.exists(self.path("other2")))
        raw["manifest"]["fingerprint"] = persist.fingerprint()
        raw["manifest"]["archive"] = {"file": "nowhere.archive"}
        lost = self.path("old") + ".lost.gz"
        write(lost, gzip.compress(json.dumps(raw).encode()))
        with self.assertRaises(persist.PersistError):                                     # history is never recreated empty
            persist.Runner.import_state(lost, self.path("other3"))


def Universe_digest_from_file(path):
    r = persist.Runner.open(path, heal=False)
    try:
        return r.u.L.digest()
    finally:
        r.close()


def modified_tree(dest):
    """A copy of src/dfl and data with one behaviour-neutral edit: other code, same league rules."""
    os.makedirs(dest)
    shutil.copytree(os.path.join(ROOT, "src", "dfl"), os.path.join(dest, "src", "dfl"), ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(os.path.join(ROOT, "data"), os.path.join(dest, "data"))
    with open(os.path.join(dest, "src", "dfl", "engine", "params.py"), "a", encoding="utf-8") as f:
        f.write("\n# an edit made by the upgrade test: it changes the code's identity and nothing else\n")


def run_in_tree(tree, code):
    full = f"import sys; sys.path.insert(0, {tree + '/src'!r}); from dfl import persist\n{code}"
    p = subprocess.run([sys.executable, "-I", "-c", full], capture_output=True, text=True)
    if p.returncode:
        raise AssertionError(p.stderr[-1500:])
    return p.stdout


class Upgrade(Base):
    """A league written by other code, carried to this code; the original file and the code it was written with stay intact."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.old_tree = os.path.join(cls.tmp, "oldcode")
        modified_tree(cls.old_tree)
        cls.old = os.path.join(cls.tmp, "league_old.db")
        cls.old_fp = persist.fingerprint(cls.old_tree)
        run_in_tree(cls.old_tree, f"r = persist.Runner.create({cls.old!r}, {cls.old_tree + '/data/season1'!r}, seed=1, snapshot_every=5, "
                                  f"engine=False); r.run_events({K}); r.close()")
        cls.ref = Universe(TABLES, seed=1, engine=False)
        cls.ref_digests = []
        for _ in range(K + MORE):
            cls.ref.step_event()
            cls.ref_digests.append(persist.quick_digest(cls.ref))

    def test_the_old_file_is_refused_here_until_an_upgrade_is_asked_for(self):
        self.assertNotEqual(self.old_fp, persist.fingerprint())
        c = persist.compatibility(self.old)
        self.assertEqual((c["schema"], c["code"]), ("ok", "differs"))
        self.assertEqual(c["changed_files"]["modified"], ["src/dfl/engine/params.py"])
        new = os.path.join(self.tmp, "refused_new.db")
        with self.assertRaises(persist.CodeMismatch):
            persist.upgrade(self.old, new)                         # running the file's own code is opt-in
        self.assertFalse(os.path.exists(new))

    def test_upgrade_continues_the_league_under_the_current_code_and_keeps_the_original(self):
        before = sha256_file(self.old)
        new = os.path.join(self.tmp, "upgraded.db")
        rep = persist.upgrade(self.old, new, run_originating_code=True)
        self.assertEqual(sha256_file(self.old), before)            # the original file is byte for byte what it was
        self.assertEqual(rep["original_sha256"], before)
        self.assertEqual((rep["from_fingerprint"], rep["to_fingerprint"]), (self.old_fp, persist.fingerprint()))
        n = persist.Runner.open(new)
        self.assertEqual(n.seq, K)
        lin = n.meta["lineage"][-1]                                # where it came from, recorded in the new file
        self.assertEqual((lin["from_fingerprint"], lin["from_seq"], lin["source_file_sha256"], lin["code_changed"], lin["state_changed"]),
                         (self.old_fp, K, before, True, False))
        self.assertEqual(lin["from_chain"], journal_rows(self.old)[-1][3])
        n.run_events(MORE)                                         # and it goes on exactly as an unbroken league under this code would
        self.assertEqual([d for _, _, d, _ in journal_rows(new)], self.ref_digests[K:])
        self.assertEqual(persist.deep_digest(n.u), persist.deep_digest(self.ref))
        self.assertEqual(persist.verify(new, deep=True), [])
        n.close()
        self.assertEqual(sha256_file(self.old), before)

    def test_the_originating_version_is_preserved_and_still_runs_the_old_file(self):
        out = os.path.join(self.tmp, "extracted")
        fp = persist.extract_code(self.old, out)
        self.assertEqual(fp, self.old_fp)
        self.assertEqual(persist.fingerprint(out), self.old_fp)    # byte-identical source and data, recorded inside the file
        self.assertEqual(persist.verify(self.old), [])
        res = run_in_tree(out, f"r = persist.Runner.open({self.old!r}, heal=False); print(r.seq, persist.quick_digest(r.u)); r.close()")
        self.assertEqual(res.split()[0], str(K))                   # the old file is still a live league under its own code
        self.assertEqual(res.split()[1], self.ref_digests[K - 1])

    def test_the_archive_carries_across_and_records_each_code_epoch(self):
        d = os.path.join(self.tmp, "withplay")
        os.makedirs(d)
        tree = os.path.join(self.tmp, "oldcode2")
        modified_tree(tree)
        old = os.path.join(d, "old.db")
        run_in_tree(tree, f"r = persist.Runner.create({old!r}, {tree + '/data/season1'!r}, seed=1, snapshot_every=5, spatial=False); "
                          f"r.run_events(20); r.close()")           # past week 1: real games are archived
        before_archive = archive.digest(old + ".archive")
        self.assertGreater(archive.stats(old + ".archive")["games"], 0)
        new = os.path.join(d, "new.db")
        persist.upgrade(old, new, run_originating_code=True)
        self.assertEqual(archive.digest(old + ".archive"), before_archive)       # history untouched by the move
        n = persist.Runner.open(new)
        n.run_events(3)
        n.close()
        self.assertGreater(archive.stats(old + ".archive")["games"], 0)
        self.assertEqual(archive.verify(old + ".archive"), [])
        a = archive.Archive.open(old + ".archive")
        epochs = json.loads(a.db.execute("SELECT value FROM meta WHERE key = 'epochs'").fetchone()[0])
        a.close()
        self.assertEqual([e["fingerprint"] for e in epochs], [persist.fingerprint(tree), persist.fingerprint()])


if __name__ == "__main__":
    unittest.main()
