"""Ranked-ballot count for the Ambassador Bowl (spec section 7): single transferable vote with the Droop quota, exact arithmetic, seeded ties.

Pure and deterministic: no clock, no global state, no iteration over a set or an unordered dict. The same ballots, places and seed give the same
result in any process.

The count (builder's reading of "Droop quota, seeded ties", recorded in spec section 7):

1. quota = floor(voters / (places + 1)) + 1.
2. Every ballot starts with weight 1 and counts for the highest-ranked candidate still in the count. A candidate's tally is the sum of those weights.
3. While places remain: if as many candidates remain in the count as places remain, all of them are elected (best tally first); otherwise,
   if any candidate has a tally at or above the quota, the one with the highest tally is elected and each ballot that counted for her keeps
   the fraction (tally - quota) / tally of its weight (the weighted inclusive Gregory transfer); otherwise the candidate with the lowest tally is
   eliminated and her ballots pass on at full weight.
4. A tie (for the highest tally to elect, or the lowest to eliminate) is settled by a seeded draw among the tied candidates alone.
5. The full order: the elected in the order of election, then the rest by continuing the count with eliminations only (the last one eliminated
   is the best-placed of those not elected). A replacement later uses this order and never reruns the count.

A ballot must rank every candidate (the spec: "all candidates ranked"), so no ballot ever runs out while a candidate remains.
"""
import random
from fractions import Fraction


def droop(voters, places):
    """The Droop quota for `places` places among `voters` voters: floor(voters / (places + 1)) + 1."""
    return voters // (places + 1) + 1


def _draw(tied, seed, step):
    """One of the tied candidates, by a draw that depends only on the seed, the step of the count and the tied set (in sorted order)."""
    tied = sorted(tied)
    if len(tied) == 1:
        return tied[0]
    return tied[int(random.Random(f"{seed}|step{step}|" + ",".join(map(str, tied))).random() * len(tied))]


def count(ballots, candidates, places, seed):
    """Count `ballots` (each a complete ranking of `candidates`, best first). Returns a plain record:
    {voters, places, quota, elected: [...], order: [...], log: [...]}; each log line is a dict {step, kind, who, tally, tied?, chosen?}
    (tallies as strings of exact fractions)."""
    cands = sorted(candidates)
    if places > len(cands):
        raise ValueError(f"{places} places for {len(cands)} candidates")
    for b in ballots:
        if sorted(b) != cands:
            raise ValueError("a ballot must rank every candidate exactly once")
    voters = len(ballots)
    quota = droop(voters, places)
    weight = [Fraction(1)] * voters
    cont = set(cands)
    elected, eliminated, log = [], [], []
    step = 0

    def tallies():
        t = {c: Fraction(0) for c in sorted(cont)}
        top = []
        for i, b in enumerate(ballots):
            c = next(x for x in b if x in cont)
            top.append(c)
            t[c] += weight[i]
        return t, top

    def note(kind, who, tally, tied=None):
        row = {"step": step, "kind": kind, "who": who, "tally": str(tally)}
        if tied and len(tied) > 1:
            row["tied"] = sorted(tied)
        log.append(row)

    while len(elected) < places:
        step += 1
        t, top = tallies()
        left = places - len(elected)
        if len(cont) <= left:                              # as many left as places: all of them are in
            while cont:                                    # best tally first; a tie is drawn among the tied alone
                best = max(t[x] for x in cont)
                tied = [x for x in cont if t[x] == best]
                c = _draw(tied, seed, step)
                note("elected-without-quota", c, best, tied)
                elected.append(c)
                cont.discard(c)
            break
        best = max(t.values())
        if best >= quota:
            tied = [c for c in t if t[c] == best]
            c = _draw(tied, seed, step)
            note("elected", c, best, tied)
            keep = (best - quota) / best
            for i, x in enumerate(top):
                if x == c:
                    weight[i] *= keep
            elected.append(c)
            cont.discard(c)
        else:
            worst = min(t.values())
            tied = [c for c in t if t[c] == worst]
            c = _draw(tied, seed, step)
            note("eliminated", c, worst, tied)
            eliminated.append(c)
            cont.discard(c)
    while cont:                                            # the rest of the order: eliminations only
        step += 1
        t, _ = tallies()
        worst = min(t.values())
        tied = [c for c in t if t[c] == worst]
        c = _draw(tied, seed, step)
        note("ordered-out", c, worst, tied)
        eliminated.append(c)
        cont.discard(c)
    order = elected + eliminated[::-1]
    assert sorted(order) == cands and len(set(elected)) == places
    return {"voters": voters, "places": places, "quota": quota, "elected": elected, "order": order, "log": log}
