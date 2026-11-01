"""tools/verify_archive_records.py: the record-level archive check (counts and game ids, not file size).

A synthetic one-season archive is built through the real writer (src/dfl/archive.py): ten playoff games, the Ambassador Bowl and the Coronation,
each with traced snaps, plus a season summary that names them. The check must pass it; and it must report, by game id, a missing postseason game,
a game written without traces, a game whose trace rows were lost, a wrong score, an extra game the summary does
not name, and a journal that disagrees with the archive."""
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
from dfl import archive as A  # noqa: E402

TOOL = os.path.join(ROOT, "tools", "verify_archive_records.py")
STEPS = 4


def blob():
    z = zlib.compress(bytes(STEPS * 22 * 2 * 2))
    b = zlib.compress(bytes(STEPS * 3 * 2))
    return {"steps": STEPS, "pos": z, "ball": b, "dt": 0.05, "roles": [], "ids": [], "events": [], "intents": [], "los": 0, "formation": "x", "play": "x", "coverage": "x"}


def game(season, gid, kind, away, home, score, traced=2, plain=0):
    table = [{"i": i, "call": "run", "trace": blob()} for i in range(traced)] + [{"i": traced + j, "call": "kick"} for j in range(plain)]
    return {"type": "game", "gid": f"S{season:03d}/{gid}", "season": season, "kind": kind, "home": home, "away": away, "neutral": False, "date": None,
            "kickoff": None, "engine": "spatial", "rule_version": "t", "score": score, "periods": [], "tie": False, "winner": home, "stats": {},
            "players": {}, "injuries": [], "pen_types": {}, "table": table}


CLUBS = {"W": [f"W{i}" for i in range(1, 8)], "E": [f"E{i}" for i in range(1, 8)]}
# play order: per conference, wild card 3v6 and 4v5 (home first in the pair), divisional, conference
LOG, GAMES = [], []


def build_games(season=1):
    games, log = [], []
    for conf in ("E", "W"):
        c = CLUBS[conf]
        plan = [("wild_card", [(3, 6), (4, 5)]), ("divisional", [(1, 4), (2, 3)]), ("conference", [(1, 2)])]
        for rd, pairs in plan:
            for lo, hi in pairs:
                away, home = c[hi - 1], c[lo - 1]
                log.append([conf, away, home, 10, 20])
                games.append((rd, game(season, f"po:{conf}:{rd}:s{hi}v{lo}", "playoff", away, home, [10, 20])))
    return games, log


def summary(season, log):
    return {"type": "season", "rid": f"S{season:03d}/summary", "season": season,
            "result": {"outcome": {"seeds": {"E": CLUBS["E"][:6], "W": CLUBS["W"][:6]}}, "playoff_log": log,
                       "bowl": {"away": "West All-Stars", "home": "East All-Stars", "away_score": 7, "home_score": 14},
                       "coronation": {"away": "E1", "home": "W1", "away_score": 21, "home_score": 24}, "coronation_winner": "W1"}}


def run(*args):
    p = subprocess.run([sys.executable, "-I", "-S", TOOL, *args], capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


class Records(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="dflrecords")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def archive(self, drop=(), plain=(), extra=(), wrong_score=None):
        """-> archive path. `drop` ids are not written, `plain` ids are written without traces, `extra` are additional game records."""
        path = os.path.join(self.tmp, f"{self._testMethodName}.archive")
        a = A.Archive.open_or_create(path, {"fingerprint": "t", "how": "test", "seq": 0, "league_id": "L"})
        games, log = build_games()
        recs, seq = [], 60
        for rd, g in games:
            if g["gid"] in drop:
                continue
            if g["gid"] in plain:
                g = dict(g, table=[{"i": 0, "call": "run"}])
            if g["gid"] == wrong_score:
                g = dict(g, score=[1, 2])
            recs.append(g)
        a.put_batch(seq, recs)
        bowl = game(1, "bowl", "bowl", "West All-Stars", "East All-Stars", [7, 14])
        cor = game(1, "coronation", "coronation", "E1", "W1", [21, 24])
        a.put_batch(seq + 1, [] if "S001/bowl" in drop else [bowl])
        a.put_batch(seq + 2, [] if "S001/coronation" in drop else [cor])
        for k, e in enumerate(extra):
            a.put_batch(seq + 3 + k, [e])
        a.put_batch(seq + 3 + len(extra), [summary(1, log)])
        a.close()
        return path

    def test_complete_postseason_passes(self):
        rc, out = run(self.archive())
        self.assertEqual(rc, 0, out)
        self.assertIn("expected 12, stored 12", out)

    def test_missing_game_is_named(self):
        rc, out = run(self.archive(drop=("S001/po:E:divisional:s3v2",)))
        self.assertEqual(rc, 1)
        self.assertIn("S001/po:E:divisional:s3v2: required by the S001 summary and not in the archive", out)

    def test_missing_bowl_and_coronation_are_named(self):
        rc, out = run(self.archive(drop=("S001/bowl", "S001/coronation")))
        self.assertEqual(rc, 1)
        self.assertIn("S001/bowl: required by the S001 summary and not in the archive", out)
        self.assertIn("S001/coronation: required by the S001 summary and not in the archive", out)

    def test_game_without_traces_fails(self):
        rc, out = run(self.archive(plain=("S001/po:W:conference:s2v1",)))
        self.assertEqual(rc, 1)
        self.assertIn("S001/po:W:conference:s2v1: no spatial traces are stored", out)

    def test_lost_trace_rows_fail(self):
        path = self.archive()
        db = sqlite3.connect(path)
        for t in A.HISTORY_TABLES:
            for op in ("update", "delete"):
                db.execute(f"DROP TRIGGER IF EXISTS {t}_no_{op}")
        db.execute("DELETE FROM traces WHERE game_id = 'S001/coronation' AND play_i = 1")
        db.commit()
        db.close()
        rc, out = run(path)
        self.assertEqual(rc, 1)
        self.assertIn("S001/coronation: declares 2 traces, 1 stored", out)

    def test_extra_game_and_wrong_score(self):
        extra = game(1, "po:E:conference:s9v9", "playoff", "E8", "E9", [3, 4])
        rc, out = run(self.archive(extra=(extra,), wrong_score="S001/po:W:wild_card:s6v3"))
        self.assertEqual(rc, 1)
        self.assertIn("S001/po:E:conference:s9v9: in the archive and not in the S001 summary", out)
        self.assertIn("stored score [1, 2], the summary says [10, 20]", out)

    def test_journal_comparison(self):
        path = self.archive()
        league = os.path.join(self.tmp, "league.db")
        db = sqlite3.connect(league)
        db.execute("CREATE TABLE journal (seq INTEGER PRIMARY KEY, n_records INTEGER, records_digest TEXT)")
        a = sqlite3.connect(path)
        for seq, n, h in a.execute("SELECT seq, n, sha256 FROM batches"):
            db.execute("INSERT INTO journal VALUES (?,?,?)", (seq, n, h))
        db.execute("INSERT INTO journal VALUES (70, 2, 'x')")          # an event the archive does not hold
        db.commit()
        db.close()
        a.close()
        rc, out = run(path, "--league", league)
        self.assertEqual(rc, 1)
        self.assertIn("the journal records events the archive lacks: [70]", out)

    def test_never_writes(self):
        path = self.archive()
        def read():
            with open(path, "rb") as f:
                return f.read()
        before = read()
        run(path)
        self.assertEqual(before, read())


if __name__ == "__main__":
    unittest.main()
