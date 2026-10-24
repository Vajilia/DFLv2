"""Deterministic character generation.

Every card is drawn from its own random stream, seeded from (world seed, card id), so a card never depends on how many other cards
were made first. The creator's only memory is the next id and the set of full names in use, which `state()` / `restore()` carry.

One shared talent factor `g` per player (plus noise per attribute) makes attributes correlate the way real athletes do. Draft classes
carry a class-quality shift, which is a fairness knob (a weak class and a strong class both occur). The age curve and all means are
PLACEHOLDER data until the engine says which attributes it needs; see data/characters/.
"""
import csv
import hashlib
import math
import os
import random

from .card import Card, Identity

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DATA = os.path.join(ROOT, "data", "characters")
ROUNDING_MIN, ROUNDING_MAX = 1, 99
DRAFT_AGES = ((21, 30), (22, 45), (23, 25))              # age, weight
CLASS_SIZE, CLASS_MIN_PER_POSITION = 672, 12
LEADER_ROLES = ("ceo", "gm", "coach", "coach", "coach", "coach")     # coaches are one category; the head-coach and coordinator posts are held, not born into


class Tables:
    pass


def load_tables(path=DATA):
    t = Tables()
    def rd(f):
        with open(os.path.join(path, f), encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))

    def lines(f):
        with open(os.path.join(path, f), encoding="utf-8") as fh:
            return [x.strip() for x in fh if x.strip()]

    t.positions = {r["pos"]: {k: (float(v) if k not in ("pos", "group") else v) for k, v in r.items()} for r in rd("POSITIONS.csv")}
    t.profile = {}
    for r in rd("PROFILE.csv"):
        t.profile.setdefault(r["pos"], []).append((r["attr"], float(r["mean"]), float(r["sd"]), float(r["load"]), float(r["weight"])))
    t.roles = {r["role"]: {k: float(v) for k, v in r.items() if k != "role"} for r in rd("ROLES.csv")}
    t.traits = [(r["code"], float(r["mean"]), float(r["sd"])) for r in rd("TRAITS.csv")]
    t.looks = {}
    for r in rd("LOOKS.csv"):
        t.looks.setdefault(r["feature"], []).append((r["value"], float(r["weight"])))
    t.age_curve = {int(r["delta"]): float(r["multiplier"]) for r in rd("AGE_CURVE.csv")}
    t.scouting = {r["key"]: float(r["value"]) for r in rd("SCOUTING.csv")}
    t.scouting_bias = {r["pos"]: float(r["bias"]) for r in rd("SCOUTING_BIAS.csv")}
    t.development = {r["key"]: float(r["value"]) for r in rd("DEVELOPMENT.csv")}
    t.soul = {r["key"]: float(r["value"]) for r in rd("SOUL.csv")}
    t.negotiation = {r["key"]: float(r["value"]) for r in rd("NEGOTIATION.csv")}
    t.coaching = {r["key"]: float(r["value"]) for r in rd("COACHING.csv")}
    t.trading = {r["key"]: float(r["value"]) for r in rd("TRADING.csv")}
    t.governance = {r["key"]: float(r["value"]) for r in rd("GOVERNANCE.csv")}
    t.archetypes = {r["code"]: {"name": r["name"], "self_bias": float(r["self_bias"]),
                                "centre": {k: float(r[k]) for k in ("AMB", "LOY", "WRK", "TMP", "GRE", "LDR")}} for r in rd("ARCHETYPES.csv")}
    t.first, t.last = lines("FIRST_NAMES.txt"), lines("LAST_NAMES.txt")
    t.attrs = {r["code"]: r for r in rd("ATTRIBUTES.csv")}
    return t


def rng_for(seed, *parts):
    h = hashlib.sha256("|".join(str(x) for x in (seed,) + parts).encode()).digest()
    return random.Random(int.from_bytes(h[:8], "big"))


def clip(x, lo=ROUNDING_MIN, hi=ROUNDING_MAX):
    return int(max(lo, min(hi, round(x))))


def pick(rng, weighted):
    tot = sum(w for _, w in weighted)
    r = rng.random() * tot
    for v, w in weighted:
        r -= w
        if r <= 0:
            return v
    return weighted[-1][0]


def apportion(total, weights, floor=0):
    """Whole-number shares of `total` by weight, each at least `floor`, summing exactly (largest remainder)."""
    keys = sorted(weights)
    rest = total - floor * len(keys)
    tw = sum(weights.values())
    raw = {k: rest * weights[k] / tw for k in keys}
    out = {k: floor + int(math.floor(raw[k])) for k in keys}
    left = total - sum(out.values())
    for k in sorted(keys, key=lambda k: (-(raw[k] - math.floor(raw[k])), k))[:left]:
        out[k] += 1
    return out


class Creator:
    def __init__(self, seed, tables=None):
        self.seed = seed
        self.t = tables or load_tables()
        self.next_id = 1
        self.taken = set()                  # full names of characters currently on the list

    # ---- state carried between runs
    def state(self):
        return {"seed": self.seed, "next_id": self.next_id, "taken": sorted(self.taken)}

    def restore(self, s):
        assert s["seed"] == self.seed
        self.next_id, self.taken = s["next_id"], set(s["taken"])

    def release_name(self, card):
        """A retired character leaves the list; her name may be used again."""
        self.taken.discard(card.identity.name)

    # ---- cards
    def class_quality(self, year):
        """Draft class strength in talent standard deviations: some classes are weak, some strong. A fairness parameter."""
        return rng_for(self.seed, "class", year).gauss(0, 0.25)

    def player(self, pos, age, year, origin="draft", quality=0.0, talent_mean=0.0):
        t = self.t
        cid = self.next_id
        self.next_id += 1
        rng = rng_for(self.seed, "card", cid)
        P = t.positions[pos]
        g = rng.gauss(talent_mean + quality, 1.0)
        peak = int(round(rng.gauss(P["peak_age"], P["peak_sd"])))
        curve = t.age_curve
        delta = max(min(age - peak, max(curve)), min(curve))
        mult = curve[delta]
        attrs = {}
        for a, mean, sd, load, _ in t.profile[pos]:
            v = mean + sd * (load * g + math.sqrt(1 - load * load) * rng.gauss(0, 1))
            attrs[a] = clip(v * mult)
        height = clip(rng.gauss(P["ht_mean"], P["ht_sd"]), 140, 200)
        weight = clip(rng.gauss(P["wt_mean"] + 0.6 * (P["wt_sd"]) * (height - P["ht_mean"]) / P["ht_sd"], P["wt_sd"] * 0.8), 45, 160)
        growth = {"peak_age": peak,
                  "ceiling": clip(rng.gauss(max(0, 4 - 0.5 * max(0, age - 21)) + 2 * max(0, -quality), 2), 0, 12),
                  "volatility": round(abs(rng.gauss(1.0, 0.35)), 2)}
        return self._finish(cid, rng, "player", pos, age, year, origin, attrs, growth, height, weight)

    def leader(self, role, year, origin="founding", shift=0.0):
        t = self.t
        cid = self.next_id
        self.next_id += 1
        rng = rng_for(self.seed, "card", cid)
        R = t.roles[role]
        age = clip(rng.gauss(R["age_mean"], R["age_sd"]), int(R["age_min"]), int(R["age_max"]))
        g = rng.gauss(shift, 1)
        attrs = {a: clip(m + sd * (ld * g + math.sqrt(1 - ld * ld) * rng.gauss(0, 1))) for a, m, sd, ld, w in t.profile[role] if w}
        hid = rng_for(self.seed, "hidden", cid)          # attributes that carry no weight (discretion) have a stream of their own, so adding one never shifts another card
        for a, m, sd, ld, w in t.profile[role]:
            if not w:
                attrs[a] = clip(m + sd * (ld * g + math.sqrt(1 - ld * ld) * hid.gauss(0, 1)))
        growth = {"peak_age": 50, "ceiling": 0, "volatility": round(abs(rng.gauss(1.0, 0.35)), 2)}
        height = clip(rng.gauss(166, 7), 145, 190)
        weight = clip(rng.gauss(64, 9), 45, 130)
        return self._finish(cid, rng, role, role, age, year, origin, attrs, growth, height, weight)

    def _finish(self, cid, rng, role, pos, age, year, origin, attrs, growth, height, weight):
        t = self.t
        traits = {c: clip(rng.gauss(m, sd), 0, 100) for c, m, sd in t.traits}
        for _ in range(200):
            first, last = rng.choice(t.first), rng.choice(t.last)
            if f"{first} {last}" not in self.taken:
                break
        else:
            raise RuntimeError("name pool exhausted")
        self.taken.add(f"{first} {last}")
        look = tuple((f, pick(rng, vals)) for f, vals in sorted(t.looks.items()))
        ident = Identity(cid, role, first, last, year - age, pos, height, weight, look, year, origin)
        return Card(ident, attrs, growth, traits)

    # ---- populations
    def draft_class(self, year, quality=None):
        """672 prospects, at least 12 at every position, ages 21-23, with the class-quality shift."""
        q = self.class_quality(year) if quality is None else quality
        weights = {p: v["draft_weight"] for p, v in self.t.positions.items()}
        counts = apportion(CLASS_SIZE, weights, floor=CLASS_MIN_PER_POSITION)
        rng = rng_for(self.seed, "classages", year)
        out = []
        for pos in sorted(counts):
            for _ in range(counts[pos]):
                out.append(self.player(pos, pick(rng, DRAFT_AGES), year, "draft", quality=q, talent_mean=-0.2))
        return out

    def leaders_for_club(self, year, origin="founding"):
        return [self.leader(r, year, origin) for r in LEADER_ROLES]

    def founding(self, year, clubs, pool_per_position=8):
        """The day-one league: for each club a 53 (the template) and a 16 practice squad, its six leaders, and the free-agent pool.
        Veterans are survivors, so their talent is shifted up and their ages spread across a career (21-34)."""
        from ..ledger.world import TEMPLATE, PS_TEMPLATE
        rng = rng_for(self.seed, "founding", year)
        ages = tuple((a, w) for a, w in zip(range(21, 35), (12, 14, 14, 12, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1)))
        cards = {"clubs": {}, "pool": []}
        for cid in clubs:
            roster = [self.player(pos, pick(rng, ages), year, "founding", talent_mean=0.35)
                      for pos, n in sorted(TEMPLATE.items()) for _ in range(n)]
            ps = [self.player(pos, pick(rng, ages[:5]), year, "founding", talent_mean=-0.2) for pos in PS_TEMPLATE]
            cards["clubs"][cid] = {"roster": roster, "ps": ps, "leaders": self.leaders_for_club(year)}
        for pos in sorted(self.t.positions):
            for _ in range(pool_per_position):
                cards["pool"].append(self.player(pos, pick(rng, ages), year, "founding", talent_mean=-0.1))
        return cards
