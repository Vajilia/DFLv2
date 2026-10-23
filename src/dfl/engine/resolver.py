"""EngineResolver: plugs the game engine into the season loop (`dfl.resolver.Resolver`).

It holds one `Squad` per club (built from character cards), plays each scheduled game with `play_game`, remembers injuries from game to
game (a player out for N weeks misses the club's next N games), and builds an All-Star squad from the best players of a set of clubs
for the Ambassador Bowl. Between seasons `new_season` can age and develop the cards (a PLACEHOLDER curve; real development is data).
"""
import random

from .game import play_game
from .squad import Squad, overall


class EngineResolver:
    def __init__(self, squads):
        self.squads = dict(squads)
        self.games_out = {}                    # club -> {card id: games still to miss}
        self.sides = {}                        # All-Star side name -> composite squad
        self.injuries_seen = 0
        self.games_played = 0

    # ---- Resolver interface
    def start_season(self, clubs, season, rng):
        missing = [c for c in clubs if c not in self.squads]
        if missing:
            raise KeyError(f"no squad for {missing[:3]}")
        self.sides.clear()
        for c in clubs:
            self.games_out[c] = {}
            self.squads[c].out = set()
            self.squads[c].new_injuries.clear()
            self.squads[c]._units = None

    def all_star_side(self, name, clubs):
        """A composite squad: the best player at each position across `clubs` (the people layer will pick All-Stars by vote)."""
        best = {}
        for c in clubs:
            for card in self.squads[c].cards.values():
                best.setdefault(card.identity.pos, []).append(card)
        cards = []
        for pos, lst in best.items():
            cards += sorted(lst, key=lambda x: (-overall(x), x.identity.id))[:4]
        self.sides[name] = Squad(name, cards)

    def squad(self, name):
        return self.sides.get(name) or self.squads[name]

    def resolve(self, game, rng, decisive):
        home, away = self.squad(game["home"]), self.squad(game["away"])
        for s in (home, away):
            out = self.games_out.get(s.cid, {})
            s.out = {i for i, n in out.items() if n > 0}
            s.new_injuries.clear()
            s._units = None
        res = play_game(home, away, random.Random(rng.random()), decisive)
        for s in (home, away):
            if s.cid in self.sides:
                continue
            out = self.games_out.setdefault(s.cid, {})
            for i in list(out):
                out[i] -= 1
                if out[i] <= 0:
                    del out[i]
            for i, weeks in s.new_injuries:
                out[i] = max(out.get(i, 0), weeks)
            self.injuries_seen += len(s.new_injuries)
        self.games_played += 1
        return res.score[1], res.score[0]            # (away, home)
