"""Step 7, part 2: the ledger against an independent model, on contracts one by one and on long random sequences of transactions.

`tests/ledger_oracle.py` recomputes cap numbers, commitments, limits and list invariants from raw fields, from the Design Document and not
from the ledger's code. Here the ledger is driven the way a league drives it, and also the ways a league never would (wrong club, a player
who is not hers, a trade of picks she does not own, a restructure larger than her base), at every phase of the year, for several years.
After EVERY call, accepted or rejected, the model and the ledger must agree, and:

  * a rejected call leaves the ledger exactly as it found it (compared by the full state digest, caches aside);
  * an accepted signing, claim, trade or restructure never leaves a club over the cap with a higher cap number than before;
  * an accepted signing, release, claim, trade or restructure never leaves a year over the viability limit with a higher figure than before;
  * the lists are sound: a player on one list only, numbers unique and (from the final cut) inside their blocks, sizes within the limits,
    each pick owned once, no negative dead money, every contract inside the document's limits.
"""
import os
import random
import sys
import unittest
from fractions import Fraction as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import ledger_oracle as O  # noqa: E402
from dfl import neutral  # noqa: E402
from dfl.ledger import gameday  # noqa: E402
from dfl.ledger.contracts import Contract, ContractError, rookie_contract  # noqa: E402
from dfl.ledger.ledger import Ledger, Rejected  # noqa: E402
from dfl.ledger.positions import MINIMUMS, POSITIONS  # noqa: E402

CACHES = neutral.DIGEST_IGNORE + ("_cmap", "_cmap_week")


def state_digest(L):
    return neutral.deep_digest(L, ignore=CACHES)


# ------------------------------------------------------------------------------------------------------------- contracts, one by one
def random_contract(rng, year, week, one_year=False):
    n = 1 if one_year else rng.randint(1, 5)
    shape = rng.choice(("steady", "rising", "declining"))
    base = [5, 5, 6, 8, 12, 20, 40, 80, 150][rng.randint(0, 8)]
    if shape == "steady":
        ys = [base] * n
    else:
        ys = sorted(F(base) + F(rng.randint(0, 40)) * rng.randint(0, 1) + F(rng.randint(0, 3), 2) for _ in range(n))
        ys = ys if shape == "rising" else ys[::-1]
    gs = [rng.choice((0, 0, F(y), F(y) / 2)) for y in ys]
    bonuses = []
    if rng.random() < 0.4:
        p = rng.randint(1, min(5, n))
        bonuses.append((F(rng.choice((5, 10, 24, 60, 120, 7))) , rng.randint(0, n - p), p))
        if rng.random() < 0.2 and n >= 2:
            q = rng.randint(1, min(5, n))
            bonuses.append((F(rng.choice((9, 15, 33))), rng.randint(0, n - q), q))
    return Contract(year, tuple(ys), tuple(gs), tuple(bonuses), week, shape)


class ContractMoney(unittest.TestCase):
    """Cap hits, uncharged bonuses, release and trade charges, restructures: the contract class against the model, on 3,000 random contracts."""

    @classmethod
    def setUpClass(cls):
        rng = random.Random(7)
        cls.cases = []
        while len(cls.cases) < 3000:
            year, week = 2001 + rng.randint(0, 3), rng.choice((0, 0, 0, 1, 5, 11, 21))
            cls.cases.append((rng, random_contract(rng, year, week)))

    def test_cap_hit_and_uncharged_bonus_by_year(self):
        for _, k in self.cases:
            for y in range(k.start_year - 1, k.start_year + len(k.years) + 1):
                self.assertEqual(k.cap_hit(y), O.cap_hit(k, y), (k, y))
                self.assertEqual(k.base_charge(y) + k.bonus_charge(y), O.cap_hit(k, y))
            for y in range(k.start_year, k.start_year + len(k.years)):
                self.assertEqual(k.accelerated_bonus(y), O.uncharged_bonus(k, y), (k, y))

    def test_bonus_total_is_charged_exactly_once_over_its_proration(self):
        for _, k in self.cases:
            total = sum(O.bonus_charge(k, y) for y in range(k.start_year, k.start_year + len(k.years)))
            self.assertEqual(total, sum(a for a, _, _ in k.bonuses), k)

    def test_release_charges_every_week_and_every_year(self):
        for rng, k in self.cases[:1500]:
            for y in range(k.start_year, k.start_year + len(k.years)):
                first = k.start_week if y == k.start_year else 0
                for week in sorted({first, min(22, first + 1), rng.randint(first, 22), 22}):
                    for split in (False, True):
                        for keep in (True, False):
                            self.assertEqual(k.release_charges(y, week, split, keep), O.dead_if_released(k, y, week, split, keep), (k, y, week, split, keep))

    def test_a_release_never_charges_more_than_the_contract_could_ever_cost_her_club(self):
        for rng, k in self.cases[:1500]:
            y = k.start_year
            dead = O.dead_if_released(k, y, k.start_week)
            owed = sum(F(g) for g in k.guaranteed) + sum(a for a, _, _ in k.bonuses)
            self.assertLessEqual(sum(dead.values()), owed + sum(F(v) for v in k.years), k)       # earned base + guarantees + bonuses, no more

    def test_the_receiver_takes_unpaid_base_and_guarantees_never_the_bonus(self):
        for rng, k in self.cases[:1500]:
            y = rng.randint(k.start_year, k.start_year + len(k.years) - 1)
            first = k.start_week if y == k.start_year else 0
            week = rng.randint(first, 22)
            r = k.assumed_by_receiver(y, week)
            self.assertEqual(r.bonuses, ())
            for yy in range(y, k.start_year + len(k.years)):
                paid = F(22 - week, 22) if yy == y else F(1)
                self.assertEqual(r.base_charge(yy), F(k.years[yy - k.start_year]) * paid, (k, y, week, yy))
                self.assertEqual(r.guarantee_in(yy), F(k.guaranteed[yy - k.start_year]) * paid, (k, y, week, yy))
            self.assertEqual(r.base_charge(y - 1), 0)

    def test_restructure_moves_base_into_a_bonus_over_the_remaining_years(self):
        done = 0
        for rng, k in self.cases:
            y = rng.randint(k.start_year, k.start_year + len(k.years) - 1)
            i = y - k.start_year
            share = F(22 - k.start_week, 22) if i == 0 else F(1)
            room = F(k.years[i]) * share - F(5) * share
            if room <= 0:
                continue
            amount = room * F(rng.randint(1, 4), 4)
            try:
                new = k.restructured(y, 0 if i else k.start_week, amount)
            except ContractError:
                continue                                               # a shape the document allows may still be refused (non-monotone result): not wrong
            done += 1
            n = min(5, len(k.years) - i)
            self.assertEqual(new.bonuses[-1], (amount, i, n))
            self.assertEqual(new.start_week, k.start_week)
            self.assertGreaterEqual(min(new.years), 5)                 # base never below the minimum
            self.assertEqual(O.base_charge(new, y), O.base_charge(k, y) - amount)
            self.assertEqual(O.bonus_charge(new, y), O.bonus_charge(k, y) + amount / n)
            self.assertLessEqual(O.cap_hit(new, y), O.cap_hit(k, y))   # restructuring never raises this year's cap hit
            total_before = sum(O.cap_hit(k, yy) for yy in range(k.start_year, k.start_year + len(k.years)))
            total_after = sum(O.cap_hit(new, yy) for yy in range(k.start_year, k.start_year + len(k.years)))
            self.assertEqual(total_before, total_after)               # the same money, moved in time
            self.assertEqual(new.guaranteed, tuple(min(g, y2) for g, y2 in zip(k.guaranteed, new.years)))
        self.assertGreater(done, 1000)

    def test_the_rookie_contract_is_four_fixed_years_at_the_scale_with_a_first_round_option(self):
        for rnd in range(1, 8):
            k = rookie_contract(2001, rnd)
            self.assertEqual((k.years, k.guaranteed, k.bonuses, k.start_week), ((F(O.ROOKIE_SCALE[rnd - 1]),) * 4, (0,) * 4, (), 0))
        k = rookie_contract(2001, 1, first_round_option=True)
        self.assertEqual(k.years, (F(25),) * 4 + (F(35),))
        self.assertEqual(rookie_contract(2001, 3, first_round_option=True).years, (F(14),) * 4)         # only round one has the option


# ------------------------------------------------------------------------------------------------------------- the long random run
class Run:
    """One seeded multi-year scenario on a small league, checked against the model after every call."""

    def __init__(self, seed, clubs=6, years=3, drafted=True):
        self.rng = random.Random(seed)
        self.cids = [f"C{i}" for i in range(clubs)]
        self.L = Ledger(self.cids, 2001)
        if drafted:
            self.L.draft_completed = self.L.year
        self.years = years
        self.calls = {}
        self.rejected = {}
        self.problems = []
        self.accepted_cuts = 0
        self.gameday_problems = []
        self.cut_failures = []

    # ---- bookkeeping
    def note(self, kind, ok):
        self.calls[kind] = self.calls.get(kind, 0) + 1
        if not ok:
            self.rejected[kind] = self.rejected.get(kind, 0) + 1

    def check_state(self, label):
        L = self.L
        for problem in O.structural_problems(L) + O.money_problems(L):
            self.problems.append(f"after {label} (year {L.year} week {L.week} {L.phase}): {problem}")

    def attempt(self, kind, clubs, fn, cap_rule, viability_rule=True):
        """Run one ledger call and apply every check. Returns the call's result, or None if the ledger rejected it."""
        L = self.L
        before = {c: (O.cap_number(L, c), O.committed(L, c)) for c in clubs}
        snap = state_digest(L)
        try:
            out = fn()
            ok = True
        except Rejected:
            out, ok = None, False
        self.note(kind, ok)
        self.ok = ok
        label = f"{kind}{'' if ok else ' (rejected)'}"
        if not ok:
            if state_digest(L) != snap:
                self.problems.append(f"{label}: a rejected call changed the ledger")
        else:
            for c in clubs:
                cap0, com0 = before[c]
                if cap_rule:
                    cap1 = O.cap_number(L, c)
                    if cap1 > L.cap and cap1 > cap0:
                        self.problems.append(f"{label}: {c} left over the cap ({cap1}) with a higher number than before ({cap0})")
                if viability_rule:
                    com1 = O.committed(L, c)
                    for y, v in com1.items():
                        if v > O.limit(L, y) and v > com0.get(y, F(0)):
                            self.problems.append(f"{label}: {c} {y} committed {v} over the limit {O.limit(L, y)} and above {com0.get(y, 0)}")
        self.check_state(label)
        return out

    # ---- the cast
    def populate(self):
        L, rng = self.L, self.rng
        for cid in self.cids:
            for pos, n in dict(MINIMUMS, HB=3).items():
                for _ in range(n + rng.randint(0, 2)):
                    p = L.new_player(pos, rng.randint(40, 90), age=rng.randint(21, 33), seasons=rng.randint(0, 7))
                    self.try_sign(cid, p.pid, "roster" if rng.random() < 0.8 else "ps", quiet=True, contract=self.cheap_contract())
        for _ in range(140):
            L.new_player(rng.choice(POSITIONS), rng.randint(35, 80), age=rng.randint(21, 34), seasons=rng.randint(0, 7))

    def cheap_contract(self):
        rng, L = self.rng, self.L
        n = 1 if L.week > 0 else rng.randint(1, 4)
        base = rng.choice((5, 5, 5, 6, 8, 10, 14, 20))
        step = rng.randint(0, 2)
        ys = tuple(F(base) + F(i) * step for i in range(n))
        gs = tuple(rng.choice((0, 0, y / 2)) for y in ys)
        bonuses = ((F(rng.choice((4, 8, 12))), 0, rng.randint(1, n)),) if rng.random() < 0.25 else ()
        return Contract(L.year, ys, gs, bonuses, L.week, "rising" if step and n > 1 else "steady")

    def contract(self):
        return random_contract(self.rng, self.L.year, self.L.week, one_year=self.L.week > 0)       # a midseason deal expires at year close

    def try_sign(self, cid, pid, slot, quiet=False, contract=None):
        L = self.L
        k = contract or (Contract(L.year, (5,), (), (), L.week) if self.rng.random() < 0.3 else self.contract())
        if quiet:
            try:
                L.sign(cid, pid, k, slot)
            except Rejected:
                pass
            return
        return self.attempt("sign", [cid], lambda: L.sign(cid, pid, k, slot), cap_rule=True)

    # ---- the random moves
    def pick_club(self):
        return self.rng.choice(self.cids)

    def players_of(self, cid, slots=("roster", "ps", "ir")):
        c = self.L.clubs[cid]
        return [pid for s in slots for pid in getattr(c, s)]

    def move(self):
        L, rng = self.L, self.rng
        kind = rng.choices(("sign", "release", "trade", "restructure", "ir", "ir_return", "promote", "waivers", "injure", "gameday", "bad"),
                           (22, 14, 12, 8, 6, 5, 6, 6, 8, 6, 5))[0]
        cid = self.pick_club()
        if kind == "sign":
            fas = [p for p in L.players.values() if p.where == "fa"]
            if fas:
                p = rng.choice(fas)
                self.try_sign(cid, p.pid, rng.choice(("roster", "ps", "roster")))
        elif kind == "release":
            pool = self.players_of(cid)
            if pool:
                pid = rng.choice(pool)
                self.attempt("release", [cid], lambda: L.release(cid, pid, split=rng.random() < 0.25), cap_rule=False)
        elif kind == "trade":
            b = rng.choice([c for c in self.cids if c != cid])
            ga = rng.sample(self.players_of(cid, ("roster", "ir")), min(rng.randint(0, 2), len(self.players_of(cid, ("roster", "ir")))))
            gb = rng.sample(self.players_of(b, ("roster", "ir")), min(rng.randint(0, 2), len(self.players_of(b, ("roster", "ir")))))
            pa = rng.sample(sorted(L.clubs[cid].picks), rng.randint(0, 2)) if rng.random() < 0.5 else []
            pb = rng.sample(sorted(L.clubs[b].picks), rng.randint(0, 2)) if rng.random() < 0.5 else []
            if rng.random() < 0.1 and pb:
                pa = pa + [pb[0]]                         # a pick the club does not own
            if ga or gb or pa or pb:
                self.attempt("trade", [cid, b], lambda: L.trade(cid, ga, b, gb, pa, pb), cap_rule=True)
        elif kind == "restructure":
            pool = self.players_of(cid, ("roster", "ir"))
            if pool:
                pid = rng.choice(pool)
                k = L.players[pid].contract
                i = L.year - k.start_year
                amount = F(rng.randint(1, 60), rng.choice((1, 2, 4)))
                self.attempt("restructure", [cid], lambda: L.restructure(cid, pid, amount), cap_rule=True)
        elif kind == "ir":
            pool = self.players_of(cid, ("roster",))
            hurt = [i for i in pool if L.players[i].out_weeks >= 4]
            if pool:
                pid = rng.choice(hurt if hurt and rng.random() < 0.8 else pool)
                self.attempt("ir", [cid], lambda: L.place_on_ir(cid, pid), cap_rule=False, viability_rule=False)
        elif kind == "ir_return":
            pool = self.players_of(cid, ("ir",))
            if pool:
                self.attempt("ir_return", [cid], lambda: L.return_from_ir(cid, rng.choice(pool)), cap_rule=False, viability_rule=False)
        elif kind == "promote":
            pool = self.players_of(cid, ("ps",))
            if pool:
                self.attempt("promote", [cid], lambda: L.promote(cid, rng.choice(pool)), cap_rule=False, viability_rule=False)
        elif kind == "waivers":
            order = list(self.cids)
            rng.shuffle(order)
            self.attempt("waivers", list(self.cids), lambda: L.resolve_waivers(order, wants=lambda c, p: rng.random() < 0.5), cap_rule=True)
        elif kind == "injure":
            pool = self.players_of(cid, ("roster", "ps", "ir"))
            for pid in rng.sample(pool, min(3, len(pool))):
                L.players[pid].out_weeks = rng.choice((0, 0, 1, 3, 4, 6, 9))
            self.check_state("injure")
        elif kind == "gameday":
            if L.phase == "season":
                gd = gameday.build(L, cid)
                self.note("gameday", True)
                if len(gd.elevated) > 2 or any(L.players[i].elevations > 3 for i in gd.elevated):
                    self.problems.append(f"gameday: {len(gd.elevated)} elevated")
                if len(gd.active) > gd.limit or not set(gd.elevated) <= set(gd.active):
                    self.gameday_problems.append(f"{cid}: {len(gd.active)} active (limit {gd.limit}), {len(gd.elevated)} elevated, elevated all dressed: {set(gd.elevated) <= set(gd.active)}")
                self.check_state("gameday")
        elif kind == "bad":                               # calls a league would never make
            other = rng.choice(self.cids)
            pool = self.players_of(other)
            if pool:
                pid = rng.choice(pool)
                who = cid if cid != other else rng.choice([c for c in self.cids if c != other])
                self.attempt("release_wrong_club", [who], lambda: L.release(who, pid), cap_rule=False)
                self.attempt("sign_not_free", [who], lambda: L.sign(who, pid, Contract(L.year, (5,), (), (), L.week)), cap_rule=True)

    # ---- the year
    def repair_and_cut(self, cid):
        """What an autopilot does at the final cut: get to 53 and 16, under the cap, with every position covered. Returns True if the cut passed."""
        L, rng = self.L, self.rng
        for _ in range(60):
            try:
                self.attempt("cut_down", [cid], lambda: L.cut_down(cid), cap_rule=False, viability_rule=False)
                if self.calls.get("cut_down", 0) and self.last_cut_ok(cid):
                    return True
            except Exception:
                raise
            c = L.clubs[cid]
            if len(c.roster) > 53 or len(c.ps) > 16 or L.overage(cid) > 0:
                pool = sorted(self.players_of(cid, ("roster", "ps")), key=lambda i: (-O.cap_hit(L.players[i].contract, L.year), i)) if L.overage(cid) > 0 else \
                    sorted(self.players_of(cid, ("roster", "ps")), key=lambda i: (L.players[i].ovr, i))
                if len(c.roster) > 53 and L.overage(cid) <= 0:
                    pool = [i for i in sorted(c.roster, key=lambda i: (L.players[i].ovr, i))]
                if len(c.ps) > 16 and L.overage(cid) <= 0 and len(c.roster) <= 53:
                    pool = sorted(c.ps, key=lambda i: (L.players[i].ovr, i))
                if pool:
                    self.attempt("release", [cid], lambda: L.release(cid, pool[0]), cap_rule=False)
                    continue
            counts = {}
            for pid in c.roster:
                p = L.players[pid]
                if p.out_weeks == 0:
                    counts[p.pos] = counts.get(p.pos, 0) + 1
            from dfl.ledger.positions import shortfalls
            short = shortfalls(counts)
            if short:
                pos = next(iter(short))
                fa = next((p for p in L.players.values() if p.where == "fa" and p.pos == ("HB" if pos == "HB" else pos)), None)
                if fa is None:
                    fa = L.new_player("HB" if pos == "HB" else pos, 55)
                self.try_sign(cid, fa.pid, "roster", contract=Contract(L.year, (5,), (), (), L.week))
                continue
            for pid in c.roster:
                L.players[pid].out_weeks = 0
        return False

    def last_cut_ok(self, cid):
        return self._cut_ok

    def autocut(self, cid):
        """Deterministic final cut: heal, fill every required place from the pool, keep the required places plus the best of the rest up to 53."""
        L, c = self.L, self.L.clubs[cid]
        need = dict(MINIMUMS, HB=3)
        for pid in c.all_pids():
            L.players[pid].out_weeks = 0
        for pos, n in need.items():
            have = [i for i in c.roster if L.players[i].pos == pos]
            for _ in range(max(0, n - len(have))):
                cands = [p for p in sorted(L.players.values(), key=lambda p: p.pid) if p.where == "fa" and p.pos == pos][:8]
                cands.append(L.new_player(pos, 50))
                for fa in cands:
                    self.try_sign(cid, fa.pid, "roster", contract=Contract(L.year, (5,), (), (), L.week))
                    if self.ok:
                        break
        keep = set()
        for pos, n in need.items():
            keep.update(sorted((i for i in c.roster if L.players[i].pos == pos), key=lambda i: (-L.players[i].ovr, i))[:n])
        rest = sorted((i for i in c.roster if i not in keep), key=lambda i: (-L.players[i].ovr, i))
        keep.update(rest[:max(0, 53 - len(keep))])
        for pid in [i for i in c.roster if i not in keep]:
            self.attempt("release", [cid], lambda: L.release(cid, pid), cap_rule=False)
        vets = sorted((i for i in c.ps if L.players[i].veteran), key=lambda i: (L.players[i].ovr, i))
        for pid in vets[:max(0, len(vets) - 6)]:
            self.attempt("release", [cid], lambda: L.release(cid, pid), cap_rule=False)
        for pid in sorted(c.ps, key=lambda i: (L.players[i].ovr, i))[:max(0, len(c.ps) - 16)]:
            self.attempt("release", [cid], lambda: L.release(cid, pid), cap_rule=False)

    def cut_all(self):
        L = self.L
        ok = True
        for cid in self.cids:
            done = False
            self.autocut(cid)
            for _ in range(60):
                try:
                    L.cut_down(cid)
                    done = True
                    self.accepted_cuts += 1
                    break
                except Rejected as e:
                    last = str(e)
                done = self._fix_for_cut(cid)
            if not done:
                self.cut_failures.append(last)
            ok &= done
        self.check_state("cut_down")
        return ok

    def surplus_victim(self, cid):
        """The weakest healthy roster player at a position the club holds more of than the required minimum."""
        L, c = self.L, self.L.clubs[cid]
        need = dict(MINIMUMS, HB=3)
        counts = {}
        for pid in c.roster:
            if L.players[pid].out_weeks == 0:
                counts[L.players[pid].pos] = counts.get(L.players[pid].pos, 0) + 1
        pool = [i for i in sorted(c.roster, key=lambda i: (L.players[i].ovr, i)) if counts.get(L.players[i].pos, 0) > need.get(L.players[i].pos, 0)]
        return pool[0] if pool else None

    def _fix_for_cut(self, cid):
        L, rng = self.L, self.rng
        c = L.clubs[cid]
        from dfl.ledger.positions import shortfalls
        if L.overage(cid) > 0:
            pool = sorted(self.players_of(cid, ("roster", "ps", "ir")), key=lambda i: (-O.cap_hit(L.players[i].contract, L.year), i))
            self.attempt("release", [cid], lambda: L.release(cid, pool[0]), cap_rule=False)
            return False
        if len(c.roster) > 53:
            pid = self.surplus_victim(cid) or sorted(c.roster, key=lambda i: (L.players[i].ovr, i))[0]
            self.attempt("release", [cid], lambda: L.release(cid, pid), cap_rule=False)
            return False
        if len(c.ps) > 16 or L.ps_veterans(cid) > 6:
            pool = sorted(c.ps, key=lambda i: (not L.players[i].veteran, L.players[i].ovr, i))
            self.attempt("release", [cid], lambda: L.release(cid, pool[0]), cap_rule=False)
            return False
        counts = {}
        for pid in c.roster:
            if L.players[pid].out_weeks == 0:
                counts[L.players[pid].pos] = counts.get(L.players[pid].pos, 0) + 1
        short = shortfalls(counts)
        if short:
            pos = next(iter(short))
            want = "HB" if pos == "HB" else pos
            fa = next((p for p in L.players.values() if p.where == "fa" and p.pos == want and p.out_weeks == 0), None) or L.new_player(want, 55)
            if len(c.roster) >= 53:
                victim = self.surplus_victim(cid)
                if victim:
                    self.attempt("release", [cid], lambda: L.release(cid, victim), cap_rule=False)
            self.try_sign(cid, fa.pid, "roster", contract=Contract(L.year, (5,), (), (), L.week))
            return False
        for pid in c.roster:                              # numbers, or an injury the cut cannot see through: heal them
            L.players[pid].out_weeks = 0
        return False

    def one_year(self):
        L, rng = self.L, self.rng
        for _ in range(60):
            self.move()
        L.open_trading()
        for _ in range(60):
            self.move()
        if rng.random() < 0.7:
            L.complete_draft()
            self.check_state("complete_draft")
        L.start_camp()
        for _ in range(80):
            self.move()
        started = False
        if self.cut_all():
            try:
                L.begin_season()
                started = True
            except Rejected:
                pass
        self.check_state("begin_season")
        if started:
            for _ in range(22):
                L.advance_week()
                self.check_state("advance_week")
                for _ in range(10):
                    self.move()
            L.end_season()
            self.check_state("end_season")
            for _ in range(10):
                self.move()
        L.resolve_waivers(list(self.cids), wants=lambda c, p: False)       # the world clears the waiver wire before the year turns (world.py:652)
        L.new_year()
        for _ in range(60):
            L.new_player(rng.choice(POSITIONS), rng.randint(35, 80), age=rng.randint(21, 34), seasons=rng.randint(0, 7))
        self.check_state("new_year")
        return started

    def run(self):
        self.populate()
        self.check_state("populate")
        seasons_played = 0
        for _ in range(self.years):
            seasons_played += self.one_year()
        return seasons_played


class RandomSequences(unittest.TestCase):
    def run_seed(self, seed, **kw):
        r = Run(seed, **kw)
        played = r.run()
        self.assertEqual(r.problems[:5], [], f"seed {seed}: {len(r.problems)} disagreements; first shown")
        self.assertEqual(r.gameday_problems[:3], [], f"seed {seed}: game-day lists broke the active limit or left an elevated player undressed")
        return r, played

    def test_three_seeds_three_years_each(self):
        totals, seasons = {}, 0
        for seed in (101, 202, 303):
            r, played = self.run_seed(seed)
            for k, v in r.calls.items():
                totals[k] = totals.get(k, 0) + v
            self.assertGreaterEqual(played, 1, f"seed {seed}: the scenario never reached a season, so most rules went untested")
            seasons += played
        self.assertGreaterEqual(seasons, 4)               # a club that cannot fill its required places under the cap simply does not start: legitimate, but rare
        # the run must really have exercised the rules, not just been rejected throughout
        self.assertGreater(totals.get("sign", 0), 600)
        self.assertGreater(totals.get("release", 0), 300)
        self.assertGreater(totals.get("trade", 0), 100)
        self.assertGreater(totals.get("restructure", 0), 60)

    def test_with_the_draft_unmade_so_the_rookie_reserve_is_counted(self):
        r, played = self.run_seed(404, drafted=False, years=2)


if __name__ == "__main__":
    unittest.main()
