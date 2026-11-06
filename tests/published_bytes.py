"""Shared check for the resume tests: every file that was public before a backup's crash point has exactly the same bytes afterwards (on the control copy and on the restored one).

The only files the daemon is DESIGNED to replace are listed here, each with its own rule, and nothing else is excused:
  * latest.json, status.json, broadcast/clock.json, broadcast/schedule.json: single-pointer controls, replaced by atomic rename (not compared);
  * the three indexes (playback, replays, honors): append-only, so the old document must be an exact PREFIX of the new one (same entries, same order, same bytes of each entry);
  * broadcast/live/<game>/manifest.json: the live recording's own control, which grows while the game is on air (not compared; its segments, head and end files are).
Release manifests and every other file are compared byte for byte. No `generated_at` or any other field is ever normalised here.
"""
import hashlib
import json
import os
import re

CONTROLS = ('latest.json', 'status.json', 'broadcast/clock.json', 'broadcast/schedule.json')
APPEND_ONLY = {'playback/index.json': 'games', 'broadcast/replays/index.json': 'games', 'broadcast/honors/index.json': 'parts'}
LIVE_MANIFEST = re.compile(r'^broadcast/live/[^/]+/manifest\.json$')


def _sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for c in iter(lambda: f.read(1 << 20), b''):
            h.update(c)
    return h.hexdigest()


def snapshot(public):
    """{'files': {rel: sha256}, 'indexes': {rel: list}} of everything public now."""
    files, indexes = {}, {}
    for d, _, names in os.walk(public):
        for n in names:
            if n.startswith('.tmp-'):
                continue
            p = os.path.join(d, n)
            rel = os.path.relpath(p, public).replace(os.sep, '/')
            files[rel] = _sha(p)
            if rel in APPEND_ONLY:
                with open(p, 'rb') as f:
                    indexes[rel] = json.loads(f.read())[APPEND_ONLY[rel]]
    return {'files': files, 'indexes': indexes}


def changed(before, public, limit=20):
    """The problems (empty = every previously published file kept its bytes) of the folder `public` against a `snapshot` taken earlier."""
    now = snapshot(public)
    out = []
    for rel, h in sorted(before['files'].items()):
        if rel in CONTROLS or LIVE_MANIFEST.match(rel) or rel in APPEND_ONLY:
            continue
        if rel not in now['files']:
            out.append('previously published file is gone: ' + rel)
        elif now['files'][rel] != h:
            out.append('previously published file changed: ' + rel)
    for rel, old in sorted(before['indexes'].items()):
        new = now['indexes'].get(rel)
        if new is None:
            out.append('index is gone: ' + rel)
        elif new[:len(old)] != old:
            out.append('index entries published earlier were changed or reordered: ' + rel)
    return out[:limit]
