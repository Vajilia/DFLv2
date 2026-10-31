"""Tiaras and the halls (Edition 8.30, section 15).

A **Tiara** goes to the Coronation winner's final 53 and its CEO, general manager, head coach and three coordinators; one per person
per season. A player is **eligible** for the halls with a Tiara and five credited seasons (a season with an active-list or Tour
appearance counts once). There are 59 halls: the league, two conferences, eight divisions and 48 clubs. Each holds an **All-Time
All-22** (one register per position slot: offence QB, HB, TE, three WR, five linemen by position; defence LE, RE, two DT, three
linebackers by position, two CB, FS, SS) and K, P and LS registers. A player's value in a hall is the sum of her season scores
(analytics.py) in seasons she was at a club inside it. Membership of a register is replaceable (a challenger equal or better
displaces the lowest holder; a tie goes against the incumbent, between challengers by a seeded draw); **ever-membership is
permanent**. Each year one **fan entrant** joins the league hall by single-winner ranked choice: the ballots are the 48 clubs' three
fan viewpoints, the candidates the best eligible players not yet in the league hall (the builder's reading of "runners-up").
Staff hold Tiaras but have no halls yet. Nothing here reaches a game. Numbers are data (data/analytics).
"""
from ..characters.creator import rng_for
from ..engine.production import tables
from .analytics import SLOTS
from .rules import ROSTER_MAX

LEAGUE = ("league",)
LEADER_ROLES = ("ceo", "gm", "hc", "oc", "dc", "stc")


def scopes(club, division_of):
    conf, div = division_of[club]
    return [LEAGUE, ("conference", conf), ("division", conf, div), ("club", club)]


def tiara_set(world, winner):
    """The exact Tiara set (section 15): the winning club's final 53 (its active roster; injured reserve and the practice squad are not
    in the 53) plus its CEO, general manager, head coach and three coordinators."""
    club = world.L.clubs[winner]
    assert len(club.roster) <= ROSTER_MAX, f"{winner} has more than 53 on its roster"
    got = set(club.roster)
    got |= {c.identity.id for r, c in world.leaders[winner].items() if r in LEADER_ROLES}
    return got


def award_tiara(world, year, winner):
    """Give the Coronation winner's final 53 and six leaders their Tiara for `year`. Idempotent: a second call for the same year and winner
    changes nothing, and the `tiaras` counter counts only Tiaras newly given. Returns the whole eligible set."""
    got = tiara_set(world, winner)
    new = 0
    for pid in got:
        years = world.tiaras.setdefault(pid, set())
        if year not in years:
            years.add(year)
            new += 1
    world.stats["tiaras"] = world.stats.get("tiaras", 0) + new
    return got


def award_rings(world, year, conf, staff_club, ready=None):
    """Tour Rings (spec section 7, Edition 8.58): the winning Bowl side's final 53 who were present and medically ready AT KICKOFF, and the
    four Bowl coaches (head coach and three coordinators of the conference's best Tour club). `ready` is the list of pids found ready at
    kickoff (bowl.ready_at_kickoff, evaluated just before the game); if it is not given it is evaluated now. A locked player who is not ready
    at kickoff gets no ring. Idempotent per year. Returns the set of card ids."""
    from . import bowl
    if ready is None:
        ready = bowl.ready_at_kickoff(world, conf)
    got = set(ready) & set(world.bowl_final[conf]["roster"])
    got |= {c.identity.id for r, c in world.leaders[staff_club].items() if r in ("hc", "oc", "dc", "stc") and c is not None}
    new = 0
    for pid in sorted(got):
        years = world.rings.setdefault(pid, set())
        if year not in years:
            years.add(year)
            new += 1
    world.stats["rings"] = world.stats.get("rings", 0) + new
    return got


def eligible(world, pid):
    return bool(world.tiaras.get(pid)) and len(world.credited.get(pid, ())) >= int(tables()["a"]["hall_min_seasons"])


def _value(world, pid, scope, division_of):
    """Sum of her season scores at clubs inside the scope."""
    tot, seen = 0.0, False
    for r in world.career.get(pid, {}).values():
        club = r.get("club")
        if club is None or "score" not in r:
            continue
        if scope in scopes(club, division_of):
            tot += r["score"]
            seen = True
    return tot if seen else None


def update(world, year, division_of):
    """Re-seat every register after a season. Returns the log entries (entered / left) of this update."""
    hall = world.hall
    log = []
    elig = [pid for pid in world.career if eligible(world, pid)]
    all_scopes = set()
    for pid in elig:
        for r in world.career[pid].values():
            if r.get("club") in division_of and "score" in r:
                all_scopes.update(scopes(r["club"], division_of))
    for scope in sorted(all_scopes):
        by_pos = {}
        for pid in elig:
            pos = world.persona[pid][1]
            if pos not in SLOTS:
                continue
            v = _value(world, pid, scope, division_of)
            if v is not None:
                by_pos.setdefault(pos, []).append((pid, v))
        reg = hall["reg"].setdefault(scope, {})
        for pos, cands in by_pos.items():
            held = set(reg.get(pos, ()))
            # equal or better displaces: sort by value, then challengers before incumbents, then a seeded draw
            cands.sort(key=lambda pv: (-pv[1], pv[0] in held, rng_for(world.creator.seed, "hall", year, scope, pos, pv[0]).random()))
            new = [pid for pid, _ in cands[:SLOTS[pos]]]
            for pid in new:
                if pid not in held:
                    hall["ever"].setdefault(scope, set()).add(pid)
                    log.append({"year": year, "scope": scope, "pos": pos, "pid": pid, "kind": "entered"})
            for pid in held - set(new):
                log.append({"year": year, "scope": scope, "pos": pos, "pid": pid, "kind": "displaced"})
            reg[pos] = new
    hall["log"].extend(log)
    return log


def fan_entrant(world, year, clubs):
    """One league-wide fan entrant by single-winner ranked choice (instant runoff, seeded ties). Returns her id or None."""
    A, hall = tables()["a"], world.hall
    pool = []
    for pid in world.career:
        if eligible(world, pid) and pid not in hall["ever"].get(LEAGUE, set()) and pid not in hall["fan"]:
            tot = sum(r["score"] for r in world.career[pid].values() if "score" in r)
            pool.append((tot, pid))
    pool.sort(key=lambda x: (-x[0], x[1]))
    pool = pool[:int(A["fan_candidates"])]
    if not pool:
        return None
    vals = [t for t, _ in pool]
    mean = sum(vals) / len(vals)
    sd = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5 or 1.0
    ballots = []
    for club in clubs:
        for view in range(3):
            rng = rng_for(world.creator.seed, "fanballot", year, club, view)
            scored = []
            for tot, pid in pool:
                played = any(r.get("club") == club for r in world.career[pid].values())
                scored.append((-((tot - mean) / sd + A["fan_noise"] * rng.gauss(0, 1) + (A["fan_loyalty"] if played else 0.0)), pid))
            scored.sort()
            ballots.append([pid for _, pid in scored])
    alive = {pid for _, pid in pool}
    rng = rng_for(world.creator.seed, "fanrunoff", year)
    while True:
        tally = {pid: 0 for pid in alive}
        for b in ballots:
            for pid in b:
                if pid in alive:
                    tally[pid] += 1
                    break
        if 2 * max(tally.values()) > len(ballots) or len(alive) == 1:
            win = max(sorted(tally), key=lambda p: (tally[p], rng_for(world.creator.seed, "fanwin", year, p).random()))
            break
        low = min(tally.values())
        out = sorted(p for p in alive if tally[p] == low)
        alive.discard(out[rng.randrange(len(out))])
    hall["fan"].append(win)
    hall["ever"].setdefault(LEAGUE, set()).add(win)
    hall["log"].append({"year": year, "scope": LEAGUE, "pos": world.persona[win][1], "pid": win, "kind": "fan"})
    return win
