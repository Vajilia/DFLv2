"""Synthetic world for exercising the ledger: player supply, a deterministic autopilot, a year loop and audits.

EVERYTHING in this file about players (ratings, aging, injuries, prices) is a PLACEHOLDER MODEL. It exists to push the
ledger through realistic volumes of signings, releases, trades, injuries and cuts. It is not evidence about league balance.
"""
import random
from fractions import Fraction as F

from . import gameday, rules as R
from .contracts import Contract, ContractError, rookie_contract
from .ledger import Ledger, Rejected
from .positions import MINIMUMS, POSITIONS, shortfalls

# a 53-man template: the 48 required places (backfield as 3 HB) plus five extras
EXTRAS = ("WR", "CB", "DT", "MLB", "C")
TEMPLATE = dict(MINIMUMS, HB=3)
for _p in EXTRAS:
    TEMPLATE[_p] = TEMPLATE.get(_p, 0) + 1
assert sum(TEMPLATE.values()) == 53
PS_TEMPLATE = ("QB", "HB", "WR", "WR", "TE", "LT", "LG", "C", "RT", "LE", "DT", "ROLB", "CB", "CB", "FS", "LB")
PS_TEMPLATE = tuple("MLB" if p == "LB" else p for p in PS_TEMPLATE)
assert len(PS_TEMPLATE) == 16


def ask(player):
    """Market annual value of a player (placeholder price curve)."""
    v = 5 + max(0, player.ovr - 55) * F(16, 10)
    if player.pos == "QB":
        v *= F(3, 2)
    return F(int(v))


class World:
    def __init__(self, cids, seed=1, reckless=(), year=2000):
        self.rng = random.Random(seed)
        self.L = Ledger(cids, year)
        self.reckless = set(reckless)
        self.stats = {"rejections": {}, "trades": 0, "trades_rejected": 0, "claims": 0, "forfeits": 0, "short_games": 0,
                      "emergency_games": 0, "elevations": 0, "ir_moves": 0, "offseason_overage_max": F(0), "overage_max": F(0), "payroll_min": None,
                      "margin_min": None, "games": 0, "committed_max": F(0), "legal_move_failures": 0}
        self._found()

    # ---------- supply
    def new_prospect(self, pos, mean=55, sd=8, age=22, seasons=0):
        return self.L.new_player(pos, max(30, min(99, int(self.rng.gauss(mean, sd)))), age, seasons)

    def _reject(self, label, e):
        import re
        k = re.sub(r"\bC\d\d\b", "<club>", str(e))[:70]
        self.stats["rejections"][k] = self.stats["rejections"].get(k, 0) + 1

    def _found(self):
        """Founding Day: every club starts with an all-rookie 53 and a squad of 16."""
        L = self.L
        for cid in L.clubs:
            for pos, n in TEMPLATE.items():
                for _ in range(n):
                    p = self.new_prospect(pos, 58, 8, self.rng.randint(21, 24))
                    L.sign(cid, p.pid, rookie_contract(L.year, self.rng.randint(1, 7)), "roster")
            for pos in PS_TEMPLATE:
                p = self.new_prospect(pos, 50, 7)
                L.sign(cid, p.pid, rookie_contract(L.year, 7), "ps")
        self.top_up_pool()

    def top_up_pool(self, floor=8):
        """Pools hold at least 8 medically available unsigned players per position (section 13)."""
        L = self.L
        have = {}
        for p in L.players.values():
            if p.where == "fa" and p.out_weeks == 0:
                have[p.pos] = have.get(p.pos, 0) + 1
        for pos in POSITIONS:
            for _ in range(max(0, floor - have.get(pos, 0))):
                self.new_prospect(pos, 47, 7, self.rng.randint(22, 28), self.rng.randint(0, 4))

    # ---------- contracts
    def offer(self, cid, p, rookie=False):
        r = self.rng
        reck = cid in self.reckless
        a = ask(p)
        if a <= R.MIN_SALARY or p.ovr < 56:
            return Contract(self.L.year, (R.MIN_SALARY,) * r.choice((1, 1, 2)), (), (), self.L.week)
        n = r.choice((1, 2, 3, 4, 5)) if not reck else 5
        bf = F(r.randint(30, 60), 100) if reck else F(r.randint(0, 20), 100)
        gf = F(r.randint(50, 100), 100) if reck else F(r.randint(0, 30), 100)
        shape = r.choice(("steady", "rising", "declining"))
        base = max(R.MIN_SALARY, a * (1 - bf))
        ys = [base * (F(105, 100) ** (i if shape == "rising" else (-i if shape == "declining" else 0))) for i in range(n)]
        ys = [max(R.MIN_SALARY, F(int(y))) for y in ys]
        if shape == "rising":
            ys = sorted(ys)
        elif shape == "declining":
            ys = sorted(ys, reverse=True)
        else:
            ys = [ys[0]] * n
        gs = tuple(F(int(y * gf)) if i < max(1, n - 1) else F(0) for i, y in enumerate(ys))
        prorate = min(5, n)
        bonus = F(int(a * bf * n))
        bonuses = ((bonus, 0, prorate),) if bonus > 0 else ()
        return Contract(self.L.year, tuple(ys), gs, bonuses, self.L.week, shape)

    def try_sign(self, cid, p, slot="roster", rookie_c=None):
        c = rookie_c or self.offer(cid, p)
        tries = 0
        while True:
            try:
                self.L.sign(cid, p.pid, c, slot)
                return True
            except (Rejected, ContractError) as e:
                self._reject("sign", e)
                tries += 1
                if tries > 2 or rookie_c is not None:
                    return False
                c = Contract(self.L.year, (max(R.MIN_SALARY, ask(p) / 2),) * 1, (), (), self.L.week) if tries == 1 else \
                    Contract(self.L.year, (R.MIN_SALARY,), (), (), self.L.week)

    # ---------- autopilot helpers
    def counts(self, cid, slots=("roster",), healthy=True):
        out = {}
        for s in slots:
            for pid in getattr(self.L.clubs[cid], s):
                p = self.L.players[pid]
                if not healthy or p.out_weeks == 0:
                    out[p.pos] = out.get(p.pos, 0) + 1
        return out

    def fas(self, pos=None):
        return sorted((p for p in self.L.players.values() if p.where == "fa" and p.out_weeks == 0 and (pos is None or p.pos == pos)),
                      key=lambda q: (-q.ovr, q.pid))

    def value(self, p):
        return p.ovr - max(0, p.age - 30) * 2

    def need(self, cid, slots=("roster",)):
        """Positions short of the template count, most urgent first."""
        have = self.counts(cid, slots)
        return sorted(((TEMPLATE[p] - have.get(p, 0), p) for p in TEMPLATE if have.get(p, 0) < TEMPLATE[p]), reverse=True)

    def fix_minimums(self, cid, slot="roster"):
        """Sign the cheapest adequate free agent wherever a league minimum is unmet. Min-salary deals only."""
        L = self.L
        for _ in range(60):
            have = self.counts(cid, ("roster",))
            short = shortfalls(have)
            if not short:
                return True
            pos = sorted(short)[0]
            want = "HB" if pos == "HB" else pos
            pool = self.fas(want)
            if not pool:
                self.top_up_pool()
                pool = self.fas(want)
            if not L.room(cid, "roster"):
                self.drop_weakest(cid, avoid=pos, slots=("roster",))
            if not self.try_sign(cid, pool[0], "roster", Contract(L.year, (R.MIN_SALARY,), (), (), L.week)):
                return False
        return False

    def drop_weakest(self, cid, avoid=None, slots=("roster", "ps"), keep_minimums=True):
        L = self.L
        c = L.clubs[cid]
        have = self.counts(cid, ("roster",), healthy=True)
        cand = [L.players[pid] for s in slots for pid in getattr(c, s)]
        cand.sort(key=lambda q: (self.value(q) - (0 if q.where == "roster" else 100), q.pid))
        req = dict(MINIMUMS, HB=3)
        for p in cand:
            if keep_minimums and p.where == "roster" and p.out_weeks == 0 and have.get(p.pos, 0) <= req.get(p.pos, 0):
                continue
            try:
                L.release(cid, p.pid, split=False)
                return p
            except Rejected as e:
                self._reject("release", e)
        return None

    # ---------- the year
    def step_year(self, first=False):
        L, rng = self.L, self.rng
        if not first:
            L.new_year()
        self.age_and_retire(skip=first)
        self.top_up_pool()
        self.pre_draft_trim()
        self.draft()
        self.free_agency()
        L.start_camp()
        for cid in L.clubs:
            self.stats["offseason_overage_max"] = max(self.stats["offseason_overage_max"], L.overage(cid))
        for cid in L.clubs:
            self.camp(cid)
        self.top_up_pool()
        L.begin_season()
        self.audit(strict_current=True)          # every club starts the season viable
        self.season()
        L.end_season()
        self.audit()

    def age_and_retire(self, skip=False):
        L = self.L
        if skip:
            return
        for p in list(L.players.values()):
            if p.where == "retired":
                continue
            p.age += 1
            if p.where in ("roster", "ps", "ir"):
                p.seasons += 1
            drift = self.rng.gauss(1.5 if p.age < 26 else (0 if p.age < 30 else -2), 2.5)
            p.ovr = int(max(30, min(99, p.ovr + drift)))
            p.elevations = 0
            if p.where == "fa" and (p.age >= 34 or (p.age >= 31 and self.rng.random() < 0.15) or p.ovr < 40):
                p.where = "retired"

    def pre_draft_trim(self):
        L = self.L
        for cid in L.clubs:
            while len(L.clubs[cid].roster) + len(L.clubs[cid].ps) > 68:
                if not self.drop_weakest(cid, keep_minimums=False):
                    break
        # waivers clear
        order = sorted(L.clubs)
        self.rng.shuffle(order)
        self.stats["claims"] += len(L.resolve_waivers(order, wants=lambda cid, p: p.ovr > 62 and self.rng.random() < 0.3))

    def draft(self):
        L, rng = self.L, self.rng
        pool = []
        for _ in range(672):
            pos = rng.choice([p for p, n in TEMPLATE.items() for _ in range(n)])
            pool.append(self.new_prospect(pos, 56, 9, rng.randint(21, 23)))
        for p in pool:
            p.where = "draft"
        for rnd in range(1, 8):
            order = sorted(L.clubs)
            rng.shuffle(order)
            for orig in order:
                owner = next(cid for cid, c in L.clubs.items() if (L.year, rnd, orig) in c.picks)
                avail = sorted((p for p in pool if p.where == "draft"), key=lambda q: (-q.ovr, q.pid))
                needy = {pos for _, pos in self.need(owner, ("roster", "ps"))}
                pick = next((p for p in avail[:40] if p.pos in needy), avail[0])
                pick.where = "fa"
                c = rookie_contract(L.year, rnd, first_round_option=(rnd == 1))
                slot = "roster" if len(L.clubs[owner].roster) < 62 else "ps"
                if slot == "ps" and not L.room(owner, "ps"):
                    slot = "roster"
                if not L.room(owner, slot) or not self.try_sign(owner, pick, slot, c):
                    pick.where = "fa"        # unsigned draftee becomes a free agent
                L.clubs[owner].picks.discard((L.year, rnd, orig))
        for p in pool:
            if p.where == "draft":
                p.where = "fa"
        L.complete_draft()

    def free_agency(self):
        L, rng = self.L, self.rng
        for cid in sorted(L.clubs, key=lambda c: rng.random()):
            self.fix_minimums(cid)
            budget_floor = R.CAP - R.RESERVE - F(90)
            for _ in range(40):
                if not L.room(cid, "roster") or L.cap_number(cid) >= budget_floor and cid not in self.reckless:
                    break
                needy = [pos for _, pos in self.need(cid)] or list(TEMPLATE)
                pos = rng.choice(needy)
                pool = [p for p in self.fas(pos) if p.ovr > 60]
                if not pool:
                    break
                p = pool[0]
                if cid not in self.reckless and L.cap_number(cid) + ask(p) > R.CAP - R.RESERVE:
                    continue
                self.try_sign(cid, p)
            self.fill_squad(cid)

    def fill_squad(self, cid):
        L = self.L
        while len(L.clubs[cid].ps) < R.PS_MAX and L.room(cid, "ps"):
            vets = L.ps_veterans(cid) >= R.PS_VETERANS_MAX
            pool = [p for p in self.fas() if p.ovr < 62 and not (vets and p.veteran)]
            if not pool:
                break
            if not self.try_sign(cid, pool[0], "ps", Contract(L.year, (R.MIN_SALARY,), (), (), L.week)):
                break
            if len(L.clubs[cid].roster) + len(L.clubs[cid].ps) >= R.PRESEASON_CEILING:
                break

    def camp(self, cid):
        """Cut to 53 and 16, clear any overage (the cap binds from the final cut), make sure the minimums hold."""
        L = self.L
        c = L.clubs[cid]
        for _ in range(200):
            # sizes
            while len(c.roster) > R.ROSTER_MAX:
                if not self.drop_weakest(cid, slots=("roster",)):
                    break
            while len(c.ps) > R.PS_MAX or L.ps_veterans(cid) > R.PS_VETERANS_MAX:
                p = sorted((L.players[i] for i in c.ps), key=lambda q: (not (q.veteran and L.ps_veterans(cid) > R.PS_VETERANS_MAX), self.value(q), q.pid))[0]
                L.release(cid, p.pid)
            self.fill_squad(cid)          # the squad counts against the cap, so fill it before measuring overage
            # overage: shed the player whose release relieves the cap the most per point of value lost
            if L.overage(cid) > 0:
                best, best_gain = None, F(0)
                for pid in c.roster + c.ps:
                    p = L.players[pid]
                    k = p.contract
                    gain = k.cap_hit(L.year) - k.release_charges(L.year, L.week).get(L.year, F(0))
                    if p.where == "ps" or gain / (1 + max(0, self.value(p) - 45)) > best_gain:
                        if gain > 0 and (best is None or gain / (1 + max(0, self.value(p) - 45)) > best_gain):
                            best, best_gain = p, gain / (1 + max(0, self.value(p) - 45))
                if best is None:
                    break
                L.release(cid, best.pid)
                continue
            if shortfalls(self.counts(cid, ("roster",))):
                self.fix_minimums(cid)
                continue
            if len(c.roster) > R.ROSTER_MAX:
                continue
            break
        order = sorted(L.clubs)
        L.resolve_waivers(order, wants=lambda cid2, p: False)
        self.fill_squad(cid)
        try:
            L.cut_down(cid)
        except Rejected as e:
            self._reject("cut_down", e)
            raise
        pay = L.cap_number(cid)
        if self.stats["payroll_min"] is None or pay < self.stats["payroll_min"]:
            self.stats["payroll_min"] = pay

    def season(self):
        L, rng = self.L, self.rng
        for week in range(1, R.PAID_WEEKS + 1):
            L.advance_week()
            self.injuries()
            for cid in L.clubs:
                self.weekly(cid, week)
            if week <= 18:
                self.games(week)
            if week <= R.TRADE_LAST_GAME_WEEK:
                self.trades()
            order = sorted(L.clubs)
            rng.shuffle(order)
            self.stats["claims"] += len(L.resolve_waivers(order, wants=lambda cid, p: p.ovr > 65 and rng.random() < 0.2))
            self.top_up_pool()
            for cid in L.clubs:
                self.stats["overage_max"] = max(self.stats["overage_max"], L.overage(cid))
                m = L.viability_margin(cid)
                self.stats["margin_min"] = m if self.stats["margin_min"] is None else min(self.stats["margin_min"], m)
                self.stats["committed_max"] = max(self.stats["committed_max"], max(L.committed(cid).values(), default=F(0)))
                if week % 3 == 1 and not self.legal_move_exists(cid):
                    self.stats["legal_move_failures"] += 1

    def injuries(self):
        L, rng = self.L, self.rng
        for cid, c in L.clubs.items():
            for pid in list(c.roster):
                p = L.players[pid]
                if p.out_weeks == 0 and rng.random() < 0.008:
                    p.out_weeks = rng.randint(1, 8) if rng.random() < 0.9 else rng.randint(9, 40)
            for pid in list(c.ps):
                p = L.players[pid]
                if p.out_weeks == 0 and rng.random() < 0.004:
                    p.out_weeks = rng.randint(1, 6)

    def weekly(self, cid, week):
        L, rng = self.L, self.rng
        c = L.clubs[cid]
        for pid in list(c.roster):
            p = L.players[pid]
            if p.out_weeks >= R.IR_MIN_GAMES and (rng.random() < 0.7):
                L.place_on_ir(cid, pid)
                self.stats["ir_moves"] += 1
        for pid in list(c.ir):
            p = L.players[pid]
            if (p.out_weeks == 0 and c.ir_returns_used < R.IR_RETURNS_MAX and week - p.ir_since >= R.IR_MIN_GAMES
                    and len(c.roster) < R.ROSTER_MAX and rng.random() < 0.6):
                try:
                    L.return_from_ir(cid, pid)
                except Rejected as e:
                    self._reject("ir_return", e)
        # the cut-down state must hold between games: refill with minimum-salary signings where a minimum is unmet
        if shortfalls(self.counts(cid)):
            self.fix_minimums(cid)
        # occasional churn: release a poor, expensive player (this is what creates dead money)
        if cid in self.reckless or rng.random() < 0.01:
            c_players = sorted((L.players[i] for i in c.roster), key=lambda q: (self.value(q), q.pid))
            for p in c_players[:3]:
                if rng.random() < (0.6 if cid in self.reckless else 0.2) and p.contract.bonuses:
                    try:
                        L.release(cid, p.pid, split=bool(rng.random() < 0.3 and L.year not in c.split_years))
                    except Rejected as e:
                        self._reject("release", e)
                    break
        while len(c.roster) < R.ROSTER_MAX - 2 and rng.random() < 0.3:
            pool = self.fas()
            if not pool:
                break
            self.try_sign(cid, pool[0], "roster", Contract(L.year, (R.MIN_SALARY,), (), (), L.week))
            break

    def games(self, week):
        L = self.L
        for cid in L.clubs:
            before = sum(p.elevations for p in L.players.values() if p.club == cid and p.where == "ps")
            gd = gameday.build(L, cid)
            after = sum(p.elevations for p in L.players.values() if p.club == cid and p.where == "ps")
            self.stats["elevations"] += after - before
            self.stats["games"] += 1
            if gd.short:
                self.stats["short_games"] += 1
            if gd.emergency:
                self.stats["emergency_games"] += 1
            if not gd.can_field:
                self.stats["forfeits"] += 1

    def trades(self):
        L, rng = self.L, self.rng
        cids = sorted(L.clubs)
        for _ in range(3):
            a, b = rng.sample(cids, 2)
            ra = [i for i in L.clubs[a].roster if L.players[i].out_weeks == 0]
            rb = [i for i in L.clubs[b].roster if L.players[i].out_weeks == 0]
            if not ra or not rb:
                continue
            pa = L.players[rng.choice(ra)]
            cands = [L.players[i] for i in rb if L.players[i].pos == pa.pos]
            if not cands:
                continue
            pb = rng.choice(cands)
            picks_a = ([sorted(L.clubs[a].picks)[0]] if rng.random() < 0.3 and L.clubs[a].picks else [])
            try:
                L.trade(a, [pa.pid], b, [pb.pid], a_picks=picks_a)
                self.stats["trades"] += 1
            except Rejected as e:
                self.stats["trades_rejected"] += 1
                self._reject("trade", e)

    def legal_move_exists(self, cid):
        """A club can always do something: sign a minimum-salary player or release one."""
        L = self.L
        if L.room(cid, "roster") and self.fas():
            p = self.fas()[0]
            if L.probe([cid], [p.pid], lambda: L.sign(cid, p.pid, Contract(L.year, (R.MIN_SALARY,), (), (), L.week), "roster")):
                return True
        c = L.clubs[cid]
        for pid in c.roster + c.ps:
            if L.probe([cid], [pid], lambda pid=pid: L.release(cid, pid)):
                return True
        return False

    # ---------- audit
    def audit(self, strict_current=False):
        """Independent recomputation of every invariant from raw state (no caches)."""
        L = self.L
        seen = {}
        for cid, c in L.clubs.items():
            for slot in ("roster", "ps", "ir"):
                for pid in getattr(c, slot):
                    assert pid not in seen, f"player {pid} on two lists"
                    seen[pid] = (cid, slot)
                    p = L.players[pid]
                    assert (p.club, p.where) == (cid, slot), (pid, p.club, p.where, cid, slot)
                    assert p.contract and p.contract.covers(L.year), "rostered player without a live contract"
        for pid, p in L.players.items():
            if p.where in ("roster", "ps", "ir"):
                assert pid in seen
            else:
                assert pid not in seen and p.club is None or p.where == "waivers", (pid, p.where)
        counts = {}
        for c in L.clubs.values():
            for (y, rnd, orig) in c.picks:
                counts[(y, rnd, orig)] = counts.get((y, rnd, orig), 0) + 1
        assert all(v == 1 for v in counts.values()), "a pick is owned twice"
        for cid, c in L.clubs.items():
            c._cmap = None
            for y, v in L.committed(cid).items():
                if y > L.year or (strict_current and y == L.year):
                    assert v <= L.limit(y), f"{cid} {y}: committed {v} > {L.limit(y)}"
            assert len(c.roster) <= R.ROSTER_MAX and len(c.ps) <= R.PS_MAX
