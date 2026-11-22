"""The optional upgrade policy (docs/SACRAMENTO_MANAGEMENT_POLICY_2026-11-22.md).

Disposable leagues only: every seed is in the block reserved for tests (7771400 to 7771499 here; the comparison uses 7770101 to 7770132).
None may ever be used for a league someone watches. Nothing here prints or records a game outcome.
"""
import copy
import inspect
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from fractions import Fraction as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl import neutral, persist as P  # noqa: E402
from dfl.ledger import rules as R  # noqa: E402
from dfl.ledger import world as W  # noqa: E402
from dfl.ledger.ledger import Rejected  # noqa: E402
from dfl.year import Universe  # noqa: E402

TABLES = os.path.join(ROOT, "data", "season1")
SEED = 7771401
SAC, OTHER = "Sacramento", "Dallas"
# the digest of a default league after 40 events, produced by the code of commit 4a1f26e (before the cap feature and this one)
BASELINE_DIGEST_40 = "b0a0780b7e0e386219560498be97d2a953528938bccb9318955fe32d7cff764f"


def fast(seed=SEED, **kw):
    return Universe(TABLES, seed=seed, engine=False, spatial=False, **kw)


def to_camp(u):
    """Fire events until camp has opened (the final cuts, and with them the upgrade routines, are the next event)."""
    for _ in range(200):
        if u.step_event() == "camp_open":
            return u
    raise AssertionError("no camp")


class TestDefault(unittest.TestCase):
    def test_no_option_leaves_no_trace(self):
        u = fast()
        self.assertNotIn("upgrade_policy", u.world.__dict__)
        self.assertIsNone(u.world.upgrade_policy)
        self.assertNotIn("upgrade_policy", repr(neutral.digest_parts(u)))

    def test_default_league_is_unchanged_from_the_baseline_commit(self):
        u = Universe(TABLES, seed=7771101, engine=True, spatial=False)
        for _ in range(40):
            u.step_event()
        self.assertEqual(neutral.deep_digest(u), BASELINE_DIGEST_40)

    def test_naming_and_clearing_the_option_is_the_default_state(self):
        u = fast()
        d0 = neutral.deep_digest(u)
        u.configure_upgrade_policy(["SAC"])
        self.assertNotEqual(neutral.deep_digest(u), d0)
        u.configure_upgrade_policy([])
        self.assertEqual(neutral.deep_digest(u), d0)

    def test_without_the_option_the_policy_never_runs(self):
        u = fast()
        calls = []
        orig = u.world.upgrade_more
        u.world.upgrade_more = lambda cid: (calls.append(cid), orig(cid))
        to_camp(u)
        u.step_event()                         # final_cuts
        self.assertEqual(calls, [])
        self.assertNotIn("upgrades_more", u.world.stats)

    def test_a_default_league_plays_the_same_with_the_code_present(self):
        """The ordinary camp routine is untouched: a year with the option absent and a year with it cleared are the same league."""
        a, b = fast(seed=SEED + 1), fast(seed=SEED + 1)
        b.configure_upgrade_policy(["SAC"])
        b.configure_upgrade_policy([])
        to_camp(a)
        to_camp(b)
        a.step_event()
        b.step_event()
        self.assertEqual(neutral.deep_digest(a), neutral.deep_digest(b))


class TestSetting(unittest.TestCase):
    def test_only_the_named_club_is_covered_and_it_is_found_by_abbreviation(self):
        u = fast(upgrade_policy=["SAC"])
        self.assertEqual(u.world.upgrade_policy, (SAC,))
        for bad in ("Sacramento", "Sacramento Sting", "sac", "SACR", "Sting", "33", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(Rejected) as cm:
                    fast().configure_upgrade_policy([bad])
                self.assertIn("unknown club abbreviation", str(cm.exception))

    def test_a_refused_setting_changes_nothing(self):
        u = fast(upgrade_policy=["SAC"])
        d0 = neutral.deep_digest(u)
        with self.assertRaises(Rejected):
            u.configure_upgrade_policy(["SAC", "NOPE"])
        self.assertEqual(neutral.deep_digest(u), d0)

    def test_in_season_changes_are_refused_by_the_universe_and_by_the_world(self):
        u = fast(upgrade_policy=["SAC"])
        u.step_event()
        with self.assertRaises(Rejected) as cm:
            u.configure_upgrade_policy([])
        self.assertIn("fixed for the season", str(cm.exception))
        self.assertEqual(u.world.upgrade_policy, (SAC,))
        w = fast().world
        w.L.phase = "regular"
        with self.assertRaises(Rejected) as cm:
            w.set_upgrade_policy([SAC])
        self.assertIn("fixed for the season", str(cm.exception))

    def test_it_is_independent_of_the_cap_multiplier(self):
        u = fast(upgrade_policy=["SAC"], cap_multipliers={"SAC": "1.15"})
        self.assertEqual(u.world.L.cap_of(SAC), F(1150))
        self.assertEqual(u.world.upgrade_policy, (SAC,))
        v = fast(upgrade_policy=["SAC"])
        self.assertEqual(v.world.L.cap_of(SAC), F(1000))
        self.assertIsNone(v.world.L.cap_multipliers)


class TestThePolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # a league at camp with the policy on, shared (copied) by the tests that only read or probe it
        cls.camp = to_camp(fast(seed=SEED + 2, upgrade_policy=["SAC"], cap_multipliers={"SAC": "1.15"}))

    def probe(self, weaken=None):
        """A copy of the camp league, the ordinary upgrade routine run (as in `camp`). `weaken` names a position at which the club's weakest starter
        (in fact every player the club has there) is made clearly poor, so that a worthwhile upgrade certainly exists (a fixture: management never sees this edit, only the views it produces)."""
        u = copy.deepcopy(self.camp)
        w = u.world
        w.upgrade(SAC)
        if weaken:
            for i in w.L.clubs[SAC].roster:
                if w.L.players[i].pos == weaken:
                    w.rating[i] -= 30
            w._views.clear()
        return u, w

    def test_it_signs_only_worthwhile_affordable_upgrades(self):
        """Every signing beats the weakest starter at a filled position by the margin and by the club's own uncertainty, fits the cap, and keeps
        the roster inside its limits. A probe records the numbers at the moment of each signing."""
        u, w = self.probe(weaken="QB")
        L, N = w.L, w.tables.negotiation
        seen = []
        orig = w.try_sign

        def watch(cid, p, slot="roster", rookie_c=None, value=None):
            mine = sorted((w.value(L.players[i], cid), -i) for i in L.clubs[cid].roster if L.players[i].pos == p.pos and L.players[i].out_weeks == 0)
            n = W.STARTERS[p.pos]
            weakest = L.players[-mine[::-1][n - 1][1]]
            gap = w.value(p, cid) - mine[::-1][n - 1][0]
            import math
            sd = math.hypot(w.view_sd(cid, p), w.view_sd(cid, weakest))
            before = L.cap_number(cid)
            ok = orig(cid, p, slot, rookie_c, value)
            seen.append((gap, sd, N["upgrade_margin"], before, value, ok, L.cap_number(cid), L.cap_of(cid), len(L.clubs[cid].roster)))
            return ok
        w.try_sign = watch
        w.upgrade_more(SAC)
        self.assertGreater(len(seen), 0, "the probe league is chosen so that at least one upgrade exists")
        for gap, sd, margin, before, v, ok, after, cap, n in seen:
            self.assertGreaterEqual(gap, margin)
            self.assertGreaterEqual(gap, sd)
            self.assertLessEqual(before + v, cap - R.RESERVE)
            self.assertLessEqual(n, R.PRESEASON_CEILING)
        self.assertEqual(w.stats["upgrades_more"], sum(1 for s in seen if s[5]))

    def test_nothing_qualifying_means_nothing_is_done(self):
        u, w = self.probe(weaken="QB")
        w.fas = lambda pos=None, cid=None: []
        before = (w.L.digest(), dict(w.stats))
        w.upgrade_more(SAC)
        self.assertEqual((w.L.digest(), dict(w.stats)), before)

    def test_the_clubs_own_uncertainty_is_used(self):
        """If the club's own uncertainty about the comparison exceeds the gap, nothing is signed; at the real uncertainty (a few points) a gap of
        thirty is signed. (The uncertainty is patched where the policy combines the two estimates' errors, after the views are drawn.)"""
        from unittest import mock
        u, w = self.probe(weaken="QB")
        with mock.patch("dfl.ledger.world.math.hypot", return_value=1000.0):
            w.upgrade_more(SAC)
        self.assertEqual(w.stats.get("upgrades_more", 0), 0)
        u1, w1 = self.probe(weaken="QB")
        w1.upgrade_more(SAC)
        self.assertGreater(w1.stats.get("upgrades_more", 0), 0)
        u2, w2 = self.probe(weaken="QB")
        with mock.patch("dfl.ledger.world.math.hypot", return_value=0.0):
            w2.upgrade_more(SAC)
        self.assertGreaterEqual(w2.stats.get("upgrades_more", 0), w1.stats.get("upgrades_more", 0))

    def test_the_cap_is_respected_before_committing(self):
        u, w = self.probe(weaken="QB")
        L = w.L
        L.cap_of = lambda cid: L.cap_number(cid) + R.RESERVE + 1        # no room: the cheapest contract is the league minimum, 5
        before = {c: len(L.clubs[c].roster) for c in L.clubs}
        w.upgrade_more(SAC)
        self.assertEqual(w.stats.get("upgrades_more", 0), 0)
        self.assertEqual({c: len(L.clubs[c].roster) for c in L.clubs}, before)

    def test_it_stops_at_the_roster_limit(self):
        u, w = self.probe(weaken="QB")
        w.L.room = lambda cid, slot="roster": False
        w.upgrade_more(SAC)
        self.assertEqual(w.stats.get("upgrades_more", 0), 0)

    def test_only_the_named_club_is_touched(self):
        u = copy.deepcopy(self.camp)
        calls = []
        orig = u.world.upgrade_more
        u.world.upgrade_more = lambda cid: (calls.append(cid), orig(cid))
        u.step_event()                                  # final_cuts
        self.assertEqual(calls, [SAC])

    def test_the_cuts_that_follow_leave_every_club_legal_and_under_its_own_cap(self):
        u = copy.deepcopy(self.camp)
        u.step_event()                                  # final_cuts
        L = u.world.L
        for cid, c in L.clubs.items():
            self.assertLessEqual(len(c.roster), R.ROSTER_MAX, cid)
            self.assertLessEqual(len(c.ps), R.PS_MAX, cid)
            self.assertLessEqual(L.cap_number(cid), L.cap_of(cid), cid)
        self.assertEqual(L.overage(SAC), 0)
        u.step_event()                                  # the next events keep going; the season-start audit runs in start_season
        u.world.audit(strict_current=True)

    def test_the_policy_reads_only_what_management_sees(self):
        """No access to hidden ratings, development, future games or outcomes: the routine is built from views, values, quotes and the ledger."""
        src = inspect.getsource(W.World.upgrade_more)
        for hidden in ("self.rating", "self.cards", ".ovr", "true_overall", "dev_rng", "develop", "resolver", "engine", "career", "production",
                       "standings", "results", "form[", "random", "rng"):
            self.assertNotIn(hidden, src, hidden)
        # and nothing under engine/ knows the policy
        for root, _, files in os.walk(os.path.join(ROOT, "src", "dfl", "engine")):
            for f in files:
                if f.endswith(".py"):
                    with open(os.path.join(root, f)) as fh:
                        self.assertNotIn("upgrade_policy", fh.read(), f)

    def test_view_sd_is_the_error_the_view_is_drawn_with(self):
        w = self.camp.world
        p = next(iter(w.fas(None, SAC)))
        a = w.view_sd(SAC, p)
        self.assertGreater(a, 0)
        own = next(w.L.players[i] for i in w.L.clubs[SAC].roster)
        self.assertLess(w.view_sd(SAC, own), w.view_sd(OTHER, own))        # a club knows its own player better than another club does


class Files(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="upgpol")
        cls.path = os.path.join(cls.tmp, "created.db")
        r = P.Runner.create(cls.path, TABLES, seed=SEED + 10, archive=False, engine=False, spatial=False, upgrade_policy=["SAC"])
        r.run_events(3)
        r.close()
        cls.step = os.path.join(cls.tmp, "step.db")
        r = P.Runner.create(cls.step, TABLES, seed=SEED + 20, archive=False, engine=False, spatial=False, snapshot_every=1)
        r.run_year()
        cls.after_year = r.seq
        cls.rec = r.configure_upgrade_policy(["SAC"])
        r.run_events(3)
        r.close()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_creation_setting_is_recorded_and_survives_save_load(self):
        st = P.status(self.path)
        self.assertEqual(st["upgrade_policy"], ["SAC"])
        r = P.Runner.open(self.path)
        self.assertEqual(r.u.world.upgrade_policy, (SAC,))
        r.close()
        self.assertEqual(P.verify(self.path, deep=True), [])

    def test_backup_and_restore_keep_the_creation_setting(self):
        bk, dest = os.path.join(self.tmp, "b.db"), os.path.join(self.tmp, "r.db")
        P.backup(self.path, bk)
        r = P.restore(bk, dest, archive_path=None)
        self.assertEqual(r.u.world.upgrade_policy, (SAC,))
        r.close()

    def test_a_default_file_has_no_setting_recorded(self):
        p = os.path.join(self.tmp, "plain.db")
        r = P.Runner.create(p, TABLES, seed=SEED + 11, archive=False, engine=False, spatial=False)
        r.run_events(2)
        r.close()
        st = P.status(p)
        self.assertNotIn("upgrade_policy", st)
        self.assertNotIn("mgmt_config", st)
        r = P.Runner.open(p)
        self.assertNotIn("mgmt_config", r.meta)
        self.assertNotIn("upgrade_policy", r.meta["universe_args"])
        r.close()

    def test_the_step_is_recorded_takes_effect_and_survives_reopen(self):
        self.assertEqual(self.rec["after_seq"], self.after_year)
        self.assertEqual(self.rec["upgrade_policy"], ["SAC"])
        st = P.status(self.step)
        self.assertEqual(st["mgmt_config"], [self.rec])
        r = P.Runner.open(self.step)
        self.assertEqual(r.u.world.upgrade_policy, (SAC,))
        self.assertEqual(self.rec["effective_year"], r.u.world.L.year)
        r.close()
        self.assertEqual(P.verify(self.step, deep=True), [])

    def test_mid_year_change_through_the_runner_is_refused_and_changes_nothing(self):
        p = os.path.join(self.tmp, "mid.db")
        r = P.Runner.create(p, TABLES, seed=SEED + 14, archive=False, engine=False, spatial=False, upgrade_policy=["SAC"])
        r.run_events(2)
        with self.assertRaises(Rejected):
            r.configure_upgrade_policy([])
        self.assertEqual(r.u.world.upgrade_policy, (SAC,))
        self.assertNotIn("mgmt_config", r.meta)
        r.close()
        self.assertNotIn("mgmt_config", P.status(p))

    def _recover(self, name, keep_through):
        copy_path = os.path.join(self.tmp, name)
        P.backup(self.step, copy_path)
        db = sqlite3.connect(copy_path)
        db.execute("DELETE FROM snapshots WHERE seq > ?", (keep_through,))
        left = [r[0] for r in db.execute("SELECT seq FROM snapshots")]
        db.commit()
        db.close()
        self.assertTrue(left and max(left) <= keep_through, left)
        r = P.Runner.open(copy_path, heal=False)
        try:
            self.assertEqual(r.u.world.upgrade_policy, (SAC,))
            self.assertEqual(r.seq, self.after_year + 3)
        finally:
            r.close()

    def test_recovery_from_a_snapshot_at_the_step_and_from_an_older_one(self):
        self._recover("at_step.db", self.after_year)
        self._recover("before_step.db", self.after_year - 1)

    def test_export_and_import_carry_the_step(self):
        doc = os.path.join(self.tmp, "export.json")
        P.export_state(self.step, doc)
        dest = os.path.join(self.tmp, "imported.db")
        r = P.Runner.import_state(doc, dest, tables=TABLES)
        self.assertEqual(r.u.world.upgrade_policy, (SAC,))
        self.assertEqual(r.meta["mgmt_config"], [self.rec])
        r.close()


class TestCommandLine(unittest.TestCase):
    def run_cli(self, *args):
        import json
        import subprocess
        r = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "tools", "league_store.py")] + list(args), capture_output=True, text=True,
                           env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"), timeout=300)
        return r.returncode, json.loads(r.stdout.strip().splitlines()[-1])

    def test_option_at_creation_and_the_offseason_step(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "l.db")
            rc, o = self.run_cli("init", f, "--seed", "424246", "--placeholder", "--no-archive", "--upgrade-policy", "SAC")
            self.assertEqual((rc, o["created"]), (0, f))
            rc, o = self.run_cli("status", f)
            self.assertEqual(o["upgrade_policy"], ["SAC"])
            rc, o = self.run_cli("configure-policy", f)                # clear: between years
            self.assertEqual((rc, o["upgrade_policy"]), (0, []))
            self.run_cli("run", f, "--events", "2")
            rc, o = self.run_cli("configure-policy", f, "SAC")
            self.assertEqual((rc, o["error"]), (1, "Rejected"))
            self.assertIn("fixed for the season", o["message"])

    def test_an_unknown_club_is_refused_and_leaves_no_file(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "l.db")
            rc, o = self.run_cli("init", f, "--seed", "424247", "--placeholder", "--no-archive", "--upgrade-policy", "Sacramento")
            self.assertEqual(rc, 1)
            self.assertIn("unknown club abbreviation", o["message"])
            self.assertFalse(os.path.exists(f) and os.path.getsize(f))


if __name__ == "__main__":
    unittest.main()
