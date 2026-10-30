"""Atomic publication of a feed, and the reader that proves a published feed is whole.

Layout under the feed root (the directory the web server serves as /feed/):

    latest.json                    the pointer: {"format", "format_version", "release", "manifest_sha256", "content_hash", "generated_at"}
    releases/<release>/manifest.json
    releases/<release>/<every file the manifest lists>

A release is written completely into a hidden temporary directory, every file is flushed, the directory is renamed to its final name, and only
then is `latest.json` replaced (write temp file in the same directory, flush, `os.replace`). Readers follow the pointer, so they see the old
release or the new one and never a mixture; a release directory is immutable once it has its name. A crash at any point leaves the previous
`latest.json` in force; leftovers are temporary directories that the next publication removes. Old releases are pruned only after the new one is
live, and the newest few are always kept so a page that is part-way through reading one does not lose it.

Nothing here reads or changes the league: it receives the dict that `feed.build` returned.
"""
import hashlib
import json
import os
import shutil
import tempfile

from . import feed

KEEP = 6
POINTER = "latest.json"


class PublishError(Exception):
    pass


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


def publish(files, root, keep=KEEP, crash_hook=None):
    """Publish a built feed under `root`. Returns {"release", "published": bool, "pruned": [...]}. A release that is already live (same name and
    content) is left alone. `crash_hook(point)` is for tests: it may raise to stop at "release_written", "pointer_written" or "pointer_replaced"."""
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
    for name in os.listdir(rel_dir):                                   # leftovers of an interrupted publication
        if name.startswith(".tmp-"):
            shutil.rmtree(os.path.join(rel_dir, name), ignore_errors=True)
    final = os.path.join(rel_dir, release)
    published = False
    if not os.path.isdir(final):
        tmp = tempfile.mkdtemp(prefix=".tmp-" + release + "-", dir=rel_dir)
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
    raw_manifest = feed.canonical(manifest).encode("utf-8")
    pointer = {"format": feed.FORMAT, "format_version": feed.FORMAT_VERSION, "release": release, "manifest_sha256": _sha(raw_manifest),
               "content_hash": manifest["content_hash"], "generated_at": manifest["generated_at"]}
    ptmp = os.path.join(root, f".{POINTER}.tmp-{os.getpid()}")
    _write(ptmp, feed.canonical(pointer).encode("utf-8"))
    hook("pointer_written")
    os.replace(ptmp, os.path.join(root, POINTER))
    _fsync_dir(root)
    hook("pointer_replaced")
    return {"release": release, "published": published, "pruned": prune(root, keep)}


def prune(root, keep=KEEP):
    """Remove releases beyond the newest `keep` (by name, which sorts by event count); never the one `latest.json` names."""
    rel_dir = os.path.join(root, "releases")
    live = None
    try:
        with open(os.path.join(root, POINTER), "rb") as f:
            live = json.loads(f.read())["release"]
    except (OSError, ValueError, KeyError):
        pass
    names = sorted(n for n in os.listdir(rel_dir) if not n.startswith("."))
    drop = [n for n in names[:-keep] if n != live] if keep > 0 else []
    for n in drop:
        shutil.rmtree(os.path.join(rel_dir, n), ignore_errors=True)
    return drop


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
