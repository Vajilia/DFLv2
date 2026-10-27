"""Passing plays: protection, routes, the quarterback's reads, the throw, the catch contest (Edition 8.48)."""
import math

from .core import DT, FIELD_W, MID, ROUTES, S
from .runplay import match

SET_T = {3: 1.35, 5: 1.95, 7: 2.5}
DROP_UC = {3: 3.8, 5: 5.6, 7: 7.4}              # depth behind the line when under centre
DROP_GUN = {3: 5.0, 5: 6.3, 7: 7.6}


class PassMixin:
    def _setup_pass(self):
        pl = self.play
        rng = self.rng
        f = self.formation
        from .core import FORMATIONS
        gun = FORMATIONS[f]["gun"]
        self.gun = gun
        dr = pl["drop"]
        self.set_t = max(0.75, SET_T[dr] - (0.5 if gun else 0.0))
        depth = (DROP_GUN if gun else DROP_UC)[dr]
        self.drop_pt = (self.los - depth, self.py[self.qb])
        self.prog = list(pl["progression"])
        self.pending = None
        self.targeted = None
        self.caught_by = None
        self.intercepted_by = None
        self.scrambled = False
        self.hurry = False
        self.int_x = 0.0
        self.tried = set()
        q = self.P[self.qb].prof
        self.perc = S["qb_perception"] * max(0.2, (100.0 - q.awr) / 50.0) * (1.0 - S["edge_perc"] * self.oc)
        self.vb = S["ball_speed_base"] + S["ball_speed_per_thp"] * q.g("THP", 50)
        self.P[self.qb].state = "drop"
        self.fake = bool(pl.get("fake"))
        self.screen = bool(pl.get("screen"))
        # routes
        blockers = list(self.OL)
        for role, route in pl["routes"].items():
            i = self.idx[role]
            p = self.P[i]
            r = ROUTES[route]
            if route == "block":
                blockers.append(i)
                p.state = "pblock"
                continue
            p.state = "route"
            p.route = route
            p.speed = r["speed"]
            sgn = 1.0 if self.py[i] >= MID else -1.0
            x0, y0 = self.px[i], self.py[i]
            sx = max(0.0, self.los - x0) if False else 0.0
            p.wps = [(x0 + dx, max(1.2, min(FIELD_W - 1.2, y0 + sgn * dy))) for dx, dy in r["wps"]]
            if route in ("go", "seam", "post", "corner", "wheel", "dig", "out", "cross"):
                p.wps = [(max(self.los + 0.5, x) if k == 0 and x < self.los else x, y) for k, (x, y) in enumerate(p.wps)]
            p.wpi = 0
        # protection: blockers against the rushers
        rushers = [j for j in self.DEF if self.P[j].state == "rush"]
        px, py, los = self.px, self.py, self.los
        cost = lambda a, d: abs(py[d] - py[a]) + 0.3 * abs(px[d] - px[a]) + (0.4 if self.P[d].role in ("lb_l", "mlb", "lb_r", "ss", "fs") else 0.0)
        pairs = match(cost, blockers, rushers)
        for a in blockers:
            pa = self.P[a]
            if a in self.OL:
                pa.state = "pblock"
            if a in pairs:
                pa.mark = pairs[a]
        self.free_rush = [j for j in rushers if j not in pairs.values()]
        self.hot = bool(self.free_rush)
        if self.hot:
            self.set_t = min(self.set_t, 1.0)
        if self.screen:
            self.screen_t = 1.5
        # press release
        for role in ("x", "z", "s", "te", "hb"):
            i = self.idx[role]
            p = self.P[i]
            if p.state != "route":
                continue
            for j in self.DEF:
                if abs(self.py[j] - self.py[i]) < 1.6 and 0.2 < self.px[j] - self.px[i] < 2.2 and self.P[j].state in ("man", "press"):
                    cb = self.P[j].prof
                    d = 0.12 + 0.004 * (cb.g("COV", 50) + 0.5 * cb.stren - p.prof.g("RTE", 45) - 0.5 * p.prof.stren)
                    p.delay = max(0.0, min(0.7, d))
                    break

    # -------------------------------------------------------------------------------------------------------- protection
    def t_pblock(self, p):
        i = p.i
        m = p.mark
        if self.screen and self.t > self.screen_t and i in self.OL and i not in (self.idx["lt"], self.idx["rt"]):
            p.state = "seek"
            p.mark = None
            return
        if m is None or self.P[m].state != "rush":
            m = None
            best, bd = None, 1e9
            for j in self.DEF:
                q = self.P[j]
                if q.state != "rush":
                    continue
                n = 2 if (q.eng is not None and q.eng.b2 is None) else (0 if q.eng is None else 9)
                dd = math.hypot(self.px[j] - self.px[i], self.py[j] - self.py[i]) + 2.0 * n
                if dd < bd:
                    best, bd = j, dd
            m = p.mark = best
            if m is None:
                self.stop(i)
                return
        qx, qy = self.px[self.qb], self.py[self.qb]
        jx, jy = self.px[m], self.py[m]
        dx, dy = qx - jx, qy - jy
        n = math.hypot(dx, dy) or 1.0
        # stand between the rusher and the quarterback, one yard in front of the rusher
        tx, ty = jx + dx / n * 1.0, jy + dy / n * 1.0
        self.go(i, tx, ty, 0.9)

    # ---------------------------------------------------------------------------------------------------- quarterback
    def t_drop(self, p):
        i = p.i
        if self.holder != i:
            return
        tx, ty = self.drop_pt
        if self.px[i] - tx > 0.35 and self.px[i] > tx:
            self.go(i, tx, ty, 0.8)
        else:
            self.stop(i)
            if self.t >= self.set_t:
                p.state = "pocket"
                self.ev("set", i)

    def free_rushers(self):
        out = []
        qx, qy = self.px[self.qb], self.py[self.qb]
        for j in self.DEF:
            q = self.P[j]
            if q.eng is None and q.stun <= 0.0 and q.state in ("rush", "pursue"):
                out.append((math.hypot(self.px[j] - qx, self.py[j] - qy), j))
        out.sort()
        return out

    def t_pocket(self, p):
        i = p.i
        if self.pending or self.holder != i or self.bstate != "held":
            return
        t = self.t
        fr = self.free_rushers()
        self.hurry = bool(fr) and fr[0][0] < S["press_near"]
        near = fr[0][0] if fr else 99.0
        k = int(max(0.0, t - self.set_t) / S["prog_dt"])
        cycle, idx = divmod(k, len(self.prog))
        limit = S["pocket_time"] - (0.55 if self.hurry else 0.0) - (0.4 if self.hot else 0.0)
        role = self.prog[idx]
        if self.hot and k >= 2:
            role = "hb" if k % 2 == 0 and "hb" in self.prog else self.prog[idx]
        margin = S["open_margin"] * (1.1 - 0.22 * min(k, 6) / 2.0) - (0.35 if self.hurry else 0.0)
        margin = max(0.15, margin)
        r = self.idx[role]
        if self.P[r].state in ("route", "settle"):
            s, T, ax, ay = self.open_est(r)
            need = margin * (1.0 + max(0.0, ax - self.px[i]) / S["depth_risk"]) * (1.0 + (S["check_pen"] if role == "hb" else 0.0))
            if s >= need and self.lane_ok(r):
                self.decision = (t, role, s, "read")
                self.begin_throw(r, T, ax, ay)
                return
        if t >= limit:
            best = None
            for rr in self.prog:
                ri = self.idx[rr]
                if self.P[ri].state not in ("route", "settle"):
                    continue
                s, T, ax, ay = self.open_est(ri)
                if best is None or s > best[0]:
                    best = (s, ri, T, ax, ay)
            if best is not None and best[0] > 0.45:
                self.decision = (t, self.P[best[1]].role, best[0], "forced")
                self.begin_throw(best[1], best[2], best[3], best[4])
                return
            self.escape(p, near)
            return
        if self.hurry and near < S["press_go"] and t > self.set_t + 0.2:
            best = None
            for rr in self.prog:
                ri = self.idx[rr]
                if self.P[ri].state not in ("route", "settle"):
                    continue
                s, T, ax, ay = self.open_est(ri)
                if best is None or s > best[0]:
                    best = (s, ri, T, ax, ay)
            if self.bail is None:
                self.bail = self.rng.random() < S["press_bail"] * (1.25 - 0.5 * min(1.0, p.prof.g("AWR", 60) / 90.0))
            if best is not None and best[0] > S["press_min"] and not self.bail:
                self.decision = (t, self.P[best[1]].role, best[0], "pressure")
                self.begin_throw(best[1], best[2], best[3], best[4])
                return
            self.escape(p, near)

    def escape(self, p, near):
        """Nobody is open: run if there is room and the legs for it, otherwise throw it away."""
        i = p.i
        pr = p.prof
        mob = (self.VM[i] - 7.2) / 2.0
        room = self.room_ahead(i)
        ps = max(0.05, min(0.75, S["scramble_base"] + 0.30 * mob)) * (1.0 if room > 7.0 else 0.35)
        if self.rng.random() < ps:
            p.state = "carry"
            self.scrambled = True
            self.runner = i
            self.ev("scramble", i)
            return
        self.begin_throw(None, 0.0, 0.0, 0.0)

    def room_ahead(self, i):
        x, y = self.px[i], self.py[i]
        best = 30.0
        for j in self.DEF:
            dx, dy = self.px[j] - x, self.py[j] - y
            if dx > 0.0 and abs(dy) < 3.0 + 0.25 * dx:
                best = min(best, dx)
        return best

    # ---------------------------------------------------------------------------------------------- the quarterback's eyes
    def future_pos(self, r, T):
        """Where receiver r will be T seconds from now if she runs the rest of her route at her speed."""
        p = self.P[r]
        x, y = self.px[r], self.py[r]
        if p.state != "route":
            return x, y
        dist = max(0.0, self.VM[r] * p.speed * 0.9 * T)
        for k in range(p.wpi, len(p.wps)):
            wx, wy = p.wps[k]
            d = math.hypot(wx - x, wy - y)
            if d >= dist:
                if d < 1e-6:
                    return x, y
                return x + (wx - x) * dist / d, y + (wy - y) * dist / d
            dist -= d
            x, y = wx, wy
        return x, y

    def open_est(self, r):
        """(perceived separation, flight time, aim x, aim y) for a throw to receiver r."""
        qb = self.qb
        qx, qy = self.px[qb], self.py[qb]
        rel = S["release_delay"]
        rx, ry = self.px[r], self.py[r]
        T = rel + math.hypot(rx - qx, ry - qy) / self.vb
        for _ in range(2):
            fx, fy = self.future_pos(r, T)
            T = rel + math.hypot(fx - qx, fy - qy) / self.vb
        fx, fy = self.future_pos(r, T)
        s = 99.0
        for j in self.DEF:
            q = self.P[j]
            if q.eng is not None and q.state == "rush":
                continue
            tt = min(T, 0.5)
            jx, jy = self.px[j] + self.vx[j] * tt, self.py[j] + self.vy[j] * tt
            d = math.hypot(fx - jx, fy - jy)
            if q.eng is None and q.state in ("zone", "man", "pursue"):
                d -= S["perc_close"] * self.VM[j] * max(0.0, T - tt - 0.15)       # a defender who is free closes on the ball while it is in the air
            d -= (0.3 if q.eng is None else -0.5)
            if d < s:
                s = d
        return s + self.rng.gauss(0.0, self.perc), T, fx, fy

    def lane_ok(self, r):
        """No defender stands in the flat part of the throw (the first fourteen yards of a throw to a nearby receiver)."""
        qx, qy = self.px[self.qb], self.py[self.qb]
        fx, fy = self.future_pos(r, 0.6)
        dx, dy = fx - qx, fy - qy
        L = math.hypot(dx, dy) or 1.0
        for j in self.DEF:
            q = self.P[j]
            if q.state == "rush" and q.eng is None and False:
                continue
            ex, ey = self.px[j] - qx, self.py[j] - qy
            tproj = (ex * dx + ey * dy) / (L * L)
            if tproj < 0.12 or tproj > 0.92:
                continue
            if tproj * L > 14.0:
                continue
            perp = abs(ex * dy - ey * dx) / L
            if perp < S["lane_width"] and q.state not in ("rush",):
                return False
        return True

    # ------------------------------------------------------------------------------------------------------------- the throw
    def begin_throw(self, r, T, ax, ay):
        qb = self.qb
        pq = self.P[qb]
        pq.state = "throwing"
        self.stop(qb)
        pr = pq.prof
        if r is None:                                                # thrown away, out of bounds past the nearest sideline
            qx, qy = self.px[qb], self.py[qb]
            side = 1.0 if qy >= MID else -1.0
            ax, ay = qx + 8.0, (FIELD_W + 2.0 if side > 0 else -2.0)
            T = S["release_delay"] + math.hypot(ax - qx, ay - qy) / self.vb
            self.thrown_away = True
        else:
            self.thrown_away = False
        dist = math.hypot(ax - self.px[qb], ay - self.py[qb])
        sd = (S["acc_err_base"] + S["acc_err_dist"] * dist) * (1.9 - S["acc_err_tha"] * pr.g("THA", 50))
        if self.hurry:
            sd *= 1.0 + S["acc_err_press"]
        ix, iy = ax, ay
        if r is not None:
            ax += self.rng.gauss(0.0, sd)
            ay += self.rng.gauss(0.0, sd)
        rel = S["release_delay"] * (1.4 if self.hurry else 1.0)
        self.pending = {"r": r, "t_rel": self.t + rel, "ax": ax, "ay": ay, "ix": ix, "iy": iy, "T": max(0.35, T - S["release_delay"])}
        self.targeted = r
        self.ev("decide", qb, r if r is not None else -1)

    def launch(self):
        pd = self.pending
        self.pending = None
        qb = self.qb
        self.bx, self.by_, self.bz = self.px[qb], self.py[qb], 1.9
        self.bx0, self.by0 = self.bx, self.by_
        self.bt0 = self.t
        self.bT = pd["T"]
        self.bax, self.bay = pd["ax"], pd["ay"]
        k = S["adjust"]
        self.rax, self.ray = pd["ix"] + (pd["ax"] - pd["ix"]) * k, pd["iy"] + (pd["ay"] - pd["iy"]) * k    # where the receiver goes
        d = math.hypot(self.bax - self.bx0, self.bay - self.by0)
        self.bH = 0.045 * max(d, 3.0) ** 1.12
        self.bstate = "flight"
        self.holder = None
        self.ev("throw", qb, pd["r"] if pd["r"] is not None else -1)
        pq = self.P[qb]
        pq.state = "idle"
        if pd["r"] is not None:
            rp = self.P[pd["r"]]
            rp.state = "catch"
        if not self.screen:
            for e in self.engs:                                   # the pass is away: the blocks and rushes are over
                if e.state == "on":
                    e.state = "done"
                    for q in (e.a, e.d, e.b2):
                        if q is not None:
                            self.P[q].eng = None
            self.engs = [e for e in self.engs if e.state == "on"]
        for p in self.P:
            if p.side == 0 and p.state in ("pblock",):
                p.state = "idle"

    def t_catch(self, p):
        self.go(p.i, self.rax, self.ray, 1.0)

    def t_settle(self, p):
        self.stop(p.i)

    # ----------------------------------------------------------------------------------------------------------- the ball
    def update_ball(self):
        if self.done:
            return
        if self.pending is not None and self.t >= self.pending["t_rel"]:
            self.launch()
        if self.bstate == "flight":
            self.flight()
        elif self.bstate == "loose":
            self.update_loose()
        self.check_dead()

    def flight(self):
        tau = (self.t - self.bt0) / self.bT
        t1 = min(tau, 1.0)
        self.bx = self.bx0 + (self.bax - self.bx0) * t1
        self.by_ = self.by0 + (self.bay - self.by0) * t1
        z1 = 1.1
        self.bz = 1.9 + (z1 - 1.9) * t1 + 4.0 * self.bH * t1 * (1.0 - t1)
        if tau > 1.0:
            self.bz = max(0.0, z1 - (tau - 1.0) * self.bT * 5.0)
            self.bx += (self.bax - self.bx0) / self.bT * (tau - 1.0) * self.bT * 0.2
        if self.thrown_away:
            if tau >= 1.0 or self.by_ < -0.5 or self.by_ > FIELD_W + 0.5:
                self.spot = (self.bx, self.by_)
                self.end("incomplete")
            return
        if self.bz > S["catch_height"]:
            return
        rad = S["catch_radius"]
        cr, cd = [], []
        for r in self.recv:
            if self.P[r].state in ("pblock", "rblock"):
                continue
            dd = math.hypot(self.px[r] - self.bx, self.py[r] - self.by_)
            if dd <= rad:
                cr.append((dd, r))
        for j in self.DEF:
            q = self.P[j]
            if q.eng is not None or q.stun > 0.0:
                continue
            dd = math.hypot(self.px[j] - self.bx, self.py[j] - self.by_)
            if dd <= (rad if q.role in ("cb_l", "cb_r", "fs", "ss", "mlb", "lb_l", "lb_r") else 0.9):
                if j not in self.tried:
                    cd.append((dd, j))
        if cr or cd:
            self.contest(sorted(cr), sorted(cd))
        elif tau >= 1.15 and self.bz <= 0.25:
            self.spot = (self.bx, self.by_)
            self.end("incomplete")
        elif self.by_ < -0.5 or self.by_ > FIELD_W + 0.5:
            self.end("incomplete")

    def skill_r(self, i):
        pr = self.P[i].prof
        return 0.6 * pr.g("HND", 42) + 0.4 * pr.g("CTC", pr.g("HND", 42))

    def skill_d(self, j):
        pr = self.P[j].prof
        return 0.55 * pr.g("COV", 38) + 0.45 * pr.g("BAL", 38)

    def contest(self, cr, cd):
        rng = self.rng
        for _, j in cd:
            self.tried.add(j)
        r = cr[0][1] if cr else None
        d = max(cd, key=lambda a: self.skill_d(a[1]) - 8.0 * a[0])[1] if cd else None
        tgt = self.targeted
        if r is not None and d is None:
            pr = self.P[r].prof
            pc = S["catch_free"] + 0.0025 * (self.skill_r(r) - 50.0) - 0.04 * (cr[0][0] / S["catch_radius"])
            if r != tgt:
                pc -= 0.1
            if self.hurry:
                pc -= 0.02
            if rng.random() < max(0.3, min(0.995, pc)):
                self.catch(r)
            else:
                self.drop(r)
            return
        if r is None:
            if rng.random() < S["touch_lane"] + 0.002 * (self.skill_d(d) - 45.0):
                if rng.random() < S["int_lane"] + 0.004 * (self.skill_d(d) - 45.0):
                    self.intercept(d)
                else:
                    self.info["pd"] = d
                    self.spot = (self.bx, self.by_)
                    self.ev("batted", d)
                    self.end("incomplete")
            return
        # both reach the ball
        pr_win = 0.50 + S["contest_gain"] * (self.skill_r(r) - self.skill_d(d)) + 0.18 * (cd and (cd[0][0] > cr[0][0]) and 1 or -1) * 0.5 \
            + 0.12 * (self.P[r].prof.ht - self.P[d].prof.ht) / 20.0 - 0.05 * (1 if self.hurry else 0)
        pr_win = max(0.12, min(0.9, pr_win))
        if rng.random() < pr_win:
            pc = S["catch_contested"] + 0.003 * (self.skill_r(r) - 50.0)
            if rng.random() < min(0.95, pc):
                self.catch(r)
            else:
                self.drop(r)
        else:
            pi = S["int_contested"] + 0.004 * (self.skill_d(d) - 45.0)
            if rng.random() < min(0.9, pi):
                self.intercept(d)
            else:
                self.info["pd"] = d
                self.spot = (self.bx, self.by_)
                self.ev("breakup", d, r)
                self.end("incomplete")

    def drop(self, r):
        self.spot = (self.bx, self.by_)
        self.ev("drop", r)
        self.end("incomplete")

    def catch(self, r):
        self.caught_by = r
        self.catch_x = self.px[r]
        self.holder = r
        self.bstate = "held"
        self.ev("catch", r)
        self.P[r].state = "carry"
        self.P[r].aux = None
        self.V[r] *= S["catch_slow"]
        self.after_catch()

    def intercept(self, j):
        self.intercepted_by = j
        self.int_x = self.bx
        self.holder = j
        self.bstate = "held"
        self.cdir = -1.0
        self.ev("intercept", j)
        for p in self.P:
            if p.side == 1:
                p.state = "escort" if p.i != j else "carry"
            else:
                p.state = "pursue"
            p.delay = 0.0
            p.mark = None
        self.P[j].aux = None
        self.P[j].state = "carry"

    def after_catch(self):
        for p in self.P:
            if p.side == 0 and p.state in ("pblock", "route", "settle", "catch", "idle") and p.i != self.holder:
                if self.screen or (p.i in self.recv and self.rng.random() < S["yac_block"]):
                    p.state = "seek"
                    p.mark = None
                else:
                    p.state = "idle"
                    self.stop(p.i)
            elif p.side == 1 and p.state != "carry":
                p.state = "pursue"
                p.delay = max(p.delay, self.t) if p.delay > self.t else 0.0
