"""Trades, stage 1 (people layer, Edition 8.26): a deterministic protocol that the autopilot general managers play.

The shape is the owner's: every club can see one common, searchable, always-current list of players built from public facts; a
club that is willing to deal puts players on its trading block; a control step asks clubs for proposals; the other club accepts,
rejects or counters; at most three rounds; the ledger validates every deal; every step is logged. A club values a player through its
own scouting view (Edition 8.21), so two clubs can each think they are winning, which is what makes a trade worth doing.

Only players and draft picks move. Cap space and cash are never traded (owner's ruling). Later stages put language-model
deciders behind the same `decide` points; the protocol, the ledger check and the log do not change.
All numbers are data (data/characters/TRADING.csv), placeholders.
"""
from fractions import Fraction as F

from . import rooms
from . import rules as R
from .ledger import Rejected
from .positions import MINIMUMS


# ---------------------------------------------------------------- the public price (Edition 8.34)
def _prior(world, pos, age, skip=None):
    """The going level at a position for players of about this age: the market knows how good starters at a position are at each age,
    not how good any one of them is. Cached for the league year."""
    T = world.tables.trading
    cache = world.pub_prior
    if cache.get("year") != world.L.year:
        cache.clear()
        cache["year"] = world.L.year
        groups = {}
        for cid in world.L.clubs:
            for pid in world.L.clubs[cid].roster + world.L.clubs[cid].ir:
                p = world.L.players[pid]
                groups.setdefault(p.pos, []).append((p.age, world.rating[pid], pid))
        cache["groups"] = groups
    rows = cache["groups"].get(pos, [])
    rows = [(a, r) for a, r, i in rows if i != skip]               # never her own rating
    near = [r for a, r in rows if abs(a - age) <= T["pub_age_window"]]
    use = near if len(near) >= T["pub_prior_min"] else [r for _, r in rows]
    return sum(use) / len(use) if use else float(world.tables.trading.get("pub_default", 60))


def public_ovr(world, p):
    """What anyone can estimate of a player's overall from public evidence: the going level for her position and age, moved by her
    recent analytical scores (section 15), which count more the more seasons she has. Reads no hidden attribute of hers."""
    T = world.tables.trading
    yrs = sorted(world.career.get(p.pid, {}).items())[-int(T["pub_years"]):]
    scores = [r["score"] for _, r in yrs if "score" in r]
    est = _prior(world, p.pos, p.age, p.pid)
    if scores:
        n = len(scores)
        est += T["pub_slope"] * (sum(scores) / n) * n / (n + T["pub_k"])
    return est


def public_market(world, p):
    """The public market price: the price curve at the public estimate of her overall."""
    from .world import price
    return float(price(int(round(public_ovr(world, p))), p.pos))


# ---------------------------------------------------------------- the common public list
def years_left(world, p):
    k = p.contract
    return sum(1 for y in range(world.L.year, world.L.year + R.MAX_COVERED_YEARS) if k.covers(y)) if k else 0


def public_row(world, p):
    """Public facts only: no hidden attributes, no true overall, no club's private valuation."""
    return {"pid": p.pid, "club": p.club, "pos": p.pos, "age": p.age, "seasons": p.seasons, "slot": p.where,
            "cap_hit": float(p.contract.cap_hit(world.L.year)), "years_left": years_left(world, p), "out_weeks": p.out_weeks,
            "market": public_market(world, p)}        # from public evidence only (Edition 8.34)


def public_list(world, pos=None, club=None, min_age=None, max_age=None, max_cap=None, min_years=None, sort="cap_hit", limit=None):
    """The searchable list: every rostered player (roster or injured reserve) of every club, filtered and sorted."""
    rows = []
    for cid in sorted(world.L.clubs):
        if club is not None and cid != club:
            continue
        for pid in world.L.clubs[cid].roster + world.L.clubs[cid].ir:
            p = world.L.players[pid]
            if pos is not None and p.pos != pos:
                continue
            row = public_row(world, p)
            if ((min_age is not None and row["age"] < min_age) or (max_age is not None and row["age"] > max_age)
                    or (max_cap is not None and row["cap_hit"] > max_cap) or (min_years is not None and row["years_left"] < min_years)):
                continue
            rows.append(row)
    rows.sort(key=lambda r: (-r[sort] if sort in ("market", "age", "seasons") else r[sort], r["pid"]))
    return rows[:limit] if limit else rows


# ---------------------------------------------------------------- a club's private valuation
def _have(world, cid):
    return world.counts(cid, ("roster",))


def pick_value(world, pk):
    T = world.tables.trading
    L = world.L
    ahead = max(0, pk[0] - (L.draft_completed + 1))
    return T["pick_round_%d" % min(9, pk[1])] * T["pick_decay"] ** ahead


def value(world, cid, pid, delta, have=None):
    """What the player is worth to this club (salary units of surplus) by its own view: the price of the overall it thinks she has,
    cut for age, moved by how short or deep the club is at her position, less her cap hit, over the contract years that count.
    delta +1: the club would receive her; -1: it would give her away."""
    from .world import price, TEMPLATE
    T = world.tables.trading
    p = world.L.players[pid]
    have = have if have is not None else _have(world, cid)
    worth = float(price(int(round(world.view(cid, p))), p.pos))
    worth *= max(T["age_floor"], 1 - T["age_step"] * max(0, p.age - T["age_start"]))
    n, need = have.get(p.pos, 0), TEMPLATE.get(p.pos, 0)
    if delta > 0:
        worth *= 1 + T["need_boost"] if n < need else (1 - T["surplus_discount"] if n > need else 1.0)
    else:
        worth *= 1 + T["need_boost"] if n - 1 < need else (1 - T["surplus_discount"] if n > need else 1.0)
    hit = float(p.contract.cap_hit(world.L.year))
    return (worth - hit) * max(1, min(years_left(world, p), int(T["years_cap"])))


def score(world, cid, gets, gives, get_picks=(), give_picks=()):
    """The club's gain from a deal, in salary units."""
    have = _have(world, cid)
    return (sum(value(world, cid, i, +1, have) for i in gets) + sum(pick_value(world, k) for k in get_picks)
            - sum(value(world, cid, i, -1, have) for i in gives) - sum(pick_value(world, k) for k in give_picks))


def margin(world, cid):
    """The surplus the club must see before it accepts: its general manager's greed raises it, her negotiation raises it too."""
    T = world.tables.trading
    gm = world.leaders.get(cid, {}).get("gm")
    greed = (gm.traits["GRE"] - 50) / 50 if gm else 0.0
    neg = (gm.attrs.get("NEG", 50) - 50) / 50 if gm else 0.0
    return T["accept_margin"] * (1 + T["greed_effect"] * greed + T["neg_effect"] * neg)


def _public_value(world, pid):
    """What anyone can estimate of a player's worth from public facts (the common list): market price less cap hit over the counted years."""
    T = world.tables.trading
    p = world.L.players[pid]
    return float(public_market(world, p) - p.contract.cap_hit(world.L.year)) * max(1, min(years_left(world, p), int(T["years_cap"])))


# ---------------------------------------------------------------- the trading block
def block(world, cid):
    """The players this club is willing to move: those worth little to it (surplus depth, aging or overpaid players)."""
    T = world.tables.trading
    have = _have(world, cid)
    cands = []
    for pid in world.L.clubs[cid].roster:
        p = world.L.players[pid]
        if p.out_weeks:
            continue
        v = value(world, cid, pid, -1, have)
        if v <= T["block_max_value"]:
            cands.append((v, pid))
    return [pid for _, pid in sorted(cands)[:int(T["block_size"])]]


# ---------------------------------------------------------------- the protocol
def _still_viable(world, cid, gets, gives):
    """After the deal the club must still have the minimum healthy players at every position."""
    have = dict(_have(world, cid))
    for i in gives:
        have[world.L.players[i].pos] = have.get(world.L.players[i].pos, 0) - 1
    for i in gets:
        have[world.L.players[i].pos] = have.get(world.L.players[i].pos, 0) + 1
    return all(have.get(pos, 0) >= m for pos, m in MINIMUMS.items())


def _log(world, kind, a, b, gives_a, gives_b, picks_a, picks_b, **extra):
    rec = {"year": world.L.year, "week": world.L.week, "kind": kind, "a": a, "b": b,
           "a_gives": sorted(gives_a), "b_gives": sorted(gives_b), "a_picks": sorted(picks_a), "b_picks": sorted(picks_b)}
    rec.update({k: round(v, 2) if isinstance(v, float) else v for k, v in extra.items()})
    world.trade_log.append(rec)
    if kind == "propose":
        world._tape = rooms.TradeTape(world, a, b)
    if getattr(world, "_tape", None) is not None:
        world._tape.note(kind, extra.get("round"), gives=sorted(gives_a), gets=sorted(gives_b), picks=sorted(picks_a), score=rec.get("score_a", rec.get("score_b")))


def _assets(world, cid, used, taken_ids, taken_picks):
    """What a club could add to a deal, the cheapest to itself first: players it values less than the public does (it sells high),
    then its picks, lowest rounds first."""
    have = _have(world, cid)
    chips = []
    for pid in world.L.clubs[cid].roster:
        p = world.L.players[pid]
        if p.out_weeks or pid in used or pid in taken_ids:
            continue
        diff = value(world, cid, pid, -1, have) - _public_value(world, pid)
        if diff < 0:
            chips.append((diff, pid))
    out = [("p", i) for _, i in sorted(chips)]
    out += [("k", k) for k in sorted(world.L.clubs[cid].picks, key=lambda k: (-k[1], -k[0]))
            if world.L.draft_completed < k[0] <= world.L.draft_completed + 3 and k not in taken_picks]
    return out


def _legal(world, a, gives_a, b, gives_b, picks_a):
    """Dry-run the deal through the ledger (it rolls back whole): clubs do not propose what the rules would reject."""
    L = world.L
    return L.probe([a, b], list(gives_a) + list(gives_b), lambda: L.trade(a, gives_a, b, gives_b, a_picks=picks_a))


def _fits(world, cid, n_get, n_give):
    """The deal must leave the club inside its roster limits (the ledger would reject it otherwise)."""
    c = world.L.clubs[cid]
    if world.L.phase in ("offseason", "camp"):
        return len(c.roster) + len(c.ps) + n_get - n_give <= R.PRESEASON_CEILING
    return len(c.roster) + n_get - n_give <= R.ROSTER_MAX


def negotiate(world, a, b, target, used):
    """One negotiation, held as a meeting room (Edition 8.29) from the first proposal to the deal or the lapse."""
    world._tape = None
    ok = _negotiate(world, a, b, target, used)
    tape, world._tape = world._tape, None
    if tape is not None:
        tape.finish("deal" if ok else "no_deal")
    return ok


def _negotiate(world, a, b, target, used):
    """Club `a` goes after `target` (on b's block). Round 1 a proposes, round 2 b accepts, rejects or counters, round 3 a answers a
    counter. Returns True if a deal went through the ledger."""
    T = world.tables.trading
    L = world.L
    maxp, maxk = int(T["max_players"]), int(T["max_picks"])
    gives_a, picks_a = [], []
    # a builds an offer from its cheapest assets until it looks fair to b on public information
    want = _public_value(world, target) + margin(world, a)
    got = 0.0
    for kind, x in _assets(world, a, used, set(), set()):
        if got >= want:
            break
        if kind == "p" and len(gives_a) < maxp:
            gives_a.append(x)
            got += max(0.0, _public_value(world, x))
        elif kind == "k" and len(picks_a) < maxk:
            picks_a.append(x)
            got += pick_value(world, x)
    if got < want:
        return False
    gets_a = [target]
    if (not _still_viable(world, a, gets_a, gives_a) or not _still_viable(world, b, gives_a, gets_a)
            or not _fits(world, a, 1, len(gives_a)) or not _fits(world, b, len(gives_a), 1)):
        return False
    sa = score(world, a, gets_a, gives_a, (), picks_a)
    if sa < margin(world, a) or not _legal(world, a, gives_a, b, gets_a, picks_a):
        return False
    world.stats["trade_proposals"] = world.stats.get("trade_proposals", 0) + 1
    _log(world, "propose", a, b, gives_a, gets_a, picks_a, [], round=1, score_a=sa)
    sb = score(world, b, gives_a, gets_a, picks_a, ())
    final_a, final_b, final_pa = list(gives_a), list(gets_a), list(picks_a)
    if sb < margin(world, b):
        # round 2: b counters by asking a for one more asset, the cheapest to a that satisfies b
        best = None
        for kind, x in _assets(world, a, used, set(gives_a), set(picks_a)):
            if kind == "p" and len(gives_a) >= maxp or kind == "k" and len(picks_a) >= maxk:
                continue
            ga, pa = gives_a + ([x] if kind == "p" else []), picks_a + ([x] if kind == "k" else [])
            if (not _still_viable(world, a, gets_a, ga) or not _still_viable(world, b, ga, gets_a)
                    or not _fits(world, a, 1, len(ga)) or not _fits(world, b, len(ga), 1)
                    or not _legal(world, a, ga, b, gets_a, pa)):
                continue
            s2 = score(world, b, ga, gets_a, pa, ())
            if s2 >= margin(world, b):
                best = (ga, pa, s2)
                break
        if best is None:
            _log(world, "reject", a, b, gives_a, gets_a, picks_a, [], round=2, score_b=sb)
            world.stats["trade_lapses"] = world.stats.get("trade_lapses", 0) + 1
            return False
        final_a, final_pa = best[0], best[1]
        world.stats["trade_counters"] = world.stats.get("trade_counters", 0) + 1
        _log(world, "counter", a, b, final_a, gets_a, final_pa, [], round=2, score_b=best[2])
        # round 3: a answers the counter
        sa2 = score(world, a, gets_a, final_a, (), final_pa)
        if sa2 < margin(world, a):
            _log(world, "reject", a, b, final_a, gets_a, final_pa, [], round=3, score_a=sa2)
            world.stats["trade_lapses"] = world.stats.get("trade_lapses", 0) + 1
            return False
    try:
        L.trade(a, final_a, b, final_b, a_picks=final_pa)
    except Rejected as e:
        world.stats["trades_rejected"] += 1
        world._reject("trade", e)
        _log(world, "illegal", a, b, final_a, final_b, final_pa, [], reason=str(e)[:60])
        return False
    world.stats["trades"] += 1
    world.stats["players_traded"] = world.stats.get("players_traded", 0) + len(final_a) + len(final_b)
    used.update(final_a)
    used.update(final_b)
    _log(world, "accept", a, b, final_a, final_b, final_pa, [])
    return True


def window(world):
    """One run of the trading step: clubs publish blocks, a random few propose for the best player on another club's block that
    they value, and each negotiation runs the three-round protocol. No limit on how many deals result; a player is in at most one deal
    per run. Returns the number of trades made."""
    T, L, rng = world.tables.trading, world.L, world.rng
    cids = sorted(L.clubs)
    blocked = {cid: block(world, cid) for cid in cids}
    order = list(cids)
    rng.shuffle(order)
    used, made = set(), 0
    for a in order[:int(T["attempts_per_call"])]:
        have = _have(world, a)
        targets = []
        for b in cids:
            if b == a:
                continue
            for pid in blocked[b]:
                if pid in used:
                    continue
                v = value(world, a, pid, +1, have)
                if v >= T["target_min"]:
                    targets.append((-v, b, pid))
        targets.sort()
        for _, b, pid in targets[:int(T["tries_per_club"])]:       # her best targets in turn until one deal is struck (Edition 8.42)
            if pid not in used and negotiate(world, a, b, pid, used):
                made += 1
                break
    return made
