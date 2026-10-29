#!/usr/bin/env python3
"""Read a DFL archive with the Python standard library alone (no DFL code, no numpy). Run it as  python3 -I -S tools/read_archive.py ...

  list    ARCHIVE [--season N]            games: id, kind, teams, engine, traced snaps
  game    ARCHIVE GAME_ID                 the stored record as JSON (score, statistics, play table)
  trace   ARCHIVE GAME_ID PLAY_I [--every K]   one snap's trace as JSON: roles, ids, events, and a frame every K steps (default 2 = 0.10 s)
  season  ARCHIVE N                       the season summary record
  verify  ARCHIVE                         re-hash every row, parse every trace; exit 1 on any problem
  stats   ARCHIVE                         storage used

The format is documented in docs/ARCHIVE.md; this file is the proof that it can be read without the program that wrote it.
"""
import array
import hashlib
import json
import sqlite3
import struct
import sys
import zlib

MAGIC = b"DFLT"


def connect(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def sha(b):
    return hashlib.sha256(b).hexdigest()


def parse_trace(c):
    if c[:4] != MAGIC:
        raise ValueError("not a trace container")
    version, hl = struct.unpack("<BI", c[4:9])
    if version != 1:
        raise ValueError(f"trace container version {version} not understood")
    head = json.loads(c[9:9 + hl])
    n = head["steps"]
    p0 = 9 + hl
    pe = p0 + n * 22 * 2 * 2
    be = pe + n * 3 * 2
    if len(c) != be:
        raise ValueError("trace container is the wrong length")
    pos, ball = array.array("h"), array.array("h")
    pos.frombytes(c[p0:pe])
    ball.frombytes(c[pe:be])
    if sys.byteorder == "big":
        pos.byteswap()
        ball.byteswap()
    return head, undelta(pos, 44, n), undelta(ball, 3, n)


def undelta(d, ncols, steps):
    """The container stores each column as its first value then successive differences; add them back (row-major result)."""
    out = array.array("h", bytes(2 * ncols * steps))
    for c in range(ncols):
        v = 0
        for s in range(steps):
            v += d[c * steps + s]
            out[s * ncols + c] = v
    return out


def get_trace(db, gid, i):
    row = db.execute("SELECT sha256, payload FROM traces WHERE game_id = ? AND play_i = ?", (gid, i)).fetchone()
    if row is None:
        raise KeyError(f"no trace {gid}#{i}")
    c = zlib.decompress(row[1])
    if sha(c) != row[0]:
        raise ValueError(f"trace {gid}#{i} fails its checksum")
    return c


def trace_json(c, every=2):
    head, pos, ball = parse_trace(c)
    n = head["steps"]
    frames = []
    for k in range(0, n, every):
        frames.append({"t": round(k * head["dt"], 2), "p": [[pos[(k * 22 + j) * 2] / 100.0, pos[(k * 22 + j) * 2 + 1] / 100.0] for j in range(22)],
                       "b": [ball[k * 3] / 100.0, ball[k * 3 + 1] / 100.0, ball[k * 3 + 2] / 100.0]})
    out = {key: head[key] for key in ("game_id", "play_i", "rule_version", "engine", "roles", "ids", "los", "formation", "play", "coverage", "dt", "events", "intents")}
    out["steps"], out["frame_every"], out["frames"] = n, every, frames
    return out


def get_game(db, gid):
    row = db.execute("SELECT sha256, payload FROM games WHERE game_id = ?", (gid,)).fetchone()
    if row is None:
        raise KeyError(f"no game {gid}")
    body = zlib.decompress(row[1])
    if sha(body) != row[0]:
        raise ValueError(f"game {gid} fails its checksum")
    return json.loads(body)


def verify(db):
    bad = []
    for gid, i, h, z in db.execute("SELECT game_id, play_i, sha256, payload FROM traces"):
        try:
            c = zlib.decompress(z)
            if sha(c) != h:
                bad.append(f"trace {gid}#{i}: checksum")
            else:
                parse_trace(c)
        except Exception as e:
            bad.append(f"trace {gid}#{i}: {e}")
    n_tr = dict(db.execute("SELECT game_id, COUNT(*) FROM traces GROUP BY game_id").fetchall())
    for gid, h, n, z in db.execute("SELECT game_id, sha256, n_traces, payload FROM games"):
        try:
            body = zlib.decompress(z)
            if sha(body) != h:
                bad.append(f"game {gid}: checksum")
        except Exception as e:
            bad.append(f"game {gid}: {e}")
        if n_tr.get(gid, 0) != n:
            bad.append(f"game {gid}: declares {n} traces, has {n_tr.get(gid, 0)}")
    for rid, h, z in db.execute("SELECT rid, sha256, payload FROM records"):
        if sha(zlib.decompress(z)) != h:
            bad.append(f"record {rid}: checksum")
    return bad


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    cmd, path, rest = argv[1], argv[2], argv[3:]
    db = connect(path)
    if cmd == "list":
        q, a = "SELECT game_id, kind, home, away, engine, n_traces FROM games", ()
        if "--season" in rest:
            q, a = q + " WHERE season = ?", (int(rest[rest.index("--season") + 1]),)
        for row in db.execute(q + " ORDER BY season, seq, game_id", a):
            print(*row, sep="\t")
    elif cmd == "game":
        print(json.dumps(get_game(db, rest[0]), indent=1, sort_keys=True))
    elif cmd == "trace":
        every = int(rest[rest.index("--every") + 1]) if "--every" in rest else 2
        print(json.dumps(trace_json(get_trace(db, rest[0], int(rest[1])), every)))
    elif cmd == "season":
        row = db.execute("SELECT sha256, payload FROM records WHERE rid = ?", (f"S{int(rest[0]):03d}/summary",)).fetchone()
        if row is None:
            print("no such season")
            return 1
        print(json.dumps(json.loads(zlib.decompress(row[1])), indent=1, sort_keys=True))
    elif cmd == "verify":
        bad = verify(db)
        print("OK" if not bad else "\n".join(bad))
        return 1 if bad else 0
    elif cmd == "stats":
        g, t = db.execute("SELECT COUNT(*), SUM(LENGTH(payload)) FROM games").fetchone(), db.execute("SELECT COUNT(*), SUM(LENGTH(payload)) FROM traces").fetchone()
        print(json.dumps({"games": g[0], "game_bytes": g[1] or 0, "traces": t[0], "trace_bytes": t[1] or 0}))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
