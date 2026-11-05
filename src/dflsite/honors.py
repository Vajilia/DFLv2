"""The Honors show's data: only what the league has actually RECORDED, and a plain statement of what it has not.

The engine records (src/dfl/ledger/honours.py, Edition 8.30 and 8.58):
  * Tour Rings      at the Ambassador Bowl: the winning side's final 53 who were present and medically ready at kickoff, and the four Bowl coaches
  * Tiaras          at the year close: the Coronation winner's final 53 and its CEO, general manager, head coach and three coordinators
  * the 59 halls    at the year close: a player is eligible with a Tiara and five credited seasons, so in a league's first years the registers are legitimately empty
  * a fan entrant   at the year close: one league-hall entrant a year, by ranked choice among eligible players (None while nobody is eligible)

It records NO season MVP, All-League team, rookie or coach award, statistical award or staff hall; those are listed as unavailable (season.honors_unavailable) and are
never made up. Hall values (season scores) are internal analytics and are not published.

`build_part(universe, part, season, teams)` reads a finished league (the builder opens a private COPY of it) and returns one public document; `check_public`
refuses any key outside this module's allowlists, so a field added to the engine's records cannot reach the public by accident. Names and positions are the
league's own public ones (rosters); player ids are the roster ids; staff carry no ids.
"""
import re

FORMAT = "dfl-broadcast-honors"
VERSION = 1
PARTS = ("rings", "tiara", "halls", "fan_entrant")
ROLE_NAMES = {"ceo": "CEO", "gm": "General manager", "hc": "Head coach", "oc": "Offensive coordinator", "dc": "Defensive coordinator", "stc": "Special teams coordinator"}

ALLOWED = {
    "doc": {"format", "format_version", "kind", "season", "year", "status", "recorded_at", "note", "side", "club", "recipients", "counts", "rule", "halls",
            "entered_this_year", "entrant", "unavailable", "eligible_players"},
    "person": {"id", "name", "pos", "role", "club"},
    "hall": {"scope", "registers", "ever_members"},
    "scope": {"level", "conference", "division", "club"},
    "entered": {"scope", "pos", "id", "name", "kind"},
    "counts": {"players", "staff"},
    "unavailable": {"field", "reason"},
}


class HonorsError(Exception):
    pass


def check_public(doc):
    """Raise HonorsError for any key outside the allowlists."""
    def keys(obj, kind, path):
        extra = set(obj) - ALLOWED[kind]
        if extra:
            raise HonorsError("%s carries keys outside the honors schema: %s" % (path, sorted(extra)))
    if doc.get("format") != FORMAT or doc.get("format_version") != VERSION or doc.get("kind") not in PARTS:
        raise HonorsError("not an honors part")
    keys(doc, "doc", "document")
    for p in doc.get("recipients", []) + ([doc["entrant"]] if doc.get("entrant") else []):
        keys(p, "person", "person")
    for h in doc.get("halls", []):
        keys(h, "hall", "hall")
        keys(h["scope"], "scope", "scope")
        for rows in h["registers"].values():
            for p in rows:
                keys(p, "person", "register entry")
    for e in doc.get("entered_this_year", []):
        keys(e, "entered", "entry")
        keys(e["scope"], "scope", "scope")
    if doc.get("counts"):
        keys(doc["counts"], "counts", "counts")
    for u in doc.get("unavailable", []):
        keys(u, "unavailable", "unavailable")
    for txt in _strings(doc):
        if re.search(r"\b(seed|rating|true overall|hidden|ballot)\b", txt, re.I):
            raise HonorsError("an honors document mentions a private concept: %r" % txt[:60])


def _strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from _strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from _strings(v)


# ---------------------------------------------------------------------------------------------------------------------------------- reading the league
class _Lookup:
    def __init__(self, universe, teams):
        self.w = universe.world
        self.teams = teams
        self.tpl = universe.tpl
        self.club_of = {}
        for club, c in self.w.L.clubs.items():
            for pid in c.roster:
                self.club_of[pid] = club
        self.staff = {}
        for club, roles in self.w.leaders.items():
            for role, card in roles.items():
                if card is not None:
                    self.staff[card.identity.id] = (club, role, card)

    def abbr(self, club):
        t = self.teams.get(club)
        return t["abbr"] if t else None

    def person(self, pid):
        w = self.w
        if pid in self.staff:
            club, role, card = self.staff[pid]
            return {"name": "%s %s" % (card.identity.first, card.identity.last), "role": ROLE_NAMES.get(role, role), "club": self.abbr(club)}
        c = w.cards.get(pid)
        if c is not None:
            club = self.club_of.get(pid)
            return {"id": int(pid), "name": "%s %s" % (c.identity.first, c.identity.last), "pos": c.identity.pos, "club": self.abbr(club) if club else None}
        if pid in w.persona:
            name, pos = w.persona[pid]
            return {"id": int(pid), "name": name, "pos": pos, "club": None}
        return {"id": int(pid), "name": None, "pos": None, "club": None}


def _sorted(people):
    return sorted(people, key=lambda p: (p.get("role") is not None, p.get("pos") or "", p.get("name") or ""))


def _base(kind, season, year, status, note, **extra):
    d = {"format": FORMAT, "format_version": VERSION, "kind": kind, "season": season, "year": year, "status": status, "note": note}
    d.update(extra)
    return d


def build_part(universe, part, season, teams, rules=None):
    """One public honors document from the finished league `universe` (a private copy opened read-only in effect). `teams`: {city: {"abbr", "name"}}."""
    if part not in PARTS:
        raise HonorsError("unknown part " + part)
    w = universe.world
    look = _Lookup(universe, teams)
    rules = rules or {}
    if part == "rings":
        return _rings(w, look, season, teams, universe)
    if part == "tiara":
        return _tiara(w, look, season, universe)
    if part == "halls":
        return _halls(w, look, season, universe, rules)
    return _fan(w, look, season, universe, rules)


def _latest(table):
    years = [y for ys in table.values() for y in ys]
    return max(years) if years else None


def _rings(w, look, season, teams, universe):
    year = _latest(w.rings)
    if year is None:
        return _base("rings", season, None, "not_recorded", "No Tour Ring is recorded for this season.", recipients=[], counts={"players": 0, "staff": 0},
                     unavailable=[{"field": "recipients", "reason": "no ring is recorded in the league"}])
    pids = sorted(p for p, ys in w.rings.items() if year in ys)
    people = [look.person(p) for p in pids]
    side = None
    for conf, rec in sorted(w.bowl_final.items()):
        if sum(1 for p in pids if p in set(rec["roster"])) > len(pids) // 2:
            side = "%s All-Stars" % conf
    players = _sorted([p for p in people if "id" in p])
    staff = _sorted([p for p in people if "id" not in p])
    return _base("rings", season, year, "recorded", "Tour Rings given at the Ambassador Bowl to the winning side's final 53 who were present and medically ready at kickoff, "
                 "and the four Bowl coaches.", side=side, recipients=players + staff, counts={"players": len(players), "staff": len(staff)})


def _tiara(w, look, season, universe):
    year = _latest(w.tiaras)
    if year is None:
        return _base("tiara", season, None, "not_recorded", "No Tiara is recorded for this season.", recipients=[], counts={"players": 0, "staff": 0},
                     unavailable=[{"field": "recipients", "reason": "no Tiara is recorded in the league"}])
    pids = sorted(p for p, ys in w.tiaras.items() if year in ys)
    people = [look.person(p) for p in pids]
    players = _sorted([p for p in people if "id" in p])
    staff = [p for p in people if "id" not in p]
    order = list(ROLE_NAMES.values())
    staff.sort(key=lambda p: order.index(p["role"]) if p["role"] in order else 99)
    club = None
    clubs = {p["club"] for p in staff if p.get("club")}
    if len(clubs) == 1:
        club = next(iter(clubs))
    return _base("tiara", season, year, "recorded", "Tiaras given to the Diamond Coronation winner's final 53 and its CEO, general manager, head coach and three coordinators.",
                 club=club, recipients=players + staff, counts={"players": len(players), "staff": len(staff)})


def _scope(look, scope):
    kind = scope[0]
    if kind == "league":
        return {"level": "league"}
    if kind == "conference":
        return {"level": "conference", "conference": scope[1]}
    if kind == "division":
        return {"level": "division", "conference": scope[1], "division": scope[2]}
    return {"level": "club", "club": look.abbr(scope[1])}


def _halls(w, look, season, universe, rules):
    year = _latest(w.tiaras)
    need = rules.get("hall_min_seasons")
    rule = ("A player is eligible for the halls with a Tiara and %s credited seasons (a season with an active-list or Tour appearance counts once)."
            % (int(need) if need is not None else "the required number of"))
    eligible = sum(1 for pid in w.career if w.tiaras.get(pid) and len(w.credited.get(pid, ())) >= (need or 5))
    halls = []
    for scope in sorted(w.hall["reg"], key=lambda s: (s[0], tuple(str(x) for x in s))):
        regs = {pos: [look.person(p) for p in pids] for pos, pids in sorted(w.hall["reg"][scope].items()) if pids}
        if not regs and not w.hall["ever"].get(scope):
            continue
        halls.append({"scope": _scope(look, scope), "registers": regs, "ever_members": len(w.hall["ever"].get(scope, ()))})
    entered = [{"scope": _scope(look, e["scope"]), "pos": e["pos"], "id": int(e["pid"]), "name": look.person(e["pid"])["name"], "kind": e["kind"]}
               for e in w.hall["log"] if e["year"] == year and e["kind"] in ("entered", "fan")] if year is not None else []
    status = "recorded" if year is not None else "not_recorded"
    note = ("%d halls hold a register." % len(halls)) if halls else "No hall holds a member yet: " + rule[0].lower() + rule[1:]
    return _base("halls", season, year, status, note, rule=rule, halls=halls, entered_this_year=entered, eligible_players=eligible,
                 unavailable=[{"field": "hall values", "reason": "season scores are internal analytics and are not published"},
                              {"field": "staff halls", "reason": "staff hold Tiaras but have no halls yet"}])


def _fan(w, look, season, universe, rules):
    year = _latest(w.tiaras)
    if not w.hall["fan"] or year is None:
        return _base("fan_entrant", season, year, "none", "No fan entrant this season: the ranked-choice vote needs an eligible player (a Tiara and the required credited seasons) "
                     "who is not already in the league hall.", entrant=None)
    last = [e for e in w.hall["log"] if e["kind"] == "fan"]
    if not last or last[-1]["year"] != year:
        return _base("fan_entrant", season, year, "none", "No fan entrant was recorded for this season.", entrant=None)
    p = look.person(last[-1]["pid"])
    return _base("fan_entrant", season, year, "recorded", "The league hall's fan entrant, chosen by single-winner ranked choice among eligible players.", entrant=p,
                 unavailable=[{"field": "fan vote counts", "reason": "the fan vote tallies are not published"}])
