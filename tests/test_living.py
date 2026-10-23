"""Cards joined to the ledger: development, aging, retirement, the card-built world, and the engine reading the ledger's squads."""
import os
import random
import statistics
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl.characters import Creator, load_tables  # noqa: E402
from dfl.characters.development import develop, retire_chance  # noqa: E402
from dfl.ledger import rules as R  # noqa: E402
from dfl.ledger.world import World, ask  # noqa: E402

T = load_tables()
CLUBS = [f"C{i:02d}" for i in range(1, 9)]


class Development(unittest.TestCase):
    def cohort(self, wrk=None, n=120, years=16):
        c = Creator(3, T)
        cards = [c.player(pos, 21, 2000) for pos in ("QB", "WR", "CB", "DT") for _ in range(n // 4)]
        if wrk is not None:
            for x in cards:
                x.traits["WRK"] = wrk
        rng = random.Random(1)
        means = []
        for yr in range(years):
            means.append(statistics.mean(x.true_overall(T) for x in cards))
            for x in cards:
                develop(x, 2001 + yr, rng, T)
        return means, cards

    def test_players_rise_to_a_peak_then_decline(self):
        m, _ = self.cohort()
        peak = max(range(len(m)), key=m.__getitem__)
        self.assertTrue(3 <= peak <= 9, m)                 # age 24-30 for this mix of positions
        self.assertGreater(m[peak], m[0] + 5)
        self.assertLess(m[-1], m[peak] - 3)

    def test_work_ethic_helps_growth_and_slows_decline(self):
        hi, _ = self.cohort(wrk=100)
        lo, _ = self.cohort(wrk=0)
        self.assertGreater(hi[6], lo[6])
        self.assertGreater(hi[-1], lo[-1])

    def test_attributes_stay_in_range_and_identity_is_untouched(self):
        _, cards = self.cohort()
        before = Creator(3, T).player("QB", 21, 2000).identity
        for x in cards:
            self.assertTrue(all(1 <= v <= 99 for v in x.attrs.values()))
            self.assertGreaterEqual(x.growth["ceiling"], 0)
        c = Creator(3, T).player("QB", 21, 2000)
        develop(c, 2001, random.Random(1), T)
        self.assertEqual(c.identity, before)

    def test_development_is_deterministic(self):
        a, b = self.cohort()[1], self.cohort()[1]
        self.assertEqual([x.dumps() for x in a], [x.dumps() for x in b])

    def test_leaders_do_not_develop(self):
        ld = Creator(3, T).leader("hc", 2000)
        before = dict(ld.attrs)
        self.assertEqual(develop(ld, 2001, random.Random(1), T), 0.0)
        self.assertEqual(ld.attrs, before)

    def test_retirement_waits_for_age_and_is_certain_at_the_hard_age(self):
        c = Creator(3, T).player("WR", 22, 2000)
        peak = c.growth["peak_age"]
        self.assertEqual(retire_chance(c, 2000 + (peak + 5 - 22), 70, T), 0.0)
        self.assertGreater(retire_chance(c, 2000 + (peak + 7 - 22), 70, T), 0.0)
        self.assertGreater(retire_chance(c, 2000 + (peak + 7 - 22), 30, T), retire_chance(c, 2000 + (peak + 7 - 22), 70, T))
        self.assertEqual(retire_chance(c, 2000 + (int(T.development["retire_hard_age"]) - 22), 70, T), 1.0)


class CardWorld(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = World(CLUBS, seed=11, reckless=["C01"])

    def test_every_ledger_player_is_a_card_with_the_same_id_and_position(self):
        w = self.w
        self.assertEqual(set(w.cards), set(w.L.players))
        for pid, p in w.L.players.items():
            c = w.cards[pid]
            self.assertEqual((c.identity.id, c.identity.pos, p.pos), (pid, p.pos, c.identity.pos))
            self.assertEqual(p.age, c.age(w.L.year))
            self.assertEqual(p.ovr, int(round(w.rating[pid])))

    def test_names_are_unique_and_every_club_has_six_leaders(self):
        w = self.w
        names = [c.identity.name for c in w.cards.values()] + [c.identity.name for d in w.leaders.values() for c in d.values()]
        self.assertEqual(len(names), len(set(names)))
        for cid in CLUBS:
            self.assertEqual(sorted(w.leaders[cid]), sorted(["ceo", "gm", "hc", "oc", "dc", "stc"]))

    def test_the_draft_class_comes_from_the_creator(self):
        w = World(CLUBS, seed=12)
        w.open_year(first=True)
        w.free_agency_open()
        w.start_draft()
        drafted = [p for p in w.L.players.values() if p.where == "draft"]
        self.assertEqual(len(drafted), 672)
        by = {}
        for p in drafted:
            by[p.pos] = by.get(p.pos, 0) + 1
            self.assertEqual(w.cards[p.pid].identity.origin, "draft")
            self.assertIn(p.age, (21, 22, 23))
        self.assertTrue(all(n >= 12 for n in by.values()))

    def test_a_year_turn_ages_develops_and_retires_and_frees_names(self):
        w = World(CLUBS, seed=13)
        for y in range(8):
            w.step_year(first=(y == 0))
        L = w.L
        self.assertEqual(set(w.cards), set(L.players))
        self.assertGreater(sum(L.retired.values()), 0)
        taken = {c.identity.name for c in w.cards.values()} | {c.identity.name for d in w.leaders.values() for c in d.values()}
        self.assertEqual(w.creator.taken, taken)                 # retired characters' names are free again
        for pid, p in L.players.items():
            self.assertEqual(p.age, w.cards[pid].age(L.year))
            self.assertEqual(p.ovr, int(round(w.rating[pid])))
            self.assertAlmostEqual(w.rating[pid], w.cards[pid].true_overall(w.tables))
        # something real happened to the founders: some are older than the founding ages allow, some improved
        ages = [p.age for p in L.players.values() if w.cards[p.pid].identity.origin == "founding"]
        self.assertTrue(max(ages) >= 30)

    def test_retired_players_are_free_agents_only(self):
        w = World(CLUBS, seed=14)
        for y in range(10):
            w.step_year(first=(y == 0))
            for p in w.L.players.values():
                self.assertNotEqual(p.where, "retired")

    def test_nobody_signs_a_star_for_the_minimum(self):
        w = World(CLUBS, seed=15)
        for y in range(6):
            w.step_year(first=(y == 0))
        for p in w.L.players.values():
            if p.where == "roster" and p.contract.cap_hit(w.L.year) <= R.MIN_SALARY and w.cards[p.pid].identity.origin == "pool":
                self.assertLessEqual(ask(p), 2 * R.MIN_SALARY)

    def test_cheap_returns_only_minimum_priced_players_when_any_exist(self):
        w = self.w
        pool = w.cheap("QB")
        self.assertTrue(pool)
        if any(ask(p) <= R.MIN_SALARY for p in w.fas("QB")):
            self.assertTrue(all(ask(p) <= R.MIN_SALARY for p in pool))


class EngineOnTheLedger(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from dfl.year import Universe
        cls.u = Universe(os.path.join(ROOT, "data", "season1"), seed=21)
        cls.u.play_year()
        cls.u.play_year()

    def test_the_engine_plays_every_game_and_hands_injuries_to_the_ledger(self):
        r = self.u.resolver
        self.assertGreater(r.games_played, 500)
        self.assertGreater(r.injuries_seen, 100)

    def test_a_squad_is_exactly_the_players_the_ledger_dresses(self):
        from dfl.ledger import gameday
        u = self.u
        cid = sorted(u.L.clubs)[0]
        sq = u.resolver.club_squad(cid)
        gd = gameday.build(u.L, cid, elevate=False)
        self.assertEqual(sorted(sq.cards), sorted(gd.active))
        on_club = set(u.L.clubs[cid].roster) | set(u.L.clubs[cid].ps)
        self.assertTrue(set(sq.cards) <= on_club)
        self.assertTrue(all(u.L.players[i].out_weeks == 0 for i in sq.cards))

    def test_leaders_coach_through_the_engine(self):
        u = self.u
        st = u.resolver.staff(sorted(u.L.clubs)[0])
        self.assertTrue(st.hc and st.oc and st.dc and st.stc)

    def test_all_star_sides_use_ledger_players(self):
        u = self.u
        clubs = sorted(u.L.clubs)[:6]
        u.resolver.all_star_side("X", clubs)
        side = u.resolver.sides["X"]
        pool = {i for c in clubs for i in u.L.clubs[c].roster}
        self.assertTrue(set(side.cards) <= pool)
        self.assertTrue(side.ready())

    def test_the_league_stays_in_one_piece(self):
        u = self.u
        self.assertEqual(set(u.world.cards), set(u.L.players))
        u.world.audit()

    def test_injury_clock_semantics(self):
        w = self.u.world
        pid = next(i for i, p in w.L.players.items() if p.where == "fa" and p.out_weeks == 0)
        p = w.L.players[pid]
        w.L.phase = "season"
        w.injure(pid, 2)
        self.assertEqual(p.out_weeks, 3)          # the clock turns before the next paid week's game, so two games are missed
        p.out_weeks = 0
        w.L.phase = "post"
        w.injure(pid, 2)
        self.assertEqual(p.out_weeks, 2)
        w.heal_round()
        self.assertEqual(p.out_weeks, 1)
        p.out_weeks = 0


if __name__ == "__main__":
    unittest.main()
