#!/usr/bin/env python3
"""Consistent backup, verification and restore of the broadcast's PUBLIC folder (and, with --profile store, of the private broadcast store). docs/PUBLIC_BACKUP.md.

  public_backup.py create  --src DIR --out BACKUP [--profile public|store] [--max-passes 8] [--require-quiescent] [--no-fsync] [--discard-partial]
  public_backup.py verify  BACKUP [--no-closure] [--scratch DIR]   every file against the manifest (size, SHA-256), no extras, then the reference closure
  public_backup.py restore BACKUP DEST [--scratch DIR]        verify, then restore into a NEW (absent or empty) directory, then verify DEST; explicit modes 0755/0644
  public_backup.py check   --src DIR [--profile ...]          read-only reference-closure check of a live folder (no backup made)
  public_backup.py quarantine BACKUP.partial [--to DIR]       move an unfinished folder aside, whole (a rename; nothing is deleted), so the backup can be retried

Exit 0 only when every check passed. Read-only on --src; the daemon and the builder are not stopped, paused or signalled.

Bounded inventory and manifest memory. No list or dict of every file is held: the inventory (what is on disk, what has been copied, what the manifest says) lives in a temporary
SQLite index read and written in small batches, and the manifest is written and read as a stream. That part of the memory no longer grows with the number of files (measured flat at
31 to 34 MB from 29,000 to 472,000 synthetic files). It is NOT a bound on the whole tool: the reference closure still loads whole the JSON index documents it checks (playback,
replay-library and Honors indexes, one release manifest at a time), so memory still grows with the size of those documents, i.e. with the number of games and events (measured in
docs/PUBLIC_BACKUP.md section 3a). The price of the bounded part is TEMPORARY DISK: the index (reported as `index_bytes`). `create` keeps its index inside BACKUP.partial and removes it
just before the commit; `verify` and `restore` keep theirs in --scratch (default: the system temporary folder). tools/backup_space.py estimates that disk beforehand and refuses a
scratch or destination in RAM (tmpfs); the create/verify/restore commands run that preflight unless --no-preflight (an estimate, not a reservation: a full disk during the run is
handled safely, see docs section 3b). The manifest format and its digest are unchanged, so every backup made before this change still verifies and restores.

One operation per name. create, restore and quarantine take an operating-system lock on `.NAME.lock` beside BACKUP (or DEST, or BACKUP.partial's BACKUP) without waiting: a second
attempt is refused ("in progress") and touches nothing, so a running backup's .partial can neither be written, discarded (--discard-partial) nor quarantined by another process. The
lock is released by the system when its holder ends, however it ends (even kill -9), and the lock file itself is never removed (removing it would reopen the race).

Why it is consistent without stopping the daemon. Published replay, release and live-segment files are immutable and the daemon replaces every other file with an atomic rename,
so any single read returns one whole version. What can go wrong is a MIX: an index copied after a file it names was not yet copied, or the other way round. So:
  1. files are copied in passes, the cumulative control files first (latest.json, the three indexes: they only grow) then every other control file, then the rest; a file is
     copied again in a later pass only if its (inode, size, mtime) changed; a pass that finds the cumulative controls unchanged from its start to its end is QUIET and ends the
     copy (a snapshot of one publication state). If the daemon keeps publishing, the passes continue (each only copies what changed) up to --max-passes;
  2. the finished copy must satisfy the REFERENCE CLOSURE: latest -> its release and every file that release's manifest lists (size and SHA-256); every playback-index entry ->
     its file with the entry's SHA-256 (and the name's 10-hex tag); every replay-library entry -> its file; every Honors-index part -> its file; every live manifest -> head,
     all its segments, and the end file when final. A copy that does not satisfy it is never committed;
  3. if the passes ended without a quiet pass, the backup is still committed only if the closure holds and then says `"quiescent": false` (a closure-valid snapshot assembled from
     more than one publication state: every index entry has its file and no file is newer than anything that depends on it); --require-quiescent refuses such a backup.
Commit protocol: everything is written under BACKUP.partial; the manifest (with its own digest) is written last, then BACKUP.partial is renamed to BACKUP. A killed or failed
backup leaves only BACKUP.partial, which `verify` and `restore` refuse ("incomplete"); a backup directory without a valid manifest is never reported valid.
"""
import argparse
import codecs
import contextlib
import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import time

FORMAT, VERSION = "dfl-public-backup", 1
MANIFEST = "BACKUP_MANIFEST.json"
FILES = "files"
INDEX = "index.sqlite"                       # the working index of an unfinished backup (removed before the commit)
CHUNK = 1 << 20
READ_CHUNK = 1 << 20                         # how much of the manifest is read at a time (tests make it tiny)
BATCH = 2000                                 # rows per database batch
CACHE_KB = 8192                              # SQLite page cache
MAX_VALUE = 8 << 20                         # no single manifest value (one file entry, the snapshot) can be larger than this
KEEP = 50                                    # problems kept and reported; the rest are only counted

# the cumulative control files (they only grow), in the order they are copied first, per profile
CUMULATIVE = {
    "public": ["latest.json", "playback/index.json", "broadcast/replays/index.json", "broadcast/honors/index.json"],
    "store": ["builds.json", "carry.json", "feed/latest.json"],
}
# the other control files (small, replaced by atomic rename): copied before the bulk
CONTROL = {
    "public": ["status.json", "broadcast/clock.json", "broadcast/schedule.json"],
    "store": ["plan.json", "clock.json"],
}
LIVE_MANIFEST = re.compile(r"^broadcast/live/[^/]+/manifest\.json$")
SEASON_BUILDS = re.compile(r"^seasons/s\d{3}/(plan|builds)\.json$")
TMP = re.compile(r"(^|/)\.tmp-[^/]*$|\.tmp$")


class Failed(Exception):
    pass


class Busy(Failed):
    """Another operation holds the lock of this name."""


class Problems(list):
    """The first KEEP problems found, and `total`: how many there were. A damaged backup of any size reports in bounded memory."""

    def __init__(self, keep=KEEP):
        super().__init__()
        self.total, self.keep = 0, keep

    def append(self, x):
        self.total += 1
        if len(self) < self.keep:
            super().append(x)


def _slurp(path):
    with open(path, "rb") as f:
        return f.read()


def _jload(path):
    return json.loads(_slurp(path))


def sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def fsync_dir(path):
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


# ------------------------------------------------------------------------------------------------------------------------ one operation per name
def lock_path(path):
    """`.NAME.lock` beside NAME, where NAME is the backup (or restore destination) with any `.partial` / `.restoring` suffix removed."""
    path = os.path.abspath(path)
    for suffix in (".partial", ".restoring"):
        if path.endswith(suffix):
            path = path[:-len(suffix)]
    d, n = os.path.split(path)
    return os.path.join(d, "." + n + ".lock")


@contextlib.contextmanager
def name_lock(path, what):
    """Hold the operating-system lock of this name, without waiting; raise Busy ("in progress") if another process, or another call in this one, holds it. Released by the
    system when the holder ends, however it ends. The lock file is left in place (removing it would let two holders each lock a different file of the same name)."""
    lp = lock_path(path)
    try:
        os.makedirs(os.path.dirname(lp), exist_ok=True)         # (a backup or restore has always created the folders above its name)
        fd = os.open(lp, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as e:
        raise Failed("cannot create the lock file %s (%s): nothing was touched" % (lp, e))
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            who = os.pread(fd, 300, 0).decode("utf-8", "replace").strip()
            raise Busy("another operation on %s is in progress (lock %s%s): nothing was touched" % (os.path.abspath(path), lp, ", held by " + who if who else ""))
        os.ftruncate(fd, 0)
        os.pwrite(fd, json.dumps({"who": what, "pid": os.getpid(), "since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, sort_keys=True).encode(), 0)
        yield
    finally:
        os.close(fd)


# ------------------------------------------------------------------------------------------------------------------------ the temporary index (SQLite)
SCHEMA = """
CREATE TABLE IF NOT EXISTS f (path TEXT PRIMARY KEY, size INTEGER NOT NULL, sha TEXT NOT NULL, class TEXT, ino INTEGER, ssize INTEGER, mtime INTEGER) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS cur (path TEXT PRIMARY KEY, prio INTEGER NOT NULL, ino INTEGER, size INTEGER, mtime INTEGER) WITHOUT ROWID;
"""


def connect(path):
    """A disposable index: no journal, no fsync, a small page cache. It is a working file, never a record; the manifest is the record."""
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("PRAGMA journal_mode=OFF")
    db.execute("PRAGMA synchronous=OFF")
    db.execute("PRAGMA temp_store=FILE")
    db.execute("PRAGMA cache_size=-%d" % CACHE_KB)
    db.executescript(SCHEMA)
    return db


def index_bytes(path):
    n = 0
    for suffix in ("", "-journal", "-wal", "-shm"):
        try:
            n += os.path.getsize(path + suffix)
        except OSError:
            pass
    return n


def remove_index(path):
    for suffix in ("", "-journal", "-wal", "-shm"):
        try:
            os.unlink(path + suffix)
        except FileNotFoundError:
            pass


def is_disk_full(e):
    """A write failed because the disk (or the quota, or the file-size limit) is exhausted."""
    if isinstance(e, sqlite3.OperationalError):
        return "full" in str(e).lower()
    return isinstance(e, OSError) and e.errno in (errno.ENOSPC, errno.EDQUOT, errno.EFBIG)


def _insert_many(db, sql, rows):
    try:
        db.executemany(sql, rows)
    except UnicodeEncodeError as e:
        raise Failed("a file name is not valid UTF-8 and cannot be recorded: %s" % e)


def iter_listing(src, links):
    """Yield (rel, st_ino, st_size, st_mtime_ns) of the regular files, temporaries excluded; symbolic links are collected into `links`, never followed."""
    for d, dirs, names in os.walk(src, followlinks=False):
        dirs[:] = sorted(x for x in dirs if not x.startswith(".tmp-"))
        for n in sorted(names):
            p = os.path.join(d, n)
            rel = os.path.relpath(p, src).replace(os.sep, "/")
            if TMP.search(rel):
                continue
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode):
                links.append(rel)
            elif stat.S_ISREG(st.st_mode):
                yield rel, st.st_ino, st.st_size, st.st_mtime_ns
        for dn in list(dirs):
            if os.path.islink(os.path.join(d, dn)):
                links.append(os.path.relpath(os.path.join(d, dn), src).replace(os.sep, "/"))
                dirs.remove(dn)


def _links_error(links):
    return Failed("symbolic links are not followed or copied: %s" % links[:3])


def listing(src):
    """-> {rel: (st_ino, st_size, st_mtime_ns)}. Small trees and tests only: it holds every file (the tools use the index instead)."""
    links = []
    out = {rel: (ino, size, mt) for rel, ino, size, mt in iter_listing(src, links)}
    if links:
        raise _links_error(links)
    return out


def _prio(profile, rel, first):
    """The copy order as a number: cumulative controls, other controls, live manifests (or season builds), then everything else, each group by name."""
    if rel in first:
        return first[rel]
    if (profile == "public" and LIVE_MANIFEST.match(rel)) or (profile == "store" and SEASON_BUILDS.match(rel)):
        return 1000
    return 2000


def _fill_cur(db, src, profile):
    first = {}
    for i, r in enumerate(CUMULATIVE[profile]):
        first[r] = i
    for i, r in enumerate(CONTROL[profile]):
        first[r] = 100 + i
    links = []
    db.execute("BEGIN")
    db.execute("DELETE FROM cur")
    batch = []
    for rel, ino, size, mt in iter_listing(src, links):
        batch.append((rel, _prio(profile, rel, first), ino, size, mt))
        if len(batch) >= BATCH:
            _insert_many(db, "INSERT INTO cur VALUES (?,?,?,?,?)", batch)
            batch = []
    _insert_many(db, "INSERT INTO cur VALUES (?,?,?,?,?)", batch)
    db.execute("COMMIT")
    if links:
        raise _links_error(links)


def _ordered(db):
    """The current listing in copy order, a batch at a time: (rel, (ino, size, mtime))."""
    for rel, ino, size, mt in db.execute("SELECT path, ino, size, mtime FROM cur WHERE prio < 1000 ORDER BY prio").fetchall():
        yield rel, (ino, size, mt)
    for prio in (1000, 2000):
        last = ""
        while True:
            rows = db.execute("SELECT path, ino, size, mtime FROM cur WHERE prio = ? AND path > ? ORDER BY path LIMIT ?", (prio, last, BATCH)).fetchall()
            for rel, ino, size, mt in rows:
                yield rel, (ino, size, mt)
            if len(rows) < BATCH:
                break
            last = rows[-1][0]


def _rows_by_path(db, sql, args=()):
    """Run a keyset-paged query `sql` (which must contain `path > ?` as its first parameter and end with LIMIT ?), yielding rows."""
    last = ""
    while True:
        rows = db.execute(sql, (last,) + tuple(args) + (BATCH,)).fetchall()
        for r in rows:
            yield r
        if len(rows) < BATCH:
            return
        last = rows[-1][0]


def order(profile, rels):
    cum = [c for c in CUMULATIVE[profile] if c in rels]
    ctl = [c for c in CONTROL[profile] if c in rels]
    extra = sorted(r for r in rels if (profile == "public" and LIVE_MANIFEST.match(r)) or (profile == "store" and SEASON_BUILDS.match(r)))
    first = set(cum) | set(ctl) | set(extra)
    # season builds files before the bulk too (they decide what the bulk must contain)
    return cum + ctl + extra + sorted(r for r in rels if r not in first)


def cum_state(src, profile):
    out = {}
    for rel in CUMULATIVE[profile]:
        p = os.path.join(src, rel)
        try:
            out[rel] = sha_file(p)
        except FileNotFoundError:
            out[rel] = None
    if profile == "store":                         # the per-season builds files are cumulative controls of the store too
        sd = os.path.join(src, "seasons")
        if os.path.isdir(sd):
            for n in sorted(os.listdir(sd)):
                for f in ("builds.json", "plan.json"):
                    p = os.path.join(sd, n, f)
                    if os.path.exists(p):
                        out["seasons/%s/%s" % (n, f)] = sha_file(p)
    return out


def class_of(profile, rel):
    if rel in CUMULATIVE[profile]:
        return "cumulative"
    if rel in CONTROL[profile] or (profile == "public" and LIVE_MANIFEST.match(rel)) or (profile == "store" and SEASON_BUILDS.match(rel)):
        return "control"
    return "content"


# ------------------------------------------------------------------------------------------------------------------------ the reference closure
class Tree:
    """Read access to a tree whose file sizes and SHA-256 are either known (the manifest) or computed on demand."""

    def __init__(self, base, known=None):
        self.base, self.known = base, known if known is not None else {}

    def exists(self, rel):
        return (rel in self.known) if self.known else os.path.isfile(os.path.join(self.base, rel))

    def size(self, rel):
        return self.known[rel][0] if rel in self.known else os.path.getsize(os.path.join(self.base, rel))

    def sha(self, rel):
        if rel in self.known:
            return self.known[rel][1]
        return sha_file(os.path.join(self.base, rel))

    def json(self, rel):
        with open(os.path.join(self.base, rel), "rb") as f:
            return json.loads(f.read())

    def rels(self, prefix):
        if self.known:
            return sorted(r for r in self.known if r.startswith(prefix))
        return self._walk(prefix)

    def _walk(self, prefix):
        for d, _, names in os.walk(os.path.join(self.base, prefix)):
            for n in names:
                yield os.path.relpath(os.path.join(d, n), self.base).replace(os.sep, "/")


class DbTree(Tree):
    """The same, with the sizes and SHA-256 of every file in the temporary index (table `f`): nothing about the files is held in memory."""

    def __init__(self, base, db):
        self.base, self.known, self.db = base, None, db

    def _row(self, rel):
        r = self.db.execute("SELECT size, sha FROM f WHERE path = ?", (rel,)).fetchone()
        if r is None:
            raise KeyError(rel)
        return r

    def exists(self, rel):
        return self.db.execute("SELECT 1 FROM f WHERE path = ?", (rel,)).fetchone() is not None

    def size(self, rel):
        return self._row(rel)[0]

    def sha(self, rel):
        return self._row(rel)[1]

    def rels(self, prefix):
        hi = prefix[:-1] + chr(ord(prefix[-1]) + 1)
        for (r,) in self.db.execute("SELECT path FROM f WHERE path >= ? AND path < ? ORDER BY path", (prefix, hi)):
            yield r


def _release_problems(t, rel_dir, out, pointer_sha=None):
    mp = rel_dir + "/manifest.json"
    if not t.exists(mp):
        out.append("release %s has no manifest" % rel_dir)
        return
    if pointer_sha and t.sha(mp) != pointer_sha:
        out.append("release %s: manifest SHA-256 differs from the pointer's" % rel_dir)
    try:
        m = t.json(mp)
    except (ValueError, OSError) as e:
        out.append("release %s: manifest unreadable (%s)" % (rel_dir, type(e).__name__))
        return
    files = m.get("files")
    if isinstance(files, dict):
        for name, rec in files.items():
            p = rel_dir + "/" + rec.get("path", name)
            if not t.exists(p):
                out.append("release %s: %s is missing" % (rel_dir, rec.get("path", name)))
            elif t.size(p) != rec.get("bytes", t.size(p)) or t.sha(p) != rec.get("sha256", t.sha(p)):
                out.append("release %s: %s differs from its manifest" % (rel_dir, rec.get("path", name)))


def closure_public(t):
    out = Problems()
    # latest -> release
    if t.exists("latest.json"):
        try:
            latest = t.json("latest.json")
            rel = latest["release"]
            _release_problems(t, "releases/" + rel, out, latest.get("manifest_sha256"))
        except (ValueError, KeyError, OSError) as e:
            out.append("latest.json unusable (%s)" % type(e).__name__)
    # every release is complete against its own manifest
    seen = {r.split("/")[1] for r in t.rels("releases/") if r.count("/") >= 2}
    for name in sorted(seen):
        _release_problems(t, "releases/" + name, out)
    # playback index
    idx = {}
    if t.exists("playback/index.json"):
        try:
            for e in t.json("playback/index.json")["games"]:
                p = "playback/" + e["file"]
                idx[e["file"].split("/")[-1]] = e
                if not t.exists(p):
                    out.append("playback index: %s is missing" % e["file"])
                    continue
                if t.sha(p) != e["sha256"] or t.size(p) != e.get("bytes", t.size(p)):
                    out.append("playback index: %s differs from its entry" % e["file"])
                if e["file"].split(".")[-2] != e["sha256"][:10]:
                    out.append("playback index: the name of %s does not carry its SHA-256" % e["file"])
        except (ValueError, KeyError, OSError) as e:
            out.append("playback/index.json unusable (%s)" % type(e).__name__)
    # replay library
    if t.exists("broadcast/replays/index.json"):
        try:
            gids = set()
            for g in t.json("broadcast/replays/index.json")["games"]:
                p = "broadcast/replays/" + g["replay"]
                if g["gid"] in gids:
                    out.append("replay library: %s listed twice" % g["gid"])
                gids.add(g["gid"])
                if not t.exists(p):
                    out.append("replay library: %s is missing" % g["replay"])
                elif g["replay"] in idx and t.sha(p) != idx[g["replay"]]["sha256"]:
                    out.append("replay library: %s differs from the published replay" % g["replay"])
                elif g["replay"] not in idx and t.exists("playback/games/" + g["replay"]) and t.sha(p) != t.sha("playback/games/" + g["replay"]):
                    out.append("replay library: %s differs from playback/games" % g["replay"])
        except (ValueError, KeyError, OSError) as e:
            out.append("broadcast/replays/index.json unusable (%s)" % type(e).__name__)
    # honors
    if t.exists("broadcast/honors/index.json"):
        try:
            for p in t.json("broadcast/honors/index.json")["parts"]:
                if not t.exists("broadcast/honors/" + p["file"]):
                    out.append("honors index: %s is missing" % p["file"])
        except (ValueError, KeyError, OSError) as e:
            out.append("broadcast/honors/index.json unusable (%s)" % type(e).__name__)
    # live recordings
    for rel in sorted(r for r in t.rels("broadcast/live/") if r.endswith("/manifest.json") and r.count("/") == 3):
        d = rel.rsplit("/", 1)[0]
        try:
            m = t.json(rel)
            if not t.exists(d + "/head.json"):
                out.append("%s: head.json is missing" % d)
            for i in range(int(m["segments"])):
                if not t.exists("%s/s%04d.json" % (d, i)):
                    out.append("%s: segment %d is missing" % (d, i))
                    break
            if m.get("state") == "final" and not t.exists(d + "/end.json"):
                out.append("%s: final but end.json is missing" % d)
        except (ValueError, KeyError, OSError) as e:
            out.append("%s unusable (%s)" % (rel, type(e).__name__))
    for rel in ("status.json", "broadcast/clock.json", "broadcast/schedule.json"):
        if t.exists(rel):
            try:
                t.json(rel)
            except (ValueError, OSError):
                out.append("%s is not valid JSON" % rel)
    return out


def closure_store(t):
    out = Problems()
    builds_files = ["builds.json"] + [r for r in t.rels("seasons/") if r.endswith("/builds.json")]
    for bf in builds_files:
        if not t.exists(bf):
            continue
        try:
            b = t.json(bf)
            for k, ev in b.get("events", {}).items():
                _release_problems(t, "feed/releases/" + ev["release"], out)
                if not t.exists(ev["pointer"]):
                    out.append("%s: pointer %s is missing" % (bf, ev["pointer"]))
            for gid, g in b.get("games", {}).items():
                d = "games/" + gid.replace("/", "-").replace(":", "-")
                for need in ("head.json", "end.json", "replay.json"):
                    if not t.exists(d + "/" + need):
                        out.append("%s: %s/%s is missing" % (bf, d, need))
                for i in range(int(g["segments"])):
                    if not t.exists("%s/s%04d.json" % (d, i)):
                        out.append("%s: %s segment %d is missing" % (bf, d, i))
                        break
                e = g["entry"]
                p = "playback/" + e["file"]
                if not t.exists(p) or t.sha(p) != e["sha256"]:
                    out.append("%s: private replay %s missing or different" % (bf, e["file"]))
            for part, rel in b.get("honors", {}).items():
                if not t.exists("honors/" + rel):
                    out.append("%s: honors %s is missing" % (bf, rel))
        except (ValueError, KeyError, OSError) as e:
            out.append("%s unusable (%s)" % (bf, type(e).__name__))
    if t.exists("carry.json"):
        try:
            c = t.json("carry.json")
            for f in ("plan.json", "builds.json"):
                if not t.exists("seasons/s%03d/%s" % (int(c["season"]), f)):
                    out.append("carry.json names season %s but seasons/s%03d/%s is missing" % (c["season"], int(c["season"]), f))
        except (ValueError, KeyError, OSError) as e:
            out.append("carry.json unusable (%s)" % type(e).__name__)
    if not t.exists("plan.json"):
        out.append("plan.json is missing")
    return out


CLOSURE = {"public": closure_public, "store": closure_store}


# ------------------------------------------------------------------------------------------------------------------------ the manifest, as a stream
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def safe_rel(p):
    return isinstance(p, str) and p != "" and "\0" not in p and not p.startswith("/") and all(x not in ("", ".", "..") for x in p.split("/"))


class _Stream:
    """Reads one JSON document piece by piece: whatever the whitespace or key order, one value at a time, with a bounded window (READ_CHUNK plus the largest single value)."""

    def __init__(self, path):
        self.f = open(path, "rb")
        self.dec = codecs.getincrementaldecoder("utf-8")()
        self.jd = json.JSONDecoder()
        self.buf, self.pos, self.eof = "", 0, False

    def close(self):
        self.f.close()

    def more(self):
        if self.eof:
            return False
        raw = self.f.read(READ_CHUNK)
        try:
            text = self.dec.decode(raw, final=not raw)
        except UnicodeDecodeError:
            raise Failed("not valid UTF-8")
        if not raw:
            self.eof = True
        self.buf = self.buf[self.pos:] + text
        self.pos = 0
        return bool(raw)

    def peek(self):
        while True:
            n = len(self.buf)
            while self.pos < n and self.buf[self.pos] in " \t\r\n":
                self.pos += 1
            if self.pos < n:
                return self.buf[self.pos]
            if not self.more():
                return ""

    def expect(self, ch):
        if self.peek() != ch:
            raise Failed("expected %r" % ch)
        self.pos += 1

    def value(self):
        self.peek()
        while True:
            try:
                v, e = self.jd.raw_decode(self.buf, self.pos)
            except ValueError:
                if len(self.buf) - self.pos > MAX_VALUE or not self.more():
                    raise Failed("not readable JSON")
                continue
            if e == len(self.buf) and self.more():           # a number may go on in the next piece
                continue
            self.pos = e
            return v

    def at_end(self):
        return self.peek() == ""


def _entry(e):
    if not (isinstance(e, dict) and safe_rel(e.get("path")) and isinstance(e.get("size"), int) and not isinstance(e.get("size"), bool) and e["size"] >= 0
            and isinstance(e.get("sha256"), str) and SHA_RE.match(e["sha256"])):
        raise Failed("a file entry of the manifest is malformed: %.120r" % (e,))
    return e


def read_manifest(mp, on_file):
    """Stream BACKUP_MANIFEST.json: call on_file(entry) for each file in order. Everything the format promises is checked (format, completeness, no bytes after the end, the digest
    over the file list, profile and snapshot). -> (meta, files, bytes): meta is every top-level key except `files`. Raises Failed."""
    r = _Stream(mp)
    meta, h, n, nbytes, seen = {}, hashlib.sha256(), 0, 0, set()
    try:
        r.expect("{")
        if r.peek() != "}":
            while True:
                key = r.value()
                if not isinstance(key, str) or key in seen:
                    raise Failed("repeated or malformed key")
                seen.add(key)
                r.expect(":")
                if key == "files":
                    r.expect("[")
                    h.update(b'{"files":[')
                    if r.peek() == "]":
                        r.pos += 1
                    else:
                        while True:
                            e = _entry(r.value())
                            h.update((b"," if n else b"") + canon(e))
                            on_file(e)
                            n += 1
                            nbytes += e["size"]
                            c = r.peek()
                            r.pos += 1
                            if c == ",":
                                continue
                            if c == "]":
                                break
                            raise Failed("truncated or malformed file list")
                else:
                    meta[key] = r.value()
                c = r.peek()
                r.pos += 1
                if c == ",":
                    continue
                if c == "}":
                    break
                raise Failed("truncated")
        else:
            r.pos += 1
        if not r.at_end():
            raise Failed("bytes after the end of the manifest")
    except Failed as e:
        raise Failed("%s is not readable JSON: incomplete or damaged, not valid (%s)" % (mp, e))
    finally:
        r.close()
    if meta.get("format") != FORMAT or meta.get("complete") is not True or "files" not in seen:
        raise Failed("%s is not a complete %s manifest: not valid" % (mp, FORMAT))
    if not isinstance(meta.get("profile"), str) or not isinstance(meta.get("snapshot"), dict):
        raise Failed("%s is not a complete %s manifest: not valid" % (mp, FORMAT))
    h.update(b'],"profile":' + canon(meta["profile"]) + b',"snapshot":' + canon({k: v for k, v in meta["snapshot"].items() if k in ("quiescent", "cumulative")}) + b"}")
    if meta.get("manifest_digest") != h.hexdigest():
        raise Failed("the manifest does not match its own digest: damaged or edited, not valid")
    return meta, n, nbytes


def write_manifest(db, tmp, profile, snap):
    """Write the manifest from the index, a batch at a time, in the layout of every earlier version (keys in sorted order), computing the digest as it goes.
    -> (digest, files, bytes)."""
    n, nbytes = db.execute("SELECT COUNT(*), COALESCE(SUM(size), 0) FROM f").fetchone()
    sub = {k: v for k, v in snap.items() if k in ("quiescent", "cumulative")}
    h = hashlib.sha256(b'{"files":[')
    with open(tmp, "wb") as out:
        out.write(b'{"closure": {"checked": true, "problems": 0},\n"complete": true,\n"files": [')
        first = True
        for rows in _batches(db, "SELECT path, size, sha, class FROM f WHERE path > ? ORDER BY path LIMIT ?"):
            for path, size, sha, cls in rows:
                e = {"path": path, "size": size, "sha256": sha, "class": cls}
                c = canon(e)
                h.update(c if first else b"," + c)
                out.write((b"\n" if first else b",\n") + json.dumps(e, sort_keys=True).encode())
                first = False
        h.update(b'],"profile":' + canon(profile) + b',"snapshot":' + canon(sub) + b"}")
        digest = h.hexdigest()
        out.write(b"\n],\n")
        out.write(("\"format\": %s,\n\"format_version\": %d,\n\"manifest_digest\": \"%s\",\n\"profile\": %s,\n\"snapshot\": %s,\n\"totals\": %s}" % (
            json.dumps(FORMAT), VERSION, digest, json.dumps(profile), json.dumps(snap, sort_keys=True), json.dumps({"bytes": nbytes, "files": n}, sort_keys=True))).encode())
        out.flush()
        os.fsync(out.fileno())
    return digest, n, nbytes


def _batches(db, sql):
    """Pages of rows, `path` first in each row; `sql` has `path > ?` then `LIMIT ?`."""
    last = ""
    while True:
        rows = db.execute(sql, (last, BATCH)).fetchall()
        if not rows:
            return
        yield rows
        last = rows[-1][0]


# ------------------------------------------------------------------------------------------------------------------------ create
def copy_file(src, dst, fsync):
    """Copy and hash in one read. Returns (sha256, size) of the bytes actually read."""
    h = hashlib.sha256()
    n = 0
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(src, "rb") as f, open(dst, "wb") as g:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
            n += len(chunk)
            g.write(chunk)
        g.flush()
        if fsync:
            os.fsync(g.fileno())
    return h.hexdigest(), n


def create(src, out, profile="public", max_passes=8, require_quiescent=False, fsync=True, discard_partial=False, hook=None, now=time.time, lock=True):
    """Make the backup. `hook(event, **info)` is for tests and fault injection (events: 'file', 'pass_end', 'before_commit'). `lock=False` only for a caller that already holds
    the lock of a larger operation (tools/recovery_set.py). Raises Busy when another operation holds this name."""
    src, out = os.path.abspath(src), os.path.abspath(out)
    if profile not in CLOSURE:
        raise Failed("unknown profile %r" % profile)
    if not os.path.isdir(src):
        raise Failed("%s is not a directory" % src)
    if os.path.commonpath([src, out]) in (src, out):
        raise Failed("the backup must not be inside the source (or the other way round)")
    with (name_lock(out, "public_backup create") if lock else contextlib.nullcontext()):
        try:
            return _create(src, out, profile, max_passes, require_quiescent, fsync, discard_partial, hook or (lambda *a, **k: None), now)
        except (OSError, sqlite3.OperationalError) as e:
            if not is_disk_full(e):
                raise
            raise Failed("the disk is full while writing %s (%s): nothing was committed. %s.partial is kept as evidence (what was written so far, its working index included), "
                         "already valid backups were not touched, and nothing was deleted. Free space or choose another destination, move the unfinished folder aside with "
                         "`public_backup.py quarantine %s.partial`, and retry." % (out, e, out, out))


def _create(src, out, profile, max_passes, require_quiescent, fsync, discard_partial, hook, now):
    if os.path.exists(out):
        raise Failed("%s already exists: a backup is never overwritten" % out)
    part = out + ".partial"
    if os.path.exists(part):
        if not discard_partial:
            raise Failed("%s exists (an earlier backup did not finish). Keep it as evidence with `public_backup.py quarantine %s` and retry; --discard-partial DELETES it" % (part, part))
        shutil.rmtree(part)               # safe: this name's lock is held, so no other operation is writing it
    os.makedirs(os.path.join(part, FILES))
    files_dir, ipath = os.path.join(part, FILES), os.path.join(part, INDEX)
    t0 = now()
    passes = []
    quiet = False
    db = connect(ipath)
    try:
        ncop = 0                                      # files copied so far
        for n in range(1, max_passes + 1):
            before = cum_state(src, profile)
            _fill_cur(db, src, profile)
            ncopy = 0
            for rel, sig in _ordered(db):
                row = db.execute("SELECT ino, ssize, mtime FROM f WHERE path = ?", (rel,)).fetchone()
                if row == sig:
                    continue
                hook("file", rel=rel, n=ncop, pass_no=n)
                try:
                    sha, size = copy_file(os.path.join(src, rel), os.path.join(files_dir, rel), fsync)
                except FileNotFoundError:             # removed by the daemon while we were here: the next pass sees the new listing
                    if row is not None:
                        db.execute("DELETE FROM f WHERE path = ?", (rel,))
                        ncop -= 1
                        try:
                            os.unlink(os.path.join(files_dir, rel))
                        except FileNotFoundError:
                            pass
                    continue
                db.execute("INSERT OR REPLACE INTO f VALUES (?,?,?,?,?,?,?)", (rel, size, sha, class_of(profile, rel), sig[0], sig[1], sig[2]))
                if row is None:
                    ncop += 1
                ncopy += 1
            after = cum_state(src, profile)
            _fill_cur(db, src, profile)
            while True:                               # deleted at the source (a reveal group with nothing due): the backup mirrors the final state
                gone = db.execute("SELECT path FROM f WHERE path NOT IN (SELECT path FROM cur) LIMIT ?", (BATCH,)).fetchall()
                if not gone:
                    break
                for (r,) in gone:
                    db.execute("DELETE FROM f WHERE path = ?", (r,))
                    ncop -= 1
                    try:
                        os.unlink(os.path.join(files_dir, r))
                    except FileNotFoundError:
                        pass
            # QUIET pass: the cumulative controls were the same at its start and end, and what we hold of them IS that state
            quiet = before == after
            for r, v in after.items():
                held = db.execute("SELECT sha FROM f WHERE path = ?", (r,)).fetchone()
                if not ((held is None and v is None) or (held is not None and held[0] == v)):
                    quiet = False
            passes.append({"pass": n, "copied": ncopy, "quiet": quiet})
            hook("pass_end", pass_no=n, copied=ncopy)
            if quiet:
                break
        if require_quiescent and not quiet:
            raise Failed("the folder kept publishing for %d passes: no quiet moment; nothing was committed" % max_passes)
        problems = CLOSURE[profile](DbTree(files_dir, db))
        if problems.total:
            raise Failed("the copy does not satisfy the reference closure (%d problems, first: %s); nothing was committed" % (problems.total, problems[0]))
        snap = {"quiescent": quiet, "passes": passes, "cumulative": after, "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0)),
                "finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now())), "seconds": round(now() - t0, 1)}
        for rel, key in (("latest.json", "latest"), ("status.json", "status"), ("broadcast/clock.json", "clock"), ("builds.json", "builds_summary")):
            if db.execute("SELECT 1 FROM f WHERE path = ?", (rel,)).fetchone():
                try:
                    doc = json.loads(_slurp(os.path.join(files_dir, rel)))
                    if rel == "builds.json":
                        doc = {"events_built": len(doc.get("events", {})), "games_built": len(doc.get("games", {}))}
                    snap[key] = doc
                except (ValueError, OSError):
                    pass
        hook("before_commit")
        mp = os.path.join(part, MANIFEST)
        digest, nfiles, nbytes = write_manifest(db, mp + ".tmp", profile, snap)
        os.replace(mp + ".tmp", mp)
        used = index_bytes(ipath)
        db.close()
        db = None
        remove_index(ipath)                             # a finished backup holds the manifest and the files, nothing else
        fsync_dir(part)
        os.replace(part, out)                           # the one atomic step: the backup exists under its name only when complete
        fsync_dir(os.path.dirname(out))
        return {"ok": True, "backup": out, "files": nfiles, "bytes": nbytes, "quiescent": quiet, "passes": len(passes), "seconds": snap["seconds"],
                "manifest_digest": digest, "profile": profile, "index_bytes": used}
    finally:
        # BACKUP.partial stays where it is when anything went wrong (evidence of an unfinished backup, refused by verify and restore, index included); never a half backup under the final name
        if db is not None:
            db.close()


# ------------------------------------------------------------------------------------------------------------------------ verify / restore
def _preflight(backup):
    if backup.rstrip("/").endswith(".partial"):
        raise Failed("%s is an unfinished backup (a .partial folder is never a valid backup, even if it holds a manifest): incomplete, not valid" % backup)
    if os.path.isdir(backup + ".partial") and not os.path.exists(backup):
        raise Failed("%s is an unfinished backup (only %s.partial exists): incomplete, not valid" % (backup, os.path.basename(backup)))
    mp = os.path.join(backup, MANIFEST)
    if not os.path.isdir(backup):
        raise Failed("%s is not a backup directory" % backup)
    if not os.path.isfile(mp):
        raise Failed("%s has no %s: incomplete, not valid" % (backup, MANIFEST))
    return mp


def load_manifest(backup):
    """Check the whole manifest (streamed: its file list is not kept) and return everything in it except the file list; `counted` has the number and bytes of the files it lists."""
    meta, n, nbytes = read_manifest(_preflight(backup), lambda e: None)
    meta["counted"] = {"files": n, "bytes": nbytes}
    return meta


@contextlib.contextmanager
def _manifest_index(backup, scratch):
    """The manifest loaded into a temporary index (table `f`), which is removed afterwards. Yields (db, meta, state): state["index_bytes"] is the peak size of the index."""
    mp = _preflight(backup)
    work = tempfile.mkdtemp(prefix="dfl-backup-index-", dir=scratch)
    state = {"index_bytes": 0, "scratch": work}
    ipath = os.path.join(work, INDEX)
    db = connect(ipath)
    try:
        batch = []

        def add(e):
            batch.append((e["path"], e["size"], e["sha256"], e.get("class")))
            if len(batch) >= BATCH:
                flush()

        def flush():
            db.execute("BEGIN")
            try:
                _insert_many(db, "INSERT INTO f (path, size, sha, class) VALUES (?,?,?,?)", batch)
            except sqlite3.IntegrityError:
                raise Failed("%s lists the same path twice: damaged, not valid" % mp)
            finally:
                db.execute("COMMIT")
            del batch[:]
        meta, n, nbytes = read_manifest(mp, add)
        flush()
        meta["counted"] = {"files": n, "bytes": nbytes}
        yield db, meta, state
    finally:
        state["index_bytes"] = max(state["index_bytes"], index_bytes(ipath))
        db.close()
        shutil.rmtree(work, ignore_errors=True)


def _compare(db, base):
    """The files under `base` against the manifest in the index: no extras, none missing, sizes, SHA-256. -> (Problems, bytes_verified)."""
    problems = Problems()
    links = []
    db.execute("DROP TABLE IF EXISTS have")
    db.execute("CREATE TABLE have (path TEXT PRIMARY KEY, size INTEGER) WITHOUT ROWID")
    if os.path.isdir(base):
        db.execute("BEGIN")
        batch = []
        for rel, ino, size, mt in iter_listing(base, links):
            batch.append((rel, size))
            if len(batch) >= BATCH:
                _insert_many(db, "INSERT INTO have VALUES (?,?)", batch)
                batch = []
        _insert_many(db, "INSERT INTO have VALUES (?,?)", batch)
        db.execute("COMMIT")
    if links:
        problems.append(str(_links_error(links)))
    extra = db.execute("SELECT COUNT(*) FROM have WHERE path NOT IN (SELECT path FROM f)").fetchone()[0]
    shown = 0
    for (rel,) in db.execute("SELECT path FROM have WHERE path NOT IN (SELECT path FROM f) ORDER BY path LIMIT ?", (KEEP,)):
        problems.append("extra file not in the manifest: %s" % rel)
        shown += 1
    problems.total += extra - shown
    nbytes = 0
    for rows in _batches(db, "SELECT f.path, f.size, f.sha, h.size FROM f LEFT JOIN have h ON h.path = f.path WHERE f.path > ? ORDER BY f.path LIMIT ?"):
        for rel, size, sha, have_size in rows:
            if have_size is None:
                problems.append("missing: %s" % rel)
            elif have_size != size:
                problems.append("size differs: %s (%d, manifest %d)" % (rel, have_size, size))
            elif sha_file(os.path.join(base, rel)) != sha:
                problems.append("SHA-256 differs: %s" % rel)
            else:
                nbytes += size
    return problems, nbytes


def verify(backup, closure=True, files_dir=None, scratch=None):
    """-> {"ok": bool, "problems": [...], ...}; never raises for a damaged backup, it reports it. `index_bytes` is the temporary disk the check used (in `scratch`)."""
    backup = os.path.abspath(backup)
    try:
        with _manifest_index(backup, scratch) as (db, m, state):
            base = files_dir or os.path.join(backup, FILES)
            problems, nbytes = _compare(db, base)
            if closure and not problems.total:
                problems = CLOSURE[m["profile"]](DbTree(base, db))
            res = {"ok": not problems.total, "problems": list(problems), "n_problems": problems.total, "backup": backup, "profile": m["profile"], "files": m["counted"]["files"],
                   "bytes_verified": nbytes, "quiescent": m["snapshot"].get("quiescent"), "manifest_digest": m["manifest_digest"]}
    except Failed as e:
        return {"ok": False, "problems": [str(e)], "backup": backup}
    except (OSError, sqlite3.OperationalError) as e:
        if not is_disk_full(e):
            raise
        return {"ok": False, "backup": backup, "problems": ["the disk is full where the working index goes (%s): the check could not run and the backup was not touched. Free space or "
                                                             "pass --scratch DIR on a disk with room (see `backup_space.py verify`)" % (scratch or tempfile.gettempdir())]}
    res["index_bytes"] = state["index_bytes"]
    return res


def restore(backup, dest, fsync=True, hook=None, scratch=None, lock=True):
    backup, dest = os.path.abspath(backup), os.path.abspath(dest)
    try:
        with (name_lock(dest, "public_backup restore") if lock else contextlib.nullcontext()):
            try:
                return _restore(backup, dest, fsync, hook or (lambda *a, **k: None), scratch)
            except (OSError, sqlite3.OperationalError) as e:
                if not is_disk_full(e):
                    raise
                return {"ok": False, "stage": "copy", "problems": [
                    "the disk is full while restoring into %s (%s): no destination was created, %s.restoring is kept as evidence of what was written, and the backup was not touched. "
                    "Free space or choose another destination, move the unfinished folder aside with `public_backup.py quarantine %s.restoring`, and retry." % (dest, e, dest, dest)]}
    except Busy as e:
        return {"ok": False, "stage": "lock", "problems": [str(e)]}


def _restore(backup, dest, fsync, hook, scratch):
    try:
        with _manifest_index(backup, scratch) as (db, m, state):
            problems, nbytes = _compare(db, os.path.join(backup, FILES))
            if not problems.total:
                problems = CLOSURE[m["profile"]](DbTree(os.path.join(backup, FILES), db))
            if problems.total:
                return {"ok": False, "stage": "verify backup", "problems": list(problems)}
            if os.path.exists(dest) and (not os.path.isdir(dest) or os.listdir(dest)):
                return {"ok": False, "stage": "destination", "problems": ["%s exists and is not an empty directory: a restore never writes into an existing tree" % dest]}
            stage = dest + ".restoring"
            if os.path.exists(stage):
                return {"ok": False, "stage": "destination", "problems": ["%s exists (an earlier restore did not finish); inspect it first" % stage]}
            os.makedirs(stage, mode=0o755)
            i = 0
            for rows in _batches(db, "SELECT path, size, sha FROM f WHERE path > ? ORDER BY path LIMIT ?"):
                for rel, size, sha_want in rows:
                    hook("file", rel=rel, n=i)
                    i += 1
                    dst = os.path.join(stage, rel)
                    sha, got = copy_file(os.path.join(backup, FILES, rel), dst, fsync)
                    if sha != sha_want or got != size:
                        return {"ok": False, "stage": "copy", "problems": ["%s changed while it was being restored" % rel]}
                    os.chmod(dst, 0o644)
            for d, dirs, _ in os.walk(stage):
                os.chmod(d, 0o755)
            problems, _ = _compare(db, stage)
            if not problems.total:
                problems = CLOSURE[m["profile"]](DbTree(stage, db))
            if problems.total:
                return {"ok": False, "stage": "verify restored tree", "problems": list(problems)[:20]}
            if os.path.isdir(dest):
                os.rmdir(dest)                                    # empty, checked above
            os.replace(stage, dest)
            fsync_dir(os.path.dirname(dest))
            res = {"ok": True, "dest": dest, "files": m["counted"]["files"], "bytes": m["counted"]["bytes"], "profile": m["profile"], "manifest_digest": m["manifest_digest"]}
    except Failed as e:
        return {"ok": False, "stage": "verify backup", "problems": [str(e)]}
    res["index_bytes"] = state["index_bytes"]
    return res


def quarantine_partial(path, to=None, now=time.time, lock=True):
    """Move an unfinished `...partial` folder (a backup or a recovery set that failed or was killed) aside, whole and unchanged, so the retry can use the name. NOTHING is
    deleted or copied: it is a rename within one filesystem, into `to` (default: a `partial-quarantine` folder beside it) under a name that still ends in `.partial` or `.restoring` (so no
    tool can ever mistake it for a finished backup or a restored tree), and a small note is added. Only a folder whose name ends in `.partial` (an unfinished backup or set) or `.restoring` (an unfinished restore) is accepted; a finished backup is never moved. It is refused ("in progress", nothing moved) while
    another operation holds the name's lock, so the .partial of a backup that is running can never be taken from under it."""
    path = os.path.abspath(path)
    if not path.endswith((".partial", ".restoring")) or not os.path.isdir(path) or os.path.islink(path):
        return {"ok": False, "problems": ["%s is not an unfinished (.partial or .restoring) folder; nothing was moved" % path]}
    try:
        with (name_lock(path, "public_backup quarantine") if lock else contextlib.nullcontext()):
            return _quarantine(path, to, now)
    except Busy as e:
        return {"ok": False, "problems": [str(e)]}


def _quarantine(path, to, now):
    qdir = os.path.abspath(to) if to else os.path.join(os.path.dirname(path), "partial-quarantine")
    os.makedirs(qdir, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now()))
    n_files = n_bytes = 0
    for d, _, names in os.walk(path):
        for f in names:
            n_files += 1
            try:
                n_bytes += os.path.getsize(os.path.join(d, f))
            except OSError:
                pass
    dest = os.path.join(qdir, "%s-%s" % (stamp, os.path.basename(path)))
    k = 1
    while os.path.exists(dest):
        k += 1
        dest = os.path.join(qdir, "%s-%d-%s" % (stamp, k, os.path.basename(path)))
    try:
        os.rename(path, dest)
    except OSError as e:
        return {"ok": False, "problems": ["could not move %s into %s (%s); a quarantine is a rename on one filesystem, pass --to on the same one" % (path, qdir, e)]}
    note = {"was": path, "quarantined_at": stamp, "files": n_files, "bytes": n_bytes, "had_manifest": os.path.exists(os.path.join(dest, MANIFEST)),
            "why": "an unfinished backup kept as evidence; it is not a valid backup and no tool will restore from it"}
    note_written = True
    try:                                              # (best effort: on a full disk the rename above is what matters, and it needs no space)
        with open(os.path.join(dest, "QUARANTINE_NOTE.json"), "w", encoding="utf-8") as f:
            json.dump(note, f, indent=1, sort_keys=True)
    except OSError:
        note_written = False
    try:
        fsync_dir(qdir)
    except OSError:
        pass
    return {"ok": True, "quarantined": dest, "files": n_files, "bytes": n_bytes, "note_written": note_written}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create")
    p.add_argument("--src", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--profile", default="public", choices=sorted(CLOSURE))
    p.add_argument("--max-passes", type=int, default=8)
    p.add_argument("--require-quiescent", action="store_true")
    p.add_argument("--no-fsync", action="store_true")
    p.add_argument("--discard-partial", action="store_true")
    p.add_argument("--no-preflight", action="store_true", help="skip the disk-space and RAM check (tools/backup_space.py)")
    p.add_argument("--allow-ram", action="store_true", help="accept a destination on tmpfs/ramfs")
    p = sub.add_parser("verify")
    p.add_argument("backup")
    p.add_argument("--no-closure", action="store_true")
    p.add_argument("--scratch", help="where the temporary index goes (default: the system temporary folder; use a disk, not RAM)")
    p.add_argument("--no-preflight", action="store_true")
    p.add_argument("--allow-ram", action="store_true")
    p = sub.add_parser("restore")
    p.add_argument("backup")
    p.add_argument("dest")
    p.add_argument("--no-fsync", action="store_true")
    p.add_argument("--scratch")
    p.add_argument("--no-preflight", action="store_true")
    p.add_argument("--allow-ram", action="store_true")
    p = sub.add_parser("quarantine")
    p.add_argument("partial")
    p.add_argument("--to")
    p = sub.add_parser("check")
    p.add_argument("--src", required=True)
    p.add_argument("--profile", default="public", choices=sorted(CLOSURE))
    a = ap.parse_args(argv)
    pre = None
    if a.cmd in ("create", "verify", "restore") and not a.no_preflight:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import backup_space as BS
        if a.cmd == "create":
            pre = BS.preflight_create(a.src, a.out, a.allow_ram, discard_partial=a.discard_partial)
        elif a.cmd == "verify":
            pre = BS.preflight_verify(os.path.abspath(a.backup), a.scratch, a.allow_ram)
        else:
            pre = BS.preflight_restore(os.path.abspath(a.backup), a.dest, a.scratch, a.allow_ram)
        if not pre["ok"]:
            print(json.dumps(dict(pre, ok=False), sort_keys=True))
            return 1
    try:
        if a.cmd == "create":
            r = create(a.src, a.out, a.profile, a.max_passes, a.require_quiescent, not a.no_fsync, a.discard_partial)
        elif a.cmd == "verify":
            r = verify(a.backup, closure=not a.no_closure, scratch=a.scratch)
        elif a.cmd == "restore":
            r = restore(a.backup, a.dest, fsync=not a.no_fsync, scratch=a.scratch)
        elif a.cmd == "quarantine":
            r = quarantine_partial(a.partial, a.to)
        else:
            problems = CLOSURE[a.profile](Tree(os.path.abspath(a.src)))
            r = {"ok": not problems.total, "problems": list(problems), "n_problems": problems.total}
    except Failed as e:
        r = {"ok": False, "problems": [str(e)]}
    if pre is not None and isinstance(r, dict):
        r["preflight"] = {"warnings": pre["warnings"], "estimate": pre["estimate"], "estimate_only": pre["estimate_only"]}
    print(json.dumps(r, sort_keys=True))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
