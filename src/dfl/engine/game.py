"""One game, play by play (slice 1: play-level resolution; the rules are Design Document section 10).

The rules core (clock, downs, kickoffs, scoring, overtime, timeouts, penalties) is exact to the spec. How a play turns out is a
statistical model driven by the hidden attributes of the eleven players on the field; it is PLACEHOLDER until calibrated against
data/engine/BANDS.csv and is meant to be replaced, behind the same interface, by the spatial engine.

Coordinates: `yl` is the offense's yards from its own goal line (0-100). Teams are indexed 0 = home, 1 = away.
"""
import math
import random
from dataclasses import dataclass, field

from . import production
from .params import P, PENALTIES, RULE_VERSION

QUARTER = 900
TWO_MINUTE = 120
OT_REGULAR = 600
OT_POST = 900
INJURY_WEEKS = ((1, 55), (2, 12), (3, 8), (4, 6), (6, 6), (8, 5), (12, 5), (18, 2), (30, 1))


GROUPS = {"OL": ("LT", "LG", "C", "RG", "RT"), "WR": ("WR",), "TE": ("TE",), "HB": ("HB", "FB"), "QB": ("QB",),
          "DL": ("LE", "RE", "DT"), "LB": ("ROLB", "MLB", "LOLB"), "DB": ("CB", "FS", "SS")}
PEN_PRE = [p for p in PENALTIES if p["when"] == "pre"]
PEN_RUN = [p for p in PENALTIES if p["when"] == "live"]
PEN_PASS = [p for p in PENALTIES if p["when"] in ("live", "pass")]
PEN_KICK = [p for p in PENALTIES if p["when"] == "kick"]
# who makes a tackle, a sack, an interception or breaks up a pass, by position group (builder's split; see docs/BOX_SCORES.md)
RUN_W = {"DL": 0.27, "LB": 0.50, "DB": 0.23}
PASS_W = {"DL": 0.04, "LB": 0.36, "DB": 0.60}
RUSH_W = {"DL": 0.72, "LB": 0.22, "DB": 0.06}
INT_W = {"DL": 0.02, "LB": 0.18, "DB": 0.80}
PD_W = {"DL": 0.06, "LB": 0.24, "DB": 0.70}
UNSPORTSMANLIKE = ("unsportsmanlike conduct", "taunting")           # two of these eject a player (section 10)
# fouls that need a pass thrown at someone, and the plays they can ride on
NEEDS_TARGET = ("defensive pass interference", "offensive pass interference", "illegal contact", "ineligible downfield pass",
                "illegal forward pass")
# dead-ball and after-the-play fouls: the play stands and the yards are added to where it ended
AFTER_PLAY = ("face mask, defense", "face mask, offense", "unnecessary roughness, defense", "unnecessary roughness, offense", "horse collar tackle",
              "roughing the passer", "personal foul", "unsportsmanlike conduct", "taunting")


def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def gamma_yards(rng, mean, sd):
    k = (mean / sd) ** 2
    return rng.gammavariate(k, sd * sd / mean)


@dataclass
class GameResult:
    home: str
    away: str
    score: tuple
    periods: int
    tie: bool
    winner: str
    stats: list
    players: dict
    injuries: dict
    plays: list = field(default_factory=list)
    rule_version: str = RULE_VERSION
    team_production: list = field(default_factory=list)
    production: dict = field(default_factory=dict)      # card id -> {play type: [opportunities, EPA, WPA]} (Edition 8.30)
    fit: list = field(default_factory=list)             # (offence, yard line, down, distance, points scored, half) per play (Edition 8.32)
    pen_types: list = field(default_factory=list)       # per team: penalty name -> [accepted, yards] (Edition 8.46)
    table: list = field(default_factory=list)           # the structured play table: one dict per event, players by id (Edition 8.47)


def new_stats():
    return dict(plays=0, pass_att=0, comp=0, pass_yds=0, sacks=0, ints=0, rush_att=0, rush_yds=0, fumbles_lost=0,
                penalties=0, pen_yds=0, first_downs=0, third_att=0, third_conv=0, fourth_att=0, fourth_conv=0,
                punts=0, fg_att=0, fg_made=0, pat_att=0, pat_made=0, two_att=0, two_made=0, td=0, top=0.0,
                drives=0, points=0, kickoffs=0, safeties=0, sack_yds=0)


class Game:
    def __init__(self, home, away, rng, decisive=False, log=False, curve=None, spatial=False, trace=False):
        self.sq = (home, away)
        self.spatial = spatial               # plays are played out by 22 players on the field (Edition 8.48); False is the play-level resolver
        self.trace = trace                   # keep each spatial play's compressed trace in the play table
        self.sim_secs = None                 # seconds the last spatial play took
        self.ball_y = None
        self.ball_team = None
        self.curve = curve                   # the season's expected-points table; None is the data table (Edition 8.32)
        self.fit = []
        self.rng = rng
        self.decisive = decisive
        self.log = [] if log else None
        self.score = [0, 0]
        self.st = (new_stats(), new_stats())
        self.pl = {}
        self.prod = {}
        self.team = [{}, {}]                 # offence k -> play type -> [plays, EPA] (Edition 8.30)
        self.period, self.t = 1, QUARTER
        self.to = [3, 3]
        self.poss = 0
        self.yl, self.down, self.dist = 25, 1, 10
        self.finished = False
        self.ot = None                       # overtime bookkeeping
        self.next = None
        self.winner_now = None
        self.arng = random.Random(rng.getrandbits(64))   # a stream of its own for crediting tackles, sacks and the like: recording never moves the game's
        self.ptab = [] if log else None                  # the structured play table (Edition 8.47)
        self.td_by = None                                # id of the player who scored the touchdown being booked
        self.pen_by = [{}, {}]               # per team: penalty name -> [accepted, yards]
        self.uns = {}                        # unsportsmanlike fouls per player; the second ejects her
        self.warned = set()                  # periods whose two-minute warning has been given

    # ------------------------------------------------------------ helpers
    def other(self, k):
        return 1 - k

    def pstat(self, card, key, n=1):
        d = self.pl.setdefault(card.identity.id, {})
        d[key] = d.get(key, 0) + n

    def behind(self, k):
        return self.score[k] < self.score[1 - k]

    def lead(self, k):
        return self.score[k] - self.score[1 - k]

    def add_points(self, k, pts):
        self.score[k] += pts
        self.st[k]["points"] += pts

    def record(self, *row, **info):
        """Add a row to the play log (tuple, for the tests and the old consumers) and an entry to the structured play table (a dict
        with the state, the event and the ids of the players in it)."""
        if self.log is not None:
            base = (self.period, round(self.t), self.poss, self.down, self.dist, self.yl)
            self.log.append(base + row)
            e = dict(info)
            e.update(i=len(self.ptab), period=base[0], clock=base[1], off=base[2], down=base[3], dist=base[4], yl=base[5], kind=row[0],
                     detail=row[1:])
            self.ptab.append(e)

    def tag(self, **kw):
        """Add facts to the entry just recorded (a first down, a score)."""
        if self.ptab:
            self.ptab[-1].update(kw)

    def pick_defender(self, ud, wts, attr, exclude=None):
        pool = ud["def"]
        ws = []
        for c in pool:
            pos = c.identity.pos
            g = "DL" if pos in GROUPS["DL"] else ("LB" if pos in GROUPS["LB"] else "DB")
            w = wts[g] * (0.4 + c.attrs.get(attr, 50) / 100.0) / (4 if g in ("DL", "DB") else 3)
            ws.append(0.0 if exclude is not None and c.identity.id == exclude else w)
        r = self.arng.random() * sum(ws)
        for c, w in zip(pool, ws):
            r -= w
            if r <= 0 < w:
                return c
        return pool[-1]

    def credit_defense(self, k, o, scores=False):
        """Who made the play on defense (tackle and assist, sack, tackle for loss, interception, pass defended). Drawn on the arng
        stream by position group and the defender's skill; the spatial engine will name them from what happens on the field."""
        if o.get("spatial"):
            return self.credit_spatial(k, o, scores)
        ud = self.sq[1 - k].units()
        kind, a = o["kind"], self.arng
        info = {}
        if kind in ("run", "scramble") and not scores:
            t = self.pick_defender(ud, RUN_W, "TAK")
            self.pstat(t, "tkl")
            info["tkl"] = t.identity.id
            if o["yards"] <= 0:
                self.pstat(t, "tfl")
            if a.random() < 0.3:
                b = self.pick_defender(ud, RUN_W, "TAK", t.identity.id)
                self.pstat(b, "ast")
                info["ast"] = b.identity.id
        elif kind == "sack":
            t = self.pick_defender(ud, RUSH_W, "PRU")
            half = a.random() < 0.12
            self.pstat(t, "sack", 0.5 if half else 1)
            self.pstat(t, "tfl", 0.5 if half else 1)
            info["sack"] = [t.identity.id]
            if half:
                b = self.pick_defender(ud, RUSH_W, "PRU", t.identity.id)
                self.pstat(b, "sack", 0.5)
                self.pstat(b, "tfl", 0.5)
                info["sack"].append(b.identity.id)
            self.pstat(o["qb"], "sacked")
            self.pstat(o["qb"], "sack_yds", -o["yards"])
        elif kind == "complete" and not scores:
            t = self.pick_defender(ud, PASS_W, "TAK")
            self.pstat(t, "tkl")
            info["tkl"] = t.identity.id
            if a.random() < 0.2:
                b = self.pick_defender(ud, PASS_W, "TAK", t.identity.id)
                self.pstat(b, "ast")
                info["ast"] = b.identity.id
        elif kind == "incomplete" and a.random() < 0.3:
            t = self.pick_defender(ud, PD_W, "COV")
            self.pstat(t, "pd")
            info["pd"] = t.identity.id
        elif kind == "int":
            t = self.pick_defender(ud, INT_W, "BAL")
            self.pstat(t, "def_int")
            info["int_by"] = t.identity.id
        if o["turnover"] == "fumble":
            f = self.pick_defender(ud, RUN_W, "TAK")
            self.pstat(f, "ff")
            r = self.pick_defender(ud, RUN_W, "AWR")
            self.pstat(r, "fr")
            info["ff"], info["fr"] = f.identity.id, r.identity.id
        return info

    @staticmethod
    def ids(o):
        d = {}
        for key, name in (("qb", "qb"), ("carrier", "car"), ("target", "tgt")):
            if o.get(key) is not None:
                d[name] = o[key].identity.id
        return d

    # ------------------------------------------------------------ clock
    def tick(self, secs, team_with_ball=None):
        """Run the clock. Quarters 1 and 3 roll over here. The end of a half, of regulation or of an overtime period sets
        `period_end`; the drive stops after the current play and `run` applies the transition."""
        if self.period_end:
            return
        if self.ot is None and self.period in (2, 4) and self.period not in self.warned and self.t > TWO_MINUTE >= self.t - secs:
            secs = self.t - TWO_MINUTE                     # the two-minute warning stops the clock at 2:00 (no timeout is charged)
            self.warned.add(self.period)
            if team_with_ball is not None:
                self.st[team_with_ball]["top"] += secs
            self.t = TWO_MINUTE
            self.record("two-minute warning")
            return
        if team_with_ball is not None:
            self.st[team_with_ball]["top"] += min(secs, max(self.t, 0))
        self.t -= secs
        if self.t > 0:
            return
        if self.ot is not None:
            if not self.ot["done"]:
                self.t = 1                      # time is extended only to finish the answering possession
                return
            self.period_end = "ot"
        elif self.period in (1, 3):
            self.period += 1
            self.t = QUARTER
        elif self.period == 2:
            self.period_end = "half"
        else:
            self.period_end = "regulation"

    def apply_period_end(self):
        pe, self.period_end = self.period_end, None
        if pe == "half":
            self.period, self.t = 3, QUARTER
            self.to = [3, 3]
            self.next = ("kickoff", 1)              # the away side kicks the second half
        elif pe == "regulation":
            if self.score[0] != self.score[1]:
                self.finished = True
            else:
                self.start_overtime(1)
        elif pe == "ot":
            if self.score[0] != self.score[1] or not self.decisive:
                self.finished = True                # a tie is possible in the regular season and on the Tour only
            else:
                self.start_overtime(self.ot["n"] + 1)

    def start_overtime(self, n):
        self.period = 4 + n
        self.t = OT_POST if self.decisive else OT_REGULAR
        self.to = [2, 2]
        starter = 1 if n % 2 == 1 else 0            # the away side kicks first, alternating each period
        self.ot = {"n": n, "poss": [0, 0], "done": False}
        self.next = ("kickoff", starter)

    def ot_possession_end(self, k):
        o = self.ot
        o["poss"][k] += 1
        if o["poss"][0] >= 1 and o["poss"][1] >= 1:
            o["done"] = True
            if self.score[0] != self.score[1]:
                self.finished = True

    def ot_sudden_score(self):
        """After both sides have had the ball, the next score wins."""
        if self.ot is not None and self.ot["done"]:
            self.finished = True

    # ------------------------------------------------------------ the game
    def run(self):
        self.period_end = None
        self.next = ("kickoff", 0)                      # home kicks to start the first half
        while not self.finished:
            if self.next[0] == "kickoff":
                self.kickoff(self.next[1], free_kick=len(self.next) > 2)
                if self.period_end:
                    self.apply_period_end()
                continue
            self.poss, self.yl = self.next[1], self.next[2]
            self.next = None
            self.drive()
            if self.period_end:
                self.apply_period_end()
            elif self.next is None and not self.finished:
                raise RuntimeError("drive ended without a next state")
        return self.result()

    def result(self):
        h, a = self.sq
        tie = self.score[0] == self.score[1]
        win = None if tie else (h.cid if self.score[0] > self.score[1] else a.cid)
        inj = {h.cid: list(h.new_injuries), a.cid: list(a.new_injuries)}
        return GameResult(h.cid, a.cid, (self.score[0], self.score[1]), self.period, tie, win,
                          [self.st[0], self.st[1]], self.pl, inj, self.log or [], production=self.prod, team_production=self.team, fit=self.fit,
                          pen_types=self.pen_by, table=self.ptab or [])

    # ------------------------------------------------------------ kicking
    def kickoff(self, kicker, free_kick=False):
        rng = self.rng
        recv = self.other(kicker)
        self.st[kicker]["kickoffs"] += 1
        trailing_late = (self.behind(kicker) and self.period == 4 and self.t <= 180 and self.ot is None and not free_kick)
        if trailing_late and rng.random() < 0.7:
            if rng.random() < P["onside_success"]:
                self.next = ("ball", kicker, 50)
                self.record("onside", "recovered")
            else:
                self.next = ("ball", recv, 50)
                self.record("onside", "failed")
            self.tick(rng.uniform(4, 8))
            return
        flag = self.kick_flag("k")
        returned = False
        if free_kick:
            start = int(clamp(rng.gauss(36, 7), 15, 60))
            returned = True
        elif rng.random() < P["kickoff_touchback"]:
            start = 25
        else:
            start = int(clamp(rng.gauss(P["kickoff_return_mean"], 6), 8, 55))
            returned = True
        if flag:
            if flag["effect"] in ("replay_back", "oob") or returned:       # a foul before the kick, or one on the return or coverage
                start = self.kick_spot(kicker, flag, start)
        kicker_card = self.sq[kicker].units()["kicker"]
        rtn = self.sq[recv].units()["returner"] if returned else None
        if returned and not free_kick:
            if rng.random() < 0.006:
                self.pstat(kicker_card, "ko")
                if rtn is not None:
                    self.pstat(rtn, "kr")
                    self.pstat(rtn, "kr_yds", 100)
                self.record("kickoff", "return touchdown", kicker=kicker_card.identity.id)
                self.poss = recv
                self.td_by = rtn.identity.id if rtn is not None else None
                self.score_td(recv, defensive=True)
                return
        self.pstat(kicker_card, "ko")
        if returned:
            if rtn is not None:
                self.pstat(rtn, "kr")
                self.pstat(rtn, "kr_yds", start + 5)
        else:
            self.pstat(kicker_card, "ko_tb")
        self.next = ("ball", recv, start)
        self.record("kickoff", start, kicker=kicker_card.identity.id)
        self.tick(rng.uniform(5, 9))

    # ------------------------------------------------------------ drive
    def drive(self):
        k = self.poss
        self.st[k]["drives"] += 1
        self.down, self.dist = 1, min(10, 100 - self.yl)
        self.next = None
        while not self.finished and self.next is None and not self.period_end:
            if self.down == 4:
                act = self.fourth_down()
                if act in ("punt", "fg"):
                    pre = production.State(self, k)
                    done = self.punt() if act == "punt" else self.field_goal()
                    if not done:
                        continue                                        # a foul nullified the kick: the drive goes on
                    production.credit_kick(self, k, pre, act)
                    break
            self.snap()
        if self.ot is not None and not self.finished:
            self.ot_possession_end(k)

    def fourth_down(self):
        k, rng = self.poss, self.rng
        d, yl = self.dist, self.yl
        fg_dist = 100 - yl + 17
        z = self.sq[k].units()["z"]["kick"]
        fg_ok = fg_dist <= 55 + max(-4, min(4, z * 2))
        q4_late = self.period >= 4 and self.t <= 300
        lead = self.lead(k)
        if q4_late and lead < 0:
            need_td = lead < -3
            if fg_ok and not need_td:
                return "fg"
            if self.t <= 120 or yl >= 40 or d <= 4:
                return "go"
        if self.period == 2 and self.t <= 25 and fg_ok:
            return "fg"
        if fg_ok:
            if d <= 2 and yl >= 94 and rng.random() < 0.3 and lead < 11:
                return "go"
            return "fg"
        if yl >= 50 and d <= 3 and rng.random() < 0.5:
            return "go"
        if yl >= 40 and d <= 1 and rng.random() < 0.45:
            return "go"
        return "punt"

    def punt(self):
        """True when the punt was made; False when a foul sent it back or gave the kicking team a first down (the drive goes on)."""
        k, rng = self.poss, self.rng
        o = self.other(k)
        flag = self.kick_flag("p")
        if flag and flag["effect"] in ("replay_back", "replay_fwd", "rough"):
            self.kick_replay(k, flag)
            return False
        u = self.sq[k].units()
        self.st[k]["punts"] += 1
        dist = clamp(rng.gauss(P["punt_mean"] + 2.0 * u["z"]["punt"], P["punt_sd"]), 20, 72)
        land = self.yl + dist
        punter = u["punter"]
        self.pstat(punter, "punts")
        self.pstat(punter, "punt_yds", round(dist))
        self.record("punt", round(dist), punter=punter.identity.id)
        elapsed = rng.uniform(5, 7)
        if land >= 100:
            self.pstat(punter, "punt_tb")
            self.next = ("ball", o, 20)
        else:
            ret = max(0, round(rng.gauss(P["punt_return_mean"], 5)))
            spot = int(clamp(100 - land + ret, 1, 99))
            rtn = self.sq[o].units()["returner"]
            if rtn is not None and ret > 0:
                self.pstat(rtn, "pr")
                self.pstat(rtn, "pr_yds", ret)
            if flag:                                    # a foul in the coverage or on the return moves the receiving team's spot
                spot = self.kick_spot(k, flag, spot)
            if spot <= 20:
                self.pstat(punter, "punt_in20")
            self.next = ("ball", o, spot)
        self.tick(elapsed + 3, None)
        self.poss = k
        return True

    def field_goal(self):
        k, rng = self.poss, self.rng
        o = self.other(k)
        flag = self.kick_flag("f")
        if flag and flag["effect"] in ("replay_back", "replay_fwd", "rough"):
            self.kick_replay(k, flag)
            return False
        dist = 100 - self.yl + 17
        z = self.sq[k].units()["z"]["kick"]
        p = 0.99 / (1 + math.exp((dist - (P["fg_mid"] + 1.5 * z)) / P["fg_scale"]))
        self.st[k]["fg_att"] += 1
        kicker = self.sq[k].units()["kicker"]
        self.pstat(kicker, "fg_att")
        elapsed = rng.uniform(4, 6)
        if rng.random() < p:
            self.st[k]["fg_made"] += 1
            self.pstat(kicker, "fg_made")
            d = self.pl.setdefault(kicker.identity.id, {})
            d["fg_long"] = max(d.get("fg_long", 0), dist)
            self.add_points(k, 3)
            self.record("field goal", "good", dist, team=k, pts=3, kicker=kicker.identity.id)
            self.next = ("kickoff", k)
            self.ot_sudden_score()
        else:
            self.record("field goal", "missed", dist, team=k, pts=0, kicker=kicker.identity.id)
            self.next = ("ball", o, int(clamp(100 - (self.yl - 7), 20, 99)))
        self.tick(elapsed + 3, None)
        return True

    # ------------------------------------------------------------ scoring
    def score_td(self, k, defensive=False):
        rng = self.rng
        self.st[k]["td"] += 1
        self.add_points(k, 6)
        self.record("touchdown", "defense" if defensive else "offense", team=k, pts=6, scorer=self.td_by)
        self.td_by = None
        if self.ot is not None and defensive:
            self.finished = True                         # a defensive score ends overtime
            return
        if self.ot is not None and self.ot["done"]:
            self.finished = True                         # sudden death
            return
        self.try_after(k)
        self.next = ("kickoff", k)

    def try_after(self, k, skip=False):
        rng = self.rng
        if skip or self.finished:
            return
        d = self.lead(k)                           # after the six points, before the try
        go_two = d in (-2,) or (self.period >= 4 and self.t <= 600 and d in (-5, -8, 1) and rng.random() < 0.5)
        if self.ot is not None:
            go_two = False
        kicker = self.sq[k].units()["kicker"]
        if go_two:
            self.st[k]["two_att"] += 1
            good = rng.random() < P["two_prob"]
            if good:
                self.st[k]["two_made"] += 1
                self.add_points(k, 2)
            self.record("two-point try", team=k, pts=2 if good else 0, good=good)
        else:
            self.st[k]["pat_att"] += 1
            good = rng.random() < P["pat_prob"]
            self.pstat(kicker, "xp_att")
            if good:
                self.st[k]["pat_made"] += 1
                self.pstat(kicker, "xp_made")
                self.add_points(k, 1)
            self.record("extra point", team=k, pts=1 if good else 0, good=good, kicker=kicker.identity.id)
        self.tick(5, None)

    def safety(self, defending):
        """`defending` is the team awarded two points; the offense that gave it up kicks a free kick from its 20."""
        self.st[defending]["safeties"] += 1
        self.add_points(defending, 2)
        self.record("safety", team=defending, pts=2)
        self.next = ("kickoff", self.other(defending), True)
        if self.ot is not None:
            self.finished = True

    # ------------------------------------------------------------ the snap
    def hurry(self, k):
        """No-huddle: late in the first half unless leading, late in the fourth quarter when trailing, or late in overtime unless leading."""
        if self.period == 2 and self.t <= 120 and not self.lead(k) > 0:
            return True
        if self.ot is not None:
            return self.t <= 90 and self.lead(k) <= 0
        return self.period >= 4 and self.t <= 300 and self.lead(k) < 0

    def milk(self, k):
        return self.period >= 4 and self.ot is None and self.lead(k) > 0 and self.t <= 300

    def pass_prob(self, k):
        p = P["pass_rate_base"]
        d, down = self.dist, self.down
        if down == 3 or down == 4:
            p += 0.18 if d >= 5 else (-0.12 if d <= 2 else 0.04)
        elif down == 2:
            p += 0.07 if d >= 8 else (-0.08 if d <= 3 else 0)
        if self.yl >= 95:
            p -= 0.12
        if self.hurry(k):
            p += 0.25
        if self.milk(k):
            p -= 0.35
        elif self.lead(k) < -10 and self.period >= 3:
            p += 0.12
        elif self.lead(k) > 10 and self.period >= 3:
            p -= 0.10
        return clamp(p, 0.1, 0.95)

    def snap(self):
        k = self.poss
        o = self.other(k)
        rng = self.rng
        off, dfn = self.sq[k], self.sq[o]
        # kneel when protecting a late lead
        if (self.period == 4 and self.ot is None and self.lead(k) > 0 and self.down < 4
                and self.t <= 40 * (self.to[o] + 1) + 5 and self.t <= 160):
            self.play_result_kneel(k)
            return
        uo, ud = off.units(), dfn.units()
        zo, zd = uo["z"], ud["z"]
        stats = self.st[k]
        is_pass = rng.random() < self.pass_prob(k)
        self.last_is_pass = is_pass
        # pre-snap penalties
        pen = self.draw_penalty(PEN_PRE, "pre")
        if pen:
            self.apply_dead_ball_penalty(k, pen)
            return
        pre = production.State(self, k)
        if self.spatial:
            outcome = self.resolve_spatial(k, uo, ud, is_pass)
        else:
            outcome = self.resolve_pass(k, uo, ud) if is_pass else self.resolve_run(k, uo, ud)
        live = self.draw_penalty(PEN_PASS if is_pass else PEN_RUN, "live")
        if live:
            if self.apply_live_penalty(k, live, outcome):
                return
        before = len(self.ptab) if self.ptab is not None else 0
        self.finish_play(k, outcome)
        if self.spatial and self.ptab is not None and len(self.ptab) > before:
            self.ptab[before]["call"] = outcome.get("call")                  # the play and coverage called and the formation
            if outcome.get("trace"):
                self.ptab[before]["trace"] = outcome["trace"]
        production.credit_scrimmage(self, k, outcome, pre, uo, ud)
        self.injuries(k)

    # ---- resolutions return a dict: kind, yards, turnover(None|'int'|'fumble'), td, stop(bool), oob(bool), pass(bool)
    def resolve_run(self, k, uo, ud):
        rng = self.rng
        zo, zd = uo["z"], ud["z"]
        edge = 0.55 * (zo["runblock"] - zd["rdef"]) + 0.45 * (zo["rb"] - 0.5 * zd["rdef"] - 0.5 * zd["cover"] * 0)
        edge += zo["off_coach"] - zd["def_coach"] + (P["home_edge"] if k == 0 else -P["home_edge"])
        m = P["run_yards_mean"] + P["run_edge_yards"] * edge
        u = rng.random()
        if u < 0.095 - 0.02 * edge:
            yards = -rng.choice((1, 1, 2, 2, 3, 4))
        elif u < 0.095 + P["big_play_run"] + 0.01 * edge:
            yards = round(gamma_yards(rng, 22, 12))
        else:
            yards = round(gamma_yards(rng, max(1.5, m - 0.45), 3.4)) - (1 if rng.random() < 0.18 else 0)
        yards = int(clamp(yards, -8, 99))
        hb = uo["hb"]
        carrier = hb if rng.random() < 0.9 else (uo["wr"][0] if rng.random() < 0.3 else hb)
        fum = rng.random() < P["fumble_run"] * clamp(1 - 0.25 * (uo["z"]["rb"]), 0.5, 1.6)
        return {"kind": "run", "yards": yards, "turnover": "fumble" if fum else None, "pass": False,
                "oob": rng.random() < P["oob_run"] * (P["oob_hurry_mult"] if self.hurry(k) else 1), "carrier": carrier}

    def resolve_pass(self, k, uo, ud):
        rng = self.rng
        zo, zd = uo["z"], ud["z"]
        coach = zo["off_coach"] - zd["def_coach"] + (P["home_edge"] if k == 0 else -P["home_edge"])
        press = zd["prush"] - zo["pro"]
        sack_p = clamp(P["sack_base"] + P["sack_pressure_gain"] * press - 0.01 * zo["qb"], 0.015, 0.17)
        qb = uo["qb"]
        if rng.random() < sack_p:
            yards = -int(clamp(round(rng.gauss(6.5, 2.5)), 1, 15))
            fum = rng.random() < P["fumble_sack"]
            return {"kind": "sack", "yards": yards, "turnover": "fumble" if fum else None, "pass": True, "oob": False, "qb": qb}
        if rng.random() < P["scramble_base"] * (1 + 0.15 * clamp(press, -2, 3)):
            yards = int(clamp(round(gamma_yards(rng, 6.5, 5.5)), 0, 40))
            return {"kind": "scramble", "yards": yards, "turnover": None, "pass": True, "oob": rng.random() < 0.3, "qb": qb, "carrier": qb}
        edge = 0.45 * zo["qb"] + 0.55 * zo["recv"] - zd["cover"] + coach - 0.20 * max(0.0, press)
        comp_p = clamp(P["comp_base"] + P["comp_edge_gain"] * edge, 0.38, 0.86)
        int_p = clamp(P["int_base"] - P["int_edge_gain"] * edge + 0.004 * max(0.0, press), 0.004, 0.08)
        target = self.pick_target(uo)
        u = rng.random()
        if u < int_p:
            return {"kind": "int", "yards": 0, "turnover": "int", "pass": True, "oob": False, "qb": qb, "target": target,
                    "air": int(clamp(round(rng.gauss(11, 7)), 1, 45))}
        if u < int_p + comp_p:
            m = P["pass_yards_mean"] + P["pass_edge_yards"] * edge + 0.02 * (uo["raw"]["qb"] - 70)
            yards = int(clamp(round(gamma_yards(rng, max(5.0, m), P["pass_yards_sd"])), 0, 99))
            return {"kind": "complete", "yards": yards, "turnover": None, "pass": True,
                    "oob": rng.random() < P["oob_pass"] * (P["oob_hurry_mult"] if self.hurry(k) else 1), "qb": qb, "target": target,
                    "fumble": rng.random() < 0.006}
        return {"kind": "incomplete", "yards": 0, "turnover": None, "pass": True, "oob": False, "qb": qb, "target": target}

    def resolve_spatial(self, k, uo, ud, is_pass):
        """Play the snap out on the field (Edition 8.48): the situation picks the calls, 22 players move, and what happens is read off the
        trace. Nothing about the clubs enters the calls, so the situation is the same for everyone."""
        from .spatial import calls
        from .spatial.core import HASH_L, HASH_R, MID
        from .spatial.sim import PlaySim
        rng = self.rng
        down, dist, yl = self.down, self.dist, self.yl
        play = calls.pass_call(rng, down, dist, yl, self.hurry(k)) if is_pass else calls.run_call(rng, down, dist, yl, self.lead(k))
        cov = calls.coverage_call(rng, down, dist, yl, is_pass, self.lead(k))
        if self.ball_team != k or self.ball_y is None:
            self.ball_y = MID
        by = max(HASH_R, min(HASH_L, self.ball_y))
        zo, zd = uo["z"], ud["z"]
        edge = zo["off_coach"] - zd["def_coach"] + (P["home_edge"] if k == 0 else -P["home_edge"])
        sim = PlaySim(uo, ud, "pass" if is_pass else "run", play, cov, yl, by, random.Random(rng.getrandbits(64)), staff=(edge, 0.0),
                      trace=self.trace, down=down, dist=dist)
        o = sim.run()
        o["call"] = (play, cov, sim.formation)
        self.sim_secs = o["secs"]
        self.ball_team = k
        spot = getattr(sim, "spot", None)
        self.ball_y = spot[1] if spot else MID
        return o

    def credit_spatial(self, k, o, scores):
        """Book what the field play showed: who tackled, sacked, defended the pass, intercepted, forced and recovered (no draws)."""
        info = {}
        for card, key, amt in o.get("credits", ()):
            self.pstat(card, key, amt)
        if o["kind"] == "sack":
            self.pstat(o["qb"], "sacked")
            self.pstat(o["qb"], "sack_yds", -o["yards"])
        if o.get("tackler") is not None:
            info["tkl"] = o["tackler"]
        if o.get("assist") is not None:
            info["ast"] = o["assist"]
        if o.get("sacker"):
            info["sack"] = list(o["sacker"])
        for key in ("pd", "int_by", "ff", "fr"):
            if o.get(key) is not None:
                info[key] = o[key]
        return info

    def pick_target(self, uo):
        rng = self.rng
        cands = uo["wr"] + [uo["te"], uo["hb"]]
        w = [1.0, 0.95, 0.9, 0.7, 0.4]
        w = [wi * (0.6 + c.attrs.get("RTE", 50) / 100.0) for wi, c in zip(w, cands)]
        r = rng.random() * sum(w)
        for c, wi in zip(cands, w):
            r -= wi
            if r <= 0:
                return c
        return cands[0]

    # ------------------------------------------------------------ penalties
    def draw_penalty(self, cands, phase):
        """At most one foul on a snap, drawn from the catalogue rows that can happen in this phase (`cands`)."""
        rng = self.rng
        u = rng.random()
        acc = 0.0
        for pen in cands:
            acc += pen["rate"]
            if u < acc:
                side = pen["side"]
                if side == "either":
                    side = "off" if rng.random() < 0.5 else "def"
                return dict(pen, side=side)
        return None

    def penalty_yards(self, pen, k):
        y = pen["yards"]
        if isinstance(y, tuple):
            _, mean, sd = y
            if pen["loss_down"]:
                return int(clamp(round(rng_gauss(self.rng, mean, sd)), 10, 40))     # grounding: 10 yards or the spot, whichever is more
            return int(clamp(round(gamma_yards(self.rng, mean, sd)), 1, 60))
        return y

    def culprit(self, team, pen, unit=None):
        """The player who committed the foul: drawn from her side's players on the field by the position groups of the foul."""
        sq = self.sq[team]
        if unit is None:
            u = sq.units()
            unit = u["off"] if team == self.poss else u["def"]
        groups = []
        for g, w in pen["who"]:
            cs = [c for c in unit if g in ("ANYOFF", "ANYDEF", "ANY") or c.identity.pos in GROUPS.get(g, ())]
            if cs:
                groups.append((w, cs))
        if not groups:
            groups = [(1.0, list(unit))]
        r = self.rng.random() * sum(w for w, _ in groups)
        for w, cs in groups:
            r -= w
            if r <= 0:
                return self.rng.choice(cs)
        return self.rng.choice(groups[-1][1])

    def kick_unit(self, team):
        sq = self.sq[team]
        return [c for c in sq.cards.values() if c.identity.id not in sq.out]

    def charge(self, team, pen, yards, card=None):
        """Book an accepted foul against its team and player; a second unsportsmanlike foul ejects her."""
        st = self.st[team]
        st["penalties"] += 1
        st["pen_yds"] += yards
        d = self.pen_by[team].setdefault(pen["name"], [0, 0])
        d[0] += 1
        d[1] += yards
        if card is None:
            return
        self.pstat(card, "pen")
        self.pstat(card, "pen_yds", yards)
        if pen["name"] in UNSPORTSMANLIKE:
            n = self.uns[card.identity.id] = self.uns.get(card.identity.id, 0) + 1
            if n >= 2:
                self.sq[team].eject(card.identity.id)
                self.record("ejection", card.identity.id)

    def apply_dead_ball_penalty(self, k, pen):
        yards = self.penalty_yards(pen, k)
        team = k if pen["side"] == "off" else self.other(k)
        self.assess(k, pen, yards, self.culprit(team, pen))
        self.tick(self.rng.uniform(2, 5), None)

    def assess(self, k, pen, yards, card=None):
        """Enforce an accepted foul from the previous spot: the down is replayed (or lost, for a foul that costs the down);
        a defensive foul can give the offense a first down. Half the distance to the goal, never more."""
        o = self.other(k)
        team = k if pen["side"] == "off" else o
        spot = isinstance(pen["yards"], tuple) and not pen["loss_down"]
        if pen["side"] == "off":
            if yards > self.yl // 2:
                yards = self.yl // 2                              # half the distance to the goal
            self.charge(team, pen, yards, card)
            self.yl -= yards
            self.dist += yards
            if self.yl <= 0:
                self.yl = 1
            self.record("penalty", pen["name"], yards, card.identity.id if card else None, team=team, player=(card.identity.id if card else None))
            if pen["loss_down"]:
                self.down += 1
                if self.down > 4:
                    self.next = ("ball", o, int(clamp(100 - self.yl, 1, 99)))
            return
        if self.yl + yards >= 100 and spot:
            yards = max(0, 99 - self.yl)                          # a spot foul in the end zone puts the ball on the one
        elif yards > (100 - self.yl) // 2:
            yards = (100 - self.yl) // 2                          # half the distance to the goal
        self.charge(team, pen, yards, card)
        self.yl += yards
        if pen["auto_first"] or yards >= self.dist:
            self.down, self.dist = 1, min(10, 100 - self.yl)
            self.st[k]["first_downs"] += 1
            self.record("penalty", pen["name"], yards, card.identity.id if card else None, "first down", team=team, player=(card.identity.id if card else None))
            return
        self.dist -= yards
        self.record("penalty", pen["name"], yards, card.identity.id if card else None, team=team, player=(card.identity.id if card else None))

    def penalty_applies(self, pen, outcome):
        kind = outcome["kind"]
        n = pen["name"]
        if n in ("defensive pass interference", "offensive pass interference", "illegal contact", "ineligible downfield pass"):
            return kind in ("complete", "incomplete", "int")
        if n == "illegal forward pass":
            return kind == "incomplete"
        if n == "intentional grounding":
            return kind in ("incomplete", "sack")
        return True

    def apply_live_penalty(self, k, pen, outcome):
        """True if the foul is accepted and the play is void (replayed with the foul applied). False if it is declined, or if it is
        added after the play: then the yards ride on `outcome` (`pen_yds`, `auto_first`) and the play's own result stands."""
        if not self.penalty_applies(pen, outcome):
            return False
        o = self.other(k)
        yards = self.penalty_yards(pen, k)
        gain = outcome["yards"]
        team = k if pen["side"] == "off" else o
        card = self.culprit(team, pen)
        scores = self.yl + gain >= 100 and outcome["turnover"] is None and outcome["kind"] in ("run", "scramble", "complete")
        if pen["name"] in AFTER_PLAY and pen["side"] == "off" or pen["name"] in AFTER_PLAY and not outcome["turnover"]:
            if scores or (pen["side"] == "off" and outcome["turnover"]):
                return False                                        # enforced on the try or the kickoff; the engine does not model that
            end = self.yl + gain
            if pen["side"] == "off":
                yards = min(yards, max(0, end // 2))
                outcome["pen_yds"] = -yards
            else:
                yards = min(yards, max(0, (100 - end) // 2))
                outcome["pen_yds"] = yards
                outcome["auto_first"] = pen["auto_first"]
            self.charge(team, pen, yards, card)
            outcome["pen"] = True
            self.record("penalty", pen["name"], yards, card.identity.id, "after the play", team=team, player=(card.identity.id if card else None))
            return False
        if pen["side"] == "off":
            # the defense takes the foul unless the play was worse for the offense
            if outcome["turnover"] or gain <= -(yards + (3 if pen["loss_down"] else 0)):
                return False
            self.assess(k, pen, yards, card)
            self.tick(self.rng.uniform(6, 10), None)                # the play was run, then the flag is administered
            return True
        value = yards + (self.dist * 0.5 if pen["auto_first"] else 0)
        play_value = gain + (4 if gain >= self.dist else 0)
        if outcome["turnover"] or value > play_value:
            self.assess(k, pen, yards, card)
            self.tick(self.rng.uniform(6, 10), None)
            return True
        return False

    # ---- fouls on kicking plays (effects: replay_back, replay_fwd, rough, ret_back, cov_fwd, oob)
    def kick_flag(self, kind):
        u = self.rng.random()
        acc = 0.0
        for pen in PEN_KICK:
            if kind in pen["kinds"]:
                acc += pen["rate"]
                if u < acc:
                    return pen
        return None

    def kick_replay(self, k, pen):
        """A foul that sends the kick back: the kicking team's (5 yards back, replay), the receiving team's before or at the kick
        (5 forward, replay) or on the kicker (15 and a first down). Returns after moving the ball; the caller replays."""
        o = self.other(k)
        team = k if pen["side"] == "off" else o
        yards = pen["yards"]
        card = self.culprit(team, pen, self.kick_unit(team))
        if pen["effect"] == "replay_back":
            yards = min(yards, self.yl // 2)
            self.yl -= yards
            self.dist += yards
        else:
            yards = min(yards, (100 - self.yl) // 2)
            self.yl += yards
            if pen["effect"] == "rough" or yards >= self.dist:
                self.down, self.dist = 1, min(10, 100 - self.yl)
                self.st[k]["first_downs"] += 1
            else:
                self.dist -= yards
        self.charge(team, pen, yards, card)
        self.record("penalty", pen["name"], yards, card.identity.id, "kick", team=team, player=(card.identity.id if card else None))
        self.tick(self.rng.uniform(3, 6), None)

    def kick_spot(self, k, pen, spot):
        """A foul during the return or the coverage moves the receiving team's starting spot."""
        o = self.other(k)
        team = k if pen["side"] == "off" else o
        card = self.culprit(team, pen, self.kick_unit(team))
        yards = pen["yards"]
        if pen["effect"] == "ret_back":
            yards = min(yards, max(0, spot // 2))
            spot -= yards
        elif pen["effect"] == "cov_fwd":
            yards = min(yards, max(0, (100 - spot) // 2))
            spot += yards
        elif pen["effect"] == "oob":                                # the receiving team takes the ball at the 40
            spot = 40                                               # (replay_back fouls only cost the yardage booked)
        self.charge(team, pen, yards, card)
        self.record("penalty", pen["name"], yards, card.identity.id, "kick", team=team, player=(card.identity.id if card else None))
        return int(clamp(spot, 1, 99))

    # ------------------------------------------------------------ completing a play
    def play_result_kneel(self, k):
        qb = self.sq[k].units()["qb"]
        self.record("kneel", -1, car=qb.identity.id)
        self.st[k]["plays"] += 1
        self.st[k]["rush_att"] += 1
        self.st[k]["rush_yds"] -= 1
        self.pstat(qb, "car")
        self.pstat(qb, "rush_yds", -1)
        self.yl = max(1, self.yl - 1)
        self.dist += 1
        self.down += 1
        self.advance_after(k, -1, stop=False, kneel=True)
        if self.down > 4 and self.next is None:
            self.next = ("ball", self.other(k), 100 - self.yl)

    def finish_play(self, k, o):
        rng = self.rng
        s = self.st[k]
        kind, yards = o["kind"], o["yards"]
        if o["turnover"] is None and kind in ("run", "scramble", "complete") and self.yl + yards > 100:
            yards = o["yards"] = 100 - self.yl                    # a touchdown counts the yards to the goal line, not past it
        s["plays"] += 1
        if kind in ("run", "scramble"):
            s["rush_att"] += 1
            s["rush_yds"] += yards
            self.pstat(o["carrier"], "car")
            self.pstat(o["carrier"], "rush_yds", yards)
        elif kind == "sack":
            s["sacks"] += 0
            self.st[self.other(k)]["sacks"] += 1
            s["pass_yds"] += 0
            s["sack_yds"] += -yards
        elif kind in ("complete", "incomplete", "int"):
            s["pass_att"] += 1
            self.pstat(o["qb"], "att")
            if o.get("target") is not None:
                self.pstat(o["target"], "tgt")
            if kind == "complete":
                s["comp"] += 1
                s["pass_yds"] += yards
                self.pstat(o["qb"], "comp")
                self.pstat(o["qb"], "pass_yds", yards)
                self.pstat(o["target"], "rec")
                self.pstat(o["target"], "rec_yds", yards)
        if self.down == 3:
            s["third_att"] += 1
        if self.down == 4:
            s["fourth_att"] += 1
        scores = o["turnover"] is None and self.yl + yards >= 100
        info = self.ids(o)
        info.update(self.credit_defense(k, o, scores))
        # turnovers
        if o["turnover"] == "int":
            s["ints"] += 1
            self.pstat(o["qb"], "int")
            spot = int(clamp(self.yl + o["air"], 2, 99))
            if o.get("spatial"):
                self.record("interception", o["air"], **info)
                self.tick(o["secs"], k)
                if o["ret_td"]:
                    self.poss = self.other(k)
                    self.td_by = info.get("int_by")
                    self.score_td(self.poss, defensive=True)
                    return
                ex = o["ret_x"]
                self.next = ("ball", self.other(k), 20 if self.yl + o["air"] >= 100 else int(clamp(100 - round(ex), 1, 99)))
                return
            self.record("interception", o["air"], **info)
            self.tick(rng.uniform(5, 8), k)
            if rng.random() < 0.03:
                self.poss = self.other(k)
                self.td_by = info.get("int_by")
                self.score_td(self.poss, defensive=True)
                return
            ret = max(0, round(abs(rng.gauss(6, 8))))
            new = int(clamp(100 - spot + ret, 1, 99)) if spot < 100 else 20
            self.next = ("ball", self.other(k), new if spot < 100 else 20)
            return
        fumble = o["turnover"] == "fumble" or o.get("fumble")
        new_yl = self.yl + yards
        if fumble and o["turnover"] == "fumble":
            s["fumbles_lost"] += 1
            self.record("fumble lost", yards, **info)
            self.tick(rng.uniform(5, 8), k)
            if new_yl <= 0:
                self.safety(self.other(k))
                return
            if rng.random() < 0.03:
                self.poss = self.other(k)
                self.td_by = info.get("fr")
                self.score_td(self.poss, defensive=True)
                return
            self.next = ("ball", self.other(k), int(clamp(100 - new_yl, 1, 99)))
            return
        # ordinary result
        self.record(kind, yards, **info)
        if new_yl >= 100:
            self.tick(rng.uniform(4, 7), k)
            if kind == "complete":
                self.pstat(o["target"], "rec_td")
                self.pstat(o["qb"], "pass_td")
            elif kind in ("run", "scramble"):
                self.pstat(o["carrier"], "rush_td")
            self.td_by = info.get("tgt") if kind == "complete" else info.get("car")
            if self.down == 3:
                s["third_conv"] += 1
            if self.down == 4:
                s["fourth_conv"] += 1
            s["first_downs"] += 1
            self.score_td(k)
            return
        if new_yl <= 0:
            self.tick(rng.uniform(4, 7), k)
            self.safety(self.other(k))
            return
        pen_yds = o.get("pen_yds", 0)
        self.yl = new_yl + pen_yds
        stop = kind == "incomplete" or o["oob"] or bool(o.get("pen"))
        made = yards >= self.dist or bool(o.get("auto_first"))
        self.advance_after(k, yards + pen_yds, stop, first=made)

    def advance_after(self, k, yards, stop, first=False, kneel=False):
        """Update downs, run the clock for the play and the time before the next snap, manage timeouts."""
        rng = self.rng
        s = self.st[k]
        o = self.other(k)
        made = first or (yards >= self.dist and not kneel)
        if made and not kneel:
            if self.down == 3:
                s["third_conv"] += 1
            if self.down == 4:
                s["fourth_conv"] += 1
            s["first_downs"] += 1
            self.down, self.dist = 1, min(10, 100 - self.yl)
            self.tag(first=True)
        elif not kneel:
            self.dist -= yards
            self.down += 1
            if self.down > 4:
                self.next = ("ball", o, int(clamp(100 - self.yl, 1, 99)))
        play_t = P["play_time_pass"] if self.last_is_pass else P["play_time_run"]
        if kneel:
            delay = 40
            play_t = 2
        elif stop:
            delay = 0
        elif made:
            delay = rng.uniform(P["delay_chains_lo"], P["delay_chains_hi"])
        elif self.hurry(k):
            delay = rng.uniform(P["delay_hurry_lo"], P["delay_hurry_hi"])
        else:
            delay = rng.uniform(P["delay_normal_lo"], P["delay_normal_hi"])
        if self.milk(k) and not stop:
            delay = max(delay, 36)
        if delay > 8 and self.timeout_wanted(k):
            delay = 4
        if self.spatial and not kneel and self.sim_secs is not None:
            play_t = self.sim_secs
        else:
            play_t = max(2.0, rng.gauss(play_t, 1.2)) if not kneel else play_t
        self.tick(play_t + delay, k)

    last_is_pass = False

    def timeout_wanted(self, k):
        """A timeout is taken at the end of a play that kept the clock running, late in a half, by a side that needs the time."""
        o = self.other(k)
        late = self.t <= 150 and (self.period == 2 or self.period >= 4)
        if not late or self.ot is not None and self.t > 90:
            return False
        rng = self.rng
        if self.to[k] > 0 and (self.behind(k) or (self.period == 2 and self.lead(k) == 0)) and rng.random() < 0.85:
            self.to[k] -= 1
            self.record("timeout", team=k, left=self.to[k])
            return True
        if self.to[o] > 0 and self.behind(o) and rng.random() < 0.85:
            self.to[o] -= 1
            self.record("timeout", team=o, left=self.to[o])
            return True
        return False

    # ------------------------------------------------------------ injuries
    def injuries(self, k):
        rng = self.rng
        p_any = 22 * P["injury_rate"]
        if rng.random() >= p_any:
            return
        team = k if rng.random() < 0.5 else self.other(k)
        sq = self.sq[team]
        u = sq.units()
        pool = u["off"] if team == k else u["def"]
        w = [1.45 - c.attrs.get("DUR", 60) / 100.0 for c in pool]
        r = rng.random() * sum(w)
        for c, wi in zip(pool, w):
            r -= wi
            if r <= 0:
                weeks = self.draw_weeks()
                sq.injure(c.identity.id, weeks)
                self.record("injury", c.identity.id, weeks)
                return

    def draw_weeks(self):
        r = self.rng.random() * sum(w for _, w in INJURY_WEEKS)
        for wk, w in INJURY_WEEKS:
            r -= w
            if r <= 0:
                return wk
        return 1


def rng_gauss(rng, m, s):
    return rng.gauss(m, s)


def play_game(home, away, rng, decisive=False, log=False, curve=None, spatial=False, trace=False):
    """Play one game between two `Squad`s. `decisive` forbids a tie (postseason, Coronation). `spatial` plays every scrimmage snap out with
    22 players on the field (Edition 8.48) instead of resolving it at the play level."""
    g = Game(home, away, rng, decisive, log, curve, spatial, trace)
    return g.run()
