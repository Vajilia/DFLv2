"""Per-player production (Edition 8.30): what each play is worth and who is credited with it.

Value of a play is expected points added (EPA): the points actually scored in it plus the expected points of the state it leaves, less
the expected points of the state it began in, from the offence's side. Win probability added (WPA) is the same swing in the chance of
winning. Each play's EPA and WPA are shared among the players on the field by role (SHARES.csv); the defence receives the opposite
sign. Nothing here draws a random number or changes any state of the game, so recording cannot change a result (tested against a
baseline digest of 240 games). All numbers are data (data/analytics), placeholders; section 15's season refit of the expected-points
table is not built.
"""
import csv
import math
import os

_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "data", "analytics")
_CACHE = {}

OL = ("LT", "LG", "C", "RG", "RT")
DL = ("LE", "RE", "DT")
LB = ("ROLB", "MLB", "LOLB")
DB = ("CB", "FS", "SS")
QUARTER = 900


def tables():
    if not _CACHE:
        def rd(name):
            with open(os.path.join(_DATA, name), newline="") as fh:
                return list(csv.DictReader(fh))
        _CACHE["ep"] = [(float(r["yl"]), float(r["ep"])) for r in rd("EP.csv")]
        _CACHE["a"] = {r["key"]: float(r["value"]) for r in rd("ANALYTICS.csv")}
        sh = {}
        for r in rd("SHARES.csv"):
            sh.setdefault((r["play"], r["side"]), {})[r["group"]] = float(r["share"])
        _CACHE["shares"] = sh
    return _CACHE


def ep(yl, down=1, dist=10):
    """Expected points for the team with the ball."""
    T = tables()
    pts = T["ep"]
    yl = max(pts[0][0], min(pts[-1][0], yl))
    base = pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= yl <= x1:
            base = y0 + (y1 - y0) * (yl - x0) / (x1 - x0)
            break
    a = T["a"]
    return base - a["down_cost"] * (down - 1) - a["dist_cost"] * (dist - 10)


def wp(margin, secs):
    """Chance the team ahead by `margin` wins with `secs` left (normal approximation)."""
    a = tables()["a"]
    sd = a["game_sigma"] * math.sqrt((max(secs, 0) + a["wp_floor_secs"]) / 3600.0)
    return 0.5 * (1 + math.erf(margin / (sd * math.sqrt(2))))


def secs_left(period, t):
    return (4 - period) * QUARTER + t if period <= 4 else t


def _split(total, cards):
    return [(c, total / len(cards)) for c in cards] if cards else []


def _groups(units, side):
    """The players on the field by role group."""
    lst = units["off"] if side == "off" else units["def"]
    pos = lambda ps: [c for c in lst if c.identity.pos in ps]
    return {"ol": pos(OL), "dl": pos(DL), "lb": pos(LB), "db": pos(DB), "te": pos(("TE",)), "wr": pos(("WR",)), "qb": pos(("QB",)),
            "k": pos(("K",)), "p": pos(("P",)), "ls": pos(("LS",)), "all": lst}


class State:
    """What the recorder needs of a game before a play: spot, down, distance, margin for the offence, clock."""
    __slots__ = ("yl", "down", "dist", "margin", "secs")

    def __init__(self, game, k):
        self.yl, self.down, self.dist = game.yl, game.down, game.dist
        self.margin = game.score[k] - game.score[1 - k]
        self.secs = secs_left(game.period, game.t)


def after_value(game, k):
    """Value to the offence k of the state the play left."""
    nxt = game.next
    if nxt is None:                                    # the same team keeps the ball
        return ep(game.yl, game.down, game.dist)
    if nxt[0] == "ball":
        v = ep(nxt[2], 1, 10)
        return v if nxt[1] == k else -v
    v = ep(tables()["a"]["kickoff_start"], 1, 10)      # a kickoff follows a score: the team that kicks gives the ball away
    return -v if nxt[1] == k else v


def swing(game, k, pre):
    """(EPA, WPA) for offence k of the play that has just been played, given the state before it."""
    margin_after = game.score[k] - game.score[1 - k]
    v_after = after_value(game, k)
    before = ep(pre.yl, pre.down, pre.dist)
    epa = (margin_after - pre.margin) + v_after - before
    return epa, wp(margin_after + v_after, secs_left(game.period, game.t)) - wp(pre.margin + before, pre.secs)


def _credit(prod, card, ptype, epa, wpa, n=1):
    d = prod.setdefault(card.identity.id, {}).setdefault(ptype, [0, 0.0, 0.0])
    d[0] += n
    d[1] += epa
    d[2] += wpa


def credit_scrimmage(game, k, o, pre, uo, ud):
    """Share a pass, run, scramble or sack among the players on the field."""
    T = tables()["shares"]
    epa, wpa = swing(game, k, pre)
    kind = o["kind"]
    play = {"run": "run", "scramble": "run", "sack": "sack"}.get(kind, "pass")
    ptype = "pass" if play in ("pass", "sack") else "run"
    go, gd = _groups(uo, "off"), _groups(ud, "def")
    prod = game.prod
    t = game.team[k].setdefault(ptype, [0, 0.0])
    t[0] += 1
    t[1] += epa
    for c in go["all"] + gd["all"]:
        _credit(prod, c, ptype, 0.0, 0.0)                                  # an opportunity for everyone on the field
    off = dict(T[(play, "off")])
    if play == "pass":
        target = o.get("target")
        rest = [c for c in go["wr"] + go["te"] + [uo["hb"]] if target is None or c.identity.id != target.identity.id]
        who = {"qb": go["qb"], "ol": go["ol"], "rest": rest, "target": [target] if target is not None else []}
        if target is None:
            off["qb"] += off.pop("target", 0.0)
    elif play == "run":
        carrier = o.get("carrier")
        who = {"carrier": [carrier] if carrier is not None else [], "ol": go["ol"], "te": go["te"], "wr": go["wr"]}
    else:
        who = {"qb": go["qb"], "ol": go["ol"]}
    for grp, share in off.items():
        for c, part in _split(share, who.get(grp, [])):
            _credit(prod, c, ptype, epa * part, wpa * part, 0)
    for grp, share in T[(play, "def")].items():
        for c, part in _split(share, gd[grp]):
            _credit(prod, c, ptype, -epa * part, -wpa * part, 0)


def credit_kick(game, k, pre, kind):
    """Share a field goal or punt among the kicker, punter and long snapper of squad k."""
    T = tables()["shares"]
    epa, wpa = swing(game, k, pre)
    sq = game.sq[k]
    g = {"k": sq.available("K")[:1], "p": sq.available("P")[:1], "ls": sq.available("LS")[:1]}
    ptype = "kick" if kind == "fg" else "punt"
    t = game.team[k].setdefault(ptype, [0, 0.0])
    t[0] += 1
    t[1] += epa
    for grp, share in T[(kind, "off")].items():
        for c, part in _split(share, g.get(grp, [])):
            _credit(game.prod, c, ptype, epa * part, wpa * part, 1)
