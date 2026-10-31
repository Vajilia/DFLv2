"""The Ambassador Bowl's voted rosters (spec section 7, Edition 8.56).

Sequence, from vote to locked roster:

1. **Ballots** are cast and frozen the moment the last Tour round is played (`cast`). The eight Ambassador head coaches vote, each on both
   conferences' rosters. A candidate is anyone on the 53-player roster of one of the conference's four Ambassador clubs (injured reserve and
   the practice squad are not). A head coach ranks every candidate at a position by what her coaches judge the player's overall to be
   (`coaching.judged`, the same estimate that picks her own club's starters: the true overall plus a symmetric error that shrinks with the
   player's seasons of evidence and the staff's judgement; no lean towards her own players). A player has one permanent position, so she is
   on exactly one positional ballot; ballots are therefore counted position by position and no tie ever crosses positions.
2. **Positional counts**: one count per position with the places of the league's positional minimums (48; the backfield counts as three HB),
   Droop quota `floor(8 / (places + 1)) + 1`, seeded ties (`stv.py`).
3. **Discretionary count**: the 5 remaining places. The field is every candidate not elected in a positional count, of every position (so
   a fullback can win one); each head coach ranks that whole field by the same judged overall; one count, 5 places, quota 2. Ties are
   drawn among the tied alone, as everywhere.
4. **Lock**, one simulated hour before kickoff (`lock`): each selected player must still be on one of the conference's four clubs' 53 and
   medically available (`out_weeks == 0`). A place whose player is not is refilled, without rerunning the count, by the next player in the
   *original order of that count* who is available and not already on the roster. If the order is exhausted the spec's exhausted-ballot rule
   applies (new distinct player from the nearest positions first; she keeps her permanent position and is named first on the emergency
   list for the short position; the exception is written to the validation log). Players who joined the four clubs after the vote were
   not on the ballot and are never chosen.

Everything the count produced is saved (`World.bowl_vote`, the ballots included), is never recomputed, and goes into the permanent archive.
"""
from . import coaching
from .gameday import NEIGHBOURS, emergency_list
from .positions import MINIMUMS, POSITIONS
from .. import stv

ROSTER = 53
DISCRETIONARY = 5
VOTERS = 8
POSITIONAL = {**{p: MINIMUMS[p] for p in MINIMUMS}, "HB": 3}              # the 48 required places (spec section 11), the backfield as three HB
assert sum(POSITIONAL.values()) == ROSTER - DISCRETIONARY == 48
CANONICAL = tuple(POSITIONS)                                                # the canonical order of positions (positions.py)


def candidates(world, clubs):
    """{position: sorted player ids} over the 53-player rosters of `clubs`."""
    out = {}
    for c in sorted(clubs):
        for pid in world.L.clubs[c].roster:
            out.setdefault(world.L.players[pid].pos, []).append(pid)
    return {pos: sorted(v) for pos, v in sorted(out.items())}


def _rank(world, voter, pids):
    """One head coach's ranking of `pids`, best judged overall first (ties by id)."""
    P = world.L.players
    return [pid for _, pid in sorted((-coaching.judged(world, voter, P[pid]), pid) for pid in pids)]


def cast(world, year, conferences):
    """Cast, count and freeze the Bowl ballots. `conferences` is {conference: [its four Ambassador clubs]}. Returns (and stores on the world)
    the saved vote record: plain values only."""
    voters = sorted(c for cl in conferences.values() for c in cl)
    assert len(voters) == VOTERS, f"{len(voters)} Ambassador head coaches vote, not {VOTERS}"
    seed = f"{world.creator.seed}|bowl|{year}"
    vote = {"year": year, "voters": voters, "conferences": {}}
    for conf, clubs in sorted(conferences.items()):
        cands = candidates(world, clubs)
        rec = {"clubs": sorted(clubs), "positional": {}, "discretionary": None, "ballots": {v: {} for v in voters}}
        elected = set()
        for pos in CANONICAL:
            places = POSITIONAL.get(pos, 0)
            if not places:
                continue
            pids = cands.get(pos, [])
            if len(pids) < places:
                raise ValueError(f"{conf} has {len(pids)} {pos} on its four clubs' rosters, fewer than the {places} places")
            ballots = []
            for v in voters:
                b = _rank(world, v, pids)
                rec["ballots"][v][pos] = b
                ballots.append(b)
            res = stv.count(ballots, pids, places, f"{seed}|{conf}|{pos}")
            rec["positional"][pos] = res
            elected.update(res["elected"])
        field = sorted(pid for pids in cands.values() for pid in pids if pid not in elected)
        ballots = []
        for v in voters:
            b = _rank(world, v, field)
            rec["ballots"][v]["discretionary"] = b
            ballots.append(b)
        rec["discretionary"] = stv.count(ballots, field, DISCRETIONARY, f"{seed}|{conf}|discretionary")
        rec["ballots"] = {v: rec["ballots"][v] for v in voters}
        vote["conferences"][conf] = rec
    world.bowl_vote = vote
    world.bowl_final = {}
    return vote


def lock(world, conf):
    """Lock one conference's roster from the frozen vote, repairing what the week's injuries and moves did. Returns the saved roster record:
    {roster: [pid...] (positional places in canonical order, then discretionary), replaced: [...], exceptions: [...], short: n}.
    Stored on the world (`bowl_final`), so a second call returns the same record."""
    if conf in world.bowl_final:
        return world.bowl_final[conf]
    vote = world.bowl_vote
    assert vote is not None and vote["year"] == world.L.year, "the Bowl ballots are cast after the last Tour round, before the Bowl"
    rec = vote["conferences"][conf]
    clubs = set(rec["clubs"])
    P = world.L.players
    in_play = {pid for c in sorted(clubs) for pid in world.L.clubs[c].roster}

    def ok(pid):
        return pid in in_play and P[pid].out_weeks == 0

    # the places, in order: (kind, position, original winner)
    places = [("pos", pos, pid) for pos in CANONICAL if pos in rec["positional"] for pid in rec["positional"][pos]["elected"]]
    places += [("disc", None, pid) for pid in rec["discretionary"]["elected"]]
    taken = {pid for _, _, pid in places if ok(pid)}
    final, replaced, exceptions = {}, [], []
    for i, (kind, pos, pid) in enumerate(places):
        if pid in taken:
            final[i] = pid
    for i, (kind, pos, pid) in enumerate(places):
        if i in final:
            continue
        order = rec["positional"][pos]["order"] if kind == "pos" else rec["discretionary"]["order"]
        new = next((q for q in order if q not in taken and ok(q)), None)
        if new is not None:
            taken.add(new)
            final[i] = new
            replaced.append({"place": i, "kind": kind, "position": pos, "out": pid, "in": new, "why": _why(world, pid, ok)})
            continue
        if kind == "disc":                                  # the discretionary field ran dry: nobody on the ballot is left
            continue
        seen = [q for q in NEIGHBOURS.get(pos, ()) if q != pos] + [q for q in CANONICAL if q != pos and q not in NEIGHBOURS.get(pos, ())]
        found = None
        for q in seen:
            if q in rec["positional"]:
                qorder = rec["positional"][q]["order"]                                 # everyone at q, best first
            else:                                                                      # fullbacks: no positional places, only the discretionary ballot
                qorder = [x for x in rec["discretionary"]["order"] if P[x].pos == q]
            cand = next((x for x in qorder if x not in taken and ok(x)), None)
            if cand is not None:
                found = (q, cand)
                break
        if found is None:                                   # nobody anywhere: the roster plays short
            continue
        q, cand = found
        taken.add(cand)
        final[i] = cand
        exceptions.append({"place": i, "short_position": pos, "player": cand, "permanent_position": q, "out": pid, "why": _why(world, pid, ok)})
    roster = [final[i] for i in sorted(final)]
    out = {"conference": conf, "roster": roster, "replaced": replaced, "exceptions": exceptions, "short": ROSTER - len(roster)}
    problems = validate(world, out)
    out["validation"] = problems
    world.bowl_final[conf] = out
    return out


def ready_at_kickoff(world, conf):
    """The locked roster's players who are present and medically ready at kickoff (spec section 7, Edition 8.58): still on a roster of the
    conference's four Ambassador clubs and `out_weeks == 0` at the moment this is called. The roster locks an hour earlier; ring eligibility is
    judged here, at kickoff, so the caller evaluates it immediately before the Bowl is played. Returns pids in roster order."""
    final = world.bowl_final[conf]
    clubs = set(world.bowl_vote["conferences"][conf]["clubs"])
    P = world.L.players
    in_play = {pid for c in sorted(clubs) for pid in world.L.clubs[c].roster}
    return [pid for pid in final["roster"] if pid in in_play and P[pid].out_weeks == 0]


def _why(world, pid, ok):
    p = world.L.players.get(pid)
    if p is None or p.where != "roster":
        return "left the roster"
    return "medically unavailable" if p.out_weeks else "left the conference's clubs"


def validate(world, final):
    """The validation log of a locked roster: [] when the roster is exactly 53 distinct, medically available players meeting every positional
    minimum; otherwise one line per departure (an exception already written in `exceptions` is named, not repeated as a failure)."""
    P = world.L.players
    roster = final["roster"]
    problems = []
    if len(set(roster)) != len(roster):
        problems.append("duplicate player on the roster")
    if len(roster) != ROSTER:
        problems.append(f"{len(roster)} players, not {ROSTER}")
    if any(P[pid].out_weeks for pid in roster):
        problems.append("a medically unavailable player is on the roster")
    counts = {}
    for pid in roster:
        counts[P[pid].pos] = counts.get(P[pid].pos, 0) + 1
    excused = {e["short_position"] for e in final["exceptions"]}
    for pos, n in sorted(POSITIONAL.items()):
        have = counts.get(pos, 0)
        if pos == "HB" and have == 2 and counts.get("FB", 0) >= 1:
            continue                                        # HB 2 and FB 1 is a legal backfield (spec section 11)
        if have < n and pos not in excused:
            problems.append(f"{pos}: {have} of {n}")
    return problems


def emergency(world, final, est):
    """The Bowl squad's emergency list: the ordinary list (neighbouring positions first, best judged first), with the player brought in for an
    exhausted position named first at that position (spec section 7)."""
    P = world.L.players
    lst = emergency_list([P[pid] for pid in final["roster"]], est)
    for e in final["exceptions"]:
        pos = e["short_position"]
        lst[pos] = (e["player"],) + tuple(x for x in lst.get(pos, ()) if x != e["player"])[:3]
    return lst
