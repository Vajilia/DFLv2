"""Lining up: players on the field from the squad, offensive formation, defensive front and shell, and the assignments (Edition 8.48)."""
import math

from .core import (COVERAGES, DEF_ROLES, FIELD_W, FORMATIONS, HASH_L, HASH_R, MID, OFF_ROLES, OL_Y, PASSES, ROUTES, RUNS, S, Prof)

# defensive roles that are rushers in a base rush, in order of the extra rushers taken after them
EXTRA_RUSHERS = ("lb_l", "lb_r", "mlb", "ss", "fs")


def clampy(y):
    return max(2.2, min(FIELD_W - 2.2, y))


class Pl:
    """One player on the field."""
    __slots__ = ("i", "side", "role", "prof", "state", "wps", "wpi", "speed", "mark", "zone", "lane", "delay", "stun", "eng", "aux", "ax", "ay",
                 "read_t", "cd", "tkl", "block_dir", "route", "front", "cush")

    def __init__(self, i, side, role, prof):
        self.i, self.side, self.role, self.prof = i, side, role, prof
        self.state = "idle"
        self.wps, self.wpi, self.speed = [], 0, 1.0
        self.mark = None          # the opponent this player is blocking or covering (index)
        self.zone = None          # (x, y, half-width) landmark of a zone defender
        self.lane = None          # a rusher's lane (x, y)
        self.delay = 0.0          # seconds before the player moves (release off the line, reads)
        self.stun = 0.0
        self.eng = None           # the engagement this player is in
        self.aux = None
        self.ax = self.ay = 0.0
        self.read_t = 0.0
        self.cd = 0.0             # tackle cooldown
        self.tkl = 0
        self.block_dir = (1.0, 0.0)
        self.route = None
        self.front = 0.0
        self.cush = 0.0


def build_offense(sim, cards, formation, mirror):
    """Place the eleven on the offense in a formation. `cards` maps role -> card."""
    f = FORMATIONS[formation]
    los, by = sim.los, sim.by
    sgn = -1.0 if mirror else 1.0
    out = []
    for role in OFF_ROLES:
        card = cards[role]
        p = Prof(card, sim.fatigue.get(card.identity.id, 1.0))
        if role in OL_Y:
            x, y = los - 0.5, by + OL_Y[role]
        else:
            dx, dy = f[role]
            x, y = los + dx, clampy(by + sgn * dy)
            if role in ("x", "z", "s", "te", "hb") and dx > -0.6:
                x = min(x, los - 0.3)                                # on the line a receiver stands just behind it
        out.append((role, p, x, y))
    return out


def defender_cards(squad_units, pkg="base"):
    """Map the defensive roles to cards. The squad lists defenders in lineup order: LE, RE, DT, DT, ROLB, MLB, LOLB, CB, CB, FS, SS."""
    d = squad_units["def"]
    pos = {}
    for c in d:
        pos.setdefault(c.identity.pos, []).append(c)
    cards = {}
    le, re_ = pos.get("LE", [None])[0], pos.get("RE", [None])[0]
    dts = pos.get("DT", [])
    lbs = {k: pos.get(k, [None])[0] for k in ("ROLB", "MLB", "LOLB")}
    cbs = pos.get("CB", [])
    cards["de_r"] = le                                                # the defense's left end lines up on the offense's right
    cards["de_l"] = re_
    cards["dt_l"] = dts[0] if dts else None
    cards["dt_r"] = dts[1] if len(dts) > 1 else None
    cards["lb_l"] = lbs["ROLB"]                                       # the defense's right backer is on the offense's left
    cards["mlb"] = lbs["MLB"]
    cards["lb_r"] = lbs["LOLB"]
    cards["cb_l"] = cbs[0] if cbs else None
    cards["cb_r"] = cbs[1] if len(cbs) > 1 else None
    cards["fs"] = pos.get("FS", [None])[0]
    cards["ss"] = pos.get("SS", [None])[0]
    spare = [c for c in d if c not in cards.values()]
    for r in DEF_ROLES:                                               # a short position is filled by the leftover players
        if cards.get(r) is None:
            cards[r] = spare.pop(0) if spare else d[0]
    return cards


def offense_cards(squad_units):
    o = squad_units["off"]
    pos = {}
    for c in o:
        pos.setdefault(c.identity.pos, []).append(c)
    wr = squad_units["wr"]
    cards = {"qb": squad_units["qb"], "hb": squad_units["hb"], "te": squad_units["te"], "x": wr[0], "z": wr[1], "s": wr[2]}
    ol = [c for c in o if c.identity.pos in ("LT", "LG", "C", "RG", "RT")]
    byp = {c.identity.pos: c for c in ol}
    spare = [c for c in ol if c not in byp.values()]
    for role in ("lt", "lg", "c", "rg", "rt"):
        cards[role] = byp.get(role.upper()) or (ol.pop(0) if ol else cards["te"])
    return cards


def build_defense(sim, cards, cov):
    """Defensive alignment from the offense's alignment and the coverage call. Positions are (x, y)."""
    los, by = sim.los, sim.by
    rec = {r: (sim.px[sim.idx[r]], sim.py[sim.idx[r]]) for r in ("x", "z", "s", "te", "hb")}
    out = {}
    # the front: four down linemen over the offensive line
    out["de_l"] = (los + 0.7, by + 4.4)
    out["dt_l"] = (los + 0.7, by + 1.0)
    out["dt_r"] = (los + 0.7, by - 1.0)
    out["de_r"] = (los + 0.7, by - 4.4)
    lbd = 4.6
    out["lb_l"] = (los + lbd, by + 4.2)
    out["mlb"] = (los + lbd, by)
    out["lb_r"] = (los + lbd, by - 4.2)
    # cornerbacks over the outside receivers
    wide = sorted((rec["x"], rec["z"]), key=lambda p: -p[1])
    press = cov["press"]
    for role, (rx, ry) in (("cb_l", wide[0]), ("cb_r", wide[1])):
        pr = sim.rng.random() < press
        out[role] = (los + (1.0 if pr else cov["off"]), clampy(ry + (0.6 if ry > MID else -0.6) * 0.0))
    # safeties
    d_fs, d_ss = cov["safety_depth"]
    strong = 1.0 if (rec["te"][1] - by) >= 0 else -1.0
    out["fs"] = (los + d_fs, by + 3.0 * strong * -1)
    out["ss"] = (los + d_ss, by + 5.0 * strong)
    # man coverage on a slot receiver pulls the matched defender over him; backers move out to cover the slot or the back
    for role, who in cov["man"].items():
        if role in ("lb_l", "lb_r", "mlb", "ss", "fs") and who in rec:
            tx, ty = rec[who]
            if who == "hb":
                out[role] = (los + 4.0, ty)
            else:
                out[role] = (los + (cov["off"] if who != "te" else 3.0), clampy(ty + (-1.0 if ty > by else 1.0)))
    return out


def rushers_for(cov, rng, play_is_run):
    """Defensive roles that rush the passer: the four linemen plus the coverage's extra rushers."""
    base = ["de_l", "dt_l", "dt_r", "de_r"]
    n = cov["rush"]
    pool = [r for r in EXTRA_RUSHERS if r not in cov.get("deep", []) and r not in cov["man"] or r in ("lb_l", "lb_r", "mlb")]
    pool = [r for r in ("lb_l", "lb_r", "mlb", "ss", "fs") if r not in cov.get("deep", [])]
    return base, pool[:n]
