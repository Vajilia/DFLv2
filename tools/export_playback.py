#!/usr/bin/env python3
"""Export finished games from the archive into the public playback format (docs/PUBLIC_PLAYBACK_FORMAT.md). Read-only on the league and archive.

  export_playback.py --archive LEAGUE.archive --feed FEED_ROOT --out PLAYBACK_DIR [--every 2] [--gid S001/reg:w01:g01 ...] [--limit N] [--json]

Standard library only: it imports nothing from the engine and never opens the league, so it runs without the simulation's code and cannot change a
league fingerprint. Which games: those the PUBLISHED feed already shows. The feed release named by FEED_ROOT/latest.json is `e<events>-<hash>`; a
game whose archive batch is a later event is not exported (an unrevealed game never reaches the public). Teams, colours and rosters come from that
release's public files, nothing else. A game already in PLAYBACK_DIR/index.json is skipped (published games are immutable). Games are written to
PLAYBACK_DIR/games/<id>.<sha10>.json and then index.json is replaced atomically; a failure leaves the previous index in place.
Prints one JSON object: games written, bytes (raw and gzip-6), time per game and peak memory."""
import argparse
import glob
import json
import os
import re
import resource
import sqlite3
import sys
import time
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dflsite import playback as P  # noqa: E402


def ro(path):
    return sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True)


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def feed_public(feed):
    """-> (events published, teams {city: {abbr, name, colors}}, roster {id: {name, number, pos}}, league_id) from the live release's public files."""
    ptr = read_json(os.path.join(feed, "latest.json"))
    rel = os.path.join(feed, "releases", ptr["release"])
    events = int(re.match(r"e(\d+)-", ptr["release"]).group(1))
    teams = {}
    for t in read_json(os.path.join(rel, "teams.json"))["teams"]:
        teams[t["city"]] = {"abbr": t["abbr"], "name": t["full_name"], "colors": [c["hex"] for c in t["colors"]][:3]}
    roster = {}
    for f in glob.glob(os.path.join(rel, "rosters", "*.json")):
        for p in read_json(f)["players"]:
            roster[int(p["id"])] = {"name": p["name"], "number": p["number"], "pos": p["pos"]}
    try:
        po = read_json(os.path.join(rel, "playoffs.json"))
        for rows in ((po.get("bowl") or {}).get("rosters") or {}).values():           # the Bowl's All-Star players (public)
            for p in rows:
                roster.setdefault(int(p["id"]), {"name": p["name"], "number": None, "pos": p["pos"]})
    except (OSError, ValueError, KeyError):
        pass
    man = read_json(os.path.join(rel, "manifest.json"))
    return events, teams, roster, (man.get("league") or {}).get("id")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--archive", required=True)
    ap.add_argument("--feed", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--every", type=int, default=2, help="keep every n-th 0.05 s step (default 2: 0.1 s)")
    ap.add_argument("--gid", action="append", help="only these archive game ids")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args(argv)
    t0 = time.time()
    events, teams, roster, league_id = feed_public(a.feed)
    all_star = {"Western All-Stars": ("WAS", ["#C9A227", "#222222"]), "Eastern All-Stars": ("EAS", ["#C9A227", "#222222"])}
    for name, (ab, cols) in all_star.items():
        teams.setdefault(name, {"abbr": ab, "name": name, "colors": cols})
    idx_path = os.path.join(a.out, "index.json")
    known = {}
    if os.path.exists(idx_path):
        old = read_json(idx_path)
        if old.get("format") != P.FORMAT or old.get("format_version") != P.VERSION:
            raise SystemExit(f"{idx_path} is not a {P.FORMAT} version {P.VERSION} index; refusing to mix formats")
        known = {e["gid"]: e for e in old["games"]}
    db = ro(a.archive)
    try:
        rows = db.execute("SELECT game_id, seq, payload FROM games WHERE seq <= ? ORDER BY seq, game_id", (events,)).fetchall()
        gids = [r[0] for r in rows]
        done, raw, gz, secs = 0, 0, 0, []
        entries = dict(known)
        os.makedirs(a.out, exist_ok=True)
        for gid, seq, z in rows:
            if gid in known or (a.gid and gid not in a.gid):
                continue
            if a.limit is not None and done >= a.limit:
                break
            t1 = time.time()
            rec = json.loads(zlib.decompress(z))
            if rec.get("engine") != "spatial":
                continue
            traces = {i: zlib.decompress(p) for i, p in db.execute("SELECT play_i, payload FROM traces WHERE game_id = ?", (gid,))}
            game = P.build_game(rec, traces, teams, roster, P.feed_id_for(gid, gids), a.every)
            rel, size, sha = P.write_game(a.out, game)
            entries[gid] = P.index_entry(game, rel, size, sha)
            done += 1
            raw += size
            gz += len(zlib.compress(open(os.path.join(a.out, rel), "rb").read(), 6))
            secs.append(round(time.time() - t1, 3))
    finally:
        db.close()
    P.write_index(a.out, list(entries.values()), league_id)
    print(json.dumps({"games_written": done, "games_in_index": len(entries), "feed_events": events, "raw_bytes": raw, "gzip6_bytes": gz,
                      "avg_raw_bytes": raw // done if done else 0, "avg_gzip6_bytes": gz // done if done else 0, "seconds_per_game": secs[:5], "total_seconds": round(time.time() - t0, 2),
                      "peak_memory_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
