"""An independent model of the ledger's money and lists, written from the Design Document (sections 0, 11, 12) and not from the ledger's code.

Nothing here calls `Contract.cap_hit`, `release_charges`, `Ledger.cap_number`, `committed`, `limit`, `viability_margin` or the ledger's own
`audit`. It reads only the raw fields (a contract's start year and week, its base salaries, guarantees and bonuses; a club's lists, dead
money and picks; a player's list and number) and recomputes everything else with exact fractions. A test that compares the two is therefore a
comparison of two readings of the document, and where they differ one of them is wrong.

Readings of the document where the text allows two, each marked READING in the code, so a reviewer can see which one the model takes.
"""
from fractions import Fraction as F

# Section 0 and 11 numbers, written out here from the document (tests/test_step7_spec_constants.py proves they equal the document's text).
PAID_WEEKS = 22
CAP, RESERVE, MIN_SALARY = F(1000), F(25), F(5)
REQUIRED_PLACES = 48
ROSTER, SQUAD, CEILING, COUNTED_BEFORE_CUT = 53, 16, 90, 69
ROOKIE_SCALE = (25, 18, 14, 11, 9, 7, 5)


# ------------------------------------------------------------------------------------------------------------- contracts
def n_years(c):
    return len(c.years)


def share(c, i):
    """Part of year i's annual rate the contract pays: a deal signed after `start_week` paid weeks pays the remaining weeks of its first year."""
    return F(PAID_WEEKS - c.start_week, PAID_WEEKS) if i == 0 else F(1)


def base_charge(c, year):
    i = year - c.start_year
    return F(c.years[i]) * share(c, i) if 0 <= i < n_years(c) else F(0)


def guarantee(c, year):
    i = year - c.start_year
    return F(c.guaranteed[i]) * share(c, i) if 0 <= i < n_years(c) else F(0)


def bonus_charge(c, year):
    """A signing bonus is paid at signing and cap-prorated evenly over its proration years, from the contract year it names."""
    i = year - c.start_year
    return sum((F(a) / p for a, j, p in c.bonuses if j <= i < j + p), F(0))


def cap_hit(c, year):
    return base_charge(c, year) + bonus_charge(c, year)


def uncharged_bonus(c, year):
    """Every bonus portion not yet charged when the year `year` is under way: this year's portion and all later ones."""
    i = year - c.start_year
    total = F(0)
    for a, j, p in c.bonuses:
        first = max(i, j)
        portions = max(0, j + p - first)
        total += F(a) * portions / p
    return total


def dead_if_released(c, year, week, split=False, keep_guarantees=True):
    """{year: dead money} if the player is released in `year` after `week` paid weeks.
    Earned base stays this year (guaranteed base in full); guarantees of later years stay in those years; every uncharged bonus lands on this
    year. READING A (the ledger's): with `split`, this year's own bonus portion stays this year and the later portions divide 50/50 between
    this year and the next. READING B: the whole uncharged bonus, this year's portion included, divides 50/50. They differ by half of this
    year's portion; the document does not say which."""
    i = year - c.start_year
    assert 0 <= i < n_years(c)
    since = week - (c.start_week if i == 0 else 0)
    earned = F(c.years[i]) * max(0, since) / PAID_WEEKS
    out = {year: max(earned, guarantee(c, year)) if keep_guarantees else earned}
    if keep_guarantees:
        for j in range(i + 1, n_years(c)):
            if c.guaranteed[j]:
                out[c.start_year + j] = F(c.guaranteed[j])
    now, later = bonus_charge(c, year), uncharged_bonus(c, year) - bonus_charge(c, year)
    if split and later:
        out[year] += now + later / 2
        out[year + 1] = out.get(year + 1, F(0)) + later / 2
    else:
        out[year] += now + later
    return {y: v for y, v in out.items() if v}


# ------------------------------------------------------------------------------------------------------------- clubs
def contracts(L, cid):
    c = L.clubs[cid]
    return [L.players[pid].contract for pid in list(c.roster) + list(c.ps) + list(c.ir)]


def rookie_reserve(L, cid):
    """Until a draft pick is made its rookie-scale hit counts in the owning club's cap number for the year of that draft (rounds 8, 9 at the
    round-7 scale). Not counted once that year's draft is complete."""
    if L.draft_completed >= L.year:
        return []
    return sorted((F(ROOKIE_SCALE[min(pk[1], 7) - 1]) for pk in L.clubs[cid].picks if pk[0] == L.year), reverse=True)


def cap_number(L, cid, year=None):
    """Cap timing: dead money plus the club's highest cap hits, only the top 69 of them from the start of the league year until the final cut
    (the extra camp bodies do not count); every contract after the final cut. READING: the unmade-pick reserve is one of the hits."""
    year = L.year if year is None else year
    hits = [cap_hit(k, year) for k in contracts(L, cid)]
    if year == L.year:
        hits += rookie_reserve(L, cid)
        if L.phase in ("offseason", "camp"):
            hits = sorted(hits, reverse=True)[:COUNTED_BEFORE_CUT]
    return sum(hits, F(0)) + F(L.clubs[cid].dead.get(year, 0))


def committed(L, cid):
    """{year: what the club would owe in that year if every contract were released at the start of it (now, for this year)}, plus dead money
    already booked. Strict reading of the viability invariant."""
    out = {}
    for y, v in L.clubs[cid].dead.items():
        if y >= L.year:
            out[y] = out.get(y, F(0)) + F(v)
    for k in contracts(L, cid):
        for y in range(L.year, k.start_year + n_years(k)):
            v = dead_if_released(k, y, L.week if y == L.year else 0).get(y, F(0))
            if v:
                out[y] = out.get(y, F(0)) + v
    return out


def limit(L, year):
    """Dead money + required-minimum roster cost + reserve <= cap. In the current year the replacement cost is pro-rated to the paid weeks to come."""
    repl = REQUIRED_PLACES * MIN_SALARY
    if year == L.year:
        repl = repl * F(PAID_WEEKS - L.week, PAID_WEEKS)
    return L.cap - RESERVE - repl


# ------------------------------------------------------------------------------------------------------------- lists and numbers
PRIMARY = {"K": (1, 9), "P": (1, 9), "LS": (1, 9), "QB": (10, 14), "TE": (15, 19), "CB": (20, 29), "HB": (30, 39), "FB": (30, 39),
           "FS": (40, 49), "SS": (40, 49), "ROLB": (50, 59), "MLB": (50, 59), "LOLB": (50, 59), "LT": (60, 79), "LG": (60, 79),
           "C": (60, 79), "RG": (60, 79), "RT": (60, 79), "WR": (80, 89), "LE": (90, 99), "RE": (90, 99), "DT": (90, 99)}
OVERFLOW = {"WR": (15, 19), "TE": (80, 89), "ROLB": (40, 49), "MLB": (40, 49), "LOLB": (40, 49), "LE": (70, 79), "RE": (70, 79), "DT": (70, 79)}


def number_legal_in_season(pos, n):
    lo, hi = PRIMARY[pos]
    if lo <= n <= hi:
        return True
    o = OVERFLOW.get(pos)
    return bool(o and o[0] <= n <= o[1])


def structural_problems(L):
    """Everything that must be true of the lists at any instant, found by walking raw state. Returns a list of strings (empty = sound)."""
    bad = []
    seen = {}
    for cid, c in L.clubs.items():
        for slot in ("roster", "ps", "ir"):
            for pid in getattr(c, slot):
                if pid in seen:
                    bad.append(f"player {pid} is on two lists: {seen[pid]} and {(cid, slot)}")
                seen[pid] = (cid, slot)
                p = L.players.get(pid)
                if p is None:
                    bad.append(f"{cid}.{slot} lists unknown player {pid}")
                    continue
                if (p.club, p.where) != (cid, slot):
                    bad.append(f"player {pid} is on {cid}.{slot} but says {(p.club, p.where)}")
                if p.contract is None:
                    bad.append(f"player {pid} on {cid}.{slot} has no contract")
                else:
                    k = p.contract
                    if not (k.start_year <= L.year < k.start_year + n_years(k)):
                        bad.append(f"player {pid} on {cid}.{slot} has a contract that does not cover {L.year}")
                    if any(F(y) < MIN_SALARY for y in k.years):
                        bad.append(f"player {pid} has a base salary below the minimum")
                    if any(F(g) > F(y) or g < 0 for g, y in zip(k.guaranteed, k.years)):
                        bad.append(f"player {pid} has a guarantee outside 0..base")
                    if not 1 <= n_years(k) <= 5 or any(not 1 <= p_ <= 5 for _, _, p_ in k.bonuses):
                        bad.append(f"player {pid} has more than five covered or proration years")
    for pid, p in L.players.items():
        if p.where in ("roster", "ps", "ir") and pid not in seen:
            bad.append(f"player {pid} says {p.where} at {p.club} but is on no list")
        if p.where in ("fa", "retired", "waivers") and pid in seen:
            bad.append(f"player {pid} says {p.where} but is on {seen[pid]}")
        if p.where == "fa" and (p.club is not None or p.contract is not None):
            bad.append(f"free agent {pid} still holds a club or a contract")
        if p.where == "waivers" and pid not in L.waivers:
            bad.append(f"player {pid} is on waivers with no waiver record")
    for pid in L.waivers:
        if L.players[pid].where != "waivers":
            bad.append(f"waiver record for {pid} but she is {L.players[pid].where}")
    owners = {}
    for cid, c in L.clubs.items():
        for pk in c.picks:
            owners.setdefault(pk, []).append(cid)
        if any(F(v) < 0 for v in c.dead.values()):
            bad.append(f"{cid} has negative dead money {c.dead}")
        taken = {}
        for pid in list(c.roster) + list(c.ps) + list(c.ir):
            n = L.players[pid].number
            if n is None:
                bad.append(f"player {pid} at {cid} has no jersey number")
                continue
            if not 1 <= n <= 99:
                bad.append(f"player {pid} at {cid} wears {n}")
            if n in taken:
                bad.append(f"{cid}: number {n} worn by {taken[n]} and {pid}")
            taken[n] = pid
            if L.phase in ("season", "post") and not number_legal_in_season(L.players[pid].pos, n):
                bad.append(f"{cid}: player {pid} ({L.players[pid].pos}) wears {n}, outside her blocks in the season")
        if L.phase in ("season", "post"):
            if len(c.roster) > ROSTER:
                bad.append(f"{cid} has {len(c.roster)} on the roster")
            if len(c.ps) > SQUAD:
                bad.append(f"{cid} has {len(c.ps)} on the practice squad")
        elif len(c.roster) + len(c.ps) > CEILING:
            bad.append(f"{cid} has {len(c.roster) + len(c.ps)} before the final cut (ceiling {CEILING})")
        if c.ir_returns_used > 8:
            bad.append(f"{cid} designated {c.ir_returns_used} returns from injured reserve")
    for pk, who in owners.items():
        if len(who) != 1:
            bad.append(f"pick {pk} owned by {who}")
    return bad


def money_problems(L):
    """Differences between the ledger's money functions and the model's, for every club."""
    bad = []
    for cid in L.clubs:
        if L.cap_number(cid) != cap_number(L, cid):
            bad.append(f"{cid}: ledger cap number {L.cap_number(cid)} != model {cap_number(L, cid)}")
        a, b = dict(L.committed(cid)), committed(L, cid)
        if {y: v for y, v in a.items() if v} != {y: v for y, v in b.items() if v}:
            bad.append(f"{cid}: ledger commitments {a} != model {b}")
        for y in set(a) | set(b) | {L.year}:
            if L.limit(y) != limit(L, y):
                bad.append(f"limit for {y}: ledger {L.limit(y)} != model {limit(L, y)}")
    return bad
