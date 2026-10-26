"""Fans, recall, elections and the front office (Edition 8.27)."""
import copy
import os
import statistics as st
import sys
import unittest
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from dfl.characters import Creator, load_tables  # noqa: E402
from dfl.ledger import governance  # noqa: E402
from dfl.ledger.world import World  # noqa: E402

CLUBS = [f"C{i:02d}" for i in range(1, 13)]


def read(path):
    with open(path) as fh:
        return fh.read()


def world(seed=7, tables=None):
    w = World(CLUBS, seed=seed, tables=tables)
    w.open_year(first=True)
    return w


class FanCards(unittest.TestCase):
    def test_three_viewpoints_seeded_between_20_and_50_percent_and_normalised(self):
        w = world()
        lo = w.tables.governance["seed_low"]
        for card in w.fans.values():
            self.assertEqual(len(card["share"]), 3)
            self.assertAlmostEqual(sum(card["share"]), 1.0)
            self.assertEqual(card["sat"], [50.0] * 3)
            self.assertAlmostEqual(card["sentiment"], 50.0)
        self.assertEqual(len({tuple(c["share"]) for c in w.fans.values()}), len(CLUBS))
        # before normalising each was in [20, 50], so after it no share is below 20/150 or above 50/90
        for card in w.fans.values():
            self.assertTrue(all(lo / 150 - 1e-9 <= s <= 50 / 90 + 1e-9 for s in card["share"]))

    def test_sentiment_returns_gradually_toward_50(self):
        w = world()
        orig = governance.signals
        governance.signals = lambda *a: (0.0, 0.0, 0.0)
        try:
            for card in w.fans.values():
                card["sat"] = [90.0, 10.0, 50.0]
            results = {c: 0.5 for c in CLUBS}
            prev = [90.0, 10.0]
            for _ in range(5):
                governance.close_year(w, results)
                now = w.fans["C01"]["sat"]
                self.assertTrue(50 < now[0] < prev[0] and prev[1] < now[1] < 50)
                prev = now[:2]
        finally:
            governance.signals = orig

    def test_a_win_a_full_payroll_and_a_kept_core_raise_sentiment(self):
        w = world()
        w.fans["C01"]["core"] = set(w.L.clubs["C01"].all_pids())
        good = governance.signals(w, "C01", 0.9)
        bad = governance.signals(w, "C01", 0.1)
        self.assertEqual((good[0], bad[0]), (1.0, -1.0))
        self.assertEqual(good[1:], bad[1:])
        self.assertEqual(good[2], 1.0)
        w.fans["C01"]["core"] = {-1, -2, -3}
        self.assertEqual(governance.signals(w, "C01", 0.5)[2], -1.0)


class Recall(unittest.TestCase):
    def setUp(self):
        self.w = world()
        self.results = {c: 0.5 for c in CLUBS}
        self.w.L.year += 0

    def hold(self, cid, sat):
        w = self.w
        orig = governance.signals
        governance.signals = lambda *a: (0.0, 0.0, 0.0)
        try:
            w.fans[cid]["sat"] = list(sat)
            w.fans[cid]["share"] = [1 / 3] * 3
            # keep the term election away from this club in this test
            governance.close_year(w, {cid: 0.5})
        finally:
            governance.signals = orig

    def test_no_ballot_above_the_recall_threshold(self):
        w = self.w
        self.hold("C01", [40.0, 40.0, 40.0])
        self.assertFalse([r for r in w.gov_log if r["kind"] == "recall_ballot"])

    def test_a_fan_majority_at_or_below_the_threshold_removes_the_ceo_but_the_successor_waits_for_the_offseason(self):
        w = self.w
        old = w.leaders["C01"]["ceo"]
        self.hold("C01", [4.0, 4.0, 4.0])
        ballot = [r for r in w.gov_log if r["kind"] == "recall_ballot" and r["club"] == "C01"]
        self.assertTrue(ballot and ballot[0]["removed"] and ballot[0]["sentiment"] <= 20)
        self.assertIs(w.leaders["C01"]["ceo"], old)                    # no midseason dismissal
        successor = w.elected["C01"]
        self.assertIsNot(successor, old)
        self.assertIn(successor, w.ceo_pool)
        governance.turn_year(w)
        self.assertIs(w.leaders["C01"]["ceo"], successor)
        self.assertNotIn(successor, w.ceo_pool)
        self.assertIn(old, w.ceo_pool)                                 # the recalled CEO joins the pool
        self.assertFalse(w.elected)

    def test_a_ballot_fails_when_too_few_fans_are_unhappy_enough_to_vote_to_remove(self):
        w = self.w
        w.tables.governance["recall_line"] = 1.0          # nobody is unhappy enough: the ballot is held and lost
        old = w.leaders["C01"]["ceo"]
        self.hold("C01", [4.0, 4.0, 4.0])
        ballot = [r for r in w.gov_log if r["kind"] == "recall_ballot" and r["club"] == "C01"]
        self.assertTrue(ballot and not ballot[0]["removed"])
        self.assertNotIn("C01", w.elected)
        self.assertIs(w.leaders["C01"]["ceo"], old)

    def test_recall_does_not_touch_clubs_without_a_season(self):
        w = self.w
        w.fans["C03"]["sat"] = [0.0, 0.0, 0.0]
        governance.close_year(w, {"C01": 0.5})
        self.assertFalse([r for r in w.gov_log if r.get("club") == "C03"])


class Elections(unittest.TestCase):
    def test_each_club_votes_for_a_ceo_once_in_three_seasons(self):
        w = world()
        seen = {c: 0 for c in CLUBS}
        for _ in range(3):
            n0 = len(w.gov_log)
            governance.close_year(w, {c: 0.5 for c in CLUBS})
            for r in w.gov_log[n0:]:
                if r["kind"] == "election":
                    seen[r["club"]] += 1
            w.L.year += 1
        self.assertEqual(set(seen.values()), {1})

    def test_the_winner_is_a_candidate_and_the_vote_is_reproducible(self):
        a, b = world(11), world(11)
        ca, cb = a.leaders["C05"]["ceo"], b.leaders["C05"]["ceo"]
        wa, wb = governance.election(a, "C05", incumbent=ca), governance.election(b, "C05", incumbent=cb)
        self.assertEqual(wa.identity.id, wb.identity.id)
        self.assertTrue(wa is ca or wa in a.ceo_pool)

    def test_an_unhappy_electorate_turns_out_the_incumbent_more_often(self):
        out = {}
        for mood in (85.0, 15.0):
            lost = 0
            for seed in range(1, 9):
                w = world(seed)
                for cid in CLUBS:
                    w.fans[cid]["sat"] = [mood] * 3
                    ceo = w.leaders[cid]["ceo"]
                    lost += governance.election(w, cid, incumbent=ceo) is not ceo
            out[mood] = lost
        self.assertGreater(out[15.0], out[85.0])

    def test_the_recalled_ceo_cannot_run_in_the_recall_election(self):
        w = world()
        ceo = w.leaders["C01"]["ceo"]
        w.ceo_pool.append(ceo)
        for _ in range(5):
            self.assertIsNot(governance.election(w, "C01", exclude={ceo.identity.id}), ceo)


class FrontOffice(unittest.TestCase):
    def test_every_club_keeps_all_six_leaders_through_many_year_turns(self):
        w = world()
        for _ in range(25):
            w.L.year += 1
            governance.turn_year(w)
            for lead in w.leaders.values():
                self.assertTrue({"ceo", "gm"} <= set(lead))

    def test_the_ceo_dismisses_the_gm_only_after_a_bad_season(self):
        w = world()
        g, c = w.tables.governance, w.tables.coaching
        g["gm_fire_p"], g["gm_resign_base"], c["resign_amb"], c["resign_loy"] = 1.0, 0.0, 0.0, 0.0
        old = {cid: w.leaders[cid]["gm"] for cid in CLUBS}
        for cid in CLUBS:
            w.leaders[cid]["ceo"].traits["TMP"] = 50
        governance.window(w, {cid: 0.5 for cid in CLUBS})
        self.assertTrue(all(w.leaders[c]["gm"] is old[c] for c in CLUBS))
        governance.window(w, {cid: 0.1 for cid in CLUBS})
        self.assertTrue(all(w.leaders[c]["gm"] is not old[c] for c in CLUBS))
        elsewhere = {d["gm"].identity.id for d in w.leaders.values()}
        self.assertTrue(all(old[c] in w.gm_pool or old[c].identity.id in elsewhere for c in CLUBS))      # let go: back in the pool, or hired by another club

    def test_a_patient_ceo_keeps_his_gm(self):
        w = world()
        g = w.tables.governance
        g["gm_fire_p"], g["patience_effect"], g["gm_resign_base"] = 1.0, 1.0, 0.0
        w.tables.coaching["resign_amb"] = w.tables.coaching["resign_loy"] = 0.0
        for cid in CLUBS:
            w.leaders[cid]["ceo"].traits["TMP"] = 100
        old = {cid: w.leaders[cid]["gm"] for cid in CLUBS}
        governance.window(w, {cid: 0.0 for cid in CLUBS})
        self.assertTrue(all(w.leaders[c]["gm"] is old[c] for c in CLUBS))

    def test_a_dismissed_gm_is_not_rehired_by_the_same_club(self):
        w = world()
        w.tables.governance["gm_fire_p"] = 1.0
        for cid in CLUBS:
            w.leaders[cid]["ceo"].traits["TMP"] = 50
        old = {cid: w.leaders[cid]["gm"] for cid in CLUBS}
        governance.window(w, {cid: 0.0 for cid in CLUBS})
        self.assertTrue(all(w.leaders[c]["gm"] is not old[c] for c in CLUBS))

    def test_pools_stay_stocked_obey_the_four_year_rule_and_names_stay_unique(self):
        w = world()
        n = int(w.tables.governance["candidates"])
        for _ in range(20):
            w.L.year += 1
            governance.turn_year(w)
            for role in ("ceo", "gm"):
                p = governance.pool(w, role)
                self.assertGreaterEqual(len(p), n)
                self.assertTrue(all(w.pool_years.get(c.identity.id, 0) < 4 for c in p))
            names = [c.identity.name for d in w.leaders.values() for c in d.values()] + [c.identity.name for c in w.ceo_pool + w.gm_pool + w.coach_pool]
            self.assertEqual(len(names), len(set(names)))

    def test_the_average_ceo_and_gm_stay_near_the_founders_over_40_years(self):
        clubs = [f"D{i:02d}" for i in range(1, 25)]
        w = World(clubs, seed=5)
        w.open_year(first=True)
        fin, gm = [], []
        for t in range(40):
            w.L.year += 1
            governance.turn_year(w)
            res = {c: ((zlib.crc32(f"{c}{t}".encode()) % 1000) / 1000) for c in clubs}
            governance.window(w, res)
            governance.close_year(w, res)
            if t >= 8:
                fin.append(st.mean(d["ceo"].attrs["FIN"] for d in w.leaders.values()))
                gm.append(st.mean(governance.coaching.fit(w, d["gm"], "gm") for d in w.leaders.values()))
        self.assertLess(abs(st.mean(fin) - 60), 6.0)         # CEOs stay a long time and the weak are voted or recalled out, so a few points above the founders (Edition 8.33)
        self.assertLess(abs(st.mean(gm) - 60), 4.0)


class Discretion(unittest.TestCase):
    """The GM's discretion is hidden and feeds only the story layer."""

    def test_it_exists_on_every_gm_and_only_on_gms(self):
        w = world()
        for lead in w.leaders.values():
            self.assertIn("DSC", lead["gm"].attrs)
            self.assertNotIn("DSC", lead["ceo"].attrs)

    def test_adding_it_shifted_no_other_card(self):
        t1 = load_tables()
        t2 = load_tables()
        t2.profile = {k: [r for r in v if r[0] != "DSC"] for k, v in t2.profile.items()}
        a, b = Creator(3, t1), Creator(3, t2)
        for role in ("ceo", "gm", "coach"):
            x, y = a.leader(role, 2030), b.leader(role, 2030)
            self.assertEqual(x.identity.name, y.identity.name)
            self.assertEqual(x.traits, y.traits)
            self.assertEqual({k: v for k, v in x.attrs.items() if k != "DSC"}, y.attrs)

    def test_no_engine_or_ledger_source_reads_it(self):
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "dfl")
        offenders = []
        for sub in ("engine", "ledger"):
            for f in sorted(os.listdir(os.path.join(root, sub))):
                if f.endswith(".py") and "DSC" in read(os.path.join(root, sub, f)):
                    offenders.append(f)
        for f in ("year.py", "league.py", "standings.py", "draft.py"):
            if "DSC" in read(os.path.join(root, f)):
                offenders.append(f)
        self.assertEqual(offenders, [])

    def test_changing_every_gms_discretion_changes_nothing_that_happens(self):
        digests = []
        for dsc in (0, 100):
            w = World(CLUBS, seed=9)
            for lead in w.leaders.values():
                lead["gm"].attrs["DSC"] = dsc
            for y in range(2):
                w.step_year(first=(y == 0))
            digests.append((w.L.digest(), len(w.trade_log), w.stats["trades"]))
        self.assertEqual(digests[0], digests[1])


if __name__ == "__main__":
    unittest.main()


class OneWinnerOneClub(unittest.TestCase):
    """A pool candidate who has won one club's election cannot win another's in the same year (Edition 8.39)."""

    def test_an_elected_candidate_is_not_a_candidate_elsewhere(self):
        from dfl.ledger import governance
        w = World(["C01", "C02", "C03", "C04"], seed=9)
        w.open_year(first=True)
        first = governance.election(w, "C01", incumbent=None, exclude=set())
        w.elected["C01"] = first
        for cid in ("C02", "C03", "C04"):
            again = governance.election(w, cid, incumbent=None, exclude=set())
            self.assertIsNot(again, first)

    def test_the_election_always_has_candidates(self):
        from dfl.ledger import governance
        w = World(["C01", "C02"], seed=9)
        w.open_year(first=True)
        everyone = {c.identity.id for c in w.ceo_pool}
        winner = governance.election(w, "C01", incumbent=None, exclude=everyone)
        self.assertNotIn(winner.identity.id, everyone)
