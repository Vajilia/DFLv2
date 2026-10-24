"""Coaches (people layer, Edition 8.24): teaching, retirement, the pool and hiring.

One market serves all four coaching roles (head coach, offensive, defensive and special-teams coordinators): the same pool rule
(four willing candidates a role, four years unsigned and you retire), the same hiring pass. Effects on players are small and capped:
a club's coaches can add at most `dev_cap` attribute points a year to any attribute of any of its players (and take as much away).
The engine's game-day coach bonus is separate and already capped (`Staff.bonus`). All numbers are data (COACHING.csv), placeholders.
"""
import math

from ..characters.creator import rng_for

COACH_ROLES = ("hc", "oc", "dc", "stc")
SIDE = {"QB": "oc", "HB": "oc", "FB": "oc", "WR": "oc", "TE": "oc", "OL": "oc",
        "DT": "dc", "DE": "dc", "LB": "dc", "CB": "dc", "S": "dc", "K": "stc", "LS": "stc"}


def teaching(world, cid, pos):
    """The club's yearly effect on a player of this position, in attribute points, inside [-dev_cap, +dev_cap]."""
    C = world.tables.coaching
    lead = world.leaders.get(cid, {})
    group = world.tables.positions[pos]["group"]

    def tch(role):
        c = lead.get(role)
        return 50.0 if c is None else float(c.attrs.get("TCH", 50))
    mix = C["hc_share"] * tch("hc") + (1 - C["hc_share"]) * tch(SIDE[group])
    return max(-C["dev_cap"], min(C["dev_cap"], C["dev_cap"] * (mix - 50.0) / 50.0))


def view(world, cid, card):
    """A club's estimate of a coach's overall: the true figure plus an error that shrinks with its general manager's evaluation."""
    T, C = world.tables, world.tables.coaching
    gm = world.leaders.get(cid, {}).get("gm")
    evl = gm.attrs.get("EVL", 50) if gm else 50
    sd = C["view_sd"] * (1 - T.scouting["evl_effect"] * (evl - 50) / 50)
    z = rng_for(world.creator.seed, "coachview", cid, card.identity.id, world.L.year).gauss(0, 1)
    return card.true_overall(T) + max(T.scouting["min_sd"], sd) * z


def retire_chance(world, card, year):
    C = world.tables.coaching
    age, top = card.age(year), world.tables.roles[card.identity.role]["age_max"]
    if age >= top:
        return 1.0
    past = age - C["retire_age_start"]
    return 0.0 if past < 0 else min(1.0, C["retire_p_start"] + C["retire_p_step"] * past)


def turn_year(world):
    """At the year turn: coaches and pool candidates retire, the pool is topped up, every vacancy is filled. Returns the hires."""
    L, T, C = world.L, world.tables, world.tables.coaching
    year, rng = L.year, world.coach_rng
    # retirements among the employed
    for cid in sorted(world.leaders):
        for role in COACH_ROLES:
            card = world.leaders[cid].get(role)
            if card is not None and rng.random() < retire_chance(world, card, year):
                world.creator.release_name(card)
                del world.leaders[cid][role]
                world.stats["coach_retirements"] = world.stats.get("coach_retirements", 0) + 1
    # the pool: age out, four-year rule
    for role in COACH_ROLES:
        keep = []
        for card in world.coach_pool[role]:
            world.pool_years[card.identity.id] = world.pool_years.get(card.identity.id, 0) + 1
            if world.pool_years[card.identity.id] >= 4 or rng.random() < retire_chance(world, card, year):
                world.creator.release_name(card)
                world.pool_years.pop(card.identity.id, None)
            else:
                keep.append(card)
        world.coach_pool[role] = keep
        while len(keep) < int(C["pool_candidates"]) + 2:          # a little over the minimum so a hiring pass has a choice
            keep.append(world.creator.leader(role, year, "hire", shift=C["pool_shift"]))
    # hiring: a random club order each year, each club takes the best of the pool by its own view
    hires = []
    order = sorted(world.leaders)
    rng.shuffle(order)
    for cid in order:
        for role in COACH_ROLES:
            if role not in world.leaders[cid]:
                hires.append((cid, role, hire(world, cid, role).identity.id))
    return hires


def hire(world, cid, role):
    """Fill one vacancy: the club takes the candidate it rates highest; the pool is topped back up to its minimum."""
    C = world.tables.coaching
    pool = world.coach_pool[role]
    best = max(pool, key=lambda c: (view(world, cid, c), -c.identity.id))
    pool.remove(best)
    world.pool_years.pop(best.identity.id, None)
    world.leaders[cid][role] = best
    world.stats["coach_hires"] = world.stats.get("coach_hires", 0) + 1
    while len(pool) < int(C["pool_candidates"]):
        pool.append(world.creator.leader(role, world.L.year, "hire", shift=C["pool_shift"]))
    return best
