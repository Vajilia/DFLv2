"""Season loop (Design Document 8.52, sections 3, 6, 7).

Season 1 tables are the template. Every later season is the template relabelled by (bed, seed): which division
holds which bed comes from the eight-season rotation (`schedule_bed_rotation`), which club holds which seed comes
from last season's final division order (`standings`).  Nothing here knows how a game is decided; that is a
`Resolver` (see `resolver.py`), so the real game engine can replace the placeholder without touching this file.
"""
import csv
import datetime as dt
import os
import random
import shutil

from . import calendar as C
from . import standings
from .schedule_bed_rotation import bed_of_division

WEST = ["Northwest", "Southwest", "Central", "Southern"]
EAST = ["Great Lakes", "Atlantic", "Midland", "Southeast"]
DIVISIONS = tuple((c, d) for c, ds in (("Western", WEST), ("Eastern", EAST)) for d in ds)   # founding bed order
OTHER_TABLES = ("INTL_VENUES", "CALENDAR", "COLOR_REGISTRY")
FOUNDING_YEAR = 2000
CORONATION_VENUE = "Amethyst Aloha Stadium"       # the Diamond Coronation's permanent venue (section 3): owned and renovated by the DFL, always neutral


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
    # The tables on disk are Season 1's. The calendar of every year is anchored on its Coronation (last Sunday of the year) and each
    # calendar week sits a whole number of weeks before it, so a later season's dates are Season 1's moved by the difference of the
    # two Coronations; the weekday and kickoff of every game are unchanged.
    days = (C.coronation(FOUNDING_YEAR + season - 1) - C.coronation(FOUNDING_YEAR)).days
    sched = [dict(r, away=slot[a], home=slot[h], date=_moved(r["date"], days)) for a, h, r in tpl.games]
    tour = [dict(r, **{c: state[tpl.s1_ambassador_division[r[c]]]["amb"] for c in ("away_nominal", "home_nominal")},
                 date=_moved(r["date"], days)) for r in tpl.tour]
    return teams, sched, tour


def _moved(iso, days):
    return (dt.date.fromisoformat(iso) + dt.timedelta(days=days)).isoformat()


def write_tables(tpl, directory, teams, sched, tour, year=None):
    """Write a season's tables. With `year`, CALENDAR.csv is that year's calendar (the schedule's dates are that year's); without it the
    Season 1 calendar is copied, which is only right for Season 1's own dates."""
    os.makedirs(directory, exist_ok=True)
    _write(f"{directory}/TEAMS.csv", teams, tpl.team_header)
    _write(f"{directory}/SCHEDULE.csv", sched, tpl.sched_header)
    _write(f"{directory}/TOUR.csv", tour, tpl.tour_header)
    for n in OTHER_TABLES:
        if n == "CALENDAR" and year is not None:
            rows = C.season_weeks(year)
            _write(f"{directory}/CALENDAR.csv", rows, list(rows[0]))
        else:
            shutil.copy(f"{tpl.dir}/{n}.csv", f"{directory}/{n}.csv")


def play_playoffs(seeds_by_conf, resolver, rng):
    """DFL format on seed numbers 1-6 per conference (seeds 1 and 2 have a bye; 3 hosts 6, 4 hosts 5; the divisional round is reseeded).
    Returns (champions by conference, list of game records). Playoff games have no ties; the higher seed hosts."""
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
        survivors = [1, 2] + wc
        winners = [play(a, b) for a, b in standings.divisional_pairs(survivors)]
        champs[conf] = by_seed[play(*sorted(winners))]
    return champs, log


ROUND_NOTE = {"wild_card": "wild card round", "divisional": "divisional round", "conference": "conference championships"}


RESULT_REVISION = 2          # 1: a season result carried the Coronation winner but not its score; 2 (Edition 8.58): it carries the score too

class SeasonRun:
    """One season played step by step, in the order the calendar fires the events (calendar.py).

    play_week(n) and play_tour_round(i) resolve games as their dates arrive; finish_regular() fixes the standings and the
    seeds; play_round() plays a playoff round for both conferences; play_bowl() and play_coronation() finish the year;
    handoff() returns (result, next_state) exactly as play_season always has.
    """

    TOUR_SLOTS = 7

    def __init__(self, tpl, state, season, resolver, rng, seed="dfl", returner_block=True):
        self.tpl, self.state, self.season, self.resolver, self.rng, self.seed = tpl, state, season, resolver, rng, seed
        self.returner_block = returner_block        # the league's own setting (parity profile), used by its draft order
        self.year = FOUNDING_YEAR + season - 1
        self.cal = {r["note"]: r["sunday"] for r in C.season_weeks(self.year) if r["phase"] in ("postseason", "ambassadors")}   # round -> its Sunday
        self.teams, self.sched, self.tour = season_tables(tpl, state, season)
        self.active = {r["team_city"]: (r["conference"], r["division"]) for r in self.teams if r["club_status"] == "active"}
        self.ambassadors = {r["team_city"]: (r["conference"], r["division"]) for r in self.teams if r["club_status"] == "ambassador"}
        resolver.start_season(sorted({**self.active, **self.ambassadors}), season, rng)
        self.week_rows = {}
        for r in self.sched:
            self.week_rows.setdefault(int(r["week"]), []).append(r)
        self.games, self.tour_games, self.playoff_log = [], [], []
        self.venues = self._draw_venues()
        self.outcome = None
        self.alive = {}
        self.by_seed = {}
        self.champs = {}
        self.round_done = []
        self._round = None
        self.bowl = None
        self.coronation = None
        self.coronation_designation = None      # saved after Week 4 (`play_week`) and never recomputed

    # ---- venues
    def _draw_venues(self):
        with open(f"{self.tpl.dir}/INTL_VENUES.csv", encoding="utf-8", newline="") as f:
            pool = [r["venue_id"] for r in csv.DictReader(f) if r["active"] == "true"]
        picks = random.Random(f"{self.seed}|venues|{self.season}").sample(pool, self.TOUR_SLOTS + 1)
        return {**{f"TR-{i + 1}": picks[i] for i in range(self.TOUR_SLOTS)}, "BOWL": picks[-1]}

    # ---- regular season and Tour
    def play_week(self, numbered_week):
        out = []
        for r in self.week_rows[numbered_week]:
            a, h = self.resolver.resolve(dict(r, id=f"reg:w{int(r['week']):02d}:g{int(r['game']):02d}", kind="regular"), self.rng, decisive=False)
            g = dict(r, away_score=a, home_score=h)
            self.games.append(g)
            out.append(g)
        if numbered_week == standings.CORONATION_AGGREGATE_WEEKS:
            self.designate_coronation_home()
        return out

    def designate_coronation_home(self):
        """Decide, once and from Weeks 1-4 alone, which conference's finalist is the Coronation's nominal home (standings.conference_designation).
        The record is frozen: a second call returns the saved one."""
        if self.coronation_designation is None:
            self.coronation_designation = standings.conference_designation(self.active, self.games, f"{self.seed}|season{self.season}")
        return self.coronation_designation

    def play_tour_round(self, i):
        out = []
        for r in self.tour:
            if int(r["tour_round"]) != i:
                continue
            g = {"away": r["away_nominal"], "home": r["home_nominal"], "neutral": True,      # no Tour venue is any club's home ground (section 7)
                 "id": f"tour:r{i}:g{int(r['game']):02d}", "kind": "tour", "date": r["date"], "kickoff": r["kickoff"]}
            a, h = self.resolver.resolve(g, self.rng, decisive=False)
            g = dict(r, away=g["away"], home=g["home"], away_score=a, home_score=h, venue=self.venues[r["venue_slot"]])
            self.tour_games.append(g)
            out.append(g)
        return out

    def tour_standings(self):
        """({club: Rec}, {conference: [clubs best first]}) over the Tour so far."""
        rec = standings.tally(self.ambassadors, self.tour_games)
        by_conf = {}
        for c, (conf, _) in self.ambassadors.items():
            by_conf.setdefault(conf, []).append(c)
        return rec, {conf: standings.rank(cs, rec, self.ambassadors, f"{self.seed}|season{self.season}")
                     for conf, cs in sorted(by_conf.items())}

    def finish_regular(self):
        assert len(self.games) == len(self.sched), "regular season not finished"
        self.outcome = standings.season_outcome(self.active, self.games, f"{self.seed}|season{self.season}")
        self.by_seed = {conf: {i + 1: c for i, c in enumerate(clubs)} for conf, clubs in self.outcome["seeds"].items()}
        self.alive = {conf: [1, 2, 3, 4, 5, 6] for conf in self.by_seed}
        return self.outcome

    # ---- playoffs
    def _play(self, conf, a, b):
        lo, hi = standings.host(a, b), max(a, b)
        by = self.by_seed[conf]
        res = self.resolver.resolve({"away": by[hi], "home": by[lo], "id": f"po:{conf}:{self._round}:s{hi}v{lo}", "kind": "playoff",
                                     "date": self.cal[ROUND_NOTE[self._round]]},
                                    self.rng, decisive=True)
        self.playoff_log.append((conf, by[hi], by[lo], res[0], res[1]))
        return lo if res[1] > res[0] else hi

    def play_round(self, name):
        assert name == ("wild_card", "divisional", "conference")[len(self.round_done)], name
        self._round = name
        for conf in self.by_seed:
            if name == "wild_card":
                wc = [self._play(conf, a, b) for a, b in standings.wild_card_pairs()]
                self.alive[conf] = [1, 2] + wc
            elif name == "divisional":
                self.alive[conf] = [self._play(conf, a, b) for a, b in standings.divisional_pairs(self.alive[conf])]
            else:
                self.champs[conf] = self.by_seed[conf][self._play(conf, *sorted(self.alive[conf]))]
        self.round_done.append(name)

    def play_bowl(self):
        """Western v Eastern Ambassador All-Stars at the Bowl venue. Rosters are the people layer's job (Phase 5); here the
        All-Star sides carry their conference's Tour clubs and the resolver is told their combined strength."""
        _, by_conf = self.tour_standings()
        sides = {}
        for conf, clubs in by_conf.items():
            name = f"{conf} All-Stars"
            sides[conf] = name
            if hasattr(self.resolver, "strength"):
                self.resolver.strength[name] = sum(self.resolver.strength.get(c, 0.0) for c in clubs) / len(clubs)
            if hasattr(self.resolver, "all_star_side"):
                self.resolver.all_star_side(name, clubs)
        # A neutral venue, so the nominal home is not a home side: the two All-Star sides carry no seeds, so the "higher seed with a
        # seeded tie" rule (section 6) reduces to a seeded draw; neither conference is always the nominal home.
        order = ["Western", "Eastern"]
        if random.Random(f"{self.seed}|bowlhome|{self.season}").random() < 0.5:
            order.reverse()
        away, home = sides[order[0]], sides[order[1]]
        res = self.resolver.resolve({"away": away, "home": home, "neutral": True, "id": "bowl", "kind": "bowl",
                                     "date": self.cal["Ambassador Bowl"], "kickoff": "12:00"}, self.rng, decisive=True)
        self.bowl = {"away": away, "home": home, "away_score": res[0], "home_score": res[1], "neutral": True,
                     "venue": self.venues["BOWL"], "staff_clubs": {conf: clubs[0] for conf, clubs in by_conf.items()}}      # each conference's best Tour club supplies its coaches
        return self.bowl

    def play_coronation(self):
        """The Diamond Coronation: always at CORONATION_VENUE and always neutral (no home-field edge in the engine). The designation made after
        Week 4 only names the nominal home finalist, which controls the established kickoff and uniform rules."""
        assert self.coronation_designation is not None, "the Week 4 designation precedes the Coronation"
        away, home = self.coronation_sides()
        res = self.resolver.resolve({"away": away, "home": home, "neutral": True, "id": "coronation", "kind": "coronation",
                                     "date": self.cal["Diamond Coronation"]},
                                    self.rng, decisive=True)
        self.coronation = {"away": away, "home": home, "away_score": res[0], "home_score": res[1], "venue": CORONATION_VENUE,
                           "neutral": True, "home_conference": self.coronation_designation["home_conference"],
                           "winner": home if res[1] > res[0] else away}
        return self.coronation

    def coronation_sides(self):
        """(away, home) for the Coronation: the finalist from the conference designated after Week 4 is the nominal home."""
        conf = self.coronation_designation["home_conference"]
        other = next(c for c in self.champs if c != conf)
        return self.champs[other], self.champs[conf]

    # ---- hand-off
    def draft_order(self, returning=None):
        """Next draft's base order (draft.py): the whole 40-club league ranked by the cascade, then exits, then the lottery."""
        from . import draft
        ranked = standings.rank(list(self.active), self.outcome["records"], self.active, f"{self.seed}|season{self.season}")
        elim = draft.elimination_rounds(self.playoff_log, (self.coronation["away"], self.coronation["home"],
                                                          self.coronation["away_score"], self.coronation["home_score"]))
        ret = sorted(returning if returning is not None else self.ambassadors)
        # clubs in their first season back (last season's ambassadors, seed 5 now); Season 1's seed 5s were never exiled
        returned = [] if self.season == 1 else [self.state[dv]["seeds"][5] for dv in self.state]
        return draft.base_order(ranked, elim, ret, f"{self.seed}|draft{self.season}", returned, self.returner_block)

    def handoff(self):
        new_state = {}
        for dv in DIVISIONS:
            order = self.outcome["division_orders"][dv]
            nxt = standings.next_division_state(order, self.state[dv]["amb"])
            new_state[dv] = {"seeds": nxt["seeds"], "amb": nxt["ambassador"]}
        c = self.coronation
        result = {"season": self.season, "year": self.year, "teams": self.teams, "schedule": self.sched, "tour": self.tour,
                  "games": self.games, "outcome": self.outcome, "champions": self.champs, "playoff_log": self.playoff_log,
                  "coronation_venue": c["venue"], "coronation_designation": self.coronation_designation, "coronation_home": c["home"],
                  "coronation_winner": c["winner"],
                  "coronation": {k: c[k] for k in ("away", "home", "away_score", "home_score")},       # the score is kept for good (Edition 8.58)
                  "result_revision": RESULT_REVISION,
                  "tour_games": self.tour_games, "bowl": self.bowl, "venues": self.venues, "draft_order": self.draft_order()}
        return result, new_state


def play_season(tpl, state, season, resolver, rng, seed="dfl"):
    """Play one whole season in one call. Returns (result dict, next state). The year loop (year.py) uses SeasonRun directly so events fire
    on their calendar dates."""
    run = SeasonRun(tpl, state, season, resolver, rng, seed)
    for n in sorted(run.week_rows):
        run.play_week(n)
    for i in range(1, 8):
        run.play_tour_round(i)
    run.finish_regular()
    for name in ("wild_card", "divisional", "conference"):
        run.play_round(name)
    run.play_bowl()
    run.play_coronation()
    return run.handoff()
