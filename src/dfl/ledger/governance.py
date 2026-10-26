"""Fans, recall, elections and the front office (people layer, Edition 8.27).

Each club has a fan card standing for one million fans in three viewpoints (results, investment, stability), seeded between 20 and 50
percent and normalised into shares. Each viewpoint holds a satisfaction between 0 and 100 that returns gradually toward 50 and moves with
its own signal after every season: the club's record, its spending against the cap, and how much of last year's core it kept. A club's
sentiment is the share-weighted satisfaction. After a club's last game, sentiment at or below the recall line calls a recall ballot: the
viewpoints below the recall line vote to remove the CEO, and a majority of the fans removes him. CEOs also stand for election every
three seasons (clubs staggered) and whenever the office falls vacant; the viewpoints vote for the candidate they rate highest, the
sitting CEO carrying credit or blame for each viewpoint's mood. A successor takes office in the next offseason.

The CEO hires the general manager from a pool and may dismiss her after a bad season, his temperament scaling his patience. A general
manager's discretion (discretion) is hidden and feeds only the story layer: nothing in the engine or the ledger reads it (tests/test_governance.py).
CEO and GM ratings reach the game only through what already exists: the GM's evaluation, negotiation and greed in views and trades, the
CEO's temperament in dismissals. All numbers are data (GOVERNANCE.csv), placeholders.
"""
from ..characters.creator import rng_for
from . import coaching, rooms
from . import rules as R

VIEWS = ("results", "investment", "stability")


def G(world):
    return world.tables.governance


# ---------------------------------------------------------------- fan cards
def found(world):
    """Founding Day: one fan card per club."""
    g = G(world)
    for cid in sorted(world.leaders):
        rng = rng_for(world.creator.seed, "fans", cid)
        raw = [rng.uniform(g["seed_low"], g["seed_high"]) for _ in VIEWS]
        tot = sum(raw)
        world.fans[cid] = {"share": [r / tot for r in raw], "sat": [g["sentiment_start"]] * len(VIEWS), "core": set(), "sentiment": g["sentiment_start"]}


def sentiment(card):
    return sum(s * a for s, a in zip(card["share"], card["sat"]))


def signals(world, cid, win_pct):
    """The three yearly signals, each in [-1, 1]: record, spending against the cap, share of last year's core kept."""
    g, L = G(world), world.L
    card = world.fans[cid]
    res = max(-1.0, min(1.0, (win_pct - 0.5) / g["results_range"]))
    pay = float(L.cap_number(cid)) / float(R.CAP)
    inv = max(-1.0, min(1.0, (pay - g["spend_ref"]) / g["spend_range"]))
    if card["core"]:
        here = set(L.clubs[cid].all_pids())
        kept = len(card["core"] & here) / len(card["core"])
        stab = max(-1.0, min(1.0, (kept - g["core_ref"]) / g["core_range"]))
    else:
        stab = 0.0
    return (res, inv, stab)


def _core(world, cid):
    L, y = world.L, world.L.year
    ranked = sorted(L.clubs[cid].all_pids(), key=lambda pid: (-float(L.players[pid].contract.cap_hit(y)) if L.players[pid].contract else 0.0, pid))
    return set(ranked[:int(G(world)["core_size"])])


def close_year(world, results):
    """After the season: every fan card moves, recall ballots are held, scheduled elections are held. Winners wait for the next offseason."""
    g, year = G(world), world.L.year
    for cid in sorted(world.fans):
        if cid not in results:
            continue
        world.form[cid] = results[cid]
        card = world.fans[cid]
        sig = signals(world, cid, results[cid])
        gains = (g["gain_results"], g["gain_investment"], g["gain_stability"])
        card["sat"] = [max(0.0, min(100.0, s + g["pull"] * (50 - s) + k * x)) for s, k, x in zip(card["sat"], gains, sig)]
        card["sentiment"] = sentiment(card)
        card["core"] = _core(world, cid)
    order = sorted(world.fans)
    for i, cid in enumerate(order):
        if cid not in results:
            continue
        card, ceo = world.fans[cid], world.leaders[cid].get("ceo")
        if ceo is None or cid in world.elected:
            continue
        recalled = False
        if card["sentiment"] <= g["recall_threshold"]:
            against = sum(sh for sh, s in zip(card["share"], card["sat"]) if s < g["recall_line"])
            world.stats["recall_ballots"] = world.stats.get("recall_ballots", 0) + 1
            recalled = against > 0.5
            world.gov_log.append({"year": year, "club": cid, "kind": "recall_ballot", "sentiment": round(card["sentiment"], 1), "against": round(against, 2), "removed": recalled})
        due = (world.L.year + i) % int(g["election_cycle"]) == 0
        if recalled or due:
            world.stats["recalls" if recalled else "elections"] = world.stats.get("recalls" if recalled else "elections", 0) + 1
            winner = election(world, cid, incumbent=None if recalled else ceo, exclude={ceo.identity.id} if recalled else set())
            if winner is not ceo:
                world.elected[cid] = winner
                if not recalled:
                    world.stats["term_losses"] = world.stats.get("term_losses", 0) + 1
            world.gov_log.append({"year": year, "club": cid, "kind": "election", "cause": "recall" if recalled else "term", "winner": winner.identity.id, "incumbent_won": winner is ceo,
                                  "sentiment": round(card["sentiment"], 1)})


# ---------------------------------------------------------------- elections
def standing(world, card, role):
    return coaching.fit(world, card, role)


def election(world, cid, incumbent=None, exclude=()):
    """The fans choose a CEO. Each viewpoint votes for the candidate it rates highest (public standing plus its own error, plus credit or
    blame for the incumbent by its mood); the candidate with most fans behind him wins. Pool candidates stay in the pool until they take office."""
    g, year = G(world), world.L.year
    exclude = set(exclude) | {c.identity.id for c in world.elected.values()}          # one candidate cannot win two clubs in a year (Edition 8.39)
    topup(world, "ceo")
    while sum(1 for c in world.ceo_pool if c.identity.id not in exclude) < int(g["candidates"]):
        world.ceo_pool.append(world.creator.leader("ceo", year, "hire", shift=g["ceo_pool_shift"]))
    cands = [c for c in world.ceo_pool if c.identity.id not in exclude]
    if incumbent is not None:
        cands = [incumbent] + cands
    card = world.fans[cid]
    tally = {}
    for j, (share, sat) in enumerate(zip(card["share"], card["sat"])):
        best = max(cands, key=lambda c: (standing(world, c, "ceo") + g["voter_sd"] * rng_for(world.creator.seed, "vote", cid, year, j, c.identity.id).gauss(0, 1)
                                          + (g["incumbent_bonus"] + g["incumbent_weight"] * (sat - 50) / 50 if c is incumbent else 0.0), -c.identity.id))
        tally[best.identity.id] = tally.get(best.identity.id, 0.0) + share
    top = max(tally.values())
    return next(c for c in cands if c.identity.id in tally and tally[c.identity.id] == top)


# ---------------------------------------------------------------- pools
def pool(world, role):
    return world.ceo_pool if role == "ceo" else world.gm_pool


def topup(world, role):
    g = G(world)
    p = pool(world, role)
    while len(p) < int(g["candidates"]):
        p.append(world.creator.leader(role, world.L.year, "hire", shift=g[role + "_pool_shift"]))


def retire_chance(world, card, role):
    g = G(world)
    age, top = card.age(world.L.year), world.tables.roles[role]["age_max"]
    if age >= top:
        return 1.0
    past = age - g["ceo_retire_age_start" if role == "ceo" else "retire_age_start"]
    return 0.0 if past < 0 else min(1.0, g["retire_p_start"] + g["retire_p_step"] * past)


def vacate(world, cid, role, why):
    card = world.leaders[cid].pop(role)
    if card not in pool(world, role):
        pool(world, role).append(card)
    world.pool_years[card.identity.id] = 0
    world.stats[why] = world.stats.get(why, 0) + 1
    world.gov_log.append({"year": world.L.year, "club": cid, "kind": why, "id": card.identity.id})
    return card


def hire_gm(world, cid, exclude=()):
    """The CEO hires the general manager from the pool: the candidate the club rates highest, the CEO's governance judgement shrinking
    the error of that rating."""
    T, g = world.tables, G(world)
    topup(world, "gm")
    ceo = world.leaders[cid].get("ceo")
    judge = float(ceo.attrs.get("GOV", 50)) if ceo is not None else 50.0
    sd = max(T.scouting["min_sd"], T.coaching["view_sd"] * (1 - T.scouting["evl_effect"] * (judge - 50) / 50))

    def seen(c, factor=1.0):
        return coaching.fit(world, c, "gm") + sd * factor * rng_for(world.creator.seed, "gmview", cid, c.identity.id, world.L.year).gauss(0, 1)
    ok = [c for c in world.gm_pool if c.identity.id not in exclude]
    best = rooms.choose(world, cid, "gm", ok, seen)
    world.gm_pool.remove(best)
    world.pool_years.pop(best.identity.id, None)
    world.leaders[cid]["gm"] = best
    world.stats["gm_hires"] = world.stats.get("gm_hires", 0) + 1
    world.gov_log.append({"year": world.L.year, "club": cid, "kind": "gm_hired", "id": best.identity.id})
    topup(world, "gm")
    return best


def seat_ceo(world, cid, card):
    old = world.leaders[cid].get("ceo")
    if old is not None and old is not card:
        vacate(world, cid, "ceo", "ceo_replaced")
    if card in world.ceo_pool:
        world.ceo_pool.remove(card)
    world.pool_years.pop(card.identity.id, None)
    world.leaders[cid]["ceo"] = card
    world.gov_log.append({"year": world.L.year, "club": cid, "kind": "ceo_seated", "id": card.identity.id})
    topup(world, "ceo")


# ---------------------------------------------------------------- the year turn and the window
def turn_year(world):
    """At the year turn: CEOs and GMs retire; pools age, retire and are topped up; elected CEOs take office; every vacancy is filled."""
    g, L, rng = G(world), world.L, world.gov_rng
    for cid in sorted(world.leaders):
        for role in ("ceo", "gm"):
            card = world.leaders[cid].get(role)
            if card is not None and rng.random() < retire_chance(world, card, role):
                world.creator.release_name(card)
                del world.leaders[cid][role]
                world.stats[role + "_retirements"] = world.stats.get(role + "_retirements", 0) + 1
                world.gov_log.append({"year": L.year, "club": cid, "kind": role + "_retired", "id": card.identity.id, "age": card.age(L.year)})
    for role in ("ceo", "gm"):
        keep = []
        for card in pool(world, role):
            n = world.pool_years[card.identity.id] = world.pool_years.get(card.identity.id, 0) + 1
            if n >= g["gm_pool_years"] or rng.random() < retire_chance(world, card, role):
                world.creator.release_name(card)
                world.pool_years.pop(card.identity.id, None)
            else:
                keep.append(card)
        pool(world, role)[:] = keep
        topup(world, role)
    for cid in sorted(world.elected):
        seat_ceo(world, cid, world.elected[cid])
    world.elected.clear()
    for cid in sorted(world.leaders):
        if "ceo" not in world.leaders[cid]:
            seat_ceo(world, cid, election(world, cid))
    order = sorted(world.leaders)
    rng.shuffle(order)
    for cid in order:
        if "gm" not in world.leaders[cid]:
            hire_gm(world, cid)


def window(world, results):
    """The leadership window: after a bad season the CEO may dismiss the general manager (his temperament scaling his patience); she may
    resign; an empty chair is filled from the pool. Runs before the coaching window, so a new GM already advises the head-coach hire."""
    g, C, rng = G(world), world.tables.coaching, world.gov_rng
    order = [c for c in sorted(world.leaders) if c in results]
    rng.shuffle(order)
    changes = 0
    for cid in order:
        lead = world.leaders[cid]
        gm, ceo = lead.get("gm"), lead.get("ceo")
        if gm is None:
            continue
        patience = (ceo.traits["TMP"] - 50) / 50 if ceo is not None else 0.0
        let_go = set()
        if results[cid] < C["bad_win_pct"] and rng.random() < g["gm_fire_p"] * (1 - g["patience_effect"] * patience):
            let_go.add(vacate(world, cid, "gm", "ceo_fires_gm").identity.id)
        else:
            p = max(0.0, g["gm_resign_base"] + C["resign_amb"] * (gm.traits["AMB"] - 50) / 50 - C["resign_loy"] * (gm.traits["LOY"] - 50) / 50)
            if rng.random() < p:
                let_go.add(vacate(world, cid, "gm", "gm_resignations").identity.id)
        if "gm" not in lead:
            hire_gm(world, cid, let_go)
            changes += 1
    return changes
