"""Game day (section 11): the active list, practice-squad elevations, short positions, emergency lists, forfeit test."""
from dataclasses import dataclass, field

from . import rules as R
from .positions import MINIMUMS, OL, POSITIONS, shortfalls

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
    emergency: dict = field(default_factory=dict) # EVERY position -> ordered pids (at most EMERGENCY_NAMES) of dressed, medically available
                                                  # players of other positions who would play there, out of position, if nobody is left
    needed: list = field(default_factory=list)    # positions with nobody dressed at kickoff (the emergency list is in use from the start)
    can_field: bool = True


EMERGENCY_NAMES = 4        # names filed per position (the spec asks for at least one)


def emergency_list(active, est):
    """{position: tuple of pids} for EVERY position. Each name is a dressed player (on the game-day list) who is medically available
    and whose permanent position is a different one; she keeps her official position (section 11: official position is permanent) and
    would only be assigned to this one for the game, at the moment nobody at the position is left.
    Order: neighbouring positions first, in the NEIGHBOURS order, then the rest; best-rated first within a position; ties by player id.
    The first names are chosen to be different players for different positions where the squad allows it, so that two empty positions
    do not both call on the same player; whoever resolves a list in a game must still skip a player already on the field
    (engine/squad.py does), which is what guarantees nobody holds two positions at once."""
    dressed = [p for p in active if p.out_weeks == 0]
    out, first = {}, set()
    for pos in POSITIONS:
        near = NEIGHBOURS.get(pos, ())
        ranked = sorted((p for p in dressed if p.pos != pos),
                        key=lambda q: (near.index(q.pos) if q.pos in near else len(near), -est(q), q.pid))
        pick = next((p for p in ranked if p.pid not in first), None) or (ranked[0] if ranked else None)
        if pick is None:
            out[pos] = ()
            continue
        first.add(pick.pid)
        out[pos] = (pick.pid,) + tuple(p.pid for p in ranked if p.pid != pick.pid)[:EMERGENCY_NAMES - 1]
    return out


def build(ledger, cid, elevate=True, judge=None):
    """Choose the active list for one game for `cid`. Elevations are counted against the players. Nothing is signed.
    `judge(player)` is how good her coaches think a player is (Edition 8.36); without it the true overall is used."""
    est = judge or (lambda q: q.ovr)
    c = ledger.clubs[cid]
    P = ledger.players
    healthy = [p for p in (P[i] for i in c.roster) if p.out_weeks == 0]
    by_pos = {}
    for p in sorted(healthy, key=lambda q: (-est(q), q.pid)):
        by_pos.setdefault(p.pos, []).append(p)
    chosen = []
    # 1. league minimums first, best players at each position
    for pos, n in MINIMUMS.items():
        chosen += by_pos.get(pos, [])[:n]
    hb, fb = by_pos.get("HB", []), by_pos.get("FB", [])
    chosen += hb[:3] if len(hb) >= 3 or not fb else hb[:2] + fb[:1]
    # 2. fill to the limit with the best of the rest
    ids = {p.pid for p in chosen}
    rest = [p for p in sorted(healthy, key=lambda q: (-est(q), q.pid)) if p.pid not in ids]

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
        pool.sort(key=lambda q: (-est(q), q.pid))
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
    # 4. the emergency list (section 11): filed before kickoff with coverage for every position, from dressed, medically available players
    gd.emergency = emergency_list(active, est)
    gd.needed = [pos for pos in MINIMUMS if counts.get(pos, 0) == 0]
    if counts.get("HB", 0) + counts.get("FB", 0) == 0 and "HB" not in gd.needed:
        gd.needed.append("HB")
    gd.can_field = len(active) >= 11
    return gd
