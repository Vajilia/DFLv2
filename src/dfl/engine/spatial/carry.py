"""The ball carrier, the tackle, the fumble and the end of the play (Edition 8.48)."""
import math

from .core import FIELD_W, OL_ROLES, S

ANGLES = tuple(math.radians(a) for a in (-72, -50, -30, -13, 0, 13, 30, 50, 72))
TAUS = (0.35, 0.70, 1.05)
SACK_STATES = ("drop", "pocket", "throwing")


class CarryMixin:
    cdir = 1.0                                # +1 when the offense has the ball, -1 after a turnover

    # ------------------------------------------------------------------------------------------------------------ the carrier
    def t_carry(self, p):
        i = p.i
        d = self.cdir
        x, y = self.px[i], self.py[i]
        pr = p.prof
        vm = self.VM[i] * S["carry_f"]
        vis = pr.g("VIS", 0.45 * pr.awr + 18.0)
        sd = 0.7 * (1.0 - min(vis, 100.0) / 125.0)
        H = S["carry_horizon"]
        opp = range(11, 22) if p.side == 0 else range(0, 11)
        dp = []
        for j in opp:
            q = self.P[j]
            if q.eng is not None or q.stun > 0.0:
                dp.append((self.px[j], self.py[j], 0.15))
            else:
                dp.append((self.px[j], self.py[j], self.VM[j] * 0.9))
        pre = self.kind == "run" and d > 0 and x < self.los + 0.5 and i == self.hb
        hy = self.hole[1] if pre else 0.0
        cur = p.aux if isinstance(p.aux, float) else 0.0
        rnd = self.rng.gauss
        best, bs = 0.0, -1e9
        for a in ANGLES:
            dx, dy = d * math.cos(a), math.sin(a)
            sx, sy = dx * vm, dy * vm
            pen = 0.0
            for tau, wt in zip(TAUS, (1.3, 1.0, 0.7)):
                cx, cy = x + sx * tau, y + sy * tau
                for jx, jy, js in dp:
                    r = js * tau + 0.8
                    ddx, ddy = cx - jx, cy - jy
                    if ddx * ddx + ddy * ddy < r * r:
                        pen += (r - math.sqrt(ddx * ddx + ddy * ddy)) * wt
            ey = y + sy * H
            if ey < 0.8:
                pen += (0.8 - ey) * 2.0
            elif ey > FIELD_W - 0.8:
                pen += (ey - FIELD_W + 0.8) * 2.0
            score = sx * H - 1.15 * pen - 0.22 * abs(a - cur) + rnd(0.0, sd)
            if pre:
                score -= 0.28 * abs(ey - hy)
            if score > bs:
                bs, best = score, a
        p.aux = best
        tx, ty = x + d * math.cos(best) * 12.0, y + math.sin(best) * 12.0
        self.go(i, tx, max(0.3, min(FIELD_W - 0.3, ty)), S["carry_f"])

    # --------------------------------------------------------------------------------------------------------------- tackles
    def update_tackles(self):
        if self.bstate != "held" or self.holder is None:
            return
        h = self.holder
        ph = self.P[h]
        hx, hy = self.px[h], self.py[h]
        side = ph.side
        opp = range(11, 22) if side == 0 else range(0, 11)
        reach = S["tackle_reach"]
        near = []
        for j in opp:
            q = self.P[j]
            if q.stun > 0.0 or q.cd > 0.0:
                continue
            arm = q.eng is not None
            if arm and not (self.kind == "run" and q.eng.kind == "run" and q.eng.lev <= S["eng_arm_lev"] and ph.state != "carry_oob"):
                continue                                          # an engaged run defender can only reach out for a carrier who runs by
            dd = math.hypot(self.px[j] - hx, self.py[j] - hy)
            if dd <= (S["eng_arm_reach"] if arm else reach):
                near.append((dd, j))
        if not near:
            return
        near.sort()
        sack = ph.state in SACK_STATES
        rng = self.rng
        for k, (dd, j) in enumerate(near):
            q = self.P[j]
            q.cd = 0.5
            if sack:
                pru = q.prof.g("PRU", 45)
                pq = S["sack_prob"] + 0.004 * (pru - 60) - 0.003 * (ph.prof.g("PRS", 50) - 50) - 0.002 * (ph.prof.agi - 50) + 0.05 * k
            else:
                tak = q.prof.g("TAK", 28 if q.role in OL_ROLES else 45)
                elu = ph.prof.g("ELU", 0.5 * ph.prof.agi + 0.3 * ph.prof.g("SPD", 50) + 0.2 * ph.prof.stren)
                rel = math.hypot(self.vx[j] - self.vx[h], self.vy[j] - self.vy[h])
                pq = (S["tackle_base"] + S["tackle_gain"] * (tak - elu) + S["tackle_speed"] * (rel - 6.0)
                      - 0.003 * (ph.prof.wt - q.prof.wt) + S["tackle_gang"] * k + 0.04 * (len(near) - 1 - k))
                if (self.px[j] - hx) * self.vx[h] + (self.py[j] - hy) * self.vy[h] < 0.0:
                    pq *= S["tackle_behind"]                              # from behind she is harder to bring down
                if q.eng is not None:
                    pq *= S["eng_arm_p"]
            if rng.random() < max(0.08, min(0.97, pq)):
                self.tackled(h, j, [m for (_, m) in near if m != j], sack)
                return
            q.stun = S["tackle_miss_stun"]
            self.V[h] *= S["tackle_miss_slow"]
            self.ev("miss", j, h)
            self.info["broken"] = self.info.get("broken", 0) + 1

    def tackled(self, h, j, others, sack):
        ph, q = self.P[h], self.P[j]
        d = self.cdir
        fwd = 0.0 if sack else S["fall_forward"] * max(0.2, min(1.6, 1.0 + 0.012 * (ph.prof.stren - q.prof.stren)))
        fwd *= 1.0 / (1.0 + 0.6 * len(others))
        self.ev("tackle", j, h)
        self.spot = (self.px[h] + d * fwd, self.py[h])
        self.info["tackler"] = j
        self.info["assist"] = [m for m in others if self.rng.random() < 0.8][:2]
        self.info["sack"] = sack
        # a strip: strength of the tackler against the ball security of the carrier
        bse = ph.prof.g("BSE", 45)
        pf = S["fumble_base"] * (1.0 + 0.02 * (q.prof.stren - 50)) * (1.0 - 0.012 * (bse - 50)) * (3.0 if sack else 1.0)
        if self.rng.random() < max(0.0, pf):
            self.fumble(h, j)
        else:
            self.end("sack" if sack else "tackle")

    def fumble(self, h, j):
        self.info["fumbler"] = h
        self.info["ff"] = j
        self.fumbled_by_side = self.P[h].side
        self.bstate = "loose"
        self.bx, self.by_ = self.px[h], self.py[h]
        a = self.rng.uniform(0, 2 * math.pi)
        sp = self.rng.uniform(1.5, 4.5)
        self.bvx = self.vx[h] * 0.4 + sp * math.cos(a)
        self.bvy = self.vy[h] * 0.4 + sp * math.sin(a)
        self.bz = 0.8
        self.loose_t = 0.0
        self.holder = None
        self.ev("fumble", h, j)
        for p in self.P:
            if p.eng is None:
                p.state = "loose"
                p.stun = 0.0

    def t_loose(self, p):
        self.go(p.i, self.bx, self.by_, 1.0)

    def update_loose(self):
        dt = 0.05
        self.bx += self.bvx * dt
        self.by_ += self.bvy * dt
        self.bvx *= 0.93
        self.bvy *= 0.93
        self.bz = max(0.0, self.bz - 0.2 * dt)
        self.loose_t += dt
        if self.by_ < 0.0 or self.by_ > FIELD_W:
            self.spot = (self.bx, max(0.0, min(FIELD_W, self.by_)))
            self.oob = True
            self.info["rec_side"] = self.fumbled_by_side
            self.info["recover"] = None
            self.end("fumble_oob")
            return
        if self.loose_t < 0.3:
            return
        near = []
        for p in self.P:
            if p.stun > 0.0 and p.eng is not None:
                continue
            dd = math.hypot(self.px[p.i] - self.bx, self.py[p.i] - self.by_)
            if dd <= 1.25:
                near.append((p, dd))
        if not near:
            if self.loose_t > 4.0:
                self.spot = (self.bx, self.by_)
                self.info["recover"] = None
                self.info["rec_side"] = self.fumbled_by_side
                self.end("fumble_dead")
            return
        w = [max(0.1, (1.2 - dd) + 0.01 * p.prof.awr + 0.004 * p.prof.g("BAL", 40)) for p, dd in near]
        r = self.rng.random() * sum(w)
        win = near[-1][0]
        for (p, dd), wi in zip(near, w):
            r -= wi
            if r <= 0:
                win = p
                break
        self.spot = (self.bx, self.by_)
        self.info["recover"] = win.i
        self.info["rec_side"] = win.side
        self.end("fumble")

    # ----------------------------------------------------------------------------------------------------------------- the end
    def check_dead(self):
        if self.done or self.bstate != "held" or self.holder is None:
            return
        h = self.holder
        x, y = self.px[h], self.py[h]
        if y < 0.0 or y > FIELD_W:
            self.oob = True
            self.spot = (x, max(0.0, min(FIELD_W, y)))
            self.end("oob")
            return
        d = self.cdir
        if d > 0 and x >= 100.0:
            self.td = True
            self.spot = (100.0, y)
            self.end("td")
        elif d < 0 and x <= 0.0:
            self.td = True
            self.spot = (0.0, y)
            self.end("td")

    def build_outcome(self):
        P = self.P
        info = self.info
        spot = getattr(self, "spot", None) or (self.bx if self.bstate != "held" else self.px[self.holder or self.qb], self.by)
        ex = spot[0]
        card = lambda i: P[i].prof.card
        credits = []
        o = {"pass": self.kind == "pass", "oob": self.oob, "turnover": None, "td": self.td, "spatial": True}
        qb = card(self.qb)
        why = self.end_reason
        sack_hit = info.get("sack") and why in ("sack", "fumble")
        if self.kind == "run":
            carrier = self.runner if getattr(self, "runner", None) is not None else self.hb
            o.update(kind="run", carrier=card(carrier), yards=int(round(ex - self.los)))
        else:
            if self.intercepted_by is not None:
                o.update(kind="int", qb=qb, target=card(self.targeted) if self.targeted is not None else None,
                         yards=0, air=int(round(self.int_x - self.los)), turnover="int", ret_x=ex, ret_td=self.td)
            elif sack_hit and not self.scrambled:
                o.update(kind="sack", qb=qb, yards=int(round(ex - self.los)))
            elif self.scrambled:
                o.update(kind="scramble", qb=qb, carrier=qb, yards=int(round(ex - self.los)))
            elif self.caught_by is not None:
                o.update(kind="complete", qb=qb, target=card(self.caught_by), yards=int(round(ex - self.los)))
            else:
                o.update(kind="incomplete", qb=qb, target=card(self.targeted) if self.targeted is not None else None, yards=0)
                if self.info.get("pd") is not None:
                    pass
        # fumbles
        if info.get("fumbler") is not None:
            rec = info.get("recover")
            side = info.get("rec_side")
            if rec is not None and side == (1 if o["kind"] != "int" else 0):
                o["turnover"] = "fumble" if o["kind"] != "int" else o["turnover"]
            elif side is not None and o["kind"] != "int":
                o["fumble"] = True
            if o["kind"] == "int" and rec is not None and side == 0:
                o["turnover"] = None                       # the offense got it back
            if o["kind"] != "int":
                o["yards"] = int(round(ex - self.los))
        # tackle and sack credit, from who was on the spot
        t = info.get("tackler")
        if t is not None:
            if info.get("sack") and not self.scrambled and o["kind"] == "sack":
                half = bool(info.get("assist")) and self.rng.random() < 0.0
                credits.append((card(t), "sack", 1.0))
                credits.append((card(t), "tfl", 1.0))
                o["sacker"] = [card(t).identity.id]
            else:
                credits.append((card(t), "tkl", 1.0))
                if o["yards"] <= 0 and o["kind"] in ("run", "scramble") and P[t].side == 1:
                    credits.append((card(t), "tfl", 1.0))
                for a in info.get("assist", []):
                    credits.append((card(a), "ast", 1.0))
                o["tackler"] = card(t).identity.id
                if info.get("assist"):
                    o["assist"] = card(info["assist"][0]).identity.id
        if info.get("ff") is not None:
            credits.append((card(info["ff"]), "ff", 1.0))
            o["ff"] = card(info["ff"]).identity.id
        if info.get("recover") is not None:
            credits.append((card(info["recover"]), "fr", 1.0))
            o["fr"] = card(info["recover"]).identity.id
        if self.intercepted_by is not None:
            credits.append((card(self.intercepted_by), "def_int", 1.0))
            o["int_by"] = card(self.intercepted_by).identity.id
        if info.get("pd") is not None:
            credits.append((card(info["pd"]), "pd", 1.0))
            o["pd"] = card(info["pd"]).identity.id
        if self.kind == "pass" and getattr(self, "bax", None) is not None and not getattr(self, "thrown_away", False):
            o["aim"] = self.bax - self.los
        if self.caught_by is not None and o["kind"] == "complete":
            o["yac"] = o["yards"] - (self.catch_x - self.los)
        o["thrown_away"] = bool(getattr(self, "thrown_away", False)) and o["kind"] == "incomplete"
        o["credits"] = credits
        o["end"] = why
        return o
