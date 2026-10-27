"""One scrimmage play, played out by 22 players at the fixed timestep (Design Document section 10, Edition 8.48).

`PlaySim(...).run()` returns an outcome dict in the same shape the play-level resolver gives the game (kind, yards, turnover, oob, carrier,
qb, target, ...) plus the facts the box score needs (who tackled, sacked, intercepted, defended, forced or recovered a fumble), the seconds the
play took and, when asked for, a compressed trace of every position at 0.05 s.

The movement is vectorised (numpy) and the decisions are made every other step (0.10 s) in plain Python on lists, which is what keeps a
play near ten milliseconds.
"""
import math
import zlib

import numpy as np

from .align import Pl, build_defense, build_offense, defender_cards, offense_cards
from .core import COVERAGES, DEF_ROLES, DT, FIELD_W, MAX_STEPS, MID, OFF_ROLES, OL_ROLES, PASSES, RUNS, S
from .carry import CarryMixin
from .defense import DefMixin
from .passplay import PassMixin
from .runplay import RunMixin

_H = np.zeros((MAX_STEPS + 2, 22, 2), dtype=np.float32)     # position history, reused by every play (single threaded)
_B = np.zeros((MAX_STEPS + 2, 3), dtype=np.float32)


class Eng:
    """A block: blocker `a`, defender `d`, leverage in the blocker's favour, the direction the pair is driven, an optional second blocker."""
    __slots__ = ("a", "d", "kind", "lev", "dirx", "diry", "cx", "cy", "ux", "uy", "b2", "t0", "state")

    def __init__(self, a, d, kind, dirx, diry, cx, cy, ux, uy, t0):
        self.a, self.d, self.kind, self.lev = a, d, kind, 0.0
        self.dirx, self.diry, self.cx, self.cy, self.ux, self.uy = dirx, diry, cx, cy, ux, uy
        self.b2 = None
        self.t0 = t0
        self.state = "on"


class PlaySim(RunMixin, PassMixin, DefMixin, CarryMixin):
    def __init__(self, off_units, def_units, kind, play, cov, los, by, rng, fatigue=None, staff=(0.0, 0.0), trace=False, down=1, dist=10,
                 yl=None, lead=0):
        self.kind = kind                      # "run" or "pass"
        self.pname = play
        self.rng = rng
        self.los = float(los)
        self.by = float(by)
        self.fatigue = fatigue or {}
        self.oc, self.dc = (max(-1.0, min(1.0, v)) for v in staff)       # the offense's edge in z (coaches, home field), capped; the second is unused
        self.down, self.togo, self.lead = down, dist, lead
        self.rec = trace
        self.cov = COVERAGES[cov]
        self.covname = cov
        self.n = 0
        self.t = 0.0
        self.done = False
        self.engs = []
        self.events = []
        self.intents = []
        # the ball
        self.holder = None                    # index of the player holding it
        self.bstate = "held"                  # held | flight | loose | dead
        self.bx = self.by_ = self.bz = 0.0
        self.end_reason = None
        self.info = {}                        # facts for the box score
        self.fumble_by = None
        self.td = False
        self.oob = False
        self.last = {}
        self.last_state = {}
        self.hole = (float(los) + 1.2, float(by))
        self.pending = None
        self.hurry = False
        self.spot = None
        self.runner = None
        self.scrambled = False
        self.targeted = None
        self.caught_by = None
        self.intercepted_by = None
        self.int_x = 0.0
        self.thrown_away = False
        self.bail = None
        self.cards_off = offense_cards(off_units)
        self.cards_def = defender_cards(def_units)
        self._setup()

    # ------------------------------------------------------------------------------------------------------------------ setup
    def _setup(self):
        rng = self.rng
        if self.kind == "run":
            self.play = RUNS[self.pname]
        else:
            self.play = PASSES[self.pname]
        formation = rng.choice(self.play["formations"])
        self.mirror = rng.random() < 0.5
        offs = build_offense(self, self.cards_off, formation, self.mirror)
        self.formation = formation
        # arrays: offense 0..10 in OFF_ROLES order, defense 11..21 in DEF_ROLES order
        self.P = []
        self.idx = {}
        self.px, self.py = [0.0] * 22, [0.0] * 22
        for i, (role, prof, x, y) in enumerate(offs):
            pl = Pl(i, 0, role, prof)
            self.P.append(pl)
            self.idx[role] = i
            self.px[i], self.py[i] = x, y
        # defense needs the receivers' places
        defpos = build_defense(self, None, self.cov)
        for k, role in enumerate(DEF_ROLES):
            i = 11 + k
            pl = Pl(i, 1, role, self._prof(self.cards_def[role]))
            self.P.append(pl)
            self.idx[role] = i
            self.px[i], self.py[i] = defpos[role]
        self.vx, self.vy = [0.0] * 22, [0.0] * 22
        self.X = np.array([[self.px[i], self.py[i]] for i in range(22)], dtype=np.float64)
        self.V = np.zeros((22, 2))
        self.T = self.X.copy()
        self.F = np.zeros(22)
        self.VM = np.array([p.prof.vmax for p in self.P])
        self.AC = np.array([p.prof.acc for p in self.P])
        self.DC = np.array([p.prof.dec for p in self.P])
        self.rowd = np.arange(11, 22)
        # who is who
        self.qb = self.idx["qb"]
        self.hb = self.idx["hb"]
        self.recv = [self.idx[r] for r in ("x", "z", "s", "te", "hb")]
        self.OL = [self.idx[r] for r in OL_ROLES]
        self.DEF = list(range(11, 22))
        self.free_d = np.ones(11, dtype=bool)
        self.qb_card = self.P[self.qb].prof.card
        self.ball_to(self.qb)
        if self.kind == "run":
            self._setup_run()
            self._setup_defense()
        else:
            self._setup_defense()
            self._setup_pass()

    def _prof(self, card):
        from .core import Prof
        return Prof(card, self.fatigue.get(card.identity.id, 1.0))

    # --------------------------------------------------------------------------------------------------------------------- ball
    def ball_to(self, i):
        self.holder = i
        self.bstate = "held"

    def bpos(self):
        if self.bstate == "held" and self.holder is not None:
            return self.px[self.holder], self.py[self.holder]
        return self.bx, self.by_

    # ------------------------------------------------------------------------------------------------------------------- helpers
    def free(self, j):
        p = self.P[j]
        return p.eng is None and p.stun <= 0.0

    def dist(self, i, j):
        return math.hypot(self.px[i] - self.px[j], self.py[i] - self.py[j])

    def ev(self, kind, a=-1, b=-1):
        self.events.append((self.n, kind, a, b))

    def intent(self, i, code, ref=-1):
        if self.rec:
            self.intents.append((self.n, i, code, ref))

    def go(self, i, x, y, f=1.0):
        """Set player i's target point and the share of top speed to use."""
        self.T[i, 0] = x
        self.T[i, 1] = y
        self.F[i] = f

    def stop(self, i):
        self.T[i] = self.X[i]
        self.F[i] = 0.0

    # ------------------------------------------------------------------------------------------------------------------- the loop
    def step(self):
        self.sync()
        if self.n % 2 == 0:
            self.think()
        self.move()
        self.sync()
        self.contact()
        _H[self.n + 1] = self.X
        bx, by, bz = self.ball_xyz()
        _B[self.n + 1] = (bx, by, bz)
        self.n += 1
        self.t = self.n * DT

    def run(self):
        while not self.done and self.n < MAX_STEPS:
            self.step()
        if not self.done:
            self.end("time")
        return self.outcome()

    def sync(self):
        X, V = self.X, self.V
        self.px = X[:, 0].tolist()
        self.py = X[:, 1].tolist()
        self.vx = V[:, 0].tolist()
        self.vy = V[:, 1].tolist()
        if self.n == 0:
            _H[0] = X

    def ball_xyz(self):
        if self.bstate == "held" and self.holder is not None:
            return self.px[self.holder], self.py[self.holder], 1.0
        return self.bx, self.by_, self.bz

    def move(self):
        X, V, T = self.X, self.V, self.T
        d = T - X
        dist = np.hypot(d[:, 0], d[:, 1]) + 1e-9
        cmd = np.minimum(self.VM * self.F, np.sqrt(2.0 * self.DC * np.maximum(dist - 0.04, 0.0)))
        vd = d / dist[:, None] * cmd[:, None]
        dv = vd - V
        m = np.hypot(dv[:, 0], dv[:, 1]) + 1e-9
        sp = np.hypot(V[:, 0], V[:, 1]) + 1e-9
        opp = np.clip(-(dv[:, 0] * V[:, 0] + dv[:, 1] * V[:, 1]) / (m * sp), 0.0, 1.0)      # how much of the change is braking
        fwd = np.clip((dv[:, 0] * V[:, 0] + dv[:, 1] * V[:, 1]) / (m * sp), 0.0, 1.0)         # how much of it is speeding up along the heading
        decay = 1.0 - (1.0 - np.clip(1.25 - sp / self.VM, 0.2, 1.0)) * fwd
        a = (self.AC + (self.DC - self.AC) * opp) * decay
        V += dv * np.minimum(1.0, a * DT / m)[:, None]
        X += V * DT

    # ------------------------------------------------------------------------------------------------------------ think and contact
    def think(self):
        t = self.t
        for p in self.P:
            if p.stun > 0.0:
                p.stun -= 0.1
                if p.stun > 0.0:
                    self.stop(p.i)
                    continue
            if p.cd > 0.0:
                p.cd -= 0.1
            if self.rec and self.last_state.get(p.i) != p.state:
                self.last_state[p.i] = p.state
                self.intents.append((self.n, p.i, p.state, -1 if p.mark is None else p.mark))
            if p.delay > t:
                continue
            if p.eng is not None:
                self.stop(p.i)
                continue
            getattr(self, "t_" + p.state)(p)

    def contact(self):
        if self.n % 2 == 0:
            self.auto_engage()
        self.update_engagements()
        self.separate()
        self.sync()
        self.update_ball()
        if not self.done:
            self.update_tackles()
            self.check_dead()

    def auto_engage(self):
        run = self.kind == "run"
        r2 = S["eng_range"]
        for i in range(0, 11):
            p = self.P[i]
            if p.eng is not None or p.stun > 0.0 or p.state not in ("pblock", "rblock", "seek"):
                continue
            m = p.mark
            x, y = self.px[i], self.py[i]
            hit = None
            if m is not None and self.dist(i, m) <= r2 and (run or self.P[m].state == "rush"):
                hit = m
            else:
                bd = r2
                for j in self.DEF:
                    q = self.P[j]
                    if (not run) and q.state != "rush":
                        continue
                    dd = math.hypot(self.px[j] - x, self.py[j] - y)
                    if dd < bd:
                        hit, bd = j, dd
            if hit is not None:
                self.try_engage(i, hit, "run" if run else "pass")

    def separate(self):
        X = self.X
        dx = X[:, None, 0] - X[None, :, 0]
        dy = X[:, None, 1] - X[None, :, 1]
        r = np.hypot(dx, dy) + 1e-9
        rad = S["sep_radius"]
        m = (r < rad) & (r > 1e-6)
        if not m.any():
            return
        eng = np.array([p.eng is not None for p in self.P])
        w = np.where(m, (rad - r) / r, 0.0)
        push = np.stack([(w * dx).sum(axis=1), (w * dy).sum(axis=1)], axis=1) * 0.5
        push[eng] = 0.0
        mag = np.hypot(push[:, 0], push[:, 1])
        push *= np.minimum(1.0, 0.25 / np.maximum(mag, 1e-9))[:, None]
        X += push

    def t_idle(self, p):
        self.stop(p.i)

    t_throwing = t_idle

    def t_route(self, p):
        i = p.i
        x, y = self.px[i], self.py[i]
        if p.wpi >= len(p.wps):
            p.state = "settle"
            self.stop(i)
            return
        wx, wy = p.wps[p.wpi]
        last = p.wpi == len(p.wps) - 1
        if math.hypot(wx - x, wy - y) < (0.45 if last else 1.0):
            p.wpi += 1
            if p.wpi >= len(p.wps):
                p.state = "settle"
                self.stop(i)
                return
            wx, wy = p.wps[p.wpi]
            last = p.wpi == len(p.wps) - 1
        if not last:                                          # run through the break: aim past the point toward the next one
            nx, ny = p.wps[p.wpi + 1]
            d = math.hypot(nx - wx, ny - wy) or 1.0
            if math.hypot(wx - x, wy - y) < 2.5:
                wx, wy = wx + (nx - wx) / d * 3.0, wy + (ny - wy) / d * 3.0
        self.go(i, wx, wy, p.speed)

    def end(self, why):
        self.done = True
        self.end_reason = why
        self.ev("end:" + why)

    # --------------------------------------------------------------------------------------------------------------- engagements
    def try_engage(self, b, d, kind):
        """Blocker b and defender d are engaged when they are close enough and neither is held up or stunned."""
        pb, pd = self.P[b], self.P[d]
        if pb.eng is not None or pd.stun > 0.0 or pb.stun > 0.0:
            return False
        if pd.eng is not None:
            e = pd.eng
            if e.b2 is None and e.kind == kind and e.a != b:
                e.b2 = b                              # a second blocker joins: a double team
                pb.eng = e
                return True
            return False
        if self.t - self.last.get((b, d), -9.0) < 0.5:
            return False
        ux, uy = self.px[d] - self.px[b], self.py[d] - self.py[b]
        m = math.hypot(ux, uy) or 1.0
        ux, uy = ux / m, uy / m
        if kind == "run":
            hx, hy = self.hole
            sgn = 1.0 if self.py[d] >= hy else -1.0
            dx_, dy_ = 0.55, 0.835 * sgn
        else:
            dx_, dy_ = -0.2, 0.0
        e = Eng(b, d, kind, dx_, dy_, (self.px[b] + self.px[d]) / 2, (self.py[b] + self.py[d]) / 2, ux, uy, self.t)
        pb.eng = pd.eng = e
        if kind == "pass" and (b, d) in self.last:
            e.lev = -0.5
        self.engs.append(e)
        self.ev("engage:" + kind, b, d)
        return True

    def block_rating(self, p, kind):
        pr = p.prof
        g = pr.g
        size = (pr.wt - 80.0) * S["eng_size"]
        if kind == "run":
            return 0.55 * g("RBK", 45) + 0.2 * pr.stren + 0.1 * g("TEC", 55) + 0.08 * pr.awr + 0.07 * pr.agi + size
        return 0.55 * g("PBK", 52) + 0.2 * pr.stren + 0.1 * g("TEC", 55) + 0.08 * pr.awr + 0.07 * pr.agi + size

    def shed_rating(self, p, kind):
        pr = p.prof
        g = pr.g
        size = (pr.wt - 80.0) * S["eng_size"]
        pos = pr.pos
        if kind == "run":
            if pos in ("LE", "RE", "DT"):
                return 0.45 * g("BSH", 60) + 0.25 * g("RDF", 60) + 0.15 * pr.stren + 0.08 * pr.awr + 0.07 * pr.agi + size
            if pos in ("ROLB", "MLB", "LOLB"):
                return 0.4 * g("TAK", 55) + 0.3 * pr.stren + 0.15 * pr.awr + 0.15 * pr.agi - 4.0 + size
            if pos in ("CB", "FS", "SS"):
                return 0.35 * g("TAK", 55) + 0.25 * pr.stren + 0.2 * pr.awr + 0.2 * pr.agi - 12.0 + size
            return 0.3 * pr.stren + 0.7 * 50.0 + size
        return 0.5 * g("PRU", 50) + 0.15 * g("ACC", 50) + 0.15 * pr.stren + 0.1 * pr.agi + 0.1 * pr.awr + size

    def update_engagements(self):
        if not self.engs:
            return
        rng = self.rng
        gain = S["eng_gain_run"] if self.kind == "run" else S["eng_gain_pass"]
        noise = S["eng_noise"] * math.sqrt(DT)
        win = S["eng_win"]
        alive = []
        for e in self.engs:
            if e.state != "on":
                continue
            pb, pd = self.P[e.a], self.P[e.d]
            if e.b2 is not None and e.kind == "run" and e.lev >= S["climb_lev"]:
                for q in (e.b2, e.a):                    # the blocker with a linebacker assigned climbs to him once the first man is handled
                    pq = self.P[q]
                    if isinstance(pq.aux, tuple) and pq.aux[0] == "combo":
                        self.climb(e, q)
                        break
            rb = self.block_rating(pb, e.kind)
            if e.b2 is not None:
                rb += S["eng_double_gain"] * self.block_rating(self.P[e.b2], e.kind)
            rd = self.shed_rating(pd, e.kind)
            fb = 1.0 - 0.1 * (1.0 - self.fatigue.get(pb.prof.id, 1.0))
            fd = 1.0 - 0.1 * (1.0 - self.fatigue.get(pd.prof.id, 1.0))
            bias = S["eng_bias_pass"] if e.kind == "pass" else S["eng_bias_run"] + (S["eng_bias_perim"] if pb.role in ("x", "z", "s", "hb") else 0.0)
            e.lev += DT * (gain * (rb * fb * (1.0 + S["edge_eng"] * self.oc) - rd * fd) / 30.0 - bias) + noise * rng.gauss(0.0, 1.0)
            lv = max(-1.6, min(1.6, e.lev))
            # the pair moves
            if e.kind == "run":
                sp = S["eng_drive_run"] * max(-0.5, min(1.0, lv)) + 0.15
                e.cx += e.dirx * sp * DT
                e.cy += e.diry * sp * DT
            else:
                sp = S["eng_back_pass"] * max(0.0, -lv)
                qx, qy = self.px[self.qb], self.py[self.qb]
                ddx, ddy = qx - e.cx, qy - e.cy
                m = math.hypot(ddx, ddy) or 1.0
                e.cx += ddx / m * sp * DT
                e.cy += ddy / m * sp * DT
            e.cx = max(0.5, min(FIELD_W - 0.5, e.cx))
            self.place_pair(e)
            if e.lev <= -win:                         # the defender wins: the rusher is free, or the run defender sheds the block
                self.release(e, "shed")
                continue
            if e.kind == "run" and e.lev >= win + 0.8 and not pd.stun:
                pass                                 # sealed: the defender stays tied up
            alive.append(e)
        self.engs = alive

    def climb(self, e, q):
        pq = self.P[q]
        if q == e.a:
            e.a, e.b2 = e.b2, e.a
        pq.eng = None
        e.b2 = None
        pq.mark = pq.aux[2]
        pq.aux = None
        pq.stun = 0.0
        self.ev("climb", q, pq.mark)

    def place_pair(self, e):
        """Hold the engaged players at the engagement's geometry, closing any gap at no more than a step's worth of running."""
        want = [(e.a, e.cx - 0.5 * e.ux * 0.9, e.cy - 0.5 * e.uy * 0.9), (e.d, e.cx + 0.5 * e.ux * 0.9, e.cy + 0.5 * e.uy * 0.9)]
        if e.b2 is not None:
            want.append((e.b2, e.cx - 0.5 * e.ux * 0.9 + 0.2, e.cy - 0.5 * e.uy * 0.9 + (0.9 if self.py[e.b2] >= e.cy else -0.9)))
        cap = S["eng_snap"]
        for q, tx, ty in want:
            dx, dy = tx - self.X[q, 0], ty - self.X[q, 1]
            n = (dx * dx + dy * dy) ** 0.5
            if n > cap:
                dx, dy = dx * cap / n, dy * cap / n
            self.X[q, 0] += dx
            self.X[q, 1] += dy
        dxv = e.dirx * 0.8 if e.kind == "run" else 0.0
        for q in (e.a, e.d, e.b2):
            if q is not None:
                self.V[q] = (dxv, e.diry * 0.8 if e.kind == "run" else 0.0)
                self.T[q] = self.X[q]
                self.F[q] = 0.0

    def release(self, e, why):
        e.state = why
        self.last[(e.a, e.d)] = self.t
        pb, pd = self.P[e.a], self.P[e.d]
        pb.eng = pd.eng = None
        if e.b2 is not None:
            self.P[e.b2].eng = None
        self.ev("shed" if e.kind == "run" else "beat", e.a, e.d)
        pb.stun = S["eng_shed_stun"] * (0.5 if e.kind == "pass" else 1.0)
        pb.mark = None if e.kind == "run" else pb.mark
        if e.b2 is not None:
            self.P[e.b2].stun = 0.2
        pd.stun = 0.0
        self.V[e.d] *= 0.5

    # ---------------------------------------------------------------------------------------------------------------- output
    def trace_blob(self):
        n = self.n + 1
        pos = np.round(_H[:n] * 100.0).astype(np.int16)
        ball = np.round(_B[:n] * 100.0).astype(np.int16)
        return {"steps": n, "dt": DT, "pos": zlib.compress(pos.tobytes(), 6), "ball": zlib.compress(ball.tobytes(), 6),
                "roles": [p.role for p in self.P], "ids": [p.prof.id for p in self.P], "events": list(self.events), "intents": list(self.intents),
                "los": self.los, "formation": self.formation, "play": self.pname, "coverage": self.covname}

    def outcome(self):
        o = self.build_outcome()
        o["secs"] = self.t + (S["tail_run"] if self.kind == "run" else S["tail_pass"])
        o["info"] = self.info
        if self.rec:
            o["trace"] = self.trace_blob()
        return o
