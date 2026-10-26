"""Constants, data tables and player profiles for the spatial engine (Edition 8.48).

Coordinates: x is yards from the offense's own goal line (0 to 100, end zones beyond), y is yards across the 53.33-yard field, increasing to
the offense's left. Time is in seconds and the physics step is 0.05 s (Design Document section 0).
"""
import csv
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
DATA = os.path.join(ROOT, "data", "engine", "spatial")

DT = 0.05
FIELD_W = 160.0 / 3.0
MID = FIELD_W / 2.0
HASH_L = MID + 3.0                     # the hash marks are 6 yards apart, centred
HASH_R = MID - 3.0
MAX_STEPS = 400                        # 20 seconds: no play lasts longer

OFF_ROLES = ("qb", "hb", "te", "x", "z", "s", "lt", "lg", "c", "rg", "rt")
DEF_ROLES = ("de_l", "dt_l", "dt_r", "de_r", "lb_l", "mlb", "lb_r", "cb_l", "cb_r", "fs", "ss")
OL_ROLES = ("lt", "lg", "c", "rg", "rt")
OL_Y = {"lt": 2.5, "lg": 1.25, "c": 0.0, "rg": -1.25, "rt": -2.5}


def _load():
    s = {}
    with open(os.path.join(DATA, "PHYSICS.csv"), newline="") as f:
        for r in csv.DictReader(l for l in f if not l.startswith("#")):
            s[r["name"]] = float(r["value"])
    j = {}
    for name in ("ROUTES", "FORMATIONS", "COVERAGES", "PLAYS"):
        with open(os.path.join(DATA, name + ".json")) as f:
            j[name] = {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    return s, j


S, _J = _load()
ROUTES, FORMATIONS, COVERAGES, PLAYS = _J["ROUTES"], _J["FORMATIONS"], _J["COVERAGES"], _J["PLAYS"]
RUNS = PLAYS["runs"]
PASSES = PLAYS["passes"]


def reload():
    global S, ROUTES, FORMATIONS, COVERAGES, PLAYS, RUNS, PASSES
    S2, J = _load()
    S.clear()
    S.update(S2)
    ROUTES, FORMATIONS, COVERAGES, PLAYS = J["ROUTES"], J["FORMATIONS"], J["COVERAGES"], J["PLAYS"]
    RUNS, PASSES = PLAYS["runs"], PLAYS["passes"]


class Prof:
    """What the spatial engine reads about one player: her physical numbers and skills (hidden attributes; never shown)."""
    __slots__ = ("card", "id", "pos", "a", "vmax", "acc", "dec", "wt", "ht", "stren", "agi", "awr", "cmp", "tough")

    def __init__(self, card, fatigue=1.0):
        a = card.attrs
        self.card = card
        self.id = card.identity.id
        self.pos = card.identity.pos
        self.a = a
        spd, acc, agi = a.get("SPD", 50), a.get("ACC", 50), a.get("AGI", 50)
        f = 1.0 - 0.1 * (1.0 - fatigue)
        self.vmax = (S["vmax_base"] + S["vmax_per_spd"] * spd) * f
        self.acc = (S["acc_base"] + S["acc_per_acc"] * acc) * (1.0 + S["agi_acc_gain"] * (agi - 50) / 100.0) * f
        self.dec = self.acc * S["dec_mult"]
        self.wt = card.identity.weight_kg
        self.ht = card.identity.height_cm
        self.stren = a.get("STR", 50)
        self.agi = agi
        self.awr = a.get("AWR", 50)
        self.cmp = a.get("CMP", 50)
        self.tough = a.get("DUR", 50)

    def g(self, k, default=50):
        return self.a.get(k, default)
