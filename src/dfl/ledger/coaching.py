"""Coaches (people layer, Edition 8.25).

A coach is one category of character. The head-coach and the three coordinator posts are held, not born into: any coach in the common
pool may be hired to any post. The pool holds new coaches and those fired or resigned; the four-year rule retires anyone who waits
too long. The chain of command: the CEO may dismiss the head coach and hires a new one (advised by the general manager's judgement of
talent); the head coach may dismiss coordinators and hires new ones.

Effects on players are small and capped: a club's coaches add at most `dev_cap` attribute points a year to any attribute of any of
its players (and take as much away). The engine's game-day coach bonus is separate and already capped (`Staff.bonus`).
All numbers are data (COACHING.csv), placeholders.
"""
from ..characters.creator import rng_for

POSTS = ("hc", "oc", "dc", "stc")
COACH_ROLES = POSTS                   # kept for callers that name the posts
SIDE = {"QB": "oc", "HB": "oc", "FB": "oc", "WR": "oc", "TE": "oc", "OL": "oc",
        "DT": "dc", "DE": "dc", "LB": "dc", "CB": "dc", "S": "dc", "K": "stc", "LS": "stc"}


def fit(world, card, post):
    """How good this coach is in this post: her coaching attributes weighted by the post's profile (data/characters/PROFILE.csv)."""
    prof = world.tables.profile[post]
    return sum(card.attrs[a] * w for a, _, _, _, w in prof) / sum(w for *_, w in prof)


def teaching(world, cid, pos):
    """The club's yearly effect on a player of this position, in attribute points, inside [-dev_cap, +dev_cap]."""
    C = world.tables.coaching
    lead = world.leaders.get(cid, {})
    group = world.tables.positions[pos]["group"]

    def tch(post):
        c = lead.get(post)
        return 50.0 if c is None else float(c.attrs.get("TCH", 50))
    mix = C["hc_share"] * tch("hc") + (1 - C["hc_share"]) * tch(SIDE[group])
    return max(-C["dev_cap"], min(C["dev_cap"], C["dev_cap"] * (mix - 50.0) / 50.0))


def judge_rating(world, cid, post):
    """The rating of whoever judges a candidate: the general manager's talent evaluation for the head coach (the CEO takes his advice),
    the head coach's game management for a coordinator."""
    lead = world.leaders.get(cid, {})
    j = lead.get("gm") if post == "hc" else lead.get("hc")
    attr = "EVL" if post == "hc" else "MGT"
    return float(j.attrs.get(attr, 50)) if j is not None else 50.0


def view(world, cid, card, post):
    """A club's estimate of a candidate for a post: her true fit plus an error that shrinks with the judge's rating."""
    T, C = world.tables, world.tables.coaching
    sd = C["view_sd"] * (1 - T.scouting["evl_effect"] * (judge_rating(world, cid, post) - 50) / 50)
    z = rng_for(world.creator.seed, "coachview", cid, card.identity.id, post, world.L.year).gauss(0, 1)
    return fit(world, card, post) + max(T.scouting["min_sd"], sd) * z


def retire_chance(world, card, year):
    C = world.tables.coaching
    age, top = card.age(year), world.tables.roles["coach"]["age_max"]
    if age >= top:
        return 1.0
    past = age - C["retire_age_start"]
    return 0.0 if past < 0 else min(1.0, C["retire_p_start"] + C["retire_p_step"] * past)


def top_up(world):
    C = world.tables.coaching
    while len(world.coach_pool) < int(C["pool_candidates"]) * len(POSTS):
        world.coach_pool.append(world.creator.leader("coach", world.L.year, "hire", shift=C["pool_shift"]))


def vacate(world, cid, post, why):
    """She leaves the post (fired or resigned) and joins the pool, her four-year clock starting again."""
    card = world.leaders[cid].pop(post)
    world.coach_pool.append(card)
    world.pool_years[card.identity.id] = 0
    world.stats[why] = world.stats.get(why, 0) + 1
    return card


def hire(world, cid, post, exclude=()):
    """Fill one post: the club takes the pool candidate it rates highest (never one it has just let go)."""
    top_up(world)
    ok = [c for c in world.coach_pool if c.identity.id not in exclude]
    best = max(ok, key=lambda c: (view(world, cid, c, post), -c.identity.id))
    world.coach_pool.remove(best)
    world.pool_years.pop(best.identity.id, None)
    world.leaders[cid][post] = best
    world.stats["coach_hires"] = world.stats.get("coach_hires", 0) + 1
    top_up(world)
    return best


def _fill(world, cid, exclude=()):
    hires = []
    for post in POSTS:                       # the head coach first: he judges the coordinators
        if post not in world.leaders[cid]:
            hires.append((cid, post, hire(world, cid, post, exclude).identity.id))
    return hires


def turn_year(world):
    """At the year turn: coaches and pool candidates retire, the pool is topped up, every empty post is filled. Returns the hires."""
    L, rng = world.L, world.coach_rng
    for cid in sorted(world.leaders):
        for post in POSTS:
            card = world.leaders[cid].get(post)
            if card is not None and rng.random() < retire_chance(world, card, L.year):
                world.creator.release_name(card)
                del world.leaders[cid][post]
                world.stats["coach_retirements"] = world.stats.get("coach_retirements", 0) + 1
    keep = []
    for card in world.coach_pool:
        world.pool_years[card.identity.id] = world.pool_years.get(card.identity.id, 0) + 1
        if world.pool_years[card.identity.id] >= 4 or rng.random() < retire_chance(world, card, L.year):
            world.creator.release_name(card)
            world.pool_years.pop(card.identity.id, None)
            world.stats["pool_retirements"] = world.stats.get("pool_retirements", 0) + 1
        else:
            keep.append(card)
    world.coach_pool = keep
    top_up(world)
    hires = []
    order = sorted(world.leaders)
    rng.shuffle(order)
    for cid in order:
        hires += _fill(world, cid)
    return hires


def window(world, results):
    """The leadership hiring window, after a season: `results` maps club to its win percentage. For each club, in a random order:
    the CEO may dismiss a head coach after a bad season; any coach may resign; the head coach is replaced from the pool; a new head
    coach, or an old one after a bad season, may dismiss coordinators; every empty post is filled. Returns the number of changes."""
    C, rng = world.tables.coaching, world.coach_rng
    order = [c for c in sorted(world.leaders) if c in results]
    rng.shuffle(order)
    top_up(world)
    changes = 0
    for cid in order:
        lead, let_go, bad = world.leaders[cid], set(), results[cid] < C["bad_win_pct"]
        ceo = lead.get("ceo")
        patience = (ceo.traits["TMP"] - 50) / 50 if ceo is not None else 0.0
        if bad and "hc" in lead and rng.random() < C["fire_hc_p"] * (1 - C["ceo_patience_effect"] * patience):
            let_go.add(vacate(world, cid, "hc", "coach_fires_hc").identity.id)
        for post in POSTS:
            card = lead.get(post)
            if card is None:
                continue
            p = max(0.0, C["resign_base"] + C["resign_amb"] * (card.traits["AMB"] - 50) / 50 - C["resign_loy"] * (card.traits["LOY"] - 50) / 50)
            if rng.random() < p:
                let_go.add(vacate(world, cid, post, "coach_resignations").identity.id)
        new_hc = "hc" not in lead
        if new_hc:
            hire(world, cid, "hc", let_go)
        for post in ("oc", "dc", "stc"):
            if post in lead and rng.random() < (C["new_hc_fire_p"] if new_hc else (C["fire_coord_p"] if bad else 0.0)):
                let_go.add(vacate(world, cid, post, "coach_fires_coord").identity.id)
        _fill(world, cid, let_go)
        changes += len(let_go)
    return changes
