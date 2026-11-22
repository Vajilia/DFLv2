"""The per-club salary-cap multiplier (docs/SACRAMENTO_CAP_ADVANTAGE_2026-11-22.md).

Disposable leagues only: every seed here is in the block reserved for tests (7771000 to 7771999; the paired comparison uses 7770001 to 7770032).
None of these seeds may ever be used for a league someone watches. Nothing here prints or records a game outcome.
"""
import copy
import os
import random
import re
import shutil
import sqlite3
import sys
import tempfile
import unittest
from fractions import Fraction as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl import neutral, persist as P  # noqa: E402
from dfl.ledger import governance, rules as R  # noqa: E402
from dfl.ledger.ledger import Rejected, multiplier_text, parse_multiplier  # noqa: E402
from dfl.year import Universe  # noqa: E402

TABLES = os.path.join(ROOT, "data", "season1")
SEED = 7771101
SAC, OTHER = "Sacramento", "Dallas"

# The digest of a default league (no option) after 40 events, produced by the code of commit 4a1f26e (before this feature) on the same seed
# and engine settings. The option must leave every existing league exactly as it was.
BASELINE_DIGEST_40 = "b0a0780b7e0e386219560498be97d2a953528938bccb9318955fe32d7cff764f"
BASELINE_COMMIT = "4a1f26e"


def fast(seed=SEED, **kw):
    return Universe(TABLES, seed=seed, engine=False, spatial=False, **kw)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = fast()

    def league(self, **kw):
        return copy.deepcopy(self.base) if not kw else fast(**kw)


class TestParse(unittest.TestCase):
    def test_exact_values(self):
        self.assertEqual(parse_multiplier("1.15"), F(23, 20))
        self.assertEqual(parse_multiplier(1), F(1))
        self.assertEqual(parse_multiplier(F(3, 2)), F(3, 2))
        self.assertEqual(parse_multiplier("1.5000"), F(3, 2))

    def test_invalid_values_are_refused_with_a_reason(self):
        cases = [(1.15, "floats are inexact"), (True, "decimal string"), (None, "decimal string"), ([1], "decimal string"),
                 ("abc", "plain decimal"), ("1,15", "plain decimal"), ("nan", "plain decimal"), ("1e0", "plain decimal"), ("-1", "plain decimal"),
                 ("", "plain decimal"), ("1.23456", "plain decimal"), ("0.99", "between 1 and 1.5"), ("0", "between 1 and 1.5"),
                 ("1.51", "between 1 and 1.5"), (2, "between 1 and 1.5")]
        for v, why in cases:
            with self.subTest(v=v):
                with self.assertRaises(Rejected) as cm:
                    parse_multiplier(v, "SAC")
                self.assertIn(why, str(cm.exception))
                self.assertIn("SAC", str(cm.exception))

    def test_canonical_text(self):
        self.assertEqual([multiplier_text(x) for x in ("1", "1.0", "1.15", "1.1500", 1, F(3, 2), "1.05")], ["1", "1", "1.15", "1.15", "1", "1.5", "1.05"])


class TestDefault(Base):
    def test_no_option_leaves_no_trace_in_the_state(self):
        L = self.base.world.L
        self.assertIsNone(L.cap_multipliers)
        self.assertNotIn("cap_multipliers", vars(L))
        for cid in L.clubs:
            self.assertEqual(L.cap_of(cid), L.cap)
        self.assertEqual(L.limit(L.year), L.limit(L.year, SAC))

    def test_setting_nothing_or_only_ones_is_the_default_state(self):
        a = self.league()
        a.configure_cap_multipliers({"SAC": "1.00"})
        b = self.league()
        self.assertNotIn("cap_multipliers", vars(a.world.L))
        self.assertEqual(neutral.deep_digest(a), neutral.deep_digest(b))

    def test_default_league_is_unchanged_from_the_baseline_commit(self):
        """Forty events of a default league (engine on) give the digest the code before this feature gave (recorded from commit 4a1f26e)."""
        u = Universe(TABLES, seed=SEED, engine=True, spatial=False)
        for _ in range(40):
            u.step_event()
        self.assertEqual(neutral.deep_digest(u), BASELINE_DIGEST_40)


class TestConfiguredClub(Base):
    def test_only_the_configured_club_has_a_different_cap(self):
        u = self.league(cap_multipliers={"SAC": "1.15"})
        L = u.world.L
        self.assertEqual(L.cap_of(SAC), F(1150))
        self.assertEqual(L.cap, F(1000))
        self.assertEqual({c for c in L.clubs if L.cap_of(c) != L.cap}, {SAC})
        self.assertEqual(dict(L.cap_multipliers), {SAC: F(23, 20)})

    def test_the_club_is_found_by_its_abbreviation_never_by_a_name_or_substring(self):
        for bad in ("Sacramento", "Sacramento Sting", "sac", "SACR", "Sting", "33", "S", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(Rejected) as cm:
                    self.league().configure_cap_multipliers({bad: "1.15"})
                self.assertIn("unknown club abbreviation", str(cm.exception))
        u = self.league()
        u.configure_cap_multipliers({"DAL": "1.10"})
        self.assertEqual({c for c in u.world.L.clubs if u.world.L.cap_of(c) != u.world.L.cap}, {OTHER})

    def test_a_ledger_call_with_an_unknown_club_id_is_refused(self):
        with self.assertRaises(Rejected) as cm:
            self.league().world.L.set_cap_multipliers({"Sacramento Sting": "1.15"})
        self.assertIn("unknown club", str(cm.exception))

    def test_a_refused_setting_changes_nothing(self):
        u = self.league(cap_multipliers={"SAC": "1.15"})
        before = neutral.deep_digest(u)
        for bad in ({"SAC": "1.6"}, {"SAC": 1.2}, {"XXX": "1.1"}, {"SAC": "1.1", "DAL": "oops"}):
            with self.subTest(bad=bad), self.assertRaises(Rejected):
                u.configure_cap_multipliers(bad)
            self.assertEqual(neutral.deep_digest(u), before)
        v = self.league()
        before = neutral.deep_digest(v)
        with self.assertRaises(Rejected):
            v.configure_cap_multipliers({"SAC": "9"})
        self.assertNotIn("cap_multipliers", vars(v.world.L))
        self.assertEqual(neutral.deep_digest(v), before)

    def test_reset_to_one_is_the_same_state_as_never_having_it(self):
        u = self.league(cap_multipliers={"SAC": "1.15"})
        u.configure_cap_multipliers({})
        self.assertNotIn("cap_multipliers", vars(u.world.L))
        self.assertEqual(neutral.deep_digest(u), neutral.deep_digest(self.league()))

    def test_a_decrease_that_would_strand_contracts_is_refused_by_name(self):
        u = self.league(cap_multipliers={"SAC": "1.15"})
        L = u.world.L
        real = L.cap_number
        L.cap_number = lambda cid, year=None: F(1100) if cid == SAC else real(cid, year)      # the club is carrying 1100: legal under 1150, not under 1000
        with self.assertRaises(Rejected) as cm:
            u.configure_cap_multipliers({})
        msg = str(cm.exception)
        self.assertIn(SAC, msg)
        self.assertIn("over its new cap", msg)
        self.assertEqual(dict(L.cap_multipliers), {SAC: F(23, 20)})
        u.configure_cap_multipliers({"SAC": "1.10"})                   # a smaller advantage that still covers it is fine
        self.assertEqual(L.cap_of(SAC), F(1100))
        with self.assertRaises(Rejected):
            u.configure_cap_multipliers({"SAC": "1.05"})


class TestSameLimitEverywhere(Base):
    """Every place the cap is used reads the same per-club limit."""

    def setUp(self):
        self.u = self.league(cap_multipliers={"SAC": "1.15"})
        self.L = self.u.world.L

    def pay(self, table):
        real = self.L.cap_number
        self.L.cap_number = lambda cid, year=None: table.get(cid, real(cid, year))

    def test_overage(self):
        self.pay({SAC: F(1100), OTHER: F(1100)})
        self.assertEqual(self.L.overage(SAC), 0)
        self.assertEqual(self.L.overage(OTHER), 100)
        self.pay({SAC: F(1200)})
        self.assertEqual(self.L.overage(SAC), 50)

    def test_the_transaction_check(self):
        cell = {SAC: F(900), OTHER: F(900)}
        real = self.L.cap_number
        self.L.cap_number = lambda cid, year=None: cell.get(cid, real(cid, year))

        def raise_pay():
            cell[SAC] = cell[OTHER] = F(1100)
        for cid, ok in ((SAC, True), (OTHER, False)):
            cell[SAC] = cell[OTHER] = F(900)
            with self.subTest(cid=cid):
                if ok:
                    self.L._txn([cid], [], raise_pay, "test")
                else:
                    with self.assertRaises(Rejected) as cm:
                        self.L._txn([cid], [], raise_pay, "test")
                    self.assertIn("breaks the cap", str(cm.exception))

    def test_viability(self):
        y = self.L.year
        self.assertEqual(self.L.limit(y, SAC) - self.L.limit(y, OTHER), 150)
        self.assertEqual(self.L.limit(y), self.L.limit(y, OTHER))                     # no club named: the league's own cap, as before
        plain = self.league().world.L                                                 # the same league without the option
        self.assertEqual(self.L.viability_margin(SAC) - plain.viability_margin(SAC), 150)
        self.assertEqual(self.L.viability_margin(OTHER), plain.viability_margin(OTHER))

    def test_the_world_audit(self):
        """The audit that every season's start and end runs holds a club to its own limit in later years."""
        def patch(L):
            real = L.committed
            L.committed = lambda cid: {L.year + 1: F(800)} if cid == SAC else real(cid)       # over the base limit (735), under Sacramento's (885)
        patch(self.L)
        self.u.world.audit()
        plain = self.league()
        patch(plain.world.L)
        with self.assertRaises(AssertionError):
            plain.world.audit()

    def test_governance_judges_spending_against_the_clubs_own_cap(self):
        plain = self.league()
        self.pay({SAC: F(1150) * F(87, 100)})
        plain_L = plain.world.L
        real = plain_L.cap_number
        plain_L.cap_number = lambda cid, year=None: F(1000) * F(87, 100) if cid == SAC else real(cid, year)
        a = governance.signals(self.u.world, SAC, 0.5)
        b = governance.signals(plain.world, SAC, 0.5)
        self.assertEqual(a[1], b[1])

    def test_the_public_roster_feed_shows_the_clubs_own_limit(self):
        from dflsite import feed
        rows = {t["team_city"]: t for t in self.u.tpl.teams}
        for cid, want in ((SAC, 1150.0), (OTHER, 1000.0)):
            with self.subTest(cid=cid):
                r = feed.roster_file(self.u, cid, {c: t["abbr"] for c, t in rows.items()}, rows[cid], self.L.year)
                self.assertEqual(r["cap"]["limit"], want)

    def test_no_place_still_reads_the_single_league_cap(self):
        """A source audit: the only reads of the league-wide scalar are the ones listed (the definition, the legacy adapter, the profiles)."""
        allowed = {("src/dfl/ledger/ledger.py", "self.cap = R.CAP if cap is None else cap"),
                   ("src/dfl/neutral.py", "L.cap = s[\"cap\"]")}
        pat = re.compile(r"(?<![A-Za-z_.])(?:L|self|ledger|world\.L|self\.L|self\.world\.L|\w+\.L)\.cap\b(?!_)")
        found = []
        for base in ("src/dfl", "src/dflsite"):
            for dp, _, fns in os.walk(os.path.join(ROOT, base)):
                for fn in fns:
                    if not fn.endswith(".py"):
                        continue
                    path = os.path.join(dp, fn)
                    rel = os.path.relpath(path, ROOT)
                    with open(path, encoding="utf-8") as fh:
                        lines = fh.read().splitlines()
                    for line in lines:
                        code = line.split("#")[0]
                        if pat.search(code) and (rel, code.strip()) not in allowed:
                            found.append((rel, code.strip()))
        base_cap_reads = [f for f in found if "cap_of" not in f[1]]
        # ledger.py reads self.cap inside cap_of, limit (no club named) and set_cap_multipliers; nothing else may.
        self.assertEqual(sorted(set(r for r, _ in base_cap_reads)), ["src/dfl/ledger/ledger.py"], base_cap_reads)
        self.assertLessEqual(len(base_cap_reads), 5, base_cap_reads)

    def test_the_engine_never_reads_the_cap(self):
        """Nothing that resolves a game, an injury or a draft order refers to salary or the cap."""
        pat = re.compile(r"\bcap_hit\b|\bcap_of\b|\bcap_number\b|\.cap\b|cap_multipliers|\bsalary\b|\bcontract\b", re.I)
        offenders = []
        for sub in ("src/dfl/engine",):
            for dp, _, fns in os.walk(os.path.join(ROOT, sub)):
                for fn in fns:
                    if fn.endswith(".py"):
                        p = os.path.join(dp, fn)
                        with open(p, encoding="utf-8") as fh:
                            lines = fh.read().splitlines()
                        for i, line in enumerate(lines, 1):
                            if pat.search(line.split("#")[0]):
                                offenders.append((os.path.relpath(p, ROOT), i, line.strip()[:80]))
        # ledger_engine.py is the bridge that hands rosters to the engine; it is allowed to mention contracts only for the squad it builds
        self.assertEqual([o for o in offenders if "cap" in o[2].lower() or "salary" in o[2].lower()], [], offenders)


class TestNoDirectOutcomeBonus(unittest.TestCase):
    def test_same_rosters_and_generator_give_the_same_game(self):
        """The option gives no direct bonus: two leagues that differ only in the multiplier (same creation, same rosters) resolve the same
        game, from the same generator state, identically."""
        a = Universe(TABLES, seed=SEED + 1, engine=True, spatial=False)
        b = Universe(TABLES, seed=SEED + 1, engine=True, spatial=False, cap_multipliers={"SAC": "1.5"})
        self.assertEqual(neutral.deep_digest(a, neutral.DIGEST_IGNORE + ("cap_multipliers",)), neutral.deep_digest(b, neutral.DIGEST_IGNORE + ("cap_multipliers",)))
        out = []
        for u in (a, b):
            u.resolver.archive = []
            u.resolver.start_season(u.clubs, 1, random.Random(3))
            score = u.resolver.resolve({"id": "t1", "home": SAC, "away": OTHER}, random.Random(99), False)
            rec = u.resolver.archive[0]
            out.append((score, rec["score"], rec["stats"], rec["players"], rec["table"]))
        self.assertEqual(out[0], out[1])


class TestManagementUsesTheRoom(unittest.TestCase):
    def test_free_agency_fills_room_above_the_base_cap(self):
        """On a disposable league where free agency is not stopped by the roster ceiling, management's spending target moves with the club's cap:
        the club ends the period above what a base-cap club may ever carry, and never above its own limit. (On other seeds the roster ceiling or
        the lack of a needy position ends free agency first and the room goes unused; that is a property of management, not of the option.)"""
        arms = {}
        for name, kw in (("plain", {}), ("room", {"cap_multipliers": {"SAC": "1.15"}})):
            u = Universe(TABLES, seed=7771002, engine=False, spatial=False, **kw)
            u.step_event()
            while True:
                key = u.step_event()
                if key == "camp_open":
                    break
            L = u.world.L
            arms[name] = (L.cap_number(SAC), L.cap_of(SAC), max(L.overage(c) for c in L.clubs))
        self.assertLessEqual(arms["plain"][0], F(1000) - R.RESERVE)
        self.assertGreater(arms["room"][0], arms["plain"][0])
        self.assertGreater(arms["room"][0], F(1000) - R.RESERVE)                        # beyond where a base-cap club may ever sign
        self.assertLessEqual(arms["room"][0], arms["room"][1])


class Files(unittest.TestCase):
    """Save, load, backup, restore and recovery of a league file with the option."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="capmult")
        cls.path = os.path.join(cls.tmp, "created.db")
        r = P.Runner.create(cls.path, TABLES, seed=SEED + 10, archive=False, engine=False, spatial=False, cap_multipliers={"SAC": F(23, 20)})
        r.run_events(3)
        r.close()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_creation_setting_is_recorded_and_survives_save_load(self):
        st = P.status(self.path)
        self.assertEqual(st["cap_multipliers"], {"SAC": "1.15"})
        r = P.Runner.open(self.path)
        self.assertEqual(r.u.world.L.cap_of(SAC), F(1150))
        self.assertEqual(r.meta["universe_args"]["cap_multipliers"], {"SAC": "1.15"})
        r.close()
        self.assertEqual(P.verify(self.path, deep=True), [])

    def test_backup_and_restore_keep_the_setting(self):
        bk, dest = os.path.join(self.tmp, "b.db"), os.path.join(self.tmp, "restored.db")
        P.backup(self.path, bk)
        r = P.restore(bk, dest, archive_path=None)
        self.assertEqual(r.u.world.L.cap_of(SAC), F(1150))
        self.assertEqual(r.meta["universe_args"]["cap_multipliers"], {"SAC": "1.15"})
        r.step()
        r.close()
        self.assertEqual(P.verify(dest, deep=True), [])

    def test_a_default_file_has_no_setting_recorded(self):
        p = os.path.join(self.tmp, "plain.db")
        r = P.Runner.create(p, TABLES, seed=SEED + 11, archive=False, engine=False, spatial=False)
        self.assertNotIn("cap_multipliers", P.status(p))
        self.assertNotIn("cap_config", P.status(p))
        self.assertNotIn("cap_config", neutral.runner_manifest(r))
        r.close()

    def test_mid_year_change_through_the_runner_is_refused_and_changes_nothing(self):
        p = os.path.join(self.tmp, "midyear.db")
        r = P.Runner.create(p, TABLES, seed=SEED + 12, archive=False, engine=False, spatial=False)
        r.run_events(2)
        digest = neutral.deep_digest(r.u)
        with self.assertRaises(Rejected) as cm:
            r.configure_cap_multipliers({"SAC": "1.15"})
        self.assertIn("fixed for the season", str(cm.exception))
        self.assertEqual(neutral.deep_digest(r.u), digest)
        self.assertNotIn("cap_config", P.status(p))
        r.close()

    def test_a_ledger_in_season_phase_refuses_directly(self):
        u = fast(seed=SEED + 13)
        for phase in ("camp", "season", "regular"):
            u.world.L.phase = phase
            with self.subTest(phase=phase), self.assertRaises(Rejected) as cm:
                u.world.L.set_cap_multipliers({SAC: "1.15"})
            self.assertIn("fixed for the season", str(cm.exception))


class OffseasonStep(unittest.TestCase):
    """The durable offseason configuration step, after a year has closed. One league year is played once for the whole class."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="capstep")
        cls.path = os.path.join(cls.tmp, "year.db")
        r = P.Runner.create(cls.path, TABLES, seed=SEED + 20, archive=False, engine=False, spatial=False, snapshot_every=1)
        r.run_year()
        cls.after_year = r.seq
        cls.rec = r.configure_cap_multipliers({"SAC": "1.15"})
        r.run_events(3)
        r.close()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_step_is_recorded(self):
        self.assertEqual(self.rec["after_seq"], self.after_year)
        self.assertEqual(self.rec["multipliers"], {"SAC": "1.15"})
        r = P.Runner.open(self.path)
        self.assertEqual(self.rec["effective_year"], r.u.world.L.year)
        r.close()
        st = P.status(self.path)
        self.assertEqual(st["cap_config"], [self.rec])

    def test_it_takes_effect_when_the_next_year_opens_and_survives_reopen(self):
        r = P.Runner.open(self.path)
        L = r.u.world.L
        self.assertEqual(L.cap_of(SAC), F(1150))
        self.assertEqual(L.cap_of(OTHER), F(1000))
        r.close()
        self.assertEqual(P.verify(self.path, deep=True), [])

    def test_backup_and_restore_keep_the_step(self):
        bk, dest = os.path.join(self.tmp, "b.db"), os.path.join(self.tmp, "r.db")
        P.backup(self.path, bk)
        r = P.restore(bk, dest, archive_path=None)
        self.assertEqual(r.u.world.L.cap_of(SAC), F(1150))
        self.assertEqual(r.meta["cap_config"], [self.rec])
        r.close()

    def _recover(self, name, keep_through):
        """Keep only the snapshots at or before `keep_through` and open the file."""
        copy_path = os.path.join(self.tmp, name)
        P.backup(self.path, copy_path)
        db = sqlite3.connect(copy_path)
        db.execute("DELETE FROM snapshots WHERE seq > ?", (keep_through,))
        left = [r[0] for r in db.execute("SELECT seq FROM snapshots")]
        db.commit()
        db.close()
        self.assertTrue(left and max(left) <= keep_through, left)
        return copy_path

    def _open_recovered(self, copy_path):
        r = P.Runner.open(copy_path, heal=False)
        try:
            self.assertEqual(r.u.world.L.cap_of(SAC), F(1150))
            self.assertEqual(r.seq, self.after_year + 3)
        finally:
            r.close()

    def test_recovery_from_the_snapshot_taken_just_before_the_step(self):
        """The year-close snapshot predates the step (the step happens after the event). Opening from it applies the recorded step, and the
        journal's later digests (which include the multipliers) are reproduced."""
        self._open_recovered(self._recover("at_step.db", self.after_year))

    def test_recovery_from_a_snapshot_older_than_the_step_reproduces_the_journal(self):
        """An older snapshot replays to the step's event and applies it from the file's record there."""
        self._open_recovered(self._recover("before_step.db", self.after_year - 1))

    def test_a_second_step_replaces_the_first_and_is_recorded_after_it(self):
        p = os.path.join(self.tmp, "two.db")
        P.backup(self.path, p)
        r = P.Runner.open(p)
        # the league is now mid-year (three events in); a second step is refused until the year closes
        with self.assertRaises(Rejected):
            r.configure_cap_multipliers({"SAC": "1.20"})
        r.run_year()
        rec2 = r.configure_cap_multipliers({"SAC": "1.20"})
        self.assertGreater(rec2["after_seq"], self.rec["after_seq"])
        self.assertEqual([c["multipliers"] for c in r.meta["cap_config"]], [{"SAC": "1.15"}, {"SAC": "1.2"}])
        r.close()
        r = P.Runner.open(p)
        self.assertEqual(r.u.world.L.cap_of(SAC), F(1200))
        r.close()
        self.assertEqual(P.verify(p, deep=True), [])

    def test_export_and_import_carry_the_setting_and_its_record(self):
        doc, new = os.path.join(self.tmp, "doc.json"), os.path.join(self.tmp, "imported.db")
        P.export_state(self.path, doc)
        r = P.Runner.import_state(doc, new, tables=TABLES)
        self.assertEqual(r.u.world.L.cap_of(SAC), F(1150))
        self.assertEqual(r.meta["cap_config"], [self.rec])
        r.close()


class TestCommandLine(unittest.TestCase):
    """tools/league_store.py: the option at creation, the offseason step, and the guard on the seeds reserved for tests."""

    def run_cli(self, *args):
        import json
        import subprocess
        r = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "tools", "league_store.py")] + list(args), capture_output=True, text=True,
                           env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"), timeout=300)
        return r.returncode, json.loads(r.stdout.strip().splitlines()[-1])

    def test_option_at_creation_and_status(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "l.db")
            rc, o = self.run_cli("init", f, "--seed", "424243", "--placeholder", "--no-archive", "--cap-multiplier", "SAC=1.15")
            self.assertEqual((rc, o["created"]), (0, f))
            rc, o = self.run_cli("status", f)
            self.assertEqual(o["cap_multipliers"], {"SAC": "1.15"})

    def test_refusals_are_clear_and_leave_no_file(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "l.db")
            for args, text in ((["--cap-multiplier", "SAC=1.6"], "between 1 and 1.5"), (["--cap-multiplier", "Sacramento=1.15"], "unknown club abbreviation"),
                               (["--cap-multiplier", "SAC"], "ABBR=VALUE")):
                with self.subTest(args=args):
                    rc, o = self.run_cli("init", f, "--seed", "424244", "--placeholder", "--no-archive", *args)
                    self.assertEqual(rc, 1)
                    self.assertIn(text, o["message"])
                    self.assertFalse(os.path.exists(f) and os.path.getsize(f))

    def test_reserved_test_seeds_are_refused_unless_it_is_a_test_league(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "l.db")
            for seed in ("7770000", "7770001", "7771999"):
                rc, o = self.run_cli("init", f, "--seed", seed, "--placeholder", "--no-archive")
                self.assertEqual((rc, o["error"]), (1, "ReservedSeed"))
            rc, o = self.run_cli("init", f, "--seed", "7772000", "--placeholder", "--no-archive")
            self.assertEqual(rc, 0)
        with tempfile.TemporaryDirectory() as d:
            rc, o = self.run_cli("init", os.path.join(d, "t.db"), "--seed", "7771999", "--placeholder", "--no-archive", "--test-league")
            self.assertEqual(rc, 0)

    def test_the_offseason_step_is_refused_mid_year(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "l.db")
            self.run_cli("init", f, "--seed", "424245", "--placeholder", "--no-archive")
            rc, o = self.run_cli("configure-cap", f, "SAC=1.2")
            self.assertEqual(rc, 0)
            self.assertEqual(o["multipliers"], {"SAC": "1.2"})
            self.run_cli("run", f, "--events", "2")
            rc, o = self.run_cli("configure-cap", f, "SAC=1.3")
            self.assertEqual((rc, o["error"]), (1, "Rejected"))
            self.assertIn("fixed for the season", o["message"])


if __name__ == "__main__":
    unittest.main()
