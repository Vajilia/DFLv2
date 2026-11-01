#!/usr/bin/env python3
"""Check published replay files against the archive they came from. Read-only; standard library only; it shares no code with the exporter
(it re-reads the archive tables and trace containers itself, from docs/ARCHIVE.md), so an exporter mistake is not repeated here.

  verify_playback_against_archive.py --archive LEAGUE.archive --playback PLAYBACK_DIR [--gid S001/... ...] [--json]

For every game in PLAYBACK_DIR/index.json (or the --gid ones) it checks
  * the file is the one the index names (size, SHA-256, hash in the file name) and the index entry agrees with the file
  * participants: the two clubs are the archive's home and away clubs; the set of players in the file's roster is exactly the set of players that appear
    in the archive's traces for that game, each on the side the archive's possession (offence = slots 0-10) puts them on
  * the final score equals the archive's; the running score is the cumulative sum of the archive's scoring plays, play by play
  * scoring events: the same plays, in the same order, with the same points, for the same side
  * the play list: same count, order, period, clock and kind as the archive's play table
  * recorded movement: a play has a trace in the file if and only if the archive holds a trace row for it; for every traced play, every kept
    sample of every position and ball column equals the archive's integer value at that step (so sampling is the only change: nothing is smoothed,
    moved or added), the last recorded step is kept, roles/ids/line of scrimmage/events agree
  * nothing private: no key outside the public schema at any depth
Exit code 0 only if every check passed for every game. The report counts what was compared."""
import argparse
import base64
import hashlib
import json
import os
import re
import sqlite3
import struct
import sys
import zlib

MAGIC = b"DFLT"
PUBLIC_KEYS = {"format", "format_version", "kind", "gid", "id", "season", "game_kind", "date", "kickoff", "neutral", "teams", "final", "periods", "tie", "winner", "roster",
               "identity_note", "sampling", "plays", "traced_plays", "field", "abbr", "name", "colors", "away", "home", "number", "pos", "side", "i", "period", "clock", "off",
               "dir", "down", "dist", "yl", "text", "first", "pts", "score", "trace", "dt", "n", "t_end", "los", "roles", "ids", "events", "ball", "ball_from", "encoding", "length",
               "width", "end_zone", "every", "step_seconds", "note"}
PUBLIC_KEYS |= {"pos"}


def ro(path):
    return sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True)


def archive_trace(payload):
    """-> (header, pos[col][step], ball[col][step]) as exact integers, per docs/ARCHIVE.md."""
    c = zlib.decompress(payload) if payload[:4] != MAGIC else payload
    assert c[:4] == MAGIC, "not a trace container"
    version, hl = struct.unpack("<BI", c[4:9])
    assert version == 1
    head = json.loads(c[9:9 + hl])
    n = head["steps"]
    p0 = 9 + hl
    vals = struct.unpack(f"<{n * 44 + n * 3}h", c[p0:p0 + (n * 44 + n * 3) * 2])
    cols = []
    for k in range(44 + 3):
        run, col = 0, []
        for s in range(n):
            run += vals[k * n + s]
            col.append(run)
        cols.append(col)
    return head, cols[:44], cols[44:]


def file_cols(b64, n, ncols):
    raw = base64.b64decode(b64)
    vals = struct.unpack(f"<{n * ncols}h", raw)
    out = []
    for k in range(ncols):
        run, col = 0, []
        for s in range(n):
            run += vals[k * n + s]
            col.append(run)
        out.append(col)
    return out


def walk_keys(o, found):
    if isinstance(o, dict):
        for k, v in o.items():
            found.add(k)
            if k != "roster":
                walk_keys(v, found)
            else:
                for pid, info in v.items():
                    found.update(info.keys())
    elif isinstance(o, list):
        for v in o:
            walk_keys(v, found)


def check_game(db, entry, play_dir):
    problems, counts = [], {"plays": 0, "traced_plays": 0, "samples": 0, "values": 0, "scoring_plays": 0, "players": 0}

    def bad(msg):
        problems.append(f"{entry['gid']}: {msg}")
    gid = entry["gid"]
    path = os.path.join(play_dir, entry["file"])
    with open(path, "rb") as f:
        raw = f.read()
    if hashlib.sha256(raw).hexdigest() != entry["sha256"] or len(raw) != entry["bytes"] or entry["sha256"][:10] not in entry["file"]:
        bad("file does not match its index entry (size, SHA-256 or the hash in its name)")
    g = json.loads(raw.decode("utf-8"))
    found = set()
    walk_keys(g, found)
    if found - PUBLIC_KEYS:
        bad(f"keys outside the public schema: {sorted(found - PUBLIC_KEYS)}")
    if g["gid"] != gid or g["id"] != entry["id"]:
        bad("file and index disagree on the game id")
    row = db.execute("SELECT payload FROM games WHERE game_id = ?", (gid,)).fetchone()
    if row is None:
        bad("not in the archive")
        return problems, counts
    rec = json.loads(zlib.decompress(row[0]))
    table = rec["table"]
    # --- participants: clubs
    for side in ("away", "home"):
        full = g["teams"][side]["name"]
        club = rec[side]
        if not full.startswith(club) and club not in full:
            bad(f"{side} club: the file says {full!r}, the archive {club!r}")
    # --- final score
    hs, as_ = rec["score"]
    if (g["final"]["home"], g["final"]["away"]) != (hs, as_):
        bad(f"final score: file home {g['final']['home']} away {g['final']['away']}, archive home {hs} away {as_}")
    # --- which team index is home: the one whose scoring plays add up to the home score
    pts = [0, 0]
    for e in table:
        if e.get("pts") and e.get("team") in (0, 1):
            pts[e["team"]] += e["pts"]
    home_idx = 0 if pts == [hs, as_] else 1 if pts == [as_, hs] else None
    if home_idx is None:
        bad(f"the archive's scoring plays add up to {pts}, not to its own final score {hs}-{as_}")
        return problems, counts
    side_of = {home_idx: "home", 1 - home_idx: "away"}
    # --- play list
    plays = g["plays"]
    counts["plays"] = len(plays)
    if [p["i"] for p in plays] != [e["i"] for e in table]:
        bad("the play list (indexes, order, count) differs from the archive's play table")
        return problems, counts
    trace_rows = {r[0]: r[1] for r in db.execute("SELECT play_i, payload FROM traces WHERE game_id = ?", (gid,))}
    flagged = {e["i"] for e in table if e.get("has_trace")}
    if set(trace_rows) != flagged:
        bad(f"the archive itself disagrees with its play table: trace rows {sorted(set(trace_rows) ^ flagged)[:5]}")
    every = g["sampling"]["every"]
    running = {"away": 0, "home": 0}
    ids_seen = {}
    for p, e in zip(plays, table):
        if (p["period"], p["clock"], p["kind"]) != (e["period"], e["clock"], e["kind"]):
            bad(f"play {p['i']}: period/clock/kind differ ({(p['period'], p['clock'], p['kind'])} vs {(e['period'], e['clock'], e['kind'])})")
        off = side_of.get(e.get("off"))
        if p["off"] != off:
            bad(f"play {p['i']}: possession differs ({p['off']} vs {off})")
        if e.get("pts") and e.get("team") in (0, 1):
            running[side_of[e["team"]]] += e["pts"]
            counts["scoring_plays"] += 1
            if p.get("pts") != e["pts"]:
                bad(f"play {p['i']}: scoring play has {p.get('pts')} points in the file, {e['pts']} in the archive")
        elif p.get("pts"):
            bad(f"play {p['i']}: the file has points the archive does not")
        if p["score"] != running:
            bad(f"play {p['i']}: running score {p['score']} differs from the archive's cumulative {running}")
        # --- movement
        has = p["i"] in trace_rows
        if has != (p["trace"] is not None):
            bad(f"play {p['i']}: the archive {'has' if has else 'has no'} trace and the file {'has none' if p['trace'] is None else 'has one'}")
            continue
        if not has:
            continue
        counts["traced_plays"] += 1
        head, apos, aball = archive_trace(trace_rows[p["i"]])
        t = p["trace"]
        steps = head["steps"]
        keep = list(range(0, steps, every))
        if keep[-1] != steps - 1:
            keep.append(steps - 1)
        if t["n"] != len(keep) or abs(t["t_end"] - (steps - 1) * 0.05) > 1e-9:
            bad(f"play {p['i']}: {t['n']} samples ending {t['t_end']} s; expected {len(keep)} ending {(steps - 1) * 0.05:.2f} s")
            continue
        fpos, fball = file_cols(t["pos"], t["n"], 44), file_cols(t["ball"], t["n"], 3)
        for k in range(44):
            want = [apos[k][s] for s in keep]
            counts["values"] += len(want)
            if fpos[k] != want:
                bad(f"play {p['i']}: position column {k} differs from the archive at a kept step")
                break
        for k in range(3):
            want = [aball[k][s] for s in keep]
            counts["values"] += len(want)
            if fball[k] != want:
                bad(f"play {p['i']}: ball column {k} differs from the archive at a kept step")
                break
        counts["samples"] += t["n"]
        if t["ids"] != head["ids"] or t["roles"] != head["roles"] or t["los"] != head["los"]:
            bad(f"play {p['i']}: roles, player ids or line of scrimmage differ")
        want_ev = [[round(s * 0.05, 2), k] for s, k, a, b in head["events"] if k.split(":")[0] in
                   ("handoff", "throw", "catch", "tackle", "fumble", "intercept", "batted", "drop", "breakup", "scramble", "miss") or k.startswith("end:")]
        if [[ev[0], ev[1]] for ev in t["events"]] != want_ev:
            bad(f"play {p['i']}: recorded events differ from the archive's")
        for k, pid in enumerate(head["ids"]):
            ids_seen.setdefault(int(pid), set()).add(side_of.get(e.get("off")) if k < 11 else ("away" if side_of.get(e.get("off")) == "home" else "home"))
    # --- participants: players
    file_ids = {int(k): v["side"] for k, v in g["roster"].items()}
    counts["players"] = len(file_ids)
    if set(file_ids) != set(ids_seen):
        bad(f"players: {len(set(file_ids) ^ set(ids_seen))} differ between the file's roster and the players in the archive's traces")
    for pid, sides in ids_seen.items():
        if len(sides) != 1 or file_ids.get(pid) not in sides:
            bad(f"player {pid}: side {file_ids.get(pid)!r} in the file, {sorted(sides)} in the archive's traces")
    if g["traced_plays"] != counts["traced_plays"]:
        bad("traced_plays count in the file is wrong")
    return problems, counts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--archive", required=True)
    ap.add_argument("--playback", required=True)
    ap.add_argument("--gid", action="append")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    with open(os.path.join(a.playback, "index.json"), encoding="utf-8") as f:
        idx = json.load(f)
    db = ro(a.archive)
    entries = [e for e in idx["games"] if not a.gid or e["gid"] in a.gid]
    allp, total, per = [], {}, {}
    for e in entries:
        probs, c = check_game(db, e, a.playback)
        allp += probs
        per[e["gid"]] = c
        for k, v in c.items():
            total[k] = total.get(k, 0) + v
    out = {"games_checked": len(entries), "problems": allp, "compared": total, "per_game": per, "ok": not allp and bool(entries)}
    if a.json:
        print(json.dumps(out, indent=1))
    else:
        for e in entries:
            c = per[e["gid"]]
            print(f"{e['gid']}: {c['plays']} plays, {c['traced_plays']} traced, {c['scoring_plays']} scoring plays, {c['players']} players, {c['samples']} samples, {c['values']} values compared")
        print(f"{len(entries)} game(s): " + ("ALL CHECKS PASSED" if out["ok"] else f"{len(allp)} PROBLEM(S)"))
        for pr in allp[:40]:
            print("  " + pr)
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
