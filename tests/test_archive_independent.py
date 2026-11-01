"""tools/verify_archive_independent.py: expected games derived from the schedule template, committed events and bracket progression.

One real season (47 events, the placeholder-free non-spatial engine; about a minute) is played and archived through the real writer. The
check must pass it with exactly 360 regular, 28 Tour, 10 playoff, 1 Bowl and 1 Coronation game, and must catch, on copies, by game id:
a game missing from archive AND season summary together (the shared omission the summary cannot reveal), a game missing from the archive
only, a game written under a wrong participant, a result changed in the archive but not in the summary, an extra game, and a
journal that carries more or fewer records than the template says the event plays."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _diag  # noqa: F401,E402
from dfl import archive as A, persist  # noqa: E402

TABLES = os.path.join(ROOT, "data", "season1")
TOOL = os.path.join(ROOT, "tools", "verify_archive_independent.py")


def run(league, *extra):
    p = subprocess.run([sys.executable, "-I", TOOL, league + ".archive", "--league", league, "--allow-engine", "play", *extra], capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def drop_triggers(db):
    for t in A.HISTORY_TABLES:
        for op in ("update", "delete"):
            db.execute(f"DROP TRIGGER IF EXISTS {t}_no_{op}")


class Independent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="dflindep")
        cls.base = os.path.join(cls.tmp, "base.db")
        r = persist.Runner.create(cls.base, TABLES, seed=1, snapshot_every=50, spatial=False)
        for _ in range(47):
            r.step()
        r.close()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def copy(self):
        dst = os.path.join(self.tmp, f"{self._testMethodName}.db")
        shutil.copy(self.base, dst)
        shutil.copy(self.base + ".archive", dst + ".archive")
        return dst

    def test_a_real_season_passes_with_the_exact_counts(self):
        rc, out = run(self.base, "--deep")
        self.assertEqual(rc, 0, out)
        self.assertIn("expected {'regular': 360, 'tour': 28, 'playoff': 10, 'bowl': 1, 'coronation': 1}", out)
        self.assertIn("stored {'regular': 360, 'tour': 28, 'playoff': 10, 'bowl': 1, 'coronation': 1}", out)

    def test_missing_in_archive_only(self):
        d = self.copy()
        db = sqlite3.connect(d + ".archive")
        drop_triggers(db)
        db.execute("DELETE FROM games WHERE game_id = 'S001/reg:w03:g02'")
        db.commit()
        db.close()
        rc, out = run(d)
        self.assertEqual(rc, 1)
        self.assertIn("S001/reg:w03:g02: expected (week_3) and not in the archive", out)

    def test_shared_omission_in_archive_and_summary_is_still_found(self):
        d = self.copy()
        db = sqlite3.connect(d + ".archive")
        drop_triggers(db)
        db.execute("DELETE FROM games WHERE game_id = 'S001/coronation'")
        row = db.execute("SELECT payload FROM records WHERE rid = 'S001/summary'").fetchone()
        rec = json.loads(zlib.decompress(row[0]))
        rec["result"].pop("coronation")                       # the summary forgets the Coronation too
        body = A.canonical_bytes(rec)
        db.execute("UPDATE records SET payload = ?, sha256 = ?, size = ? WHERE rid = 'S001/summary'", (zlib.compress(body), A.sha(body), len(body)))
        db.commit()
        db.close()
        rc, out = run(d)
        self.assertEqual(rc, 1)
        self.assertIn("S001/coronation: expected (coronation) and not in the archive", out)

    def test_wrong_participant(self):
        d = self.copy()
        db = sqlite3.connect(d + ".archive")
        drop_triggers(db)
        db.execute("UPDATE games SET home = 'Nowhere' WHERE game_id = 'S001/tour:r2:g01'")
        db.commit()
        db.close()
        rc, out = run(d)
        self.assertEqual(rc, 1)
        self.assertIn("S001/tour:r2:g01: stored away", out)
        self.assertIn("home Nowhere", out)

    def test_result_changed_in_archive_only_disagrees_with_summary(self):
        d = self.copy()
        db = sqlite3.connect(d + ".archive")
        drop_triggers(db)
        z, = db.execute("SELECT payload FROM games WHERE game_id = 'S001/reg:w01:g01'").fetchone()
        g = json.loads(zlib.decompress(z))
        g["score"] = [g["score"][0] + 7, g["score"][1]]
        body = A.canonical_bytes(g)
        db.execute("UPDATE games SET payload = ?, sha256 = ?, size = ? WHERE game_id = 'S001/reg:w01:g01'", (zlib.compress(body), A.sha(body), len(body)))
        db.commit()
        db.close()
        rc, out = run(d)
        self.assertEqual(rc, 1)
        self.assertIn("summary and archive disagree on", out)

    def test_extra_game(self):
        d = self.copy()
        db = sqlite3.connect(d + ".archive")
        drop_triggers(db)
        db.execute("INSERT INTO games SELECT 'S001/reg:w99:g01', season, seq, kind, home, away, engine, rule_version, n_traces, sha256, size, payload FROM games WHERE game_id = 'S001/reg:w01:g01'")
        db.commit()
        db.close()
        rc, out = run(d)
        self.assertEqual(rc, 1)
        self.assertIn("S001/reg:w99:g01: in the archive and not expected for S001", out)

    def test_journal_record_count_must_match_the_template(self):
        d = self.copy()
        db = sqlite3.connect(d)
        db.execute("UPDATE journal SET n_records = n_records + 1 WHERE key = 'week_5'")
        db.commit()
        db.close()
        rc, out = run(d)
        self.assertEqual(rc, 1)
        self.assertIn("(week_5) carries", out)

    def test_traces_are_required_of_the_spatial_engine(self):
        p = subprocess.run([sys.executable, "-I", TOOL, self.base + ".archive", "--league", self.base], capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)                      # this season was played without the 22-player engine
        self.assertIn("engine 'play', expected 'spatial'", p.stdout)


if __name__ == "__main__":
    unittest.main()
