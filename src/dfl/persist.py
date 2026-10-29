"""Durable saving, restart recovery, atomic event processing, backup, restore and upgrade (Phase 6, storage half).

The league is a `year.Universe`; its unit of work is one calendar event (`Universe.step_event`). This module keeps a SQLite file that
always holds a complete, verifiable record of the league as of its last *committed* event:

  meta       facts about the file: save-schema version, the fingerprint of the code and data that may continue it, how it was made,
             the lineage of files it was upgraded from, the sequence number and chain value it starts from, creation arguments.
  journal    one row per committed event: sequence number, year, events fired so far, key, a digest of the league after the event and
             the number of archive records the event produced and their digest, and a hash chain over all rows so far (editing any past row,
             including what it says the archive holds, breaks every later link).
  snapshots  zlib-compressed pickles of the whole Universe at chosen sequence numbers, each with its own SHA-256.
  code       the source and data files (a zip, with a per-file manifest) of every code version that has written to the file.

**Atomic events.** An event runs in memory; only when it has finished are its archive records (archive.py) and then one SQLite
transaction (journal row, snapshot if due, pruning) written. An exception inside an event, or a kill at any instant, leaves the file at
the previous event; the half-changed league is discarded and reloaded. The transaction also checks that the file still stands where this
runner last left it, so two runners cannot silently fork one league. The archive is written first and idempotently, so it can be ahead
of the league file by the one event in flight, never behind; the repeated event finds identical records already there.

**Archive consistency.** The league and its archive share a random `league_id`. `Runner.open` compares every journal row with the archive's
batch table (`archive_check`) before it replays anything, and refuses (`ArchiveInconsistent`, nothing written) an archive that lacks
records the journal committed, holds different ones, belongs to another league, is absent or unreadable, or is ahead by more than the one
event an interrupted commit leaves. `repair_archive` rebuilds missing events by replay and proves each against the journal.

**Recovery.** `Runner.open` takes the newest snapshot whose checksum holds, then replays the journal after it by firing the same events
again and comparing the digest after each with the journal's (and the regenerated archive records with the archive's). Replay is exact
because the league is deterministic (seeded random streams, no clock, no network, no language model).

**Compatibility is two different questions.** *Save schema* (`format`): can this code read the file's tables at all? *Code identity*
(`fingerprint`): is this the exact source and data that wrote the league, so that replaying and continuing it reproduces what would have
happened? A file opens for use only if both hold. `compatibility()` reports both without loading anything, and `status` and `verify`
work under any code. A file whose format this code does not read, or whose code differs, is refused with a message that says why.

**Upgrade (code changed).** `upgrade(old, new)` never touches the old file. It copies it, exports the league *under the code that wrote
it* (the file carries that code in its `code` table; running it is opt-in because it is code), writes the state in the neutral form of
neutral.py, imports that under the current code into a new file, checks the imported state by value against the digest taken at export,
and records the old file's identity (code fingerprint, final sequence number and chain value) in the new file's lineage. The new file
continues the sequence numbers and the hash chain of the old one. Exact reproduction holds within each code version; across an upgrade
the past is preserved (the old file and the archive are kept) and the future is whatever the new code does.

**Backup and restore.** `backup` uses SQLite's online backup (consistent while the file is in use) and verifies the copy; `restore`
verifies a backup before it becomes a live file. `backup_set` and `restore_set` do the same for a league file with its archive.

**Trust.** Snapshots are pickles read through an allow-listed unpickler (only `dfl`, plus a few plain data types), so a damaged or
hostile file cannot run arbitrary code. Checksums detect damage; they do not authenticate. Nothing here reads a clock.
"""
import hashlib
import io
import json
import os
import pickle
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid
import zipfile
import zlib

from . import neutral
from .neutral import deep_digest          # re-exported: the exhaustive state digest
from .year import Universe

FORMAT_VERSION = 3                      # the save schema this code writes
SUPPORTED_FORMATS = (3,)                # the save schemas this code can read (formats 1 and 2 were development builds; see _refuse)
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class PersistError(Exception):
    pass


class UnsupportedFormat(PersistError):
    """The save schema is one this code does not read."""


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


class ArchiveInconsistent(PersistError):
    """The archive beside the league does not hold what the league's journal says it committed (records missing or different, an archive
    of another league, or one too far ahead to be an interrupted commit). `kinds` lists which; `seqs` the events concerned."""

    def __init__(self, message, kinds=(), seqs=()):
        super().__init__(message)
        self.kinds, self.seqs = tuple(kinds), tuple(seqs)


class StateMismatch(PersistError):
    """An imported state does not equal the state that was exported. `diagnostics` (a dict, also written to $DFL_DIAG_DIR when that is set)
    says where: see `mismatch_report`."""
    diagnostics = None


DIAG_CONTEXT = []        # callables returning a dict, called when a mismatch report is made (the test runner registers the order of tests run)


def mismatch_report(doc, u, actual_digest, source=None):
    """Everything needed to tell a real state difference from an unstable digest, gathered at the moment of a mismatch: the expected and
    actual digests, the digest of the parts of the league on both sides, the random generators' states on both sides, the differences between
    the exported document and a fresh encoding of the imported state, fresh re-decodes of the same document (an unstable digest gives
    different answers for identical input), versions, the hash seed, and whatever the registered context hooks add."""
    import platform
    m = doc["manifest"]
    expected = m.get("deep_digest")
    again = []
    for _ in range(3):
        try:
            again.append(neutral.deep_digest(neutral.decode(doc["state"])))
        except Exception as e:                       # a report must never hide the failure it describes
            again.append(f"error: {e}")
    parts_now, rngs_now = neutral.digest_parts(u), neutral.rng_states(u)
    parts_was, rngs_was = m.get("digest_parts"), m.get("rng_states")
    rep = {
        "expected_digest": expected, "actual_digest": actual_digest, "fresh_redecode_digests": again,
        "digest_is_unstable": len(set(again + [actual_digest])) > 1,
        "differing_parts": None if parts_was is None else sorted(k for k in set(parts_was) | set(parts_now) if parts_was.get(k) != parts_now.get(k)),
        "differing_random_states": None if rngs_was is None else sorted(k for k in set(rngs_was) | set(rngs_now) if rngs_was.get(k) != rngs_now.get(k)),
        "random_states_imported": rngs_now, "random_states_exported": rngs_was,
        "document_differences": neutral.state_diff(doc["state"], neutral.encode(u)),
        "manifest": {k: m.get(k) for k in ("league_format", "fingerprint", "seq", "chain", "seed", "rule_version", "created_with", "league_id")},
        "this_code_fingerprint": fingerprint(), "source": source,
        "versions": {"python": sys.version, "implementation": platform.python_implementation(), "platform": platform.platform(),
                     "numpy": getattr(neutral.np, "__version__", None), "sqlite": sqlite3.sqlite_version, "zlib": zlib.ZLIB_VERSION,
                     "document_version": neutral.DOC_VERSION, "save_schema": FORMAT_VERSION},
        "environment": {"PYTHONHASHSEED": os.environ.get("PYTHONHASHSEED"), "hash_randomization": sys.flags.hash_randomization,
                        "isolated": sys.flags.isolated, "pid": os.getpid(), "cwd": os.getcwd(), "argv": sys.argv[:6],
                        "recursion_limit": sys.getrecursionlimit()},
        "context": {},
    }
    for i, hook in enumerate(DIAG_CONTEXT):
        try:
            rep["context"][getattr(hook, "__name__", f"hook{i}")] = hook()
        except Exception as e:
            rep["context"][getattr(hook, "__name__", f"hook{i}")] = f"error: {e}"
    where = os.environ.get("DFL_DIAG_DIR")
    if where:
        try:
            os.makedirs(where, exist_ok=True)
            fd, path = tempfile.mkstemp(prefix="statemismatch-", suffix=".json", dir=where)
            with os.fdopen(fd, "w") as f:
                json.dump(rep, f, indent=1, sort_keys=True, default=str)
            rep["written_to"] = path
        except OSError as e:
            rep["written_to"] = f"could not write: {e}"
    return rep


# ---------------------------------------------------------------------------------------------------------------- code identity
def code_manifest(root=ROOT):
    """{relative path: SHA-256} of every source file under src/dfl and every file under data/."""
    out = {}
    for top in ("src/dfl", "data"):
        base = os.path.join(root, top)
        for dirpath, dirs, files in os.walk(base):
            dirs[:] = sorted(d for d in dirs if d != "__pycache__")
            for f in sorted(files):
                if f.endswith((".pyc", ".pyo")):
                    continue
                path = os.path.join(dirpath, f)
                with open(path, "rb") as fh:
                    out[os.path.relpath(path, root).replace(os.sep, "/")] = hashlib.sha256(fh.read()).hexdigest()
    return out


def fingerprint(root=ROOT, manifest=None):
    """One SHA-256 over the manifest: same code and data, same value."""
    manifest = manifest if manifest is not None else code_manifest(root)
    h = hashlib.sha256()
    for k in sorted(manifest):
        h.update(k.encode() + b"\0" + manifest[k].encode() + b"\n")
    return h.hexdigest()


def code_zip(root=ROOT, manifest=None):
    """The code and data themselves as a zip (deterministic bytes), so a league file can reproduce the code that wrote it."""
    manifest = manifest if manifest is not None else code_manifest(root)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in sorted(manifest):
            info = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(os.path.join(root, rel), "rb") as fh:
                z.writestr(info, fh.read())
    return buf.getvalue()


def rule_version():
    from .engine.params import RULE_VERSION
    return RULE_VERSION


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


def quick_digest(u):
    """The per-event digest in the journal: the ledger digest plus the cursors, the random streams and the books that decide the
    future. Cheap (milliseconds); `deep_digest` is the exhaustive check used when asked and in tests."""
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
    digest TEXT NOT NULL, chain TEXT NOT NULL, n_records INTEGER NOT NULL, records_digest TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS snapshots (
    seq INTEGER PRIMARY KEY, sha256 TEXT NOT NULL, size INTEGER NOT NULL, checkpoint INTEGER NOT NULL, payload BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS code (fingerprint TEXT PRIMARY KEY, manifest TEXT NOT NULL, zip BLOB NOT NULL);
"""
GENESIS = "0" * 64


def _chain(prev, seq, key, digest, n_records=0, records_digest=""):
    """The journal's hash chain. It covers the event's archive records (how many, and their digest), so editing what the journal says the
    archive holds breaks the chain."""
    return hashlib.sha256(f"{prev}|{seq}|{key}|{digest}|{n_records}|{records_digest}".encode()).hexdigest()


def connect(path, readonly=False):
    if readonly:
        return sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True, isolation_level=None)
    db = sqlite3.connect(path, isolation_level=None)         # explicit transactions below
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    return db


def read_meta(db):
    return {k: json.loads(v) for k, v in db.execute("SELECT key, value FROM meta")}


def _tables_ref(tables):
    """Tables are stored relative to the repository root when they live in it, so a file moves between machines."""
    t = os.path.abspath(tables)
    return os.path.relpath(t, ROOT).replace(os.sep, "/") if t.startswith(ROOT + os.sep) else t


def _tables_path(ref):
    return ref if os.path.isabs(ref) else os.path.join(ROOT, ref)


def _resolve_archive(league_path, ref):
    return ref if os.path.isabs(ref) else os.path.join(os.path.dirname(os.path.abspath(league_path)), ref)


def _archive_ref(league_path, archive_path):
    base = os.path.dirname(os.path.abspath(league_path))
    a = os.path.abspath(archive_path)
    return os.path.basename(a) if os.path.dirname(a) == base else a


# ---------------------------------------------------------------------------------------------------------------- archive consistency
def _archive_chain(table, upto):
    """Hash chain over the archive's batches up to event `upto` ({seq: (n, digest)} from archive.batch_table)."""
    return neutral.batch_chain(GENESIS, sorted((s, n, d) for s, (n, d) in table.items() if s <= upto))


def archive_check(path, archive_file=None):
    """Compare a league file's journal with the archive beside it. Read-only; needs no snapshot and no league state, so it works on any
    file of a supported schema. Returns a dict:
      absent / unreadable / foreign   the archive is not there, cannot be read, or belongs to another league
      base_ok     the archive's history up to where this file's journal begins is what the file recorded then
      missing     journal events whose records the archive lacks
      mismatched  journal events whose records in the archive differ from the journal's count or digest (or that the journal says had none)
      ahead       archive batches for events after the journal's head (one is what an interrupted commit leaves)
    """
    from . import archive as A
    db = connect(path, readonly=True)
    try:
        meta = read_meta(db)
        rows = db.execute("SELECT seq, n_records, records_digest FROM journal ORDER BY seq").fetchall()
    finally:
        db.close()
    head = rows[-1][0] if rows else meta.get("base", {"seq": 0})["seq"]
    out = {"has_archive": bool(meta.get("archive")), "file": None, "head": head, "absent": False, "unreadable": None, "foreign": False,
           "base_ok": True, "missing": [], "mismatched": [], "ahead": []}
    if not out["has_archive"]:
        return out
    apath = archive_file or _resolve_archive(path, meta["archive"]["file"])
    out["file"] = apath
    if not os.path.exists(apath):
        out["absent"] = True
        return out
    try:
        mine = A.league_id(apath)
        table = A.batch_table(apath)
    except sqlite3.DatabaseError as e:
        out["unreadable"] = str(e)
        return out
    if mine != meta.get("league_id"):
        out["foreign"] = True
        return out
    base = meta.get("archive_base") or {"through": 0, "chain": GENESIS}
    out["base_ok"] = _archive_chain(table, base["through"]) == base["chain"]
    for seq, n, rd in rows:
        got = table.get(seq)
        if n == 0:
            if got is not None:
                out["mismatched"].append(seq)
        elif got is None:
            out["missing"].append(seq)
        elif got != (n, rd):
            out["mismatched"].append(seq)
    out["ahead"] = sorted(s for s in table if s > head)
    return out


def _judge_archive(path, chk, window_floor, repair_archive, allow_archive_ahead):
    """Raise ArchiveInconsistent unless the archive may be used to continue the league. `window_floor` is the newest event the replay
    starts after: with `repair_archive`, missing batches are acceptable because replay will rebuild (and prove) them."""
    bad, kinds, seqs = [], [], []

    def add(kind, why, ss=()):
        bad.append(why)
        kinds.append(kind)
        seqs.extend(ss)

    if chk["absent"]:
        add("absent", f"the archive {chk['file']} is not there")
    elif chk["unreadable"]:
        add("unreadable", f"the archive {chk['file']} cannot be read ({chk['unreadable']})")
    elif chk["foreign"]:
        add("foreign", f"the archive {chk['file']} belongs to another league")
    else:
        if not chk["base_ok"]:
            add("base", "the archive's history from before this league file began is not the history the file recorded")
        if chk["mismatched"]:
            m = chk["mismatched"]
            add("mismatched", f"{len(m)} event(s) have different records in the archive than the league committed (first: event {m[0]})", m)
        if chk["missing"]:
            m = chk["missing"]
            if not repair_archive:
                add("missing", f"the archive lacks the records of {len(m)} event(s) the league has committed (first: event {m[0]}, last: "
                               f"event {m[-1]}); it is older than the league file or from a different backup. Restore the archive that "
                               "belongs with this league file, or open with repair_archive=True to rebuild the missing events by replay", m)
        ahead = chk["ahead"]
        if ahead and (allow_archive_ahead is False) and ahead != [chk["head"] + 1]:
            add("ahead", f"the archive holds records for {len(ahead)} event(s) after the league's last committed event {chk['head']} "
                         f"(first: event {ahead[0]}); an interrupted commit leaves at most event {chk['head'] + 1}. This looks like an older "
                         "league file beside a newer archive; open with allow_archive_ahead=True if that is intended", ahead)
    if bad:
        raise ArchiveInconsistent(f"{path}: " + "; ".join(bad), kinds, seqs)


# ---------------------------------------------------------------------------------------------------------------- compatibility
def compatibility(path):
    """Two questions about a league file, answered without loading any snapshot (so it works under any code):
    `schema`: "ok" if this code reads the file's save schema, else "unsupported";
    `code`:   "exact" if the file was written by exactly this source and data, else "differs" (with the files that differ).
    `usable` is True only when both hold."""
    db = connect(path, readonly=True)
    try:
        meta = read_meta(db)
        have = meta.get("fingerprint")
        row = db.execute("SELECT manifest FROM code WHERE fingerprint = ?", (have,)).fetchone() if meta.get("format") in SUPPORTED_FORMATS else None
    finally:
        db.close()
    fmt = meta.get("format")
    out = {"format": fmt, "supported_formats": list(SUPPORTED_FORMATS), "file_fingerprint": have, "current_fingerprint": None,
           "schema": "ok" if fmt in SUPPORTED_FORMATS else "unsupported", "code": None, "changed_files": None,
           "created_with": meta.get("created_with"), "lineage": meta.get("lineage", [])}
    if out["schema"] == "ok":
        mine = code_manifest()
        out["current_fingerprint"] = fingerprint(manifest=mine)
        out["code"] = "exact" if have == out["current_fingerprint"] else "differs"
        if out["code"] == "differs" and row:
            then = json.loads(row[0])
            out["changed_files"] = {"modified": sorted(k for k in mine if k in then and mine[k] != then[k]),
                                    "added": sorted(k for k in mine if k not in then), "removed": sorted(k for k in then if k not in mine)}
    out["usable"] = out["schema"] == "ok" and out["code"] == "exact"
    return out


def _refuse(path, c):
    fmt = c["format"]
    if c["schema"] == "unsupported":
        if not isinstance(fmt, int) or fmt < min(SUPPORTED_FORMATS):
            raise UnsupportedFormat(
                f"{path} has save schema {fmt}, which this code (reads {c['supported_formats']}) does not read. Schemas 1 and 2 were "
                "development builds and never held a league of record: recreate the league from its seed, or open the file with the "
                "checkout that wrote it. There is no automatic upgrade for it.")
        raise UnsupportedFormat(f"{path} has save schema {fmt}, newer than this code reads ({c['supported_formats']}); use newer code.")
    ch = c["changed_files"]
    where = ""
    if ch:
        names = ch["modified"] + ["+" + n for n in ch["added"]] + ["-" + n for n in ch["removed"]]
        where = f" {len(names)} file(s) differ: {', '.join(names[:6])}{' ...' if len(names) > 6 else ''}."
    raise CodeMismatch(
        f"{path} was written by code {str(c['file_fingerprint'])[:12]}, this is {str(c['current_fingerprint'])[:12]}.{where} The schema "
        "is readable but replaying or continuing it under different code would not reproduce what the original code would have done. "
        "'status' and 'verify' still work. To continue the league under this code: league_store.py upgrade OLD NEW --run-originating-code "
        "(the original file is kept untouched). To read the old file only: league_store.py extract-code OLD DIR.")


class Runner:
    """A league whose every event is durable. Use `Runner.create(path, tables, ...)` for a new file and `Runner.open(path)` to resume.

    snapshot_every   write a full snapshot after every Nth event (1 = every event). Between snapshots recovery replays the journal.
    keep             how many of the newest ordinary snapshots to keep (the one before the latest is the fallback if the latest is damaged).
    Year-end snapshots are checkpoints and are kept for ever.
    archive          True (the default) to keep the permanent game and trace archive beside the file; see archive.py.
    crash_hook       test seam: called with a point name inside the commit; raising there is a crash.
    """

    allow_archive_ahead = False

    def __init__(self, path, db, universe, seq, chain, meta, crash_hook=None, archive=None):
        self.path, self.db, self.u, self.seq, self.chain, self.meta, self.crash_hook, self.archive = (
            path, db, universe, seq, chain, meta, crash_hook, archive)

    # ---- creation and opening
    @classmethod
    def create(cls, path, tables, seed=1, snapshot_every=1, keep=3, crash_hook=None, archive=True, archive_path=None, **universe_args):
        if os.path.exists(path) and os.path.getsize(path):
            raise PersistError(f"{path} already exists")
        u = Universe(tables, seed=seed, archive=archive, **universe_args)
        meta = {"seed": seed, "snapshot_every": snapshot_every, "keep": keep, "universe_args": universe_args, "tables": _tables_ref(tables),
                "archive": None, "league_id": uuid.uuid4().hex}
        if archive:
            apath = archive_path or path + ".archive"
            meta["archive"] = {"file": _archive_ref(path, apath)}
        return cls._new_file(path, u, meta, 0, GENESIS, how="create", crash_hook=crash_hook)

    @classmethod
    def _new_file(cls, path, u, meta, base_seq, base_chain, how, lineage=(), crash_hook=None):
        from . import archive as A
        manifest = code_manifest()
        fp = fingerprint(manifest=manifest)
        meta = dict(meta, format=FORMAT_VERSION, fingerprint=fp, base={"seq": base_seq, "chain": base_chain}, lineage=list(lineage),
                    created_with={"how": how, "format": FORMAT_VERSION, "fingerprint": fp, "rule_version": rule_version()})
        db = connect(path)
        db.executescript(SCHEMA)
        raw, sha = dumps(u)
        arch = None
        if meta.get("archive"):
            apath = _resolve_archive(path, meta["archive"]["file"])
            try:
                arch = A.Archive.open_or_create(apath, {"fingerprint": fp, "how": how, "seq": base_seq, "league_id": meta["league_id"]})
                meta["archive_base"] = {"through": base_seq, "chain": _archive_chain(A.batch_table(apath), base_seq)}
            except BaseException:
                db.close()                                    # an archive that is not this league's leaves no half-made league file
                for ext in ("", "-wal", "-shm"):
                    if os.path.exists(path + ext):
                        os.remove(path + ext)
                raise
        db.execute("BEGIN IMMEDIATE")
        try:
            for k, v in meta.items():
                db.execute("INSERT INTO meta VALUES (?,?)", (k, json.dumps(v, sort_keys=True)))
            db.execute("INSERT INTO code VALUES (?,?,?)", (fp, json.dumps(manifest, sort_keys=True), code_zip(manifest=manifest)))
            db.execute("INSERT INTO snapshots VALUES (?,?,?,1,?)", (base_seq, sha, len(raw), raw))
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
        return cls(path, db, u, base_seq, base_chain, meta, crash_hook, arch)

    @classmethod
    def open(cls, path, allow_code_change=False, crash_hook=None, heal=True, with_archive=True, repair_archive=False,
             allow_archive_ahead=False):
        """Open for use. Refuses a save schema this code does not read, and (unless `allow_code_change`, for forensic reading only)
        a file written by different code. The refusal says which and why. `with_archive=False` opens the league alone.

        The archive beside the league must hold every record the journal says was committed, and nothing different (`ArchiveInconsistent`
        otherwise: missing, mismatched, another league's, or ahead by more than the one event an interrupted commit leaves).
        `repair_archive` rebuilds missing events by replaying from the newest snapshot before them; each rebuilt batch must equal the
        count and digest the journal committed. `allow_archive_ahead` accepts an archive that is further ahead (an older league file
        restored beside a newer archive of the same league); the extra events are confirmed as they are replayed."""
        if not os.path.exists(path):
            raise PersistError(f"{path} does not exist")
        c = compatibility(path)
        if c["schema"] != "ok" or (c["code"] != "exact" and not allow_code_change):
            _refuse(path, c)
        db = connect(path)
        meta = read_meta(db)
        head, chain = cls._head(db, meta)
        start = head                                          # newest event a snapshot may be taken at
        if meta.get("archive") and with_archive:
            chk = archive_check(path)
            _judge_archive(path, chk, head, repair_archive, allow_archive_ahead)
            if chk["missing"]:
                start = min(head, chk["missing"][0] - 1)      # replay must pass through the first missing event to rebuild it
        u, seq = None, None
        for sseq, sha, raw in db.execute("SELECT seq, sha256, payload FROM snapshots WHERE seq <= ? ORDER BY seq DESC", (start,)):
            if hashlib.sha256(raw).hexdigest() != sha:
                continue                                      # damaged: fall back to an older snapshot and replay further
            try:
                u, seq = loads(raw), sseq
            except Exception:
                continue
            break
        if u is None:
            raise Unrecoverable("no snapshot in the file passes its checksum")
        u.rebind(_tables_path(meta["tables"]))
        arch = None
        if meta.get("archive") and with_archive:
            from . import archive as A
            arch = A.Archive.open(_resolve_archive(path, meta["archive"]["file"]))
        r = cls(path, db, u, seq, None, meta, crash_hook, arch)
        r.allow_archive_ahead = allow_archive_ahead
        r._replay(head, repair_archive)
        r.chain = chain
        if heal and r.seq != seq:
            r._write_snapshot(r.seq)                          # next recovery starts here, not at the old snapshot
        return r

    @staticmethod
    def _head(db, meta=None):
        meta = meta or read_meta(db)
        row = db.execute("SELECT seq, chain FROM journal ORDER BY seq DESC LIMIT 1").fetchone()
        if row:
            return row[0], row[1]
        b = meta.get("base", {"seq": 0, "chain": GENESIS})
        return b["seq"], b["chain"]

    def _replay(self, head, repair_archive=False):
        rows = self.db.execute("SELECT seq, year, n_fired, key, digest, n_records, records_digest FROM journal "
                               "WHERE seq > ? AND seq <= ? ORDER BY seq", (self.seq, head)).fetchall()
        for seq, year, n_fired, key, digest, n_rec, rec_digest in rows:
            at = self.u.cursor
            fired = self.u.step_event()
            if fired != key or quick_digest(self.u) != digest:
                raise ReplayDivergence(f"replaying event {seq} ({key}) did not reproduce the journal (cursor was {at})")
            records = self.u.take_archive()
            if self.archive is not None:
                pb = self.archive.prepare(records) if records else None
                got = (pb.n, pb.digest) if pb else (0, "")
                if got != (n_rec, rec_digest):
                    raise ReplayDivergence(f"replaying event {seq} ({key}) produced archive records other than the journal committed")
                if pb:
                    self.archive.confirm_batch(seq, pb, repair=repair_archive)
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
        records = self.u.take_archive()
        due = seq % int(self.meta["snapshot_every"]) == 0 or self.u.cursor is None
        raw = sha = None
        if due:
            raw, sha = dumps(self.u)
        year, n_fired = self.u.fired[-1][0], len(self.u.fired)
        pb = None
        if records:
            if self.archive is None:
                raise PersistError("this runner was opened without its archive, so it cannot commit events that produce history")
            pb = self.archive.prepare(records)
        n_rec, rec_digest = (pb.n, pb.digest) if pb else (0, "")
        chain = _chain(self.chain, seq, key, digest, n_rec, rec_digest)
        self._hook("event_done")
        if pb:
            self.archive.put_batch(seq, pb)                   # idempotent; durable before the league file moves on
        self._hook("archive_written")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            head, head_chain = self._head(self.db, self.meta)
            if head != self.seq or head_chain != self.chain:
                raise ConcurrentWriter(f"the file is at event {head}, this runner is at {self.seq}")
            self.db.execute("INSERT INTO journal VALUES (?,?,?,?,?,?,?,?)", (seq, year, n_fired, key, digest, chain, n_rec, rec_digest))
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
        fresh = Runner.open(self.path, allow_code_change=True, crash_hook=self.crash_hook, heal=False,
                            allow_archive_ahead=self.allow_archive_ahead)
        self.db.close()
        if self.archive is not None:
            self.archive.close()
        self.db, self.u, self.seq, self.chain, self.archive = fresh.db, fresh.u, fresh.seq, fresh.chain, fresh.archive

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
        if self.archive is not None:
            self.archive.close()

    # ---- maintenance
    def backup(self, dest):
        return backup(self.path, dest, source_db=self.db)

    def status(self):
        return status(self.path, self.db)

    # ---- moving a league to new code
    @classmethod
    def import_state(cls, doc_path, dest, accept_code_change=False, accept_state_change=False, tables=None, archive_path=None,
                     source_file_sha256=None, crash_hook=None):
        """Build a new league file from a state document (neutral.py). The state is rebuilt and compared by value with the digest taken
        at export: a difference is `StateMismatch` unless `accept_state_change` says it is intended (the code changed the shape of the
        state on purpose). A document from other code is refused unless `accept_code_change`. The new file continues the document's
        sequence numbers and hash chain and records where it came from in its lineage."""
        if os.path.exists(dest) and os.path.getsize(dest):
            raise PersistError(f"{dest} already exists")
        with open(doc_path, "rb") as f:
            raw = f.read()
        doc = neutral.loads_doc(raw)
        m = doc["manifest"]
        mine = fingerprint()
        code_changed = m["fingerprint"] != mine
        if code_changed and not accept_code_change:
            raise CodeMismatch(f"the state was exported from code {str(m['fingerprint'])[:12]} and this is {mine[:12]}; importing starts a new "
                               "code epoch for the league, which must be asked for explicitly (accept_code_change)")
        u = neutral.decode(doc["state"])
        u.rebind(tables or _tables_path(m["tables"]))
        d = neutral.deep_digest(u)
        state_changed = d != m["deep_digest"]
        if state_changed and not accept_state_change:
            rep = mismatch_report(doc, u, d, source=doc_path)
            where = f"; report: {rep['written_to']}" if rep.get("written_to") else ""
            note = "; the digest itself is unstable (identical input digested differently)" if rep["digest_is_unstable"] else ""
            err = StateMismatch("the imported league differs from the exported one (state digest "
                                f"{d[:12]} against {m['deep_digest'][:12]}); nothing was written{note}{where}")
            err.diagnostics = rep
            raise err
        lineage = list(m.get("lineage") or []) + [{
            "from_fingerprint": m["fingerprint"], "from_format": m["league_format"], "from_rule_version": m.get("rule_version"),
            "from_seq": m["seq"], "from_chain": m["chain"], "source_file_sha256": source_file_sha256,
            "state_document_sha256": neutral.state_digest(raw), "state_digest": m["deep_digest"],
            "code_changed": code_changed, "state_changed": state_changed}]
        meta = {k: m[k] for k in ("seed", "snapshot_every", "keep", "universe_args", "tables", "league_id")}
        meta["archive"] = None
        if m.get("archive"):
            from . import archive as A
            a = archive_path or _resolve_archive(doc_path, m["archive"]["file"])
            if not os.path.exists(a):
                raise PersistError("the league has an archive but it was not found; pass archive_path (the archive is permanent history "
                                   "and is never recreated empty)")
            if A.league_id(a) != m["league_id"]:
                raise ArchiveInconsistent(f"the archive {a} belongs to another league", ["foreign"])
            if _archive_chain(A.batch_table(a), m["seq"]) != m["archive_chain"]:
                raise ArchiveInconsistent(f"the archive {a} does not hold the records the exported league committed through event "
                                          f"{m['seq']} (missing or different)", ["missing"])
            meta["archive"] = {"file": _archive_ref(dest, a)}
        return cls._new_file(dest, u, meta, m["seq"], m["chain"], how="import", lineage=lineage, crash_hook=crash_hook)


def status(path, db=None):
    own = db is None
    db = db or connect(path, readonly=True)
    try:
        meta = read_meta(db)
        head, chain = Runner._head(db, meta)
        snaps = [(s, c, n) for s, c, n in db.execute("SELECT seq, checkpoint, size FROM snapshots ORDER BY seq")]
        last = db.execute("SELECT year, n_fired, key FROM journal ORDER BY seq DESC LIMIT 1").fetchone()
        return {"league_id": meta.get("league_id"), "events": head, "last": last, "chain": chain, "snapshots": snaps, "bytes": os.path.getsize(path),
                "format": meta.get("format"), "base": meta.get("base"), "lineage": len(meta.get("lineage", [])),
                "fingerprint": meta.get("fingerprint"), "archive": meta.get("archive")}
    finally:
        if own:
            db.close()


# ---------------------------------------------------------------------------------------------------------------- verify, backup, restore
def verify(path, deep=False, archive=False):
    """-> list of problems (empty means the file is sound). Checks SQLite integrity, journal continuity and hash chain from the file's
    base, snapshot checksums, the recorded code and, with deep=True, that the file recovers (under the current code). With
    archive=True it also checks the archive beside the league against the journal (see archive_check; the archive's own contents are
    checked by archive.verify)."""
    problems = []
    db = connect(path, readonly=True)
    try:
        res = db.execute("PRAGMA integrity_check").fetchall()
        if res != [("ok",)]:
            problems.append(f"sqlite integrity check: {res[:3]}")
        meta = read_meta(db)
        base = meta.get("base", {"seq": 0, "chain": GENESIS})
        prev, expect = base["chain"], base["seq"] + 1
        for seq, key, digest, chain, n_rec, rec_digest in db.execute(
                "SELECT seq, key, digest, chain, n_records, records_digest FROM journal ORDER BY seq"):
            if seq != expect:
                problems.append(f"journal gap: expected event {expect}, found {seq}")
                expect = seq
            if chain != _chain(prev, seq, key, digest, n_rec, rec_digest):
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
        for fp, manifest, z in db.execute("SELECT fingerprint, manifest, zip FROM code"):
            if fingerprint(manifest=json.loads(manifest)) != fp:
                problems.append(f"code record {fp[:12]} does not match its manifest")
            try:
                names = set(zipfile.ZipFile(io.BytesIO(z)).namelist())
            except zipfile.BadZipFile:
                problems.append(f"code record {fp[:12]} is not a readable zip")
                continue
            if names != set(json.loads(manifest)):
                problems.append(f"code record {fp[:12]} does not hold the files its manifest lists")
        if meta.get("fingerprint") and not db.execute("SELECT 1 FROM code WHERE fingerprint = ?", (meta["fingerprint"],)).fetchone():
            problems.append("the file's own code version is not recorded in it")
    finally:
        db.close()
    if archive and not problems:
        try:
            _judge_archive(path, archive_check(path), None, False, False)
        except ArchiveInconsistent as e:
            problems.append(str(e))
    if deep and not problems:
        try:
            Runner.open(path, heal=False, with_archive=False).close()
        except PersistError as e:
            problems.append(f"recovery failed: {e}")
    return problems


def backup(src, dest, source_db=None):
    """Consistent online copy of a league file (safe while a runner is using it), verified before it is reported done."""
    if os.path.exists(dest):
        raise PersistError(f"{dest} already exists")
    own = source_db is None
    sdb = source_db or connect(src, readonly=True)
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


def restore(backup_path, dest, allow_code_change=False, archive_path=None, open_after=True, allow_archive_ahead=False):
    """Make `dest` a live league file from a verified backup. Never overwrites an existing file. `archive_path` points the restored
    league at a restored archive. Returns the opened Runner (opening proves it recovers) unless open_after is False."""
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
    if archive_path is not None:
        db = connect(dest)
        try:
            if db.execute("SELECT 1 FROM meta WHERE key = 'archive'").fetchone():
                db.execute("UPDATE meta SET value = ? WHERE key = 'archive'",
                           (json.dumps({"file": _archive_ref(dest, archive_path)}, sort_keys=True),))
        finally:
            db.close()
    return Runner.open(dest, allow_code_change=allow_code_change, allow_archive_ahead=allow_archive_ahead) if open_after else None


def backup_set(league_path, dest_dir):
    """Back up a league file and its archive together into `dest_dir` (league first, then archive: the archive is never behind the
    league, so the pair is always consistent). Returns {"league": ..., "archive": ... or None}."""
    from . import archive as A
    os.makedirs(dest_dir, exist_ok=True)
    db = connect(league_path, readonly=True)
    try:
        meta = read_meta(db)
    finally:
        db.close()
    out = {"league": backup(league_path, os.path.join(dest_dir, "league.db")), "archive": None}
    if meta.get("archive"):
        out["archive"] = A.backup(_resolve_archive(league_path, meta["archive"]["file"]), os.path.join(dest_dir, "archive.db"))
    return out


def restore_set(backup_dir, dest_dir, allow_code_change=False):
    """Restore a backed-up league and archive into `dest_dir` (which must not hold either file). Returns the opened Runner."""
    from . import archive as A
    os.makedirs(dest_dir, exist_ok=True)
    lg, ar = os.path.join(dest_dir, "league.db"), os.path.join(dest_dir, "archive.db")
    src_archive = os.path.join(backup_dir, "archive.db")
    if os.path.exists(lg) or os.path.exists(ar):
        raise PersistError(f"{dest_dir} already holds a league or an archive; restore never overwrites")
    if os.path.exists(src_archive):
        problems = A.verify(src_archive)
        if problems:
            raise Corrupt("the archive backup does not verify: " + "; ".join(problems))
        A.restore(src_archive, ar)
    return restore(os.path.join(backup_dir, "league.db"), lg, allow_code_change=allow_code_change,
                   archive_path=ar if os.path.exists(ar) else None, allow_archive_ahead=True)


# ---------------------------------------------------------------------------------------------------------------- upgrade
def extract_code(path, dest, fingerprint_=None):
    """Write the source and data a league file recorded (by default the version that may continue it) into `dest`, ready to run:
    `dest/src/dfl`, `dest/data`. Returns the fingerprint."""
    db = connect(path, readonly=True)
    try:
        meta = read_meta(db)
        fp = fingerprint_ or meta["fingerprint"]
        row = db.execute("SELECT manifest, zip FROM code WHERE fingerprint = ?", (fp,)).fetchone()
    finally:
        db.close()
    if not row:
        raise PersistError(f"the file holds no record of code version {str(fp)[:12]}")
    manifest = json.loads(row[0])
    root = os.path.abspath(dest)
    with zipfile.ZipFile(io.BytesIO(row[1])) as z:
        for name in z.namelist():
            target = os.path.abspath(os.path.join(root, name))
            if os.path.commonpath([root, target]) != root:
                raise Corrupt(f"unsafe path in code record: {name}")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as fh:
                fh.write(z.read(name))
    if fingerprint(root, manifest=code_manifest(root)) != fp:
        raise Corrupt("the extracted code does not reproduce its fingerprint")
    return fp


# Run under the originating code. It touches only what any format-3 version provides: Runner.open(path, heal=False, with_archive=False),
# .u, .meta, .seq, .chain. The current neutral.py is loaded by file path so the document is written in the current document format.
_WORKER = r'''
import sys, importlib.util
code_dir, db_copy, neutral_path, out = sys.argv[1:5]
sys.path.insert(0, code_dir + "/src")
spec = importlib.util.spec_from_file_location("dfl_neutral_current", neutral_path)
N = importlib.util.module_from_spec(spec)
sys.modules["dfl_neutral_current"] = N
spec.loader.exec_module(N)
from dfl import persist as P
r = P.Runner.open(db_copy, heal=False, with_archive=False)
with open(out, "wb") as f:
    f.write(N.dumps_doc(r.u, N.runner_manifest(r)))
r.close()
'''


def backup_to(src, dest):
    """Plain SQLite copy without the verification pass (used on temporary working copies)."""
    sdb = connect(src, readonly=True)
    out = sqlite3.connect(dest)
    try:
        sdb.backup(out)
    finally:
        out.close()
        sdb.close()


def export_state(path, out_path):
    """Write the league in the file as a neutral state document (neutral.py), under the code that wrote it (this code, so the file must
    be usable). The file is not modified. Export alone is not a migration: see `upgrade`. Returns the document's SHA-256."""
    if os.path.exists(out_path):
        raise PersistError(f"{out_path} already exists")
    c = compatibility(path)
    if not c["usable"]:
        _refuse(path, c)
    work = tempfile.mkdtemp(prefix="dflexport")
    try:
        copy = os.path.join(work, "copy.db")
        backup_to(path, copy)
        r = Runner.open(copy, heal=False, with_archive=False)
        raw = neutral.dumps_doc(r.u, neutral.runner_manifest(r))
        r.close()
        with open(out_path, "wb") as f:
            f.write(raw)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return neutral.state_digest(raw)


def _file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def upgrade(old, new, run_originating_code=False, accept_state_change=False, archive_path=None):
    """Continue the league in `old` under the current code, as a new file `new`. The old file is not modified (its bytes are hashed
    before and after, and all work is done on a copy). Same code: export and import in this process. Different code: the file's recorded
    code is extracted and the export is run under it in a separate process, which executes code from the file, so it must be asked for
    (`run_originating_code`). Either way the import checks the rebuilt state by value against the export. Returns a report."""
    c = compatibility(old)
    if c["schema"] != "ok":
        _refuse(old, c)
    if c["code"] != "exact" and not run_originating_code:
        _refuse(old, c)
    before = _file_sha256(old)
    work = tempfile.mkdtemp(prefix="dflupgrade")
    try:
        copy, doc = os.path.join(work, "copy.db"), os.path.join(work, "state.gz")
        backup_to(old, copy)
        if c["code"] == "exact":
            r = Runner.open(copy, heal=False, with_archive=False)
            with open(doc, "wb") as f:
                f.write(neutral.dumps_doc(r.u, neutral.runner_manifest(r)))
            r.close()
        else:
            code_dir = os.path.join(work, "code")
            extract_code(copy, code_dir)
            res = subprocess.run([sys.executable, "-I", "-c", _WORKER, code_dir, copy, neutral.__file__, doc], capture_output=True, text=True)
            if res.returncode:
                raise PersistError("exporting under the originating code failed: " + res.stderr[-800:])
        db = connect(old, readonly=True)
        try:
            meta = read_meta(db)
        finally:
            db.close()
        arch = archive_path or (_resolve_archive(old, meta["archive"]["file"]) if meta.get("archive") else None)
        if meta.get("archive"):
            _judge_archive(old, archive_check(old, arch), None, False, False)         # the old pair must be consistent before it is carried on
        r = Runner.import_state(doc, new, accept_code_change=True, accept_state_change=accept_state_change, archive_path=arch,
                                source_file_sha256=before)
        report = {"from_fingerprint": c["file_fingerprint"], "to_fingerprint": r.meta["fingerprint"], "events": r.seq, "chain": r.chain,
                  "code_changed": c["code"] != "exact", "lineage": r.meta["lineage"][-1]}
        r.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if _file_sha256(old) != before:
        raise Corrupt("the original league file changed during the upgrade")
    report["original_sha256"] = before
    return report
