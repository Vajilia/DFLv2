#!/usr/bin/env python3
"""DFL standings module (section 3): records, tiebreak cascade, playoff seeding and bracket, exile and seed
hand-off, Coronation venue eligibility. Pure functions, deterministic given a seed string. No file I/O.

Data shapes
  teams : {club: (conference, division)}   active clubs only
  games : [{"away": club, "home": club, "away_score": int, "home_score": int}, ...]   regular season only
"""
import random
from fractions import Fraction
from itertools import groupby


def pct(w, l):
    """Win % = W / (W + L); ties never enter. Zero decisions counts as 0."""
    return Fraction(w, w + l) if w + l else Fraction(0)


class Rec:
    __slots__ = ("w", "l", "t", "gp", "pf", "pa", "dw", "dl", "cw", "cl", "opps", "beaten")

    def __init__(self):
        self.w = self.l = self.t = self.gp = self.pf = self.pa = 0
        self.dw = self.dl = self.cw = self.cl = 0
        self.opps, self.beaten = [], []

    @property
    def pd(self):
        return self.pf - self.pa


def tally(teams, games):
    """A tied game is NOT PLAYED for standings (no W/L, no division/conference record, not in opponent lists)
    but IS PLAYED for statistics (gp, pf, pa and so point differential)."""
    rec = {c: Rec() for c in teams}
    for g in games:
        a, h = g["away"], g["home"]
        sa, sh = int(g["away_score"]), int(g["home_score"])
        for me, opp, pf, pa in ((a, h, sa, sh), (h, a, sh, sa)):
            r = rec[me]
            r.gp += 1
            r.pf += pf
            r.pa += pa
            if pf == pa:
                r.t += 1
                continue
            same_div = teams[me] == teams[opp]
            same_conf = teams[me][0] == teams[opp][0]
            r.opps.append(opp)
            if pf > pa:
                r.w += 1
                r.beaten.append(opp)
                r.dw += same_div
                r.cw += same_conf
            else:
                r.l += 1
                r.dl += same_div
                r.cl += same_conf
    return rec


def _opp_pct(opps, rec):
    w = sum(rec[o].w for o in opps)
    l = sum(rec[o].l for o in opps)
    return pct(w, l)


def rank(clubs, rec, teams, seed=""):
    """Order clubs best-first by the cascade: (1) wins (2) win % (3) division wins (4) division win %
    (5) conference wins (6) conference win % (7) strength of schedule (8) point differential
    (9) strength of victory, then a seeded draw. Steps 3-4 only if every tied club shares a division, 5-6 only if
    every tied club shares a conference. A tied group is sorted once by the whole cascade (no reduction)."""
    clubs = list(clubs)
    rng = random.Random(seed)
    draw = {c: rng.random() for c in sorted(clubs)}

    def base(c):
        return rec[c].w, pct(rec[c].w, rec[c].l)

    out = []
    for _, grp in groupby(sorted(clubs, key=base, reverse=True), key=base):
        grp = list(grp)
        if len(grp) > 1:
            same_div = len({teams[c] for c in grp}) == 1
            same_conf = len({teams[c][0] for c in grp}) == 1

            def key(c):
                r = rec[c]
                k = []
                if same_div:
                    k += [r.dw, pct(r.dw, r.dl)]
                if same_conf:
                    k += [r.cw, pct(r.cw, r.cl)]
                k += [_opp_pct(r.opps, rec), r.pd, _opp_pct(r.beaten, rec), draw[c]]
                return tuple(k)

            grp.sort(key=key, reverse=True)
        out += grp
    return out


def divisions(teams):
    d = {}
    for c, dv in teams.items():
        d.setdefault(dv, []).append(c)
    return d


def division_orders(teams, rec, seed=""):
    """{(conf, div): [rank 1 .. rank 5]}"""
    return {dv: rank(members, rec, teams, f"{seed}|{dv}") for dv, members in sorted(divisions(teams).items())}


def conference_seeds(conf, teams, rec, div_orders, seed=""):
    """Seven playoff clubs, seed 1 first: four division winners ordered by the cascade, then three wild cards."""
    winners = [o[0] for dv, o in sorted(div_orders.items()) if dv[0] == conf]
    ordered = rank(winners, rec, teams, f"{seed}|{conf}|winners")
    others = [c for c, dv in teams.items() if dv[0] == conf and c not in winners]
    wild = rank(others, rec, teams, f"{seed}|{conf}|wild")[:3]
    return ordered + wild


def wild_card_pairs():
    """Seed numbers; seed 1 has a bye. The first number is the higher seed and hosts."""
    return [(2, 7), (3, 6), (4, 5)]


def divisional_pairs(survivors):
    """survivors: the four seed numbers left after the wild card round. Seed 1 (the best) plays the lowest
    remaining seed; the other two meet."""
    s = sorted(survivors)
    assert len(s) == 4
    return [(s[0], s[3]), (s[1], s[2])]


def host(a, b):
    """Higher seed (lower number) is at home."""
    return min(a, b)


def season_outcome(teams, games, seed=""):
    """Everything the next season needs from this season's results."""
    rec = tally(teams, games)
    orders = division_orders(teams, rec, seed)
    return {
        "records": rec,
        "division_orders": orders,
        "seeds": {c: conference_seeds(c, teams, rec, orders, seed) for c in sorted({dv[0] for dv in teams.values()})},
        "exiled": {dv: o[-1] for dv, o in orders.items()},
    }


def next_division_state(order, ambassador):
    """Ranks 1-4 keep seeds 1-4; the last place is exiled; the returning ambassador takes seed 5."""
    assert len(order) == 5
    return {"seeds": {i + 1: c for i, c in enumerate(order[:4])} | {5: ambassador}, "ambassador": order[4]}


def coronation_eligible(history, venues, year, window=40):
    """No venue hosts twice inside a rolling window: anything that hosted in the previous window-1 years is out."""
    recent = {v for y, v in history.items() if year - window < y < year}
    return sorted(set(venues) - recent)


def coronation_pick(history, venues, year, seed=""):
    """Fictitious fan vote: a seeded random draw among the eligible venues."""
    el = coronation_eligible(history, venues, year)
    assert el, "no eligible venue"
    return random.Random(f"{seed}|{year}").choice(el)


def random_results(games, rng, strength=None, tie_rate=0.01):
    """Test utility: attach plausible random scores to schedule rows. strength: {club: float}."""
    out = []
    for g in games:
        sa = (strength or {}).get(g["away"], 0.0)
        sh = (strength or {}).get(g["home"], 0.0)
        if rng.random() < tie_rate:
            s = rng.randint(3, 30)
            pa = ph = s
        else:
            pa = max(0, round(rng.gauss(21 + 4 * sa, 8)))
            ph = max(0, round(rng.gauss(23 + 4 * sh, 8)))
            while pa == ph:
                ph += rng.choice([-3, 3, 7])
                ph = max(0, ph)
        out.append(dict(g, away_score=pa, home_score=ph))
    return out
