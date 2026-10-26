"""Yearly development, aging and retirement of a character card.

Everything numeric here is data (data/characters/DEVELOPMENT.csv and AGE_CURVE.csv) and is a PLACEHOLDER until the owner rules on the
curves (spec section 20, item 3). The shape, though, is the design:

  * A card was created at some age with her attributes already scaled by the age curve at (age - her own peak age).  One year later
    each attribute is multiplied by the ratio of the curve at the new age to the curve at the old age.  So a player reaches her
    created potential at her own peak and declines after it, and every year of the card's life follows the same curve it was born on.
  * How hard an attribute follows the curve depends on its kind: physical (core) fully, learned (skill) mostly, mental little.
  * Before her peak she also gains her hidden `ceiling` (points still to come), spread over the years left to peak.
  * Work ethic speeds growth and slows decline.  A personal volatility scales the yearly noise.
  * Retirement is a decision about a free agent: the owner's pool rule (four years unsigned) is separate and unchanged.
"""
import math

from .creator import clip


def curve(tables, delta, decline=1.0):
    """The age multiplier at `delta` years from a player's own peak. `decline` (a position's, Edition 8.38) stretches how far it falls
    below 1 after the peak: a running back falls faster than a long snapper. Before the peak it is unchanged."""
    c = tables.age_curve
    m = c[max(min(delta, max(c)), min(c))]
    return max(0.3, 1 - decline * (1 - m)) if delta > 0 and m < 1 else m


def kind_of(tables, attr):
    return tables.attrs[attr]["kind"] if attr in tables.attrs else "skill"


def develop(card, year, rng, tables, coach=0.0):
    """Advance one card by one year. `year` is the year she turns (her new age is year - birth_year). Returns the change in true overall.
    `coach` is the club's teaching effect in attribute points (already capped by the caller); it is scaled by attribute kind."""
    if card.identity.role != "player":
        return 0.0
    D = tables.development
    before = card.true_overall(tables)
    age = card.age(year)
    peak = card.growth["peak_age"]
    dec = tables.positions[card.identity.pos]["decline"]
    ratio = curve(tables, age - peak, dec) / curve(tables, age - 1 - peak, dec)
    wrk = (card.traits["WRK"] - 50) / 50.0
    vol = card.growth["volatility"]
    gain = 0.0
    if age <= peak and card.growth["ceiling"] > 0:
        gain = min(card.growth["ceiling"], card.growth["ceiling"] / max(1, peak - age + 1))
        card.growth["ceiling"] = round(card.growth["ceiling"] - gain, 3)
    for a, v in list(card.attrs.items()):
        kind = kind_of(tables, a)
        r = 1 + D["scale_" + kind] * (ratio - 1)
        if r >= 1:
            r = 1 + (r - 1) * (1 + D["wrk_effect"] * wrk)
        else:
            r = 1 - (1 - r) * (1 - D["wrk_effect"] * wrk)
        noise = rng.gauss(0, D["noise_" + kind] * vol)
        card.attrs[a] = clip(v * r + gain * (1 + D["wrk_effect"] * wrk) + noise + coach * D["scale_" + kind])
    return card.true_overall(tables) - before


def retire_chance_contracted(card, year, tables):
    """Probability that a player retires from wear at this year turn, whether or not she is still under contract (Edition 8.38): wear, injury and life take players out
    of the game at every age, more as the years pass and more at the positions that decline fastest. `retire_c_p_base` plus
    `retire_c_p_step` for each year past `retire_c_age_start`, times her position's decline rate; at the hard age she retires. Her
    contract ends with her and leaves no dead money (the NFL treatment of a retiring player's unpaid salary)."""
    if card.identity.role != "player":
        return 0.0
    D = tables.development
    age = card.age(year)
    if age >= D["retire_hard_age"]:
        return 1.0
    if age < D["retire_c_age_start"]:
        return 0.0
    wear = D["retire_c_p_base"] + D["retire_c_p_step"] * (age - D["retire_c_age_start"])
    return min(1.0, wear * tables.positions[card.identity.pos]["decline"])


def retire_chance(card, year, overall, tables):
    """Probability that this card retires at this year turn if she is a free agent."""
    if card.identity.role != "player":
        return 0.0
    D = tables.development
    age = card.age(year)
    if age >= D["retire_hard_age"]:
        return 1.0
    past = age - card.growth["peak_age"] - D["retire_after_peak"]
    if past < 0:
        return 0.0
    p = D["retire_p_base"] + D["retire_p_step"] * past
    if overall < D["retire_floor_overall"]:
        p = max(p, D["retire_floor_p"])
    return min(1.0, p)
