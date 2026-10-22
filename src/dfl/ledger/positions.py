"""Official positions and league minimums (section 11). Official position is permanent."""
POSITIONS = ("K", "P", "QB", "HB", "FB", "WR", "TE", "LT", "LG", "C", "RG", "RT", "LE", "RE", "DT",
             "ROLB", "MLB", "LOLB", "CB", "FS", "SS", "LS")
OL = ("LT", "LG", "C", "RG", "RT")
MINIMUMS = {"K": 1, "P": 1, "QB": 3, "WR": 5, "TE": 2, "LT": 2, "LG": 2, "C": 1, "RG": 2, "RT": 2, "LE": 2, "RE": 2,
            "DT": 3, "ROLB": 2, "MLB": 3, "LOLB": 2, "CB": 5, "FS": 2, "SS": 2, "LS": 1}
BACKFIELD = ("HB", "FB")          # three HB, or two HB and one FB
assert sum(MINIMUMS.values()) + 3 == 48


def shortfalls(counts):
    """{position: places short} for a count of medically available players by position.
    The backfield is reported under 'HB' (HB 3, or HB 2 plus FB 1)."""
    out = {p: MINIMUMS[p] - counts.get(p, 0) for p in MINIMUMS if counts.get(p, 0) < MINIMUMS[p]}
    hb, fb = counts.get("HB", 0), counts.get("FB", 0)
    if not (hb >= 3 or (hb >= 2 and fb >= 1)):
        out["HB"] = 3 - hb if hb < 2 else 1
    return out
