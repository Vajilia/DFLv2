"""Development saves (save schema 3) and how this code treats them.

Schema 3 journals hold the old partial state digest and the pickles of code that took its parity settings from module constants. This code
(schema 4) neither opens nor continues such a file, and says so plainly; it can carry the league forward with `upgrade`, which runs the
file's own recorded code to export it, imports the export here, and checks the rebuilt state by value. The fixture is the exact source and
data of the commit that wrote schema 3 (030b51a), so these tests use a genuine schema-3 file, not a simulation of one.

Also here: the import check stays strict. Attributes that an adapter had to supply are left out of the comparison, nothing else is.
"""
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from fractions import Fraction as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl import neutral, persist  # noqa: E402
from dfl.year import Universe  # noqa: E402

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "schema3_code_030b51a.tar.gz")
FIXTURE_SHA256 = "177ac0aea85a0b01f6bb45d843ca586cb82e4c9d34d7b5a0f983498258bf6d88"
TABLES = os.path.join(ROOT, "data", "season1")
K, MORE = 12, 6


def sha256_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def run_in_tree(tree, code):
    full = f"import sys; sys.path.insert(0, {tree + '/src'!r}); from dfl import persist\n{code}"
    p = subprocess.run([sys.executable, "-I", "-c", full], capture_output=True, text=True)
    if p.returncode:
        raise AssertionError(p.stderr[-1500:])
    return p.stdout


class Schema3(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="dflformat3")
        cls.tree = os.path.join(cls.tmp, "schema3code")
        os.makedirs(cls.tree)
        with tarfile.open(FIXTURE) as t:
            t.extractall(cls.tree, filter="data")
        cls.old = os.path.join(cls.tmp, "league_v3.db")
        run_in_tree(cls.tree, f"r = persist.Runner.create({cls.old!r}, {cls.tree + '/data/season1'!r}, seed=1, snapshot_every=5, engine=False, "
                              f"archive=False, profile='none'); r.run_events({K}); r.close()")
        cls.ref = Universe(TABLES, seed=1, engine=False, profile="none")
        for _ in range(K):
            cls.ref.step_event()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_fixture_is_the_code_that_wrote_schema_3(self):
        self.assertEqual(sha256_file(FIXTURE), FIXTURE_SHA256)
        c = persist.compatibility(self.old)
        self.assertEqual(c["format"], 3)
        self.assertEqual(c["file_fingerprint"], persist.fingerprint(self.tree))

    def test_it_is_refused_plainly_and_nothing_is_written(self):
        c = persist.compatibility(self.old)
        self.assertEqual((c["schema"], c["code"], c["usable"]), ("upgradable", "differs", False))
        with self.assertRaises(persist.UnsupportedFormat) as cm:
            persist.Runner.open(self.old)
        msg = str(cm.exception)
        for must in ("development save schema 3", "algorithm 1", "algorithm 2", "all six random generators", "upgrade", "--run-originating-code", "untouched"):
            self.assertIn(must, msg)
        new = os.path.join(self.tmp, "refused.db")
        with self.assertRaises(persist.UnsupportedFormat):
            persist.upgrade(self.old, new)                              # running the file's own code is opt-in, as for any other code change
        self.assertFalse(os.path.exists(new))
        self.assertEqual(persist.status(self.old)["events"], K)         # reading it still works
        self.assertEqual(persist.verify(self.old), [])

    def test_upgrade_carries_it_forward_and_it_continues_exactly(self):
        before = sha256_file(self.old)
        new = os.path.join(self.tmp, "upgraded.db")
        rep = persist.upgrade(self.old, new, run_originating_code=True)        # no accept_state_change: the check was by value and passed
        self.assertEqual(sha256_file(self.old), before)
        self.assertTrue(rep["code_changed"])
        n = persist.Runner.open(new)
        self.assertEqual((n.meta["format"], n.seq), (4, K))
        lin = n.meta["lineage"][-1]
        self.assertEqual((lin["from_format"], lin["from_seq"], lin["code_changed"], lin["state_changed"]), (3, K, True, False))
        self.assertEqual(lin["adapted_attributes"], ["cap", "returner_block"])  # what the older state could not have carried, named
        self.assertEqual((n.u.world.L.cap, n.u.returner_block), (F(10 ** 7), False))   # from its profile, "none"
        self.assertEqual(persist.deep_digest(n.u), persist.deep_digest(self.ref))     # every value, six generators included
        ref = Universe(TABLES, seed=1, engine=False, profile="none")
        for _ in range(K):
            ref.step_event()
        for _ in range(MORE):
            ref.step_event()
        n.run_events(MORE)
        self.assertEqual(persist.journal_digest(n.u), persist.journal_digest(ref))
        self.assertEqual(persist.verify(new, deep=True), [])
        n.close()
        self.assertEqual(sha256_file(self.old), before)


class ImportStaysStrict(unittest.TestCase):
    """Allowing for what an adapter supplied must not weaken the comparison of everything else."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="dflstrict")
        cls.path = os.path.join(cls.tmp, "league.db")
        r = persist.Runner.create(cls.path, TABLES, seed=1, snapshot_every=5, engine=False, archive=False, profile="rigged")
        r.run_events(14)
        raw = neutral.dumps_doc(r.u, neutral.runner_manifest(r))
        cls.doc = json.loads(gzip.decompress(raw).decode())
        r.close()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @staticmethod
    def strip(node):
        if isinstance(node, dict):
            if "$o" in node and "s" in node and node["$o"].rsplit(".", 1)[-1] in ("Ledger", "Universe", "SeasonRun"):
                node["s"] = [kv for kv in node["s"] if kv[0] not in ("cap", "returner_block")]
            for v in node.values():
                ImportStaysStrict.strip(v)
        elif isinstance(node, list):
            for v in node:
                ImportStaysStrict.strip(v)

    @staticmethod
    def players(node, found):
        if isinstance(node, dict):
            if node.get("$o", "").endswith("ledger.Player"):
                found.append(node)
            for v in node.values():
                ImportStaysStrict.players(v, found)
        elif isinstance(node, list):
            for v in node:
                ImportStaysStrict.players(v, found)
        return found

    def write(self, name, doc):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as f:
            f.write(gzip.compress(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode(), 6, mtime=0))
        return path

    def test_a_state_missing_only_the_settings_imports_and_names_them(self):
        doc = json.loads(json.dumps(self.doc))
        self.strip(doc["state"])
        r = persist.Runner.import_state(self.write("plain.doc", doc), os.path.join(self.tmp, "ok.db"))
        self.assertEqual(r.meta["lineage"][-1]["adapted_attributes"], ["cap", "returner_block"])
        self.assertFalse(r.meta["lineage"][-1]["state_changed"])
        self.assertEqual((r.u.world.L.cap, r.u.returner_block), (F(10 ** 7), False))
        r.close()

    def test_any_other_difference_is_still_refused(self):
        doc = json.loads(json.dumps(self.doc))
        self.strip(doc["state"])
        victim = self.players(doc["state"], [])[0]
        for pair in victim["s"]:
            if pair[0] == "ovr":
                pair[1] += 1
        with self.assertRaises(persist.StateMismatch):
            persist.Runner.import_state(self.write("tampered.doc", doc), os.path.join(self.tmp, "bad.db"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "bad.db")))

    def test_a_generator_state_changed_in_transit_is_refused(self):
        doc = json.loads(json.dumps(self.doc))
        self.strip(doc["state"])
        gens = []

        def find(node):
            if isinstance(node, dict):
                if "$r" in node:
                    gens.append(node["$r"])
                for v in node.values():
                    find(v)
            elif isinstance(node, list):
                for v in node:
                    find(v)
        find(doc["state"])
        self.assertEqual(len(gens), 6)
        gens[3]["$t"][1]["$t"][5] += 1                                              # one word of one generator's 624-word state
        with self.assertRaises(persist.StateMismatch):
            persist.Runner.import_state(self.write("rng.doc", doc), os.path.join(self.tmp, "bad2.db"))

    def test_documents_with_older_digests_are_refused_by_name(self):
        doc = json.loads(json.dumps(self.doc))
        doc["version"] = 1
        with self.assertRaises(neutral.NeutralError) as cm:
            persist.Runner.import_state(self.write("v1.doc", doc), os.path.join(self.tmp, "v1.db"))
        self.assertIn("version 1", str(cm.exception))
        doc = json.loads(json.dumps(self.doc))
        doc["manifest"]["digest_algorithm"] = 1
        with self.assertRaises(neutral.NeutralError) as cm:
            persist.Runner.import_state(self.write("alg1.doc", doc), os.path.join(self.tmp, "alg1.db"))
        self.assertIn("algorithm 1", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
