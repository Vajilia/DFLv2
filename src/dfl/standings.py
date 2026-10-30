#!/usr/bin/env python3
"""DFL standings module (section 3): records, tiebreak cascade, playoff seeding and bracket, exile and seed
hand-off, and the Week 4 designation of the Coronation's nominal home conference. Pure functions, deterministic given a seed string.
No file I/O.

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


def priority(seed, club):
    """The club's draw priority for the season: one number per club, fixed by the season's seed and her name alone.
    It does not depend on which other clubs are being ranked or why (division, wild card, Tour, draft), so a tie that is
    otherwise identical is broken the same way in every scope. Higher ranks higher; the draft reverses the ranked list."""
    return random.Random(f"{seed}|priority|{club}").random()


CASCADE = ("wins", "win_pct", "division_wins", "division_win_pct", "conference_wins", "conference_win_pct",
           "point_differential", "strength_of_schedule", "strength_of_victory", "seeded_draw")


def rank(clubs, rec, teams, seed=""):
    """Order clubs best-first by the league-wide cascade (section 3), the same wherever clubs are ranked (division, wild card, Tour, draft):
    (1) wins (2) win % (3) division wins (4) division win % (5) conference wins (6) conference win % (7) point differential
    (8) strength of schedule (9) strength of victory (10) the club's season draw priority (`priority`; the same in every scope).
    Steps 3-4 apply only if every tied club shares a division, 5-6 only if every tied club shares a conference; inapplicable steps are
    skipped. A tied group is sorted once by the whole cascade (no reduction)."""
    clubs = list(clubs)
    draw = {c: priority(seed, c) for c in clubs}

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
                k += [r.pd, _opp_pct(r.opps, rec), _opp_pct(r.beaten, rec), draw[c]]
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
    return {dv: rank(members, rec, teams, seed) for dv, members in sorted(divisions(teams).items())}


PLAYOFF_CLUBS_PER_CONFERENCE = 6
WILD_CARDS = 2


def conference_seeds(conf, teams, rec, div_orders, seed=""):
    """Six playoff clubs, seed 1 first: four division winners ordered by the cascade, then two wild cards
    (never a division's last-place club, which is exiled)."""
    winners = [o[0] for dv, o in sorted(div_orders.items()) if dv[0] == conf]
    ordered = rank(winners, rec, teams, seed)
    exiled = {o[-1] for dv, o in div_orders.items() if dv[0] == conf}     # a last-place club cannot take a wild card
    others = [c for c, dv in teams.items() if dv[0] == conf and c not in winners and c not in exiled]
    wild = rank(others, rec, teams, seed)[:WILD_CARDS]
    return ordered + wild


def wild_card_pairs():
    """Seed numbers; seeds 1 and 2 have a bye. The first number is the higher seed and hosts: 3 hosts 6, 4 hosts 5."""
    return [(3, 6), (4, 5)]


def divisional_pairs(survivors):
    """survivors: the four seed numbers left after the wild card round (always 1, 2 and the two wild card winners). The round is reseeded:
    seed 1 hosts the lowest surviving seed and seed 2 hosts the other survivor."""
    s = sorted(survivors)
    assert len(s) == 4 and s[:2] == [1, 2], s
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


CORONATION_AGGREGATE_WEEKS = 4


def conference_designation(teams, games, seed="", through_week=CORONATION_AGGREGATE_WEEKS):
    """Which conference's finalist is the Diamond Coronation's nominal home (section 3). Pool every interconference game of numbered
    weeks 1..`through_week` and treat each conference as one aggregate team, then rank the two by the league cascade, skipping the steps
    that cannot apply to two conferences (division and conference records):
      wins, win %, point differential, strength of schedule, strength of victory, then a seeded draw.
    Only games of the first `through_week` numbered weeks are read, so nothing later can change the answer. Ties are treated as
    everywhere else: not played for W/L or the opponent lists, but their points count toward point differential.
    Aggregate strength of schedule = combined win % of the opponents the aggregate played, one entry per decided pooled game;
    strength of victory = the same over the opponents it beat. Each opponent's record is her full regular-season record through
    `through_week` (all her games, not only the interconference ones). Returns the saved decision record (plain values only)."""
    early = [g for g in games if int(g["week"]) <= through_week]
    rec = tally(teams, early)
    confs = sorted({dv[0] for dv in teams.values()})
    assert len(confs) == 2, confs
    agg = {c: Rec() for c in confs}
    pooled = 0
    for g in early:
        a, h = g["away"], g["home"]
        if teams[a][0] == teams[h][0]:
            continue
        pooled += 1
        sa, sh = int(g["away_score"]), int(g["home_score"])
        for me, opp, pf, pa in ((a, h, sa, sh), (h, a, sh, sa)):
            r = agg[teams[me][0]]
            r.gp += 1
            r.pf += pf
            r.pa += pa
            if pf == pa:
                r.t += 1
                continue
            r.opps.append(opp)
            if pf > pa:
                r.w += 1
                r.beaten.append(opp)
            else:
                r.l += 1
    draw = {c: random.Random(f"{seed}|coronation-home|{c}").random() for c in confs}
    steps = (("wins", lambda c: agg[c].w), ("win_pct", lambda c: pct(agg[c].w, agg[c].l)), ("point_differential", lambda c: agg[c].pd),
             ("strength_of_schedule", lambda c: _opp_pct(agg[c].opps, rec)), ("strength_of_victory", lambda c: _opp_pct(agg[c].beaten, rec)),
             ("seeded_draw", lambda c: draw[c]))
    decided_by, home = "seeded_draw", None
    for name, f in steps:
        a, b = (f(c) for c in confs)
        if a != b:
            decided_by, home = name, confs[0] if a > b else confs[1]
            break
    if home is None:                                    # unreachable: the draw values differ unless the seed strings collide
        home = confs[0]
    show = lambda x: str(x) if isinstance(x, Fraction) else x
    return {"through_week": through_week, "interconference_games": pooled, "home_conference": home, "decided_by": decided_by,
            "skipped_steps": ["division_wins", "division_win_pct", "conference_wins", "conference_win_pct"],
            "conferences": {c: {"w": agg[c].w, "l": agg[c].l, "t": agg[c].t, "pf": agg[c].pf, "pa": agg[c].pa, "pd": agg[c].pd,
                                "win_pct": show(pct(agg[c].w, agg[c].l)), "sos": show(_opp_pct(agg[c].opps, rec)),
                                "sov": show(_opp_pct(agg[c].beaten, rec)), "draw": draw[c]} for c in confs}}


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
