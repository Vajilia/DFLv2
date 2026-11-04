"""The public playback format (`dfl-public-playback`, version 1): what a browser needs to replay one finished game, converted from the archive.

The engine produces results and as-played spatial traces (docs/ARCHIVE.md). This module converts one archived game into a public file; the browser
draws that file and never runs or imports the simulation. It uses the Python standard library alone and imports nothing from the engine, so it can
be deployed, changed and tested separately from it (docs/PUBLIC_PLAYBACK_FORMAT.md).

What is copied, and what is not.
  copied   the game's participants, final score, and each play's period, clock, possession, down, distance, field position, kind, a one-line
           description built from the recorded play, the running score, and for a play the engine simulated snap by snap: every player's
           position and the ball's, sampled every `every` steps (0.05 s each), with the recorded throw / catch / handoff / tackle / fumble events.
  never    ratings, attributes, play calls, formations, coverages, player intents, the box-score statistics, injury details (the play-by-play only says that an unnamed player left the game hurt), anything not in the list
           above. `check_public` walks a finished file and refuses any key outside the schema, so a field added to the archive cannot leak by accident.
  honesty  a play with no stored trace (kickoff, punt, kick, penalty, timeout, kneel ...) carries `trace: null` and is shown as an event, not as
           movement. Nothing is interpolated between plays and no movement is ever invented: between two samples of a trace the viewer draws a
           straight line at constant speed, and says so.

Coordinates. A trace is in the offence's frame: x is yards from its own goal line (0-100, end zones beyond), y yards across the 53.33-yard field,
increasing to the offence's left. The file keeps that frame; `dir` on each play says which way the offence attacks on the display (+1: toward the
right-hand end zone), which is a display convention (the engine records no absolute direction): the home team attacks right in periods 1 and 3 and
left in periods 2 and 4, and the ends are swapped by the viewer, not stored.
"""
import base64
import hashlib
import json
import os
import re
import struct
import zlib

FORMAT = "dfl-public-playback"
VERSION = 1
MAGIC = b"DFLT"
STEP = 0.05                       # seconds per recorded step
SNAP_KINDS = ("run", "complete", "incomplete", "sack", "scramble", "interception", "fumble lost")
EVENT_KINDS = ("handoff", "throw", "catch", "tackle", "fumble", "intercept", "batted", "drop", "breakup", "scramble", "miss")

# Every key a public file may contain, by place. check_public refuses anything else.
ALLOWED = {
    "root": {"format", "format_version", "kind", "gid", "id", "season", "game_kind", "date", "kickoff", "neutral", "teams", "final", "periods", "tie",
             "winner", "roster", "identity_note", "sampling", "plays", "traced_plays", "field"},
    "team": {"abbr", "name", "colors"},
    "final": {"away", "home"},
    "player": {"name", "number", "pos", "side"},
    "play": {"i", "period", "clock", "off", "dir", "down", "dist", "yl", "kind", "text", "first", "pts", "score", "trace"},
    "score": {"away", "home"},
    "trace": {"dt", "n", "t_end", "los", "roles", "ids", "events", "pos", "ball", "ball_from", "encoding"},
    "field": {"length", "width", "end_zone"},
    "sampling": {"every", "step_seconds", "note"},
}


class PlaybackError(Exception):
    pass


# ---------------------------------------------------------------------------------------------------------------- reading a trace container
def decode_b64(s):
    """The bytes of a base64 field of a game file."""
    return base64.b64decode(s)


def parse_trace(c):
    """-> (header dict, positions int16 list [steps][44] flat row-major, ball int16 list [steps][3] flat). Reads docs/ARCHIVE.md's container."""
    if c[:4] != MAGIC:
        raise PlaybackError("not a trace container")
    version, hl = struct.unpack("<BI", c[4:9])
    if version != 1:
        raise PlaybackError(f"trace container version {version} not understood")
    head = json.loads(c[9:9 + hl])
    n = head["steps"]
    p0 = 9 + hl
    pe = p0 + n * 44 * 2
    be = pe + n * 3 * 2
    if len(c) != be:
        raise PlaybackError("trace container is the wrong length")
    pos = list(struct.unpack(f"<{n * 44}h", c[p0:pe]))
    ball = list(struct.unpack(f"<{n * 3}h", c[pe:be]))
    return head, _undelta(pos, 44, n), _undelta(ball, 3, n)


def _undelta(d, ncols, steps):
    out = [0] * (ncols * steps)
    for c in range(ncols):
        v = 0
        for s in range(steps):
            v += d[c * steps + s]
            out[s * ncols + c] = v
    return out


def _delta_b64(rows, ncols, keep):
    """Row-major ints -> column-delta int16 little-endian, base64, over the step indices `keep` (the archive's layout, so a reader that knows the
    archive container reads this)."""
    n = len(keep)
    cols = []
    for c in range(ncols):
        prev = 0
        for k, s in enumerate(keep):
            v = rows[s * ncols + c]
            cols.append(v - prev if k else v)
            prev = v
    for v in cols:
        if not -32768 <= v <= 32767:
            raise PlaybackError("a position step does not fit in 16 bits")
    return base64.b64encode(struct.pack(f"<{len(cols)}h", *cols)).decode("ascii"), n


def encode_trace(container, every=2):
    head, pos, ball = parse_trace(container)
    n = head["steps"]
    keep = list(range(0, n, every))
    if keep[-1] != n - 1:
        keep.append(n - 1)
    p, _ = _delta_b64(pos, 44, keep)
    b, _ = _delta_b64(ball, 3, keep)
    # The archive's first ball sample is (0, 0, 0): the ball is not placed yet. It is exported as stored, and `ball_from` says from which sample
    # on the ball was recorded, so a viewer does not draw it in the corner of the field.
    ball_from = 0
    while ball_from < len(keep) - 1 and not any(ball[keep[ball_from] * 3 + c] for c in range(3)):
        ball_from += 1
    events = [[round(s * STEP, 2), kind, a, b_] for s, kind, a, b_ in head["events"] if kind.split(":")[0] in EVENT_KINDS or kind.startswith("end:")]
    return {"dt": round(every * STEP, 3), "n": len(keep), "t_end": round((n - 1) * STEP, 3), "los": head["los"], "roles": head["roles"], "ids": head["ids"],
            "events": events, "pos": p, "ball": b, "ball_from": ball_from, "encoding": "int16le column-delta base64; positions in hundredths of a yard, 44 columns (x,y per slot), 3 ball columns (x,y,height)"}


# ---------------------------------------------------------------------------------------------------------------- one game
def _gain(d):
    return d[0] if d and isinstance(d[0], int) else None


def _yards(n):
    return f"{abs(n)} yard" + ("" if abs(n) == 1 else "s")


def describe(e, nm, who):
    """One line for a play table entry, from recorded fields only. `nm(id)` -> name or a plain fallback; `who(index)` -> team abbreviation."""
    k, d = e["kind"], e.get("detail") or []
    g = _gain(d)
    first = " First down." if e.get("first") else ""
    if k == "kickoff":
        return f"{nm(e.get('kicker'))} kicks off."
    if k == "run":
        s = f"{nm(e.get('car'))} runs for {'no gain' if g == 0 else (_yards(g) if g and g > 0 else _yards(g) + ' loss')}"
        return s + (f" (tackle: {nm(e['tkl'])})" if e.get("tkl") else "") + "." + first
    if k == "scramble":
        return f"{nm(e.get('qb'))} scrambles for {'no gain' if g == 0 else (_yards(g) if g and g > 0 else _yards(g) + ' loss')}." + first
    if k == "complete":
        return f"{nm(e.get('qb'))} completes a pass to {nm(e.get('tgt'))} for {_yards(g) if g is not None else 'a gain'}." + first
    if k == "incomplete":
        return f"{nm(e.get('qb'))}'s pass for {nm(e.get('tgt'))} is incomplete."
    if k == "sack":
        return f"{nm(e.get('qb'))} is sacked for a loss of {_yards(g) if g is not None else 'yards'}."
    if k == "interception":
        return f"{nm(e.get('qb'))}'s pass is intercepted by {nm(e.get('int_by'))}."
    if k == "fumble lost":
        return "Fumble lost to the defense."
    if k == "touchdown":
        return f"Touchdown, {nm(e.get('scorer'))}."
    if k == "extra point":
        return "Extra point is good." if e.get("good") else "Extra point is no good."
    if k == "two-point try":
        return "Two-point try is good." if e.get("good") else "Two-point try fails."
    if k == "field goal":
        return f"{nm(e.get('kicker'))} kicks a field goal." if (d and d[0] == "good") else f"{nm(e.get('kicker'))}'s field goal attempt is no good."
    if k == "punt":
        return f"{nm(e.get('punter'))} punts."
    if k == "penalty":
        return f"Penalty on {who(e.get('team'))}: {d[0]}{', ' + _yards(d[1]) if len(d) > 1 and isinstance(d[1], int) else ''}." if d else "Penalty."
    if k == "kneel":
        return f"{nm(e.get('car'))} kneels."
    if k == "timeout":
        return f"Timeout, {who(e.get('team'))}."
    if k == "safety":
        return "Safety."
    if k == "onside":
        return "Onside kick attempt."
    if k == "two-minute warning":
        return "Two-minute warning."
    if k == "injury":
        return "A player is injured and leaves the game."
    return k.capitalize() + "."


def build_game(rec, traces, teams, roster, feed_id, every=2):
    """rec: the archive's game record (dict, as stored); traces: {play_i: container bytes}; teams: {club name: {"abbr", "name", "colors": [hex...]}} for
    the two clubs (or All-Star sides); roster: {player id (int): {"name", "number", "pos"}} (public rosters, may be partial); feed_id: the id the
    public schedule uses for this game. -> the public game dict (checked by check_public)."""
    home, away = rec["home"], rec["away"]
    if home not in teams or away not in teams:
        raise PlaybackError(f"{rec['gid']}: no public team record for {home!r} or {away!r}")
    # team index in the play table: 0 / 1. Fixed by the scoring: the index whose recorded points add up to the home score is the home team.
    pts = [0, 0]
    for e in rec["table"]:
        if e.get("pts") and e.get("team") in (0, 1):
            pts[e["team"]] += e["pts"]
    hs, as_ = rec["score"][0], rec["score"][1]
    if pts == [hs, as_] and pts != [as_, hs]:
        idx_home = 0
    elif pts == [as_, hs] and pts != [hs, as_]:
        idx_home = 1
    elif hs == as_ and pts[0] == pts[1] == hs:
        idx_home = 0
    else:
        raise PlaybackError(f"{rec['gid']}: scoring plays add up to {pts}, the final score is home {hs} away {as_}; the sides cannot be matched")
    side = {idx_home: "home", 1 - idx_home: "away"}
    abbr = {"home": teams[home]["abbr"], "away": teams[away]["abbr"]}

    def who(i):
        return abbr.get(side.get(i), "?")

    used = {}

    def nm(pid):
        if pid is None:
            return "a player"
        r = roster.get(int(pid))
        return r["name"] if r else f"player {int(pid)}"
    plays, score = [], {"away": 0, "home": 0}
    for e in rec["table"]:
        off = side.get(e.get("off"))
        period = e["period"]
        d = 1 if ((period % 2 == 1) == (off == "home")) else -1        # home attacks right in periods 1 and 3, left in 2 and 4; the other team the opposite
        if e.get("pts") and e.get("team") in (0, 1):
            score[side[e["team"]]] += e["pts"]
        tr = None
        if e.get("has_trace"):
            c = traces.get(e["i"])
            if c is None:
                raise PlaybackError(f"{rec['gid']}: play {e['i']} is flagged as traced and has no stored trace")
            tr = encode_trace(c, every)
            other = "away" if off == "home" else "home"
            for k, pid in enumerate(tr["ids"]):                       # slots 0-10 are the offence, 11-21 the defence
                used[int(pid)] = off if k < 11 else other
        play = {"i": e["i"], "period": period, "clock": e["clock"], "off": off, "dir": d, "kind": e["kind"], "text": describe(e, nm, who), "score": dict(score), "trace": tr}
        if e["kind"] in SNAP_KINDS or e["kind"] == "kneel":
            play.update(down=e["down"], dist=e["dist"], yl=e["yl"])
        if e.get("first"):
            play["first"] = True
        if e.get("pts"):
            play["pts"] = e["pts"]
        plays.append(play)
    if score != {"away": as_, "home": hs}:
        raise PlaybackError(f"{rec['gid']}: the plays add up to {score}, the final score is home {hs} away {as_}")
    # who played for whom: from the traces (the first eleven slots are the offence, the last eleven the defence)
    ros = {}
    for pid, s in used.items():
        r = roster.get(pid)
        ros[str(pid)] = dict({"side": s}, **({"name": r["name"], "number": r["number"], "pos": r["pos"]} if r else {}))
    out = {"format": FORMAT, "format_version": VERSION, "kind": "game", "gid": rec["gid"], "id": feed_id, "season": rec["season"], "game_kind": rec["kind"],
           "date": rec.get("date"), "kickoff": rec.get("kickoff"), "neutral": bool(rec.get("neutral")),
           "teams": {"away": {k: teams[away][k] for k in ("abbr", "name", "colors")}, "home": {k: teams[home][k] for k in ("abbr", "name", "colors")}},
           "final": {"away": as_, "home": hs}, "periods": rec["periods"], "tie": bool(rec["tie"]), "winner": ("home" if rec["winner"] == home else "away") if rec.get("winner") else None,
           "roster": ros,
           "identity_note": "Names, numbers and positions come from the public rosters when this file was made; a player who has since left a roster has no name here.",
           "sampling": {"every": every, "step_seconds": STEP, "note": "Positions are recorded every 0.05 s; this file keeps one sample every %d steps (%.2f s). The viewer draws a straight line at constant speed between kept samples." % (every, every * STEP)},
           "plays": plays, "traced_plays": sum(1 for p in plays if p["trace"]), "field": {"length": 100, "width": 53.33, "end_zone": 10}}
    check_public(out)
    return out


# ---------------------------------------------------------------------------------------------------------------- the privacy gate
def check_public(o):
    """Refuse any key outside ALLOWED, at any depth of the places the schema names. Raises PlaybackError."""
    def keys(obj, place):
        bad = set(obj) - ALLOWED[place]
        if bad:
            raise PlaybackError(f"{place} carries keys outside the public schema: {sorted(bad)}")
    keys(o, "root")
    keys(o["final"], "final")
    keys(o["sampling"], "sampling")
    keys(o["field"], "field")
    for t in o["teams"].values():
        keys(t, "team")
    for p in o["roster"].values():
        keys(p, "player")
    for pl in o["plays"]:
        keys(pl, "play")
        keys(pl["score"], "score")
        if pl["trace"] is not None:
            keys(pl["trace"], "trace")


# ---------------------------------------------------------------------------------------------------------------- files
def canonical(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def slug(gid):
    return re.sub(r"[^A-Za-z0-9]+", "-", gid).strip("-")


def feed_id_for(gid, siblings):
    """The id the public schedule gives an archived game. Regular, Tour, Bowl and Coronation ids are the archive id without the season; a playoff game
    is `po:<conference>:<round>:<k>` where k counts that conference's games in the round by the host's seed (3 before 4; 1 before 2)."""
    rest = gid.split("/", 1)[1]
    m = re.match(r"po:([^:]+):([^:]+):s(\d+)v(\d+)$", rest)
    if not m:
        return rest
    conf, rd = m.group(1), m.group(2)
    mates = sorted(int(re.match(r".*:s\d+v(\d+)$", g).group(1)) for g in siblings if g.startswith(f"{gid.split('/')[0]}/po:{conf}:{rd}:"))
    return f"po:{conf}:{rd}:{mates.index(int(m.group(4))) + 1}"


def write_game(out_dir, game):
    """Write `games/<slug>.<sha10>.json` (immutable: the hash is in the name). An existing file of that name must be byte-identical. -> (relative path, bytes, sha256)."""
    body = canonical(game)
    h = hashlib.sha256(body).hexdigest()
    rel = f"games/{slug(game['gid'])}.{h[:10]}.json"
    path = os.path.join(out_dir, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        with open(path, "rb") as f:
            if f.read() != body:
                raise PlaybackError(f"{rel} exists with different content; refusing to overwrite a published game")
    else:
        tmp = path + f".tmp-{os.getpid()}"
        with open(tmp, "wb") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    return rel, len(body), h


def write_index(out_dir, entries, league_id=None):
    """Atomically replace `index.json`: the list of games that can be replayed. Entries are sorted so equal input gives equal bytes."""
    idx = {"format": FORMAT, "format_version": VERSION, "kind": "index", "league_id": league_id, "games": sorted(entries, key=lambda e: e["gid"])}
    path = os.path.join(out_dir, "index.json")
    tmp = path + f".tmp-{os.getpid()}"
    with open(tmp, "wb") as f:
        f.write(canonical(idx))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return idx


def index_entry(game, rel, size, sha):
    return {"id": game["id"], "gid": game["gid"], "season": game["season"], "game_kind": game["game_kind"], "file": rel, "bytes": size, "sha256": sha,
            "away": game["teams"]["away"]["abbr"], "home": game["teams"]["home"]["abbr"], "away_score": game["final"]["away"], "home_score": game["final"]["home"],
            "plays": len(game["plays"]), "traced_plays": game["traced_plays"]}
