#!/usr/bin/env python3
"""READ-ONLY dry-run inventory of what a multi-season broadcast stores, and what could EVENTUALLY be removed under the proposed retention rules
(docs/STORAGE_RETENTION.md). This program contains no code that deletes, moves or rewrites anything; it only measures and classifies.

  storage_inventory.py --root R [--public P] [--keep-live-seasons 2] [--json] [--list]      (a report is the program's standard output; it writes no file)

R   the private root (league/, store/); P the public folder the daemon fills (optional; without it nothing is called removable that depends on a public copy).

Classes (each file belongs to exactly one):
  permanent           the league and its archive, the season plans and builds, Honors parts, every feed release that is public or current, every playback
                      file (the published replay URLs, immutable), the cumulative indexes, `latest.json`. Never a candidate.
  cache_after_handover  private copies the builder made for a season that has been handed over (store/carry.json lists it) AND whose public copy exists with the
                      same size: the live recordings and replay copies under store/games, the private playback export, the private feed releases of that season.
                      Derivable from the archive again (export_playback.py / broadcast_build.py); not needed by the daemon once the season is behind the clock.
  duplicate_public    broadcast/replays/<name> and playback/games/<name> hold the same bytes: one copy could be a hard link (saves the size, removes no URL).
  old_live            broadcast/live/<game>/ of a final game in a season older than the newest `--keep-live-seasons`: only the replay of it is needed afterwards.
                      CONDITIONAL on the frontend (a Watch link to an old game would stop working); a decision, not a rule.
  in_use              anything the daemon or the builder still reads for the season on air or being built.

Exit 0 always (an inventory is not a test). The summary says how many bytes each class holds, per season, and the total of the removable classes.
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dflsite import season as S  # noqa: E402


def size_of(path):
    """(bytes, files) of a file or directory tree; symlinks are not followed."""
    if os.path.isfile(path):
        return os.path.getsize(path), 1
    total = n = 0
    stack = [path]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                    elif e.is_file(follow_symlinks=False):
                        total += e.stat(follow_symlinks=False).st_size
                        n += 1
        except OSError:
            pass
    return total, n


def jget(path):
    try:
        with open(path, "rb") as f:
            return json.loads(f.read())
    except (OSError, ValueError):
        return None


def season_of(name):
    m = re.match(r"^S(\d+)[-/]", name)
    return int(m.group(1)) if m else None


def inventory(root, public=None, keep_live=2):
    store = os.path.join(os.path.abspath(root), "store")
    carry = jget(os.path.join(store, S.CARRY)) or {}
    handed = set(carry.get("seasons", []))
    on_air = int(carry["season"]) if carry else None
    seasons = {}
    root_plan = jget(os.path.join(store, S.PLAN)) or {}
    first = int(root_plan.get("season", 1))
    seasons[first] = jget(os.path.join(store, S.BUILDS)) or {}
    sd = os.path.join(store, S.SEASONS)
    if os.path.isdir(sd):
        for name in sorted(os.listdir(sd)):
            b = jget(os.path.join(sd, name, S.BUILDS))
            p = jget(os.path.join(sd, name, S.PLAN))
            if b is not None and p is not None:
                seasons[int(p["season"])] = b
    newest = max(seasons) if seasons else first
    if on_air is None:
        on_air = first
    rows = []

    def add(cls, where, path, season, note, public_copy=None):
        b, n = size_of(path)
        rows.append({"class": cls, "where": where, "path": os.path.relpath(path, os.path.abspath(root)) if not path.startswith(public or "\0") else "public:" + os.path.relpath(path, public),
                     "season": season, "bytes": b, "files": n, "note": note})

    # ---- the league
    for rel in ("league/league.db", "league/league.db-wal", "league/league.db-shm", "league/league.db.archive", "league/ctl"):
        p = os.path.join(os.path.abspath(root), rel)
        if os.path.exists(p):
            add("permanent", "private", p, None, "the league and its archive: the permanent game history and the recovery source")
    for rel in ("build.log.jsonl",):
        p = os.path.join(os.path.abspath(root), rel)
        if os.path.exists(p):
            add("permanent", "private", p, None, "builder log")
    # ---- the store
    for name in ("plan.json", "builds.json", "carry.json", "clock.json", "clock_log.jsonl"):
        p = os.path.join(store, name)
        if os.path.exists(p):
            add("permanent", "private", p, None, "plan, builds, hand-over record, clock")
    if os.path.isdir(sd):
        add("permanent", "private", sd, None, "later seasons' plans and builds")
    hon = os.path.join(store, "honors")
    if os.path.isdir(hon):
        add("permanent", "private", hon, None, "Honors parts")
    # private feed releases: season by builds
    rel_season = {}
    last_release = {}
    for s, b in seasons.items():
        for seq, ev in b.get("events", {}).items():
            rel_season[ev["release"]] = s
        if b.get("events"):
            top = max(b["events"], key=int)
            last_release[s] = b["events"][top]["release"]
    gen = (seasons.get(first) or {}).get("genesis")
    if gen:
        rel_season[gen["release"]] = first
    rd = os.path.join(store, "feed", "releases")
    if os.path.isdir(rd):
        for name in sorted(os.listdir(rd)):
            s = rel_season.get(name)
            pub = os.path.join(public, "releases", name) if public else None
            if s in handed and last_release.get(s) != name and pub and os.path.isdir(pub) and size_of(pub) == size_of(os.path.join(rd, name)):
                add("cache_after_handover", "private", os.path.join(rd, name), s, "private copy of a release that is already public and not the season's last")
            else:
                add("permanent" if s not in handed else "in_use", "private", os.path.join(rd, name), s, "release (current, last of its season, or without a verified public copy)")
    pd = os.path.join(store, "feed", "pointers")
    if os.path.isdir(pd):
        add("permanent", "private", pd, None, "one tiny pointer per event")
    # private games and playback export
    gd = os.path.join(store, "games")
    if os.path.isdir(gd):
        for name in sorted(os.listdir(gd)):
            s = season_of(name)
            end = jget(os.path.join(gd, name, "end.json")) or {}
            rp = os.path.join(public, "broadcast", "replays", end.get("replay", "\0")) if public else None
            if s in handed and rp and os.path.isfile(rp) and os.path.getsize(rp) == os.path.getsize(os.path.join(gd, name, "replay.json")):
                add("cache_after_handover", "private", os.path.join(gd, name), s, "live recording and replay copy of a game whose season is behind the clock; replay is public")
            else:
                add("in_use", "private", os.path.join(gd, name), s, "season on air or being built, or no verified public replay")
    pbd = os.path.join(store, "playback")
    if os.path.isdir(pbd):
        idx = jget(os.path.join(pbd, "index.json")) or {"games": []}
        files = {e["file"]: e for e in idx["games"]}
        gdir = os.path.join(pbd, "games")
        if os.path.isdir(gdir):
            for name in sorted(os.listdir(gdir)):
                e = files.get("games/" + name)
                s = int(e["season"]) if e else season_of(name)
                pubf = os.path.join(public, "playback", "games", name) if public else None
                if s in handed and pubf and os.path.isfile(pubf) and os.path.getsize(pubf) == os.path.getsize(os.path.join(gdir, name)):
                    add("cache_after_handover", "private", os.path.join(gdir, name), s, "private export of a replay already public (immutable name)")
                else:
                    add("in_use", "private", os.path.join(gdir, name), s, "export of the season on air or being built")
        if os.path.exists(os.path.join(pbd, "index.json")):
            add("in_use", "private", os.path.join(pbd, "index.json"), None, "the exporter's index (rewritten at every event)")
    # ---- the public tree
    if public and os.path.isdir(public):
        for name in ("latest.json", "status.json"):
            p = os.path.join(public, name)
            if os.path.exists(p):
                add("permanent", "public", p, None, "pointer / heartbeat")
        pg = os.path.join(public, "playback", "games")
        if os.path.isdir(pg):
            for name in sorted(os.listdir(pg)):
                add("permanent", "public", os.path.join(pg, name), season_of(name), "published replay: an immutable URL the site links to")
        for sub, note in (("releases", "feed releases (site history)"), ("playback/index.json", "cumulative index"),
                          ("broadcast/honors", "Honors"), ("broadcast/replays/index.json", "replay library index"), ("broadcast/clock.json", "clock"), ("broadcast/schedule.json", "schedule")):
            p = os.path.join(public, sub)
            if os.path.exists(p):
                add("permanent", "public", p, None, note)
        rp = os.path.join(public, "broadcast", "replays")
        if os.path.isdir(rp):
            for name in sorted(os.listdir(rp)):
                if name == "index.json":
                    continue
                twin = os.path.join(public, "playback", "games", name)
                s = season_of(name)
                if os.path.isfile(twin) and os.path.getsize(twin) == os.path.getsize(os.path.join(rp, name)) and not os.path.samefile(twin, os.path.join(rp, name)):
                    add("duplicate_public", "public", os.path.join(rp, name), s, "same bytes as playback/games/%s: could be a hard link" % name)
                else:
                    add("permanent", "public", os.path.join(rp, name), s, "replay (no separate twin or already linked)")
        ld = os.path.join(public, "broadcast", "live")
        if os.path.isdir(ld):
            for name in sorted(os.listdir(ld)):
                s = season_of(name)
                man = jget(os.path.join(ld, name, "manifest.json")) or {}
                end = jget(os.path.join(ld, name, "end.json")) or {}
                have_replay = bool(end.get("replay")) and os.path.isfile(os.path.join(public, "broadcast", "replays", end["replay"]))
                if s is not None and s <= newest - keep_live and man.get("state") == "final" and have_replay:
                    add("old_live", "public", os.path.join(ld, name), s, "final game of a season older than the newest %d; its replay stays" % keep_live)
                else:
                    add("in_use", "public", os.path.join(ld, name), s, "live recording of a current or recent season (or not final)")
    return rows


def summarize(rows):
    cls, per = {}, {}
    for r in rows:
        c = cls.setdefault(r["class"], {"bytes": 0, "files": 0, "paths": 0})
        c["bytes"] += r["bytes"]
        c["files"] += r["files"]
        c["paths"] += 1
        p = per.setdefault(str(r["season"]), {}).setdefault(r["class"], 0)
        per[str(r["season"])][r["class"]] = p + r["bytes"]
    total = sum(c["bytes"] for c in cls.values())
    removable = cls.get("cache_after_handover", {}).get("bytes", 0)
    return {"classes": cls, "by_season": per, "total_bytes": total, "reclaimable_after_handover_bytes": removable,
            "reclaimable_by_hard_link_bytes": cls.get("duplicate_public", {}).get("bytes", 0), "conditional_old_live_bytes": cls.get("old_live", {}).get("bytes", 0),
            "deletes_anything": False}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", required=True)
    ap.add_argument("--public")
    ap.add_argument("--keep-live-seasons", type=int, default=2)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--list", action="store_true", help="also print every candidate path")
    a = ap.parse_args(argv)
    rows = inventory(a.root, a.public, a.keep_live_seasons)
    doc = {"summary": summarize(rows), "candidates": [r for r in rows if r["class"] in ("cache_after_handover", "duplicate_public", "old_live")]}
    if a.json:
        print(json.dumps(doc, sort_keys=True))
    else:
        s = doc["summary"]
        print("%-22s %12s %9s" % ("class", "MB", "files"))
        for k, v in sorted(s["classes"].items()):
            print("%-22s %12.1f %9d" % (k, v["bytes"] / 1e6, v["files"]))
        print("total %.1f MB; reclaimable after hand-over %.1f MB; by hard link %.1f MB; conditional old live %.1f MB; deletes nothing" % (
            s["total_bytes"] / 1e6, s["reclaimable_after_handover_bytes"] / 1e6, s["reclaimable_by_hard_link_bytes"] / 1e6, s["conditional_old_live_bytes"] / 1e6))
        if a.list:
            for r in doc["candidates"]:
                print("%-21s %-8s %-40s %10.2f MB" % (r["class"], r["where"], r["path"], r["bytes"] / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
