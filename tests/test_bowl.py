"""The Ambassador Bowl's ballots, voted rosters, lock-time repairs and rings (src/dfl/ledger/bowl.py, spec section 7, Edition 8.56).

One placeholder-resolver league year is played once to the end of the Tour (where the ballots are cast and frozen). Variants of that state are
copied and damaged (injuries, releases, a whole position gone) to exercise the lock. A second class plays a year on the 22-player-free engine
to the Bowl itself and checks that the side that takes the field is the voted roster.
"""
import copy
import json
import os
import random
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl import stv  # noqa: E402
from dfl.ledger import bowl, honours  # noqa: E402
from dfl.ledger.positions import MINIMUMS  # noqa: E402
from dfl.year import Universe  # noqa: E402

TABLES = os.path.join(ROOT, "data", "season1")


def run_to(u, key):
    while not (u.fired and u.fired[-1][2] == key):
        u.step_event()


class Ballots(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.u = Universe(TABLES, seed=1, engine=False, spatial=False)
        run_to(cls.u, "tour_round_7")
        cls.world = cls.u.world
        cls.pre = copy.deepcopy(cls.u.world)            # the world just after the ballots are frozen, before any lock
        cls.vote = cls.world.bowl_vote
        cls.clubs = {conf: v["clubs"] for conf, v in cls.vote["conferences"].items()}

    def variant(self, healthy=True):
        """A copy of the world after the ballots; by default with everyone medically available, so a test adds exactly the damage it names."""
        w = copy.deepcopy(self.pre)
        if healthy:
            for p in w.L.players.values():
                p.out_weeks = 0
        return w

    def test_the_world_as_it_is_locks_a_valid_roster_whoever_happens_to_be_hurt(self):
        w = self.variant(healthy=False)
        hurt = sum(1 for conf, rec in w.bowl_vote["conferences"].items() for pos in rec["positional"].values() for pid in pos["elected"] if w.L.players[pid].out_weeks)
        for conf in w.bowl_vote["conferences"]:
            f = bowl.lock(w, conf)
            self.assertEqual((len(f["roster"]), len(set(f["roster"])), f["validation"]), (53, 53, []))
            self.assertTrue(all(w.L.players[pid].out_weeks == 0 for pid in f["roster"]))
            for r in f["replaced"]:
                self.assertEqual(r["why"], "medically unavailable")
        self.assertGreaterEqual(hurt, 0)

    # ---- the ballots
    def test_eight_head_coaches_vote_and_the_ballots_are_cast_once_the_tour_is_over(self):
        self.assertEqual(self.vote["year"], self.world.L.year)
        self.assertEqual(len(self.vote["voters"]), 8)
        self.assertEqual(sorted(self.vote["voters"]), sorted(self.u.run.ambassadors))
        self.assertEqual({c: len(v) for c, v in self.clubs.items()}, {"Western": 4, "Eastern": 4})
        self.assertEqual(len(self.u.run.tour_games), 28)            # the whole Tour is in

    def test_every_voter_ranks_every_candidate_of_the_position_once(self):
        P = self.world.L.players
        for conf, rec in self.vote["conferences"].items():
            pool = {}
            for c in rec["clubs"]:
                for pid in self.world.L.clubs[c].roster:
                    pool.setdefault(P[pid].pos, []).append(pid)
            for v in self.vote["voters"]:
                for pos, ranking in rec["ballots"][v].items():
                    if pos == "discretionary":
                        continue
                    self.assertEqual(sorted(ranking), sorted(pool[pos]), (conf, v, pos))

    def test_a_ballot_follows_what_the_voter_judges(self):
        from dfl.ledger import coaching
        P = self.world.L.players
        rec = self.vote["conferences"]["Western"]
        for v in self.vote["voters"][:3]:
            for pos in ("QB", "CB"):
                judged = [coaching.judged(self.world, v, P[pid]) for pid in rec["ballots"][v][pos]]
                self.assertEqual(judged, sorted(judged, reverse=True))

    def test_the_places_and_quotas_are_the_spec_numbers(self):
        self.assertEqual(sum(bowl.POSITIONAL.values()), 48)
        self.assertEqual((bowl.DISCRETIONARY, bowl.ROSTER, bowl.VOTERS), (5, 53, 8))
        for rec in self.vote["conferences"].values():
            for pos, res in rec["positional"].items():
                self.assertEqual(res["places"], bowl.POSITIONAL[pos])
                self.assertEqual(res["quota"], 8 // (bowl.POSITIONAL[pos] + 1) + 1)
                self.assertEqual(len(res["elected"]), bowl.POSITIONAL[pos])
            d = rec["discretionary"]
            self.assertEqual((d["places"], d["quota"], len(d["elected"])), (5, 2, 5))
            self.assertEqual(set(rec["positional"]), {p for p in bowl.POSITIONAL})

    def test_the_positional_winners_are_at_the_position_and_the_53_are_distinct_players_of_the_four_clubs(self):
        P = self.world.L.players
        for conf, rec in self.vote["conferences"].items():
            won = []
            for pos, res in rec["positional"].items():
                for pid in res["elected"]:
                    self.assertEqual(P[pid].pos, pos)
                    self.assertIn(P[pid].club, rec["clubs"])
                    won.append(pid)
            won += rec["discretionary"]["elected"]
            self.assertEqual(len(won), 53)
            self.assertEqual(len(set(won)), 53)
            for pid in rec["discretionary"]["elected"]:
                self.assertIn(P[pid].club, rec["clubs"])

    def test_the_discretionary_field_is_everyone_not_elected_by_position_and_may_include_a_fullback(self):
        for conf, rec in self.vote["conferences"].items():
            elected = {pid for r in rec["positional"].values() for pid in r["elected"]}
            field = set(rec["discretionary"]["order"])
            self.assertFalse(field & elected)
            P = self.world.L.players
            everyone = {pid for c in rec["clubs"] for pid in self.world.L.clubs[c].roster}
            self.assertEqual(field | elected, everyone)
            self.assertTrue(any(P[pid].pos == "FB" for pid in field) or not any(P[p].pos == "FB" for p in everyone))

    def test_recounting_a_position_from_the_saved_ballots_gives_the_saved_result(self):
        rec = self.vote["conferences"]["Eastern"]
        seed = f"{self.world.creator.seed}|bowl|{self.vote['year']}"
        for pos in ("QB", "WR", "K"):
            ballots = [rec["ballots"][v][pos] for v in self.vote["voters"]]
            again = stv.count(ballots, sorted(ballots[0]), bowl.POSITIONAL[pos], f"{seed}|Eastern|{pos}")
            self.assertEqual(again, rec["positional"][pos])
        ballots = [rec["ballots"][v]["discretionary"] for v in self.vote["voters"]]
        self.assertEqual(stv.count(ballots, sorted(ballots[0]), 5, f"{seed}|Eastern|discretionary"), rec["discretionary"])

    def test_the_record_is_plain_values(self):
        json.dumps(self.vote)

    # ---- the lock
    def test_with_nobody_hurt_the_roster_is_the_vote_exactly(self):
        w = self.variant()
        for conf, rec in w.bowl_vote["conferences"].items():
            f = bowl.lock(w, conf)
            want = [pid for pos in bowl.CANONICAL if pos in rec["positional"] for pid in rec["positional"][pos]["elected"]] + rec["discretionary"]["elected"]
            self.assertEqual(f["roster"], want)
            self.assertEqual((f["replaced"], f["exceptions"], f["short"], f["validation"]), ([], [], 0, []))

    def test_locking_twice_returns_the_same_record_and_never_touches_the_vote(self):
        w = self.variant()
        before = json.dumps(w.bowl_vote, sort_keys=True)
        a = bowl.lock(w, "Western")
        self.assertIs(bowl.lock(w, "Western"), a)
        self.assertEqual(json.dumps(w.bowl_vote, sort_keys=True), before)

    def test_an_injured_winner_is_replaced_by_the_next_in_the_original_order(self):
        w = self.variant()
        rec = w.bowl_vote["conferences"]["Western"]
        res = rec["positional"]["WR"]
        hurt = res["elected"][1]
        w.L.players[hurt].out_weeks = 3
        f = bowl.lock(w, "Western")
        self.assertNotIn(hurt, f["roster"])
        first_left = next(q for q in res["order"] if q not in res["elected"])
        self.assertEqual(f["replaced"], [{"place": f["replaced"][0]["place"], "kind": "pos", "position": "WR", "out": hurt, "in": first_left,
                                          "why": "medically unavailable"}])
        self.assertEqual(w.L.players[first_left].pos, "WR")
        self.assertEqual((len(f["roster"]), len(set(f["roster"])), f["exceptions"], f["validation"]), (53, 53, [], []))

    def test_replacements_skip_those_already_on_the_roster_and_those_hurt_too(self):
        w = self.variant()
        res = w.bowl_vote["conferences"]["Western"]["positional"]["CB"]
        order = res["order"]
        for pid in res["elected"][:2] + [order[5]]:        # two winners and the first in line after the winners
            w.L.players[pid].out_weeks = 2
        f = bowl.lock(w, "Western")
        got = [r["in"] for r in f["replaced"] if r["position"] == "CB"]
        on_the_roster = set(res["elected"][2:]) | set(w.bowl_vote["conferences"]["Western"]["discretionary"]["elected"])
        want = [q for q in order[6:] if q not in on_the_roster][:2]           # skips anyone already on the roster by another route
        self.assertEqual(got, want)
        self.assertFalse(set(got) & set(res["elected"]))

    def test_a_player_who_left_is_replaced_like_one_who_is_hurt(self):
        w = self.variant()
        res = w.bowl_vote["conferences"]["Eastern"]["positional"]["QB"]
        gone = res["elected"][0]
        p = w.L.players[gone]
        w.L.clubs[p.club].roster.remove(gone)
        p.where, p.club = "fa", None
        f = bowl.lock(w, "Eastern")
        self.assertNotIn(gone, f["roster"])
        self.assertEqual(f["replaced"][0]["why"], "left the roster")
        self.assertEqual(len(f["roster"]), 53)

    def test_a_winner_traded_within_the_conference_stays(self):
        w = self.variant()
        rec = w.bowl_vote["conferences"]["Eastern"]
        pid = rec["positional"]["QB"]["elected"][0]
        a = w.L.players[pid].club
        b = next(c for c in rec["clubs"] if c != a)
        w.L.clubs[a].roster.remove(pid)
        w.L.clubs[b].roster.append(pid)
        w.L.players[pid].club = b
        f = bowl.lock(w, "Eastern")
        self.assertIn(pid, f["roster"])
        self.assertEqual(f["replaced"], [])

    def test_a_winner_traded_to_the_other_conference_is_gone(self):
        w = self.variant()
        rec = w.bowl_vote["conferences"]["Eastern"]
        pid = rec["positional"]["QB"]["elected"][0]
        a = w.L.players[pid].club
        other = w.bowl_vote["conferences"]["Western"]["clubs"][0]
        w.L.clubs[a].roster.remove(pid)
        w.L.clubs[other].roster.append(pid)
        w.L.players[pid].club = other
        f = bowl.lock(w, "Eastern")
        self.assertNotIn(pid, f["roster"])
        self.assertEqual(f["replaced"][0]["why"], "left the conference's clubs")

    def test_a_discretionary_winner_is_replaced_from_the_discretionary_order(self):
        w = self.variant()
        d = w.bowl_vote["conferences"]["Western"]["discretionary"]
        w.L.players[d["elected"][2]].out_weeks = 1
        f = bowl.lock(w, "Western")
        r = f["replaced"][0]
        self.assertEqual((r["kind"], r["out"], r["in"]), ("disc", d["elected"][2], d["order"][5]))
        self.assertIn(r["in"], f["roster"])

    def test_an_exhausted_ballot_brings_in_a_new_player_from_the_nearest_position_and_logs_the_exception(self):
        w = self.variant()
        rec = w.bowl_vote["conferences"]["Western"]
        P = w.L.players
        kickers = [pid for c in rec["clubs"] for pid in w.L.clubs[c].roster if P[pid].pos == "K"]
        for pid in kickers:
            P[pid].out_weeks = 4                           # every kicker on the four clubs
        f = bowl.lock(w, "Western")
        self.assertEqual(len(f["exceptions"]), 1)
        e = f["exceptions"][0]
        self.assertEqual(e["short_position"], "K")
        self.assertEqual(e["permanent_position"], "P")      # the first neighbour of K
        self.assertEqual(P[e["player"]].pos, "P")           # she keeps her permanent position
        self.assertEqual(e["why"], "medically unavailable")
        self.assertEqual((len(f["roster"]), len(set(f["roster"]))), (53, 53))
        self.assertEqual(f["validation"], [])               # the short position is excused, nothing else is wrong
        self.assertEqual(sum(1 for pid in f["roster"] if P[pid].pos == "K"), 0)
        self.assertEqual(P[e["player"]].out_weeks, 0)
        lst = bowl.emergency(w, f, lambda q: q.ovr)
        self.assertEqual(lst["K"][0], e["player"])           # named first on the emergency list for K

    def test_the_nearest_position_that_has_somebody_is_used(self):
        w = self.variant()
        rec = w.bowl_vote["conferences"]["Eastern"]
        P = w.L.players
        for c in rec["clubs"]:
            for pid in w.L.clubs[c].roster:
                if P[pid].pos in ("K", "P"):
                    P[pid].out_weeks = 5
        f = bowl.lock(w, "Eastern")
        by_pos = {e["short_position"]: e for e in f["exceptions"]}
        self.assertEqual(set(by_pos), {"K", "P"})
        self.assertEqual({P[e["player"]].pos for e in by_pos.values()}, {"QB"})        # K -> P -> QB, then P -> K -> QB
        self.assertEqual(len({e["player"] for e in f["exceptions"]}), 2)               # two different players
        self.assertEqual(len(set(f["roster"])), 53)

    def test_a_third_halfback_can_be_a_fullback_with_two_halfbacks(self):
        w = self.variant()
        rec = w.bowl_vote["conferences"]["Western"]
        P = w.L.players
        hbs = sorted(pid for c in rec["clubs"] for pid in w.L.clubs[c].roster if P[pid].pos == "HB")
        for pid in hbs[2:]:
            P[pid].out_weeks = 2
        keep = rec["positional"]["HB"]["order"]
        f = bowl.lock(w, "Western")
        self.assertEqual(len(f["roster"]), 53)
        n_hb = sum(1 for pid in f["roster"] if P[pid].pos == "HB")
        self.assertLessEqual(n_hb, 2)
        self.assertTrue(f["validation"] == [] or all("HB" not in x for x in f["validation"]))
        self.assertEqual(len(keep), len(set(keep)))

    def test_when_too_few_are_available_the_roster_plays_short_and_says_so(self):
        w = self.variant()
        rec = w.bowl_vote["conferences"]["Western"]
        P = w.L.players
        pool = [pid for c in rec["clubs"] for pid in w.L.clubs[c].roster]
        for pid in sorted(pool)[:-40]:
            P[pid].out_weeks = 6
        f = bowl.lock(w, "Western")
        self.assertEqual(len(f["roster"]), 40)
        self.assertEqual(len(set(f["roster"])), 40)
        self.assertEqual(f["short"], 13)
        self.assertTrue(any("40 players, not 53" in x for x in f["validation"]))
        self.assertTrue(all(P[pid].out_weeks == 0 for pid in f["roster"]))

    def test_only_players_who_were_on_the_ballot_can_be_chosen(self):
        w = self.variant()
        rec = w.bowl_vote["conferences"]["Western"]
        P = w.L.players
        for pid in rec["positional"]["WR"]["elected"]:
            P[pid].out_weeks = 1
        ballot = {pid for r in rec["positional"].values() for pid in r["order"]} | set(rec["discretionary"]["order"])
        f = bowl.lock(w, "Western")
        self.assertTrue(set(f["roster"]) <= ballot)

    def test_minimums_are_met_by_permanent_position_in_every_ordinary_roster(self):
        w = self.variant()
        P = w.L.players
        for conf in w.bowl_vote["conferences"]:
            f = bowl.lock(w, conf)
            counts = {}
            for pid in f["roster"]:
                counts[P[pid].pos] = counts.get(P[pid].pos, 0) + 1
            for pos, n in MINIMUMS.items():
                self.assertGreaterEqual(counts.get(pos, 0), n, (conf, pos))
            self.assertGreaterEqual(counts.get("HB", 0), 3)


class RingsAndRecord(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.u = Universe(TABLES, seed=1, engine=False, spatial=False)
        run_to(cls.u, "ambassador_bowl")
        cls.w = cls.u.world
        cls.bowl = cls.u.run.bowl

    def test_the_bowl_carries_the_locked_rosters_and_the_winner(self):
        b = self.bowl
        self.assertEqual(set(b["rosters"]), {"Western", "Eastern"})
        self.assertTrue(all(len(r) == 53 for r in b["rosters"].values()))
        self.assertIn(b["winner"], ("Western All-Stars", "Eastern All-Stars"))
        self.assertNotEqual(b["away_score"], b["home_score"])
        self.assertEqual(b["rosters"], {c: f["roster"] for c, f in self.w.bowl_final.items()})

    def test_rings_go_to_the_winning_53_and_the_four_bowl_coaches(self):
        won = self.bowl["winner"].split()[0]
        staff = self.bowl["staff_clubs"][won]
        coaches = {c.identity.id for r, c in self.w.leaders[staff].items() if r in ("hc", "oc", "dc", "stc") and c is not None}
        got = {pid for pid, years in self.w.rings.items() if self.w.L.year in years}
        self.assertEqual(got, set(self.bowl["rosters"][won]) | coaches)
        self.assertEqual(len(coaches), 4)
        lost = "Eastern" if won == "Western" else "Western"
        self.assertFalse(got & (set(self.bowl["rosters"][lost]) - set(self.bowl["rosters"][won])))

    def test_awarding_rings_again_changes_nothing(self):
        n = self.w.stats["rings"]
        snap = {k: set(v) for k, v in self.w.rings.items()}
        won = self.bowl["winner"].split()[0]
        honours.award_rings(self.w, self.w.L.year, won, self.bowl["staff_clubs"][won])
        self.assertEqual(self.w.stats["rings"], n)
        self.assertEqual({k: set(v) for k, v in self.w.rings.items()}, snap)

    def test_the_archive_summary_keeps_the_frozen_ballots_and_the_locked_rosters(self):
        u = self.u
        run_to(u, "year_close")
        rec = u.season_record(u.reports[-1]["result"])
        json.dumps(rec)
        self.assertEqual(len(rec["bowl_vote"]["voters"]), 8)
        self.assertEqual(set(rec["bowl_final"]), {"Western", "Eastern"})
        self.assertEqual(rec["result"]["bowl"]["rosters"], {c: rec["bowl_final"][c]["roster"] for c in rec["bowl_final"]})


class PlayedByTheEngine(unittest.TestCase):
    def test_the_side_that_takes_the_field_is_the_voted_roster_coached_by_the_best_tour_club(self):
        u = Universe(TABLES, seed=2, engine=True, spatial=False)
        run_to(u, "ambassador_bowl")
        sides = u.run.resolver.sides
        for conf in ("Western", "Eastern"):
            sq = sides[f"{conf} All-Stars"]
            self.assertEqual(sorted(sq.cards), sorted(u.world.bowl_final[conf]["roster"]))
            self.assertEqual(len(sq.cards), 53)
            staff = u.run.bowl["staff_clubs"][conf]
            self.assertIs(sq.staff.hc, u.world.leaders[staff].get("hc"))


class HashOrder(unittest.TestCase):
    def test_the_vote_is_the_same_under_different_hash_seeds(self):
        code = r"""
import json, sys
sys.path.insert(0, %r)
from dfl.year import Universe
u = Universe(%r, seed=4, engine=False, spatial=False)
while not (u.fired and u.fired[-1][2] == "tour_round_7"):
    u.step_event()
import hashlib
print(hashlib.sha256(json.dumps(u.world.bowl_vote, sort_keys=True).encode()).hexdigest())
""" % (os.path.join(ROOT, "src"), TABLES)
        procs = []
        for h in (11, 4242):
            env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
            env["PYTHONHASHSEED"] = str(h)
            procs.append(subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env))
        outs = []
        for p in procs:
            o, e = p.communicate()
            self.assertEqual(p.returncode, 0, e[-1500:])
            outs.append(o.strip())
        self.assertEqual(len(outs[0]), 64)
        self.assertEqual(outs[0], outs[1])


if __name__ == "__main__":
    unittest.main()
