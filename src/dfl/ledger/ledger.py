"""League ledger: every club's roster lists, contracts, dead money, picks and the legal transactions between them.

Principles (Design Document 8.48, sections 11, 12, 17)
  * Reserve/commit: every transaction is applied to a snapshot-protected copy of the clubs and players it touches and
    rolled back whole if any rule fails, so money, picks, roster places and contracts are never assigned twice.
  * Cap timing (Ed 8.9): the cap is enforced all year. Until the final cut a club's cap number counts only its 69 highest
    cap hits plus dead money (the NFL's top-51 rule, adapted); afterwards every contract counts. A move that leaves a
    club over the cap and raises its cap number is rejected; a release is never blocked by the cap (it may carry a club
    over; the club then cannot add cap until it is back under). At the final cut every club must be at or under the cap.
    The viability invariant binds at all times, which is what guarantees the cut is reachable.
  * Waivers (Ed 8.9): a player with 4 or more credited seasons who is released goes straight to free agency.
  * There is no salary floor. Practice-squad salaries count against the cap.
  * Viability invariant: for the current and every committed future year,
        committed money + 48 x minimum salary + reserve <= cap          (committed money <= 735)
    "Committed money" is what the club owes in that year even if every contract were released today: dead money,
    guarantees, earned base, and (this year) every unpaid bonus accelerated onto the cap.
    This is the strict, worst-case reading of the document's "dead money" term (assumption A1 in docs/LEDGER.md).
    In the current year the 48 x minimum replacement cost is pro-rated to the paid weeks still to come (A2). A move is
    rejected if it leaves a year over the limit AND raises that year's committed money; a club that drifts over the
    limit by the passage of weeks (earned salary accrues) is not frozen, it just cannot make things worse.
  * No banking: unused room is lost; nothing carries to the next year.
"""
import copy
import hashlib
import json
from dataclasses import dataclass, field
from fractions import Fraction as F

from . import numbers as N
from . import rules as R
from .contracts import Contract, ContractError
from .positions import OL, shortfalls


class LedgerError(Exception):
    pass


class Rejected(LedgerError):
    """A transaction broke a rule and was rolled back."""


@dataclass
class Player:
    pid: int
    pos: str
    ovr: int
    age: int = 24
    seasons: int = 0               # credited seasons
    contract: Contract = None
    club: str = None
    where: str = "fa"              # fa | roster | ps | ir | waivers | retired
    out_weeks: int = 0
    elevations: int = 0
    ir_since: int = None
    prev_club: str = None          # club whose contract just expired (free-agent movement tracking)
    snap_top: bool = False         # top quarter of her club's snaps last year (PLACEHOLDER: ranked by her coaches' judged overall, Edition 8.37, until snaps are counted)
    number: int = None             # jersey number while with a club (Ed 8.15)
    last_club: str = None          # club and number she last held, so a same-club re-signing can take it back
    last_number: int = None
    pool_years: int = 0            # year openings survived unsigned in the free-agent pool; reset when hired; retired at POOL_YEARS_MAX

    @property
    def veteran(self):
        return self.seasons > R.PS_VETERAN_AFTER


@dataclass
class Club:
    cid: str
    roster: list = field(default_factory=list)
    ps: list = field(default_factory=list)
    ir: list = field(default_factory=list)
    dead: dict = field(default_factory=dict)         # year -> dead money
    picks: set = field(default_factory=set)          # (draft_year, round, original cid)
    ir_returns_used: int = 0
    split_years: set = field(default_factory=set)
    _cmap: dict = field(default=None, repr=False, compare=False)
    _cmap_week: tuple = field(default=None, repr=False, compare=False)

    def all_pids(self):
        return self.roster + self.ps + self.ir

    def snapshot(self):
        s = Club(self.cid, list(self.roster), list(self.ps), list(self.ir), dict(self.dead),
                 set(self.picks), self.ir_returns_used, set(self.split_years))
        return s


class Ledger:
    def __init__(self, cids, year=2000):
        self.year, self.week, self.phase = year, 0, "offseason"
        self.draft_completed = year - 1
        self.clubs = {c: Club(c) for c in cids}
        self.players = {}
        self.waivers = {}              # pid -> dict(from=cid, relief={year: amount})
        self.events = []
        self.fa_moves = []             # free agents who changed clubs this offseason (feeds compensatory picks)
        self.retired = {"pool": 0, "age": 0}     # characters removed from the list, by reason (career records go to the people layer)
        self._next_pid = 1
        for c in cids:
            for y in range(year, year + 3):
                for rnd in range(1, 8):
                    self.clubs[c].picks.add((y, rnd, c))

    # ---------- bookkeeping
    def log(self, kind, **kw):
        self.events.append({"id": len(self.events) + 1, "year": self.year, "week": self.week, "kind": kind, **kw})

    def new_player(self, pos, ovr, age=24, seasons=0, pid=None):
        """Register a player. With `pid` (a character card's id) the ledger uses that id; otherwise it numbers her itself."""
        if pid is None:
            pid = self._next_pid
        if pid in self.players:
            raise Rejected(f"player id {pid} is already in use")
        p = Player(pid, pos, ovr, age, seasons)
        self.players[p.pid] = p
        self._next_pid = max(self._next_pid, pid + 1)
        return p

    def digest(self):
        h = hashlib.sha256()
        for cid in sorted(self.clubs):
            c = self.clubs[cid]
            h.update(json.dumps([cid, c.roster, c.ps, c.ir, sorted((y, str(v)) for y, v in c.dead.items()),
                                 sorted(c.picks),
                                 c.ir_returns_used], default=str).encode())
        for pid in sorted(self.players):
            p = self.players[pid]
            h.update(f"{pid}|{p.pos}|{p.ovr}|{p.club}|{p.where}|{p.contract}|{p.out_weeks}|{p.number}".encode())
        return h.hexdigest()

    # ---------- money
    def contracts_of(self, cid):
        return [self.players[p].contract for p in self.clubs[cid].all_pids()]

    def rookie_reserve(self, cid):
        """Cap hits of the club's unmade picks in this year's draft, at the rookie scale (round 8 and 9 picks at the round-7 scale).
        They count against the cap until the pick is made, so a club always keeps the room to sign its class (NFL rookie reserve)."""
        if self.draft_completed >= self.year:
            return []
        return sorted((R.ROOKIE_SCALE[min(pk[1], 7) - 1] for pk in self.clubs[cid].picks if pk[0] == self.year), reverse=True)

    def cap_number(self, cid, year=None):
        year = self.year if year is None else year
        c = self.clubs[cid]
        hits = [k.cap_hit(year) for k in self.contracts_of(cid) if k]
        if year == self.year:
            hits += self.rookie_reserve(cid)
        if year == self.year and self.phase in ("offseason", "camp") and len(hits) > R.CAP_COUNTED_BEFORE_CUT:
            hits = sorted(hits, reverse=True)[:R.CAP_COUNTED_BEFORE_CUT]
        return sum(hits, F(0)) + c.dead.get(year, F(0))

    def overage(self, cid):
        return max(F(0), self.cap_number(cid) - R.CAP)


    def committed(self, cid):
        """{year: money owed in that year if every contract were released at the start of it (this year: now)}."""
        c = self.clubs[cid]
        key = (self.year, self.week)
        if c._cmap is not None and c._cmap_week == key:
            return c._cmap
        m = {}
        for y, v in c.dead.items():
            if y >= self.year:
                m[y] = m.get(y, F(0)) + v
        for pid in c.all_pids():
            k = self.players[pid].contract
            if k:
                # worst case for each year: every contract released at the start of that year (now, for this year),
                # so the accelerated bonus lands in the year it would actually land in
                for y in range(self.year, k.end_year + 1):
                    v = k.release_charges(y, self.week if y == self.year else 0).get(y, F(0))
                    if v:
                        m[y] = m.get(y, F(0)) + v
        c._cmap, c._cmap_week = m, key
        return m

    def limit(self, year):
        """Largest committed money the invariant allows in `year`."""
        repl = R.REQUIRED_PLACES * R.MIN_SALARY
        if year == self.year:
            repl = repl * F(R.PAID_WEEKS - self.week, R.PAID_WEEKS)
        return R.CAP - R.RESERVE - repl

    def viability_margin(self, cid):
        m = self.committed(cid)
        years = set(m) | {self.year}
        return min(self.limit(y) - m.get(y, F(0)) for y in years)

    def _viable(self, cid, before):
        after = self.committed(cid)
        return all(v <= self.limit(y) or v <= before.get(y, F(0)) for y, v in after.items())

    # ---------- transaction wrapper (reserve / commit)
    def _txn(self, cids, pids, fn, label, capcheck=True):
        before = {c: dict(self.committed(c)) for c in cids}
        cap_before = {c: self.cap_number(c) for c in cids}
        snap_c = {c: self.clubs[c].snapshot() for c in cids}
        snap_p = {p: copy.copy(self.players[p]) for p in pids if p in self.players}
        snap_w = {k: {**v, "relief": dict(v["relief"])} for k, v in self.waivers.items()}
        n_events, n_moves = len(self.events), len(self.fa_moves)
        try:
            out = fn()
            for c in cids:
                self.clubs[c]._cmap = None
                if not self._viable(c, before[c]):
                    raise Rejected(f"{label}: breaks the viability invariant for {c}")
                if capcheck:
                    now = self.cap_number(c)
                    if now > R.CAP and now > cap_before[c]:
                        raise Rejected(f"{label}: breaks the cap for {c}")
            return out
        except Exception:
            for c, s in snap_c.items():          # restore in place so references held elsewhere stay valid
                self.clubs[c].__dict__.update(s.__dict__)
                self.clubs[c]._cmap = None
            for p, s in snap_p.items():
                self.players[p].__dict__.update(s.__dict__)
            self.waivers = snap_w
            del self.events[n_events:]
            del self.fa_moves[n_moves:]
            raise

    # ---------- roster counts
    def room(self, cid, slot):
        c = self.clubs[cid]
        if self.phase in ("offseason", "camp"):
            return len(c.roster) + len(c.ps) < R.PRESEASON_CEILING
        return len(c.roster) < R.ROSTER_MAX if slot == "roster" else len(c.ps) < R.PS_MAX

    def ps_veterans(self, cid):
        return sum(1 for p in self.clubs[cid].ps if self.players[p].veteran)

    # ---------- transactions
    # ---------- jersey numbers
    def _relaxed(self):
        return self.phase in ("offseason", "camp")

    def _taken(self, cid, but=None):
        return {self.players[i].number for i in self.clubs[cid].all_pids() if i != but and self.players[i].number is not None}

    def _give_number(self, cid, p, prefer=None):
        """Give `p` (already on `cid`'s lists) a number: keep `prefer` if free and legal, else the lowest open one. A player in her own
        block has priority: if only overflow holders stand in the way, one of them is moved to another number."""
        relaxed = self._relaxed()
        taken = self._taken(cid, but=p.pid)
        n = N.choose(p.pos, taken, relaxed, prefer)
        if n is None or (not N.is_primary(p.pos, n) and n != prefer and not relaxed):
            lo, hi = N.PRIMARY[p.pos]
            for i in sorted(self.clubs[cid].all_pids()):
                q = self.players[i]
                if i != p.pid and q.number is not None and lo <= q.number <= hi and not N.is_primary(q.pos, q.number):
                    other = N.choose(q.pos, taken | {q.number}, relaxed)
                    if other is not None:
                        q.number, n = other, q.number
                        break
        if n is None:
            raise Rejected(f"no jersey number open for {p.pos} at {cid}")
        p.number = n

    def plan_numbers(self, cid):
        """In-season numbering of a club: ({pid: new number} for players whose number changes, [pids who cannot get a legal one]).
        Players holding a number in their own block keep it; players with none (or an illegal or shared one) are placed next, so a
        primary player takes precedence over an overflow holder; overflow holders keep theirs if still free."""
        c = self.clubs[cid]
        taken, keep, fresh, overflow = set(), set(), [], []
        for pid in sorted(c.all_pids()):
            p = self.players[pid]
            n = p.number
            if n is not None and N.is_primary(p.pos, n) and n not in taken:
                taken.add(n)
            elif n is not None and N.legal(p.pos, n) and n not in taken:
                overflow.append(p)
            else:
                fresh.append(p)
        new, bad = {}, []
        for p in fresh:
            n = N.choose(p.pos, taken)
            if n is None:
                bad.append(p.pid)
            else:
                new[p.pid] = n
                taken.add(n)
        for p in overflow:
            if p.number not in taken:
                taken.add(p.number)
                continue
            n = N.choose(p.pos, taken)
            if n is None:
                bad.append(p.pid)
            else:
                new[p.pid] = n
                taken.add(n)
        return new, bad

    def number_room(self, cid, pos):
        """Could one more player at `pos` hold a legal in-season number at `cid`? (Used by autopilots before signing in camp.)"""
        new, bad = self.plan_numbers(cid)
        if bad:
            return False
        taken = {self.players[i].number for i in self.clubs[cid].all_pids() if i not in new and self.players[i].number is not None}
        return N.choose(pos, taken | set(new.values())) is not None

    def _release_number(self, p, cid):
        if p.number is not None:
            p.last_club, p.last_number = cid, p.number
        p.number = None

    def sign(self, cid, pid, contract, slot="roster"):
        p = self.players[pid]

        def go():
            if p.where != "fa":
                raise Rejected(f"player {pid} is not a free agent")
            if (contract.start_year, contract.start_week) != (self.year, self.week):
                raise Rejected("contract must start now")
            if slot not in ("roster", "ps"):
                raise Rejected("slot")
            if not self.room(cid, slot):
                raise Rejected(f"no {slot} room for {cid}")
            if slot == "ps" and p.veteran and self.ps_veterans(cid) >= R.PS_VETERANS_MAX:
                raise Rejected("practice squad veteran limit")
            p.contract, p.club, p.where = contract, cid, slot
            p.pool_years = 0
            getattr(self.clubs[cid], slot).append(pid)
            self._give_number(cid, p, prefer=p.last_number if p.last_club == cid else None)
            if p.prev_club is not None and self.phase in ("offseason", "camp"):
                if p.prev_club != cid:
                    self.fa_moves.append({"pid": pid, "from": p.prev_club, "to": cid, "base": contract.years[0],
                                          "snap_top_quarter": p.snap_top})
                p.prev_club = None
            self.log("sign", club=cid, pid=pid, slot=slot, cap_hit=str(contract.cap_hit(self.year)))
        self._txn([cid], [pid], go, "sign")
        return p

    def _lists(self, cid):
        c = self.clubs[cid]
        return {"roster": c.roster, "ps": c.ps, "ir": c.ir}

    def release(self, cid, pid, split=False):
        """Release a player: charges become dead money; she enters waivers (resolve with `resolve_waivers`), unless she has 4+ credited seasons, when she goes straight to free agency."""
        p = self.players[pid]

        def go():
            if p.club != cid or p.where not in ("roster", "ps", "ir"):
                raise Rejected("player is not under contract to that club")
            c = self.clubs[cid]
            if split:
                if self.year in c.split_years:
                    raise Rejected("split release already used this league year")
                c.split_years.add(self.year)
            charges = p.contract.release_charges(self.year, self.week, split)
            base_only = p.contract.release_charges(self.year, self.week, split, keep_guarantees=False)
            for y, v in charges.items():
                c.dead[y] = c.dead.get(y, F(0)) + v
            relief = {y: charges.get(y, F(0)) - base_only.get(y, F(0)) for y in charges}
            relief = {y: v for y, v in relief.items() if v > 0}
            # a guarantee claimed by another club leaves this club; the bonus never does
            self._lists(cid)[p.where].remove(pid)
            self.waivers[pid] = {"from": cid, "relief": relief, "contract": p.contract}
            if p.seasons >= R.VESTED_SEASONS:      # a vested veteran is not subject to waivers
                del self.waivers[pid]
                p.where, p.club, p.contract = "fa", None, None
                self._release_number(p, cid)
            else:
                p.where = "waivers"
            self.log("release", club=cid, pid=pid, split=split, dead={str(y): str(v) for y, v in charges.items()})
        self._txn([cid], [pid], go, "release", capcheck=False)

    def resolve_waivers(self, claim_order, wants=None):
        """One simulated day passes. `claim_order` is the priority list of club ids; `wants(cid, player)` says whether
        a club claims. A claimant assumes unpaid base and guarantees (never the bonus). Unclaimed players become free agents."""
        claimed = []
        for pid in sorted(self.waivers):
            w = self.waivers[pid]
            p = self.players[pid]
            took = False
            for cid in claim_order:
                if cid == w["from"] or (wants and not wants(cid, p)):
                    continue
                try:
                    self._claim(cid, pid)
                    took = True
                    claimed.append((pid, cid))
                    break
                except Rejected:
                    continue
            if not took:
                p.where, p.club, p.contract = "fa", None, None
                self._release_number(p, w["from"])
                del self.waivers[pid]
        return claimed

    def _claim(self, cid, pid):
        w = self.waivers[pid]
        p = self.players[pid]
        src = w["from"]

        def go():
            slot = "roster"
            if not self.room(cid, slot):
                raise Rejected("no roster room to claim")
            for y, v in w["relief"].items():
                self.clubs[src].dead[y] = self.clubs[src].dead.get(y, F(0)) - v
            p.contract = w["contract"].assumed_by_receiver(self.year, self.week)
            p.club, p.where = cid, "roster"
            self.clubs[cid].roster.append(pid)
            self._give_number(cid, p, prefer=p.number)
            del self.waivers[pid]
            self.log("claim", club=cid, pid=pid, from_club=src)
        self._txn([cid, src], [pid], go, "claim")

    def trade_open(self):
        return self.phase != "season" or self.week <= R.TRADE_LAST_PAID_WEEK

    def trade(self, a, a_gives, b, b_gives, a_picks=(), b_picks=()):
        pids = list(a_gives) + list(b_gives)

        def go():
            if not self.trade_open():
                raise Rejected("trade window closed")
            if a == b:
                raise Rejected("same club")
            for src, dst, gives, picks in ((a, b, a_gives, a_picks), (b, a, b_gives, b_picks)):
                for pid in gives:
                    p = self.players[pid]
                    if p.club != src or p.where not in ("roster", "ir"):
                        raise Rejected("only players on the roster or injured reserve can be traded")
                    k = p.contract
                    for y, v in k.release_charges(self.year, self.week, False, keep_guarantees=False).items():
                        self.clubs[src].dead[y] = self.clubs[src].dead.get(y, F(0)) + v
                    self._lists(src)[p.where].remove(pid)
                    p.contract = k.assumed_by_receiver(self.year, self.week)
                    p.club = dst
                    self._lists(dst)[p.where].append(pid)
                for pk in picks:
                    if pk not in self.clubs[src].picks:
                        raise Rejected(f"{src} does not own pick {pk}")
                    if not self.draft_completed < pk[0] <= self.draft_completed + 3:
                        raise Rejected("only the next three uncompleted drafts' picks are tradable")
                    self.clubs[src].picks.remove(pk)
                    self.clubs[dst].picks.add(pk)
            for src, dst, gives in ((a, b, a_gives), (b, a, b_gives)):
                for pid in sorted(gives):
                    self._give_number(dst, self.players[pid], prefer=self.players[pid].number)
            for c in (a, b):
                cl = self.clubs[c]
                if self.phase in ("offseason", "camp"):
                    if len(cl.roster) + len(cl.ps) > R.PRESEASON_CEILING:
                        raise Rejected(f"{c} over the preseason ceiling")
                elif len(cl.roster) > R.ROSTER_MAX:
                    raise Rejected(f"{c} over 53")
            self.log("trade", a=a, b=b, a_gives=list(a_gives), b_gives=list(b_gives))
        self._txn([a, b], pids, go, "trade")

    def restructure(self, cid, pid, amount):
        p = self.players[pid]

        def go():
            if p.club != cid or p.where not in ("roster", "ir"):
                raise Rejected("player not on the roster")
            try:
                p.contract = p.contract.restructured(self.year, self.week, F(amount))
            except ContractError as e:
                raise Rejected(str(e))
            self.log("restructure", club=cid, pid=pid, amount=str(amount))
        self._txn([cid], [pid], go, "restructure")

    # ---------- injured reserve, practice squad
    def place_on_ir(self, cid, pid):
        p = self.players[pid]
        if p.club != cid or p.where != "roster":
            raise Rejected("player not on the 53")
        if p.out_weeks < R.IR_MIN_GAMES:
            raise Rejected("injury shorter than the four-game minimum")
        self.clubs[cid].roster.remove(pid)
        self.clubs[cid].ir.append(pid)
        p.where, p.ir_since = "ir", self.week
        self.log("ir", club=cid, pid=pid)

    def return_from_ir(self, cid, pid):
        p, c = self.players[pid], self.clubs[cid]
        if p.club != cid or p.where != "ir":
            raise Rejected("not on injured reserve")
        if c.ir_returns_used >= R.IR_RETURNS_MAX:
            raise Rejected("no designated returns left this season")
        if p.out_weeks > 0 or self.week - p.ir_since < R.IR_MIN_GAMES:
            raise Rejected("not yet eligible to return")
        if len(c.roster) >= R.ROSTER_MAX and self.phase not in ("offseason", "camp"):
            raise Rejected("no roster room")
        c.ir.remove(pid)
        c.roster.append(pid)
        p.where, p.ir_since = "roster", None
        c.ir_returns_used += 1
        self.log("ir_return", club=cid, pid=pid)

    def promote(self, cid, pid):
        p, c = self.players[pid], self.clubs[cid]
        if p.club != cid or p.where != "ps":
            raise Rejected("not on the practice squad")
        if len(c.roster) >= R.ROSTER_MAX and self.phase not in ("offseason", "camp"):
            raise Rejected("no roster room")
        c.ps.remove(pid)
        c.roster.append(pid)
        p.where = "roster"
        self.log("promote", club=cid, pid=pid)

    # ---------- calendar
    def start_camp(self):
        self.phase = "camp"

    def cut_down(self, cid):
        """The final cut to 53 and 16: sizes, cap (the cap binds from here on), positional coverage. There is no salary floor."""
        c = self.clubs[cid]
        if self.phase != "camp":
            raise Rejected("not in camp")
        if len(c.roster) > R.ROSTER_MAX or len(c.ps) > R.PS_MAX or self.ps_veterans(cid) > R.PS_VETERANS_MAX:
            raise Rejected(f"{cid}: roster {len(c.roster)} / squad {len(c.ps)} not within 53 / 16")
        if self.overage(cid) > 0:
            raise Rejected(f"{cid}: overage {self.overage(cid)} not cleared")
        counts = {}
        for pid in c.roster:
            p = self.players[pid]
            if p.out_weeks == 0:
                counts[p.pos] = counts.get(p.pos, 0) + 1
        short = shortfalls(counts)
        if short:
            raise Rejected(f"{cid}: positional minimums not met {short}")
        # the final cut ends the camp relaxation: anyone whose number is outside her blocks is given a legal one
        new, bad = self.plan_numbers(cid)
        if bad:
            raise Rejected(f"{cid}: no legal jersey number for {sorted({self.players[i].pos for i in bad})}")
        for pid, n in new.items():
            self.players[pid].number = n
        self.log("cut_down", club=cid)

    def complete_draft(self):
        self.draft_completed = self.year

    def probe(self, cids, pids, fn):
        """Would `fn` succeed? Runs it and rolls everything back either way."""
        class _Undo(Exception):
            pass

        def go():
            fn()
            raise _Undo()
        try:
            self._txn(cids, pids, go, "probe")
        except _Undo:
            return True
        except Rejected:
            return False
        return True

    def legal_moves(self, cid, kinds=None):
        """Every single transaction this club could legally make right now, one at a time, as `(kind, pid, slot)` (Phase 1's enumerator).
        Each is proved by a dry run that is rolled back, so nothing changes. It is lazy: take the first to learn that a move exists, or
        read it all for a decision point. Kinds: `sign_min` (a free agent at the league minimum, to `roster` or `ps`), `release`,
        `ir` (place an injured player on injured reserve), `ir_return`, `promote` (practice squad to the 53). Trades and waiver claims
        involve other clubs and are enumerated by their own steps."""
        want = set(kinds) if kinds else {"sign_min", "release", "ir", "ir_return", "promote"}
        c = self.clubs[cid]
        if "sign_min" in want:
            for p in sorted((q for q in self.players.values() if q.where == "fa" and q.out_weeks == 0), key=lambda q: q.pid):
                for slot in ("roster", "ps"):
                    if self.room(cid, slot) and self.probe([cid], [p.pid], lambda p=p, slot=slot: self.sign(cid, p.pid, Contract(self.year, (R.MIN_SALARY,), (), (), self.week), slot)):
                        yield ("sign_min", p.pid, slot)
        if "release" in want:
            for slot in ("roster", "ps", "ir"):
                for pid in list(getattr(c, slot)):
                    if self.probe([cid], [pid], lambda pid=pid: self.release(cid, pid)):
                        yield ("release", pid, slot)
        for kind, slot, op in (("ir", "roster", self.place_on_ir), ("ir_return", "ir", self.return_from_ir), ("promote", "ps", self.promote)):
            if kind in want:
                for pid in list(getattr(c, slot)):
                    if self.probe([cid], [pid], lambda pid=pid, op=op: op(cid, pid)):
                        yield (kind, pid, slot)

    def begin_season(self):
        over = [c for c in self.clubs if self.overage(c) > 0]
        if over:
            raise Rejected(f"clubs over the cap at the end of the final cut: {over}")
        self.phase, self.week = "season", 0

    def advance_week(self):
        self.week += 1
        for p in self.players.values():
            if p.out_weeks > 0:
                p.out_weeks -= 1
        for c in self.clubs.values():
            c._cmap = None
        self.log("week")

    def end_season(self):
        self.phase = "post"

    def retire(self, pid, reason):
        """A free agent leaves the game for good and is removed from the list (database size). Only pool members can retire here."""
        p = self.players[pid]
        if p.where != "fa":
            raise Rejected(f"player {pid} is not in the pool")
        del self.players[pid]
        self.retired[reason] = self.retired.get(reason, 0) + 1

    def retire_contracted(self, pid):
        """A player under contract retires (Edition 8.38): her contract ends with her, nothing is charged to the club, and she is gone."""
        p = self.players[pid]
        if p.where not in ("roster", "ps", "ir"):
            raise Rejected(f"player {pid} is not on a club's lists")
        cid = p.club
        self._lists(cid)[p.where].remove(pid)
        self._release_number(p, cid)
        self.clubs[cid]._cmap = None
        del self.players[pid]
        self.retired["contract"] = self.retired.get("contract", 0) + 1
        self.log("retire_contract", club=cid, pid=pid)

    def new_year(self, rank=None):
        """League year rolls over. Contracts that have run out release their players to the free-agent pool; elevations and
        IR returns reset; old dead money drops off; nothing is banked."""
        rank = rank or (lambda cid, p: p.ovr)       # how good her club thinks she is; without a club view, the true overall
        tops = {}
        for c in self.clubs.values():       # the club's top quarter by her coaches' judgement (Edition 8.37), taken before the year turns
            ranked = sorted(c.all_pids(), key=lambda i: (-rank(c.cid, self.players[i]), i))
            tops[c.cid] = set(ranked[:max(1, len(ranked) // 4)])
        self.year += 1
        self.week, self.phase = 0, "offseason"
        self.fa_moves = []
        for pid, p in list(self.players.items()):
            p.prev_club = None
            if p.where == "fa":
                p.pool_years += 1
                if p.pool_years >= R.POOL_YEARS_MAX:
                    self.retire(pid, "pool")
        for c in self.clubs.values():
            top = tops[c.cid]
            for pid in list(c.all_pids()):
                p = self.players[pid]
                p.snap_top = pid in top
                if p.contract.end_year < self.year:
                    self._lists(c.cid)[p.where].remove(pid)
                    p.prev_club = c.cid
                    self._release_number(p, c.cid)
                    p.contract, p.club, p.where = None, None, "fa"
            c.dead = {y: v for y, v in c.dead.items() if y >= self.year}
            c.ir_returns_used = 0
            c._cmap = None
            for rnd in range(1, 8):
                c.picks.add((self.year + 2, rnd, c.cid))
        for p in self.players.values():
            p.elevations = 0
        self.log("new_year")
