"""The public feed: what a viewer of the league may see, as plain versioned JSON (format `dfl-public-feed`, version 1).

`build(universe, ...)` reads a Universe whose state is exactly its COMMITTED state (the exporter opens a copy of the league file at its last
committed event) and returns {file path: JSON-able value}. It never advances, saves or alters the league, and it takes nothing from a request:
the website only ever reads files this module produced.

Public (spec section 1: "the user is a pure viewer: no control, no secret information"): the calendar and what has been done on it, teams (names,
colours, grounds), schedules and the results already played, standings, the playoff bracket as it stands, public rosters (the "public facts" of
spec section 11), the history of seasons finished in this league, and the approved rules.
Never exported (and checked on every build by `check_public`): hidden attributes and ratings, the true overall or any estimate of it, growth,
traits, memories and relationships, private valuations, the league seed or any random draw, the Bowl ballots and counts, the draft order and
lottery, awards, rings and halls, and the result of any event that has not been committed.

Every record type has an exact allowlist of fields (SCHEMAS). A field that is not on the list fails the build, so adding information to the feed
is always a deliberate edit of this file and of `docs/PUBLIC_FEED_FORMAT.md`.
"""
import csv
import datetime as dt
import hashlib
import json
import os
import re

from dfl import calendar as C
from dfl import league, standings
from dfl.ledger import trading

FORMAT = "dfl-public-feed"
FORMAT_VERSION = 1          # the major version: a reader that does not know it must refuse the feed
FORMAT_MINOR = 0            # additions that older v1 readers may ignore

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RULES_SUMMARY = os.path.join(ROOT, "docs", "public", "RULES_SUMMARY.md")
SPEC_DIR = os.path.join(ROOT, "docs", "spec")

EVENT_LABELS = {
    "offseason_open": "League year turns", "hiring_open": "Leadership hiring opens", "hiring_close": "Leadership hiring closes",
    "trading_open": "Trading opens", "free_agency_open": "Free agency opens", "draft_round_1": "Draft, round 1",
    "draft_rounds_2_3": "Draft, rounds 2 and 3", "draft_rounds_4_7": "Draft, rounds 4 to 7", "camp_open": "Training camp opens",
    "final_cuts": "Final cuts", "waivers_clear": "Waivers clear", "trade_close": "Trading closes", "wild_card": "Wild Card round",
    "divisional": "Divisional round", "conference_championships": "Conference Championships", "ambassador_bowl": "Ambassador Bowl",
    "coronation": "Diamond Coronation", "year_close": "League year closes",
}
ROUNDS = (("wild_card", "Wild Card", "wild card round", 2), ("divisional", "Divisional", "divisional round", 2),
          ("conference", "Conference Championship", "conference championships", 1))
SCHEDULE_FROM = "camp_open"        # the schedule is shown from the day camp opens (and always once the season has started)

DEVELOPMENT_LABEL = ("DEVELOPMENT / TEST LEAGUE. This league is run to test the simulation as it is built. Its teams, players, scores and history "
                     "are not final and may be reset whenever the rules or the simulation change.")
NOTICES = [
    {"id": "dev-league", "level": "important", "text": DEVELOPMENT_LABEL},
    {"id": "event-reveal", "level": "info",
     "text": "Results appear when the league commits an event: a whole week's games show together once the week has been played. The "
             "real-time clock that reveals each game at its own kickoff is not built yet."},
    {"id": "no-hidden", "level": "info",
     "text": "Players' ratings, coaches' views and club deliberations are not published. Rosters show only public facts."},
    {"id": "not-built", "level": "info",
     "text": "Not built yet: the live game view and play-by-play, player and team statistics, box scores, the draft pages, a transactions feed, "
             "awards and halls, and the media coverage."},
    {"id": "history-scope", "level": "info",
     "text": "History covers only the seasons played in this development league so far. Earlier Diamond Coronation scores are not yet "
             "recorded in the league's season reports, so only the latest season shows one."},
    {"id": "bowl", "level": "info", "text": "The Ambassador Bowl rosters and result appear after the game. How they were chosen is not published."},
    {"id": "rules-pending", "level": "info",
     "text": "Some rule details still await the owner's ruling; the Rules page lists only what is approved."},
    {"id": "map", "level": "info", "text": "The official DFL map has not been added to this preview yet."},
]


# ------------------------------------------------------------------------------------------------------------------- schema allowlists
class Opt:
    def __init__(self, shape):
        self.shape = shape


class Many:
    def __init__(self, shape):
        self.shape = shape


class Map:
    def __init__(self, shape):
        self.shape = shape


def check_shape(shape, value, path, errs):
    """Append to errs every place where `value` does not match `shape` exactly (unknown or missing keys are errors)."""
    if isinstance(shape, Opt):
        if value is not None:
            check_shape(shape.shape, value, path, errs)
    elif isinstance(shape, Many):
        if not isinstance(value, list):
            errs.append(f"{path}: expected a list")
        else:
            for i, v in enumerate(value):
                check_shape(shape.shape, v, f"{path}[{i}]", errs)
    elif isinstance(shape, Map):
        if not isinstance(value, dict):
            errs.append(f"{path}: expected an object")
        else:
            for k, v in value.items():
                if not isinstance(k, str):
                    errs.append(f"{path}: non-string key {k!r}")
                check_shape(shape.shape, v, f"{path}.{k}", errs)
    elif isinstance(shape, dict):
        if not isinstance(value, dict):
            errs.append(f"{path}: expected an object")
            return
        for k in value:
            if k not in shape:
                errs.append(f"{path}: field {k!r} is not in the public schema")
        for k, s in shape.items():
            if k not in value:
                if not isinstance(s, Opt):
                    errs.append(f"{path}: missing field {k!r}")
            else:
                check_shape(s, value[k], f"{path}.{k}", errs)
    elif shape is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errs.append(f"{path}: expected a number, got {type(value).__name__}")
    elif shape is int:
        if isinstance(value, bool) or not isinstance(value, int):
            errs.append(f"{path}: expected an integer, got {type(value).__name__}")
    elif shape is bool:
        if not isinstance(value, bool):
            errs.append(f"{path}: expected true/false")
    elif shape is str:
        if not isinstance(value, str):
            errs.append(f"{path}: expected a string, got {type(value).__name__}")
    else:
        errs.append(f"{path}: bad schema entry {shape!r}")


EVENT = {"index": int, "key": str, "label": str, "date": str}
GAME = {"id": str, "kind": str, "week": Opt(int), "round": Opt(str), "date": Opt(str), "kickoff": Opt(str), "away": str, "home": str,
        "status": str, "away_score": Opt(int), "home_score": Opt(int), "venue": Opt(str), "note": Opt(str)}
PLAYOFF_GAME = dict(GAME, away_seed=Opt(int), home_seed=Opt(int))
CLUB_LINE = {"abbr": str, "w": int, "l": int, "t": int, "pct": float, "pf": int, "pa": int, "pd": int, "div": str, "conf": str, "rank": int}
PLAYER = {"id": int, "name": str, "pos": str, "number": Opt(int), "age": int, "seasons": int, "height_cm": int, "weight_kg": int, "slot": str,
          "injury": {"out": bool, "weeks_out": int}, "contract": {"cap_hit": float, "years_left": int}}
BOWL_PLAYER = {"id": int, "name": str, "pos": str, "club": Opt(str)}
BOWL = {"status": str, "game": GAME, "winner": Opt(str), "rosters": Map(Many(BOWL_PLAYER)), "coach_clubs": Map(str)}

SCHEMAS = {
    "manifest": {
        "format": str, "format_version": int, "format_minor": int,
        "league": {"id": str, "name": str, "kind": str, "engine": str, "label": str},
        "release": str, "generated_at": str, "content_hash": str,
        "season": Opt(int), "year": Opt(int), "simulation_date": Opt(str), "last_event": Opt(EVENT), "events_done": int, "events_total": int,
        "files": Map({"path": str, "sha256": str, "bytes": int}),
    },
    "season": {
        "season": int, "year": int, "phase": str, "status_text": str, "simulation_date": Opt(str), "last_event": Opt(EVENT),
        "next_event": Opt(EVENT), "events_done": int, "events_total": int, "regular_weeks": {"total": int, "played": int},
        "schedule_published": bool, "regular_season_complete": bool, "postseason_started": bool, "complete": bool, "champion": Opt(str),
    },
    "calendar": {"year": int, "events": Many({"index": int, "date": str, "key": str, "label": str, "phase": str, "status": str, "note": str})},
    "notices": {"development_label": str, "notices": Many({"id": str, "level": str, "text": str})},
    "teams": {"year": int, "teams": Many({
        "id": int, "abbr": str, "city": str, "name": str, "full_name": str, "conference": str, "division": str, "status": str,
        "venue": str, "location": str, "colors": Many({"name": str, "hex": str})})},
    "schedule": {"year": int, "games": Many(GAME)},
    "standings": {
        "year": int, "through_week": int,
        "divisions": Many({"conference": str, "division": str, "clubs": Many(dict(CLUB_LINE, exiled=bool))}),
        "ambassadors": Many({"conference": str, "clubs": Many(CLUB_LINE)}),
        "designation": Opt({"through_week": int, "home_conference": str, "decided_by": str,
                            "conferences": Map({"w": int, "l": int, "t": int, "pf": int, "pa": int, "pd": int})}),
    },
    "playoffs": {
        "year": int, "status": str, "seeds": Map(Many({"abbr": str, "playoff_seed": int})),
        "rounds": Many({"name": str, "label": str, "conferences": Map(Many(PLAYOFF_GAME))}),
        "coronation": Opt(PLAYOFF_GAME), "coronation_home_conference": Opt(str), "champion": Opt(str), "bowl": Opt(BOWL),
    },
    "history": {"seasons": Many({
        "season": int, "year": int, "champion": str, "runner_up": str, "score": Opt(str), "venue": str,
        "bowl_winner": Opt(str), "bowl_score": Opt(str), "exiled": Many(str),
        "division_winners": Many({"conference": str, "division": str, "abbr": str}), "seeds": Map(Many(str)),
        "standings": Many({"conference": str, "division": str, "clubs": Many(CLUB_LINE)}),
        "playoff_games": Many(PLAYOFF_GAME)})},
    "venues": {"venues": Many({"id": str, "name": str, "city": str, "country": str})},
    "rules": {"spec_edition": str, "spec_hash": str, "updated": str, "sections": Many({"title": str, "paragraphs": Many(str)})},
    "roster": {"abbr": str, "full_name": str, "year": int, "cap": {"used": float, "limit": float},
               "leaders": Many({"role": str, "name": str, "age": int}), "players": Many(PLAYER)},
}

# Names that must never appear as a key anywhere in a public file, whatever the schema says (a second, independent net).
FORBIDDEN_KEYS = {
    "attrs", "attributes", "growth", "traits", "memory", "memories", "relations", "soul", "rating", "ratings", "ovr", "overall", "true_overall",
    "potential", "ceiling", "seed", "rng", "draw", "motivation", "motive", "ballot", "ballots", "votes", "judged", "view", "valuation", "market",
    "draft_order", "lottery", "awards", "tiaras", "rings", "halls", "look", "private", "hidden", "digest", "fingerprint", "volatility",
}
FORBIDDEN_TEXT = re.compile(r"\b(true overall|hidden rating|private motivation|ballot)\b", re.I)
EXEMPT_TEXT = ("notices.json", "rules.json")          # prose written by hand, which may name what is withheld


def kind_of(name):
    return "roster" if name.startswith("rosters/") else name.rsplit(".", 1)[0]


def check_public(files, secrets=()):
    """-> list of problems in a built feed (empty when sound): schema allowlist per file, forbidden keys anywhere, forbidden words in text,
    and none of `secrets` (strings such as the league seed) anywhere in any file."""
    errs = []

    def walk(o, path, exempt):
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(k, str) and k.lower() in FORBIDDEN_KEYS:
                    errs.append(f"{path}.{k}: forbidden key")
                walk(v, f"{path}.{k}", exempt)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{path}[{i}]", exempt)
        elif isinstance(o, str) and not exempt and FORBIDDEN_TEXT.search(o):
            errs.append(f"{path}: forbidden wording {o[:40]!r}")
    for name, value in sorted(files.items()):
        kind = kind_of(name)
        if kind not in SCHEMAS:
            errs.append(f"{name}: not a file of the public format")
            continue
        check_shape(SCHEMAS[kind], value, name, errs)
        walk(value, name, name in EXEMPT_TEXT)
        if secrets:
            text = canonical(value)
            for s in secrets:
                if s and s in text:
                    errs.append(f"{name}: contains a withheld value")
    return errs


# ------------------------------------------------------------------------------------------------------------------- helpers
def canonical(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _label(key):
    m = re.fullmatch(r"week_(\d+)", key)
    if m:
        return f"Week {int(m.group(1))}"
    m = re.fullmatch(r"break_(\d+)", key)
    if m:
        return f"Break {int(m.group(1))}"
    m = re.fullmatch(r"tour_round_(\d+)", key)
    if m:
        return f"Ambassador Tour, round {int(m.group(1))}"
    return EVENT_LABELS.get(key, key.replace("_", " ").capitalize())


def _event(i, d, key):
    return {"index": i, "key": key, "label": _label(key), "date": d if isinstance(d, str) else d.isoformat()}


def _cal(year):
    """{note: sunday} for the postseason and Ambassador rounds of `year`."""
    return {r["note"]: r["sunday"] for r in C.season_weeks(year) if r["phase"] in ("postseason", "ambassadors")}


def _venues(u):
    with open(os.path.join(u.tpl.dir, "INTL_VENUES.csv"), encoding="utf-8", newline="") as f:
        return {r["venue_id"]: r for r in csv.DictReader(f)}


def _venue_text(venues, vid):
    v = venues.get(vid)
    return f'{v["venue_name"]}, {v["city"]}' if v else None


def _season_number(u):
    return max(1, u.season)


def _teams_rows(u):
    return u.run.teams if u.run is not None else league.season_tables(u.tpl, u.state, _season_number(u))[0]


def _line(abbr, r, rank):
    return {"abbr": abbr, "w": r.w, "l": r.l, "t": r.t, "pct": round(float(standings.pct(r.w, r.l)), 4), "pf": r.pf, "pa": r.pa, "pd": r.pd,
            "div": f"{r.dw}-{r.dl}", "conf": f"{r.cw}-{r.cl}", "rank": rank}


def spec_edition():
    """(edition, content hash) of the newest spec file in docs/spec, read from its own header."""
    names = [n for n in os.listdir(SPEC_DIR) if re.fullmatch(r"DFL_Design_Document_Ed[\d.]+\.md", n)]
    names.sort(key=lambda n: [int(x) for x in re.findall(r"\d+", n)])
    with open(os.path.join(SPEC_DIR, names[-1]), encoding="utf-8") as f:
        head = f.read(4000)
    return re.search(r"\*\*Edition ([\d.]+)", head).group(1), re.search(r"`([0-9a-f]{64})`", head).group(1)


def rules_text():
    with open(RULES_SUMMARY, encoding="utf-8") as f:
        return f.read()


def rules_file():
    """The curated public summary of the approved rules (docs/public/RULES_SUMMARY.md), tied to the spec edition and hash it was checked against."""
    raw = rules_text()
    _, meta_text, body = raw.split("---", 2)
    meta = dict(re.findall(r"^(\w+): (.+)$", meta_text, flags=re.M))
    sections, cur = [], None
    for line in body.split("\n"):
        if line.startswith("## "):
            cur = {"title": line[3:].strip(), "paragraphs": []}
            sections.append(cur)
        elif line.strip() and cur is not None:
            cur["paragraphs"].append(line.strip())
    return {"spec_edition": meta["spec_edition"], "spec_hash": meta["spec_hash"], "updated": meta["updated"], "sections": sections}


# ------------------------------------------------------------------------------------------------------------------- the pieces
def season_file(u, abbr):
    year = u.cursor[0] if u.cursor else (u.fired[-1][0] if u.fired else league.FOUNDING_YEAR)
    in_year = [f for f in u.fired if f[0] == year]
    keys = {f[2] for f in in_year}
    events = C.year_events(year)
    last = _event(len(in_year), in_year[-1][1], in_year[-1][2]) if in_year else None
    nxt = _event(len(in_year) + 1, events[len(in_year)][0], events[len(in_year)][1]) if len(in_year) < len(events) else None
    run = u.run if (u.run is not None and u.run.year == year) else None
    if run is not None:
        weeks = set(run.week_rows)
    elif SCHEDULE_FROM in keys:
        weeks = {int(r["week"]) for r in league.season_tables(u.tpl, u.state, _season_number(u))[1]}
    else:
        weeks = set()
    reg_total = len(weeks)
    reg_played = sum(1 for k in keys if re.fullmatch(r"week_\d+", k))
    complete = "year_close" in keys
    post = "wild_card" in keys
    phase = "complete" if complete else "postseason" if post else "regular season" if "week_1" in keys else "offseason"
    texts = {"offseason": "Offseason: the league year is under way.",
             "regular season": f"Regular season: {reg_played} of {reg_total} weeks played." if reg_total else "Regular season under way.",
             "postseason": "Postseason under way.", "complete": f"The {year} league year is complete."}
    champ = None
    if run is not None and run.coronation:
        champ = abbr[run.coronation["winner"]]
    return {"season": _season_number(u), "year": year, "phase": phase, "status_text": texts[phase],
            "simulation_date": in_year[-1][1] if in_year else None, "last_event": last, "next_event": nxt, "events_done": len(in_year),
            "events_total": len(events), "regular_weeks": {"total": reg_total, "played": reg_played},
            "schedule_published": run is not None or SCHEDULE_FROM in keys, "regular_season_complete": run is not None and run.outcome is not None,
            "postseason_started": post, "complete": complete, "champion": champ}


def calendar_file(u, year):
    done = {(f[0], f[2]) for f in u.fired}
    return {"year": year, "events": [
        {"index": i, "date": d.isoformat(), "key": k, "label": _label(k), "phase": ph, "status": "done" if (year, k) in done else "upcoming",
         "note": note or ""} for i, (d, k, ph, note) in enumerate(C.year_events(year), 1)]}


def teams_file(u, year):
    teams = []
    for r in sorted(_teams_rows(u), key=lambda r: int(r["team_id"])):
        teams.append({
            "id": int(r["team_id"]), "abbr": r["abbr"], "city": r["team_city"], "name": r["team_name"], "full_name": f'{r["team_city"]} {r["team_name"]}',
            "conference": r["conference"], "division": r["division"], "status": r["club_status"], "venue": r["venue"], "location": r["location"],
            "colors": [{"name": r[f"{k}_color"], "hex": r[f"{k}_hex"]} for k in ("primary", "secondary", "tertiary") if r.get(f"{k}_hex")]})
    return {"year": year, "teams": teams}


def _game(row, kind, gid, abbr, played, week=None, rnd=None, venue=None, note=None):
    return {"id": gid, "kind": kind, "week": week, "round": rnd, "date": row.get("date"), "kickoff": row.get("kickoff"), "away": abbr[row["away"]],
            "home": abbr[row["home"]], "status": "final" if played else "scheduled", "away_score": int(row["away_score"]) if played else None,
            "home_score": int(row["home_score"]) if played else None, "venue": venue, "note": note}


def schedule_file(u, abbr, venues, year):
    run = u.run if (u.run is not None and u.run.year == year) else None
    sched, tour, played, tplayed = None, None, {}, {}
    if run is not None:
        sched, tour = run.sched, run.tour
        played = {(g["week"], g["game"]): g for g in run.games}
        tplayed = {(g["tour_round"], g["game"]): g for g in run.tour_games}
    elif any(f[0] == year and f[2] == SCHEDULE_FROM for f in u.fired):
        _, sched, tour = league.season_tables(u.tpl, u.state, _season_number(u))
    games = []
    for r in sched or ():
        g = played.get((r["week"], r["game"]))
        games.append(_game(g or r, "regular", f'reg:w{int(r["week"]):02d}:g{int(r["game"]):02d}', abbr, g is not None, week=int(r["week"])))
    for r in tour or ():
        g = tplayed.get((r["tour_round"], r["game"]))
        row = g if g else dict(r, away=r["away_nominal"], home=r["home_nominal"])
        games.append(_game(row, "tour", f'tour:r{int(r["tour_round"])}:g{int(r["game"]):02d}', abbr, g is not None,
                           rnd=f'Tour round {int(r["tour_round"])}', venue=_venue_text(venues, g["venue"]) if g else None,
                           note="Ambassador Tour: neutral ground; the nominal home club is listed second"))
    games.sort(key=lambda x: (x["date"] or "", x["kickoff"] or "", x["id"]))
    return {"year": year, "games": games}


def standings_file(u, abbr, year):
    run = u.run if (u.run is not None and u.run.year == year) else None
    if run is None:
        return {"year": year, "through_week": 0, "divisions": [], "ambassadors": [], "designation": None}
    seed = f"{run.seed}|season{run.season}"
    rec = standings.tally(run.active, run.games)
    divs = {}
    for c, key in run.active.items():
        divs.setdefault(key, []).append(c)
    exiled = set(run.outcome["exiled"].values()) if run.outcome else set()
    out = []
    for (conf, dv), clubs in sorted(divs.items()):
        order = standings.rank(clubs, rec, run.active, seed)
        out.append({"conference": conf, "division": dv,
                    "clubs": [dict(_line(abbr[c], rec[c], i + 1), exiled=c in exiled) for i, c in enumerate(order)]})
    trec, by_conf = run.tour_standings()
    amb = [{"conference": conf, "clubs": [_line(abbr[c], trec[c], i + 1) for i, c in enumerate(cs)]} for conf, cs in sorted(by_conf.items())]
    d = run.coronation_designation
    des = None
    if d is not None:
        des = {"through_week": d["through_week"], "home_conference": d["home_conference"], "decided_by": d["decided_by"],
               "conferences": {k: {f: v[f] for f in ("w", "l", "t", "pf", "pa", "pd")} for k, v in d["conferences"].items()}}
    weeks = sorted({int(g["week"]) for g in run.games})
    return {"year": year, "through_week": weeks[-1] if weeks else 0, "divisions": out, "ambassadors": amb, "designation": des}


def _playoff_games(log, by_seed, cal, upto_conf=None):
    """{conference: {round name: [played PLAYOFF_GAME...]}} from the playoff log (conf, away, home, away score, home score), in play order."""
    out = {}
    for conf, by in by_seed.items():
        inv = {c: s for s, c in by.items()}
        mine = [g for g in log if g[0] == conf]
        i, per = 0, {}
        for name, label, note, size in ROUNDS:
            chunk, i = mine[i:i + size], i + size
            per[name] = [{"id": f"po:{conf}:{name}:{k + 1}", "kind": "playoff", "week": None, "round": label, "date": cal.get(note), "kickoff": None,
                          "away": ab, "home": hb, "status": "final", "away_score": sa, "home_score": sh, "venue": None, "note": None,
                          "away_seed": inv[ab], "home_seed": inv[hb]} for k, (_, ab, hb, sa, sh) in enumerate(chunk)]
        out[conf] = per
    return out


def playoffs_file(u, abbr, venues, year):
    empty = {"year": year, "status": "not started", "seeds": {}, "rounds": [], "coronation": None, "coronation_home_conference": None,
             "champion": None, "bowl": None}
    run = u.run if (u.run is not None and u.run.year == year) else None
    if run is None:
        return empty
    outcome = run.outcome
    if outcome is None and run.games and len(run.games) == len(run.sched):          # every regular-season game played: the seeds are known
        outcome = standings.season_outcome(run.active, run.games, f"{run.seed}|season{run.season}")      # a pure function; nothing is stored
    if outcome is None:
        return empty
    by_seed = {conf: {i + 1: c for i, c in enumerate(cl)} for conf, cl in sorted(outcome["seeds"].items())}
    cal = _cal(year)
    seeds = {conf: [{"abbr": abbr[c], "playoff_seed": s} for s, c in sorted(by.items())] for conf, by in by_seed.items()}
    played = {conf: {name: [dict(g, away=abbr[g["away"]], home=abbr[g["home"]]) for g in games] for name, games in per.items()}
              for conf, per in _playoff_games(run.playoff_log, by_seed, cal).items()}
    nxt = len(run.round_done)                       # only the round about to be played shows unplayed games
    rounds = []
    for ri, (name, label, note, size) in enumerate(ROUNDS):
        confs = {}
        for conf in sorted(by_seed):
            games, by = list(played[conf][name]), by_seed[conf]
            if ri == nxt:
                alive = run.alive[conf] if ri else [1, 2, 3, 4, 5, 6]
                pairs = standings.wild_card_pairs() if ri == 0 else standings.divisional_pairs(alive) if ri == 1 else [tuple(sorted(alive))]
                for k, (host, away) in enumerate(pairs):
                    games.append({"id": f"po:{conf}:{name}:{k + 1}", "kind": "playoff", "week": None, "round": label, "date": cal.get(note),
                                  "kickoff": None, "away": abbr[by[away]], "home": abbr[by[host]], "status": "scheduled", "away_score": None,
                                  "home_score": None, "venue": None, "note": None, "away_seed": away, "home_seed": host})
            confs[conf] = games
        if any(confs.values()):
            rounds.append({"name": name, "label": label, "conferences": confs})
    cor, c = None, run.coronation
    d = run.coronation_designation
    if c:
        cor = {"id": "coronation", "kind": "coronation", "week": None, "round": "Diamond Coronation", "date": cal.get("Diamond Coronation"),
               "kickoff": None, "away": abbr[c["away"]], "home": abbr[c["home"]], "status": "final", "away_score": c["away_score"],
               "home_score": c["home_score"], "venue": c["venue"], "note": "Neutral site; the nominal home club is listed second",
               "away_seed": None, "home_seed": None}
    elif len(run.champs) == 2 and d is not None:
        away, home = run.coronation_sides()
        cor = {"id": "coronation", "kind": "coronation", "week": None, "round": "Diamond Coronation", "date": cal.get("Diamond Coronation"),
               "kickoff": None, "away": abbr[away], "home": abbr[home], "status": "scheduled", "away_score": None, "home_score": None,
               "venue": league.CORONATION_VENUE, "note": "Neutral site; the nominal home club is listed second", "away_seed": None, "home_seed": None}
    bowl = None
    b = run.bowl
    if b:
        w = u.world
        rosters = {}
        for conf, pids in sorted(b.get("rosters", {}).items()):
            rows = []
            for pid in pids:
                p, card = w.L.players.get(pid), w.cards.get(pid)
                rows.append({"id": pid, "name": card.identity.name if card else str(pid), "pos": p.pos if p else "?",
                             "club": abbr.get(p.club) if p is not None else None})
            rosters[conf] = rows
        bowl = {"status": "final",
                "game": {"id": "bowl", "kind": "bowl", "week": None, "round": "Ambassador Bowl", "date": cal.get("Ambassador Bowl"), "kickoff": "12:00",
                         "away": b["away"], "home": b["home"], "status": "final", "away_score": b["away_score"], "home_score": b["home_score"],
                         "venue": _venue_text(venues, b["venue"]), "note": "Neutral site"},
                "winner": b.get("winner"), "rosters": rosters, "coach_clubs": {k: abbr[v] for k, v in sorted(b["staff_clubs"].items())}}
    status = "complete" if c else "in progress" if run.round_done else "seeded"
    return {"year": year, "status": status, "seeds": seeds, "rounds": rounds, "coronation": cor,
            "coronation_home_conference": d["home_conference"] if d else None, "champion": abbr[c["winner"]] if c else None, "bowl": bowl}


def history_file(u, year_now):
    seasons = []
    for rep in u.reports:
        r = rep["result"]
        o = r["outcome"]
        ab = {t["team_city"]: t["abbr"] for t in r["teams"]}
        active = {t["team_city"]: (t["conference"], t["division"]) for t in r["teams"] if t["club_status"] == "active"}
        champs = list(r["champions"].values())
        winner = rep["winner"]
        runner = next(c for c in champs if c != winner)
        run = u.run if (u.run is not None and u.run.year == rep["year"] and u.run.coronation) else None
        score = f'{max(run.coronation["away_score"], run.coronation["home_score"])}-{min(run.coronation["away_score"], run.coronation["home_score"])}' if run else None
        stand, winners = [], []
        for dv in sorted(o["division_orders"]):
            order = o["division_orders"][dv]
            stand.append({"conference": dv[0], "division": dv[1], "clubs": [_line(ab[c], o["records"][c], i + 1) for i, c in enumerate(order)]})
            winners.append({"conference": dv[0], "division": dv[1], "abbr": ab[order[0]]})
        by_seed = {conf: {i + 1: c for i, c in enumerate(cl)} for conf, cl in sorted(o["seeds"].items())}
        per = _playoff_games(r["playoff_log"], by_seed, _cal(rep["year"]))
        games = [dict(g, away=ab[g["away"]], home=ab[g["home"]]) for conf in sorted(per) for name, _, _, _ in ROUNDS for g in per[conf][name]]
        b = rep["bowl"]
        seasons.append({
            "season": rep["season"], "year": rep["year"], "champion": ab[winner], "runner_up": ab[runner], "score": score, "venue": rep["venue"],
            "bowl_winner": b.get("winner") if b else None,
            "bowl_score": f'{b["away"]} {b["away_score"]}, {b["home"]} {b["home_score"]}' if b else None,
            "exiled": sorted(ab[c] for c in rep["exiled"]), "division_winners": winners,
            "seeds": {conf: [ab[c] for c in cl] for conf, cl in sorted(o["seeds"].items())}, "standings": stand, "playoff_games": games})
    return {"seasons": seasons}


def roster_file(u, cid, abbr, team, year):
    w = u.world
    L = w.L
    club = L.clubs[cid]
    players = []
    for slot, pids in (("roster", club.roster), ("practice squad", club.ps), ("injured reserve", club.ir)):
        for pid in pids:
            p, card = L.players[pid], w.cards[pid]
            k = p.contract
            players.append({"id": pid, "name": card.identity.name, "pos": p.pos, "number": p.number, "age": p.age, "seasons": p.seasons,
                            "height_cm": card.identity.height_cm, "weight_kg": card.identity.weight_kg, "slot": slot,
                            "injury": {"out": p.out_weeks > 0, "weeks_out": int(p.out_weeks)},
                            "contract": {"cap_hit": round(float(k.cap_hit(L.year)), 2) if k else 0.0, "years_left": trading.years_left(w, p)}})
    players.sort(key=lambda r: (("roster", "practice squad", "injured reserve").index(r["slot"]), r["pos"], r["name"], r["id"]))
    leaders = [{"role": role, "name": c.identity.name, "age": c.age(L.year)} for role, c in sorted(w.leaders.get(cid, {}).items()) if c is not None]
    return {"abbr": abbr[cid], "full_name": f'{team["team_city"]} {team["team_name"]}', "year": year,
            "cap": {"used": round(float(L.cap_number(cid)), 2), "limit": round(float(L.cap), 2)}, "leaders": leaders, "players": players}


def venues_file(u):
    return {"venues": [{"id": v["venue_id"], "name": v["venue_name"], "city": v["city"], "country": v["country"]}
                       for v in sorted(_venues(u).values(), key=lambda r: r["venue_id"])]}


# ------------------------------------------------------------------------------------------------------------------- the build
def build(u, league_id, league_name="DFL Development League", engine="dfl", generated_at=None):
    """Build every public file from a Universe in its committed state. Returns {path: value}; `manifest.json` lists the others by byte size
    and SHA-256 and carries the release name. `generated_at` (an ISO time) is the only wall-clock value; everything else is a function of the
    league. Raises ValueError if anything in the result is not public."""
    rows = _teams_rows(u)
    by_city = {r["team_city"]: r for r in rows}
    abbr = {c: r["abbr"] for c, r in by_city.items()}
    season = season_file(u, abbr)
    year = season["year"]
    venues = _venues(u)
    files = {"season.json": season, "calendar.json": calendar_file(u, year),
             "notices.json": {"development_label": DEVELOPMENT_LABEL, "notices": NOTICES}, "teams.json": teams_file(u, year),
             "schedule.json": schedule_file(u, abbr, venues, year), "standings.json": standings_file(u, abbr, year),
             "playoffs.json": playoffs_file(u, abbr, venues, year), "history.json": history_file(u, year), "venues.json": venues_file(u),
             "rules.json": rules_file()}
    for cid in sorted(u.world.L.clubs):
        files[f"rosters/{abbr[cid]}.json"] = roster_file(u, cid, abbr, by_city[cid], year)
    secrets = [s for s in (str(u.world.creator.seed), u.seed_str) if len(s) >= 6]
    errs = check_public(files, secrets)
    if errs:
        raise ValueError("the feed is not entirely public:\n" + "\n".join(errs[:20]))
    return seal(files, league_id, league_name, engine, len(u.fired), generated_at)


def seal(files, league_id, league_name, engine, events_total_fired, generated_at=None):
    """Add the manifest to a dict of feed files (everything but `manifest.json`): per-file size and SHA-256, the content hash over them, and the
    release name `e<events fired>-<first 10 hex of the content hash>`. The same files always give the same release name."""
    files = {k: v for k, v in files.items() if k != "manifest.json"}
    season = files["season.json"]
    index = {}
    for name in sorted(files):
        raw = canonical(files[name]).encode("utf-8")
        index[name] = {"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    content_hash = hashlib.sha256("".join(f"{n}:{index[n]['sha256']}\n" for n in sorted(index)).encode()).hexdigest()
    manifest = {
        "format": FORMAT, "format_version": FORMAT_VERSION, "format_minor": FORMAT_MINOR,
        "league": {"id": league_id, "name": league_name, "kind": "development", "engine": engine, "label": DEVELOPMENT_LABEL},
        "release": f"e{events_total_fired:05d}-{content_hash[:10]}",
        "generated_at": generated_at or dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "content_hash": content_hash, "season": season["season"], "year": season["year"], "simulation_date": season["simulation_date"],
        "last_event": season["last_event"], "events_done": season["events_done"], "events_total": season["events_total"], "files": index}
    errs = check_public({"manifest.json": manifest})
    if errs:
        raise ValueError("the manifest is not public:\n" + "\n".join(errs[:20]))
    files["manifest.json"] = manifest
    return files
