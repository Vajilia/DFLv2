"""Consistent snapshots of SQLite files for the exporter.

The league file and its archive are SQLite databases that a runner may be writing at the moment the exporter looks at them. A plain file copy
of such a database can be torn (pages from two different commits, or a main file without the write-ahead log that holds the newest ones).
SQLite's own online backup API (`Connection.backup`) copies a transactionally consistent image: it reads through a read transaction, restarts
if a writer changes the source meanwhile, and so returns the database exactly as it was at one commit. Every snapshot here is made that way,
into a temporary name, checked with `PRAGMA quick_check`, and renamed into place only if it passes.

The league and the archive are two files, so their pair needs one more rule (see `snapshot_archive`): the league is snapshotted FIRST and the
archive second. The runner writes a batch to the archive before it commits the event to the league, so the archive is never behind the league;
a snapshot pair taken in this order is therefore consistent, with the archive at most one interrupted commit ahead (which the league's own
`archive_check` calls `ahead` and accepts).
"""
import os
import sqlite3


class SnapshotError(Exception):
    pass


def snapshot_sqlite(src, dest, expect_tables=()):
    """A consistent copy of the SQLite database `src` at `dest` (never overwritten). The source is opened read-only; the copy is checked."""
    if os.path.exists(dest):
        raise SnapshotError(f"{dest} already exists")
    if not os.path.exists(src):
        raise SnapshotError(f"{src} does not exist")
    tmp = dest + ".partial"
    sdb = sqlite3.connect(f"file:{os.path.abspath(src)}?mode=ro", uri=True, isolation_level=None, timeout=30)
    try:
        out = sqlite3.connect(tmp)
        try:
            sdb.backup(out)                                   # SQLite's online backup: one consistent commit, restarted if a writer interferes
            check = out.execute("PRAGMA quick_check").fetchall()
            if check != [("ok",)]:
                raise SnapshotError("the snapshot failed SQLite's integrity check: " + "; ".join(str(r[0]) for r in check[:3]))
            have = {r[0] for r in out.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            missing = [t for t in expect_tables if t not in have]
            if missing:
                raise SnapshotError(f"the snapshot lacks table(s) {missing}: {src} is not the kind of file expected")
        finally:
            out.close()
        os.replace(tmp, dest)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    finally:
        sdb.close()
        for ext in ("-wal", "-shm", "-journal"):
            if os.path.exists(tmp + ext):
                os.remove(tmp + ext)
    return dest


def snapshot_archive(archive_path, dest):
    """Snapshot a game archive. Call this AFTER the league file has been snapshotted (see the module note). Only a quick integrity check is
    made here; each archived game is checksummed when it is read (`Archive.game`), and the pair is checked with `persist.archive_check`."""
    return snapshot_sqlite(archive_path, dest, expect_tables=("games", "records", "meta"))
