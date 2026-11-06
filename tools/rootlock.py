"""The builder root's exclusion lock (docs/PUBLIC_BACKUP.md section 5).

One exclusive lock file, `ROOT/.builder.lock`, serialises everything that CHANGES a builder root: the builder's own changing commands (`broadcast_build.py init`, `step`,
`next-season`, `continuous`) and the taking of a recovery set (tools/recovery_set.py create), which must see a root that does not move. It is an operating-system
`flock`, taken without waiting: the second party is refused at once (exit status 75, the league runner's "busy" status) and nothing of it has run. The operating system
drops the lock when the holder dies, however it dies, so a killed builder never leaves it behind. The file's text (who, pid, since) is only for the message.

Read-only commands (`broadcast_build.py status`, the daemon, every check) do not take it. The lock is not inherited by the processes a holder starts (the league runner has
its own lock, `ROOT/league/ctl/runner.lock`, which a recovery set also refuses to go past).
"""
import contextlib
import fcntl
import json
import os
import time

LOCK = ".builder.lock"
EXIT_BUSY = 75


class Busy(Exception):
    def __init__(self, root, info):
        self.root, self.info = root, info
        who = (info or {}).get("who", "another process")
        super().__init__("%s is busy: %s holds the builder lock (pid %s since %s); nothing was changed" % (root, who, (info or {}).get("pid", "?"), (info or {}).get("since", "?")))


def path(root):
    return os.path.join(os.path.abspath(root), LOCK)


def _info(p):
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.loads(f.read() or "{}")
    except (OSError, ValueError):
        return {}


@contextlib.contextmanager
def hold(root, who):
    """Take the lock for the duration of the block, or raise Busy at once."""
    p = path(root)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Busy(root, _info(p))
        os.ftruncate(fd, 0)
        os.write(fd, json.dumps({"who": who, "pid": os.getpid(), "since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}).encode())
        os.fsync(fd)
        try:
            yield
        finally:
            os.ftruncate(fd, 0)                     # (the lock itself goes with the descriptor)
    finally:
        os.close(fd)


def holder(root):
    """Who holds the lock now ({} when the holder wrote nothing), or None if it is free. A momentary shared probe: for tests and messages, not for deciding anything."""
    p = path(root)
    try:
        fd = os.open(p, os.O_RDONLY)
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError:
            return _info(p)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return None
    finally:
        os.close(fd)
