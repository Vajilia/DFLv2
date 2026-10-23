"""Jersey numbers (Design Document 8.15, section 11): position blocks, overflow blocks and the choice of a number. Pure functions."""
from .positions import POSITIONS

BLOCKS = (
    (1, 9, ("K", "P", "LS")), (10, 14, ("QB",)), (15, 19, ("TE",)), (20, 29, ("CB",)), (30, 39, ("HB", "FB")),
    (40, 49, ("FS", "SS")), (50, 59, ("ROLB", "MLB", "LOLB")), (60, 79, ("LT", "LG", "C", "RG", "RT")),
    (80, 89, ("WR",)), (90, 99, ("LE", "RE", "DT")),
)
PRIMARY = {pos: (lo, hi) for lo, hi, poss in BLOCKS for pos in poss}
OVERFLOW = {"WR": (15, 19), "TE": (80, 89), "ROLB": (40, 49), "MLB": (40, 49), "LOLB": (40, 49),
            "LE": (70, 79), "RE": (70, 79), "DT": (70, 79)}
assert set(PRIMARY) == set(POSITIONS)


def in_range(n, rng):
    return rng is not None and rng[0] <= n <= rng[1]


def is_primary(pos, n):
    return in_range(n, PRIMARY[pos])


def legal(pos, n, relaxed=False):
    """Legal in the season: the position's block or its overflow block. Before the final cut (relaxed) any number 1-99."""
    return is_primary(pos, n) or in_range(n, OVERFLOW.get(pos)) or (relaxed and 1 <= n <= 99)


def choose(pos, taken, relaxed=False, prefer=None):
    """The number a player gets: the one she already has or prefers if free and legal, else the lowest open number in her primary
    block, then her overflow block, then (before the final cut only) any open number. None if nothing is open."""
    if prefer is not None and prefer not in taken and legal(pos, prefer, relaxed):
        return prefer
    ranges = [PRIMARY[pos]] + ([OVERFLOW[pos]] if pos in OVERFLOW else []) + ([(1, 99)] if relaxed else [])
    for lo, hi in ranges:
        for n in range(lo, hi + 1):
            if n not in taken:
                return n
    return None
