"""Durable saving (src/dfl/persist.py): an uninterrupted league and a league that was saved, killed and restored must be the same league.

The reference is a plain `Universe` stepped event by event. Every persisted or restored run is compared with it by the journal digest
after each event and by an exhaustive digest of the whole object graph at the end (`persist.deep_digest`: every value, every random
generator's state, independent of dict and set order, so it holds across processes with different hash randomisation).
Games use the placeholder resolver so that the tests run in seconds; one class repeats the proof with the 22-player engine."""
import json
import os
import pickle
import random
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _diag  # noqa: F401,E402  (failure diagnostics; see tests/_diag.py)
from dfl import persist  # noqa: E402
from dfl.year import Universe  # noqa: E402

TABLES = os.path.join(ROOT, "data", "season1")
TOOL = os.path.join(ROOT, "tools", "league_store.py")
N = 52                    # events in the reference: one whole year (49 events) and three of the next
H = 20                    # the shorter horizon most tests run to: four game weeks into the season


class Boom(Exception):
    pass


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="dflpersist")
        u = Universe(TABLES, seed=1, engine=False)
        cls.ref = []
        cls.ref_deep = {}
        for i in range(1, N + 1):
            u.step_event()
            cls.ref.append(persist.quick_digest(u))
            if i in (13, H, N):
                cls.ref_deep[i] = persist.deep_digest(u)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.tmp, f"{self._testMethodName}_{name}.db")

    def new(self, name="a", every=5, **kw):
        return persist.Runner.create(self.path(name), TABLES, seed=1, snapshot_every=every, engine=False, **kw)

    def journal(self, p):
        db = sqlite3.connect(p)
        try:
            return [d for (d,) in db.execute("SELECT digest FROM journal ORDER BY seq")]
        finally:
            db.close()


class Equivalence(Base):
    def test_a_saved_run_killed_as_the_year_closes_is_the_same_league_as_an_unsaved_one(self):
        r = self.new()
        r.run_events(49)                                       # the whole year; the year-close snapshot is a checkpoint
        r.db.close()                                           # killed the instant the year closes
        r = persist.Runner.open(self.path("a"))
        self.assertEqual((r.seq, r.u.cursor), (49, None))
        self.assertEqual(persist.quick_digest(r.u), self.ref[48])
        r.run_events(N - 49)                                   # the next year opens on the first event after the restart
        self.assertEqual(self.journal(r.path), self.ref)
        self.assertEqual(persist.deep_digest(r.u), self.ref_deep[N])
        self.assertEqual(persist.verify(r.path), [])
        r.close()

    def test_stepping_event_by_event_is_the_same_as_playing_a_whole_year(self):
        a = Universe(TABLES, seed=1, engine=False)
        a.play_year()                                           # the original whole-year entry point
        self.assertIsNone(a.cursor)
        self.assertEqual(persist.quick_digest(a), self.ref[48])  # the reference was built one event at a time

    def test_killed_and_restored_at_any_point_ends_in_the_same_league(self):
        # 13: week 1 has just been played, the exact state is compared with the reference's at that moment;
        # 17: mid-season with a season in flight and the newest snapshot two events back, so recovery replays
        for k, goal in ((13, H), (17, H)):
            with self.subTest(killed_after=k):
                name = f"k{k}"
                r = self.new(name)
                r.run_events(k)
                r.db.close()                                   # the process dies: nothing is closed or flushed by us
                del r
                r2 = persist.Runner.open(self.path(name))
                self.assertEqual(r2.seq, k)
                self.assertEqual(persist.quick_digest(r2.u), self.ref[k - 1])
                if k in self.ref_deep:
                    self.assertEqual(persist.deep_digest(r2.u), self.ref_deep[k])
                r2.run_events(goal - k)
                self.assertEqual(self.journal(self.path(name)), self.ref[:goal])
                self.assertEqual(persist.deep_digest(r2.u), self.ref_deep[goal])
                r2.close()

    def test_a_restored_run_does_not_repeat_or_lose_an_event(self):
        r = self.new("once", every=1)
        r.run_events(12)
        r.close()
        r2 = persist.Runner.open(self.path("once"))
        r2.run_events(3)
        db = sqlite3.connect(self.path("once"))
        seqs = [s for (s,) in db.execute("SELECT seq FROM journal ORDER BY seq")]
        keys = [k for (k,) in db.execute("SELECT key FROM journal ORDER BY seq")]
        db.close()
        self.assertEqual(seqs, list(range(1, 16)))
        self.assertEqual(len(r2.u.fired), 15)
        self.assertEqual([f[2] for f in r2.u.fired], keys)
        r2.close()


class Atomicity(Base):
    def test_an_exception_inside_an_event_changes_nothing_and_the_retry_is_exact(self):
        r = self.new("exc")
        r.run_events(10)
        before_u, before_seq = persist.quick_digest(r.u), r.seq
        real = Universe.on_venue_vote                          # event 10 is the venue vote... fire the next, event 11 (final_cuts)
        real_cuts = Universe.on_final_cuts

        def broken(self_, year, date, key):
            real_cuts(self_, year, date, key)                  # the event gets half way: the world has already changed
            raise Boom("power failure")
        Universe.on_final_cuts = broken
        try:
            with self.assertRaises(Boom):
                r.step()
        finally:
            Universe.on_final_cuts = real_cuts
        self.assertEqual(r.seq, before_seq)
        self.assertEqual(persist.quick_digest(r.u), before_u)  # the half-changed league was thrown away and reloaded
        self.assertEqual(len(self.journal(r.path)), 10)
        r.run_events(H - 10)
        self.assertEqual(self.journal(r.path), self.ref[:H])
        self.assertEqual(persist.deep_digest(r.u), self.ref_deep[H])
        r.close()

    def test_a_crash_between_the_journal_row_and_the_snapshot_rolls_the_whole_event_back(self):
        calls = []

        def hook(point):
            if point == "journal_written":                             # the commit has several hook points (v2 added "archive_written"); count one
                calls.append(point)
                if len(calls) == 4:                                    # the 4th event, after its journal row is written
                    raise Boom("killed mid-commit")
        r = self.new("mid", every=1, crash_hook=hook)
        r.run_events(3)
        with self.assertRaises(Boom):
            r.step()
        self.assertEqual(len(self.journal(r.path)), 3)         # no row, no snapshot: all or nothing
        self.assertEqual(persist.verify(r.path), [])
        r.crash_hook = None
        r.run_events(H - 3)
        self.assertEqual(self.journal(r.path), self.ref[:H])
        r.close()

    def test_a_second_runner_cannot_fork_the_league(self):
        r1 = self.new("fork")
        r1.run_events(3)
        r2 = persist.Runner.open(r1.path)
        r1.step()                                              # r1 moves on to event 4
        with self.assertRaises(persist.ConcurrentWriter):
            r2.step()                                          # r2 still believes the file is at 3
        self.assertEqual(r2.seq, 4)                            # it reloaded the real state instead of keeping a fork
        r2.step()
        self.assertEqual(self.journal(r1.path), self.ref[:5])
        r1.close()
        r2.close()


class ProcessDeath(Base):
    def run_tool(self, *args, hashseed="0"):
        env = dict(os.environ, PYTHONHASHSEED=hashseed)
        return subprocess.run([sys.executable, "-I", TOOL, *args], capture_output=True, text=True, cwd=ROOT, env=env)

    def test_a_process_killed_in_the_middle_of_a_commit_recovers_in_another_process(self):
        p = self.path("proc")
        self.assertEqual(self.run_tool("init", p, "--placeholder", "--snapshot-every", "4", hashseed="1").returncode, 0)
        # process 1 dies (os._exit, no clean-up) inside the 17th commit, after its journal row is written but before COMMIT
        died = self.run_tool("run", p, "--events", "19", "--die-inside", "17", hashseed="11")
        self.assertEqual(died.returncode, 9, died.stderr)
        st = json.loads(self.run_tool("status", p, hashseed="12").stdout)
        self.assertEqual(st["events"], 16)                     # event 17 never happened as far as the file is concerned
        self.assertEqual(json.loads(self.run_tool("verify", p, "--deep", hashseed="13").stdout)["ok"], True)
        # process 2, with different hash randomisation, finishes the job
        done = self.run_tool("run", p, "--events", str(H - 16), "--deep", hashseed="999")
        self.assertEqual(done.returncode, 0, done.stderr)
        res = json.loads(done.stdout)
        self.assertEqual(res["events"], H)
        self.assertEqual(res["deep"], self.ref_deep[H])        # the league the uninterrupted run reached, value for value
        self.assertEqual(self.journal(p), self.ref[:H])

    def test_a_process_killed_between_events_recovers_too(self):
        p = self.path("after")
        self.run_tool("init", p, "--placeholder", "--snapshot-every", "6", hashseed="2")
        self.assertEqual(self.run_tool("run", p, "--events", "19", "--die-after", "14", hashseed="21").returncode, 9)
        res = json.loads(self.run_tool("run", p, "--events", str(H - 14), "--deep", hashseed="22").stdout)
        self.assertEqual((res["events"], res["deep"]), (H, self.ref_deep[H]))


class Damage(Base):
    def test_a_damaged_newest_snapshot_falls_back_to_an_older_one_and_replays(self):
        r = self.new("snap", every=5)
        r.run_events(18)
        r.close()
        db = sqlite3.connect(self.path("snap"))
        newest = db.execute("SELECT MAX(seq) FROM snapshots").fetchone()[0]
        self.assertEqual(newest, 15)
        db.execute("UPDATE snapshots SET payload = ? WHERE seq = ?", (b"damaged", newest))
        db.commit()
        db.close()
        self.assertTrue(any("snapshot 15" in p for p in persist.verify(self.path("snap"))))
        r2 = persist.Runner.open(self.path("snap"))            # snapshot 10, replay 11 to 18
        self.assertEqual(r2.seq, 18)
        self.assertEqual(persist.quick_digest(r2.u), self.ref[17])
        r2.run_events(H - 18)
        self.assertEqual(persist.deep_digest(r2.u), self.ref_deep[H])
        r2.close()

    def test_a_league_file_with_no_good_snapshot_is_reported_not_guessed(self):
        r = self.new("none", every=5)
        r.run_events(7)
        r.close()
        db = sqlite3.connect(self.path("none"))
        db.execute("UPDATE snapshots SET payload = x'00'")
        db.commit()
        db.close()
        with self.assertRaises(persist.Unrecoverable):
            persist.Runner.open(self.path("none"))

    def test_an_edited_journal_is_detected_by_the_hash_chain_and_by_replay(self):
        r = self.new("tamper", every=10)
        r.run_events(15)
        r.close()
        db = sqlite3.connect(self.path("tamper"))
        db.execute("UPDATE journal SET digest = ? WHERE seq = 12", ("0" * 64,))
        db.commit()
        db.close()
        self.assertTrue(any("chain broken at event 12" in p for p in persist.verify(self.path("tamper"))))
        with self.assertRaises(persist.ReplayDivergence):       # snapshot 10, replaying 11 to 15 meets the edited row
            persist.Runner.open(self.path("tamper"))

    def test_a_gap_in_the_journal_is_detected(self):
        r = self.new("gap", every=10)
        r.run_events(6)
        r.close()
        db = sqlite3.connect(self.path("gap"))
        db.execute("DELETE FROM journal WHERE seq = 3")
        db.commit()
        db.close()
        self.assertTrue(any("journal gap" in p for p in persist.verify(self.path("gap"))))

    def test_a_file_written_by_other_code_is_refused_unless_allowed(self):
        r = self.new("code")
        r.run_events(2)
        r.close()
        db = sqlite3.connect(self.path("code"))
        db.execute("UPDATE meta SET value = ? WHERE key = 'fingerprint'", (json.dumps("0" * 64),))
        db.commit()
        db.close()
        with self.assertRaises(persist.CodeMismatch):
            persist.Runner.open(self.path("code"))
        persist.Runner.open(self.path("code"), allow_code_change=True).close()

    def test_a_snapshot_cannot_carry_code_to_run(self):
        evil = pickle.dumps(os.system)                           # a pickle that names os.system
        import zlib
        with self.assertRaises(pickle.UnpicklingError):
            persist.loads(zlib.compress(evil))
        with self.assertRaises(pickle.UnpicklingError):
            persist.loads(zlib.compress(pickle.dumps(subprocess.Popen)))


class BackupRestore(Base):
    def test_backup_while_running_restores_to_the_same_league_and_continues_identically(self):
        r = self.new("live", every=4)
        r.run_events(14)
        b = self.path("backup")
        r.backup(b)                                             # taken while the runner has the file open
        r.run_events(H - 14)                                    # the live league carries on
        self.assertEqual(persist.verify(b), [])
        self.assertEqual(persist.status(b)["events"], 14)
        r2 = persist.restore(b, self.path("restored"))
        self.assertEqual(r2.seq, 14)
        self.assertEqual(persist.quick_digest(r2.u), self.ref[13])
        r2.run_events(H - 14)
        self.assertEqual(persist.deep_digest(r2.u), persist.deep_digest(r.u))     # restored and never-stopped runs agree
        self.assertEqual(persist.deep_digest(r2.u), self.ref_deep[H])
        self.assertEqual(self.journal(self.path("restored")), self.ref[:H])
        r.close()
        r2.close()

    def test_restore_never_overwrites_and_refuses_a_damaged_backup(self):
        r = self.new("src", every=3)
        r.run_events(6)
        b = self.path("bk")
        r.backup(b)
        with self.assertRaises(persist.PersistError):
            r.backup(b)                                         # already exists
        with self.assertRaises(persist.PersistError):
            persist.restore(b, r.path)                          # would overwrite the live file
        bad = self.path("bad")
        shutil.copy(b, bad)
        db = sqlite3.connect(bad)
        db.execute("UPDATE journal SET key = 'x' WHERE seq = 2")
        db.commit()
        db.close()
        with self.assertRaises(persist.Corrupt):
            persist.restore(bad, self.path("never"))
        self.assertFalse(os.path.exists(self.path("never")))
        r.close()

    def test_the_command_line_backup_and_restore(self):
        p, b, q = self.path("cli"), self.path("clibk"), self.path("cliq")
        run = lambda *a: subprocess.run([sys.executable, "-I", TOOL, *a], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(run("init", p, "--placeholder", "--snapshot-every", "5").returncode, 0)
        self.assertEqual(run("run", p, "--events", "12").returncode, 0)
        self.assertEqual(run("backup", p, b).returncode, 0)
        self.assertEqual(run("restore", b, q).returncode, 0)
        self.assertEqual(json.loads(run("digest", p).stdout)["deep"], json.loads(run("digest", q).stdout)["deep"])
        self.assertEqual(run("verify", q, "--deep").returncode, 0)


class DeepDigest(unittest.TestCase):
    def test_it_ignores_order_and_identity_but_sees_every_value(self):
        a = {"x": [1, 2, {3, 4, 5}], "y": {"b": 1, "a": 2}, "r": random.Random(5)}
        b = {"r": random.Random(5), "y": {"a": 2, "b": 1}, "x": [1, 2, {5, 4, 3}]}
        self.assertEqual(persist.deep_digest(a), persist.deep_digest(b))
        b["r"].random()                                         # a generator one draw further on is a different league
        self.assertNotEqual(persist.deep_digest(a), persist.deep_digest(b))
        c = {"x": [1, 2, {3, 4, 6}], "y": {"b": 1, "a": 2}, "r": random.Random(5)}
        self.assertNotEqual(persist.deep_digest(a), persist.deep_digest(c))

    def test_shared_references_and_cycles_are_handled(self):
        shared = [1, 2]
        x = {"a": shared, "b": shared}
        x["self"] = x
        self.assertEqual(persist.deep_digest(x), persist.deep_digest(x))


@unittest.skipUnless(os.environ.get("DFL_SLOW_TESTS", "1") == "1", "slow")
class SpatialEngineRestore(unittest.TestCase):
    """The same proof with the league's default engine: the first game week and the first Tour round, 22 players on the field, killed and restored between."""
    def test_restored_equals_uninterrupted_with_the_22_player_engine(self):
        tmp = tempfile.mkdtemp(prefix="dflspatial")
        try:
            u = Universe(TABLES, seed=2)
            for _ in range(14):                                  # week_1 (event 13: 16 games) and tour round 1 (event 14: 4 games)
                u.step_event()
            want = persist.deep_digest(u)
            p = os.path.join(tmp, "s.db")
            r = persist.Runner.create(p, TABLES, seed=2, snapshot_every=6)
            r.run_events(13)                                     # week_1 played, then "killed"
            r.close()
            r = persist.Runner.open(p)                           # snapshot at 12, replay of week_1 under the real engine
            self.assertEqual(r.seq, 13)
            r.run_events(1)
            self.assertEqual(persist.deep_digest(r.u), want)
            r.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
