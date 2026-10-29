"""LedgerEngine: the game engine reading its squads from the living ledger (`ledger.world.World`).

A club's squad for a game is exactly the players the ledger dresses for it (the game-day active list, practice-squad elevations and
emergency players included), as cards. Injuries the engine produces go straight back to the ledger as `out_weeks`, so the weekly
management (injured reserve, replacements, waivers) reacts to them. Nothing here is a second source of truth.
"""
import random

from ..ledger import coaching, gameday
from .game import play_game
from .squad import Squad, Staff


class LedgerEngine:
    def __init__(self, world, spatial=False):
        self.w = world
        self.spatial = spatial                 # play every snap with 22 players on the field (Edition 8.52: the league's default engine)
        self.sides = {}
        self.injuries_seen = 0
        self.games_played = 0

    # ---- Resolver interface
    def start_season(self, clubs, season, rng):
        missing = [c for c in clubs if c not in self.w.L.clubs]
        if missing:
            raise KeyError(f"no ledger club for {missing[:3]}")
        self.sides.clear()

    def staff(self, cid):
        lead = self.w.leaders.get(cid, {})
        return Staff(hc=lead.get("hc"), oc=lead.get("oc"), dc=lead.get("dc"), stc=lead.get("stc"))

    def club_squad(self, cid):
        w = self.w
        gd = w.active.get(cid)
        if gd is None or w.active_week.get(cid) != w.L.week or w.L.phase != "season":
            gd = gameday.build(w.L, cid, elevate=False, judge=lambda q: coaching.judged(w, cid, q))
        cards = [w.cards[pid] for pid in gd.active]
        seen = {pid: coaching.judged(w, cid, w.L.players[pid]) for pid in gd.active}      # her coaches choose starters by what they judge, not the truth
        return Squad(cid, cards, self.staff(cid), seen)

    def all_star_side(self, name, clubs):
        """A composite squad: the best healthy player at each position across `clubs` (the people layer will pick All-Stars by vote)."""
        w = self.w
        best = {}
        for c in clubs:
            for pid in w.L.clubs[c].roster:
                if w.L.players[pid].out_weeks == 0:
                    best.setdefault(w.L.players[pid].pos, []).append(pid)
        cards = []
        for pos, pids in best.items():
            cards += [w.cards[i] for i in sorted(pids, key=lambda i: (-w.rating[i], i))[:4]]
        self.sides[name] = Squad(name, cards, ratings=w.rating)

    def squad(self, name):
        return self.sides.get(name) or self.club_squad(name)

    def resolve(self, game, rng, decisive):
        home, away = self.squad(game["home"]), self.squad(game["away"])
        res = play_game(home, away, random.Random(rng.random()), decisive, curve=self.w.ep_curve, spatial=self.spatial)
        for s in (home, away):
            if s.cid in self.sides:
                continue
            for pid, weeks in s.new_injuries:
                self.w.injure(pid, weeks)
            self.injuries_seen += len(s.new_injuries)
        self.w.record_game(res, home, away)
        self.games_played += 1
        return res.score[1], res.score[0]            # (away, home)
