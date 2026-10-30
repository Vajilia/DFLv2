"""Waiver claim order (Design Document section 12): "Priority is last season's draft order until four league weeks finish, then weekly from
current standings, with Tour standings in their prior draft bands."

Readings, stated once (docs/STEP7_LEDGER_VERIFICATION.md, owner question W1): "first claim" goes to the worst record, as in the draft; the clubs
away on the Tour have no place in the home clubs' standings, so each keeps the place her prior draft band gave her, and the home clubs fill the
other places in standings order; the standings used are those of the league weeks finished before the paid week being processed.
"""
import os
import random
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl import standings  # noqa: E402
from dfl.ledger import rules as R  # noqa: E402
from dfl.ledger.contracts import Contract  # noqa: E402
from dfl.ledger.ledger import Ledger  # noqa: E402
from dfl.ledger.world import World  # noqa: E402
from dfl.year import Universe  # noqa: E402

CLUBS = [f"C{i:02d}" for i in range(12)]
TOUR = ["C03", "C07"]
HOME = [c for c in CLUBS if c not in TOUR]
TEAMS = {c: ("W" if i < 5 else "E", f"D{i % 2}") for i, c in enumerate(HOME)}
PRIOR_DRAFT = ["C07", "C03", "C09", "C01", "C11", "C00", "C05", "C02", "C10", "C04", "C08", "C06"]       # the Tour clubs held the first two places


def game(week, away, home, a, h):
    return {"week": week, "game": 1, "away": away, "home": home, "away_score": a, "home_score": h}


def stub(games, prior=PRIOR_DRAFT, with_run=True):
    run = types.SimpleNamespace(games=games, active=TEAMS, ambassadors=TOUR, seed="dfltest", season=2) if with_run else None
    return types.SimpleNamespace(draft_order=prior, L=types.SimpleNamespace(clubs={c: None for c in CLUBS}), run=run)


def weeks(n, rng):
    """n weeks of games among the home clubs, deterministic scores."""
    out = []
    for w in range(1, n + 1):
        order = list(HOME)
        rng.shuffle(order)
        for i in range(0, len(order), 2):
            out.append(game(w, order[i], order[i + 1], rng.randint(0, 40), rng.randint(0, 40)))
    return out


class Order(unittest.TestCase):
    def test_before_four_weeks_have_finished_it_is_last_seasons_draft_order(self):
        rng = random.Random(1)
        for n in (0, 1, 2, 3):
            self.assertEqual(Universe.waiver_priority(stub(weeks(n, rng))), PRIOR_DRAFT, n)

    def test_without_a_season_underway_or_an_earlier_draft(self):
        self.assertEqual(Universe.waiver_priority(stub([], with_run=False)), PRIOR_DRAFT)
        self.assertEqual(Universe.waiver_priority(stub(weeks(3, random.Random(2)), prior=None)), CLUBS)       # first season, early weeks: ids
        late = Universe.waiver_priority(stub(weeks(5, random.Random(2)), prior=None))                         # later: standings, Tour clubs at their id places
        self.assertEqual([late[CLUBS.index(c)] for c in TOUR], TOUR)
        s = stub([], prior=None, with_run=False)
        self.assertEqual(Universe.waiver_priority(s), CLUBS)

    def test_from_the_fifth_week_it_is_the_standings_worst_first_with_tour_clubs_in_their_bands(self):
        rng = random.Random(3)
        for n in (4, 5, 9):
            games = weeks(n, rng)
            got = Universe.waiver_priority(stub(games))
            self.assertEqual(sorted(got), CLUBS)
            for i, c in enumerate(PRIOR_DRAFT):
                if c in TOUR:
                    self.assertEqual(got[i], c, "a Tour club keeps her band's place")
            rec = standings.tally(TEAMS, games)
            best_first = standings.rank(list(TEAMS), rec, TEAMS, "dfltest|season2")
            self.assertEqual([c for c in got if c not in TOUR], list(reversed(best_first)))
            wins = [rec[c].w for c in [c for c in got if c not in TOUR]]
            self.assertEqual(wins, sorted(wins), "fewest wins first")

    def test_it_is_a_pure_function_of_the_state(self):
        games = weeks(6, random.Random(4))
        self.assertEqual(Universe.waiver_priority(stub(games)), Universe.waiver_priority(stub(list(games))))


class InTheWorld(unittest.TestCase):
    def test_a_bare_world_uses_id_order_and_draws_no_random_numbers(self):
        w = World(CLUBS[:4], seed=1)
        before = w.rng.getstate()
        self.assertEqual(w.waiver_order(), CLUBS[:4])
        self.assertEqual(w.rng.getstate(), before)

    def test_the_first_club_in_the_order_gets_the_claim(self):
        for order in (["C00", "C01", "C02"], ["C02", "C01", "C00"]):
            L = Ledger(["C00", "C01", "C02"], 2001)
            L.draft_completed = L.year
            p = L.new_player("QB", 80, age=24, seasons=1)
            L.sign("C00" if order[0] != "C00" else "C01", p.pid, Contract(L.year, (5,), (), (), L.week), "roster")
            src = p.club
            L.release(src, p.pid)
            claims = L.resolve_waivers([c for c in order if c != src] + [src], wants=lambda c, q: True)
            self.assertEqual(claims, [(p.pid, [c for c in order if c != src][0])])


if __name__ == "__main__":
    unittest.main()
