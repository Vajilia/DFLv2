"""Atomic publication of a feed, and the reader that proves a published feed is whole.

Layout under the feed root (the directory the web server serves as /feed/):

    latest.json                    the pointer: {"format", "format_version", "release", "manifest_sha256", "content_hash", "generated_at"}
    releases/<release>/manifest.json
    releases/<release>/<every file the manifest lists>
    .publish.lock                  the publisher lock (an flock; see below). Never deleted.

A release is written completely into a hidden temporary directory, every file is flushed, the directory is renamed to its final name, and only
then is `latest.json` replaced (write temp file in the same directory, flush, `os.replace`). Readers follow the pointer, so they see the old
release or the new one and never a mixture; a release directory is immutable once it has its name. A crash at any point leaves the previous
`latest.json` in force.

ONE PUBLISHER AT A TIME. Everything that writes under the feed root (publishing, sweeping leftovers, pruning) happens while holding an exclusive
`flock` on `.publish.lock`. The operating system drops the lock when the holder dies, however it dies (even `kill -9`), so there is never a
stale lock to clear. A second publisher does not wait by default: it raises `PublishBusy` at once and changes nothing, and the caller tries
again later (the exporter exits 75 so the timer just runs again). Because the lock is held, a leftover `.tmp-*` directory found by the next
publisher can only be the debris of a publisher that died; even so it is removed only if it is older than `STALE_TMP_SECONDS`, and a
publisher removes nothing but its own temporary directory when it fails. A publisher never touches another live publisher's directory.

PRUNING NEVER STRANDS A SLOW VIEWER. Old releases are removed only when they are beyond the newest `keep` AND have been on disk for longer than
`PRUNE_GRACE_SECONDS` (default one hour), and never the live one. If a viewer nevertheless asks for a release that has gone, the site re-reads
`latest.json` and carries on with the live release (site/assets/app.js, tested in a browser).

THE FEED NEVER MOVES BACKWARDS. A release with fewer simulated events than the live one is refused unless `allow_older` is set (a restored
league, rolled back on purpose).

Nothing here reads or changes the league: it receives the dict that `feed.build` returned.
"""
import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
import time

from . import feed

KEEP = 6
POINTER = "latest.json"
LOCK = ".publish.lock"
INPUTS = ".export_inputs.json"
PRUNE_GRACE_SECONDS = 3600          # a superseded release stays at least this long, so a page part-way through reading it is not stranded
STALE_TMP_SECONDS = 600             # a leftover temporary directory this old, found while holding the lock, is the debris of a dead publisher


class PublishError(Exception):
    pass


class PublishBusy(PublishError):
    """Another publisher holds the feed's lock. Nothing was changed; try again later."""


class PublisherLock:
    """The exclusive publisher lock of a feed root (`with PublisherLock(root): ...`). `wait` is how many seconds to keep trying (0 = fail at
    once with PublishBusy). Not re-entrant: pass the held lock to `publish(lock=...)` instead of taking it twice."""

    def __init__(self, root, wait=0):
        self.root, self.wait, self.fd = root, wait, None

    def __enter__(self):
        os.makedirs(self.root, exist_ok=True)
        fd = os.open(os.path.join(self.root, LOCK), os.O_RDWR | os.O_CREAT, 0o644)
        deadline = time.monotonic() + self.wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise PublishBusy(f"another publisher holds {os.path.join(self.root, LOCK)}; nothing was changed")
                time.sleep(0.05)
        self.fd = fd
        try:                                                        # informational only: who holds it, since when
            os.ftruncate(fd, 0)
            os.write(fd, json.dumps({"pid": os.getpid(), "since": time.time()}).encode())
        except OSError:
            pass
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
                self.fd = None
        return False


class FeedError(Exception):
    """A feed that cannot be read: unknown format or version, missing or damaged file."""


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _write(path, raw):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())


def _fsync_dir(path):
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _events_of(release):
    m = re.match(r"^e(\d+)-", release or "")
    return int(m.group(1)) if m else None


def _live_release(root):
    try:
        with open(os.path.join(root, POINTER), "rb") as f:
            return json.loads(f.read())["release"]
    except (OSError, ValueError, KeyError):
        return None


def _sweep_stale(root, rel_dir, older_than):
    """Remove leftovers of dead publishers: temporary directories and pointer temp files older than `older_than` seconds. Only called while
    holding the lock. Returns the names removed."""
    now, gone = time.time(), []
    for base, prefix in ((rel_dir, ".tmp-"), (root, f".{POINTER}.tmp-"), (root, f"{INPUTS}.tmp-")):
        for name in os.listdir(base):
            path = os.path.join(base, name)
            if name.startswith(prefix) and now - os.path.getmtime(path) > older_than:
                shutil.rmtree(path, ignore_errors=True) if os.path.isdir(path) else _unlink(path)
                gone.append(name)
    return gone


def build_inputs_id():
    """A fingerprint of everything the feed builder reads besides the league: the public rules summary (and the spec edition it names), the feed
    builder's own code and the format version. If it changes, the feed must be rebuilt even when the league has committed no new event."""
    h = hashlib.sha256()
    h.update(feed.canonical(feed.rules_file()).encode("utf-8"))
    h.update(json.dumps([feed.spec_edition(), feed.FORMAT, feed.FORMAT_VERSION, feed.FORMAT_MINOR]).encode("utf-8"))
    with open(feed.__file__, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def read_inputs(root):
    """What the live release was last built from: {"release", "inputs"} or None. A private dotfile in the feed root (the web server never serves it)."""
    try:
        with open(os.path.join(root, INPUTS), "rb") as f:
            d = json.loads(f.read())
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def write_inputs(root, release, inputs):
    """Record, atomically and under the publisher lock, the builder fingerprint the live `release` was built (or checked) with."""
    tmp = os.path.join(root, f"{INPUTS}.tmp-{os.getpid()}")
    _write(tmp, feed.canonical({"release": release, "inputs": inputs}).encode("utf-8"))
    os.replace(tmp, os.path.join(root, INPUTS))
    _fsync_dir(root)


def inputs_current(root):
    """True if the live release was built with the builder inputs that are in force now."""
    rec = read_inputs(root)
    return bool(rec) and rec.get("release") == _live_release(root) and rec.get("inputs") == build_inputs_id()


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def publish(files, root, keep=KEEP, crash_hook=None, lock=None, wait=0, grace=PRUNE_GRACE_SECONDS, allow_older=False, stale_tmp=STALE_TMP_SECONDS):
    """Publish a built feed under `root`. Returns {"release", "published": bool, "pruned": [...]}. A release that is already live (same name and
    content) is left alone. Holds the publisher lock throughout (pass an already-held `lock` to reuse it; `wait` seconds of patience otherwise,
    then PublishBusy). `crash_hook(point)` is for tests: it may raise or block at "lock_held", "release_written", "pointer_written" or
    "pointer_replaced"."""
    if lock is None:
        with PublisherLock(root, wait) as held:
            return publish(files, root, keep, crash_hook, held, wait, grace, allow_older, stale_tmp)
    hook = crash_hook or (lambda point: None)
    errs = feed.check_public(files)
    if errs:
        raise PublishError("refusing to publish a feed that is not entirely public:\n" + "\n".join(errs[:10]))
    manifest = files["manifest.json"]
    release = manifest["release"]
    if manifest["format"] != feed.FORMAT or manifest["format_version"] != feed.FORMAT_VERSION:
        raise PublishError("the manifest does not describe this format version")
    rel_dir = os.path.join(root, "releases")
    os.makedirs(rel_dir, exist_ok=True)
    hook("lock_held")
    live, mine, theirs = _live_release(root), _events_of(release), None
    theirs = _events_of(live)
    if not allow_older and mine is not None and theirs is not None and mine < theirs:
        raise PublishError(f"refusing to move the feed backwards: {release} has {mine} events, the live release {live} has {theirs} "
                           "(publish with allow_older only after restoring an older league on purpose)")
    _sweep_stale(root, rel_dir, stale_tmp)
    final = os.path.join(rel_dir, release)
    published = False
    if not os.path.isdir(final):
        tmp = tempfile.mkdtemp(prefix=f".tmp-{release}-{os.getpid()}-", dir=rel_dir)         # ours alone: only this call ever removes it
        os.chmod(tmp, 0o755)                                                                  # mkdtemp makes it private (0700); the web server runs as another user and must be able to read the release
        try:
            body = {}
            for name in sorted(files):
                if name != "manifest.json":
                    body[name] = feed.canonical(files[name]).encode("utf-8")
                    _write(os.path.join(tmp, name), body[name])
            for name, entry in manifest["files"].items():              # the manifest must describe exactly the bytes just written
                if name not in body or entry["sha256"] != _sha(body[name]) or entry["bytes"] != len(body[name]):
                    raise PublishError(f"{name}: written bytes do not match the manifest")
            if set(body) != set(manifest["files"]):
                raise PublishError("the manifest does not list exactly the files written")
            _write(os.path.join(tmp, "manifest.json"), feed.canonical(manifest).encode("utf-8"))
            for dirpath, _, names in os.walk(tmp):
                _fsync_dir(dirpath)
            os.rename(tmp, final)
            _fsync_dir(rel_dir)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        published = True
    hook("release_written")
    if published:
        raw_manifest, man = feed.canonical(manifest).encode("utf-8"), manifest
    else:
        # The release already exists. Same name only means same first ten hex digits of the content hash, so it is reused ONLY if what is on disk is
        # exactly what was built now: the same file index, and every file's bytes matching it (a changed rules summary, a damaged file or a name collision
        # is refused and the pointer is not touched). Only `generated_at` may differ. The pointer must describe the bytes ON DISK, never a fresh manifest
        # that was not written (found on the Droplet 2026-10-31: the pointer's manifest_sha256 matched no file).
        with open(os.path.join(final, "manifest.json"), "rb") as f:
            raw_manifest = f.read()
        man = json.loads(raw_manifest)
        if man.get("content_hash") != manifest["content_hash"] or man.get("files") != manifest["files"]:
            raise PublishError(f"release {release} exists with different content; refusing to point at it")
        for name, entry in manifest["files"].items():
            try:
                with open(os.path.join(final, name), "rb") as f:
                    raw = f.read()
            except OSError:
                raise PublishError(f"release {release} exists but {name} is missing; refusing to point at it") from None
            if _sha(raw) != entry["sha256"] or len(raw) != entry["bytes"]:
                raise PublishError(f"release {release} exists but {name} differs from what was built; refusing to point at it")
    pointer = {"format": feed.FORMAT, "format_version": feed.FORMAT_VERSION, "release": release, "manifest_sha256": _sha(raw_manifest),
               "content_hash": man["content_hash"], "generated_at": man["generated_at"]}
    ptmp = os.path.join(root, f".{POINTER}.tmp-{os.getpid()}")
    _write(ptmp, feed.canonical(pointer).encode("utf-8"))
    hook("pointer_written")
    os.replace(ptmp, os.path.join(root, POINTER))
    _fsync_dir(root)
    hook("pointer_replaced")
    return {"release": release, "published": published, "pruned": prune(root, keep, grace)}


def prune(root, keep=KEEP, grace=PRUNE_GRACE_SECONDS):
    """Remove releases beyond the newest `keep` (by name, which sorts by event count) that have also been on disk longer than `grace` seconds;
    never the one `latest.json` names. Call it only while holding the publisher lock (`publish` does)."""
    rel_dir = os.path.join(root, "releases")
    live = _live_release(root)
    names = sorted(n for n in os.listdir(rel_dir) if not n.startswith("."))
    now = time.time()
    drop = ([n for n in names[:-keep] if n != live and now - os.path.getmtime(os.path.join(rel_dir, n)) >= grace] if keep > 0 else [])
    for n in drop:
        shutil.rmtree(os.path.join(rel_dir, n), ignore_errors=True)
    return drop


def install_release(src_root, dest_root, wait=0, allow_older=False):
    """Install the live release of one feed root into another (for example a seed feed shipped with a deployment package). The source is read
    and verified first (`read_release`), the release directory is copied under a name of its own, verified again at the destination, renamed into
    place and only then is the destination's pointer replaced, all under the destination's publisher lock. Returns {"release", "installed": bool}."""
    data = read_release(src_root, verify=True)
    release = data["manifest.json"]["release"]
    with PublisherLock(dest_root, wait):
        rel_dir = os.path.join(dest_root, "releases")
        os.makedirs(rel_dir, exist_ok=True)
        live, mine, theirs = _live_release(dest_root), _events_of(release), None
        theirs = _events_of(live)
        if not allow_older and mine is not None and theirs is not None and mine < theirs:
            raise PublishError(f"refusing to move the feed backwards: {release} has {mine} events, the live release {live} has {theirs}")
        final = os.path.join(rel_dir, release)
        installed = False
        if not os.path.isdir(final):
            tmp = tempfile.mkdtemp(prefix=f".tmp-{release}-{os.getpid()}-", dir=rel_dir)
            os.chmod(tmp, 0o755)
            try:
                shutil.copytree(os.path.join(src_root, "releases", release), tmp, dirs_exist_ok=True)
                for dirpath, _, names in os.walk(tmp):
                    for n in names:
                        fd = os.open(os.path.join(dirpath, n), os.O_RDONLY)
                        try:
                            os.fsync(fd)
                        finally:
                            os.close(fd)
                    _fsync_dir(dirpath)
                os.rename(tmp, final)
                _fsync_dir(rel_dir)
            except BaseException:
                shutil.rmtree(tmp, ignore_errors=True)
                raise
            installed = True
        with open(os.path.join(src_root, POINTER), "rb") as f:
            pointer_raw = f.read()
        ptmp = os.path.join(dest_root, f".{POINTER}.tmp-{os.getpid()}")
        _write(ptmp, pointer_raw)
        again = read_release_at(dest_root, release, json.loads(pointer_raw))        # what is now in place must be what was verified
        if again != release:
            os.unlink(ptmp)
            raise PublishError("the installed release does not verify")
        os.replace(ptmp, os.path.join(dest_root, POINTER))
        _fsync_dir(dest_root)
        return {"release": release, "installed": installed}


def read_release_at(root, release, pointer):
    """Verify a release directory against a given pointer without relying on the root's own latest.json. Returns the release name."""
    base = os.path.join(root, "releases", release)
    with open(os.path.join(base, "manifest.json"), "rb") as f:
        raw = f.read()
    if _sha(raw) != pointer["manifest_sha256"]:
        raise FeedError(f"release {release}: manifest does not match the pointer")
    manifest = json.loads(raw)
    for name, entry in manifest["files"].items():
        with open(os.path.join(base, name), "rb") as f:
            body = f.read()
        if len(body) != entry["bytes"] or _sha(body) != entry["sha256"]:
            raise FeedError(f"release {release}: {name} does not match the manifest")
    return release


# ------------------------------------------------------------------------------------------------------------------- the reader
def read_release(root, release=None, verify=True):
    """Read a published release the way the website does: follow the pointer, check the format and version, load the manifest and every file it
    lists, and (with `verify`) check each file's size and SHA-256. Returns {path: parsed JSON}. Raises FeedError on anything unreadable.
    Unknown extra files and unknown extra fields are ignored (minor versions add them); an unknown major version is refused."""
    try:
        with open(os.path.join(root, POINTER), "rb") as f:
            ptr = json.loads(f.read())
    except (OSError, ValueError) as e:
        raise FeedError(f"no readable {POINTER}: {e}")
    if ptr.get("format") != feed.FORMAT:
        raise FeedError(f"not a {feed.FORMAT} feed")
    if ptr.get("format_version") != feed.FORMAT_VERSION:
        raise FeedError(f"feed version {ptr.get('format_version')} is not understood (this reader reads version {feed.FORMAT_VERSION})")
    release = release or ptr["release"]
    base = os.path.join(root, "releases", release)
    try:
        with open(os.path.join(base, "manifest.json"), "rb") as f:
            raw = f.read()
        manifest = json.loads(raw)
    except (OSError, ValueError) as e:
        raise FeedError(f"release {release}: manifest unreadable: {e}")
    if verify and release == ptr["release"] and _sha(raw) != ptr["manifest_sha256"]:
        raise FeedError(f"release {release}: manifest does not match the pointer")
    if manifest.get("format") != feed.FORMAT or manifest.get("format_version") != feed.FORMAT_VERSION:
        raise FeedError(f"release {release}: format or version not understood")
    out = {"manifest.json": manifest}
    for name, entry in manifest["files"].items():
        try:
            with open(os.path.join(base, name), "rb") as f:
                body = f.read()
        except OSError as e:
            raise FeedError(f"release {release}: {name} missing: {e}")
        if verify and (len(body) != entry["bytes"] or _sha(body) != entry["sha256"]):
            raise FeedError(f"release {release}: {name} does not match the manifest")
        out[name] = json.loads(body)
    return out
