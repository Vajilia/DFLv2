"""Shared fixtures for the website tests: one placeholder-resolver league stepped through the stages a viewer can see, with the public feed built
at each stage. Built once per test process (about a minute and a half)."""
import copy
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl import persist  # noqa: E402
from dfl.year import Universe  # noqa: E402
from dflsite import feed  # noqa: E402

TABLES = os.path.join(ROOT, "data", "season1")
SEED = 777123            # six digits, so the secret-value check applies to it
STAMP = "2026-01-01T00:00:00+00:00"
LEAGUE = "0123456789abcdef0123456789abcdef"
_cache = {}


def run_to(u, key, year=None):
    while not (u.fired and u.fired[-1][2] == key and (year is None or u.fired[-1][0] == year)):
        u.step_event()


def build(u):
    return feed.build(u, LEAGUE, "Test League", engine="test", generated_at=STAMP)


def stages():
    """-> (universe at the end, {stage name: built feed}). Stage names: start, camp, w6, reg_end, wild_card, bowl, coronation, closed, next_year."""
    if "s" in _cache:
        if isinstance(_cache["s"], Exception):
            raise _cache["s"]
        return _cache["s"]
    try:
        _cache["s"] = _build_stages()
    except Exception as e:                      # fail once, quickly, for every test that needs the stages
        _cache["s"] = e
        raise
    return _cache["s"]


def _build_stages():
    u = Universe(TABLES, seed=SEED, engine=False)
    out = {"start": build(u)}
    snaps = {}
    for name, key, year in (("camp", "camp_open", 2000), ("w6", "week_6", 2000), ("reg_end", "week_19", 2000), ("wild_card", "wild_card", 2000),
                            ("bowl", "ambassador_bowl", 2000), ("coronation", "coronation", 2000), ("closed", "year_close", 2000),
                            ("next_year", "offseason_open", 2001)):
        run_to(u, key, year)
        out[name] = build(u)
        if name == "bowl":
            snaps[name] = persist.loads(persist.dumps(u)[0])           # an independent copy to poison without touching the main league
        if name == "w6":
            snaps[name] = persist.loads(persist.dumps(u)[0])
    return (u, out, snaps)


def poisoned(u):
    """A copy of the league in which everything hidden has been scrambled: ratings, attributes, growth, traits, memories, relationships, the
    Bowl ballots, honours, the draft order. A feed that reads none of it is unchanged."""
    w = u.world
    for p in w.L.players.values():
        p.ovr = 1
    for c in w.cards.values():
        for k in c.attrs:
            c.attrs[k] = 1
        for k in list(c.growth):
            c.growth[k] = 0
        for k in c.traits:
            c.traits[k] = 100 - c.traits[k]
        c.memory.append({"poison": "ZZSECRETZZ"})
        c.relations["ZZSECRETZZ"] = {"poison": 1}
        c.soul["poison"] = "ZZSECRETZZ"
    if w.bowl_vote:
        for conf in w.bowl_vote["conferences"].values():
            for v in conf["ballots"]:
                for k, b in conf["ballots"][v].items():
                    conf["ballots"][v][k] = list(reversed(b))
    w.rings = {"ZZSECRETZZ": 99}
    w.tiaras = {"ZZSECRETZZ": 99}
    if u.draft_order:
        u.draft_order = list(reversed(u.draft_order))
    return u
