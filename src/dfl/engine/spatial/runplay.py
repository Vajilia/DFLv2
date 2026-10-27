"""Running plays and the blocking assignments (Edition 8.48)."""
import math

from .core import FIELD_W, S

BOX = ("de_l", "dt_l", "dt_r", "de_r", "lb_l", "mlb", "lb_r")


def match(cost, A, D, order=None):
    """Greedy pairing of the blockers A with the defenders D, cheapest pair first; or, with `order` (a key on defenders), the defenders
    in that order each take the cheapest blocker still free, so the defenders left unblocked are the last in the order. Returns {a: d}."""
    ua, ud, out = set(), set(), {}
    if order is not None:
        for d in sorted(D, key=order):
            free = [a for a in A if a not in ua]
            if not free:
                break
            a = min(free, key=lambda a: cost(a, d))
            out[a] = d
            ua.add(a)
        return out
    pairs = sorted((cost(a, d), a, d) for a in A for d in D)
    for c, a, d in pairs:
        if a in ua or d in ud:
            continue
        out[a] = d
        ua.add(a)
        ud.add(d)
    return out


class RunMixin:
    def _setup_run(self):
        pl = self.play
        rng = self.rng
        self.ps = 1.0 if rng.random() < 0.5 else -1.0                    # the side the play goes to
        self.scheme = pl["scheme"]
        self.hole = (self.los + 1.2, self.by + self.ps * pl["aim"])
        self.handoff_t = pl["handoff"]
        self.runner = self.hb
        px, py = self.px, self.py
        box = [self.idx[r] for r in BOX]
        blockers = list(self.OL) + [self.idx["te"]]
        shift = {"zone": 1.0, "gap": 0.4, "draw": 0.0, "sneak": 0.0}[self.scheme]
        pull = None
        if pl.get("pull"):
            pull = self.idx["rg"] if self.ps > 0 else self.idx["lg"]
            blockers.remove(pull)
        los, ps = self.los, self.ps
        cost = lambda a, d: abs(py[d] - (py[a] + ps * shift)) + 0.35 * (px[d] - los) + (0.4 if d == self.idx["mlb"] else 0.0)
        hy = self.hole[1]
        pairs = match(cost, blockers, box, order=lambda d: abs(py[d] - hy) + (0.8 if d in (self.idx['lb_l'], self.idx['lb_r'], self.idx['mlb']) else 0.0))
        delay = 0.3 if self.scheme == "draw" else 0.0
        lbs = {self.idx["lb_l"], self.idx["lb_r"], self.idx["mlb"]}
        front = {a: d for a, d in pairs.items() if d not in lbs}
        for a in blockers:
            pa = self.P[a]
            pa.delay = delay
            if a in pairs:
                pa.mark = pairs[a]
                pa.state = "rblock"
                if pairs[a] in lbs and front:
                    # combination block: help a neighbour with the lineman, then climb to the backer
                    nb = min(front, key=lambda b: abs(py[b] - py[a]))
                    pa.aux = ("combo", front[nb], pairs[a])
                    pa.mark = front[nb]
            else:
                pa.state = "seek"
        if pull is not None:
            pp = self.P[pull]
            pp.state = "seek"
            pp.aux = ("pull", self.hole[0] - 0.4, self.hole[1] + ps * 1.4)
        # wide receivers block the nearest defensive backs
        dbs = [self.idx[r] for r in ("cb_l", "cb_r", "ss", "fs")]
        wr = [self.idx[r] for r in ("x", "z", "s")]
        w2 = match(lambda a, d: math.hypot(px[a] - px[d], py[a] - py[d]), wr, dbs)
        for a, d in w2.items():
            self.P[a].mark = d
            self.P[a].state = "rblock"
        if self.scheme == "sneak":
            self.runner = self.qb
            self.P[self.qb].state = "carry"
            self.P[self.hb].state = "seek"
            self.hole = (self.los + 1.0, self.by)
            return
        self.P[self.hb].state = "hbrun"
        self.P[self.qb].state = "handoff"
        qb = self.P[self.qb]
        self.mesh = (self.px[self.qb] + 0.2, self.py[self.qb] + ps * 0.8)

    # the quarterback holds still at the mesh point and then fakes
    def t_handoff(self, p):
        self.stop(p.i)

    def t_hbrun(self, p):
        i = p.i
        mx, my = self.mesh
        dd = math.hypot(self.px[i] - mx, self.py[i] - my)
        if (self.t >= self.handoff_t and dd < 1.0) or self.t > self.handoff_t + 1.2:
            self.ball_to(i)
            p.state = "carry"
            self.ev("handoff", self.qb, i)
            self.P[self.qb].state = "idle"
            self.stop(self.qb)
            self.t_carry(p)
            return
        self.go(i, mx, my, 0.85)

    def t_rblock(self, p):
        m = p.mark
        if m is None:
            p.state = "seek"
            return
        q = self.P[m]
        if q.eng is not None and q.eng.a != p.i and q.eng.b2 is not None:
            p.state = "seek"
            p.mark = None
            return
        self.go(p.i, self.px[m] + self.vx[m] * 0.12, self.py[m] + self.vy[m] * 0.12, 1.0)

    def t_seek(self, p):
        i = p.i
        a = p.aux
        if isinstance(a, tuple) and a[0] == "pull":
            if math.hypot(self.px[i] - a[1], self.py[i] - a[2]) > 0.9:
                self.go(i, a[1], a[2], 1.0)
                return
            p.aux = None
        best, bd = None, 1e9
        x, y = self.px[i], self.py[i]
        for j in self.DEF:
            q = self.P[j]
            if q.eng is not None or q.stun > 0.0:
                continue
            if self.px[j] < x - 1.5:
                continue
            dd = math.hypot(self.px[j] - x, self.py[j] - y) + 0.15 * abs(self.py[j] - self.hole[1])
            if dd < bd:
                best, bd = j, dd
        if best is None or bd > 12.0:
            h = self.holder
            if h is None:
                self.stop(i)
                return
            self.go(i, self.px[h] + 2.0, self.py[h], 0.9)
            return
        p.mark = best
        self.go(i, self.px[best] + self.vx[best] * 0.12, self.py[best] + self.vy[best] * 0.12, 1.0)
