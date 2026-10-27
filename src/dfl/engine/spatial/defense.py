"""The defense: pass rush, man and zone coverage, run fits and pursuit (Edition 8.48)."""
import math

from .align import rushers_for
from .core import COVERAGES, DT, FIELD_W, MID, S
from . import core

DL = ("de_l", "dt_l", "dt_r", "de_r")
LANE = {"de_l": 2.4, "de_r": -2.4, "dt_l": 0.4, "dt_r": -0.4, "lb_l": 1.2, "lb_r": -1.2, "mlb": 0.0, "ss": 1.5, "fs": -1.5}


class DefMixin:
    def hist(self, j, lag):
        """Where player j was `lag` steps ago, as the position a defender sees."""
        from .sim import _H
        k = max(0, self.n - lag)
        return float(_H[k, j, 0]), float(_H[k, j, 1])

    def _setup_defense(self):
        cov = self.cov
        rng = self.rng
        los = self.los
        if self.kind == "run":
            self._setup_run_defense()
            return
        base, extra = rushers_for(cov, rng, False)
        rushers = list(base) + list(extra)
        man = dict(cov["man"])
        if "cb_l" in man and "cb_r" in man and {man["cb_l"], man["cb_r"]} == {"x", "z"}:
            # the corners take the wide receivers by where they stand, whichever way the play is lined up
            if self.py[self.idx[man["cb_l"]]] < self.py[self.idx[man["cb_r"]]]:
                man["cb_l"], man["cb_r"] = man["cb_r"], man["cb_l"]
        zones = cov["zones"]
        fake = self.play.get("fake")
        for role in core.DEF_ROLES:
            i = self.idx[role]
            p = self.P[i]
            if role in rushers:
                p.state = "rush"
                p.front = LANE.get(role, 0.0)
                continue
            if role in man:
                tgt = self.idx[man[role]]
                p.mark = tgt
                p.state = "man"
                p.cush = cov["off"] if role.startswith("cb") else 1.5
                continue
            if role in zones:
                zd, zy, hw = zones[role]
                p.zone = (los + zd, zy, hw)
                p.state = "zone"
                if fake and role in ("lb_l", "mlb", "lb_r"):
                    p.aux = ("bite", 0.25 + 0.5 * (100.0 - p.prof.awr) / 100.0)
                continue
            if role == cov.get("spy"):
                p.state = "spy"
                continue
            p.zone = (los + 6.0, self.by + (1.0 if role in ("lb_l", "ss") else -1.0) * 4.0, 7.0)
            p.state = "zone"
        # press defenders and man on a back who stays in to block become rushers or sit
        for role in list(man):
            p = self.P[self.idx[role]]
            if p.cush <= 1.0 and role.startswith("cb"):
                pass
            if role.startswith("cb") and (self.px[p.i] - self.px[p.mark]) < 2.2:
                p.state = "man"                                      # still man; the press flag is read when the routes are set

    def _setup_run_defense(self):
        fake = self.scheme == "draw"
        for role in core.DEF_ROLES:
            i = self.idx[role]
            p = self.P[i]
            a = p.prof.awr
            gx = {"lb_l": S["gap_lb"], "mlb": 0.0, "lb_r": -S["gap_lb"], "ss": 5.5, "cb_l": 0.0, "cb_r": 0.0, "fs": 0.0}
            if role in gx:
                if role.startswith("cb"):
                    p.zone = (self.px[i] + 0.5, self.py[i], 3.0)
                elif role == "fs":
                    p.zone = (self.los + 8.0, self.by, 6.0)
                elif role == "ss":
                    p.zone = (self.los + 4.5, self.by + gx[role] * (1.0 if self.py[i] > self.by else -1.0), 3.0)
                else:
                    p.zone = (self.los + S["fill_depth"], self.by + gx[role], S["gap_w"])
            if role in DL:
                p.state = "dlrun"
                p.read_t = 0.14 + 0.0025 * (100.0 - a) + (0.7 if fake else 0.0)
            elif role in ("lb_l", "mlb", "lb_r"):
                p.state = "runfit"
                p.read_t = 0.22 + S["run_read_extra"] + 0.0035 * (100.0 - a) + (0.5 if fake else 0.0)
            else:
                p.state = "runfit"
                p.read_t = 0.38 + S["run_read_extra"] + 0.004 * (100.0 - a) + (0.4 if fake else 0.0)

    # -------------------------------------------------------------------------------------------------------- run defense
    def target_of_pursuit(self):
        h = self.holder
        if h is None:
            return self.bx, self.by_, 0.0, 0.0, False
        if self.kind == "run" and h == self.qb and not self.scrambled:
            hb = self.hb
            return self.px[hb], self.py[hb], 0.0, 0.0, True
        vx, vy = self.vx[h], self.vy[h]
        sp = math.hypot(vx, vy)
        if sp > 0.5:                                             # defenders judge where a runner will be at her running speed, not her speed now
            k = max(sp, S["pursuit_pred"] * self.VM[h] * 0.97) / sp
            vx, vy = vx * k, vy * k
        return self.px[h], self.py[h], vx, vy, False

    def t_dlrun(self, p):
        i = p.i
        if self.t < p.read_t:
            self.go(i, self.los - 0.1, self.py[i] + (self.by - self.py[i]) * 0.05, 0.95)
            return
        p.state = "pursue"
        self.t_pursue(p)

    def t_runfit(self, p):
        i = p.i
        if self.t < p.read_t:
            if p.role in ("lb_l", "mlb", "lb_r"):
                self.go(i, self.px[i] - 0.4, self.py[i], 0.35)
            else:
                self.stop(i)
            return
        p.state = "pursue"
        self.t_pursue(p)

    def intercept_point(self, i, tx, ty, vx, vy, speed):
        """Where defender i, running at `speed`, can meet a runner at (tx, ty) moving (vx, vy); the point far down her path if she cannot."""
        dx, dy = tx - self.px[i], ty - self.py[i]
        a = vx * vx + vy * vy - speed * speed
        b = 2.0 * (dx * vx + dy * vy)
        c = dx * dx + dy * dy
        t = None
        if abs(a) < 1e-6:
            if b < -1e-6:
                t = -c / b
        else:
            disc = b * b - 4.0 * a * c
            if disc >= 0.0:
                r = math.sqrt(disc)
                cand = [x for x in ((-b - r) / (2.0 * a), (-b + r) / (2.0 * a)) if x > 0.0]
                if cand:
                    t = min(cand)
        if t is None:
            t = 1.5
        t = min(t, 2.6)
        return tx + vx * t, ty + vy * t

    def t_pursue(self, p):
        i = p.i
        tx, ty, vx, vy, key = self.target_of_pursuit()
        if key:
            self.go(i, tx, ty, 1.0)
            return
        ax, ay = self.intercept_point(i, tx, ty, vx, vy, self.VM[i] * 0.95)
        if self.kind == "run" and self.cdir > 0 and p.role not in DL and tx < self.los + 0.3:
            ax = max(ax, self.los + S["fill_depth"])                    # backers and backs fill at the line; only the linemen go into the backfield
        self.go(i, ax, ay, 1.0)

    def t_escort(self, p):
        h = self.holder
        if h is None:
            self.stop(p.i)
            return
        self.go(p.i, self.px[h] - 3.0, self.py[h] + (3.0 if self.py[p.i] > self.py[h] else -3.0), 0.9)

    # ---------------------------------------------------------------------------------------------------------- pass rush
    def t_rush(self, p):
        i = p.i
        h = self.holder
        if self.bstate == "flight" or self.done:
            self.t_ball(p)
            return
        if h is None or h != self.qb or self.scrambled:
            p.state = "pursue"
            self.t_pursue(p)
            return
        qx, qy = self.px[self.qb], self.py[self.qb]
        x = self.px[i]
        lane = p.front
        if x - qx > 3.0 and abs(lane) > 1.0:
            self.go(i, qx + 1.0, qy + lane, 1.0)
        else:
            self.go(i, qx, qy, 1.0)

    def t_spy(self, p):
        i = p.i
        h = self.holder
        if self.scrambled and h is not None:
            p.state = "pursue"
            return
        if self.bstate == "flight":
            self.t_ball(p)
            return
        qy = self.py[self.qb]
        self.go(i, self.los + 7.0, qy, 0.6)

    # ----------------------------------------------------------------------------------------------------------- coverage
    def react_lag(self, p, key):
        pr = p.prof
        s = 0.30 - 0.0030 * pr.g(key, 45) - 0.0008 * (pr.awr - 50)
        return max(1, int(max(0.04, s) / DT))

    def t_ball(self, p):
        """The ball is in the air: break on it after a short reaction, to where it will be a moment from now (not to where it will land)."""
        if self.bstate != "flight":
            return
        if p.aux != "ball":
            p.aux = "ball"
            p.delay = self.t + S["ball_react"] + 0.003 * (100.0 - p.prof.awr)
            return
        tau = min(1.0, (self.t - self.bt0 + S["ball_lead"]) / self.bT)
        self.go(p.i, self.bx0 + (self.bax - self.bx0) * tau, self.by0 + (self.bay - self.by0) * tau, 1.0)

    def hold_or_ball(self, p):
        if self.bstate == "flight":
            if p.mark is not None and p.mark == self.targeted:
                # the target's man stays on her
                return False
            self.t_ball(p)
            return True
        if self.bstate == "held" and self.holder is not None and self.holder != self.qb and self.cdir > 0:
            p.state = "pursue"
            self.t_pursue(p)
            return True
        if self.scrambled and self.holder is not None:
            p.state = "pursue"
            self.t_pursue(p)
            return True
        return False

    def db_move(self, p, tx, ty, f=1.0):
        """Move a defensive back: backpedalling is slower; a turn to run downfield costs a moment."""
        i = p.i
        x, y = self.px[i], self.py[i]
        dx = tx - x
        if dx > 1.2 and p.aux != "turned" and self.vx[i] > -0.5 and math.hypot(self.vx[i], self.vy[i]) < 3.5:
            f *= 1.0
        if dx > 0.8:
            if p.aux != "turned":
                if p.cd2 if False else False:
                    pass
            back = S["backpedal"]
            ratio = back if p.aux != "turned" else 1.0
            # a defender who is beaten by more than three yards turns and runs
            if dx > 3.0 and p.aux != "turned":
                p.aux = "turned"
                p.delay = self.t + max(0.05, 0.28 - 0.003 * (p.prof.agi - 50))
                self.stop(i)
                return
            self.go(i, tx, ty, f * ratio)
        else:
            self.go(i, tx, ty, f)

    def t_man(self, p):
        if self.hold_or_ball(p):
            return
        m = p.mark
        mp = self.P[m]
        if mp.state in ("pblock", "rblock", "idle") and self.t > 0.6:
            p.state = "zone"
            p.zone = (self.los + 5.0, self.by, 8.0)
            return
        lag = self.react_lag(p, "COV")
        rx, ry = self.hist(m, lag)
        vx, vy = self.vx[m], self.vy[m]
        cush = max(0.0, p.cush - 1.2 * self.t) if m != self.targeted else 0.0
        tx, ty = rx + vx * 0.12 + 0.9 + cush * 0.0, ry + vy * 0.12
        # keep the cushion while the receiver is at speed
        spd = math.hypot(vx, vy)
        if spd > 3.0:
            tx += min(cush, 2.5)
        self.db_move(p, tx, ty, 1.0)

    def zone_assign(self):
        """Each zone defender takes the receiver nearest his zone (one receiver each), as a pattern-matching defense does."""
        if getattr(self, "_za_n", -1) == self.n:
            return self._za
        defs = [j for j in self.DEF if self.P[j].state == "zone" and self.P[j].zone is not None]
        recs = []
        for r in self.recv:
            q = self.P[r]
            if q.state in ("route", "settle", "catch"):
                recs.append((r, self.px[r], self.py[r]))
        pairs = []
        for j in defs:
            zx, zy, hw = self.P[j].zone
            deep = zx - self.los >= 10.0
            for r, rx, ry in recs:
                if rx < self.los - 0.5:
                    continue
                c = abs(ry - zy) + (0.35 if deep else 0.6) * abs(rx - zx)
                if deep:
                    c += 5.0 * max(0.0, (zx - S["band_deep"]) - rx)            # a deep defender does not take a receiver far underneath him
                else:
                    c += 5.0 * max(0.0, rx - (zx + S["band_under"]))            # an underneath defender passes off a receiver who gets behind him
                if c <= hw + S["zone_reach"]:
                    pairs.append((c, j, r))
        pairs.sort()
        out, uj, ur = {}, set(), set()
        for c, j, r in pairs:
            if j in uj or r in ur:
                continue
            out[j] = r
            uj.add(j)
            ur.add(r)
        self._za, self._za_n = out, self.n
        return out

    def t_zone(self, p):
        a = p.aux
        if isinstance(a, tuple) and a[0] == "bite":
            if self.t < a[1]:
                self.go(p.i, self.los + 1.0, self.py[p.i], 0.8)
                return
            p.aux = None
        if self.hold_or_ball(p):
            return
        i = p.i
        zx, zy, hw = p.zone
        lag = self.react_lag(p, "COV")
        r = self.zone_assign().get(i)
        deep = zx - self.los >= 10.0
        if r is not None:
            rx, ry = self.hist(r, lag)
            if deep:
                tx, ty = max(zx, rx + 2.2), 0.7 * ry + 0.3 * zy
            else:
                tx, ty = min(rx + S["under_off"], zx + S["band_under"] + 1.0), 0.85 * ry + 0.15 * zy
        else:
            tx, ty = zx, zy
        self.db_move(p, tx, ty, 0.95)
