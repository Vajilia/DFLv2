"""Play calling for the spatial engine: which play the offense runs and which coverage the defense plays (Edition 8.48).

The call depends on the situation only (down, distance, field position, score and clock), never on who the players are, so that the fairness
property of section 10 holds: the same situation draws from the same weights for every club.
"""
from .core import PASSES, RUNS

COV_BASE = {"cover3": 0.31, "cover1": 0.18, "cover2": 0.16, "cover4": 0.13, "cover0": 0.02, "cover1_blitz": 0.10, "cover3_blitz": 0.10}


def draw(rng, table):
    r = rng.random() * sum(table.values())
    for k, w in table.items():
        r -= w
        if r <= 0:
            return k
    return k


def pass_call(rng, down, dist, yl, hurry=False):
    """Pick a passing play. Short yardage favours quick, short concepts; long yardage and the two-minute drill favour the deeper ones;
    inside the opponent's 20 nothing is thrown deep."""
    w = {k: v["weight"] for k, v in PASSES.items() if v["weight"] > 0}
    if dist <= 3:
        for k, m in (("quick_slants", 1.5), ("stick", 1.6), ("four_verts", 0.3), ("deep_shot", 0.4), ("screen", 0.6)):
            w[k] *= m
    elif dist >= 8 or down >= 3 and dist >= 5:
        for k, m in (("deep_shot", 1.6), ("four_verts", 1.5), ("flood", 1.4), ("smash", 1.3), ("mesh", 1.2), ("quick_slants", 0.7), ("stick", 0.6)):
            w[k] *= m
    if down >= 3 and dist >= 5:
        # on third and long the concepts that run to the sticks replace the ones that check down short of them
        for k, m in (("quick_slants", 0.5), ("curl_flat", 0.6), ("stick", 0.4), ("mesh", 0.7), ("screen", 0.3), ("play_action", 0.5),
                     ("smash", 1.4), ("flood", 1.5), ("empty_levels", 1.3)):
            w[k] *= m
    if yl >= 80:
        for k in ("four_verts", "deep_shot", "play_action"):
            w[k] *= 0.4 if yl < 90 else 0.15
    if hurry:
        for k, m in (("smash", 1.4), ("curl_flat", 1.3), ("four_verts", 0.7), ("screen", 0.3), ("play_action", 0.4)):
            w[k] *= m
    return draw(rng, w)


def run_call(rng, down, dist, yl, lead=0):
    w = {k: v["weight"] for k, v in RUNS.items() if v["weight"] > 0}
    if dist <= 2:
        for k, m in (("power", 1.8), ("inside_zone", 1.4), ("draw", 0.3), ("outside_zone", 0.6)):
            w[k] *= m
    elif dist >= 8:
        for k, m in (("draw", 2.2), ("outside_zone", 1.1), ("power", 0.7)):
            w[k] *= m
    return draw(rng, w)


def coverage_call(rng, down, dist, yl, is_pass, lead=0):
    """The defensive shell. Long yardage brings the two-deep and four-deep shells; short yardage and the goal line bring man and pressure."""
    w = dict(COV_BASE)
    if dist >= 8:
        w["cover4"] *= 1.5
        w["cover2"] *= 1.3
        w["cover0"] *= 0.5
        w["cover1_blitz"] *= 0.7
    elif dist <= 2:
        w["cover1"] *= 1.3
        w["cover0"] *= 2.0
        w["cover4"] *= 0.5
    if yl >= 90:
        w["cover1"] *= 1.4
        w["cover0"] *= 2.5
        w["cover4"] *= 0.4
    if not is_pass:
        w["cover0"] *= 0.5
    return draw(rng, w)
