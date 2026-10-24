"""The soul: a fixed archetype, a self-view, and bounded drift of the six traits (Edition 8.22).

Everything numeric is data (data/characters/ARCHETYPES.csv, SOUL.csv) and is a PLACEHOLDER until the owner rules on the list.

  * Archetype: the nearest archetype centre to the traits she was created with. Fixed for life; it is never re-judged.
  * Self-view: how good she thinks she is. Her true overall plus her archetype's self-bias (a competitor overrates herself, a
    loyalist is modest). Negotiation (slice 5.3) reads it; nothing else does yet.
  * Drift: once a year each trait closes a share of the gap to her archetype's centre, plus a little noise, and never moves more
    than `drift_bound` from the value she was created with. So a soul can change a little with the years but cannot become another soul.
"""
from .creator import clip

TRAITS = ("AMB", "LOY", "WRK", "TMP", "GRE", "LDR")


def archetype_of(traits, tables):
    return min(sorted(tables.archetypes), key=lambda a: sum((traits[k] - tables.archetypes[a]["centre"][k]) ** 2 for k in TRAITS))


def assign(card, tables):
    """Give a card her soul if she has none (idempotent: a soul, once given, is never changed)."""
    if not card.soul:
        card.soul = {"archetype": archetype_of(card.traits, tables), "base": {k: card.traits[k] for k in TRAITS}}
    return card.soul


def self_view(card, tables):
    """What she believes her overall is."""
    return card.true_overall(tables) + tables.archetypes[assign(card, tables)["archetype"]]["self_bias"]


def drift(card, rng, tables):
    """Move the traits one year. Returns nothing; changes card.traits in place, always within the bound."""
    S = tables.soul
    soul = assign(card, tables)
    centre = tables.archetypes[soul["archetype"]]["centre"]
    for k in TRAITS:
        base = soul["base"][k]
        v = card.traits[k] + S["drift_pull"] * (centre[k] - card.traits[k]) + rng.gauss(0, S["drift_noise"])
        lo, hi = max(0, base - S["drift_bound"]), min(100, base + S["drift_bound"])
        card.traits[k] = clip(min(hi, max(lo, v)), 0, 100)
