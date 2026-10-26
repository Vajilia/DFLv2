"""A club's game squad: the cards available, the starters at each position, the unit ratings the play resolver reads, injuries.

Formation (slice 1): offense QB, HB, TE, 3 WR, 5 OL; defense 4-3 (LE, RE, 2 DT, ROLB, MLB, LOLB, 2 CB, FS, SS). Special teams K, P, LS.
Unit ratings are weighted means of hidden attributes. They are read only by the engine; nothing here is shown to the viewer.
"""
from ..characters.creator import load_tables
from .params import P

_TABLES = None


def overall(card):
    """Hidden true overall (for choosing starters)."""
    global _TABLES
    if _TABLES is None:
        _TABLES = load_tables()
    return card.true_overall(_TABLES)

OFF = (("QB", 1), ("HB", 1), ("TE", 1), ("WR", 3), ("LT", 1), ("LG", 1), ("C", 1), ("RG", 1), ("RT", 1))
DEF = (("LE", 1), ("RE", 1), ("DT", 2), ("ROLB", 1), ("MLB", 1), ("LOLB", 1), ("CB", 2), ("FS", 1), ("SS", 1))
SPECIAL = (("K", 1), ("P", 1), ("LS", 1))
OL = ("LT", "LG", "C", "RG", "RT")
DL = ("LE", "RE", "DT")
LB = ("ROLB", "MLB", "LOLB")
DB = ("CB", "FS", "SS")


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 50.0


def wmean(card, weights):
    return sum(card.attrs.get(a, 50) * w for a, w in weights.items()) / sum(weights.values())


class Staff:
    """The three coaches whose ratings the engine reads (optional). Effects are small and capped."""
    def __init__(self, hc=None, oc=None, dc=None, stc=None):
        self.hc, self.oc, self.dc, self.stc = hc, oc, dc, stc

    def bonus(self, card, attr):
        return 0.0 if card is None else max(-0.5, min(0.5, (card.attrs.get(attr, 60) - 60) / 40.0))


class Squad:
    def __init__(self, cid, cards, staff=None, ratings=None):
        self.cid = cid
        self.ratings = ratings or {}      # card id -> hidden true overall, when the caller keeps them current (saves recomputing)
        self.cards = {c.identity.id: c for c in cards}
        self.staff = staff or Staff()
        self.out = set()                 # ids injured in this game
        self.new_injuries = []           # (id, weeks)
        self.ejected = []                # ids ejected in this game (two unsportsmanlike fouls, section 10)
        self._units = None
        self._rating = {}

    # ---- lineup
    def _rate(self, c):
        i = c.identity.id
        if i not in self._rating:
            self._rating[i] = self.ratings[i] if i in self.ratings else overall(c)
        return self._rating[i]

    def available(self, pos):
        cs = [c for c in self.cards.values() if c.identity.pos == pos and c.identity.id not in self.out]
        return sorted(cs, key=lambda c: (-self._rate(c), c.identity.id))

    def lineup(self, spec):
        """The best available players at each position; a position with too few is filled by the best leftover player from anywhere
        (the out-of-position penalty is an open engine item, so slice 1 simply uses her own attributes)."""
        out, short = [], 0
        for pos, n in spec:
            got = self.available(pos)[:n]
            out += got
            short += n - len(got)
        if short:
            have = {c.identity.id for c in out}
            spare = sorted((c for c in self.cards.values() if c.identity.id not in have and c.identity.id not in self.out
                            and c.identity.pos not in ("K", "P", "LS", "QB")), key=lambda c: (-self._rate(c), c.identity.id))
            out += spare[:short]
        return out

    def ready(self):
        """Can this squad field a full offense, defense and kicking game?"""
        return all(len(self.available(p)) >= n for p, n in OFF + DEF + SPECIAL)

    def injure(self, cid, weeks):
        self.out.add(cid)
        self.new_injuries.append((cid, weeks))
        self._units = None

    def eject(self, cid):
        self.out.add(cid)
        self.ejected.append(cid)
        self._units = None

    # ---- unit ratings (0-99 scale) and z-scores
    def units(self):
        if self._units is not None:
            return self._units
        o, d = self.lineup(OFF), self.lineup(DEF)
        by = lambda lst, ps: [c for c in lst if c.identity.pos in ps]
        pick = lambda lst, ps, n: (by(lst, ps) + [c for c in lst if c.identity.pos not in ps])[:n]
        qb = pick(o, ("QB",), 1)[0]
        hb = pick(o, ("HB",), 1)[0]
        wr, te, ol = pick(o, ("WR",), 3), pick(o, ("TE",), 1), pick(o, OL, 5)
        dl, lb, db = pick(d, DL, 4), pick(d, LB, 3), pick(d, DB, 4)
        u = {
            "qb": wmean(qb, {"THA": 40, "THP": 25, "PRS": 20, "AWR": 15}),
            "pro": mean(wmean(c, {"PBK": 70, "STR": 15, "AWR": 15}) for c in ol),
            "recv": mean([wmean(c, {"RTE": 30, "HND": 30, "CTC": 10, "SPD": 15, "AGI": 15}) for c in wr] +
                         [wmean(te[0], {"RTE": 30, "HND": 40, "STR": 15, "AGI": 15})]),
            "rb": wmean(hb, {"VIS": 30, "ELU": 30, "SPD": 15, "ACC": 10, "BSE": 10, "STR": 5}),
            "runblock": mean(wmean(c, {"RBK": 60, "STR": 25, "AWR": 15}) for c in ol),
            "prush": 0.8 * mean(wmean(c, {"PRU": 55, "ACC": 20, "STR": 25}) for c in dl) +
                     0.2 * mean(wmean(c, {"PRU": 60, "ACC": 40}) for c in lb),
            "cover": 0.75 * mean(wmean(c, {"COV": 50, "BAL": 20, "SPD": 20, "AGI": 10}) for c in db) +
                     0.25 * mean(wmean(c, {"COV": 60, "SPD": 20, "AWR": 20}) for c in lb),
            "rdef": 0.5 * mean(wmean(c, {"RDF": 50, "BSH": 30, "STR": 20}) for c in dl) +
                    0.35 * mean(wmean(c, {"TAK": 60, "AWR": 20, "SPD": 20}) for c in lb) +
                    0.15 * mean(wmean(c, {"TAK": 70, "AWR": 30}) for c in db),
        }
        # with no kicker or punter dressed (an emergency), the best leg among the rest takes the kick
        legs = lambda w: max(self.cards.values(), key=lambda c: (wmean(c, w), -c.identity.id))
        k, p = self.available("K") or [legs({"KPW": 40, "KAC": 60})], self.available("P") or [legs({"KPW": 60, "KAC": 40})]
        u["kick"] = wmean(k[0], {"KPW": 40, "KAC": 60})
        u["punt"] = wmean(p[0], {"KPW": 60, "KAC": 40})
        runners = [c for c in self.cards.values() if c.identity.pos in ("HB", "WR", "CB") and c.identity.id not in self.out]
        returner = max(runners, key=lambda c: (wmean(c, {"SPD": 40, "ELU": 40, "BSE": 20}), -c.identity.id)) if runners else None
        z = {n: (u[n] - P.get("mean_" + n, P["mean_kick"] if n == "punt" else 70)) / P.get("sd_" + n, P["sd_kick"] if n == "punt" else 5) for n in u}
        st = self.staff
        z["off_coach"] = st.bonus(st.oc, "SCH") + 0.5 * st.bonus(st.hc, "MGT")
        z["def_coach"] = st.bonus(st.dc, "SCH") + 0.5 * st.bonus(st.hc, "MGT")
        self._units = {"raw": u, "z": z, "off": o, "def": d, "qb": qb, "hb": hb, "wr": wr, "te": te[0], "kicker": k[0], "punter": p[0],
                       "returner": returner}
        return self._units
