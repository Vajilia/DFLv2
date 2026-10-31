"""The small status file that tells the website whether the league is moving and whether the updates are healthy.

`feed/status.json` sits beside `latest.json` but is NOT part of a release: a release is immutable content, this file is a heartbeat that is rewritten
(atomically: temporary file, then one rename) every time the exporter runs, even when there is nothing new to publish. The page reads it every minute and
turns "the exporter has not looked for ten minutes" or "the last export failed" or "the runner is not responding" into a visible warning instead of a
silently frozen page.

Public-safe by construction: it carries times, counts, the runner's state word and the NAME of an error's type, never a message, a path or a process id.
"""
import json
import os
import re
import sqlite3
import time

FORMAT = "dfl-feed-status"
VERSION = 1
FILE = "status.json"
RUNNER_FILE = "runner_status.json"
STATES = ("running", "paused", "stopped", "error")


def now_iso(t=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if t is None else t))


def write_atomic(path, obj):
    d = os.path.dirname(os.path.abspath(path))
    tmp = os.path.join(d, f".tmp-{os.path.basename(path)}-{os.getpid()}")
    raw = (json.dumps(obj, indent=1, sort_keys=True) + "\n").encode()
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def read(path):
    try:
        with open(path, "rb") as f:
            v = json.loads(f.read())
        return v if isinstance(v, dict) else None
    except (OSError, ValueError):
        return None


def league_events(league_path):
    """The number of events the league file has committed, read without disturbing a runner (read-only connection)."""
    db = sqlite3.connect(f"file:{os.path.abspath(league_path)}?mode=ro", uri=True, timeout=30)
    try:
        j = db.execute("SELECT MAX(seq) FROM journal").fetchone()[0]
        s = db.execute("SELECT MAX(seq) FROM snapshots").fetchone()[0]
        return max(j or 0, s or 0)
    finally:
        db.close()


def published_events(feed_root):
    """The number of events the live release was built from (None if nothing is published yet)."""
    from . import publish
    return publish._events_of(publish._live_release(feed_root))


def public_runner(rs):
    """The part of the runner's own status file that may be published: no pid, no message, no path."""
    if not isinstance(rs, dict):
        return None
    state = rs.get("state") if rs.get("state") in STATES else "error"
    out = {"state": state, "updated_at": rs.get("updated_at"), "pace_seconds": rs.get("pace_seconds"), "next_event_due": rs.get("next_event_due"),
           "last_event_at": rs.get("last_event_at"), "events": rs.get("seq"), "until_events": rs.get("until_events"),
           "limit_reached": bool(rs.get("limit_reached"))}
    err = rs.get("error")
    if isinstance(err, dict):
        out["error"] = {"type": re.sub(r"[^A-Za-z0-9_.]", "", str(err.get("type", "Error")))[:60], "at": err.get("at")}
    return out


def update(feed_root, league_events_n, published_n, release, ok, error_type=None, seconds=None, published_now=False, runner=None, previous=None):
    """Write `feed/status.json`. `previous` is the earlier content, so the last-success and last-publish times survive a failing run."""
    prev = (previous or {}).get("export") or {}
    t = now_iso()
    doc = {"format": FORMAT, "format_version": VERSION, "checked_at": t, "league_events": league_events_n, "published_events": published_n, "release": release,
           "export": {"ok": bool(ok), "error": None if ok else {"type": re.sub(r"[^A-Za-z0-9_.]", "", str(error_type or "Error"))[:60], "at": t},
                      "last_success_at": t if ok else prev.get("last_success_at"), "last_publish_at": t if published_now else prev.get("last_publish_at"),
                      "seconds": seconds},
           "runner": public_runner(runner)}
    write_atomic(os.path.join(feed_root, FILE), doc)
    return doc
