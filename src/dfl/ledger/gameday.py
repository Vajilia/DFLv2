"""Game day (section 11): the active list, practice-squad elevations, short positions, emergency lists, forfeit test."""
from dataclasses import dataclass, field

from . import rules as R
from .positions import MINIMUMS, OL, shortfalls

OFFENCE = ("QB", "HB", "FB", "WR", "TE", "LT", "LG", "C", "RG", "RT")
DEFENCE = ("LE", "RE", "DT", "ROLB", "MLB", "LOLB", "CB", "FS", "SS")
# who covers for whom when a position has nobody left (deterministic, closest first)
NEIGHBOURS = {
    "K": ("P", "QB"), "P": ("K", "QB"), "LS": ("C", "TE"), "QB": ("WR", "HB"), "HB": ("FB", "WR"), "FB": ("HB", "TE"),
    "WR": ("HB", "TE", "CB"), "TE": ("WR", "FB", "RT"), "LT": ("LG", "RT"), "LG": ("LT", "C"), "C": ("LG", "RG"),
    "RG": ("RT", "C"), "RT": ("RG", "LT"), "LE": ("RE", "DT"), "RE": ("LE", "DT"), "DT": ("LE", "RE"),
    "ROLB": ("LOLB", "MLB"), "MLB": ("ROLB", "LOLB"), "LOLB": ("ROLB", "MLB"), "CB": ("FS", "SS"),
    "FS": ("SS", "CB"), "SS": ("FS", "CB"),
}


@dataclass
class GameDay:
    limit: int
    active: list
    elevated: list
    short: dict                                   # position -> places short of the league minimum
    emergency: dict = field(default_factory=dict) # position -> dressed pid who plays out of position when nobody is left
    can_field: bool = True


def build(ledger, cid, elevate=True):
    """Choose the active list for one game for `cid`. Elevations are counted against the players. Nothing is signed."""
    c = ledger.clubs[cid]
    P = ledger.players
    healthy = [p for p in (P[i] for i in c.roster) if p.out_weeks == 0]
    by_pos = {}
    for p in sorted(healthy, key=lambda q: (-q.ovr, q.pid)):
        by_pos.setdefault(p.pos, []).append(p)
    chosen = []
    # 1. league minimums first, best players at each position
    for pos, n in MINIMUMS.items():
        chosen += by_pos.get(pos, [])[:n]
    hb, fb = by_pos.get("HB", []), by_pos.get("FB", [])
    chosen += hb[:3] if len(hb) >= 3 or not fb else hb[:2] + fb[:1]
    # 2. fill to the limit with the best of the rest
    ids = {p.pid for p in chosen}
    rest = [p for p in sorted(healthy, key=lambda q: (-q.ovr, q.pid)) if p.pid not in ids]

    for p in rest:
        n_ol_now = sum(1 for q in chosen if q.pos in OL)
        lim = R.ACTIVE_LIMIT if n_ol_now >= R.OL_FOR_FULL else R.ACTIVE_LIMIT_FEW_OL
        if len(chosen) >= lim:
            break
        chosen.append(p)
    n_ol_now = sum(1 for q in chosen if q.pos in OL)
    limit = R.ACTIVE_LIMIT if n_ol_now >= R.OL_FOR_FULL else R.ACTIVE_LIMIT_FEW_OL
    chosen = chosen[:limit]
    # 3. elevate from the practice squad where a position is short, then where the list is short
    elevated = []
    counts = {}
    for p in chosen:
        counts[p.pos] = counts.get(p.pos, 0) + 1
    if elevate:
        pool = [p for p in (P[i] for i in c.ps) if p.out_weeks == 0 and p.elevations < R.ELEVATIONS_PER_PLAYER]
        pool.sort(key=lambda q: (-q.ovr, q.pid))
        while len(elevated) < R.ELEVATIONS_PER_GAME:
            gaps = shortfalls(counts)
            pick = None
            for pos in gaps:
                want = ("HB", "FB") if pos == "HB" else (pos,)
                pick = next((p for p in pool if p.pos in want and p not in elevated), None)
                if pick:
                    break
            if pick is None and len(chosen) + len(elevated) < limit:
                pick = next((p for p in pool if p not in elevated), None)
            if pick is None:
                break
            elevated.append(pick)
            counts[pick.pos] = counts.get(pick.pos, 0) + 1
            pick.elevations += 1
    active = chosen + elevated
    n_ol_now = sum(1 for q in active if q.pos in OL)
    if n_ol_now < R.OL_FOR_FULL and len(active) > R.ACTIVE_LIMIT_FEW_OL:
        active = active[:R.ACTIVE_LIMIT_FEW_OL]
    gd = GameDay(R.ACTIVE_LIMIT if n_ol_now >= R.OL_FOR_FULL else R.ACTIVE_LIMIT_FEW_OL,
                 [p.pid for p in active], [p.pid for p in elevated], shortfalls(counts))
    # 4. every position with nobody left gets a named emergency player from its neighbours (filed before kickoff)
    for pos in MINIMUMS:
        if counts.get(pos, 0) == 0:
            for n in NEIGHBOURS[pos]:
                cand = next((p for p in sorted(active, key=lambda q: (-q.ovr, q.pid)) if p.pos == n), None)
                if cand:
                    gd.emergency[pos] = cand.pid
                    break
            else:
                best = next(iter(sorted(active, key=lambda q: (-q.ovr, q.pid))), None)
                if best:
                    gd.emergency[pos] = best.pid
    if counts.get("HB", 0) + counts.get("FB", 0) == 0 and active:
        gd.emergency["HB"] = sorted(active, key=lambda q: (-q.ovr, q.pid))[0].pid
    gd.can_field = len(active) >= 11
    return gd
