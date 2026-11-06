"""Season mode of the broadcast daemon: one league year, built privately ahead of time and released only as the broadcast clock reaches it.

Three stores, three owners (docs/BROADCAST_PLAN.md, "Season integration"):

  PLAN    store/plan.json    written once by tools/broadcast_plan.py: the nominal timetable (windows and blocks), which window every game belongs to
  BUILDS  store/builds.json  appended by tools/broadcast_build.py, one event at a time: private feed releases, game recordings, honours parts
  PUBLIC  <public>/          written ONLY by `SeasonBroadcaster.tick`: nothing in it exists before the clock reaches it

`materialize(plan, builds)` turns the first two into what the daemon needs: the private schedule (actual window starts, with every overrun applied), the list of
timed reveals, and the HORIZON, the first instant for which something is not yet built. The clock is never allowed past the horizon, so a slow builder makes the
broadcast stand still (and says so), never skip or show the wrong thing.

Overruns.  A window lasts max(slot, longest game + viewer delay). The excess shifts every later window and block by the same number of seconds, so later
breaks keep their length and nothing is accelerated or skipped. The shift is private until it is visible: the public schedule moves later windows only once a
slot has expired with a game still live (broadcast.make_schedule).

What is released when, in one pass (the order inside a block is: files first, pointers last):
  * a block's last league event: its feed release directory, then `latest.json`; the replay files of the games of its events, then `playback/index.json`
  * `broadcast/honors/<part>.json` parts, then `broadcast/honors/index.json`
  * `status.json` is rewritten by the daemon itself from BROADCAST progress (released events, the clock's state), never the league's

Several seasons (docs/BROADCAST_CONTINUITY.md).  The first season's plan and builds stay at the store root. The builder makes each later season in
store/seasons/sNNN/ (its own plan and builds, no genesis release), possibly while the earlier season is still on air. When the clock reaches the end of the
season on air and the next one is complete and fits, THE DAEMON writes store/carry.json in one atomic step: it records what the finished seasons made public
(their replay-index entries, replay library, Honors parts, last release) and where the new season starts on the same, never-restarted broadcast clock
(`offset_s`: the actual end of the season before, overruns included). That one file is the whole hand-over: before it the old season is current, after it the new
one is, and a crash can leave nothing in between. The carried records are placed back into every cumulative index from the first second of the new season, so
the replay library, the playback index, the Honors index and `latest.json` never lose or reorder an earlier season; the earlier seasons' files are not touched.
"""
import json
import math
import os
import re
import time

from . import broadcast as B
from . import playback as PB
from . import status as ST

PLAN = "plan.json"
BUILDS = "builds.json"
SEASONS = "seasons"                    # store/seasons/sNNN/{plan,builds}.json: the second and later seasons (the first stays at the root, as before)
CARRY = "carry.json"
HANDOVER_GRACE_S = 10                             # a hand-over this close to the end is on time (the daemon ticks every second and a prepared successor stops the clock at the end)
CONTINUOUS = "continuous"                         # marker file in the store: the broadcast is a recurring league, so the clock holds at the end of a season until the next one is on air                   # written by the daemon at the end of a season: what the earlier seasons already made public, and where this season starts
CARRY_FORMAT = "dfl-broadcast-carry"
PAUSE = "PAUSE"
LOOKAHEAD_S = 6 * 3600
STATUS_EVERY_S = 5
PLAN_FORMAT = "dfl-broadcast-plan"
BUILDS_FORMAT = "dfl-broadcast-builds"
HONORS_INDEX_FORMAT = "dfl-broadcast-honors"


class SeasonError(Exception):
    pass


# ---------------------------------------------------------------------------------------------------------------------------------- assignment
def _po_order(gid):
    m = re.match(r"^S\d+/po:([^:]+):([^:]+):s(\d+)v(\d+)$", gid)
    if not m:
        raise SeasonError("not a playoff game id: " + gid)
    return (m.group(1), int(m.group(3)), int(m.group(4)))


def assign_round(plan, round_key, gids):
    """{gid: window key} for one postseason round. Games are listed by conference and then seed, and cut into the round's windows by their sizes (an
    Eastern pair first, then a Western pair): two games kick off together and nothing else about a game decides its window."""
    sizes, keys = plan["round_sizes"][round_key], plan["rounds"][round_key]
    ordered = sorted(gids, key=_po_order)
    if len(ordered) != sum(sizes):
        raise SeasonError("%s: %d games found, the plan has room for %d" % (round_key, len(ordered), sum(sizes)))
    out, i = {}, 0
    for k, n in zip(keys, sizes):
        for g in ordered[i:i + n]:
            out[g] = k
        i += n
    return out


def window_of(plan, gid):
    """The plan window key of a regular-season, Tour, Bowl or Coronation game id like 'S001/reg:w01:g01'; None for a postseason round game (assign_round)."""
    season, rest = gid.split("/", 1)
    if season != "S%03d" % plan["season"]:
        raise SeasonError("%s does not belong to season %d" % (gid, plan["season"]))
    if rest in plan["games"]:
        return plan["games"][rest]
    if rest == "bowl":
        return plan["single"]["ambassador_bowl"]
    if rest == "coronation":
        return plan["single"]["coronation"]
    return None


# ---------------------------------------------------------------------------------------------------------------------------------- materialize
class View:
    def __init__(self):
        self.windows, self.reveals, self.pointers, self.horizon = [], [], [], None
        self.horizon_why = None
        self.events_revealed = 0
        self.total_s = None
        self.complete = False            # every item of the season is built (the broadcast can reach its end)
        self.end_s = None                # the actual end on the broadcast clock, overruns included (only when complete)
        self.last = None                 # the last release of a complete season
        self.playback, self.honors_parts, self.carry_replays = [], [], []


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def honors_unavailable():
    """What the Honors show cannot present, said in public so nobody reads a missing award as an omission or an invention."""
    return [
        {"award": "Season MVP", "reason": "not implemented in the league engine"},
        {"award": "All-League teams", "reason": "not implemented in the league engine"},
        {"award": "Rookie of the Year", "reason": "not implemented in the league engine"},
        {"award": "Coach of the Year", "reason": "not implemented in the league engine"},
        {"award": "Statistical leaders' awards", "reason": "not implemented in the league engine"},
        {"award": "Halls for coaches, executives and staff", "reason": "staff hold Tiaras but have no halls yet (Edition 8.30)"},
    ]


def _playback_index(builds, entries):
    return {"format": PB.FORMAT, "format_version": PB.VERSION, "kind": "index", "league_id": builds.get("league_id"), "games": sorted(entries, key=lambda e: e["gid"])}


def _honors_index(parts):
    return {"format": HONORS_INDEX_FORMAT, "format_version": 1, "kind": "index", "parts": [{k: x[k] for k in ("part", "season", "file")} for x in parts],
            "unavailable": honors_unavailable()}


def materialize(plan, builds, carry=None, hold_at_end=False):
    """-> View. Pure function of the plan, the builds and (for a later season) the carry record (no clock, no files).
    With a `carry` the season starts at `carry["offset_s"]` on the broadcast clock and every cumulative public file starts from what the earlier seasons made public.
    `hold_at_end`: when the whole season is built, the clock may not run past its end (the next season has not started)."""
    if plan.get("format") != PLAN_FORMAT or builds.get("format") != BUILDS_FORMAT:
        raise SeasonError("not a broadcast plan / builds file")
    if carry is not None and carry.get("format") != CARRY_FORMAT:
        raise SeasonError("not a broadcast carry record")
    if carry is not None:
        if int(carry["season"]) != int(plan["season"]):
            raise SeasonError("the carry record starts season %s but the plan is season %s" % (carry["season"], plan["season"]))
        used = {e["gid"] for e in carry["playback"]}
        again = sorted(set(builds["games"]) & used)
        if again:
            raise SeasonError("game ids already played in an earlier season: %s" % again[:3])
        base = int(plan.get("event_base", 0))
        if base != int(carry["last"]["events"]) or any(int(k) <= base for k in builds["events"]):
            raise SeasonError("the season's events do not follow the last one made public (%s)" % carry["last"]["events"])
    ev_seq = {rec["key"]: int(seq) for seq, rec in builds["events"].items()}
    games = builds["games"]
    by_window = {}
    for gid in sorted(games):
        k = window_of(plan, gid)
        if k is not None:
            by_window.setdefault(k, []).append(gid)
    for rk in plan["rounds"]:
        gids = [g for g, rec in games.items() if rec["key"] == rk]
        if len(gids) == sum(plan["round_sizes"][rk]):
            for g, k in assign_round(plan, rk, gids).items():
                by_window.setdefault(k, []).append(g)
    v = View()
    offset = int(carry["offset_s"]) if carry else 0
    v.total_s = plan["total_s"] + offset
    shift, announce_at, order = offset, offset, 0
    playback = [dict(e) for e in carry["playback"]] if carry else []
    honors_parts = [dict(x) for x in carry["honors"]] if carry else []
    v.carry_replays = [dict(x) for x in carry["replays"]] if carry else []

    def add(**r):
        nonlocal order
        order += 1
        r["order"] = order
        v.reveals.append(r)

    if carry:                                              # the state the earlier seasons left public: restated at the first second so no cumulative file is ever dropped
        last = carry["last"]
        add(at_s=offset, src="feed/releases/" + last["release"], dst="releases/" + last["release"])
        add(at_s=offset, src=last["pointer"], dst="latest.json", group="latest", last=True)
        v.pointers.append({"at_s": offset, "release": last["release"], "events": last["events"]})
        v.events_revealed = last["events"]
        if playback:
            add(at_s=offset, dst="playback/index.json", data=PB.canonical(_playback_index(builds, playback)), group="playback-index", last=True)
        if honors_parts:
            add(at_s=offset, dst="broadcast/honors/index.json", data=_canonical(_honors_index(honors_parts)), group="honors-index", last=True)
    gen = builds.get("genesis")
    if gen:                                                # the state of the league before the broadcast began
        add(at_s=0, src="feed/releases/" + gen["release"], dst="releases/" + gen["release"])
        add(at_s=0, src=gen["pointer"], dst="latest.json", group="latest", last=True)
        v.pointers.append({"at_s": 0, "release": gen["release"], "events": gen["events"]})
    for it in plan["items"]:
        if it["kind"] == "window":
            gids = sorted(by_window.get(it["key"], []))
            if len(gids) < it["expected"]:
                v.horizon, v.horizon_why = it["start_s"] + shift, "window %s not built" % it["key"]
                break
            recs = [games[g] for g in gids]
            dur = max(math.ceil(r["length_s"] + r["delay_s"]) for r in recs)
            over = max(0, dur - it["slot_s"])
            win = {"key": it["key"], "label": it["label"], "start_s": it["start_s"] + shift, "nominal_s": it["start_s"] + offset, "slot_s": it["slot_s"], "overrun_s": over,
                   "games": [{"gid": g, "away": games[g]["away"], "home": games[g]["home"], "segments": games[g]["segments"], "length_s": games[g]["length_s"],
                              "replay": games[g]["replay"]} for g in gids]}
            if it["gated"]:
                win["announce_s"] = announce_at
            v.windows.append(win)
            shift += over
            continue
        t0 = it["start_s"] + shift
        missing = [k for k in it["events"] if k not in ev_seq] + [p["part"] for p in it["parts"] if p["part"] not in builds["honors"]]
        if missing:
            v.horizon, v.horizon_why = t0, "block %r not built (%s)" % (it["label"], ", ".join(missing))
            break
        last = builds["events"][str(ev_seq[it["events"][-1]])]
        at = t0 + it["events_at"]
        add(at_s=at, src="feed/releases/" + last["release"], dst="releases/" + last["release"])
        add(at_s=at, src=last["pointer"], dst="latest.json", group="latest", last=True)
        v.pointers.append({"at_s": at, "release": last["release"], "events": last["events"]})
        v.events_revealed = last["events"]
        announce_at = at
        for k in it["events"]:
            for g in sorted(games):
                if games[g]["key"] == k:
                    entry = games[g]["entry"]
                    add(at_s=at, src="playback/" + entry["file"], dst="playback/" + entry["file"])
                    playback.append(entry)
        if playback:
            add(at_s=at, dst="playback/index.json", data=PB.canonical(_playback_index(builds, playback)), group="playback-index", last=True)
        for p in it["parts"]:
            pat = t0 + p["at"]
            rel = builds["honors"][p["part"]]
            add(at_s=pat, src="honors/" + rel, dst="broadcast/honors/" + rel)
            honors_parts.append({"part": p["part"], "season": plan["season"], "file": rel, "at_s": pat})
            add(at_s=pat, dst="broadcast/honors/index.json", data=_canonical(_honors_index(honors_parts)), group="honors-index", last=True)
    else:                                                  # every item was reached: the season is fully built
        v.complete = True
        v.end_s = plan["items"][-1]["end_s"] + shift
        v.last = {"release": last["release"], "pointer": last["pointer"], "events": last["events"]}
        v.playback, v.honors_parts = playback, honors_parts
        if hold_at_end:
            v.horizon, v.horizon_why = v.end_s, "season complete; the next season has not started"
    return v


# ---------------------------------------------------------------------------------------------------------------------------------- the daemon
class SeasonBroadcaster(B.Broadcaster):
    """The season's broadcast process: `tick(now)` about once a second (tools/broadcast_run.py). Everything it needs is in the store; it holds no state of
    its own that a restart could lose (the clock is a file, the public tree is repaired to match the clock on the first tick)."""

    def __init__(self, store, public, lease=B.LEASE_S, guard_publication=True, plan=None, builds_fn=None, state_dir=None):
        """`plan`, `builds_fn` (() -> (cache key, builds)) and `state_dir` are for tests and the season check, which run a real store under a changed plan or with the
        builder deliberately behind, and must not write a clock into it; the daemon passes none of them. Without them the daemon follows the store's seasons."""
        super().__init__(store, public, lease, guard_publication)
        self.state_dir = state_dir or store                     # the clock, its log and the PAUSE switch (the season check keeps them away from a real store)
        self.clock = B.Clock(self.state_dir, public)
        self._builds_fn = builds_fn
        self._fixed_plan = plan
        self._view = None
        self._key = None
        self._holding = False
        self._failed = False
        self._memo = {}
        self._status_at = 0.0
        self._moving = None
        self._blocked = None                                    # why a prepared next season was not started (said once)
        self._cur = None
        self.plan = None
        self._load_current()

    # ------------------------------------------------------------------------------------------------------------------------ which season is on air
    def _stat(self, path):
        try:
            p = os.stat(path)
            return (p.st_mtime_ns, p.st_size)
        except OSError:
            return None

    def _load_current(self):
        """Read the carry record (if any) and select the season on air: its plan, its builds file. A single atomic file decides, so a restart always agrees."""
        carry = None
        cpath = os.path.join(self.store, CARRY)
        if self._fixed_plan is None and os.path.exists(cpath):
            carry = B.get(cpath)
            bdir = os.path.join(self.store, SEASONS, "s%03d" % int(carry["season"]))
            self.plan = B.get(os.path.join(bdir, PLAN))
        else:
            bdir = self.store
            self.plan = self._fixed_plan if self._fixed_plan is not None else B.get(os.path.join(self.store, PLAN))
        self._carry, self._bdir = carry, bdir
        return carry

    def _next_dir(self):
        return os.path.join(self.store, SEASONS, "s%03d" % (int(self.plan["season"]) + 1))

    def view(self):
        if self._builds_fn is not None:
            key, builds = self._builds_fn()
            if self._view is None or key != self._key:
                self._view, self._key = materialize(self.plan, builds), key
            return self._view
        bpath = os.path.join(self._bdir, BUILDS)
        hold = self._carry is not None or os.path.exists(os.path.join(self._next_dir(), PLAN)) or os.path.exists(os.path.join(self.store, CONTINUOUS))
        key = (self._stat(bpath), self._stat(os.path.join(self.store, CARRY)), hold)
        if self._view is None or key != self._key:
            if key[0] is None:
                builds = {"format": BUILDS_FORMAT, "events": {}, "games": {}, "honors": {}}
            else:
                builds = B.get(bpath)
            self._view, self._key = materialize(self.plan, builds, self._carry, hold_at_end=hold), key
        return self._view

    # ------------------------------------------------------------------------------------------------------------------------ the hand-over
    def _carry_record(self, v, pos=None):
        """What the season now ending made public, added to what the earlier ones had: the single record that starts the next season."""
        prior = self._carry or {"seasons": [], "playback": [], "honors": [], "replays": []}
        # the next season starts at the end of this one, or at the clock's position if the clock already ran past the end (a store not marked continuous): never in the past
        offset = v.end_s if pos is None or pos - v.end_s <= HANDOVER_GRACE_S else int(math.ceil(pos - 1e-3))
        replays = [dict(r) for r in prior["replays"]]
        for w in v.windows:
            for g in w["games"]:
                src = os.path.join(self.store, "games", B.safe(g["gid"]))
                end, head = B.get(os.path.join(src, "end.json")), B.get(os.path.join(src, "head.json"))
                replays.append({"gid": g["gid"], "replay": end["replay"], "final": end["final"], "season": head.get("season"), "game_kind": head.get("game_kind")})
        return {"format": CARRY_FORMAT, "format_version": 1, "season": int(self.plan["season"]) + 1, "seasons": list(prior["seasons"]) + [int(self.plan["season"])],
                "offset_s": offset, "playback": sorted(v.playback, key=lambda e: e["gid"]), "honors": v.honors_parts, "replays": sorted(replays, key=lambda r: r["gid"]),
                "last": v.last}

    def _maybe_hand_over(self, now):
        """At the end of the season on air, start the next one if it is prepared, built far enough to be named and fits what has been made public. Nothing else
        ever changes season: a prepared season that does not fit is reported (stderr, once) and the clock holds at the end."""
        if self._fixed_plan is not None or self._builds_fn is not None:
            return
        nd = self._next_dir()
        if not (os.path.exists(os.path.join(nd, PLAN)) and os.path.exists(os.path.join(nd, BUILDS))):
            return
        st = self.clock.state()
        if st is None:
            return
        v = self.view()
        if not v.complete or self.clock.position(now) < v.end_s - 1e-6:
            return
        carry = self._carry_record(v, self.clock.position(now))
        try:
            nplan, nbuilds = B.get(os.path.join(nd, PLAN)), B.get(os.path.join(nd, BUILDS))
            if int(nplan["season"]) != carry["season"]:
                raise SeasonError("the prepared season is %s, season %s is next" % (nplan["season"], carry["season"]))
            materialize(nplan, nbuilds, carry)                                   # every game id, event number and window must fit what is already public
        except (SeasonError, KeyError, ValueError, OSError) as e:
            why = "%s: %s" % (type(e).__name__, str(e)[:200])
            if why != self._blocked:
                self._blocked = why
                import sys
                import json as _json
                print(_json.dumps({"season_not_started": True, "season": carry["season"], "why": why, "at": now}), file=sys.stderr, flush=True)
            return
        B.put(os.path.join(self.store, CARRY), carry)                              # the one atomic step: the next season is on air from here on
        self._blocked = None
        self._load_current()
        self._view = None
        self._memo = {}

    def schedule(self):
        v = self.view()
        return {"windows": v.windows, "lookahead_s": LOOKAHEAD_S, "carry_replays": v.carry_replays}

    def reveals(self):
        return self.view().reveals

    def _control(self, now):
        st = self.clock.state()
        if st is None:
            return
        wanted = os.path.exists(os.path.join(self.state_dir, PAUSE))
        if wanted and st["state"] == "running":
            self.clock.pause(now)
        elif not wanted and st["state"] == "paused":
            self.clock.resume(now, self.lease)

    def tick(self, now=None):
        now = time.time() if now is None else now
        try:
            return self._tick(now)
        except BaseException as e:
            self._failed = True
            try:
                self._status(now, ok=False, error=type(e).__name__, force=True)
            except Exception:
                pass
            raise

    def _tick(self, now):
        self._control(now)
        self._maybe_hand_over(now)
        view = self.view()
        st = self.clock.state()
        hold = False
        if st["state"] == "running" and view.horizon is not None:
            hold = self.clock.position(now) >= view.horizon - 1e-6
        recovering = self._failed
        # while publication is failing the daemon is alive but must not re-anchor the clock every second: the outage is recorded once, when it ends
        down = self.clock.heartbeat(now, self.lease, holding=hold or recovering, why="coverage_wait" if self._holding else "downtime")
        self._holding = hold
        pos = self.clock.position(now)
        placed, removed = B.reconcile(self.store, self.public, self.schedule(), pos, view.reveals, self._memo)
        if recovering:
            down = self.clock.heartbeat(now, self.lease, holding=hold, why="publish_failure")
            pos = self.clock.position(now)
            self._failed = False
        if self.guard_publication:
            ready = pos + B.DELAY_S
            if view.horizon is not None:
                ready = min(ready, view.horizon)
            st = self.clock.state()
            st["ready_until"] = ready
            B.put(self.clock.path, st)
            B.put(self.clock.mirror, st)
        self._status(now, ok=True, pos=pos, view=view, placed=placed)
        return {"pos": pos, "down_s": down, "placed": placed, "removed": removed, "holding": hold, "horizon": view.horizon}

    # ------------------------------------------------------------------------------------------------------------------------ status.json
    def _status(self, now, ok, error=None, pos=None, view=None, placed=0, force=False):
        """The heartbeat the site reads (feed/status.json, format dfl-feed-status 1), written from BROADCAST progress: the number of events whose results are
        public, the clock's own state. The league's progress, the builder's progress and the horizon never appear here."""
        if pos is None:
            pos = self.clock.position(now) or 0.0
        view = view or self.view()
        st = self.clock.state()
        moving = bool(st) and st["state"] == "running" and now < B.clock_limit(st) - 1e-6 and not (view.horizon is not None and pos >= view.horizon - 1e-6)
        # a change between running and paused (the PAUSE switch, a coverage hold) is written at once: the pages must not say "running" while the clock stands still
        changed = moving != self._moving
        if not force and not changed and placed == 0 and now - self._status_at < STATUS_EVERY_S:
            return
        self._status_at = now
        self._moving = moving
        path = os.path.join(self.public, ST.FILE)
        previous = ST.read(path)
        pdoc = (previous or {}).get("export") or {}
        t = ST.now_iso(now)
        due = [p for p in view.pointers if p["at_s"] <= pos]
        cur = max(due, key=lambda p: (p["at_s"], p["events"])) if due else None
        last_pub = ST.now_iso(now) if placed else pdoc.get("last_publish_at")
        doc = {"format": ST.FORMAT, "format_version": ST.VERSION, "checked_at": t, "league_events": cur["events"] if cur else 0,
               "published_events": cur["events"] if cur else None, "release": cur["release"] if cur else None, "source": "broadcast",
               "export": {"ok": bool(ok), "error": None if ok else {"type": re.sub(r"[^A-Za-z0-9_.]", "", str(error or "Error"))[:60], "at": t},
                          "last_success_at": t if ok else pdoc.get("last_success_at"), "last_publish_at": last_pub, "seconds": None},
               "runner": {"state": "running" if moving else "paused", "updated_at": t, "pace_seconds": None, "next_event_due": None,
                          "last_event_at": last_pub, "events": cur["events"] if cur else 0, "until_events": None, "limit_reached": False}}
        ST.write_atomic(path, doc)
