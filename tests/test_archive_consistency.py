"""The archive must hold what the league says it committed (persist.archive_check, Runner.open).

Proved here: on opening, a league whose archive lacks records, holds different ones, belongs to another league, or is further ahead than an
interrupted commit can explain is refused (`ArchiveInconsistent`) before anything is written; an archive one event ahead after an
interrupted commit is accepted and the retry makes it exact; an older archive restored beside a newer league is refused even when the gap
is older than the newest snapshot, and can be rebuilt by replay (each rebuilt batch checked against the journal); an older league beside a
newer archive of the same league is refused unless asked for; the journal's hash chain covers the archive records, so editing what the
journal says the archive holds is detected; upgrade and import carry the same guarantee."""
import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl import archive as A, persist  # noqa: E402

TABLES = os.path.join(ROOT, "data", "season1")
STORE = os.path.join(ROOT, "tools", "league_store.py")
EARLY, MID, LATE = 15, 30, 40


class Boom(Exception):
    pass


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def journal(path):
    db = sqlite3.connect(path)
    try:
        return db.execute("SELECT seq, digest, chain, n_records, records_digest FROM journal ORDER BY seq").fetchall()
    finally:
        db.close()


def tamper(path, sql, args=()):
    """Edit a file the way a damaged disk or a careless tool would (the archive's append-only triggers are dropped first)."""
    db = sqlite3.connect(path)
    for t in A.HISTORY_TABLES:
        for op in ("update", "delete"):
            db.execute(f"DROP TRIGGER IF EXISTS {t}_no_{op}")
    db.execute(sql, args)
    db.commit()
    db.close()


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="dflconsistency")
        cls.at = {}
        d = os.path.join(cls.tmp, "src")
        os.makedirs(d)
        r = persist.Runner.create(os.path.join(d, "league.db"), TABLES, seed=1, snapshot_every=5, spatial=False)
        for stop in (EARLY, MID, LATE):
            r.run_events(stop - r.seq)
            cls.at[stop] = os.path.join(cls.tmp, f"at{stop}")
            os.makedirs(cls.at[stop])
            persist.backup(r.path, os.path.join(cls.at[stop], "league.db"))
            A.backup(r.archive.path, os.path.join(cls.at[stop], "league.db.archive"))
        cls.ref_journal = journal(r.path)
        cls.ref_digest = A.digest(r.archive.path)
        cls.batches = A.batch_table(r.archive.path)
        r.close()
        cls.early_batches = [s for s in cls.batches if s <= EARLY]
        cls.mid_batches = [s for s in cls.batches if EARLY < s <= MID]
        assert cls.early_batches and len(cls.mid_batches) >= 2, "the fixture must have games before and after the early point"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def pair(self, league_at, archive_at=None):
        """A fresh directory holding the league file from event `league_at` and the archive from event `archive_at` (default the same)."""
        d = tempfile.mkdtemp(dir=self.tmp)
        shutil.copy(os.path.join(self.at[league_at], "league.db"), os.path.join(d, "league.db"))
        shutil.copy(os.path.join(self.at[archive_at or league_at], "league.db.archive"), os.path.join(d, "league.db.archive"))
        return os.path.join(d, "league.db"), os.path.join(d, "league.db.archive")

    def finish(self, r, to=LATE):
        r.run_events(to - r.seq)
        j = journal(r.path)
        self.assertEqual(j, self.ref_journal[j[0][0] - 1:j[-1][0]])      # the same events, digests and chain as the uninterrupted run
        self.assertEqual(A.digest(r.archive.path), self.ref_digest)
        self.assertEqual(A.verify(r.archive.path), [])
        r.close()


class Consistent(Base):
    def test_a_matching_pair_is_clean_and_opens(self):
        lg, ar = self.pair(MID)
        c = persist.archive_check(lg)
        self.assertEqual((c["missing"], c["mismatched"], c["ahead"], c["foreign"], c["absent"], c["base_ok"]), ([], [], [], False, False, True))
        self.assertEqual(persist.verify(lg, archive=True), [])
        self.finish(persist.Runner.open(lg))

    def test_the_journal_records_how_many_records_each_event_archived_and_their_digest(self):
        rows = {s: (n, d) for s, _, _, n, d in self.ref_journal if n}
        self.assertEqual(rows, self.batches)
        self.assertTrue(all(n == 0 and d == "" for s, _, _, n, d in self.ref_journal if s not in self.batches))


class OlderArchiveBesideNewerLeague(Base):
    def test_it_is_refused_even_when_the_gap_is_older_than_the_newest_snapshot(self):
        lg, ar = self.pair(MID, archive_at=EARLY)                   # league at 30, snapshot at 30; archive misses events 16-30
        before = (journal(lg), sha(ar))
        with self.assertRaises(persist.ArchiveInconsistent) as cm:
            persist.Runner.open(lg)
        self.assertEqual(cm.exception.kinds, ("missing",))
        self.assertEqual(sorted(cm.exception.seqs), self.mid_batches)
        self.assertEqual((journal(lg), sha(ar)), before)            # nothing was written to either file
        self.assertTrue(any("lacks" in p for p in persist.verify(lg, archive=True)))

    def test_it_is_refused_by_every_way_of_continuing(self):
        lg, ar = self.pair(MID, archive_at=EARLY)
        for kw in ({}, {"heal": False}, {"allow_code_change": True}):
            with self.assertRaises(persist.ArchiveInconsistent):
                persist.Runner.open(lg, **kw)
        out = subprocess.run([sys.executable, STORE, "run", lg, "--events", "1"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 1)
        self.assertIn("ArchiveInconsistent", out.stdout)
        out = subprocess.run([sys.executable, STORE, "verify", lg], capture_output=True, text=True)
        self.assertEqual(out.returncode, 2)

    def test_replay_rebuilds_it_from_a_snapshot_before_the_gap_and_proves_each_batch_against_the_journal(self):
        lg, ar = self.pair(MID, archive_at=EARLY)
        r = persist.Runner.open(lg, repair_archive=True)
        self.assertEqual(r.seq, MID)
        self.assertEqual(persist.archive_check(lg)["missing"], [])
        self.assertEqual(A.batch_table(ar), {s: v for s, v in self.batches.items() if s <= MID})
        self.finish(r)

    def test_a_rebuild_that_replays_to_other_records_than_the_journal_committed_is_refused(self):
        lg, ar = self.pair(MID, archive_at=EARLY)
        tamper(lg, "UPDATE journal SET records_digest = ? WHERE seq = ?", ("0" * 64, self.mid_batches[0]))
        with self.assertRaises(persist.ReplayDivergence):
            persist.Runner.open(lg, repair_archive=True)
        self.assertEqual(A.batch_table(ar), {s: v for s, v in self.batches.items() if s <= EARLY})      # nothing was written

    def test_present_but_different_records_are_never_repaired(self):
        lg, ar = self.pair(MID)
        tamper(ar, "UPDATE batches SET sha256 = ? WHERE seq = ?", ("0" * 64, self.mid_batches[0]))
        with self.assertRaises(persist.ArchiveInconsistent):
            persist.Runner.open(lg, repair_archive=True)

    def test_a_batch_missing_from_the_middle_of_the_history_is_found(self):
        lg, ar = self.pair(MID)
        s = self.mid_batches[0]
        tamper(ar, "DELETE FROM batches WHERE seq = ?", (s,))
        with self.assertRaises(persist.ArchiveInconsistent) as cm:
            persist.Runner.open(lg)
        self.assertEqual((cm.exception.kinds, cm.exception.seqs), (("missing",), (s,)))


class MismatchedAndForeign(Base):
    def test_different_records_under_the_same_event_are_refused(self):
        lg, ar = self.pair(MID)
        s = self.mid_batches[0]
        tamper(ar, "UPDATE batches SET sha256 = ? WHERE seq = ?", ("f" * 64, s))
        with self.assertRaises(persist.ArchiveInconsistent) as cm:
            persist.Runner.open(lg)
        self.assertEqual((cm.exception.kinds, cm.exception.seqs), (("mismatched",), (s,)))
        tamper(ar, "UPDATE batches SET sha256 = ?, n = n + 1 WHERE seq = ?", (self.batches[s][1], s))
        with self.assertRaises(persist.ArchiveInconsistent):        # right digest, wrong count
            persist.Runner.open(lg)

    def test_a_batch_for_an_event_the_journal_says_had_no_records_is_refused(self):
        lg, ar = self.pair(MID)
        quiet = next(s for s in range(1, MID + 1) if s not in self.batches)
        tamper(ar, "INSERT INTO batches VALUES (?,?,?)", (quiet, 1, "a" * 64))
        with self.assertRaises(persist.ArchiveInconsistent) as cm:
            persist.Runner.open(lg)
        self.assertIn("mismatched", cm.exception.kinds)

    def test_another_leagues_archive_is_refused_before_anything_is_written(self):
        lg, ar = self.pair(MID)
        other = os.path.join(self.tmp, "other")
        os.makedirs(other, exist_ok=True)
        o = persist.Runner.create(os.path.join(other, f"{self._testMethodName}.db"), TABLES, seed=1, snapshot_every=50, spatial=False)
        o.run_events(MID)                                           # the same seed and the same history: identical games, different league
        self.assertEqual(A.batch_table(o.archive.path), {s: v for s, v in self.batches.items() if s <= MID})
        o_archive = A.backup(o.archive.path, os.path.join(other, "o.archive"))
        o.close()
        os.remove(ar)
        shutil.copy(o_archive, ar)
        before = journal(lg)
        with self.assertRaises(persist.ArchiveInconsistent) as cm:
            persist.Runner.open(lg)
        self.assertEqual(cm.exception.kinds, ("foreign",))
        self.assertEqual(journal(lg), before)
        self.assertIn("belongs to another league", " ".join(persist.verify(lg, archive=True)))

    def test_creating_a_league_beside_someone_elses_archive_is_refused(self):
        lg, ar = self.pair(MID)
        d = tempfile.mkdtemp(dir=self.tmp)
        with self.assertRaises(A.ArchiveError):
            persist.Runner.create(os.path.join(d, "new.db"), TABLES, seed=1, spatial=False, archive_path=ar)
        self.assertEqual(os.listdir(d), [])                         # no half-made league file is left behind

    def test_a_missing_or_unreadable_archive_is_refused_not_recreated_empty(self):
        lg, ar = self.pair(MID)
        os.remove(ar)
        with self.assertRaises(persist.ArchiveInconsistent) as cm:
            persist.Runner.open(lg)
        self.assertEqual(cm.exception.kinds, ("absent",))
        self.assertFalse(os.path.exists(ar))
        with open(ar, "wb") as f:
            f.write(b"this is not an archive" * 100)
        with self.assertRaises(persist.ArchiveInconsistent) as cm:
            persist.Runner.open(lg)
        self.assertEqual(cm.exception.kinds, ("unreadable",))


class ArchiveAhead(Base):
    def test_one_event_ahead_after_an_interrupted_commit_is_accepted_and_the_retry_makes_it_exact(self):
        lg, ar = self.pair(EARLY)
        s = self.mid_batches[0]
        r = persist.Runner.open(lg)
        r.run_events(s - 1 - r.seq)

        def die(point):
            if point == "archive_written":
                raise Boom()
        r.crash_hook = die
        with self.assertRaises(Boom):
            r.step()                                                # the archive has event s, the league's journal does not
        r.close()
        c = persist.archive_check(lg)
        self.assertEqual((c["head"], c["ahead"], c["missing"], c["mismatched"]), (s - 1, [s], [], []))
        self.assertEqual(persist.verify(lg, archive=True), [])
        st = subprocess.run([sys.executable, STORE, "status", lg], capture_output=True, text=True).stdout
        self.assertIn('"archive_ahead_by": 1', st)
        r = persist.Runner.open(lg)                                 # normal continuation, no flag needed
        self.assertEqual(r.seq, s - 1)
        self.finish(r)

    def test_an_older_league_beside_a_newer_archive_is_refused_unless_asked_for(self):
        lg, ar = self.pair(EARLY, archive_at=MID)                   # league at 15, archive of the same league at 30
        before = sha(ar)
        with self.assertRaises(persist.ArchiveInconsistent) as cm:
            persist.Runner.open(lg)
        self.assertEqual(cm.exception.kinds, ("ahead",))
        self.assertEqual(sorted(cm.exception.seqs), self.mid_batches)
        self.assertEqual(sha(ar), before)
        r = persist.Runner.open(lg, allow_archive_ahead=True)       # the extra events are confirmed as the league replays them
        self.assertEqual(r.seq, EARLY)
        r.run_events(MID - r.seq)
        self.assertEqual(A.batch_table(ar), {s: v for s, v in self.batches.items() if s <= MID})
        self.finish(r)

    def test_a_league_that_diverges_from_an_archive_that_is_ahead_is_stopped(self):
        lg, ar = self.pair(EARLY, archive_at=MID)
        s = self.mid_batches[0]
        tamper(ar, "UPDATE batches SET sha256 = ? WHERE seq = ?", ("e" * 64, s))
        r = persist.Runner.open(lg, allow_archive_ahead=True)
        with self.assertRaises(A.ArchiveConflict):
            r.run_events(MID - r.seq)


class JournalCoversTheArchive(Base):
    def test_editing_what_the_journal_says_the_archive_holds_breaks_the_hash_chain(self):
        lg, ar = self.pair(MID)
        s = self.mid_batches[0]
        tamper(lg, "UPDATE journal SET n_records = 0, records_digest = '' WHERE seq = ?", (s,))
        problems = persist.verify(lg)
        self.assertTrue(any(f"journal chain broken at event {s}" in p for p in problems), problems)
        with self.assertRaises(persist.ArchiveInconsistent):        # and the archive now holds a batch the journal denies
            persist.Runner.open(lg)

    def test_replay_checks_the_records_it_regenerates_against_the_journal(self):
        lg, ar = self.pair(MID)
        s = self.mid_batches[-1]
        db = sqlite3.connect(lg)
        db.execute("DELETE FROM snapshots WHERE seq > ?", (EARLY,))      # force a replay through the event
        db.commit()
        db.close()
        tamper(lg, "UPDATE journal SET records_digest = ? WHERE seq = ?", ("1" * 64, s))
        tamper(ar, "UPDATE batches SET sha256 = ? WHERE seq = ?", ("1" * 64, s))   # the pair now agree with each other, not with the games
        with self.assertRaises(persist.ReplayDivergence):
            persist.Runner.open(lg)


class UpgradeAndRestore(Base):
    def test_upgrade_refuses_a_pair_that_is_not_consistent_and_carries_a_consistent_one(self):
        lg, ar = self.pair(MID, archive_at=EARLY)
        new = os.path.join(os.path.dirname(lg), "new.db")
        with self.assertRaises(persist.ArchiveInconsistent):
            persist.upgrade(lg, new)
        self.assertFalse(os.path.exists(new))
        lg, ar = self.pair(MID)
        new = os.path.join(os.path.dirname(lg), "new.db")
        rep = persist.upgrade(lg, new)
        self.assertEqual(rep["events"], MID)
        n = persist.Runner.open(new)
        self.assertEqual(n.meta["league_id"], persist.read_meta(sqlite3.connect(lg))["league_id"])
        self.assertEqual(persist.archive_check(new)["base_ok"], True)
        self.finish(n)

    def test_importing_a_state_beside_the_wrong_or_short_archive_is_refused(self):
        lg, ar = self.pair(MID)
        doc = os.path.join(os.path.dirname(lg), "state.gz")
        persist.export_state(lg, doc)
        short = os.path.join(os.path.dirname(lg), "short.archive")
        shutil.copy(os.path.join(self.at[EARLY], "league.db.archive"), short)
        with self.assertRaises(persist.ArchiveInconsistent):
            persist.Runner.import_state(doc, os.path.join(os.path.dirname(lg), "x.db"), accept_code_change=True, archive_path=short)
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(lg), "x.db")))

    def test_a_backup_set_whose_archive_was_copied_while_the_league_ran_on_restores(self):
        b = tempfile.mkdtemp(dir=self.tmp)
        shutil.copy(os.path.join(self.at[EARLY], "league.db"), os.path.join(b, "league.db"))
        shutil.copy(os.path.join(self.at[MID], "league.db.archive"), os.path.join(b, "archive.db"))     # copied second: further ahead
        r = persist.restore_set(b, os.path.join(b, "restored"))
        self.assertEqual(r.seq, EARLY)
        self.finish(r)

    def test_a_runner_opened_without_its_archive_cannot_commit_events_that_make_history(self):
        lg, ar = self.pair(EARLY)
        r = persist.Runner.open(lg, with_archive=False)
        head = r.seq
        with self.assertRaises(persist.PersistError):
            r.run_events(MID)
        self.assertLess(r.seq - head, MID)
        self.assertEqual(journal(lg)[-1][0], r.seq)


if __name__ == "__main__":
    unittest.main()
