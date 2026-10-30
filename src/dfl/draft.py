"""Draft order, the returning-ambassador lottery and compensatory picks (Design Document 8.9, section 12).

Pure functions: nothing here touches the ledger. A pick is the ledger's (draft_year, round, original_club) tuple,
so ownership and trades follow the original club; the slot a pick takes depends only on the original club.
"""
import random

ROUNDS = 7
COMP_LEAGUE_MAX = 48
COMP_CLUB_MAX = 2
COMP_BASE_MIN = 75          # first-year base, in the document's money units (section 0: comp threshold)
ELIMINATION_ORDER = ("wild_card", "divisional", "conference", "runner_up", "champion")


def lottery(returning, seed):
    """Equal-chance seeded order of the returning ambassadors (slots 1-8 of every round)."""
    order = sorted(returning)
    random.Random(f"{seed}|lottery").shuffle(order)
    return order


def elimination_rounds(playoff_log, coronation):
    """Derive each playoff club's exit from play_playoffs' log plus the Coronation.

    playoff_log: [(conference, away, home, away_score, home_score)] in play order, per conference 3 wild card games,
    2 divisional games, then the conference championship. coronation: (away, home, away_score, home_score).
    """
    out = {}
    per_conf = {}
    for conf, away, home, a, h in playoff_log:
        per_conf.setdefault(conf, []).append((away, home, a, h))
    for conf, games in per_conf.items():
        assert len(games) == 6, (conf, len(games))
        for i, (away, home, a, h) in enumerate(games):
            loser = away if a < h else home
            out[loser] = "wild_card" if i < 3 else "divisional" if i < 5 else "conference"
    away, home, a, h = coronation
    out[away if a < h else home] = "runner_up"
    out[home if h > a else away] = "champion"
    return out


def base_order(ranked, elimination, returning, seed, returned=(), returner_block=True):
    """The order of the 48 original clubs for every round, pick 1 first.

    ranked: all clubs that played the regular season, best first by the standings cascade (so the reverse is worst first,
    with every tiebreaker reversed). elimination: {club: exit round} for the 14 playoff clubs. returning: the eight
    ambassadors coming back, who take slots 1-8 by lottery. returned: clubs in their first season back from exile, who have
    already had their lottery pick: one that missed the playoffs picks as a block at the end of the non-playoff clubs
    (never ahead of her record), worst record first inside the block (Edition 8.19). A returned club that made the playoffs
    is placed by her playoff exit like any other.
    """
    assert set(elimination) <= set(ranked)
    worst_first = list(reversed(ranked))
    order = lottery(returning, seed)
    non_playoff = [c for c in worst_first if c not in elimination]
    returned = returned if returner_block else ()      # Edition 8.19; a parity test bed (parity.py) may switch the block off for its league
    order += [c for c in non_playoff if c not in returned] + [c for c in non_playoff if c in returned]
    for stage in ELIMINATION_ORDER:
        order += [c for c in worst_first if elimination.get(c) == stage]
    assert len(order) == len(set(order)) == len(ranked) + len(returning)
    return order


def ordinary_slots(year, order):
    """[(round, pick_in_round, original_club)] for the seven ordinary rounds."""
    return [(r, i + 1, c) for r in range(1, ROUNDS + 1) for i, c in enumerate(order)]


def compensatory(movements, order):
    """Compensatory picks, NFL-style: departures are netted against signings.

    movements: [{"pid", "from", "to", "base", "snap_top_quarter"(bool)}] for every free agent who changed clubs, `base`
    being the first-year base of the contract he signed. A movement qualifies if base >= COMP_BASE_MIN and, for a
    departure, the player was in his old club's top quarter of snaps (a signing counts regardless of snaps).
    For each club the qualifying signings cancel its qualifying departures, highest value first; each unmatched
    departure earns a pick, at most two per club, 48 in the league, best value first (ties: the club picking earlier in
    `order`). Returns [(club, value, pid)] in pick order.
    """
    pos = {c: i for i, c in enumerate(order)}
    lost, gained = {}, {}
    for m in movements:
        if m["base"] < COMP_BASE_MIN:
            continue
        if m["snap_top_quarter"]:
            lost.setdefault(m["from"], []).append(m)
        gained.setdefault(m["to"], []).append(m)
    cands = []
    for club, outs in lost.items():
        outs = sorted(outs, key=lambda m: (-m["base"], m["pid"]))
        cancel = len(gained.get(club, []))
        for m in outs[cancel:][:COMP_CLUB_MAX]:
            cands.append((club, m["base"], m["pid"]))
    cands.sort(key=lambda t: (-t[1], pos.get(t[0], 10 ** 6), t[2]))
    return cands[:COMP_LEAGUE_MAX]


def full_slots(year, order, comps):
    """Ordinary rounds then compensatory picks (round 8; a club's second pick is round 9 so the ledger key stays unique)."""
    slots = ordinary_slots(year, order)
    seen = {}
    n = 0
    for club, _, _ in comps:
        seen[club] = seen.get(club, 0) + 1
        n += 1
        slots.append((7 + seen[club], n, club))
    return slots
