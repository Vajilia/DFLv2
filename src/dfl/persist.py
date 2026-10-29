"""Durable saving, restart recovery, atomic event processing, backup and restore (Phase 6, storage half).

The league is a `year.Universe`; its unit of work is one calendar event (`Universe.step_event`). This module keeps a SQLite file that
always holds a complete, verifiable record of the league as of its last *committed* event:

  meta       one row per fact about the file: format version, code fingerprint, creation arguments, snapshot cadence.
  journal    one row per committed event: sequence number, year, event index, key, a digest of the league after the event and a
             hash chain over all rows so far (editing any past row breaks every later link).
  snapshots  zlib-compressed pickles of the whole Universe at chosen sequence numbers, each with its own SHA-256.

**Atomic events.** An event runs in memory; only when it has finished does one SQLite transaction write its journal row, its
snapshot (if one is due) and the pruning of old snapshots. An exception inside an event, or a kill at any instant, therefore leaves the
file at the previous event; the half-changed in-memory league is discarded and reloaded. The transaction also checks that the file
still stands at the sequence number this runner last wrote, so two runners cannot silently fork one league.

**Recovery.** `Runner.open` takes the newest snapshot whose checksum holds, then replays the journal after it by firing the same
events again and comparing the digest after each with the journal's. Replay is exact because the league is deterministic (seeded
random streams, no clock, no network, no language model); a mismatch raises `ReplayDivergence` instead of continuing quietly.

**Backup and restore.** `backup` uses SQLite's online backup (consistent while the file is in use) and verifies the copy;
`restore` verifies a backup before it becomes a live file. `verify` checks SQLite integrity, journal continuity, the hash chain and
every snapshot checksum.

**Trust.** Snapshots are Python pickles read through an allow-listed unpickler (only `dfl`, `numpy` and a few standard-library
types), so a damaged or hostile file cannot run arbitrary code; it is still a format tied to this code: the file records a
fingerprint of the source and data files and refuses to open under different code unless told to (`allow_code_change`).
Nothing here reads a clock.
"""
import hashlib
import io
import json
import os
import pickle
import random
import sqlite3
import sys
import types
import zlib

from .year import Universe

FORMAT_VERSION = 1
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class PersistError(Exception):
    pass


class CodeMismatch(PersistError):
    """The file was written by different source or data files than the ones now running."""


class Unrecoverable(PersistError):
    """No snapshot in the file can be loaded."""


class ReplayDivergence(PersistError):
    """Replaying the journal did not reproduce the recorded digests."""


class ConcurrentWriter(PersistError):
    """Another runner has committed to the file since this one last did."""


class Corrupt(PersistError):
    pass


# ---------------------------------------------------------------------------------------------------------------- fingerprint
def fingerprint(root=ROOT):
    """SHA-256 over every source file under src/dfl and every file under data/, by relative path. Same code and data, same value."""
    h = hashlib.sha256()
    for top in ("src/dfl", "data"):
        base = os.path.join(root, top)
        for dirpath, dirs, files in os.walk(base):
            dirs[:] = sorted(d for d in dirs if d != "__pycache__")
            for f in sorted(files):
                if f.endswith((".pyc", ".pyo")):
                    continue
                path = os.path.join(dirpath, f)
                h.update(os.path.relpath(path, root).replace(os.sep, "/").encode() + b"\0")
                with open(path, "rb") as fh:
                    h.update(hashlib.sha256(fh.read()).digest())
    return h.hexdigest()


# ---------------------------------------------------------------------------------------------------------------- serialisation
_SAFE = {
    "builtins": {"set", "frozenset", "dict", "list", "tuple", "int", "float", "str", "bytes", "bytearray", "complex", "range", "slice",
                 "object", "bool"},
    "collections": {"OrderedDict", "defaultdict", "deque", "Counter"},
    "random": {"Random"},
    "fractions": {"Fraction"},
    "decimal": {"Decimal"},
    "datetime": {"date", "datetime", "timedelta", "time"},
    "copyreg": {"_reconstructor"},
    "numpy": {"dtype", "ndarray"},
    "numpy.core.multiarray": {"_reconstruct", "scalar"},
    "numpy._core.multiarray": {"_reconstruct", "scalar"},
}


class _Unpickler(pickle.Unpickler):
    """Reads only the classes a league can contain: this package's own, and a short list of plain data types."""
    def find_class(self, module, name):
        if module.startswith("dfl.") or name in _SAFE.get(module, ()):
            return super().find_class(module, name)
        raise pickle.UnpicklingError(f"a league file may not contain {module}.{name}")


def dumps(obj):
    """-> (compressed bytes, sha256 hex of those bytes)."""
    raw = zlib.compress(pickle.dumps(obj, protocol=5), 3)
    return raw, hashlib.sha256(raw).hexdigest()


def loads(raw):
    return _Unpickler(io.BytesIO(zlib.decompress(raw))).load()


# ---------------------------------------------------------------------------------------------------------------- digests
def deep_digest(obj, ignore=("_units",)):
    """A digest of an entire object graph that does not depend on dict or set order, memory addresses or hash randomisation, so two
    leagues that hold the same values have the same digest whichever process built them. Random generators contribute their full
    state. `ignore` names derived caches that may or may not be filled without changing any outcome."""
    import numpy as np
    memo = {}
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))

    def prim(tag, body):
        b = body if isinstance(body, bytes) else str(body).encode()
        return tag + str(len(b)).encode() + b":" + b

    def comb(tag, parts):
        hh = hashlib.sha256(tag)
        for p in parts:
            hh.update(len(p).to_bytes(4, "big"))
            hh.update(p)
        return b"h" + hh.digest()

    def h(o):
        t = type(o)
        if o is None:
            return b"N"
        if t is bool or t is int:
            return prim(b"i", repr(o))
        if t is float:
            return prim(b"f", repr(o))
        if t is str:
            return prim(b"s", o.encode())
        if t is bytes:
            return prim(b"b", o)
        i = id(o)
        if i in memo:
            return memo[i]
        memo[i] = b"cycle"
        name = f"{t.__module__}.{t.__qualname__}".encode()
        if isinstance(o, random.Random):
            r = comb(b"random" + name, [h(o.getstate())])
        elif isinstance(o, np.ndarray):
            r = comb(b"nd", [str(o.dtype).encode(), repr(o.shape).encode(), o.tobytes()])
        elif isinstance(o, np.generic):
            r = prim(b"g", repr(o))
        elif isinstance(o, dict):
            r = comb(b"dict" + name, sorted(h(k) + b"=" + h(v) for k, v in o.items()))
        elif isinstance(o, (set, frozenset)):
            r = comb(b"set" + name, sorted(h(x) for x in o))
        elif isinstance(o, (list, tuple)) or t.__name__ == "deque":
            r = comb(b"seq" + name, [h(x) for x in o])
        elif isinstance(o, (int, float, str, bytes)):
            r = comb(b"sub" + name, [repr(o).encode()])
        elif isinstance(o, (type, types.FunctionType, types.MethodType, types.BuiltinFunctionType)):
            r = prim(b"c", f"{getattr(o, '__module__', '')}.{getattr(o, '__qualname__', repr(o))}")
        elif hasattr(o, "__dict__") or hasattr(t, "__slots__"):
            state = dict(getattr(o, "__dict__", {}))
            for klass in t.__mro__:
                for sl in getattr(klass, "__slots__", ()) or ():
                    if hasattr(o, sl):
                        state[sl] = getattr(o, sl)
            r = comb(b"obj" + name, sorted(h(k) + b"=" + h(v) for k, v in state.items() if k not in ignore))
        else:
            r = prim(b"o", f"{name!r}:{o!r}")
        memo[i] = r
        return r

    return hashlib.sha256(h(obj)).hexdigest()


def quick_digest(u):
    """The per-event digest in the journal: the ledger digest plus the cursors, the random streams and the books that decide the
    future. Cheap (milliseconds); `deep_digest` is the exhaustive check used at snapshots when asked and in tests."""
    hh = hashlib.sha256()
    hh.update(u.L.digest().encode())
    hh.update(repr((u.season, u.cursor, u.paid_week, len(u.fired), len(u.reports), sorted(u.history.items()))).encode())
    for rng in (u.rng, u.world.rng):
        hh.update(hashlib.sha256(repr(rng.getstate()).encode()).digest())
    st = u.world.stats
    hh.update(json.dumps(st, sort_keys=True, default=str).encode())
    hh.update(repr((getattr(u.resolver, "games_played", None), getattr(u.resolver, "injuries_seen", None))).encode())
    return hh.hexdigest()


# ---------------------------------------------------------------------------------------------------------------- the file
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS journal (
    seq INTEGER PRIMARY KEY, year INTEGER NOT NULL, n_fired INTEGER NOT NULL, key TEXT NOT NULL,
    digest TEXT NOT NULL, chain TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS snapshots (
    seq INTEGER PRIMARY KEY, sha256 TEXT NOT NULL, size INTEGER NOT NULL, checkpoint INTEGER NOT NULL, payload BLOB NOT NULL);
"""
GENESIS = "0" * 64


def _chain(prev, seq, key, digest):
    return hashlib.sha256(f"{prev}|{seq}|{key}|{digest}".encode()).hexdigest()


def connect(path):
    db = sqlite3.connect(path, isolation_level=None)         # explicit transactions below
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    return db


class Runner:
    """A league whose every event is durable. Use `Runner.create(path, tables, ...)` for a new file and `Runner.open(path)` to resume.

    snapshot_every   write a full snapshot after every Nth event (1 = every event). Between snapshots recovery replays the journal.
    keep             how many of the newest ordinary snapshots to keep (the one before the latest is the fallback if the latest is damaged).
    Year-end snapshots are checkpoints and are kept for ever.
    crash_hook       test seam: called with a point name ("event_done", "journal_written") inside the commit; raising there is a crash.
    """

    def __init__(self, path, db, universe, seq, chain, meta, crash_hook=None):
        self.path, self.db, self.u, self.seq, self.chain, self.meta, self.crash_hook = path, db, universe, seq, chain, meta, crash_hook

    # ---- creation and opening
    @classmethod
    def create(cls, path, tables, seed=1, snapshot_every=1, keep=3, crash_hook=None, **universe_args):
        if os.path.exists(path) and os.path.getsize(path):
            raise PersistError(f"{path} already exists")
        db = connect(path)
        db.executescript(SCHEMA)
        meta = {"format": FORMAT_VERSION, "fingerprint": fingerprint(), "seed": seed, "snapshot_every": snapshot_every, "keep": keep,
                "universe_args": universe_args, "tables": os.path.abspath(tables)}
        u = Universe(tables, seed=seed, **universe_args)
        raw, sha = dumps(u)
        db.execute("BEGIN IMMEDIATE")
        try:
            for k, v in meta.items():
                db.execute("INSERT INTO meta VALUES (?,?)", (k, json.dumps(v, sort_keys=True)))
            db.execute("INSERT INTO snapshots VALUES (0,?,?,1,?)", (sha, len(raw), raw))
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
        return cls(path, db, u, 0, GENESIS, meta, crash_hook)

    @classmethod
    def open(cls, path, allow_code_change=False, crash_hook=None, heal=True):
        if not os.path.exists(path):
            raise PersistError(f"{path} does not exist")
        db = connect(path)
        meta = {k: json.loads(v) for k, v in db.execute("SELECT key, value FROM meta")}
        if meta.get("format") != FORMAT_VERSION:
            raise PersistError(f"league file format {meta.get('format')} is not {FORMAT_VERSION}")
        if meta["fingerprint"] != fingerprint() and not allow_code_change:
            raise CodeMismatch("this league file was written by different source or data files; replay would not be exact")
        head, chain = cls._head(db)
        u, seq = None, None
        for sseq, sha, raw in db.execute("SELECT seq, sha256, payload FROM snapshots WHERE seq <= ? ORDER BY seq DESC", (head,)):
            if hashlib.sha256(raw).hexdigest() != sha:
                continue                                      # damaged: fall back to an older snapshot and replay further
            try:
                u, seq = loads(raw), sseq
            except Exception:
                continue
            break
        if u is None:
            raise Unrecoverable("no snapshot in the file passes its checksum")
        r = cls(path, db, u, seq, None, meta, crash_hook)
        r._replay(head)
        r.chain = chain
        if heal and r.seq != seq:
            r._write_snapshot(r.seq)                          # next recovery starts here, not at the old snapshot
        return r

    @staticmethod
    def _head(db):
        row = db.execute("SELECT seq, chain FROM journal ORDER BY seq DESC LIMIT 1").fetchone()
        return (row[0], row[1]) if row else (0, GENESIS)

    def _replay(self, head):
        rows = self.db.execute("SELECT seq, year, n_fired, key, digest FROM journal WHERE seq > ? AND seq <= ? ORDER BY seq", (self.seq, head)).fetchall()
        for seq, year, n_fired, key, digest in rows:
            at = self.u.cursor
            fired = self.u.step_event()
            if fired != key or quick_digest(self.u) != digest:
                raise ReplayDivergence(f"replaying event {seq} ({key}) did not reproduce the journal (cursor was {at})")
            self.seq = seq

    def _write_snapshot(self, seq, checkpoint=None):
        raw, sha = dumps(self.u)
        if checkpoint is None:
            checkpoint = int(self.u.cursor is None)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?)", (seq, sha, len(raw), checkpoint, raw))
            self._prune()
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def _prune(self):
        keep = int(self.meta.get("keep", 3))
        self.db.execute("DELETE FROM snapshots WHERE checkpoint = 0 AND seq NOT IN "
                        "(SELECT seq FROM snapshots WHERE checkpoint = 0 ORDER BY seq DESC LIMIT ?)", (keep,))

    # ---- running
    def step(self):
        """Fire the next event and commit it atomically. Returns the event key. If the event raises, or the commit does, the league in
        memory is reloaded from the file, so the runner never holds a state the file does not."""
        try:
            key = self.u.step_event()
            digest = quick_digest(self.u)
            self._commit(key, digest)
        except BaseException:
            self._reload()
            raise
        return key

    def _commit(self, key, digest):
        seq = self.seq + 1
        due = seq % int(self.meta["snapshot_every"]) == 0 or self.u.cursor is None
        raw = sha = None
        if due:
            raw, sha = dumps(self.u)
        year, n_fired = self.u.fired[-1][0], len(self.u.fired)
        chain = _chain(self.chain, seq, key, digest)
        self._hook("event_done")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            head, head_chain = self._head(self.db)
            if head != self.seq or head_chain != self.chain:
                raise ConcurrentWriter(f"the file is at event {head}, this runner is at {self.seq}")
            self.db.execute("INSERT INTO journal VALUES (?,?,?,?,?,?)", (seq, year, n_fired, key, digest, chain))
            self._hook("journal_written")
            if due:
                cp = int(self.u.cursor is None)
                self.db.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?)", (seq, sha, len(raw), cp, raw))
                self._prune()
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.seq, self.chain = seq, chain

    def _hook(self, point):
        if self.crash_hook:
            self.crash_hook(point)

    def _reload(self):
        """Discard the in-memory league and rebuild it from the file (the last committed event)."""
        fresh = Runner.open(self.path, allow_code_change=True, crash_hook=self.crash_hook, heal=False)
        self.db.close()
        self.db, self.u, self.seq, self.chain = fresh.db, fresh.u, fresh.seq, fresh.chain

    def run_events(self, n):
        return [self.step() for _ in range(n)]

    def run_year(self):
        """Fire events until the year in progress (or the next one) closes. Returns the number of events fired."""
        self.step()
        n = 1
        while self.u.cursor is not None:
            self.step()
            n += 1
        return n

    def close(self):
        self.db.close()

    # ---- maintenance
    def backup(self, dest):
        return backup(self.path, dest, source_db=self.db)

    def status(self):
        return status(self.path, self.db)


def status(path, db=None):
    own = db is None
    db = db or connect(path)
    try:
        head, chain = Runner._head(db)
        snaps = [(s, c, n) for s, c, n in db.execute("SELECT seq, checkpoint, size FROM snapshots ORDER BY seq")]
        last = db.execute("SELECT year, n_fired, key FROM journal ORDER BY seq DESC LIMIT 1").fetchone()
        return {"events": head, "last": last, "chain": chain, "snapshots": snaps, "bytes": os.path.getsize(path)}
    finally:
        if own:
            db.close()


# ---------------------------------------------------------------------------------------------------------------- verify, backup, restore
def verify(path, deep=False):
    """-> list of problems (empty means the file is sound). Checks SQLite integrity, journal continuity and hash chain, snapshot
    checksums and, with deep=True, that the newest snapshot loads."""
    problems = []
    db = connect(path)
    try:
        res = db.execute("PRAGMA integrity_check").fetchall()
        if res != [("ok",)]:
            problems.append(f"sqlite integrity check: {res[:3]}")
        prev, expect = GENESIS, 1
        for seq, key, digest, chain in db.execute("SELECT seq, key, digest, chain FROM journal ORDER BY seq"):
            if seq != expect:
                problems.append(f"journal gap: expected event {expect}, found {seq}")
                expect = seq
            if chain != _chain(prev, seq, key, digest):
                problems.append(f"journal chain broken at event {seq}")
            prev, expect = chain, expect + 1
        head = expect - 1
        n_ok = 0
        for seq, sha, raw in db.execute("SELECT seq, sha256, payload FROM snapshots ORDER BY seq"):
            if hashlib.sha256(raw).hexdigest() != sha:
                problems.append(f"snapshot {seq} fails its checksum")
            else:
                n_ok += 1
            if seq > head:
                problems.append(f"snapshot {seq} is ahead of the journal (head {head})")
        if not n_ok:
            problems.append("no snapshot passes its checksum")
        if deep and not problems:
            try:
                Runner.open(path, allow_code_change=True, heal=False).close()
            except PersistError as e:
                problems.append(f"recovery failed: {e}")
        return problems
    finally:
        db.close()


def backup(src, dest, source_db=None):
    """Consistent online copy of a league file (safe while a runner is using it), verified before it is reported done."""
    if os.path.exists(dest):
        raise PersistError(f"{dest} already exists")
    own = source_db is None
    sdb = source_db or connect(src)
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
            raise Corrupt("the backup did not verify: " + "; ".join(problems))
        os.replace(tmp, dest)
    finally:
        if own:
            sdb.close()
        for ext in ("-wal", "-shm"):
            if os.path.exists(tmp + ext):
                os.remove(tmp + ext)
    return dest


def restore(backup_path, dest, allow_code_change=False):
    """Make `dest` a live league file from a verified backup. Never overwrites an existing file."""
    if os.path.exists(dest):
        raise PersistError(f"{dest} already exists; restore never overwrites a league file")
    problems = verify(backup_path)
    if problems:
        raise Corrupt("the backup does not verify: " + "; ".join(problems))
    tmp = dest + ".partial"
    src = sqlite3.connect(backup_path)
    out = sqlite3.connect(tmp)
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()
    os.replace(tmp, dest)
    r = Runner.open(dest, allow_code_change=allow_code_change)       # proves it recovers
    return r
