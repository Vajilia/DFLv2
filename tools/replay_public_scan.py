#!/usr/bin/env python3
"""Scan a whole public replay folder for anything outside the public-data rules. Read-only; standard library only; it shares no code with the exporter
(which refuses private keys while it writes) or with the verifier (which compares games with the archive): this one looks only at the files a visitor
can download, so it also catches a file that was changed or put there by something other than the exporter.

  replay_public_scan.py --playback PLAYBACK_DIR [--json]

For every file in index.json, and for the index itself, it checks
  * every object key, at every depth, is one the public schema names (a field added later cannot pass unnoticed: the list is in this file)
  * the roster holds only `name`, `number`, `pos`, `side` per player; numbers are integers or null; names are strings
  * no key or text mentions ratings, attributes, contracts, salaries, play calls, formations, coverages, intents or injuries (the one allowed sentence,
    "A player is injured and leaves the game.", on a play of kind "injury" that has no trace)
  * the file is plain ASCII, canonical JSON (the same bytes the exporter writes for that content), and the name carries its own hash
  * no file in the folder is outside games/*.json and index.json
Exit 0 only if nothing was found. The report counts what was scanned."""
import argparse
import hashlib
import json
import os
import re
import sys

KEYS = {"format", "format_version", "kind", "league_id", "games", "id", "gid", "season", "game_kind", "file", "bytes", "sha256", "away", "home", "away_score",
        "home_score", "plays", "traced_plays", "date", "kickoff", "neutral", "teams", "abbr", "name", "colors", "final", "periods", "tie", "winner", "roster",
        "number", "pos", "side", "identity_note", "sampling", "every", "step_seconds", "note", "field", "length", "width", "end_zone", "i", "period", "clock", "off",
        "dir", "text", "score", "down", "dist", "yl", "first", "pts", "trace", "dt", "n", "t_end", "los", "roles", "ids", "events", "ball", "ball_from", "encoding"}
PLAYER_KEYS = {"name", "number", "pos", "side"}
WORDS = r"ratings?|ovr|overall|attributes?|contracts?|salary|salaries|cap_?hit|play_?calls?|formations?|coverages?|intents?|injur\w*|stamina|skills?|grades?|scouting|drafts?"
BANNED_KEY = re.compile(WORDS, re.I)                           # inside a key name: any occurrence
BANNED_TEXT = re.compile(r"\b(" + WORDS + r")\b", re.I)         # inside a text value: whole words only (a surname may contain "ovr")
INJURY_TEXT = "A player is injured and leaves the game."
ROOT_FILES = {"index.json"}
BASE64 = re.compile(r"[A-Za-z0-9+/]*={0,2}")


def canonical(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def walk(o, where, found, in_roster=False):
    """Collect (path, problem) for every key outside KEYS and every banned word in a key or a text value."""
    if isinstance(o, dict):
        for k, v in o.items():
            if not in_roster and k not in KEYS:
                found.append((where, f"key {k!r} is not in the public schema"))
            if BANNED_KEY.search(k):
                found.append((where, f"key {k!r} names something private"))
            if k in ("pos", "ball") and isinstance(v, str) and len(v) > 64:      # a trace's encoded positions: numbers, not text (a roster position is two or three letters)
                if not BASE64.fullmatch(v):
                    found.append((f"{where}.{k}", "encoded positions are not base64"))
                continue
            if k == "kind" and v == "injury":                    # the public label of a play where an unnamed player left hurt (its text is checked separately)
                continue
            walk(v, f"{where}.{k}", found, in_roster=(k == "roster" and not in_roster))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            walk(v, f"{where}[{i}]", found)
    elif isinstance(o, str) and o != INJURY_TEXT and BANNED_TEXT.search(o):
        found.append((where, f"text {o[:60]!r} mentions something private"))


def scan_game(raw, entry):
    problems = []
    try:
        raw.decode("ascii")
    except UnicodeDecodeError:
        problems.append("file is not plain ASCII")
    g = json.loads(raw.decode("utf-8"))
    if canonical(g) != raw:
        problems.append("file is not canonical JSON (its bytes differ from the canonical form of its own content)")
    if hashlib.sha256(raw).hexdigest() != entry["sha256"] or entry["sha256"][:10] not in entry["file"]:
        problems.append("file does not match the SHA-256 in the index and in its own name")
    found = []
    walk(g, "$", found)
    problems += [f"{w}: {m}" for w, m in found]
    for pid, info in g.get("roster", {}).items():
        if not set(info) <= PLAYER_KEYS:
            problems.append(f"roster {pid}: keys {sorted(set(info) - PLAYER_KEYS)} are not public")
        if "number" in info and info["number"] is not None and not isinstance(info["number"], int):
            problems.append(f"roster {pid}: number is not an integer")
        if "name" in info and not isinstance(info["name"], str):
            problems.append(f"roster {pid}: name is not text")
    injuries = 0
    for p in g.get("plays", []):
        if p.get("kind") == "injury":
            injuries += 1
            if p.get("text") != INJURY_TEXT or p.get("trace") is not None:
                problems.append(f"play {p.get('i')}: an injury play must carry only the fixed sentence and no movement")
        elif INJURY_TEXT in str(p.get("text")):
            problems.append(f"play {p.get('i')}: the injury sentence on a play that is not an injury")
    return problems, injuries


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--playback", required=True)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    with open(os.path.join(a.playback, "index.json"), "rb") as f:
        raw_index = f.read()
    idx = json.loads(raw_index.decode("utf-8"))
    problems = []
    if canonical(idx) != raw_index:
        problems.append("index.json: not canonical JSON")
    found = []
    walk(idx, "index", found)
    problems += [f"{w}: {m}" for w, m in found]
    scanned, injuries, players, unnamed, games_with_unnamed = 0, 0, 0, 0, 0
    named = set()
    for e in idx["games"]:
        named.add(e["file"])
        path = os.path.join(a.playback, e["file"])
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError as ex:
            problems.append(f"{e['gid']}: cannot read {e['file']}: {ex}")
            continue
        pr, inj = scan_game(raw, e)
        problems += [f"{e['gid']}: {x}" for x in pr]
        scanned += 1
        injuries += inj
        roster = json.loads(raw.decode("utf-8")).get("roster", {})
        players += len(roster)
        gone = sum(1 for info in roster.values() if "name" not in info)
        unnamed += gone
        games_with_unnamed += 1 if gone else 0
    for d, _, files in os.walk(a.playback):
        for n in files:
            rel = os.path.relpath(os.path.join(d, n), a.playback).replace(os.sep, "/")
            if rel not in named and rel not in ROOT_FILES:
                problems.append(f"{rel}: a file in the public folder that the index does not name")
    out = {"games_scanned": scanned, "players_in_rosters": players, "players_without_a_name": unnamed, "games_with_unnamed_players": games_with_unnamed, "injury_plays": injuries, "problems": problems, "ok": not problems and scanned > 0}
    if a.json:
        print(json.dumps(out, indent=1))
    else:
        print(f"{scanned} game file(s) scanned, {players} roster entries ({unnamed} without a name, in {games_with_unnamed} games), {injuries} injury plays: " + ("NOTHING OUTSIDE THE PUBLIC RULES" if out["ok"] else f"{len(problems)} PROBLEM(S)"))
        for p in problems[:40]:
            print("  " + p)
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
