"""Season analytics (Edition 8.30): from per-player production to the analytical score of section 15.

For each player-season: opportunities (plays with a role), expected points added (EPA), win probability added (WPA). Three components,
each standardised within the position that season among players who reached the opportunity threshold:
- role execution: her EPA above what a bench-level player at her position would have made in the same opportunities;
- opponent-adjusted difficulty: the same, with each opportunity weighted by how good the opposing unit was (from the public production of
  the clubs, not from hidden ratings: a defence that allowed little is tough, an offence that made a lot is tough);
- leverage: her WPA.
Score = 0.60 role + 0.25 opponent-adjusted + 0.15 leverage. Standardising within position and season is the era standardisation.
Everything here reads recorded production only; nothing reads it back into a game. All numbers are data (data/analytics), placeholders.
"""
import statistics as st

from ..engine.production import tables

# players needed per club by official position (the engine's formation and kicking game)
SLOTS = {"QB": 1, "HB": 1, "TE": 1, "WR": 3, "LT": 1, "LG": 1, "C": 1, "RG": 1, "RT": 1, "LE": 1, "RE": 1, "DT": 2, "ROLB": 1, "MLB": 1, "LOLB": 1,
         "CB": 2, "FS": 1, "SS": 1, "K": 1, "P": 1, "LS": 1}
DEF_POS = {"LE", "RE", "DT", "ROLB", "MLB", "LOLB", "CB", "FS", "SS"}
KICK_POS = {"K", "P", "LS"}


def _z(values):
    m = st.fmean(values)
    sd = st.pstdev(values) if len(values) > 1 else 0.0
    return (lambda x: (x - m) / sd) if sd > 1e-9 else (lambda x: 0.0)


def unit_quality(team):
    """{(side, play type): (club -> z)}: how tough each club's unit was, from its public production.
    A defence is tough when it allowed little per play; an offence when it made a lot."""
    out = {}
    for side in ("off", "def"):
        for pt in ("pass", "run"):
            per = {c: v[side][pt][1] / v[side][pt][0] for c, v in team.items() if side in v and pt in v[side] and v[side][pt][0] > 0}
            if len(per) < 3:
                continue
            f = _z(list(per.values()))
            out[(side, pt)] = {c: (-f(x) if side == "def" else f(x)) for c, x in per.items()}
    return out


def season_close(world, year):
    """Score the season and file it under `world.career`; credited seasons are recorded; the bulky production is then dropped."""
    A = tables()["a"]
    prod = world.production.pop(year, {})
    team = world.team_production.pop(year, {})
    for pid in world.appearances.pop(year, set()):
        world.credited.setdefault(pid, set()).add(year)
    quality = unit_quality(team)
    rows = {}
    for pid, byopp in prod.items():
        card = world.cards.get(pid)
        if card is None:
            continue
        pos = card.identity.pos
        n = epa = wpa = adj = 0.0
        for opp, types in byopp.items():
            for pt, (cnt, e, w) in types.items():
                n += cnt
                epa += e
                wpa += w
                f = 1.0
                if pos not in KICK_POS and pt in ("pass", "run"):
                    side = "off" if pos in DEF_POS else "def"                  # the opposing unit
                    z = quality.get((side, pt), {}).get(opp)
                    if z is not None:
                        f = max(0.3, min(1.7, 1 + A["difficulty_gain"] * z))
                adj += e * f
        if n > 0:
            world.persona[pid] = (card.identity.name, pos)           # kept for the halls after the card is purged
            rows[pid] = {"pos": pos, "n": n, "epa": epa, "adj": adj, "wpa": wpa}
    clubs = max(1, len(team))
    bypos = {}
    for pid, r in rows.items():
        bypos.setdefault(r["pos"], []).append(pid)
    for pos, pids in bypos.items():
        pids.sort(key=lambda i: (-rows[i]["n"], i))
        starters = max(1, SLOTS.get(pos, 1) * clubs)
        top = pids[:starters]
        full = st.median(rows[i]["n"] for i in top)
        thr = A["threshold_share"] * full
        ok = [i for i in pids if rows[i]["n"] >= thr]
        tset = set(top)
        bench = [i for i in ok if i not in tset]
        base = (sum(rows[i]["epa"] for i in bench) / sum(rows[i]["n"] for i in bench)) if bench and sum(rows[i]["n"] for i in bench) > 0 else 0.0
        for i in ok:
            r = rows[i]
            r["role"], r["opp_adj"], r["lev"] = r["epa"] - base * r["n"], r["adj"] - base * r["n"], r["wpa"]
        if not ok:
            continue
        zr, za, zl = (_z([rows[i][k] for i in ok]) for k in ("role", "opp_adj", "lev"))
        for i in ok:
            r = rows[i]
            r["score"] = A["w_role"] * zr(r["role"]) + A["w_opp"] * za(r["opp_adj"]) + A["w_lev"] * zl(r["lev"])
    for pid, r in rows.items():
        p = world.L.players.get(pid)
        r["club"] = p.club if p is not None else None
        r["year"] = year
        world.career.setdefault(pid, {})[year] = r
    return rows
