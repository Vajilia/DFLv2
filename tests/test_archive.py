"""The permanent archive (src/dfl/archive.py): every game as played, with every spatial snap's trace.

Proved here: capturing history never changes what the league does; the archive is complete (every simulated snap has its trace) and sound
(every row re-hashes); it can be read without any DFL code; a retry after a crash neither duplicates nor alters history, and a conflicting
write is refused; the archive cannot be edited through its own interface; league file and archive back up and restore as a pair."""
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
import numpy as np  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _diag  # noqa: F401,E402  (failure diagnostics; see tests/_diag.py)
from dfl import archive as A, persist  # noqa: E402
from dfl.engine.spatial import trace as dfl_trace  # noqa: E402
from dfl.year import Universe  # noqa: E402

TABLES = os.path.join(ROOT, "data", "season1")
READER = os.path.join(ROOT, "tools", "read_archive.py")


class Boom(Exception):
    pass


def reader(*args):
    """The standard-library-only reader, run with no site-packages at all (-S): no numpy, no DFL code."""
    p = subprocess.run([sys.executable, "-I", "-S", READER, *args], capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="dflarchive")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.tmp, f"{self._testMethodName}_{name}.db")

    def league(self, name="a", every=5, **kw):
        kw.setdefault("spatial", False)
        return persist.Runner.create(self.path(name), TABLES, seed=1, snapshot_every=every, **kw)


def step_until_games(r, limit=40):
    """Step until the archive holds its first batch of games."""
    for _ in range(limit):
        r.step()
        if r.archive.db.execute("SELECT COUNT(*) FROM games").fetchone()[0]:
            return r.seq
    raise AssertionError("no game was played")


class SpatialWeek(Base):
    """One real week of the 22-player engine, archived, against the same week with the archive off."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.p = os.path.join(cls.tmp, "spatial.db")
        r = persist.Runner.create(cls.p, TABLES, seed=1, snapshot_every=50, spatial=True)
        cls.n = step_until_games(r)
        cls.archived = [d for (d,) in sqlite3.connect(cls.p).execute("SELECT digest FROM journal ORDER BY seq")]
        cls.week_games = len(r.u.run.week_rows[1])
        r.close()
        u = Universe(TABLES, seed=1, spatial=True)                       # the same league with nothing archived
        cls.plain = []
        for _ in range(cls.n):
            u.step_event()
            cls.plain.append(persist.quick_digest(u))
        cls.a = A.Archive.open(cls.p + ".archive")

    @classmethod
    def tearDownClass(cls):
        cls.a.close()
        super().tearDownClass()

    def test_capturing_history_does_not_change_what_the_league_does(self):
        self.assertEqual(self.archived, self.plain)                       # same digest after every event, games and traces on or off

    def test_the_week_is_archived_whole_and_the_archive_is_sound(self):
        self.assertEqual(len(self.a.games()), self.week_games)
        self.assertEqual(A.verify(self.p + ".archive"), [])
        for gid, season, kind, home, away, engine, n_traces in self.a.games():
            self.assertEqual((season, kind, engine), (1, "regular", "spatial"))
            self.assertGreater(n_traces, 50)
            g = self.a.game(gid)
            snaps = [e for e in g["table"] if "call" in e]                 # every snap the 22-player engine simulated carries its call...
            self.assertEqual(len(snaps), n_traces)                         # ...and has its trace
            self.assertTrue(all(e.get("has_trace") for e in snaps))
            self.assertIn("Ed 8.52", g["rule_version"])                    # stored with the rule and engine version it was played under

    def test_a_trace_is_the_positions_as_played_and_reads_back_exactly(self):
        gid = self.a.games()[0][0]
        g = self.a.game(gid)
        i = next(e["i"] for e in g["table"] if e.get("has_trace"))
        head, pos, ball = A.parse_trace(self.a.trace_bytes(gid, i))
        self.assertEqual((head["game_id"], head["play_i"], head["engine"]), (gid, i, "spatial"))
        self.assertEqual(len(head["roles"]), 22)
        self.assertEqual(len(pos), head["steps"] * 44)
        self.assertEqual(len(ball), head["steps"] * 3)
        self.assertTrue(head["events"] and head["intents"])
        # and it equals what the engine's own decoder gives for the same play, played again from the same seed
        from dfl.engine.game import play_game
        import random
        u = Universe(TABLES, seed=1, spatial=True)
        while not u.step_event().startswith("week"):
            pass
        eng = u.resolver
        row = u.run.sched[0]
        res = play_game(eng.squad(row["home"]), eng.squad(row["away"]), random.Random(5), False, log=True, curve=eng.w.ep_curve, spatial=True,
                        trace=True)
        entry = next(e for e in res.table if "trace" in e)
        rec = eng.game_record({"id": "x"}, res)
        eng.season = 1
        row_, body, traces = A.encode_game(dict(rec, gid="S001/x", season=1, kind="regular"))
        head2, pos2, ball2 = A.parse_trace(traces[entry["i"]])
        dec = dfl_trace.decode(entry["trace"])
        self.assertTrue(np.array_equal(np.array(pos2, dtype=np.float64).reshape(-1, 22, 2) / 100.0, dec["pos"]))
        self.assertTrue(np.array_equal(np.array(ball2, dtype=np.float64).reshape(-1, 3) / 100.0, dec["ball"]))
        self.assertEqual(head2["ids"], list(dec["ids"]))

    def test_it_can_be_read_with_no_dfl_code_and_no_third_party_library(self):
        rc, out, err = reader("verify", self.p + ".archive")
        self.assertEqual((rc, out.strip()), (0, "OK"), err)
        rc, out, err = reader("list", self.p + ".archive")
        rows = [ln.split("\t") for ln in out.strip().splitlines()]
        self.assertEqual(len(rows), self.week_games)
        gid, kind, home, away, engine, n = rows[0]
        rc, out, err = reader("game", self.p + ".archive", gid)
        g = json.loads(out)
        self.assertEqual((g["gid"], g["home"], g["away"]), (gid, home, away))
        self.assertEqual(g, self.a.game(gid))                              # the stand-alone reader and the module agree
        i = next(e["i"] for e in g["table"] if e.get("has_trace"))
        rc, out, err = reader("trace", self.p + ".archive", gid, str(i), "--every", "10")
        t = json.loads(out)
        head, pos, ball = A.parse_trace(self.a.trace_bytes(gid, i))
        self.assertEqual(t["steps"], head["steps"])
        self.assertEqual(len(t["frames"]), -(-head["steps"] // 10))
        self.assertEqual(t["frames"][0]["p"][0], [pos[0] / 100.0, pos[1] / 100.0])
        self.assertEqual(t["roles"], head["roles"])

    def test_traces_cannot_be_edited_or_removed(self):
        for sql in ("UPDATE traces SET sha256 = 'x'", "DELETE FROM traces", "UPDATE games SET sha256 = 'x'"):
            with self.subTest(sql), self.assertRaises(sqlite3.IntegrityError):
                self.a.db.execute(sql)
        self.assertEqual(A.verify(self.p + ".archive"), [])

    def test_storage_is_measured_from_the_file(self):
        s = A.stats(self.p + ".archive")
        self.assertEqual(s["games"], self.week_games)
        self.assertGreater(s["per_trace_bytes"], 500)
        self.assertLess(s["per_trace_bytes"], 20000)
        self.assertEqual(s["trace_bytes"] + s["game_row_bytes"], sum(b for _, b in s["per_season"].values()))


class Retry(Base):
    def reference(self, n):
        u = Universe(TABLES, seed=1, spatial=False, archive=True)
        d = tempfile.mkdtemp(dir=self.tmp)
        ref = A.Archive.open_or_create(os.path.join(d, "ref.archive"), {"fingerprint": "ref", "how": "test", "seq": 0})
        digests = []
        for k in range(n):
            u.step_event()
            digests.append(persist.quick_digest(u))
            recs = u.take_archive()
            if recs:
                ref.put_batch(k + 1, recs)
        ref.close()
        return A.digest(os.path.join(d, "ref.archive")), digests

    def test_writing_the_same_batch_again_changes_nothing(self):
        u = Universe(TABLES, seed=1, spatial=False, archive=True)
        a = A.Archive.open_or_create(self.path("r") + ".archive", {"fingerprint": "t", "how": "test", "seq": 0})
        batches = []
        for k in range(1, 24):
            u.step_event()
            recs = u.take_archive()
            if recs:
                a.put_batch(k, recs)
                batches.append((k, recs))
        self.assertGreater(len(batches), 3)
        before = A.digest(a.path)
        n_before = a.db.execute("SELECT COUNT(*) FROM games").fetchone()[0]
        for k, recs in batches:
            a.put_batch(k, recs)                                           # a retry of everything
            a.confirm_batch(k, recs)
        self.assertEqual((A.digest(a.path), a.db.execute("SELECT COUNT(*) FROM games").fetchone()[0]), (before, n_before))
        self.assertEqual(A.verify(a.path), [])
        a.close()

    def test_a_different_record_under_an_existing_id_is_refused_and_nothing_is_written(self):
        u = Universe(TABLES, seed=1, spatial=False, archive=True)
        a = A.Archive.open_or_create(self.path("c") + ".archive", {"fingerprint": "t", "how": "test", "seq": 0})
        k = 0
        while True:
            k += 1
            u.step_event()
            recs = u.take_archive()
            if recs:
                break
        a.put_batch(k, recs)
        before = A.digest(a.path)
        forged = [dict(recs[0], score=[recs[0]["score"][0] + 7, recs[0]["score"][1]])] + recs[1:]
        with self.assertRaises(A.ArchiveConflict):
            a.put_batch(k, forged)                                         # same event, different history
        with self.assertRaises(A.ArchiveConflict):
            a.confirm_batch(k, forged)
        other = [dict(forged[0])] + []                                     # same game id in a later event
        with self.assertRaises(A.ArchiveConflict):
            a.put_batch(k + 100, other)
        self.assertEqual(A.digest(a.path), before)
        a.close()

    def test_history_cannot_be_edited_through_the_archive_file(self):
        r = self.league()
        step_until_games(r)
        r.close()
        db = sqlite3.connect(self.path("a") + ".archive")
        for sql in ("UPDATE games SET home = 'x'", "DELETE FROM games", "UPDATE batches SET n = 0", "DELETE FROM batches"):
            with self.subTest(sql), self.assertRaises(sqlite3.IntegrityError):
                db.execute(sql)
        names = {n for (n,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        self.assertEqual(names, {f"{t}_no_{op}" for t in A.HISTORY_TABLES for op in ("update", "delete")})     # traces and records too
        db.close()

    def test_a_crash_after_the_archive_write_and_before_the_league_commit_is_repaired_by_the_retry(self):
        ref_digest, ref_journal = self.reference(30)
        seen = {"armed": False, "n": None}

        def hook(point):
            if point == "archive_written" and seen["armed"]:
                n = r.archive.db.execute("SELECT COUNT(*) FROM batches").fetchone()[0]
                if n > seen["n"]:                                          # the archive now holds this event; die before the journal does
                    raise Boom("killed between the archive and the league file")
        r = self.league("x", every=3, crash_hook=hook)
        r.run_events(12)
        seen.update(armed=True, n=r.archive.db.execute("SELECT COUNT(*) FROM batches").fetchone()[0])
        head = r.seq
        with self.assertRaises(Boom):
            while True:
                r.step()
        self.assertEqual(persist.status(r.path)["events"], r.seq)           # the league file stands at the last committed event
        ahead = r.archive.db.execute("SELECT MAX(seq) FROM batches").fetchone()[0]
        self.assertEqual(ahead, r.seq + 1)                                 # and the archive is exactly one event ahead, never behind
        seen["armed"] = False
        r.run_events(30 - r.seq)                                           # the repeated event finds identical rows and moves on
        self.assertEqual(A.digest(r.archive.path), ref_digest)             # same history as a run that never crashed
        self.assertEqual([d for (d,) in sqlite3.connect(r.path).execute("SELECT digest FROM journal ORDER BY seq")], ref_journal)
        self.assertEqual(A.verify(r.archive.path), [])
        r.close()

    def test_an_archive_that_is_behind_the_league_is_refused_unless_repair_is_asked_for(self):
        ref_digest, ref_journal = self.reference(20)
        r = self.league("b", every=5)
        r.run_events(15)
        old = A.backup(r.archive.path, self.path("old") + ".archive")        # an older copy of the archive, taken at a snapshot
        r.run_events(3)                                                      # league at 18: recovery replays 16 to 18 from the snapshot at 15
        r.close()
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(self.path("b") + ".archive" + ext):
                os.remove(self.path("b") + ".archive" + ext)
        shutil.copy(old, self.path("b") + ".archive")
        with self.assertRaises(persist.ArchiveInconsistent):
            persist.Runner.open(self.path("b"))                              # the journal says events the archive never saw
        r = persist.Runner.open(self.path("b"), repair_archive=True)         # the archive is rebuilt from the deterministic replay
        r.run_events(2)
        self.assertEqual(A.digest(r.archive.path), ref_digest)
        self.assertEqual(A.verify(r.archive.path), [])
        r.close()


class Damage(Base):
    def tamper(self, archive_path, sql, args=()):
        db = sqlite3.connect(archive_path)
        for t in A.HISTORY_TABLES:
            for op in ("update", "delete"):
                db.execute(f"DROP TRIGGER IF EXISTS {t}_no_{op}")
        db.execute(sql, args)
        db.commit()
        db.close()

    def test_verify_finds_damage_to_a_game_a_trace_or_a_missing_trace(self):
        r = persist.Runner.create(self.path("d"), TABLES, seed=1, spatial=True, snapshot_every=50)
        step_until_games(r)
        r.close()
        ap = self.path("d") + ".archive"
        self.assertEqual(A.verify(ap), [])
        for name, sql, words in (
                ("game", "UPDATE games SET sha256 = '0' WHERE rowid = 1", "game"),
                ("trace", "UPDATE traces SET sha256 = '0' WHERE rowid = 1", "trace"),
                ("missing", "DELETE FROM traces WHERE rowid = (SELECT MIN(rowid) FROM traces)", "traces")):
            with self.subTest(name):
                copy = self.path("d_" + name) + ".archive"
                shutil.copy(ap + "", copy)
                self.tamper(copy, sql)
                problems = A.verify(copy)
                self.assertTrue(problems and any(words in x for x in problems), problems)
                rc, out, err = reader("verify", copy)
                self.assertEqual(rc, 1)

    def test_a_damaged_archive_cannot_be_backed_up_or_restored_as_if_it_were_fine(self):
        r = self.league("e")
        step_until_games(r)
        r.close()
        ap = self.path("e") + ".archive"
        self.tamper(ap, "UPDATE games SET sha256 = '0' WHERE rowid = 1")
        with self.assertRaises(A.ArchiveError):
            A.backup(ap, self.path("e_bak") + ".archive")
        with self.assertRaises(A.ArchiveError):
            persist.backup_set(self.path("e"), os.path.join(self.tmp, "e_set"))


class BackupSet(Base):
    def test_league_and_archive_back_up_together_and_the_restored_pair_goes_on_identically(self):
        r = self.league("s", every=4)
        r.run_events(16)
        out = persist.backup_set(self.path("s"), os.path.join(self.tmp, "set16"))
        self.assertTrue(os.path.exists(out["league"]) and os.path.exists(out["archive"]))
        r.run_events(6)                                                      # the live league moves on; the backup is the state at 16
        want = A.digest(r.archive.path)
        want_deep = persist.deep_digest(r.u)
        r.close()
        dest = os.path.join(self.tmp, "restored")
        n = persist.restore_set(os.path.dirname(out["league"]), dest)
        self.assertEqual(n.seq, 16)
        self.assertEqual(A.verify(os.path.join(dest, "archive.db")), [])
        n.run_events(6)
        self.assertEqual(persist.deep_digest(n.u), want_deep)               # same league...
        self.assertEqual(A.digest(os.path.join(dest, "archive.db")), want)   # ...and same history
        self.assertEqual(persist.verify(os.path.join(dest, "league.db"), deep=True), [])
        n.close()
        with self.assertRaises(persist.PersistError):                       # a restore never overwrites
            persist.restore_set(os.path.dirname(out["league"]), dest)


class SeasonRecord(Base):
    def test_a_finished_season_leaves_its_summary_in_the_archive_and_is_readable_without_dfl(self):
        r = self.league("y", every=7, engine=False)
        r.run_year()
        champ = r.u.reports[-1]["winner"]
        r.close()
        a = A.Archive.open(self.path("y") + ".archive")
        s = a.record("S001/summary")
        a.close()
        self.assertEqual(s["result"]["coronation_winner"], champ)
        self.assertIn("Eastern/East", s["result"]["outcome"]["division_orders"])        # no tuple keys: plain JSON
        rc, out, err = reader("season", self.path("y") + ".archive", "1")
        self.assertEqual(json.loads(out)["result"]["coronation_winner"], champ)
        self.assertEqual(A.verify(self.path("y") + ".archive"), [])


if __name__ == "__main__":
    unittest.main()
