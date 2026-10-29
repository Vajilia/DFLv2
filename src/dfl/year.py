"""The whole DFL year, event by event (Design Document 8.9, sections 2, 7, 11, 12).

`Universe` couples the league layer (`league.SeasonRun`: games, standings, playoffs, Tour, Bowl, Coronation) to the roster
and cap ledger (`ledger.world.World`: contracts, draft, free agency, cuts, weeks) and fires every event of
`calendar.year_events(year)` on its date, in order. Club ids are the league's team_city names.

Players are character cards (founding league, draft classes, pool top-ups from the `Creator`; yearly development, aging and
retirement), games are played by the engine from the squads the ledger dresses (`engine=True`, the default; `engine=False` uses the
placeholder resolver), and the engine's injuries go back into the ledger. Prices and the clubs' decisions are still the autopilot's
placeholders. This module proves that every event of the year fires once, in order, with the right preconditions, for as many years
as you care to run, and it is the living league that fairness is measured on.
"""
import random

from . import calendar as C
from . import draft, league, parity, standings
from .ledger import analytics, coaching, governance, honours
from .ledger import rules as R
from .ledger.world import World
from .engine.ledger_engine import LedgerEngine
from .resolver import PlaceholderResolver


class Universe:
    def __init__(self, tables, seed=1, reckless=2, engine=True, profile="tight", spatial=True, archive=False):
        self.tpl = league.Template(tables)
        self.clubs = sorted(t["team_city"] for t in self.tpl.teams)
        self.profile = profile
        settings = parity.apply(profile)
        priority = self.clubs[-settings["priority_clubs"]:] if settings.get("priority_clubs") else ()
        self.world = World(self.clubs, seed=seed, reckless=self.clubs[:reckless], year=league.FOUNDING_YEAR, engine_injuries=engine, priority=priority)
        self.state = self.tpl.initial_state()
        self.resolver = LedgerEngine(self.world, spatial=spatial) if engine else PlaceholderResolver()
        self.rng = random.Random(f"{seed}|league")
        self.seed_str = f"dfl{seed}"
        self.history = {}
        self.season = 0
        self.draft_order = None
        self.last_results = {}            # club -> win percentage of the season just finished (leadership window input)
        self.fired = []                   # (year, date, key) in firing order
        self.reports = []
        self.run = None
        self.vote = None
        self.paid_week = 0
        self.cursor = None                # (year, index of the next event) while a year is open, else None
        self.archive_buffer = [] if archive else None     # records for the permanent archive (archive.py), drained after every event
        if archive:
            self.resolver.archive = self.archive_buffer   # the ledger engine adds each game's record here

    # ------------------------------------------------------------------ the year
    def play_year(self):
        """Fire every event of one league year, in order. Same as calling `step_event()` until the year closes."""
        self.step_event()
        while self.cursor is not None:
            self.step_event()
        return self.reports[-1]

    def step_event(self):
        """Fire the next calendar event and return its key. The first call of a year opens it (season counter, per-year fields); the
        call that fires the last event closes it (`cursor` returns to None). One event is the unit of durable saving (persist.py):
        everything an event changes is in this object, so saving between two events and restoring loses and repeats nothing."""
        if self.cursor is None:
            self.season += 1
            self.cursor = (league.FOUNDING_YEAR + self.season - 1, 0)
            self.run, self.vote, self.paid_week, self.comps = None, None, 0, []
            self.venues = sorted({self.tpl.venue[c] for c in self.tpl.division_of})
        year, i = self.cursor
        events = C.year_events(year)
        date, key, phase, _ = events[i]
        getattr(self, "on_" + self._handler(key))(year, date, key)
        self.fired.append((year, date.isoformat(), key))
        self.cursor = None if i + 1 == len(events) else (year, i + 1)
        return key

    @staticmethod
    def _handler(key):
        for prefix in ("week_", "break_", "tour_round_"):
            if key.startswith(prefix):
                return prefix.rstrip("_")
        return key

    def season_record(self, result):
        """The season's history for the archive: results, standings, seeds, playoffs, Tour, Bowl, Coronation. The tables the season was
        built from (teams, schedule, tour slots) are derived from the template and the division state, so they are left out."""
        keep = {k: v for k, v in result.items() if k not in ("teams", "schedule", "tour")}
        out = dict(keep["outcome"])
        out["records"] = {c: {f: getattr(r, f) for f in r.__slots__} for c, r in out["records"].items()}       # standings.Rec -> plain values
        keep["outcome"] = out

        def plain(o):                       # (conference, division) keys become "conference/division"
            if isinstance(o, dict):
                return {("/".join(map(str, k)) if isinstance(k, tuple) else k): plain(v) for k, v in o.items()}
            if isinstance(o, (list, tuple)):
                return [plain(x) for x in o]
            return o
        return {"type": "season", "rid": f"S{self.season:03d}/summary", "season": self.season, "result": plain(keep)}

    def take_archive(self):
        """The archive records produced since the last call (games, season summaries), removed from the league. [] when not archiving."""
        if not self.archive_buffer:
            return []
        out = list(self.archive_buffer)
        del self.archive_buffer[:]
        return out

    def rebind(self, tables):
        """Point the season template at this machine's copy of the tables after a restore or import (the path is not league state)."""
        self.tpl.dir = tables

    @property
    def L(self):
        return self.world.L

    # ------------------------------------------------------------------ offseason
    def on_offseason_open(self, year, date, key):
        self.world.open_year(first=(self.season == 1))
        assert self.L.year == year and self.L.phase == "offseason"

    def on_hiring_open(self, year, date, key):
        """The leadership window (Edition 8.25): dismissals, resignations and hires among the coaches, judged on last season's records."""
        if self.last_results:
            governance.window(self.world, self.last_results)
            coaching.window(self.world, self.last_results)

    def on_hiring_close(self, year, date, key):
        pass

    def on_trading_open(self, year, date, key):
        self.L.open_trading()
        assert self.L.trade_open()
        self.world.trades()

    def on_free_agency_open(self, year, date, key):
        self.world.free_agency_open()

    def on_draft_round_1(self, year, date, key):
        order = self.draft_order
        self.comps = draft.compensatory(self.L.fa_moves, order) if order else []
        self.world.start_draft(order, self.comps)
        self.world.draft_rounds([1])

    def on_draft_rounds_2_3(self, year, date, key):
        self.world.draft_rounds([2, 3])

    def on_draft_rounds_4_7(self, year, date, key):
        self.world.draft_rounds([4, 5, 6, 7, 8, 9])
        self.world.finish_draft()
        assert self.L.draft_completed == year

    def on_camp_open(self, year, date, key):
        self.world.camp_open()
        assert self.L.phase == "camp"

    def on_venue_vote(self, year, date, key):
        self.vote = standings.coronation_pick(self.history, self.venues, year, self.seed_str)

    def on_final_cuts(self, year, date, key):
        self.world.final_cuts()
        for cid, c in self.L.clubs.items():
            assert len(c.roster) <= R.ROSTER_MAX and len(c.ps) <= R.PS_MAX and self.L.overage(cid) == 0, cid

    def on_waivers_clear(self, year, date, key):
        order = sorted(self.L.clubs)
        self.world.rng.shuffle(order)
        self.world.stats["claims"] += len(self.L.resolve_waivers(order, wants=lambda cid, p: self.world.view(cid, p) > 62 and self.world.rng.random() < 0.3))

    # ------------------------------------------------------------------ the season
    def _begin_season(self, year):
        assert self.vote is not None, "the venue vote precedes the season"
        self.world.start_season()
        self.run = league.SeasonRun(self.tpl, self.state, self.season, self.resolver, self.rng, self.history, self.seed_str)
        assert self.run.year == year

    def _paid(self):
        self.paid_week += 1
        return self.paid_week

    def _tour_week(self, paid):
        return set(self.run.ambassadors) if paid in C.TOUR_CALENDAR_WEEKS else set()

    def on_week(self, year, date, key):
        n = int(key.split("_")[1])
        if n == 1:
            self._begin_season(year)
        paid = self._paid()
        playing = {c for g in self.run.week_rows[n] for c in (g["away"], g["home"])} | self._tour_week(paid)
        self.world.play_paid_week(paid, playing)
        self.run.play_week(n)

    def on_break(self, year, date, key):
        paid = self._paid()
        self.world.play_paid_week(paid, self._tour_week(paid))

    def on_tour_round(self, year, date, key):
        i = int(key.split("_")[2])
        assert self.paid_week == C.TOUR_CALENDAR_WEEKS[i - 1], (key, self.paid_week)
        self.run.play_tour_round(i)

    def on_trade_close(self, year, date, key):
        assert self.paid_week == R.TRADE_LAST_PAID_WEEK and self.L.trade_open()       # the last open day
        self.L.close_trading()
        self._closing = True

    def on_venue_announce(self, year, date, key):
        assert self.vote is not None

    # ------------------------------------------------------------------ postseason
    def on_wild_card(self, year, date, key):
        assert self.paid_week == R.PAID_WEEKS and not self.L.trade_open()
        self.world.end_season()
        self.run.finish_regular()
        self.run.play_round("wild_card")
        self.world.heal_round()

    def on_divisional(self, year, date, key):
        self.run.play_round("divisional")
        self.world.heal_round()

    def on_conference_championships(self, year, date, key):
        self.run.play_round("conference")
        self.world.heal_round()

    def on_ambassador_bowl(self, year, date, key):
        self.run.play_bowl()
        self.world.heal_round()

    def on_coronation(self, year, date, key):
        c = self.run.play_coronation()
        assert c["venue"] == self.vote, "the Coronation is held where the June vote said"
        assert (date.weekday(), date) == (6, C.coronation(year))

    def on_year_close(self, year, date, key):
        result, self.state = self.run.handoff()
        self.draft_order = result["draft_order"]
        self.last_results = {c: (r.w / (r.w + r.l) if r.w + r.l else 0.5) for c, r in result["outcome"]["records"].items()}
        governance.close_year(self.world, self.last_results)
        analytics.season_close(self.world, self.L.year)
        honours.award_tiara(self.world, self.L.year, result["coronation_winner"])
        honours.update(self.world, self.L.year, self.tpl.division_of)
        honours.fan_entrant(self.world, self.L.year, self.clubs)
        if self.archive_buffer is not None:
            self.archive_buffer.append(self.season_record(result))
        L = self.L
        self.reports.append({
            "year": year, "season": self.season, "winner": result["coronation_winner"], "venue": result["coronation_venue"],
            "exiled": sorted(result["outcome"]["exiled"].values()), "bowl": result["bowl"],
            "comps": len(self.comps), "events": sum(1 for f in self.fired if f[0] == year) + 1,
            "digest": L.digest(), "result": result})
