"""The broadcast layer: one authoritative clock, protected live game segments, timed reveals (docs/BROADCAST_PLAN.md).

The league may be computed ahead of what the public sees. This module is the only thing that decides when a piece of finished data becomes public:

  store   (private, never served)  everything computed: full replays, the timetable with results, the clock's own state and log
  public  (served as static files) only what the broadcast clock has already released

Nothing is "hidden" by an access rule: an unreleased file does not exist in the public tree, so a direct request for it is a plain 404, and a copy of the
public tree at any moment contains nothing that is not already on the viewers' screens (give or take the few seconds named below).

Clock.  `position(state, now)` is the broadcast position in seconds. It runs at one second per second while the broadcast is running and the daemon's lease
is valid, and stands still while paused or after its lease/publication horizon has lapsed (the daemon died or the server was down). When the daemon comes back it records the
gap as a pause: there is no catch-up and nothing is skipped. All times are broadcast seconds from the start of the year's broadcast; wall time enters only
through `anchor_wall` and `lease_until`.

Live segments.  A game kicks off at its window's start. Its plays are laid out on the game's own timeline (a traced play lasts trace.t_end + HOLD_S, any
other play CARD_S, at speed 1; a long game must extend its window, never accelerate). The timeline is cut into SEG_S-second
segments by the time each play STARTS. Segment n is released when the broadcast is n * SEG_S seconds into the game; a viewer shows game time
`position - kickoff - DELAY_S`. Whole plays starting in that bucket are public, including their complete traces/result text. This is buffered publication, not frame-level secrecy; a long play can expose more than SEG_S + DELAY_S seconds. Endings and total play counts stay private until the last play is shown.
"""
import hashlib
import json
import os
import shutil
import time

from . import playback as PB

HOLD_S = 1.2            # site/assets/playback.js: pause after a traced play ends
CARD_S = 2.6            # site/assets/playback.js: an event card without movement
SEG_S = 5               # a live segment holds the plays that start in these seconds of game time
DELAY_S = 3             # viewers show the game this far behind the released data
SLOT_MARGIN_S = 30      # a game must end this long before its slot does
LEASE_S = 90            # the clock stands still this long after the daemon's last heartbeat

CLOCK_FORMAT = "dfl-broadcast-clock"
LIVE_KEYS = {"format", "format_version", "kind", "gid", "id", "season", "game_kind", "date", "kickoff", "neutral", "teams", "roster", "identity_note",
             "sampling", "field", "broadcast"}
FINAL_KEYS = {"format", "format_version", "kind", "gid", "final", "winner", "tie", "periods", "plays", "traced_plays", "replay"}
SEG_PLAY_KEYS = PB.ALLOWED["play"] | {"t0", "dur"}


class BroadcastError(Exception):
    pass


# ---------------------------------------------------------------------------------------------------------------------------------- files
FSYNC = True            # tests that tick through a whole simulated day switch this off; a real daemon never does


def put(path, obj):
    """Write JSON atomically: a reader sees the old file or the whole new one. Temporary names start with a dot (never served)."""
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, ".tmp-%d-%s" % (os.getpid(), os.path.basename(path)))
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    with open(tmp, "wb") as f:
        f.write(raw)
        f.flush()
        if FSYNC:
            os.fsync(f.fileno())
    os.replace(tmp, path)


def get(path):
    with open(path, "rb") as f:
        return json.loads(f.read())


def safe(gid):
    """A game id as a file name: 'S001/reg:w01:g01' -> 'S001-reg-w01-g01'."""
    return gid.replace("/", "-").replace(":", "-")


# ---------------------------------------------------------------------------------------------------------------------------------- the clock
def clock_limit(state):
    """Last wall instant supported by both the daemon lease and published coverage."""
    limit = state["lease_until"]
    if "ready_until" in state:
        limit = min(limit, state["anchor_wall"] + state["ready_until"] - state["anchor_pos"])
    return limit


def position(state, now):
    """Broadcast seconds at wall time `now` for a clock state (the contents of clock.json)."""
    if state["state"] != "running":
        return float(state["anchor_pos"])
    return float(state["anchor_pos"]) + max(0.0, min(now, clock_limit(state)) - state["anchor_wall"])


class Clock:
    """The one authoritative clock. Its truth is store/clock.json; every change is appended to store/clock_log.jsonl and mirrored to public/broadcast/clock.json."""

    def __init__(self, store, public):
        self.path = os.path.join(store, "clock.json")
        self.log = os.path.join(store, "clock_log.jsonl")
        self.mirror = os.path.join(public, "broadcast", "clock.json")

    def state(self):
        try:
            return get(self.path)
        except OSError:
            return None

    def _write(self, st, why, now, **extra):
        st["format"] = CLOCK_FORMAT
        put(self.path, st)
        put(self.mirror, st)
        with open(self.log, "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(extra, at=now, why=why, state=st["state"], pos=st["anchor_pos"], epoch=st["epoch"]), sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def start(self, now, pos=0.0, lease=LEASE_S, ready_until=None):
        """`ready_until`: the publication horizon from the first instant (a season's clock must not run before its first tick has released anything)."""
        if self.state() is not None:
            raise BroadcastError("the clock already exists: use resume")
        st = {"state": "running", "anchor_wall": now, "anchor_pos": pos, "lease_until": now + lease, "epoch": 1}
        if ready_until is not None:
            st["ready_until"] = ready_until
        self._write(st, "start", now)

    def position(self, now):
        st = self.state()
        return None if st is None else position(st, now)

    def pause(self, now):
        st = self.state()
        if st["state"] == "paused":
            return
        st = dict(st, state="paused", anchor_pos=position(st, now), anchor_wall=now, epoch=st["epoch"] + 1)
        self._write(st, "pause", now)

    def resume(self, now, lease=LEASE_S):
        st = self.state()
        if st["state"] == "running":
            return
        self._write(dict(st, state="running", anchor_wall=now, lease_until=now + lease, epoch=st["epoch"] + 1, **({"ready_until": st["anchor_pos"] + DELAY_S} if "ready_until" in st else {})), "resume", now)

    def heartbeat(self, now, lease=LEASE_S, holding=False, why="downtime"):
        """Called every few seconds by the daemon. If the previous lease lapsed, the gap was downtime: it is recorded as a pause and the position resumes
        from where the viewers' clocks stopped (lease end). Returns the downtime in seconds.
        `holding`: the daemon is alive and standing deliberately at the edge of what has been built (the clock cannot pass `ready_until`): the lease is kept
        but nothing is recorded yet; the stand-still is recorded ONCE, as `why`, when coverage arrives (a second per second of log lines would be noise)."""
        st = self.state()
        if st["state"] != "running":
            return 0.0
        if holding:
            if st["lease_until"] - now < lease - 5:
                put(self.path, dict(st, lease_until=now + lease))
                put(self.mirror, dict(st, lease_until=now + lease))
            return 0.0
        down = max(0.0, now - clock_limit(st))
        if down > 0:
            frozen = position(st, now)
            self._write(dict(st, anchor_pos=frozen, anchor_wall=now, lease_until=now + lease, epoch=st["epoch"] + 1, **({"ready_until": frozen} if "ready_until" in st else {})), why, now, down_s=round(down, 3))
        elif st["lease_until"] - now < lease - 5:       # renew about every 5 seconds, not every tick
            put(self.path, dict(st, lease_until=now + lease))
            put(self.mirror, dict(st, lease_until=now + lease))
        return down


# ---------------------------------------------------------------------------------------------------------------------------------- one game
def timeline(game, slot_s):
    """(plays with t0/dur after kickoff, speed=1, game length). A nominal slot never changes speed."""
    raw, total = [], 0.0
    for p in game["plays"]:
        tr = p.get("trace")
        d = (float(tr["t_end"]) + HOLD_S) if tr else CARD_S
        raw.append((p, total, d))
        total += d
    # Slots are nominal. Never accelerate a long game to recover schedule time.
    speed = 1.0
    out = []
    for p, t0, d in raw:
        q = dict(p)
        q["t0"] = round(t0 / speed, 3)
        q["dur"] = round(d / speed, 3)
        out.append(q)
    return out, round(speed, 4), round(total / speed, 3)


def build_game(game, slot_s, seg_s=SEG_S, delay_s=DELAY_S):
    """What the store keeps for one game: the public-safe header, the segments, and the private ending. `game` is a finished `dfl-public-playback` game."""
    PB.check_public(game)
    plays, speed, length = timeline(game, slot_s)
    head = {k: game[k] for k in LIVE_KEYS if k in game}
    head.update(kind="live-game", broadcast={"seg_s": seg_s, "delay_s": delay_s, "speed": speed, "slot_s": slot_s})
    segs = {}
    for p in plays:
        segs.setdefault(int(p["t0"] // seg_s), []).append(p)
    n = max(segs) + 1 if segs else 0
    segments = [segs.get(i, []) for i in range(n)]
    end = {k: game[k] for k in ("final", "winner", "tie", "periods", "traced_plays") if k in game}
    end["plays"] = len(game["plays"])
    end.update(format=PB.FORMAT, format_version=PB.VERSION, kind="live-final", gid=game["gid"])
    return {"head": head, "segments": segments, "end": end, "length_s": length, "speed": speed}


def store_game(store, game, slot_s, replay_name=None):
    """Write a finished game into the private store; returns its record. `replay_name` is the published file name of the full replay."""
    g = build_game(game, slot_s)
    d = os.path.join(store, "games", safe(game["gid"]))
    put(os.path.join(d, "head.json"), g["head"])
    for i, seg in enumerate(g["segments"]):
        put(os.path.join(d, "s%04d.json" % i), {"gid": game["gid"], "n": i, "plays": seg})
    full = json.dumps(game, sort_keys=True, separators=(",", ":")).encode()
    h = hashlib.sha256(full).hexdigest()[:10]
    name = replay_name or "%s.%s.json" % (safe(game["gid"]), h)
    with open(os.path.join(d, "replay.json"), "wb") as f:
        f.write(full)
    end = dict(g["end"], replay=name)
    put(os.path.join(d, "end.json"), end)
    return {"gid": game["gid"], "segments": len(g["segments"]), "length_s": g["length_s"], "speed": g["speed"], "replay": name}


def check_live_file(obj, kind):
    """Refuse any key outside the live schema: a field added to the source files cannot reach the public tree by accident."""
    if kind == "head":
        extra = set(obj) - LIVE_KEYS
    elif kind == "seg":
        extra = set(obj) - {"gid", "n", "plays"}
        for p in obj["plays"]:
            extra |= set(p) - SEG_PLAY_KEYS
    elif kind == "end":
        extra = set(obj) - FINAL_KEYS
        if not isinstance(obj.get("plays"), int):
            raise BroadcastError("end.json holds the number of plays, not the plays")
    else:
        raise BroadcastError("unknown kind " + kind)
    def nested_keys(value, place):
        return set(value) - PB.ALLOWED[place]
    if kind == "head":
        for value in obj["teams"].values():
            extra |= nested_keys(value, "team")
        for value in obj["roster"].values():
            extra |= nested_keys(value, "player")
        extra |= nested_keys(obj["sampling"], "sampling")
        extra |= nested_keys(obj["field"], "field")
        extra |= set(obj["broadcast"]) - {"seg_s", "delay_s", "speed", "slot_s"}
    elif kind == "seg":
        for play in obj["plays"]:
            extra |= nested_keys(play["score"], "score")
            if play.get("trace"):
                extra |= nested_keys(play["trace"], "trace")
    elif kind == "end":
        extra |= nested_keys(obj["final"], "final")
    if extra:
        raise BroadcastError("%s carries keys outside the live schema: %s" % (kind, sorted(extra)))


# ---------------------------------------------------------------------------------------------------------------------------------- the timetable
def _gated(w):
    """Postseason, Bowl and Coronation participants are not public until announced: they depend on results the viewers have not seen."""
    return any("/po:" in g["gid"] or g["gid"].endswith(("/coronation", "/bowl")) for g in w["games"])


def make_schedule(windows, pos=0, lookahead_s=None):
    """windows: [{"label", "start_s", "slot_s", "games": [{"gid", "away", "home"}]}] -> (public schedule, private schedule). They differ only in that
    the private one retains computed participant identities and lengths; the public one gates announcements.

    A private window may carry `nominal_s` (its place in the timetable before any overrun) and `overrun_s` (how long its games run past the nominal slot).
    The PUBLIC start of a window is its nominal start plus the overruns that have already become visible, that is, those whose slot has expired with a
    game still live. A future overrun is therefore never in the public schedule before the viewers can see it, and a window that has begun never moves.
    `lookahead_s` lists only windows that begin within that many seconds (and every window that has begun)."""
    shift, shown = 0, []
    for w in windows:
        announced = w["nominal_s"] + shift if "nominal_s" in w else w["start_s"]
        if lookahead_s is None or announced <= pos + lookahead_s or w["start_s"] <= pos:
            gate = w.get("announce_s", w["start_s"] if _gated(w) else 0)
            shown.append({"label": w["label"], "start_s": announced, "slot_s": w["slot_s"],
                          "games": [{k: g[k] for k in ("gid", "away", "home") if k in g} for g in w["games"]] if pos >= gate else []})
        if w.get("overrun_s", 0) > 0 and pos >= w["start_s"] + w["slot_s"]:
            shift += w["overrun_s"]
    pub = {"format": "dfl-broadcast-schedule", "format_version": 1, "windows": shown}
    return pub, {"windows": windows}


_CACHE = {}


def _cached_get(path):
    """Immutable private files (head, end) are read once: a daemon ticking every second must not reread hundreds of games. Keyed by size and mtime."""
    st = os.stat(path)
    key = (st.st_mtime_ns, st.st_size)
    hit = _CACHE.get(path)
    if hit and hit[0] == key:
        return hit[1]
    obj = get(path)
    _CACHE[path] = (key, obj)
    return obj


def _live_dir(public, gid):
    return os.path.join(public, "broadcast", "live", safe(gid))


def _desired(game_rec, win_start, pos, n_segs, length_s, delay_s):
    """What of one game the public tree may hold at broadcast position `pos`."""
    t = pos - win_start
    want = {"head": t >= 0, "segs": 0, "end": False}
    if want["head"]:
        want["segs"] = min(n_segs, int(t // SEG_S) + 1)
        want["end"] = t >= length_s + delay_s
    return want


FULL_PASS_S = 120       # every window is re-examined at least this often; in between, finished and far-future windows are skipped


def reconcile(store, public, sched, pos, reveals=(), memo=None):
    """Make the public tree exactly what the broadcast position allows: place what is due, remove what is not (a restart or a rewound clock is repaired the same
    way). Idempotent. `sched` is the private schedule. Returns the number of files placed and removed.
    `memo`: a dict the caller keeps between calls (a daemon ticking once a second). With it, windows whose games are all final and in place are not re-examined,
    nor windows more than two minutes from their kickoff, except in a FULL pass (the first call, a position that went backwards, and every FULL_PASS_S seconds):
    a new process, or a clock set back, repairs everything at once. Without it every call is a full pass."""
    placed = removed = 0
    memo = {} if memo is None else memo
    full = memo.get("settled") is None or pos < memo.get("pos", -1) or pos - memo.get("full_pos", -1e18) >= FULL_PASS_S
    if full:
        memo["settled"], memo["full_pos"] = {}, pos
    if not memo.get("swept"):                 # debris of a process that died mid-write: a new process is the only writer, so every dot-temporary is its predecessor's
        memo["swept"] = True
        for d, dirs, names in os.walk(public):
            for n in list(dirs) + names:
                if n.startswith(".tmp-"):
                    p = os.path.join(d, n)
                    shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.unlink(p)
                    removed += 1
            dirs[:] = [x for x in dirs if not x.startswith(".tmp-")]
    memo["pos"] = pos
    public_schedule, _ = make_schedule(sched["windows"], pos, sched.get("lookahead_s"))
    schedule_path = os.path.join(public, "broadcast", "schedule.json")
    if not os.path.exists(schedule_path) or get(schedule_path) != public_schedule:
        put(schedule_path, public_schedule)
    replays = os.path.join(public, "broadcast", "replays")
    done = []
    for wi, w in enumerate(sched["windows"]):
        if not full:
            if wi in memo["settled"]:
                done += memo["settled"][wi]
                continue
            if pos < w["start_s"] - 120:
                continue
        p0, r0, wdone = placed, removed, []
        for g in w["games"]:
            gid = g["gid"]
            src = os.path.join(store, "games", safe(gid))
            end = _cached_get(os.path.join(src, "end.json"))
            head = _cached_get(os.path.join(src, "head.json"))
            n_segs = g["segments"]
            want = _desired(g, w["start_s"], pos, n_segs, g["length_s"], head["broadcast"]["delay_s"])
            dst = _live_dir(public, gid)
            have = set(os.listdir(dst)) if os.path.isdir(dst) else set()
            for n in [n for n in have if n.startswith(".")]:       # a temporary file left by a crash: this process is the only writer
                os.unlink(os.path.join(dst, n))
                removed += 1
            have = {n for n in have if not n.startswith(".")}
            due = set()
            if want["head"]:
                due.add("head.json")
            due |= {"s%04d.json" % i for i in range(want["segs"])}
            if want["end"]:
                due.add("end.json")
            for name in sorted(due - have):
                obj = (_cached_get if name in ("head.json", "end.json") else get)(os.path.join(src, name))
                check_live_file(obj, "head" if name == "head.json" else "end" if name == "end.json" else "seg")
                put(os.path.join(dst, name), obj)
                placed += 1
            for name in sorted(have - due - {"manifest.json"}):
                os.unlink(os.path.join(dst, name))
                removed += 1
            if want["head"]:
                man = {"gid": gid, "state": "final" if want["end"] else "live", "segments": want["segs"], "seg_s": SEG_S, "delay_s": head["broadcast"]["delay_s"], "speed": head["broadcast"]["speed"],
                       "start_s": w["start_s"]}
                cur = None
                try:
                    cur = get(os.path.join(dst, "manifest.json"))
                except OSError:
                    pass
                if cur != man:
                    put(os.path.join(dst, "manifest.json"), man)
            elif os.path.isdir(dst):
                shutil.rmtree(dst)
                removed += 1
            rp = os.path.join(replays, end["replay"])
            if want["end"]:
                if not os.path.exists(rp):
                    os.makedirs(replays, exist_ok=True)
                    tmp = os.path.join(replays, ".tmp-" + end["replay"])
                    shutil.copyfile(os.path.join(src, "replay.json"), tmp)
                    os.replace(tmp, rp)
                    placed += 1
                wdone.append({"gid": gid, "replay": end["replay"], "final": end["final"], "season": head.get("season"), "game_kind": head.get("game_kind")})
            elif os.path.exists(rp):
                os.unlink(rp)
                removed += 1
        done += wdone
        if w["games"] and len(wdone) == len(w["games"]) and (placed, removed) == (p0, r0):
            memo["settled"][wi] = wdone
    # the replay index lists only broadcasts that have ended: it is the on-demand library
    idx = {"format": "dfl-broadcast-replays", "format_version": 1, "games": sorted(done, key=lambda d: d["gid"])}
    ip = os.path.join(replays, "index.json")
    if not os.path.exists(ip) or get(ip) != idx:
        put(ip, idx)
    r_placed, r_removed = _reveals(store, public, reveals, pos)
    return placed + r_placed, removed + r_removed


def _raw(path):
    with open(path, "rb") as f:
        return f.read()


def _reveals(store, public, reveals, pos):
    """reveals: [{"at_s", "src" | "data", "dst", "last", "replace", "group"}]: a private file or directory (or, with `data`, bytes made by the planner) that becomes
    public at `at_s` (the week's feed release, then its pointer). A reveal is copied whole and renamed into place, never visible half-made; one that is not
    due is removed (a restart or a rewound clock is repaired the same way).

    `group`: reveals that share a group and a `dst` are successive versions of ONE public file (the latest-release pointer, a cumulative index): only the
    newest due version is placed, never each in turn (a restart that finds several due must not flip the file through every old version)."""
    placed = removed = 0
    plain, groups = [], {}
    for r in reveals:
        if r.get("group"):
            groups.setdefault((r["group"], r["dst"]), []).append(r)
        else:
            plain.append(r)
    for rs in groups.values():
        due = [r for r in rs if pos >= r["at_s"]]
        if due:
            plain.append(dict(max(due, key=lambda r: (r["at_s"], r.get("order", 0))), replace=True))
        else:
            plain.append(dict(rs[0], at_s=float("inf"), replace=False))
    for r in sorted(plain, key=lambda r: (r["at_s"], r.get("last", False))):
        dst = os.path.join(public, r["dst"])
        due = pos >= r["at_s"]
        if "data" in r:
            raw = r["data"] if isinstance(r["data"], bytes) else r["data"].encode()
            same = lambda: os.path.exists(dst) and _raw(dst) == raw
        else:
            src = os.path.join(store, r["src"])
            same = lambda: os.path.exists(dst) and _raw(src) == _raw(dst)
        if due:
            if not os.path.exists(dst) or (r.get("replace") and not same()):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                tmp = os.path.join(os.path.dirname(dst), ".tmp-" + os.path.basename(dst))
                if "data" in r:
                    with open(tmp, "wb") as f:
                        f.write(raw)
                        f.flush()
                        os.fsync(f.fileno())
                elif os.path.isdir(src):
                    shutil.rmtree(tmp, ignore_errors=True)
                    shutil.copytree(src, tmp)
                else:
                    shutil.copyfile(src, tmp)
                os.replace(tmp, dst)
                placed += 1
        elif os.path.exists(dst) and not r.get("replace"):
            shutil.rmtree(dst) if os.path.isdir(dst) else os.unlink(dst)
            removed += 1
    return placed, removed


# ---------------------------------------------------------------------------------------------------------------------------------- the daemon
class Broadcaster:
    """Everything the broadcast process does: keep the clock's lease, and reconcile the public tree with the clock. One instance, one process (the CLI
    takes a lock); `tick(now)` is called about once a second."""

    def __init__(self, store, public, lease=LEASE_S, guard_publication=True):
        self.store, self.public, self.lease = store, public, lease
        self.guard_publication = guard_publication
        self.clock = Clock(store, public)

    def schedule(self):
        return get(os.path.join(self.store, "schedule.json"))

    def reveals(self):
        try:
            return get(os.path.join(self.store, "reveals.json"))["reveals"]
        except OSError:
            return []

    def tick(self, now=None):
        now = time.time() if now is None else now
        down = self.clock.heartbeat(now, self.lease)
        pos = self.clock.position(now)
        placed, removed = reconcile(self.store, self.public, self.schedule(), pos, self.reveals())
        if self.guard_publication:
            # Released segments cover at least this much of the delayed viewer's timeline.
            # A daemon death must not let the clock outrun the material actually served.
            st = self.clock.state()
            st["ready_until"] = pos + DELAY_S
            put(self.clock.path, st)
            put(self.clock.mirror, st)
        return {"pos": pos, "down_s": down, "placed": placed, "removed": removed}


# ---------------------------------------------------------------------------------------------------------------------------------- the reference viewer
def viewer_state(public, gid, pos):
    """What any viewer must show for game `gid` at broadcast position `pos`, computed from the PUBLIC files alone (the site's viewer and the demo page do the
    same arithmetic). The state is a function of the files and the position, never of when or how often the viewer loaded: a late joiner and a refresh get the
    same answer as a viewer who has watched from the start."""
    d = _live_dir(public, gid)
    try:
        man = get(os.path.join(d, "manifest.json"))
    except OSError:
        return {"state": "scheduled"}
    plays = []
    for i in range(man["segments"]):
        plays += get(os.path.join(d, "s%04d.json" % i))["plays"]
    tau = pos - man["start_s"] - man["delay_s"]
    idx = -1
    for i, p in enumerate(plays):
        if p["t0"] <= tau:
            idx = i
    shown = plays[idx] if idx >= 0 else None
    state = "live"
    if man["state"] == "final":
        end = get(os.path.join(d, "end.json"))
        if idx + 1 == end["plays"] and tau >= shown["t0"] + shown["dur"]:
            state = "final"
    return {"state": state, "idx": idx, "score": (shown or {"score": {"away": 0, "home": 0}})["score"], "text": shown["text"] if shown else None}
