"""LedgerEngine: the game engine reading its squads from the living ledger (`ledger.world.World`).

A club's squad for a game is exactly the players the ledger dresses for it (the game-day active list, practice-squad elevations and
emergency players included), as cards. Injuries the engine produces go straight back to the ledger as `out_weeks`, so the weekly
management (injured reserve, replacements, waivers) reacts to them. Nothing here is a second source of truth.
"""
import random

from ..ledger import bowl, coaching, gameday
from .game import play_game
from .squad import Squad, Staff


class LedgerEngine:
    def __init__(self, world, spatial=False):
        self.w = world
        self.spatial = spatial                 # play every snap with 22 players on the field (Edition 8.52: the league's default engine)
        self.sides = {}
        self.injuries_seen = 0
        self.games_played = 0
        self.archive = None                    # a list (set by Universe) to which each game's as-played record is appended; None = off
        self.season = None

    # ---- Resolver interface
    def start_season(self, clubs, season, rng):
        missing = [c for c in clubs if c not in self.w.L.clubs]
        if missing:
            raise KeyError(f"no ledger club for {missing[:3]}")
        self.sides.clear()
        self.season = season

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
        return Squad(cid, cards, self.staff(cid), seen, emergency=gd.emergency)

    def all_star_side(self, name, clubs):
        """The Bowl side of one conference. With the ballots cast (after the last Tour round) it is the locked, voted roster of 53 (ledger/bowl.py),
        coached by the conference's best Tour club (`clubs[0]`: head coach and three coordinators), who judge the starters. Without a vote (a bare
        league with no Tour ballots) it falls back to the composite of the best healthy players at each position."""
        w = self.w
        conf = name.split()[0]
        vote = w.bowl_vote
        if (vote is not None and vote["year"] == w.L.year and conf in vote["conferences"]
                and set(clubs) == set(vote["conferences"][conf]["clubs"])):          # the ballots were cast for exactly these four clubs
            final = bowl.lock(w, conf)
            est = lambda q: coaching.judged(w, clubs[0], q)
            P = w.L.players
            seen = {pid: est(P[pid]) for pid in final["roster"]}
            final["staff_club"] = clubs[0]
            self.sides[name] = Squad(name, [w.cards[pid] for pid in final["roster"]], self.staff(clubs[0]), seen,
                                     emergency=bowl.emergency(w, final, est))
            return
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
        keep = self.archive is not None            # the permanent record: the play table, and every snap's trace when the 22-player engine plays
        res = play_game(home, away, random.Random(rng.random()), decisive, log=keep, curve=self.w.ep_curve, spatial=self.spatial,
                        trace=keep and self.spatial, neutral=bool(game.get("neutral")))
        for s in (home, away):
            if s.cid in self.sides:
                continue
            for pid, weeks in s.new_injuries:
                self.w.injure(pid, weeks)
            self.injuries_seen += len(s.new_injuries)
        self.w.record_game(res, home, away)
        self.games_played += 1
        if keep:
            self.archive.append(self.game_record(game, res))
        return res.score[1], res.score[0]            # (away, home)

    def game_record(self, game, res):
        """What the archive keeps of one game, as played (archive.py encodes it)."""
        gid = f"S{self.season:03d}/{game['id']}"
        return {"type": "game", "gid": gid, "season": self.season, "kind": game.get("kind", "game"), "home": res.home, "away": res.away,
                "neutral": bool(game.get("neutral")), "date": game.get("date"), "kickoff": game.get("kickoff"), "engine": "spatial" if self.spatial else "play", "rule_version": res.rule_version,
                "score": list(res.score), "periods": res.periods, "tie": res.tie, "winner": res.winner, "stats": res.stats,
                "players": res.players, "injuries": res.injuries, "pen_types": res.pen_types, "table": res.table}
