"""Season loop (Design Document 8.6, sections 3, 6, 7).

Season 1 tables are the template. Every later season is the template relabelled by (bed, seed): which division
holds which bed comes from the eight-season rotation (`schedule_bed_rotation`), which club holds which seed comes
from last season's final division order (`standings`).  Nothing here knows how a game is decided; that is a
`Resolver` (see `resolver.py`), so the real game engine can replace the placeholder without touching this file.
"""
import csv
import os
import shutil

from . import standings
from .schedule_bed_rotation import bed_of_division

WEST = ["Northwest", "Southwest", "Central", "Southern"]
EAST = ["Midwest", "Northeast", "East", "Southeast"]
DIVISIONS = tuple((c, d) for c, ds in (("Western", WEST), ("Eastern", EAST)) for d in ds)   # founding bed order
OTHER_TABLES = ("INTL_VENUES", "CALENDAR", "COLOR_REGISTRY")
FOUNDING_YEAR = 2000


def bed_of(conf, div, season):
    """Bed (1-8) the division fills in a 1-based season."""
    return bed_of_division(season, (conf, div), DIVISIONS) + 1


def _read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _write(path, rows, header):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


class Template:
    """Season 1: TEAMS, SCHEDULE and TOUR, plus the (bed, seed) position of every game's two clubs."""

    def __init__(self, directory):
        self.dir = directory
        self.teams = _read(f"{directory}/TEAMS.csv")
        self.sched = _read(f"{directory}/SCHEDULE.csv")
        self.tour = _read(f"{directory}/TOUR.csv")
        self.team_header = list(self.teams[0])
        self.sched_header = list(self.sched[0])
        self.tour_header = list(self.tour[0])
        by_city = {t["team_city"]: t for t in self.teams}
        pos = {c: tuple(map(int, t["bed_id"].split("-"))) for c, t in by_city.items() if t["bed_id"]}
        self.games = [(pos[r["away"]], pos[r["home"]], r) for r in self.sched]
        self.division_of = {c: (t["conference"], t["division"]) for c, t in by_city.items()}
        self.s1_ambassador_division = {t["team_city"]: self.division_of[t["team_city"]]
                                       for t in self.teams if t["club_status"] == "ambassador"}
        self.venue = {c: t["venue"] for c, t in by_city.items()}

    def initial_state(self):
        """{division: {"seeds": {1..5: club}, "amb": club}} for Season 1."""
        state = {}
        for dv in DIVISIONS:
            members = [t for t in self.teams if self.division_of[t["team_city"]] == dv]
            state[dv] = {"seeds": {int(t["division_rank"]): t["team_city"] for t in members if t["club_status"] == "active"},
                         "amb": next(t["team_city"] for t in members if t["club_status"] == "ambassador")}
        return state


def season_tables(tpl, state, season):
    """(teams_rows, schedule_rows, tour_rows) for a season, from the Season 1 template."""
    beds = {dv: bed_of(dv[0], dv[1], season) for dv in DIVISIONS}
    assert sorted(beds.values()) == list(range(1, 9)), (season, beds)
    slot = {(beds[dv], seed): club for dv in DIVISIONS for seed, club in state[dv]["seeds"].items()}
    assert len(slot) == 40
    teams = []
    for t in tpl.teams:
        r = dict(t)
        dv = tpl.division_of[t["team_city"]]
        seed = next((s for s, c in state[dv]["seeds"].items() if c == t["team_city"]), None)
        if seed:
            r.update(division_rank=str(seed), club_status="active", is_active="true", draft_band="standard",
                     bed_id=f"{beds[dv]}-{seed}")
        else:
            assert state[dv]["amb"] == t["team_city"]
            r.update(division_rank="6", club_status="ambassador", is_active="false", draft_band="lottery_1_8", bed_id="")
        teams.append(r)
    order = {dv: i for i, dv in enumerate(DIVISIONS)}
    teams.sort(key=lambda r: (order[(r["conference"], r["division"])], int(r["division_rank"])))
    sched = [dict(r, away=slot[a], home=slot[h]) for a, h, r in tpl.games]
    tour = [dict(r, **{c: state[tpl.s1_ambassador_division[r[c]]]["amb"] for c in ("away_nominal", "home_nominal")})
            for r in tpl.tour]
    return teams, sched, tour


def write_tables(tpl, directory, teams, sched, tour):
    os.makedirs(directory, exist_ok=True)
    _write(f"{directory}/TEAMS.csv", teams, tpl.team_header)
    _write(f"{directory}/SCHEDULE.csv", sched, tpl.sched_header)
    _write(f"{directory}/TOUR.csv", tour, tpl.tour_header)
    for n in OTHER_TABLES:
        shutil.copy(f"{tpl.dir}/{n}.csv", f"{directory}/{n}.csv")


def play_playoffs(seeds_by_conf, resolver, rng):
    """NFL format on seed numbers 1-7 per conference. Returns (champions by conference, list of game records).
    Playoff games have no ties; the higher seed hosts."""
    log, champs = [], {}
    for conf, clubs in seeds_by_conf.items():
        by_seed = {i + 1: c for i, c in enumerate(clubs)}

        def play(a, b):
            lo, hi = standings.host(a, b), max(a, b)
            res = resolver.resolve({"away": by_seed[hi], "home": by_seed[lo]}, rng, decisive=True)
            hs, as_ = res[1], res[0]
            log.append((conf, by_seed[hi], by_seed[lo], as_, hs))
            return lo if hs > as_ else hi

        wc = [play(a, b) for a, b in standings.wild_card_pairs()]
        survivors = [1] + wc
        winners = [play(a, b) for a, b in standings.divisional_pairs(survivors)]
        champs[conf] = by_seed[play(*sorted(winners))]
    return champs, log


def play_season(tpl, state, season, resolver, rng, history, seed="dfl"):
    """Play one season. Returns (result dict, next state). `history` maps year -> Coronation venue club (mutated)."""
    teams_rows, sched, tour = season_tables(tpl, state, season)
    active = {r["team_city"]: (r["conference"], r["division"]) for r in teams_rows if r["club_status"] == "active"}
    resolver.start_season(sorted(active), season, rng)
    games = []
    for r in sched:
        a, h = resolver.resolve(r, rng, decisive=False)
        games.append(dict(r, away_score=a, home_score=h))
    outcome = standings.season_outcome(active, games, f"{seed}|season{season}")
    champs, playoff_log = play_playoffs(outcome["seeds"], resolver, rng)
    year = FOUNDING_YEAR + season - 1
    venues = sorted({tpl.venue[c] for c in tpl.division_of})
    venue = standings.coronation_pick(history, venues, year, seed)
    history[year] = venue
    finalists = sorted(champs.values())
    c_away, c_home = champs["Western"], champs["Eastern"]
    c_res = resolver.resolve({"away": c_away, "home": c_home}, rng, decisive=True)
    host_club = next((c for c in finalists if tpl.venue[c] == venue), None)
    winner = c_home if c_res[1] > c_res[0] else c_away
    new_state = {}
    for dv in DIVISIONS:
        order = outcome["division_orders"][dv]
        nxt = standings.next_division_state(order, state[dv]["amb"])
        new_state[dv] = {"seeds": nxt["seeds"], "amb": nxt["ambassador"]}
    result = {"season": season, "year": year, "teams": teams_rows, "schedule": sched, "tour": tour, "games": games,
              "outcome": outcome, "champions": champs, "playoff_log": playoff_log, "coronation_venue": venue,
              "coronation_home_club": host_club, "coronation_winner": winner}
    return result, new_state
