"""Game resolvers.  A resolver turns one scheduled game into a score.

`Resolver` is the seam between the league layer and whatever decides games.  `PlaceholderResolver` is a
stand-in (club strengths plus noise); it can exercise structure, standings and rotation but it is NOT evidence
for scoring realism or tie frequency, which belong to the real engine.
"""
from typing import Protocol


class Resolver(Protocol):
    def start_season(self, clubs, season, rng): ...

    def resolve(self, game, rng, decisive):
        """-> (away_score, home_score). decisive=True forbids a tie (playoffs, Coronation)."""


class PlaceholderResolver:
    def __init__(self, tie_rate=0.004, home_edge=1.5, persistence=0.7, spread=0.6):
        self.tie_rate, self.home_edge, self.persistence, self.spread = tie_rate, home_edge, persistence, spread
        self.strength = {}

    def start_season(self, clubs, season, rng):
        # strengths drift: mean-reverting random walk; clubs new to the active list start at 0
        self.strength = {c: self.persistence * self.strength.get(c, 0.0) + rng.gauss(0, self.spread) for c in clubs}

    def resolve(self, game, rng, decisive):
        sa, sh = self.strength.get(game["away"], 0.0), self.strength.get(game["home"], 0.0)
        if not decisive and rng.random() < self.tie_rate:
            s = rng.randint(3, 30)
            return s, s
        a = max(0, round(rng.gauss(21 + 4 * sa, 8)))
        h = max(0, round(rng.gauss(21 + self.home_edge + 4 * sh, 8)))
        while a == h:
            h = max(0, h + rng.choice([-3, 3, 7]))
        return a, h
