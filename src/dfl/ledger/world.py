"""A living world around the ledger: players come from character cards, develop, age and retire; a deterministic autopilot
(the stand-in for clubs' decisions) pushes the ledger through realistic volumes of signings, releases, trades, injuries and cuts.

Players are character cards (`characters`): the founding league, every draft class and every top-up of the free-agent pool are
made by the `Creator`; a card's hidden attributes develop each year by `characters.development`. What is STILL PLACEHOLDER here:
the autopilot's decisions (it ranks players by their true overall, where real clubs will work from scouting estimates), prices
(`ask`), the random injuries of players who are not playing (the engine injures those who are), and leaders, who do not yet age or
change clubs (the people layer). It is not evidence about league balance by itself; measure balance with the engine running.
"""
import random
from fractions import Fraction as F

import math

from ..characters import Creator, load_tables
from ..characters.creator import rng_for
from ..characters import soul
from ..characters.development import develop, retire_chance, retire_chance_contracted
from . import coaching, gameday, governance, rooms, rules as R, trading
from .contracts import Contract, ContractError, rookie_contract
from ..engine import production
from .ledger import Ledger, Rejected
from .positions import MINIMUMS, POSITIONS, shortfalls

# a 53-man template: the 48 required places (backfield as 3 HB) plus five extras
EXTRAS = ("WR", "CB", "DT", "MLB", "C")
TEMPLATE = dict(MINIMUMS, HB=3)
STARTERS = dict(K=1, P=1, QB=1, HB=1, WR=3, TE=1, LT=1, LG=1, C=1, RG=1, RT=1, LE=1, RE=1, DT=2, ROLB=1, MLB=1, LOLB=1, CB=3, FS=1, SS=1, LS=1)     # who a club upgrades (Edition 8.39)
for _p in EXTRAS:
    TEMPLATE[_p] = TEMPLATE.get(_p, 0) + 1
assert sum(TEMPLATE.values()) == 53
PS_TEMPLATE = ("QB", "HB", "WR", "WR", "TE", "LT", "LG", "C", "RT", "LE", "DT", "ROLB", "CB", "CB", "FS", "LB")
PS_TEMPLATE = tuple("MLB" if p == "LB" else p for p in PS_TEMPLATE)
assert len(PS_TEMPLATE) == 16


PRICE_BASE = 60          # PLACEHOLDER: overall at which a player is worth more than the minimum


def price(ovr, pos):
    """Annual value of a player of this overall (placeholder price curve)."""
    v = 5 + max(0, ovr - PRICE_BASE) * F(16, 10)
    if pos == "QB":
        v *= F(3, 2)
    return F(int(v))


def ask(player):
    """Market annual value of a player (placeholder price curve)."""
    return price(player.ovr, player.pos)


class World:
    def __init__(self, cids, seed=1, reckless=(), year=2000, tables=None, engine_injuries=False, priority=()):
        self.rng = random.Random(seed)
        self.dev_rng = random.Random(f"{seed}|develop")
        self.soul_rng = random.Random(f"{seed}|soul")
        self.coach_rng = random.Random(f"{seed}|coach")
        self.coach_pool = []                                      # willing coaches, new or let go: one pool for every coaching post
        self.pool_years = {}                                      # coach id -> year turns spent in the pool
        self.gov_rng = random.Random(f"{seed}|gov")
        self.fans = {}                                            # club -> fan card (Edition 8.27)
        self.ceo_pool, self.gm_pool = [], []                      # willing CEO candidates and general managers, new or let go
        self.elected = {}                                         # club -> CEO elected after the last game, who takes office in the next offseason
        self.gov_log = []                                         # recall ballots, elections, dismissals and hires (feeds the story layer)
        self.pub_prior = {}                                       # the going level at each position and age for the public trade price, per league year (Edition 8.34)
        self.ep_curve = None                                      # the expected-points table refit from past seasons; None is the data table (Edition 8.32)
        self.ep_acc = {}                                          # league year -> a season's sums over its plays (production.new_acc)
        self.production = {}                                      # league year -> card id -> opponent -> play type -> [opportunities, EPA, WPA] (Edition 8.30)
        self.team_production = {}                                 # league year -> club -> "off"/"def" -> play type -> [plays, EPA] (EPA the unit made / allowed)
        self.appearances = {}                                     # league year -> card ids that were on a game-day active list (section 15 credited seasons)
        self.career = {}                                          # card id -> league year -> analytical season record (analytics.py)
        self.credited = {}                                        # card id -> league years with an active-list or Tour appearance (section 15)
        self.hall = {"reg": {}, "ever": {}, "fan": [], "log": []}       # section 15 halls (honours.py)
        self.persona = {}                                         # card id -> (name, official position), kept for the halls after retirement
        self.tiaras = {}                                          # card id -> league years of her Tiaras (honours.py)
        self.rooms, self.room_seq, self._tape = [], 0, None          # meeting-room transcripts of the last few league years (Edition 8.29)
        self.form = {}                                            # club -> win percentage of its last season (what candidates see)
        self.trade_log = []                                       # every proposal, counter, rejection and deal (the engine replays this)
        self.L = Ledger(cids, year)
        self.reckless = set(reckless)
        self.priority = list(priority)             # clubs that always shop first in free agency (a parity-test control only)
        self.tables = tables or load_tables()
        self.creator = Creator(seed, self.tables)
        self.cards = {}                  # player id -> Card, for everyone in the ledger (the id is the card id)
        self.rating = {}                 # player id -> hidden true overall (float), refreshed when the card develops
        self.leaders = {}                # club -> {role: Card}; coaches hold posts and move through the pool (Edition 8.25); the CEO and GM are elected and hired (Edition 8.27)
        self._views = {}                 # (club, player) -> that club's estimate this league year
        self._judged = {}                # (club, player) -> that club's coaches' estimate of her own player this league year (Edition 8.36)
        self.active = {}                 # club -> GameDay of its latest game
        self.active_week = {}
        self.engine_injuries = engine_injuries     # True: the engine injures players who play; the random model keeps to the rest
        self.stats = {"rejections": {}, "trades": 0, "trades_rejected": 0, "claims": 0, "forfeits": 0, "short_games": 0,
                      "emergency_games": 0, "elevations": 0, "ir_moves": 0, "unsigned_picks": 0, "offseason_overage_max": F(0), "overage_max": F(0), "payroll_min": None,
                      "margin_min": None, "games": 0, "committed_max": F(0), "legal_move_failures": 0}
        self._found()

    def record_game(self, res, home, away):
        """File one game's per-player and per-unit production under the league year (Edition 8.30). Nothing reads it back into a game."""
        y = self.L.year
        if res.fit:
            production.accumulate(self.ep_acc.setdefault(y, production.new_acc()), res.fit)
        prod, apps, team = self.production.setdefault(y, {}), self.appearances.setdefault(y, set()), self.team_production.setdefault(y, {})
        for sq, opp in ((home, away), (away, home)):
            apps.update(sq.cards)
            for pid in sq.cards:
                if pid in res.production:
                    d = prod.setdefault(pid, {}).setdefault(opp.cid, {})
                    for pt, (n, epa, wpa) in res.production[pid].items():
                        x = d.setdefault(pt, [0, 0.0, 0.0])
                        x[0] += n
                        x[1] += epa
                        x[2] += wpa
        for k, (a, b) in enumerate(((home, away), (away, home))):
            for pt, (n, epa) in res.team_production[k].items():
                for club, side, sign in ((a.cid, "off", 1), (b.cid, "def", 1)):
                    x = team.setdefault(club, {}).setdefault(side, {}).setdefault(pt, [0, 0.0])
                    x[0] += n
                    x[1] += epa

    # ---------- supply
    def register(self, card, seasons=0):
        """Put a character into the ledger under her card id. The autopilot sees her true overall rounded (placeholder for scouting)."""
        o = card.true_overall(self.tables)
        p = self.L.new_player(card.identity.pos, int(round(o)), card.age(self.L.year), seasons, pid=card.identity.id)
        self.cards[p.pid] = card
        self.rating[p.pid] = o
        soul.assign(card, self.tables)
        return p

    def view(self, cid, p):
        """A club's estimate of a player's overall (Edition 8.21): her hidden overall plus the position's bias plus an error that is
        fixed for the club and player over a league year, shrinks with her credited seasons and with the club's general manager's
        evaluation, and is smaller for the club's own players. Clubs decide by this, never by the true figure."""
        key = (cid, p.pid)
        v = self._views.get(key)
        if v is None:
            S = self.tables.scouting
            gm = self.leaders.get(cid, {}).get("gm")
            evl = gm.attrs.get("EVL", 50) if gm else 50
            sd = S["base_sd"] / math.sqrt(1 + S["seasons_k"] * p.seasons)
            sd *= 1 - S["evl_effect"] * (evl - 50) / 50
            if p.club == cid:
                sd *= S["own_factor"]
            sd = max(S["min_sd"], sd)
            z = rng_for(self.creator.seed, "scout", cid, p.pid, self.L.year).gauss(0, 1)
            v = self._views[key] = self.rating[p.pid] + self.tables.scouting_bias.get(p.pos, 0.0) + sd * z
        return v

    def purge(self):
        """Characters the ledger has retired leave the list; their names may be used again."""
        for pid in [k for k in self.cards if k not in self.L.players]:
            self.creator.release_name(self.cards.pop(pid))
            self.rating.pop(pid, None)

    def injure(self, pid, weeks):
        """An injury from the engine: she misses `weeks` games (the clock turns before each paid week's games; after the season it does not)."""
        p = self.L.players[pid]
        p.out_weeks = max(p.out_weeks, weeks + (1 if self.L.phase == "season" else 0))

    def heal_round(self):
        """A postseason round has been played: injuries move on a week (paid weeks do this themselves)."""
        for p in self.L.players.values():
            if p.out_weeks > 0:
                p.out_weeks -= 1

    def _reject(self, label, e):
        import re
        k = re.sub(r"\bC\d\d\b", "<club>", str(e))[:70]
        self.stats["rejections"][k] = self.stats["rejections"].get(k, 0) + 1

    def _found(self):
        """Founding Day: every club starts with an all-rookie 53 and a squad of 16."""
        L = self.L
        f = self.creator.founding(L.year, sorted(L.clubs))
        for cid in sorted(L.clubs):
            d = f["clubs"][cid]
            for card in d["roster"]:
                L.sign(cid, self.register(card).pid, rookie_contract(L.year, self.rng.randint(1, 7)), "roster")
            for card in d["ps"]:
                L.sign(cid, self.register(card).pid, rookie_contract(L.year, 7), "ps")
            lead = {c.identity.role: c for c in d["leaders"] if c.identity.role != "coach"}
            lead.update(zip(coaching.POSTS, [c for c in d["leaders"] if c.identity.role == "coach"]))     # the founding coaches take the four posts
            self.leaders[cid] = lead
        governance.found(self)
        for card in f["pool"]:
            self.register(card, seasons=max(0, min(4, card.age(L.year) - 22)))
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
                age = self.rng.randint(22, 28)
                self.register(self.creator.player(pos, age, L.year, "pool", talent_mean=-0.6), seasons=min(4, age - 22))

    # ---------- contracts
    def demand(self, p, cid):
        """What she asks of this club (Edition 8.23): the price of the overall she believes she has, moved by her greed and ambition,
        lowered for her last club by her loyalty and for every year she has waited unsigned. Never below the league minimum."""
        N, card = self.tables.negotiation, self.cards[p.pid]
        tr = card.traits
        mult = 1 + N["greed_effect"] * (tr["GRE"] - 50) / 50 + N["ambition_effect"] * (tr["AMB"] - 50) / 50
        if p.last_club == cid:
            mult -= N["loyalty_effect"] * (tr["LOY"] - 50) / 50
        mult -= N["pool_discount"] * p.pool_years
        return max(R.MIN_SALARY, F(int(price(int(round(soul.self_view(card, self.tables))), p.pos) * max(0.3, mult))))

    def quote(self, cid, p):
        """The market price of a player as this club reads it, from its own view of her overall (Edition 8.35); the true-overall price
        only when no club is asking (the league's own bookkeeping)."""
        return ask(p) if cid is None else price(int(round(self.view(cid, p))), p.pos)

    def willing(self, cid, p):
        """The most this club will pay her: the price of the overall it believes she has, plus a little slack."""
        return F(int(price(int(round(self.view(cid, p))), p.pos) * (1 + self.tables.negotiation["club_slack"])))

    def negotiate(self, cid, p):
        """The yearly price a deal would be struck at, or None if she asks more than the club will pay. A player whose market price is
        the league minimum or less takes the minimum and does not bargain (section 13, market floor)."""
        q = self.quote(cid, p)
        if q <= R.MIN_SALARY or round(self.view(cid, p)) < 56:
            return q
        return rooms.contract(self, cid, p)

    def offer(self, cid, p, rookie=False, value=None):
        r = self.rng
        reck = cid in self.reckless
        a = value if value is not None else self.quote(cid, p)
        if a <= R.MIN_SALARY or round(self.view(cid, p)) < 56:
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

    def try_sign(self, cid, p, slot="roster", rookie_c=None, value=None):
        c = rookie_c or self.offer(cid, p, value=value)
        tries = 0
        while True:
            try:
                self.L.sign(cid, p.pid, c, slot)
                return True
            except (Rejected, ContractError) as e:
                self._reject("sign", e)
                tries += 1
                if tries > 2 or rookie_c is not None or self.quote(cid, p) > 2 * R.MIN_SALARY:      # nobody takes far less than her price
                    return False
                c = Contract(self.L.year, (max(R.MIN_SALARY, self.quote(cid, p) / 2),) * 1, (), (), self.L.week) if tries == 1 else \
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

    def fas(self, pos=None, cid=None):
        """Healthy free agents, best first: by the club's own view when a club is looking, by market rating otherwise."""
        rank = (lambda q: (-self.view(cid, q), q.pid)) if cid else (lambda q: (-q.ovr, q.pid))
        return sorted((p for p in self.L.players.values() if p.where == "fa" and p.out_weeks == 0 and (pos is None or p.pos == pos)), key=rank)

    def cheap(self, pos=None, cid=None):
        """Free agents who would sign for the league minimum, best first (a market price at or under the minimum). If nobody at that
        position is that cheap, the lowest-rated free agents. The autopilot may not buy a star at the minimum: nobody sells one."""
        pool = self.fas(pos, cid)
        ok = [p for p in pool if self.quote(cid, p) <= R.MIN_SALARY]
        return ok or pool[::-1]

    def value(self, p, cid=None):
        base = self.view(cid, p) if cid else p.ovr
        return base - max(0, p.age - 30) * 2

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
            pool = self.cheap(want, cid)
            if not pool:
                self.top_up_pool()
                pool = self.cheap(want, cid)
            if not L.room(cid, "roster"):
                self.drop_weakest(cid, avoid=pos, slots=("roster",))
            if not L.number_room(cid, want):         # her block is full of surplus players: make a number free first
                self.release_for_number(cid, want)
            if not self.try_sign(cid, pool[0], "roster", Contract(L.year, (R.MIN_SALARY,), (), (), L.week)):
                # a club at the cap makes room: release the surplus-position player whose release relieves the cap most
                if not self.make_cap_room(cid, avoid=pos):
                    return False
        return False

    def release_for_number(self, cid, pos):
        """Free a jersey number for `pos`: release the weakest surplus player competing for the same numbers (her own block, or any
        player standing in a number she could use), preferring an injured one. Players needed for a league minimum are kept."""
        from . import numbers as N
        L = self.L
        c = L.clubs[cid]
        ranges = [N.PRIMARY[pos]] + ([N.OVERFLOW[pos]] if pos in N.OVERFLOW else [])
        have = self.counts(cid, ("roster",), healthy=True)
        req = dict(MINIMUMS, HB=3)
        cand = []
        for pid in c.roster + c.ps + c.ir:
            q = L.players[pid]
            if q.where == "roster" and q.out_weeks == 0 and have.get(q.pos, 0) <= req.get(q.pos, 0):
                continue
            if q.pos == pos or N.PRIMARY[q.pos] == N.PRIMARY[pos] or (q.number is not None and any(N.in_range(q.number, r) for r in ranges)):
                cand.append(q)
        if not cand:
            return False
        def relief(x):          # prefer a release that does not raise the cap number (a bonus accelerating onto this year can)
            k = x.contract
            return k.cap_hit(L.year) - k.release_charges(L.year, L.week).get(L.year, F(0))
        q = min(cand, key=lambda x: (relief(x) < 0, x.out_weeks == 0, self.value(x, cid), x.pid))
        try:
            L.release(cid, q.pid)
        except Rejected as e:
            self._reject("release", e)
            return False
        return True

    def make_cap_room(self, cid, avoid=None):
        """Release one player (not needed to meet a minimum) whose release lowers the cap number. False if none exists."""
        L = self.L
        c = L.clubs[cid]
        have = self.counts(cid, ("roster",), healthy=True)
        req = dict(MINIMUMS, HB=3)
        best, best_key = None, None
        for pid in c.roster + c.ps:
            p = L.players[pid]
            if p.where == "roster" and p.out_weeks == 0 and have.get(p.pos, 0) <= req.get(p.pos, 0):
                continue
            k = p.contract
            relief = k.cap_hit(L.year) - k.release_charges(L.year, L.week).get(L.year, F(0))
            if relief <= 0:
                continue
            key = (relief / (1 + max(0, self.value(p, cid) - 45)), -p.pid)
            if best is None or key > best_key:
                best, best_key = p, key
        if best is None:
            return False
        try:
            L.release(cid, best.pid)
        except Rejected as e:
            self._reject("release", e)
            return False
        return True

    def drop_weakest(self, cid, avoid=None, slots=("roster", "ps"), keep_minimums=True):
        L = self.L
        c = L.clubs[cid]
        have = self.counts(cid, ("roster",), healthy=True)
        cand = [L.players[pid] for s in slots for pid in getattr(c, s)]
        cand.sort(key=lambda q: (self.value(q, cid) - (0 if q.where == "roster" else 100), q.pid))
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
    # Each method below is one calendar event (calendar.py); step_year runs them back to back for the ledger soak and
    # year.py fires them on their dates.
    def open_year(self, first=False):
        """Offseason opens: the league year turns, contracts expire, retirements take effect."""
        if not first:
            self.L.new_year(rank=lambda cid, p: coaching.judged(self, cid, p))
        self.age_and_retire(skip=first)
        rooms.prune(self)
        self.top_up_pool()

    def free_agency_open(self):
        self.pre_draft_trim()
        self.free_agency()

    def camp_open(self):
        self.L.start_camp()
        for cid in self.L.clubs:
            self.stats["offseason_overage_max"] = max(self.stats["offseason_overage_max"], self.L.overage(cid))

    def final_cuts(self):
        for cid in self.L.clubs:
            self.camp(cid)
        self.top_up_pool()

    def start_season(self):
        self.L.begin_season()
        self.audit(strict_current=True)          # every club starts the season viable

    def end_season(self):
        self.L.end_season()
        self.active.clear()
        self.audit()

    def step_year(self, first=False):
        self.open_year(first)
        self.free_agency_open()
        self.start_draft()
        self.draft_rounds(range(1, 8))
        self.finish_draft()
        self.camp_open()
        self.final_cuts()
        self.start_season()
        self.season()
        self.end_season()

    def age_and_retire(self, skip=False):
        """The year turns: every card is a year older and develops; injuries heal over the offseason; free agents may retire."""
        L, D = self.L, self.tables.development
        self._views.clear()                # clubs re-judge everyone each league year
        self._judged.clear()               # and so do coaches
        self.purge()                       # the pool rule (four years unsigned) already retired some at the league-year turn
        if skip:
            return
        teach = {}
        for pid in sorted(L.players):
            p, card = L.players[pid], self.cards[pid]
            p.age = card.age(L.year)
            if p.where in ("roster", "ps", "ir"):
                p.seasons += 1
            soul.drift(card, self.soul_rng, self.tables)
            if p.where in ("roster", "ps", "ir") and p.club in self.leaders:
                coach = teach.get((p.club, p.pos))
                if coach is None:
                    coach = teach[(p.club, p.pos)] = coaching.teaching(self, p.club, p.pos)
            else:
                coach = 0.0
            develop(card, L.year, self.dev_rng, self.tables, coach)
            o = card.true_overall(self.tables)
            self.rating[pid], p.ovr = o, int(round(o))
            p.elevations = 0
            p.out_weeks = max(0, p.out_weeks - int(D["heal_offseason_weeks"]))
            wear = retire_chance_contracted(card, L.year, self.tables)          # wear and life take players at every age, signed or not (Edition 8.38)
            if p.where == "fa" and self.dev_rng.random() < 1 - (1 - retire_chance(card, L.year, o, self.tables)) * (1 - wear):
                L.retire(pid, "age")
            elif p.where in ("roster", "ps", "ir") and self.dev_rng.random() < wear:
                L.retire_contracted(pid)
        self.purge()
        governance.turn_year(self)
        coaching.turn_year(self)

    def pre_draft_trim(self):
        L = self.L
        for cid in L.clubs:
            while len(L.clubs[cid].roster) + len(L.clubs[cid].ps) > R.PRESEASON_CEILING - 9:     # room for 7 picks + 2 comps
                if not self.drop_weakest(cid, keep_minimums=False):
                    break
        # waivers clear
        order = sorted(L.clubs)
        self.rng.shuffle(order)
        self.stats["claims"] += len(L.resolve_waivers(order, wants=lambda cid, p: self.view(cid, p) > 62 and self.rng.random() < 0.3))

    def start_draft(self, order=None, comps=()):
        """Create the class and put the compensatory picks (rounds 8 and 9) into their owners' hands."""
        from .. import draft as D
        L, rng = self.L, self.rng
        self._pool = []
        for card in self.creator.draft_class(L.year):
            p = self.register(card)
            p.where = "draft"
            self._pool.append(p)
        if order is None:
            order = sorted(L.clubs)
            rng.shuffle(order)
        self._slots = D.full_slots(L.year, order, comps)
        for rnd, _, club in self._slots:
            if rnd > D.ROUNDS:
                L.clubs[club].picks.add((L.year, rnd, club))

    def draft_rounds(self, rounds):
        L = self.L
        rounds = set(rounds)
        for rnd, _, orig in self._slots:
            if rnd not in rounds:
                continue
            owner = next(cid for cid, c in L.clubs.items() if (L.year, rnd, orig) in c.picks)
            avail = sorted((p for p in self._pool if p.where == "draft"), key=lambda q: (-self.view(owner, q), q.pid))
            needy = {pos for _, pos in self.need(owner, ("roster", "ps"))}
            pick = next((p for p in avail[:40] if p.pos in needy), avail[0])
            pick.where = "fa"
            L.clubs[owner].picks.discard((L.year, rnd, orig))     # the pick is spent; the real contract replaces its cap reserve
            c = rookie_contract(L.year, min(rnd, 7), first_round_option=(rnd == 1))
            slot = "roster" if len(L.clubs[owner].roster) < 62 else "ps"
            if slot == "ps" and not L.room(owner, "ps"):
                slot = "roster"
            if not L.room(owner, slot):
                self.drop_weakest(owner)          # a club cuts a veteran to sign its pick
            if not L.room(owner, slot) or not self.try_sign(owner, pick, slot, c):
                pick.where = "fa"        # unsigned draftee becomes a free agent
                self.stats["unsigned_picks"] += 1
            self.stats["picks_made"] = self.stats.get("picks_made", 0) + 1

    def finish_draft(self):
        for p in self._pool:
            if p.where == "draft":
                p.where = "fa"
        self.L.complete_draft()

    def free_agency(self):
        L, rng = self.L, self.rng
        order = sorted(L.clubs, key=lambda c: rng.random())
        order = [c for c in self.priority if c in L.clubs] + [c for c in order if c not in self.priority]
        for cid in order:
            self.fix_minimums(cid)
            budget_floor = R.CAP - R.RESERVE - F(90)
            for _ in range(40):
                if (not L.room(cid, "roster") or L.cap_number(cid) >= budget_floor and cid not in self.reckless
                        or len(L.clubs[cid].roster) + len(L.clubs[cid].ps) >= R.PRESEASON_CEILING - 9):    # leave room for 7 picks + 2 comps
                    break
                needy = [pos for _, pos in self.need(cid)] or list(TEMPLATE)
                pos = rng.choice(needy)
                pool = [p for p in self.fas(pos, cid) if self.view(cid, p) > 60]
                if not pool:
                    break
                p, v = None, None
                for q in pool:
                    v = self.negotiate(cid, q)
                    if v is not None:
                        p = q
                        break
                if p is None:
                    continue
                if cid not in self.reckless and L.cap_number(cid) + v > R.CAP - R.RESERVE:
                    continue
                self.try_sign(cid, p, value=v)
            self.fill_squad(cid)

    def fill_squad(self, cid):
        L = self.L
        while len(L.clubs[cid].ps) < R.PS_MAX and L.room(cid, "ps"):
            vets = L.ps_veterans(cid) >= R.PS_VETERANS_MAX
            room = {}

            def numbered(pos):
                if pos not in room:
                    room[pos] = L.number_room(cid, pos)
                return room[pos]
            pool = [p for p in self.cheap(None, cid) if self.quote(cid, p) <= R.MIN_SALARY and not (vets and p.veteran) and numbered(p.pos)]
            if not pool:
                break
            if not self.try_sign(cid, pool[0], "ps", Contract(L.year, (R.MIN_SALARY,), (), (), L.week)):
                break
            if len(L.clubs[cid].roster) + len(L.clubs[cid].ps) >= R.PRESEASON_CEILING:
                break

    def upgrade(self, cid):
        """Upgrade signings (Edition 8.39): before the cut, a club signs a free agent who is clearly better, by her own view and with age
        counted, than her weakest starter at that position, at most `upgrade_max` times, biggest gap first, if the price fits under the cap.
        The cut then sheds whoever the club now values least. This is how a veteran replaces a rookie in the NFL."""
        L, N = self.L, self.tables.negotiation
        gaps = []
        for pos, n in STARTERS.items():
            mine = sorted((self.value(L.players[i], cid) for i in L.clubs[cid].roster if L.players[i].pos == pos and L.players[i].out_weeks == 0), reverse=True)
            if len(mine) < n:
                continue            # short of a starter: the minimums rule fills it
            best = next(iter(self.fas(pos, cid)), None)
            if best is not None and self.value(best, cid) >= mine[n - 1] + N["upgrade_margin"]:
                gaps.append((mine[n - 1] - self.value(best, cid), pos, best))
        done = 0
        for _, pos, best in sorted(gaps, key=lambda g: (g[0], g[1])):
            if done >= N["upgrade_max"] or not L.room(cid, "roster"):
                break
            v = self.negotiate(cid, best)
            if v is None or (cid not in self.reckless and L.cap_number(cid) + v > R.CAP - R.RESERVE):
                continue
            done += self.try_sign(cid, best, value=v)
        self.stats["upgrades"] = self.stats.get("upgrades", 0) + done

    def camp(self, cid):
        """Cut to 53 and 16, clear any overage (the cap binds from the final cut), make sure the minimums hold."""
        L = self.L
        c = L.clubs[cid]
        self.upgrade(cid)
        for _ in range(200):
            # sizes
            while len(c.roster) > R.ROSTER_MAX:
                if not self.drop_weakest(cid, slots=("roster",)):
                    break
            while len(c.ps) > R.PS_MAX or L.ps_veterans(cid) > R.PS_VETERANS_MAX:
                p = sorted((L.players[i] for i in c.ps), key=lambda q: (not (q.veteran and L.ps_veterans(cid) > R.PS_VETERANS_MAX), self.value(q, cid), q.pid))[0]
                L.release(cid, p.pid)
            self.fill_squad(cid)          # the squad counts against the cap, so fill it before measuring overage
            # overage: shed the player whose release relieves the cap the most per point of value lost
            if L.overage(cid) > 0:
                best, best_gain = None, F(0)
                for pid in c.roster + c.ps:
                    p = L.players[pid]
                    k = p.contract
                    gain = k.cap_hit(L.year) - k.release_charges(L.year, L.week).get(L.year, F(0))
                    if p.where == "ps" or gain / (1 + max(0, self.value(p, cid) - 45)) > best_gain:
                        if gain > 0 and (best is None or gain / (1 + max(0, self.value(p, cid) - 45)) > best_gain):
                            best, best_gain = p, gain / (1 + max(0, self.value(p, cid) - 45))
                if best is None:
                    break
                L.release(cid, best.pid)
                continue
            _, unnumbered = L.plan_numbers(cid)
            if unnumbered:          # more players at a position than it has jersey numbers: shed the weakest of that position
                pos = L.players[unnumbered[0]].pos
                if self.release_for_number(cid, pos):
                    continue
                weakest = min((L.players[i] for i in c.roster + c.ps + c.ir if L.players[i].pos == pos),
                              key=lambda q: (q.out_weeks == 0, self.value(q, cid), q.pid))      # an injured player frees a number without costing a healthy body
                L.release(cid, weakest.pid)
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
        for _ in range(30):
            try:
                L.cut_down(cid)
                break
            except Rejected as e:
                # a squad signing can leave the greedy numbering short of a number for a position the club is deep at (the check before a signing
                # looks at one more player, the plan numbers them all): shed the surplus at that position, restore the minimums, fill the squad
                if "jersey" not in str(e):
                    self._reject("cut_down", e)
                    raise
                self.stats["numbering_repairs"] = self.stats.get("numbering_repairs", 0) + 1
                _, unnumbered = L.plan_numbers(cid)
                pos = L.players[unnumbered[0]].pos
                if not self.release_for_number(cid, pos):
                    weakest = min((L.players[i] for i in c.roster + c.ps + c.ir if L.players[i].pos == pos),
                                  key=lambda q: (q.out_weeks == 0, self.value(q, cid), q.pid))
                    L.release(cid, weakest.pid)
                if shortfalls(self.counts(cid, ("roster",))):
                    self.fix_minimums(cid)
        else:
            self._reject("cut_down", Rejected(f"{cid}: numbering not repaired"))
            raise Rejected(f"{cid}: numbering not repaired")
        pay = L.cap_number(cid)
        if self.stats["payroll_min"] is None or pay < self.stats["payroll_min"]:
            self.stats["payroll_min"] = pay

    def season(self):
        for week in range(1, R.PAID_WEEKS + 1):
            self.play_paid_week(week, None)

    def play_paid_week(self, week, playing):
        """One paid week (regular or break): clock, injuries, club upkeep, game days for the clubs that play, trades,
        waivers, audits. `playing` is the set of clubs with a game this week (None: every club, for the soak)."""
        L, rng = self.L, self.rng
        L.advance_week()
        assert L.week == week, (L.week, week)
        self.injuries()
        for cid in L.clubs:
            self.weekly(cid, week)
        self.games(week, playing)
        if week <= R.TRADE_LAST_PAID_WEEK:
            self.trades()
        order = sorted(L.clubs)
        rng.shuffle(order)
        self.stats["claims"] += len(L.resolve_waivers(order, wants=lambda cid, p: self.view(cid, p) > 65 and rng.random() < 0.2))
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
            dressed = set(self.active[cid].active) if self.engine_injuries and cid in self.active else ()
            for pid in list(c.roster):
                if pid in dressed:          # the engine injures those who play
                    continue
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
            c_players = sorted((L.players[i] for i in c.roster), key=lambda q: (self.value(q, cid), q.pid))
            for p in c_players[:3]:
                if rng.random() < (0.6 if cid in self.reckless else 0.2) and p.contract.bonuses:
                    try:
                        L.release(cid, p.pid, split=bool(rng.random() < 0.3 and L.year not in c.split_years))
                    except Rejected as e:
                        self._reject("release", e)
                    break
        while len(c.roster) < R.ROSTER_MAX - 2 and rng.random() < 0.3:
            pool = self.cheap(None, cid)
            if not pool:
                break
            self.try_sign(cid, pool[0], "roster", Contract(L.year, (R.MIN_SALARY,), (), (), L.week))
            break

    def games(self, week, playing=None):
        L = self.L
        for cid in (sorted(L.clubs) if playing is None else sorted(playing)):
            before = sum(p.elevations for p in L.players.values() if p.club == cid and p.where == "ps")
            gd = gameday.build(L, cid, judge=lambda q, c=cid: coaching.judged(self, c, q))
            self.active[cid], self.active_week[cid] = gd, L.week
            after = sum(p.elevations for p in L.players.values() if p.club == cid and p.where == "ps")
            self.stats["elevations"] += after - before
            self.stats["games"] += 1
            self.stats.setdefault("games_by_week", {})[week] = self.stats.get("games_by_week", {}).get(week, 0) + 1
            if gd.short:
                self.stats["short_games"] += 1
            if gd.emergency:
                self.stats["emergency_games"] += 1
            if not gd.can_field:
                self.stats["forfeits"] += 1

    def trades(self):
        """The trading step (Edition 8.26): blocks, proposals, the three-round protocol, ledger validation, the log."""
        return trading.window(self)

    def trades_random(self):
        """The earlier stand-in: three random same-position swaps. Kept for comparison runs."""
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
