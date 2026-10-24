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


def curve(tables, delta):
    c = tables.age_curve
    return c[max(min(delta, max(c)), min(c))]


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
    ratio = curve(tables, age - peak) / curve(tables, age - 1 - peak)
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
