#!/usr/bin/env python3
"""Reconcile the two cumulative game indexes by (season, game ID): the replay library (broadcast/replays/index.json) and the playback index (playback/index.json).

  index_reconcile.py --root ROOT --public PUBLIC [--at-position S] [--json]

They are meant to differ for a while, by design. The replay library lists a game when its BROADCAST ends (the viewers have just watched it, src/dflsite/broadcast.py `reconcile`);
the playback index lists it when its block's results are RELEASED (the block's `events_at`, src/dflsite/season.py `materialize`), which is later: after the window's slot ends and
the recap begins. So at most positions the library holds some games the playback index does not yet. This tool says, for every game that differs, which of the following it is,
using the independent oracle of the monitor (the plan, the builds and the carry record), not the files:

  both                       in both indexes, and both are due: the normal state
  reveal_delay               in the library only; the broadcast has ended and the results are not due yet: INTENDED, and shows how long the gap is
  not_yet                    in neither; the broadcast has not ended: nothing to list
  early_replay               in the library before the broadcast ended
  early_playback             in the playback index before its results are due
  omission_replay            not in the library although the broadcast has ended
  omission_playback          not in the playback index although its results are due
  playback_without_replay    in the playback index but not in the library (results public for a game that is not replayable)
  unknown                    in an index but in nobody's plan or carry record
  duplicate                  the same (season, game ID) twice in one index

Only reveal_delay is accepted. Reads files; writes nothing, deletes nothing. Positions are checked with the monitor's tolerance (rehearsal_check.TOL_S): a game whose threshold falls within
it of the position is judged only by the side that cannot be wrong.
"""
import argparse
import collections
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, HERE)
import broadcast_season_check as SC     # noqa: E402
from dflsite import broadcast as B, season as S    # noqa: E402

TOL_S = 6.0
ACCEPTED = {"both", "reveal_delay", "not_yet"}


def _games(path):
    try:
        return B.get(path)["games"]
    except FileNotFoundError:
        return []


def key_of(g, default_season):
    return (int(g.get("season", default_season)), g["gid"])


def reconcile(root, public, pos_before=None, pos_after=None, tol_s=TOL_S):
    """-> {"position": [lo, hi], "season": n, "rows": [...], "counts": {...}, "duplicates": [...], "ok": bool}. `rows` lists every key that is not simply `both`/`not_yet`."""
    store = os.path.join(root, "store")
    oracle = SC.FollowingOracle(store)
    cur = oracle._cur
    cpath = os.path.join(store, S.CARRY)
    carry = B.get(cpath) if os.path.exists(cpath) else None
    season = int(carry["season"]) if carry else oracle.first
    if pos_before is None:
        pos_before = B.Clock(store, os.path.join(store, "_unused")).position(time.time())
    # the indexes are read AFTER the first position and BEFORE the second, so a change between the two reads can only be judged by the tolerance
    rep = _games(os.path.join(public, "broadcast", "replays", "index.json"))
    pbk = _games(os.path.join(public, "playback", "index.json"))
    if pos_after is None:
        pos_after = B.Clock(store, os.path.join(store, "_unused")).position(time.time())

    rcount = collections.Counter(key_of(g, season) for g in rep)
    pcount = collections.Counter(key_of(g, season) for g in pbk)
    dups = sorted([("replays", k, n) for k, n in rcount.items() if n > 1] + [("playback", k, n) for k, n in pcount.items() if n > 1])

    # what the oracle says, per key. Earlier seasons are complete: both indexes list all of their games. The season on air is judged by time.
    due = {}                                   # key -> (ended_at, reveal_at) for the season on air
    gfile = {gid: rec["entry"]["file"] for gid, rec in cur.builds.get("games", {}).items() if "entry" in rec}
    reveal_at = {}
    for r in cur.view.reveals:
        if r["dst"].startswith("playback/") and r["dst"] != "playback/index.json":
            reveal_at[r["dst"][len("playback/"):]] = r["at_s"]
    for gid, (w, g) in cur.games.items():
        due[(season, gid)] = (w["start_s"] + g["length_s"] + B.DELAY_S, reveal_at.get(gfile.get(gid)))
    past = set()
    if carry:
        past = {key_of(g, 0) for g in carry["replays"]} | {key_of(g, 0) for g in carry["playback"]}
    keys = set(rcount) | set(pcount) | set(due) | past
    rows, counts = [], collections.Counter()
    for k in sorted(keys):
        in_r, in_p = k in rcount, k in pcount
        if k in past:
            kind = "both" if in_r and in_p else ("omission_replay" if in_p else "omission_playback")
            end = rev = None
        elif k in due:
            end, rev = due[k]
            must_r, may_r = end <= pos_before - tol_s, end <= pos_after + tol_s       # due for sure / allowed
            must_p = rev is not None and rev <= pos_before - tol_s
            may_p = rev is not None and rev <= pos_after + tol_s
            if in_p and not in_r:
                kind = "playback_without_replay"
            elif in_r and not may_r:
                kind = "early_replay"
            elif in_p and not may_p:
                kind = "early_playback"
            elif must_r and not in_r:
                kind = "omission_replay"
            elif must_p and not in_p:
                kind = "omission_playback"
            elif in_r and in_p:
                kind = "both"
            elif in_r:
                kind = "reveal_delay"
            elif not in_r and not in_p:
                kind = "not_yet"
            else:
                kind = "unknown"
        else:
            kind, end, rev = "unknown", None, None
        counts[kind] += 1
        if kind not in ("both", "not_yet"):
            row = {"season": k[0], "gid": k[1], "class": kind, "in_replays": in_r, "in_playback": in_p, "broadcast_ended_at_s": end, "results_due_at_s": rev}
            if kind == "reveal_delay" and end is not None and rev is not None:
                row["gap_s"] = round(rev - end, 1)
                row["results_due_in_s"] = round(rev - pos_after, 1)
            rows.append(row)
    ok = not dups and all(c in ACCEPTED for c in counts)
    return {"position": [round(pos_before, 1), round(pos_after, 1)], "season": season, "replays": len(rep), "playback": len(pbk), "unique_replays": len(rcount), "unique_playback": len(pcount),
            "counts": dict(counts), "duplicates": dups, "rows": rows, "ok": ok}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", required=True)
    ap.add_argument("--public", required=True)
    ap.add_argument("--at-position", type=float, help="judge as of this broadcast position (default: the clock now)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    pos = a.at_position
    rep = reconcile(os.path.abspath(a.root), os.path.abspath(a.public), pos, pos, 0.0 if pos is not None else TOL_S)
    if a.json:
        json.dump(rep, sys.stdout, indent=1, sort_keys=True)
        print()
    else:
        print("position %.0f s, season %d: replay library %d entries (%d unique), playback index %d (%d unique)" % (rep["position"][1], rep["season"], rep["replays"], rep["unique_replays"], rep["playback"], rep["unique_playback"]))
        print("counts: " + ", ".join("%s %d" % kv for kv in sorted(rep["counts"].items())))
        for r in rep["rows"]:
            print("  %-16s season %d  %-28s  library %-5s playback %-5s  ended at %s, results due %s%s" % (r["class"], r["season"], r["gid"], r["in_replays"], r["in_playback"], r["broadcast_ended_at_s"], r["results_due_at_s"],
                                                                                                      ("  (gap %.0f s; due in %.0f s)" % (r["gap_s"], r["results_due_in_s"])) if "gap_s" in r else ""))
        for d in rep["duplicates"]:
            print("  DUPLICATE in %s: %s x%d" % d)
        print("RESULT: %s" % ("every difference is an intended reveal delay" if rep["ok"] else "UNEXPLAINED differences or duplicates"))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
