#!/usr/bin/env python3
"""Completeness check for a public replay set: which archived games have a replay, and why the others do not. Read-only; standard library only; it shares
no code with the exporter or with tools/verify_playback_against_archive.py (which checks the games that ARE in the index, not the ones that are missing).

  replay_coverage.py --archive LEAGUE.archive --playback PLAYBACK_DIR --feed FEED_ROOT [--expect-complete] [--json]

Every game row in the archive is put in exactly one class:
  exported    an index entry names it and its file exists
  unrevealed  its archive record was committed after the published feed's event count (the exporter never writes these, on purpose)
  not_spatial the archive record is not from the spatial engine, so it has no stored movement (the exporter skips these)
  missing     none of the above: the index should have an entry and has not. This is the only class that is a problem.
Also checked: an index entry for a game the archive does not hold (orphan); two entries with the same (season, id) (the site could not tell them
apart); a playoff round whose numbers are not exactly 1..n; an id that is not the archive id without its season for a non-playoff game; a file that
is missing from PLAYBACK_DIR/games; a file in games/ that no index entry names (left over). Exit 0 if there is no problem (and, with
--expect-complete, no game at all outside the class `exported`); exit 1 otherwise. The report lists counts per season and kind and every exception."""
import argparse
import json
import os
import re
import sqlite3
import sys
import zlib

PO = re.compile(r"^po:([^:]+):([^:]+):s(\d+)v(\d+)$")
ID_PO = re.compile(r"^po:([^:]+):([^:]+):(\d+)$")


def ro(path):
    return sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True)


def feed_events(feed):
    with open(os.path.join(feed, "latest.json"), encoding="utf-8") as f:
        rel = json.load(f)["release"]
    m = re.match(r"e(\d+)-", rel)
    if not m:
        raise SystemExit(f"{feed}/latest.json names release {rel!r}; expected e<events>-<hash>")
    return int(m.group(1)), rel


def feed_games(feed, release):
    """{(season number, public game id): (away, home, away_score, home_score)} for every finished game the published feed itself lists: the playoff games of each
    season in history.json, and the schedule of the season that is current (schedule.json), whose season number is found through its year."""
    rel = os.path.join(feed, "releases", release)
    out = {}
    with open(os.path.join(rel, "history.json"), encoding="utf-8") as f:
        seasons = json.load(f).get("seasons") or []
    by_year = {s["year"]: s["season"] for s in seasons}
    for s in seasons:
        for g in s.get("playoff_games") or []:
            if g.get("status", "final") == "final" and g.get("away_score") is not None:
                out[(s["season"], g["id"])] = (g["away"], g["home"], g["away_score"], g["home_score"])
    with open(os.path.join(rel, "schedule.json"), encoding="utf-8") as f:
        sched = json.load(f)
    season = by_year.get(sched.get("year"), max((s["season"] for s in seasons), default=0) + 1)
    for g in sched.get("games") or []:
        if g.get("status") == "final" and g.get("away_score") is not None:
            out.setdefault((season, g["id"]), (g["away"], g["home"], g["away_score"], g["home_score"]))
    return out


def analyse(archive, playback, feed):
    events, release = feed_events(feed)
    with open(os.path.join(playback, "index.json"), encoding="utf-8") as f:
        index = json.load(f)
    entries = index["games"]
    by_gid = {e["gid"]: e for e in entries}
    db = ro(archive)
    try:
        traced = dict(db.execute("SELECT game_id, COUNT(*) FROM traces GROUP BY game_id"))
        rows = []
        for gid, seq, z in db.execute("SELECT game_id, seq, payload FROM games ORDER BY seq, game_id"):
            rec = json.loads(zlib.decompress(z))
            rows.append({"gid": gid, "seq": seq, "season": rec.get("season"), "kind": rec.get("kind"), "engine": rec.get("engine"), "traces": traced.get(gid, 0)})
    finally:
        db.close()
    archive_ids = {r["gid"] for r in rows}
    problems, exceptions, classes = [], {"unrevealed": [], "not_spatial": [], "missing": []}, {}
    table = {}
    for r in rows:
        if r["gid"] in by_gid:
            cls = "exported"
        elif r["seq"] > events:
            cls = "unrevealed"
        elif r["engine"] != "spatial":
            cls = "not_spatial"
        else:
            cls = "missing"
        if cls != "exported":
            exceptions[cls].append(r["gid"])
        r["class"] = cls
        key = (r["season"], r["kind"])
        t = table.setdefault(key, {"archive": 0, "exported": 0, "unrevealed": 0, "not_spatial": 0, "missing": 0})
        t["archive"] += 1
        t[cls] += 1
        classes[cls] = classes.get(cls, 0) + 1
    for gid in exceptions["missing"]:
        problems.append(f"{gid}: in the archive, revealed, spatial, and not in the index")
    for e in entries:
        if e["gid"] not in archive_ids:
            problems.append(f"{e['gid']}: in the index but not in the archive (orphan)")
        if not os.path.exists(os.path.join(playback, e["file"])):
            problems.append(f"{e['gid']}: index names {e['file']} and the file is missing")
    named = {e["file"] for e in entries}
    gdir = os.path.join(playback, "games")
    for n in sorted(os.listdir(gdir)) if os.path.isdir(gdir) else []:
        if f"games/{n}" not in named:
            problems.append(f"games/{n}: not named by any index entry (left over)")
    seen = {}
    for e in entries:
        k = (e["season"], e["id"])
        if k in seen:
            problems.append(f"season {e['season']} id {e['id']!r}: given to both {seen[k]} and {e['gid']}")
        seen[k] = e["gid"]
        rest = e["gid"].split("/", 1)[1]
        if PO.match(rest):
            if not ID_PO.match(e["id"]) or ID_PO.match(e["id"]).group(1, 2) != PO.match(rest).group(1, 2):
                problems.append(f"{e['gid']}: playoff id {e['id']!r} does not carry the archive game's conference and round")
        elif e["id"] != rest:
            problems.append(f"{e['gid']}: id {e['id']!r} is not the archive id without its season ({rest!r})")
    rounds = {}
    for e in entries:
        m = ID_PO.match(e["id"])
        if m:
            rounds.setdefault((e["season"], m.group(1), m.group(2)), []).append(int(m.group(3)))
    for k, ns in sorted(rounds.items()):
        if sorted(ns) != list(range(1, len(ns) + 1)):
            problems.append(f"season {k[0]} {k[1]} {k[2]}: playoff numbers are {sorted(ns)}, expected 1..{len(ns)}")
    # the site offers a replay only where the feed's own game (same season and id) has the same clubs and score: check that every game the feed lists
    # finds its replay that way, and say which entries are the ones that do not
    by_key = {(e["season"], e["id"]): e for e in entries}
    fg = feed_games(feed, release)
    feed_no_replay = []
    for (season, gid), want in sorted(fg.items()):
        e = by_key.get((season, gid))
        if e is None:
            twin = [x["gid"] for x in entries if x["season"] == season and (x["away"], x["home"], x["away_score"], x["home_score"]) == want]
            feed_no_replay.append({"season": season, "id": gid, "feed": list(want), "same_game_under": twin})
        elif (e["away"], e["home"], e["away_score"], e["home_score"]) != want:
            feed_no_replay.append({"season": season, "id": gid, "feed": list(want), "index": [e["away"], e["home"], e["away_score"], e["home_score"]], "same_id_other_game": True})
    for x in feed_no_replay:
        problems.append(f"feed game season {x['season']} {x['id']} {x['feed']}: no replay with the same id, clubs and score" +
                        (f" (the same game is under {x['same_game_under']})" if x.get("same_game_under") else ""))
    return {"feed_release": release, "feed_events": events, "archive_games": len(rows), "index_games": len(entries), "classes": classes,
            "feed_games": len(fg), "feed_games_without_replay": feed_no_replay,
            "by_season_kind": [dict(season=k[0], kind=k[1], **v) for k, v in sorted(table.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1])))],
            "exceptions": exceptions, "problems": problems, "index_format": [index.get("format"), index.get("format_version")]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--archive", required=True)
    ap.add_argument("--playback", required=True)
    ap.add_argument("--feed", required=True)
    ap.add_argument("--expect-complete", action="store_true", help="also fail if any archived game is not exported")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = analyse(a.archive, a.playback, a.feed)
    incomplete = [g for c in ("unrevealed", "not_spatial", "missing") for g in r["exceptions"][c]]
    r["ok"] = not r["problems"] and not (a.expect_complete and incomplete)
    if a.json:
        print(json.dumps(r, indent=1))
    else:
        print(f"feed {r['feed_release']} (events {r['feed_events']}): archive {r['archive_games']} games, index {r['index_games']} entries; " +
              ", ".join(f"{k} {v}" for k, v in sorted(r["classes"].items())))
        print(f"the feed lists {r['feed_games']} finished games (history playoff games and the current schedule); {len(r['feed_games_without_replay'])} of them have no replay under the same id, clubs and score")
        for row in r["by_season_kind"]:
            print(f"  season {row['season']} {row['kind']:<10} archive {row['archive']:>4}  exported {row['exported']:>4}  unrevealed {row['unrevealed']}  not_spatial {row['not_spatial']}  missing {row['missing']}")
        for c in ("unrevealed", "not_spatial", "missing"):
            for g in r["exceptions"][c][:20]:
                print(f"  {c}: {g}")
        for p in r["problems"][:40]:
            print("  PROBLEM " + p)
        print("COVERAGE OK" if r["ok"] else "COVERAGE PROBLEM")
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
