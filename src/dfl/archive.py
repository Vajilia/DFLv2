"""The permanent archive: every game as played, with every spatial snap's trace (Edition 8.52 section 17, section 20 item 49).

What it is. A separate SQLite file beside the league file, append-only, holding what the league has *done*, in a form that needs no DFL
code to read: JSON for the records, a small documented binary container for each snap's trace (docs/ARCHIVE.md). `tools/read_archive.py`
reads it with the Python standard library alone. The league file (persist.py) holds what the league *is*; this holds what happened, once,
for ever, and is never copied into snapshots or hand-offs.

  meta     format name and version, creation facts, one "epoch" per code version that has written to it.
  batches  one row per league event that produced records: the event's sequence number, how many records, their combined digest.
  games    one row per game: id, season, kind, teams, the SHA-256 of the canonical JSON of the whole record, and that JSON (zlib).
           The record holds the score, team and player statistics, injuries and the full play table, with each snap's trace removed.
  traces   one row per spatial snap: (game id, play index) -> container bytes (zlib), with the SHA-256 of the uncompressed container.
  records  other history by id (season summaries).

Integrity. Every row carries the SHA-256 of its *uncompressed* content, so identity does not depend on a zlib version. SQL triggers refuse
UPDATE and DELETE on every history table. `verify` re-reads and re-hashes everything, parses every trace container and checks that every
game's declared number of traces is the number stored.

Retry safety. The league file's commit writes this archive first (`put_batch`), then moves its own journal forward; so a crash can leave
the archive one event ahead of the league, never behind. The repeated event produces the same records (the league is deterministic) and
`put_batch` sees identical rows already there and does nothing. A row that exists with different content is `ArchiveConflict` and nothing
is written: history is never overwritten. `confirm_batch` is the same check used while recovering the league by replay.

Not authentication: checksums detect damage and accidental edits, not a forger who rewrites rows and hashes together.
"""
import array
import dataclasses
import hashlib
import json
import os
import sqlite3
import struct
import sys
import zlib

FORMAT_NAME = "dfl-archive"
FORMAT_VERSION = 1
TRACE_MAGIC = b"DFLT"
TRACE_VERSION = 1
HISTORY_TABLES = ("batches", "games", "traces", "records")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS batches (seq INTEGER PRIMARY KEY, n INTEGER NOT NULL, sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS games (
    game_id TEXT PRIMARY KEY, season INTEGER NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL, home TEXT NOT NULL, away TEXT NOT NULL,
    engine TEXT NOT NULL, rule_version TEXT NOT NULL, n_traces INTEGER NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL,
    payload BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS traces (
    game_id TEXT NOT NULL, play_i INTEGER NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL, payload BLOB NOT NULL,
    PRIMARY KEY (game_id, play_i));
CREATE TABLE IF NOT EXISTS records (
    rid TEXT PRIMARY KEY, kind TEXT NOT NULL, season INTEGER NOT NULL, seq INTEGER NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL,
    payload BLOB NOT NULL);
CREATE INDEX IF NOT EXISTS games_by_season ON games (season, seq);
"""


class ArchiveError(Exception):
    pass


class ArchiveConflict(ArchiveError):
    """A record already stored differs from the one being written. Nothing was written."""


class ArchiveMissing(ArchiveError):
    """The league file says an event happened but the archive does not hold its records."""


# ---------------------------------------------------------------------------------------------------------------- encoding
def canon(o):
    """Plain JSON-able form of a record: tuples and sets become lists, dict keys become strings, numpy scalars become numbers, dataclasses
    become dicts. Anything else is an error, so an unexpected type is noticed the day it appears and never silently dropped."""
    t = type(o)
    if o is None or t is bool or t is str or t is int:
        return o
    if t is float:
        if o != o or o in (float("inf"), float("-inf")):
            raise TypeError("a non-finite number cannot be archived")
        return o
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            ks = k if isinstance(k, str) else str(k) if isinstance(k, (int, bool)) else None
            if ks is None:
                raise TypeError(f"archive dict key of type {type(k).__name__}")
            if ks in out:
                raise TypeError(f"archive dict keys collide on {ks!r}")
            out[ks] = canon(v)
        return out
    if isinstance(o, (list, tuple)):
        return [canon(x) for x in o]
    if isinstance(o, (set, frozenset)):
        return sorted((canon(x) for x in o), key=lambda x: json.dumps(x, sort_keys=True))
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return canon({f.name: getattr(o, f.name) for f in dataclasses.fields(o)})
    if hasattr(o, "_asdict"):
        return canon(o._asdict())
    mod = type(o).__module__
    if mod == "numpy" or mod.startswith("numpy."):
        if hasattr(o, "item"):
            return canon(o.item())
    raise TypeError(f"cannot archive a {t.__module__}.{t.__qualname__}")


def canonical_bytes(o):
    return json.dumps(canon(o), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def sha(b):
    return hashlib.sha256(b).hexdigest()


def trace_container(blob, extra):
    """The documented bytes of one snap's trace (docs/ARCHIVE.md). `blob` is the engine's trace dict. Uncompressed; the caller zlibs it
    for storage and hashes it as it is here."""
    steps = int(blob["steps"])
    pos = array.array("h")
    pos.frombytes(zlib.decompress(blob["pos"]))
    ball = array.array("h")
    ball.frombytes(zlib.decompress(blob["ball"]))
    if len(pos) != steps * 22 * 2 or len(ball) != steps * 3:
        raise ArchiveError("trace arrays do not match their step count")
    if sys.byteorder == "big":
        pos.byteswap()
        ball.byteswap()
    pos_d, ball_d = _delta_columns(pos, 22 * 2, steps), _delta_columns(ball, 3, steps)
    header = dict(extra, steps=steps, dt=blob["dt"], roles=blob["roles"], ids=blob["ids"], events=blob["events"], intents=blob["intents"],
                  los=blob["los"], formation=blob["formation"], play=blob["play"], coverage=blob["coverage"],
                  units="hundredths of a yard, int16 little-endian", pos_shape=[steps, 22, 2], ball_shape=[steps, 3],
                  layout="column-delta: for each column in turn, the first step's value, then each later step minus the one before")
    h = canonical_bytes(header)
    if sys.byteorder == "big":
        pos_d.byteswap()
        ball_d.byteswap()
    return TRACE_MAGIC + struct.pack("<BI", TRACE_VERSION, len(h)) + h + pos_d.tobytes() + ball_d.tobytes()


def _delta_columns(a, ncols, steps):
    """Row-major int16 array (steps x ncols) -> column by column, each column as its first value then successive differences. Smooth
    motion becomes small numbers that compress about twice as well; nothing is lost (parse_trace adds them back)."""
    out = array.array("h")
    for c in range(ncols):
        col = a[c::ncols]
        out.append(col[0])
        out.extend(b - x for x, b in zip(col, col[1:]))
    return out


def _undelta_columns(d, ncols, steps):
    out = array.array("h", bytes(2 * ncols * steps))
    for c in range(ncols):
        v = 0
        for s in range(steps):
            v += d[c * steps + s]
            out[s * ncols + c] = v
    return out


def encode_game(rec):
    """-> (row dict, game json bytes, {play_i: container bytes}). `rec` is the engine's game record; the traces are taken out of its
    play table (each entry that had one is marked `has_trace`)."""
    rec = dict(rec)
    table, traces = [], {}
    for e in rec.pop("table"):
        e = dict(e)
        blob = e.pop("trace", None)
        if blob is not None:
            traces[int(e["i"])] = trace_container(blob, {"game_id": rec["gid"], "play_i": int(e["i"]), "rule_version": rec["rule_version"],
                                                          "engine": rec["engine"]})
            e["has_trace"] = True
        table.append(e)
    rec["table"] = table
    rec["n_traces"] = len(traces)
    body = canonical_bytes(rec)
    row = dict(game_id=rec["gid"], season=int(rec["season"]), kind=rec["kind"], home=rec["home"], away=rec["away"], engine=rec["engine"],
               rule_version=rec["rule_version"], n_traces=len(traces), sha256=sha(body))
    return row, body, traces


def encode_record(rec):
    body = canonical_bytes(rec)
    return dict(rid=rec["rid"], kind=rec["type"], season=int(rec["season"]), sha256=sha(body)), body


def batch_digest(items):
    h = hashlib.sha256()
    for k in sorted(items):
        h.update(k.encode() + b"=" + items[k].encode() + b"\n")
    return h.hexdigest()


# ---------------------------------------------------------------------------------------------------------------- the file
def _connect(path, readonly=False):
    if readonly:
        return sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True, isolation_level=None)
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    return db


def _connect_new(path):
    """A new archive file: 16 KB pages, so a typical trace (about 5 KB) sits inside one page instead of spilling into overflow pages."""
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("PRAGMA page_size=16384")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    return db


def _meta(db):
    return {k: json.loads(v) for k, v in db.execute("SELECT key, value FROM meta")}


class Prepared:
    """One event's records, encoded and hashed (the league journal stores `n` and `digest` for the event)."""
    def __init__(self, games, recs, digest):
        self.games, self.recs, self.digest, self.n = games, recs, digest, len(games) + len(recs)


class Archive:
    def __init__(self, path, db):
        self.path, self.db = path, db

    @classmethod
    def open_or_create(cls, path, info):
        """Open the archive, or create it. `info` ({"fingerprint", "how", "seq", "league_id"}) is added to its epochs when it differs from the
        last. An existing archive that belongs to another league (different `league_id`) is refused."""
        fresh = not (os.path.exists(path) and os.path.getsize(path))
        db = _connect(path) if not fresh else _connect_new(path)
        db.executescript(SCHEMA)
        a = cls(path, db)
        if fresh:
            a._init_meta(info.get("league_id"))
        else:
            a._check_format()
            a.check_league(info.get("league_id"))
        a._epoch(info)
        a._triggers()
        return a

    @classmethod
    def open(cls, path):
        if not os.path.exists(path):
            raise ArchiveError(f"the archive {path} does not exist (it is permanent history and is never recreated empty)")
        db = _connect(path)
        a = cls(path, db)
        a._check_format()
        return a

    def check_league(self, league_id):
        mine = _meta(self.db).get("league_id")
        if league_id is not None and mine != league_id:
            raise ArchiveError(f"{self.path} belongs to another league (archive league id {str(mine)[:12]}, league {str(league_id)[:12]})")

    def _init_meta(self, league_id=None):
        self.db.execute("BEGIN IMMEDIATE")
        for k, v in (("format", FORMAT_NAME), ("version", FORMAT_VERSION), ("trace_version", TRACE_VERSION), ("epochs", []),
                     ("league_id", league_id)):
            self.db.execute("INSERT OR IGNORE INTO meta VALUES (?,?)", (k, json.dumps(v)))
        self.db.execute("COMMIT")

    def _check_format(self):
        m = _meta(self.db)
        if m.get("format") != FORMAT_NAME or m.get("version") != FORMAT_VERSION:
            raise ArchiveError(f"{self.path} is not a {FORMAT_NAME} version {FORMAT_VERSION} archive (found {m.get('format')} {m.get('version')})")

    def _epoch(self, info):
        m = _meta(self.db)
        ep = m.get("epochs", [])
        if not ep or ep[-1].get("fingerprint") != info.get("fingerprint"):
            ep.append(dict(info))
            self.db.execute("UPDATE meta SET value = ? WHERE key = 'epochs'", (json.dumps(ep),))

    def _triggers(self):
        for t in HISTORY_TABLES:
            for op in ("UPDATE", "DELETE"):
                self.db.execute(f"CREATE TRIGGER IF NOT EXISTS {t}_no_{op.lower()} BEFORE {op} ON {t} "
                                f"BEGIN SELECT RAISE(ABORT, 'the archive is append-only'); END")

    def close(self):
        self.db.close()

    # ---- writing
    def prepare(self, records):
        """Encode and hash one event's records (no I/O)."""
        if isinstance(records, Prepared):
            return records
        games, recs = [], []
        for r in records:
            if r["type"] == "game":
                games.append(encode_game(r))
            elif r["type"] == "season":
                recs.append(encode_record(r))
            else:
                raise ArchiveError(f"unknown archive record type {r['type']!r}")
        ids = {row["game_id"]: row["sha256"] for row, _, _ in games}
        ids.update({row["rid"]: row["sha256"] for row, _ in recs})
        if len(ids) != len(games) + len(recs):
            raise ArchiveError("two records in one batch share an id")
        return Prepared(games, recs, batch_digest(ids))

    def put_batch(self, seq, records):
        """Store the records one league event produced. Idempotent: identical records already present are left alone; a stored record
        that differs is `ArchiveConflict` and nothing at all is written."""
        pb = self.prepare(records)
        games, recs, bd = pb.games, pb.recs, pb.digest
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT n, sha256 FROM batches WHERE seq = ?", (seq,)).fetchone()
            if row:
                if row != (len(games) + len(recs), bd):
                    raise ArchiveConflict(f"event {seq} is already archived with different content")
                self._check_rows(games, recs)
            else:
                self._check_rows(games, recs, new_ok=True)
                self._insert(seq, games, recs, bd)
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def _check_rows(self, games, recs, new_ok=False):
        for row, body, traces in games:
            old = self.db.execute("SELECT sha256, n_traces FROM games WHERE game_id = ?", (row["game_id"],)).fetchone()
            if old is None:
                if not new_ok:
                    raise ArchiveMissing(f"game {row['game_id']} is not in the archive")
                continue
            if old != (row["sha256"], row["n_traces"]):
                raise ArchiveConflict(f"game {row['game_id']} is already archived with different content")
            for i, c in traces.items():
                t = self.db.execute("SELECT sha256 FROM traces WHERE game_id = ? AND play_i = ?", (row["game_id"], i)).fetchone()
                if t is None or t[0] != sha(c):
                    raise ArchiveConflict(f"trace {row['game_id']}#{i} is missing or differs")
        for row, body in recs:
            old = self.db.execute("SELECT sha256 FROM records WHERE rid = ?", (row["rid"],)).fetchone()
            if old is None:
                if not new_ok:
                    raise ArchiveMissing(f"record {row['rid']} is not in the archive")
            elif old[0] != row["sha256"]:
                raise ArchiveConflict(f"record {row['rid']} is already archived with different content")

    def _insert(self, seq, games, recs, bd):
        for row, body, traces in games:
            z = zlib.compress(body, 6)
            self.db.execute("INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            (row["game_id"], row["season"], seq, row["kind"], row["home"], row["away"], row["engine"], row["rule_version"],
                             row["n_traces"], row["sha256"], len(body), z))
            for i, c in sorted(traces.items()):
                self.db.execute("INSERT INTO traces VALUES (?,?,?,?,?)", (row["game_id"], i, sha(c), len(c), zlib.compress(c, 6)))
        for row, body in recs:
            self.db.execute("INSERT INTO records VALUES (?,?,?,?,?,?,?)",
                            (row["rid"], row["kind"], row["season"], seq, row["sha256"], len(body), zlib.compress(body, 6)))
        self.db.execute("INSERT INTO batches VALUES (?,?,?)", (seq, len(games) + len(recs), bd))

    def confirm_batch(self, seq, records, repair=False):
        """While recovering the league by replay: the archive must already hold exactly these records. With `repair`, a missing batch is
        written (for an archive restored from an older backup than the league file); a differing one is always `ArchiveConflict`."""
        try:
            pb = self.prepare(records)
            games, recs, bd = pb.games, pb.recs, pb.digest
            row = self.db.execute("SELECT n, sha256 FROM batches WHERE seq = ?", (seq,)).fetchone()
            if row is None:
                raise ArchiveMissing(f"event {seq} is in the league's journal but not in the archive")
            if row != (len(games) + len(recs), bd):
                raise ArchiveConflict(f"event {seq} replayed to different records than the archive holds")
            self._check_rows(games, recs)
        except ArchiveMissing:
            if not repair:
                raise
            self.put_batch(seq, pb)

    # ---- reading
    def games(self, season=None):
        q, a = "SELECT game_id, season, kind, home, away, engine, n_traces FROM games", ()
        if season is not None:
            q, a = q + " WHERE season = ?", (season,)
        return self.db.execute(q + " ORDER BY season, seq, game_id", a).fetchall()

    def game(self, game_id):
        row = self.db.execute("SELECT sha256, payload FROM games WHERE game_id = ?", (game_id,)).fetchone()
        if row is None:
            raise KeyError(game_id)
        body = zlib.decompress(row[1])
        if sha(body) != row[0]:
            raise ArchiveError(f"game {game_id} fails its checksum")
        return json.loads(body)

    def trace_bytes(self, game_id, play_i):
        row = self.db.execute("SELECT sha256, payload FROM traces WHERE game_id = ? AND play_i = ?", (game_id, play_i)).fetchone()
        if row is None:
            raise KeyError((game_id, play_i))
        c = zlib.decompress(row[1])
        if sha(c) != row[0]:
            raise ArchiveError(f"trace {game_id}#{play_i} fails its checksum")
        return c

    def record(self, rid):
        row = self.db.execute("SELECT sha256, payload FROM records WHERE rid = ?", (rid,)).fetchone()
        if row is None:
            raise KeyError(rid)
        body = zlib.decompress(row[1])
        if sha(body) != row[0]:
            raise ArchiveError(f"record {rid} fails its checksum")
        return json.loads(body)


# ---------------------------------------------------------------------------------------------------------------- whole-file operations
def batch_table(path):
    """{event seq: (record count, digest)} for every batch in the archive (read-only; cheap)."""
    db = _connect(path, readonly=True)
    try:
        return {s: (n, h) for s, n, h in db.execute("SELECT seq, n, sha256 FROM batches")}
    finally:
        db.close()


def league_id(path):
    db = _connect(path, readonly=True)
    try:
        return _meta(db).get("league_id")
    finally:
        db.close()


def parse_trace(c):
    """Container bytes -> (header dict, pos array('h') of steps*22*2, ball array('h') of steps*3). Standard library only."""
    if c[:4] != TRACE_MAGIC:
        raise ArchiveError("not a trace container")
    version, hl = struct.unpack("<BI", c[4:9])
    if version != TRACE_VERSION:
        raise ArchiveError(f"trace container version {version} is not readable here")
    head = json.loads(c[9:9 + hl])
    n = head["steps"]
    p0 = 9 + hl
    pe = p0 + n * 22 * 2 * 2
    be = pe + n * 3 * 2
    if len(c) != be:
        raise ArchiveError("trace container is the wrong length")
    pos, ball = array.array("h"), array.array("h")
    pos.frombytes(c[p0:pe])
    ball.frombytes(c[pe:be])
    if sys.byteorder == "big":
        pos.byteswap()
        ball.byteswap()
    return head, _undelta_columns(pos, 44, n), _undelta_columns(ball, 3, n)


def verify(path):
    """-> list of problems (empty: sound). Re-hashes every row, parses every trace, checks counts and that no history can be changed."""
    problems = []
    db = _connect(path, readonly=True)
    try:
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            problems.append("sqlite integrity check failed")
        m = _meta(db)
        if m.get("format") != FORMAT_NAME or m.get("version") != FORMAT_VERSION:
            problems.append(f"not a {FORMAT_NAME} v{FORMAT_VERSION} file")
            return problems
        have_triggers = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        for t in HISTORY_TABLES:
            for op in ("update", "delete"):
                if f"{t}_no_{op}" not in have_triggers:
                    problems.append(f"append-only trigger {t}_no_{op} is missing")
        n_tr = {}
        for gid, i, h, size, z in db.execute("SELECT game_id, play_i, sha256, size, payload FROM traces"):
            try:
                c = zlib.decompress(z)
                if sha(c) != h or len(c) != size:
                    problems.append(f"trace {gid}#{i} fails its checksum")
                else:
                    head, _, _ = parse_trace(c)
                    if head["game_id"] != gid or head["play_i"] != i:
                        problems.append(f"trace {gid}#{i} is labelled {head['game_id']}#{head['play_i']}")
            except Exception as e:
                problems.append(f"trace {gid}#{i} unreadable: {e}")
            n_tr[gid] = n_tr.get(gid, 0) + 1
        by_seq = {}
        for gid, season, seq, kind, h, n, size, z in db.execute("SELECT game_id, season, seq, kind, sha256, n_traces, size, payload FROM games"):
            try:
                body = zlib.decompress(z)
                if sha(body) != h or len(body) != size:
                    problems.append(f"game {gid} fails its checksum")
                else:
                    g = json.loads(body)
                    flagged = sum(1 for e in g["table"] if e.get("has_trace"))
                    if g["gid"] != gid or g["n_traces"] != n or flagged != n:
                        problems.append(f"game {gid} is inconsistent with its own table")
                    if g["engine"] == "spatial":
                        missing = [e["i"] for e in g["table"] if "call" in e and not e.get("has_trace")]
                        if missing:       # in the 22-player engine every simulated snap carries a play call and must carry its trace
                            problems.append(f"game {gid}: {len(missing)} snap(s) have no trace (first: play {missing[0]})")
            except Exception as e:
                problems.append(f"game {gid} unreadable: {e}")
            if n_tr.pop(gid, 0) != n:
                problems.append(f"game {gid} declares {n} traces and stores a different number")
            by_seq.setdefault(seq, {})[gid] = h
        for gid in n_tr:
            problems.append(f"traces stored for unknown game {gid}")
        for rid, kind, season, seq, h, size, z in db.execute("SELECT rid, kind, season, seq, sha256, size, payload FROM records"):
            try:
                body = zlib.decompress(z)
                if sha(body) != h or len(body) != size:
                    problems.append(f"record {rid} fails its checksum")
            except Exception as e:
                problems.append(f"record {rid} unreadable: {e}")
            by_seq.setdefault(seq, {})[rid] = h
        batches = {s: (n, h) for s, n, h in db.execute("SELECT seq, n, sha256 FROM batches")}
        for s, items in by_seq.items():
            if batches.get(s) != (len(items), batch_digest(items)):
                problems.append(f"batch for event {s} does not match the rows stored for it")
        for s in set(batches) - set(by_seq):
            problems.append(f"batch for event {s} has no rows")
    finally:
        db.close()
    return problems


def digest(path):
    """One SHA-256 over the identity of everything in the archive (ids and content hashes, not file layout): two archives with equal
    digests hold the same history."""
    db = _connect(path, readonly=True)
    try:
        h = hashlib.sha256()
        for q in ("SELECT 'g:' || game_id, sha256 FROM games", "SELECT 't:' || game_id || '#' || play_i, sha256 FROM traces",
                  "SELECT 'r:' || rid, sha256 FROM records"):
            for k, v in sorted(db.execute(q).fetchall()):
                h.update(f"{k}={v}\n".encode())
        return h.hexdigest()
    finally:
        db.close()


def stats(path):
    """Storage actually used, per table, per game, per traced snap, per season (bytes on disk are the stored, compressed payloads)."""
    db = _connect(path, readonly=True)
    try:
        one = lambda q: db.execute(q).fetchone()[0] or 0
        n_games, n_traces = one("SELECT COUNT(*) FROM games"), one("SELECT COUNT(*) FROM traces")
        g_bytes, t_bytes = one("SELECT SUM(LENGTH(payload)) FROM games"), one("SELECT SUM(LENGTH(payload)) FROM traces")
        r_bytes = one("SELECT SUM(LENGTH(payload)) FROM records")
        per_season = {s: (n, b) for s, n, b in db.execute(
            "SELECT g.season, COUNT(*), SUM(LENGTH(g.payload)) + COALESCE((SELECT SUM(LENGTH(t.payload)) FROM traces t "
            "JOIN games g2 ON g2.game_id = t.game_id WHERE g2.season = g.season), 0) FROM games g GROUP BY g.season")}
        by_kind = {k: (n, b) for k, n, b in db.execute("SELECT kind, COUNT(*), SUM(LENGTH(payload)) FROM games GROUP BY kind")}
        size = sum(os.path.getsize(path + x) for x in ("", "-wal") if os.path.exists(path + x))
        return {"file_bytes": size, "games": n_games, "traces": n_traces, "game_row_bytes": g_bytes, "trace_bytes": t_bytes,
                "record_bytes": r_bytes, "per_game_bytes": (g_bytes + t_bytes) / n_games if n_games else 0,
                "per_trace_bytes": t_bytes / n_traces if n_traces else 0,
                "uncompressed_trace_bytes": one("SELECT SUM(size) FROM traces"), "per_season": per_season, "by_kind": by_kind}
    finally:
        db.close()


def backup(src, dest):
    """Consistent online copy of an archive (safe while it is being written), verified before it is reported done. Never overwrites."""
    if os.path.exists(dest):
        raise ArchiveError(f"{dest} already exists")
    sdb = _connect(src, readonly=True)
    tmp = dest + ".partial"
    try:
        out = sqlite3.connect(tmp)
        try:
            sdb.backup(out)
        finally:
            out.close()
        problems = verify(tmp)
        if problems:
            os.remove(tmp)
            raise ArchiveError("the archive backup did not verify: " + "; ".join(problems[:5]))
        os.replace(tmp, dest)
    finally:
        sdb.close()
        for ext in ("-wal", "-shm"):
            if os.path.exists(tmp + ext):
                os.remove(tmp + ext)
    return dest


def restore(backup_path, dest):
    """Make `dest` a live archive from a verified backup. Never overwrites."""
    if os.path.exists(dest):
        raise ArchiveError(f"{dest} already exists; restore never overwrites an archive")
    problems = verify(backup_path)
    if problems:
        raise ArchiveError("the archive backup does not verify: " + "; ".join(problems[:5]))
    tmp = dest + ".partial"
    src = sqlite3.connect(backup_path)
    out = sqlite3.connect(tmp)
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()
    os.replace(tmp, dest)
    return dest
