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
from dfl.characters import soul  # noqa: E402
from dfl.ledger import coaching  # noqa: E402
from dfl.characters.card import Card  # noqa: E402
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
        ld = Creator(3, T).leader("coach", 2000)
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
        taken = {c.identity.name for c in w.cards.values()} | {c.identity.name for d in w.leaders.values() for c in d.values()} | {c.identity.name for c in w.coach_pool} | {c.identity.name for c in w.ceo_pool + w.gm_pool}
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


class Scouting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = World(CLUBS, seed=31)
        cls.w.open_year(first=True)

    def errors(self, players, cid):
        w = self.w
        return [w.view(cid, p) - w.rating[p.pid] - w.tables.scouting_bias.get(p.pos, 0.0) for p in players]

    def test_a_view_is_stable_within_a_year_and_differs_between_clubs(self):
        w = self.w
        p = next(iter(w.L.players.values()))
        self.assertEqual(w.view("C01", p), w.view("C01", p))
        self.assertNotEqual(w.view("C01", p), w.view("C02", p))

    def test_views_are_unbiased_apart_from_the_position_bias_and_noisy(self):
        w = self.w
        fa = [p for p in w.L.players.values() if p.where == "fa"][:600]
        e = self.errors(fa, "C03")
        self.assertLess(abs(statistics.mean(e)), 0.6)
        self.assertGreater(statistics.pstdev(e), 1.5)

    def test_more_seasons_and_a_better_evaluator_and_own_players_all_shrink_the_error(self):
        w = self.w
        pool = [p for p in w.L.players.values() if p.where == "fa"][:400]
        rookies = []
        for p in pool:
            q = type(p)(p.pid, p.pos, p.ovr, p.age, 0)
            rookies.append(q)
        vets = [type(p)(p.pid, p.pos, p.ovr, p.age, 8) for p in pool]
        def sd(ps):
            w._views.clear()
            return statistics.pstdev(self.errors(ps, "C04"))
        self.assertGreater(sd(rookies), sd(vets) * 1.4)
        club = "C05"
        w._views.clear()
        w.leaders[club]["gm"].attrs["EVL"] = 95
        good = statistics.pstdev(self.errors(rookies, club))
        w._views.clear()
        w.leaders[club]["gm"].attrs["EVL"] = 5
        bad = statistics.pstdev(self.errors(rookies, club))
        self.assertGreater(bad, good * 1.5)
        w._views.clear()
        own = [type(p)(p.pid, p.pos, p.ovr, p.age, 0, club=club) for p in pool]
        w.leaders[club]["gm"].attrs["EVL"] = 50
        w._views.clear()
        e_own = statistics.pstdev(self.errors(own, club))
        w._views.clear()
        e_other = statistics.pstdev(self.errors(rookies, club))
        self.assertLess(e_own, e_other * 0.8)

    def test_clubs_re_judge_everyone_each_year(self):
        w = World(CLUBS, seed=32)
        w.open_year(first=True)
        p = next(iter(w.L.players.values()))
        a = w.view("C01", p)
        w.L.new_year()
        w.age_and_retire()
        if p.pid in w.L.players:
            self.assertNotEqual(w.view("C01", w.L.players[p.pid]), a)

    def test_decisions_follow_the_view_not_the_truth(self):
        w = World(CLUBS, seed=33)
        w.open_year(first=True)
        pool = w.fas("QB", "C06")
        views = [w.view("C06", p) for p in pool]
        self.assertEqual(views, sorted(views, reverse=True))


class ClubPrices(unittest.TestCase):
    """Clubs price a free agent by their own view of her overall, never by the true figure (Edition 8.35)."""

    def setUp(self):
        self.w = World(CLUBS, seed=33)
        self.w.open_year(first=True)
        self.p = next(p for p in sorted(self.w.L.players.values(), key=lambda q: q.pid) if p.where == "fa" and p.pos == "WR")

    def test_a_clubs_quote_follows_its_view_and_two_clubs_can_differ(self):
        w, p = self.w, self.p
        w._views[("C01", p.pid)], w._views[("C02", p.pid)] = 80.0, 62.0
        self.assertEqual(w.quote("C01", p), World.quote(w, "C01", p))
        self.assertGreater(w.quote("C01", p), w.quote("C02", p))
        from dfl.ledger.world import price
        self.assertEqual(w.quote("C01", p), price(80, p.pos))
        self.assertEqual(w.quote(None, p), ask(p))               # no club asking: the league's own bookkeeping

    def test_a_player_a_club_takes_for_a_scrub_signs_at_the_minimum_whatever_she_really_is(self):
        w, p = self.w, self.p
        w.rating[p.pid], p.ovr = 85.0, 85
        w._views[("C01", p.pid)] = 50.0
        self.assertEqual(w.negotiate("C01", p), w.quote("C01", p))
        self.assertLessEqual(w.quote("C01", p), R.MIN_SALARY)
        self.assertEqual(w.offer("C01", p).cap_hit(w.L.year), R.MIN_SALARY)
        self.assertIn(p, w.cheap(p.pos, "C01"))

    def test_a_star_by_the_clubs_own_view_is_negotiated_not_handed_over_at_the_minimum(self):
        w, p = self.w, self.p
        w.rating[p.pid], p.ovr = 55.0, 55
        w._views[("C01", p.pid)] = 85.0
        self.assertGreater(w.quote("C01", p), R.MIN_SALARY)
        v = w.negotiate("C01", p)
        self.assertTrue(v is None or v > R.MIN_SALARY)


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
        from dfl.ledger import coaching
        gd = gameday.build(u.L, cid, elevate=False, judge=lambda q: coaching.judged(u.resolver.w, cid, q))
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


class Souls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = World(CLUBS, seed=41)
        cls.w.open_year(first=True)

    def cards(self):
        return [c for c in self.w.cards.values() if c.identity.role == "player"]

    def test_every_registered_player_has_a_soul_with_a_known_archetype(self):
        w = self.w
        for c in self.cards():
            self.assertIn(c.soul["archetype"], w.tables.archetypes)
            self.assertEqual(set(c.soul["base"]), set(soul.TRAITS))

    def test_all_archetypes_occur_and_none_dominates(self):
        from collections import Counter
        n = Counter(c.soul["archetype"] for c in self.cards())
        self.assertEqual(set(n), set(self.w.tables.archetypes))
        self.assertLess(max(n.values()) / sum(n.values()), 0.35)

    def test_assign_is_idempotent_and_archetype_is_nearest_centre(self):
        t = self.w.tables
        c = self.cards()[0]
        first = dict(c.soul)
        soul.assign(c, t)
        self.assertEqual(c.soul, first)
        self.assertEqual(c.soul["archetype"], soul.archetype_of(c.soul["base"], t))

    def test_drift_never_leaves_the_bound_and_never_changes_the_archetype(self):
        import random
        t = self.w.tables
        c = Card.loads(self.cards()[0].dumps())
        a, base = c.soul["archetype"], dict(c.soul["base"])
        rng = random.Random(5)
        for _ in range(200):
            soul.drift(c, rng, t)
            for k in soul.TRAITS:
                self.assertLessEqual(abs(c.traits[k] - base[k]), t.soul["drift_bound"] + 1)
                self.assertTrue(0 <= c.traits[k] <= 100)
        self.assertEqual(c.soul["archetype"], a)

    def test_drift_pulls_toward_the_archetype_centre(self):
        import random
        t = self.w.tables
        c = Card.loads(self.cards()[0].dumps())
        centre = t.archetypes[c.soul["archetype"]]["centre"]
        d0 = sum(abs(c.traits[k] - centre[k]) for k in soul.TRAITS)
        rng = random.Random(6)
        for _ in range(40):
            soul.drift(c, rng, t)
        d1 = sum(abs(c.traits[k] - centre[k]) for k in soul.TRAITS)
        self.assertLess(d1, d0 + 15)          # noise can push it out of a tight spot, but never far; the bound test covers the limit

    def test_self_view_is_true_overall_plus_the_archetype_bias(self):
        t = self.w.tables
        for c in self.cards()[:50]:
            self.assertAlmostEqual(soul.self_view(c, t), c.true_overall(t) + t.archetypes[c.soul["archetype"]]["self_bias"])

    def test_a_card_survives_storage_with_its_soul_and_old_cards_still_load(self):
        c = self.cards()[0]
        self.assertEqual(Card.loads(c.dumps()).soul, c.soul)
        d = c.to_dict(); d.pop("soul")
        self.assertEqual(Card.from_dict(d).soul, {})

    def test_the_league_year_moves_traits_but_not_souls(self):
        w = World(CLUBS, seed=42)
        w.open_year(first=True)
        before = {pid: (dict(c.traits), c.soul["archetype"]) for pid, c in w.cards.items() if c.identity.role == "player"}
        w.L.year += 1
        w.age_and_retire()
        moved = same = 0
        for pid, (tr, arch) in before.items():
            c = w.cards.get(pid)
            if c is None:
                continue
            self.assertEqual(c.soul["archetype"], arch)
            moved += c.traits != tr
            same += c.traits == tr
        self.assertGreater(moved, same)


class Negotiation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = World(CLUBS, seed=51)
        cls.w.open_year(first=True)

    def star(self, **traits):
        """A fresh free agent with a chosen soul and a market price above the minimum."""
        w = self.w
        for card in (w.creator.player("WR", 26, w.L.year, "pool", talent_mean=2.5) for _ in range(30)):
            p = w.register(card, seasons=4)
            if ask(p) > R.MIN_SALARY and p.ovr >= 60:
                card.traits.update(traits)
                card.soul["archetype"] = "PRO"           # a neutral self-view (bias 0) for the comparisons
                return p, card
        self.fail("no priced player made")

    def test_greed_and_ambition_raise_the_demand_and_loyalty_lowers_it_for_her_last_club(self):
        w = self.w
        base = dict(GRE=50, AMB=50, LOY=50)
        p, c = self.star(**base)
        d0 = w.demand(p, "C01")
        c.traits.update(GRE=100); d_gre = w.demand(p, "C01")
        c.traits.update(GRE=50, AMB=100); d_amb = w.demand(p, "C01")
        c.traits.update(AMB=50, LOY=100)
        p.last_club = "C01"
        d_home, d_away = w.demand(p, "C01"), w.demand(p, "C02")
        self.assertGreater(d_gre, d0)
        self.assertGreater(d_amb, d0)
        self.assertLess(d_home, d0)
        self.assertEqual(d_away, d0)

    def test_a_competitor_asks_more_than_a_loyalist_of_the_same_ability(self):
        w = self.w
        p, c = self.star(GRE=50, AMB=50, LOY=50)
        c.soul["archetype"] = "CMP"; hi = w.demand(p, "C01")
        c.soul["archetype"] = "LOL"; lo = w.demand(p, "C01")
        self.assertGreater(hi, lo)

    def test_every_year_unsigned_lowers_the_price_and_it_stays_at_or_above_the_minimum(self):
        w = self.w
        p, c = self.star(GRE=50, AMB=50, LOY=50)
        prev = w.demand(p, "C01")
        for y in range(1, 4):
            p.pool_years = y
            d = w.demand(p, "C01")
            self.assertLessEqual(d, prev)
            prev = d
        p.pool_years = 50                       # far beyond the four-year pool rule: her price bottoms out at 30 percent of her own valuation
        self.assertGreaterEqual(w.demand(p, "C01"), R.MIN_SALARY)
        self.assertLess(w.demand(p, "C01"), prev)

    def test_players_priced_at_the_minimum_do_not_bargain(self):
        w = self.w
        cheap = next(p for p in w.L.players.values() if p.where == "fa" and ask(p) <= R.MIN_SALARY)
        self.assertEqual(w.negotiate("C01", cheap), ask(cheap))

    def test_no_deal_when_she_asks_more_than_the_club_will_pay_and_a_deal_at_her_price_otherwise(self):
        w = self.w
        p, c = self.star(GRE=100, AMB=100, LOY=50)
        c.soul["archetype"] = "CMP"
        w._views[("C05", p.pid)] = float(p.ovr) - 6          # this club thinks she is much worse than she thinks
        self.assertIsNone(w.negotiate("C05", p))
        w._views[("C06", p.pid)] = float(p.ovr) + 10         # this one thinks she is much better
        got = w.negotiate("C06", p)
        d = w.demand(p, "C06")
        self.assertTrue(0.9 * d <= got <= 1.07 * d)            # struck in the contract room around her demand
        self.assertLessEqual(got, w.willing("C06", p))

    def test_free_agency_refuses_some_deals_and_still_fills_the_rosters(self):
        w = World(CLUBS, seed=52)
        w.step_year(first=True)
        self.assertGreater(w.stats.get("refusals", 0), 0)
        self.assertGreater(w.stats.get("deals", 0), 0)
        for cid in w.L.clubs:
            self.assertGreaterEqual(len(w.L.clubs[cid].roster), 46)


class Coaches(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = World(CLUBS, seed=71)
        cls.w.open_year(first=True)

    def test_coaches_are_one_category_and_hold_posts(self):
        w = self.w
        for lead in w.leaders.values():
            self.assertEqual({lead[p].identity.role for p in coaching.POSTS}, {"coach"})
            self.assertEqual(len({lead[p].identity.id for p in coaching.POSTS}), 4)
            for p in coaching.POSTS:
                self.assertTrue({"MGT", "MOT", "TCH", "SCH", "ADJ"} <= set(lead[p].attrs))
        c = w.leaders["C01"]["oc"]
        self.assertNotEqual(coaching.fit(w, c, "hc"), coaching.fit(w, c, "oc"))      # the same coach fits different posts differently

    def test_teaching_is_capped_signed_and_zero_for_an_average_staff(self):
        w = self.w
        cap = w.tables.coaching["dev_cap"]
        lead = w.leaders["C01"]
        for tch, sign in ((99, 1), (1, -1)):
            for r in coaching.POSTS:
                lead[r].attrs["TCH"] = tch
            e = coaching.teaching(w, "C01", "QB")
            self.assertLessEqual(abs(e), cap)
            self.assertEqual(e > 0, sign > 0)
        for r in coaching.POSTS:
            lead[r].attrs["TCH"] = 50
        self.assertEqual(coaching.teaching(w, "C01", "QB"), 0.0)

    def test_only_the_position_groups_own_coordinator_counts(self):
        w = self.w
        lead = w.leaders["C02"]
        for r in coaching.POSTS:
            lead[r].attrs["TCH"] = 50
        lead["oc"].attrs["TCH"] = 99
        self.assertGreater(coaching.teaching(w, "C02", "WR"), 0)
        self.assertEqual(coaching.teaching(w, "C02", "CB"), 0.0)

    def test_a_teaching_effect_moves_development_the_right_way_and_by_little(self):
        w = self.w
        base = Card.loads(next(c for c in w.cards.values() if c.identity.pos == "WR").dumps())
        outs = {}
        for coach in (-0.5, 0.0, 0.5):
            c = Card.loads(base.dumps())
            develop(c, w.L.year, random.Random(9), w.tables, coach)
            outs[coach] = c.true_overall(w.tables)
        self.assertLess(outs[-0.5], outs[0.0])
        self.assertLess(outs[0.0], outs[0.5])
        self.assertLess(outs[0.5] - outs[-0.5], 1.5)

    def test_a_coach_retires_for_certain_at_the_oldest_age_and_never_before_the_start_age(self):
        w = self.w
        card = w.leaders["C03"]["hc"]
        top = w.tables.roles["coach"]["age_max"]
        young = int(w.tables.coaching["retire_age_start"]) - 1
        self.assertEqual(coaching.retire_chance(w, card, card.identity.birth_year + top), 1.0)
        self.assertEqual(coaching.retire_chance(w, card, card.identity.birth_year + young), 0.0)

    def test_twenty_five_years_every_club_has_its_four_posts_and_the_pool_holds_its_minimum(self):
        w = World(CLUBS, seed=72)
        w.open_year(first=True)
        top = w.tables.roles["coach"]["age_max"]
        need = int(w.tables.coaching["pool_candidates"]) * len(coaching.POSTS)
        for _ in range(25):
            w.L.year += 1
            w.age_and_retire()
            ids = set()
            for cid, lead in w.leaders.items():
                self.assertTrue({"ceo", "gm", "hc", "oc", "dc", "stc"} <= set(lead))
                for r in coaching.POSTS:
                    self.assertLessEqual(lead[r].age(w.L.year), top)
                    self.assertNotIn(lead[r].identity.id, ids)             # a coach holds one post, at one club
                    ids.add(lead[r].identity.id)
            self.assertGreaterEqual(len(w.coach_pool), need)
            self.assertTrue(all(w.pool_years.get(c.identity.id, 0) < 4 for c in w.coach_pool))
            self.assertFalse(ids & {c.identity.id for c in w.coach_pool})
        self.assertGreater(w.stats["coach_retirements"], 20)

    def test_a_vacancy_goes_to_the_pool_candidate_the_club_rates_highest_for_that_post(self):
        w = World(CLUBS, seed=74)
        w.open_year(first=True)
        w.L.year += 1
        w.age_and_retire()
        for cid, post in (("C04", "oc"), ("C05", "hc")):
            del w.leaders[cid][post]
            pool = list(w.coach_pool)
            best = max(pool, key=lambda c: (coaching.view(w, cid, c, post), -c.identity.id))
            got = coaching.hire(w, cid, post)
            self.assertIs(got, best)
            self.assertIs(w.leaders[cid][post], best)
            self.assertNotIn(best, w.coach_pool)

    def test_any_pool_coach_can_be_hired_to_any_post_and_a_fired_coach_rejoins_the_pool(self):
        w = World(CLUBS, seed=75)
        w.open_year(first=True)
        w.L.year += 1
        w.age_and_retire()
        coord = w.leaders["C06"]["dc"]
        coaching.vacate(w, "C06", "dc", "coach_fires_coord")
        self.assertIn(coord, w.coach_pool)
        self.assertEqual(w.pool_years[coord.identity.id], 0)
        for a in ("MGT", "MOT", "TCH"):
            coord.attrs[a] = 99                         # an outstanding head-coach fit, far above any other candidate
        del w.leaders["C07"]["hc"]
        got = coaching.hire(w, "C07", "hc")
        self.assertIs(got, coord)                       # a coordinator let go by one club becomes another club's head coach
        self.assertIs(w.leaders["C07"]["hc"], coord)
        self.assertNotIn(coord, w.coach_pool)

    def test_the_window_dismisses_after_bad_seasons_resigns_and_always_refills(self):
        w = World(CLUBS, seed=76)
        w.open_year(first=True)
        w.L.year += 1
        w.age_and_retire()
        before = {cid: {p: lead[p].identity.id for p in coaching.POSTS} for cid, lead in w.leaders.items()}
        results = {cid: (0.1 if i % 2 else 0.6) for i, cid in enumerate(sorted(w.leaders))}
        for _ in range(3):
            w.L.year += 1
            w.age_and_retire()
            coaching.window(w, results)
        for cid, lead in w.leaders.items():
            self.assertTrue(set(coaching.POSTS) <= set(lead))
        self.assertGreater(w.stats.get("coach_fires_hc", 0), 0)
        self.assertGreater(w.stats.get("coach_fires_coord", 0), 0)
        self.assertGreater(w.stats.get("coach_resignations", 0), 0)
        bad = [c for i, c in enumerate(sorted(w.leaders)) if i % 2]
        good = [c for i, c in enumerate(sorted(w.leaders)) if not i % 2]
        changed = lambda cs: sum(w.leaders[c][p].identity.id != before[c][p] for c in cs for p in coaching.POSTS)
        self.assertGreater(changed(bad), changed(good))           # bad seasons cost coaches their posts

    def test_the_window_never_rehires_the_coach_the_club_just_let_go(self):
        w = World(CLUBS, seed=77)
        w.open_year(first=True)
        w.L.year += 1
        w.age_and_retire()
        w.tables.coaching["fire_hc_p"] = 1.0
        w.tables.coaching["ceo_patience_effect"] = 0.0
        try:
            for cid in w.leaders:
                w.leaders[cid]["ceo"].traits["TMP"] = 50
            old = {cid: w.leaders[cid]["hc"].identity.id for cid in w.leaders}
            coaching.window(w, {cid: 0.0 for cid in w.leaders})
            for cid in w.leaders:
                self.assertNotEqual(w.leaders[cid]["hc"].identity.id, old[cid])
        finally:
            w.tables.coaching["fire_hc_p"] = 0.35
            w.tables.coaching["ceo_patience_effect"] = 0.5

    def test_leadership_is_repeatable(self):
        def run():
            w = World(CLUBS, seed=73)
            w.open_year(first=True)
            res = {cid: 0.2 + 0.05 * i % 7 / 10 for i, cid in enumerate(sorted(w.leaders))}
            for _ in range(8):
                w.L.year += 1
                w.age_and_retire()
                coaching.window(w, res)
            return sorted((cid, r, c.identity.id) for cid, lead in w.leaders.items() for r, c in lead.items())
        self.assertEqual(run(), run())


class CoachJudgement(unittest.TestCase):
    """Coaches choose starters by their own estimate of a player, with an error as likely to flatter as to wrong her (Edition 8.36)."""

    @classmethod
    def setUpClass(cls):
        cls.w = World(CLUBS, seed=41)
        cls.w.open_year(first=True)

    def _errors(self, w):
        from dfl.ledger import coaching
        out = []
        for cid in sorted(w.L.clubs)[:8]:
            for pid in w.L.clubs[cid].roster:
                p = w.L.players[pid]
                out.append(coaching.judged(w, cid, p) - w.rating[pid])
        return out

    def test_the_error_has_no_lean_either_way(self):
        e = self._errors(self.w)
        mean = sum(e) / len(e)
        sd = (sum((x - mean) ** 2 for x in e) / len(e)) ** 0.5
        self.assertLess(abs(mean), 3 * sd / len(e) ** 0.5)
        self.assertLess(abs(sum(1 for x in e if x > 0) / len(e) - 0.5), 0.07)

    def test_it_is_fixed_within_the_year(self):
        from dfl.ledger import coaching
        cid = sorted(self.w.L.clubs)[0]
        p = self.w.L.players[self.w.L.clubs[cid].roster[0]]
        a = coaching.judged(self.w, cid, p)
        self.w._judged.clear()
        self.assertEqual(a, coaching.judged(self.w, cid, p))

    def test_a_better_staff_and_more_evidence_cut_the_error(self):
        from dfl.ledger import coaching
        w = World(CLUBS, seed=41)
        w.open_year(first=True)
        cid = sorted(w.L.clubs)[0]
        lead = w.leaders[cid]
        pos = w.L.players[w.L.clubs[cid].roster[0]].pos

        def spread(attr_value, seasons):
            for post, key in (("hc", "MGT"), ("oc", "ADJ"), ("dc", "ADJ"), ("stc", "ADJ")):
                lead[post].attrs[key] = attr_value
            xs = []
            for pid in w.L.clubs[cid].roster:
                p = w.L.players[pid]
                p.seasons = seasons
                w._judged.clear()
                xs.append(coaching.judged(w, cid, p) - w.rating[pid])
            return (sum(x * x for x in xs) / len(xs)) ** 0.5
        self.assertLess(spread(90, 0), spread(30, 0))
        self.assertLess(spread(50, 8), spread(50, 0))

    def test_judging_changes_who_dresses_but_not_how_many(self):
        from dfl.ledger import coaching, gameday
        w = self.w
        differ = 0
        for cid in sorted(w.L.clubs):
            true = gameday.build(w.L, cid, elevate=False)
            seen = gameday.build(w.L, cid, elevate=False, judge=lambda q, c=cid: coaching.judged(w, c, q))
            self.assertEqual(len(true.active), len(seen.active))
            differ += set(true.active) != set(seen.active)
        self.assertGreater(differ, 0)


class SnapRanking(unittest.TestCase):
    """The year-end top-quarter flag follows the club's coaches' judged overall (Edition 8.37)."""

    def test_the_flag_follows_the_ranking_it_is_given(self):
        w = World(CLUBS, seed=43)
        w.open_year(first=True)
        cid = sorted(w.L.clubs)[0]
        pids = sorted(w.L.clubs[cid].all_pids())
        low = pids[0]
        w.L.new_year(rank=lambda c, p: 1000.0 if p.pid == low else 0.0)
        self.assertTrue(w.L.players[low].snap_top)

    def test_the_world_ranks_by_judgement_not_truth(self):
        from dfl.ledger import coaching
        w = World(CLUBS, seed=43)
        w.open_year(first=True)
        seen = []
        orig = w.L.new_year
        w.L.new_year = lambda rank=None: (seen.append(rank), orig(rank=rank))[1]
        w.open_year()
        self.assertTrue(seen and seen[0] is not None)
