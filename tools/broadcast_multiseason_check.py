#!/usr/bin/env python3
"""A real built store, two seasons, the daemon in VIRTUAL time across the year boundary (docs/BROADCAST_CONTINUITY.md). Development tool; touches only the folders given.

  broadcast_multiseason_check.py --root R --public P --state S [--step 20] [--past 400] [--report FILE]

R   a private root whose first season is completely built and whose next season was prepared with `broadcast_build.py next-season` and built at least as far as the
    results of its first week. R/store/carry.json is written by the check (the daemon's hand-over); use a COPY of a root. P and S must be new.
It runs the daemon (src/dflsite/season.py, no publication guard, so the clock follows the virtual wall clock whatever the step) over the whole first season, snapshots
the public tree just before the hand-over, runs on into the next season up to `--past` seconds after its first results are public, then checks:
  * every file of the first season (releases, replays, live recordings, playback files, honors parts, every file but the position-dependent ones) is byte-identical
  * the cumulative indexes (playback, replay library, honors) keep every first-season entry, unchanged and in order, and never shrink
  * (season, id) and gid are unique in the indexes although ids repeat between seasons; every playback file matches its entry's season, gid, score and SHA-256
  * nothing of the second season is public before its own time: no second-season release, result or award until the block that reveals it
  * latest.json never moves backwards
Exit 0 and a JSON report if every check passes.
"""
import argparse
import hashlib
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dflsite import broadcast as B  # noqa: E402
from dflsite import season as S  # noqa: E402

VOLATILE = ("broadcast/schedule.json", "broadcast/clock.json", "status.json")


def tree(public):
    out = {}
    for d, _, names in os.walk(public):
        for n in names:
            p = os.path.join(d, n)
            rel = os.path.relpath(p, public)
            if rel in VOLATILE or rel.endswith(".tmp") or "/.tmp-" in rel or os.path.basename(rel).startswith(".tmp-"):
                continue
            with open(p, "rb") as f:
                out[rel] = hashlib.sha256(f.read()).hexdigest()
    return out


def js(public, rel):
    with open(os.path.join(public, rel), "rb") as f:
        return json.loads(f.read())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", required=True)
    ap.add_argument("--public", required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--step", type=float, default=20.0)
    ap.add_argument("--past", type=float, default=400.0)
    ap.add_argument("--report")
    a = ap.parse_args(argv)
    store = os.path.join(os.path.abspath(a.root), "store")
    for d in (a.public, a.state):
        if os.path.exists(d) and os.listdir(d):
            raise SystemExit("%s must be new" % d)
        os.makedirs(d, exist_ok=True)
    B.FSYNC = False
    t0 = 1_000_000.0
    bc = S.SeasonBroadcaster(store, a.public, state_dir=a.state, guard_publication=False)
    plan1 = bc.plan
    view1 = bc.view()
    if not view1.complete:
        raise SystemExit("the first season is not completely built (the check needs the whole season on air)")
    end1 = view1.end_s
    bc.clock.start(t0, 0.0, B.LEASE_S)
    problems, notes = [], {}

    def check(ok, msg):
        if not ok:
            problems.append(msg)

    started = time.time()
    t = 0.0
    snap = None
    last_events = 0
    while t < end1 - a.step:
        bc.tick(t0 + t)
        t += a.step
        ev = int(js(a.public, "latest.json")["release"][1:6]) if os.path.exists(os.path.join(a.public, "latest.json")) else 0
        check(ev >= last_events, "latest.json moved backwards at %d s" % t)
        last_events = ev
    bc.tick(t0 + end1 - 1)
    notes["season1_wall_s"] = round(time.time() - started, 1)
    snap = tree(a.public)
    idx1 = js(a.public, "playback/index.json")
    rep1 = js(a.public, "broadcast/replays/index.json")
    hon1 = js(a.public, "broadcast/honors/index.json")
    notes.update(files_at_end_of_season1=len(snap), games_season1=len(idx1["games"]), honors_parts_season1=len(hon1["parts"]))
    check(not os.path.exists(os.path.join(store, S.CARRY)), "the carry record exists before the boundary")
    # the second season
    nd = bc._next_dir()
    b2 = B.get(os.path.join(nd, S.BUILDS))
    p2 = B.get(os.path.join(nd, S.PLAN))
    seq_keys = {int(k): v["key"] for k, v in b2["events"].items()}
    notes["season2_events_built"] = len(seq_keys)
    gids2 = sorted(b2["games"])
    tokens2 = [b2["events"][k]["release"] for k in b2["events"]]
    first_reveal = None
    t = end1
    last_n = len(idx1["games"])
    stop_after = None
    while True:
        bc.tick(t0 + t)
        pos_v = bc.view()
        ev = int(js(a.public, "latest.json")["release"][1:6])
        check(ev >= last_events, "latest.json moved backwards at %d s" % t)
        last_events = ev
        # second-season releases and results exist publicly only once the latest pointer says so
        if os.path.exists(os.path.join(a.public, "playback/index.json")):
            ix = js(a.public, "playback/index.json")
            check(len(ix["games"]) >= last_n, "playback index shrank at %d s" % t)
            last_n = len(ix["games"])
            if first_reveal is None and any(e["season"] == plan1["season"] + 1 for e in ix["games"]):
                first_reveal = t
                stop_after = t + a.past
        if stop_after is not None and t >= stop_after:
            break
        if pos_v.horizon is not None and bc.clock.position(t0 + t) >= pos_v.horizon and first_reveal is None and t > end1 + 3600 * 6:
            problems.append("the clock is held at the builder's horizon before the second season's first results (%s): build more events" % pos_v.horizon_why)
            break
        t += a.step
        if t > end1 + p2["total_s"]:
            problems.append("ran past the end of the second season without its first results")
            break
    notes["boundary_s"] = end1
    notes["season2_first_results_s"] = first_reveal
    # before the boundary and after: the first season is untouched
    after = tree(a.public)
    changed = [k for k, v in snap.items() if after.get(k) != v and not k.endswith("index.json") and k != "latest.json"]
    check(not changed, "first-season files changed or vanished: %s" % changed[:5])
    idx = js(a.public, "playback/index.json")
    check([e for e in idx["games"] if e["season"] == plan1["season"]] == idx1["games"], "first-season playback entries changed")
    rep = js(a.public, "broadcast/replays/index.json")
    check([g for g in rep["games"] if g["season"] == plan1["season"]] == rep1["games"], "first-season replay-library entries changed")
    hon = js(a.public, "broadcast/honors/index.json")
    check([p for p in hon["parts"] if p["season"] == plan1["season"]] == hon1["parts"], "first-season honors entries changed")
    ids = [(e["season"], e["id"]) for e in idx["games"]]
    check(len(set(ids)) == len(ids) and len({e["gid"] for e in idx["games"]}) == len(idx["games"]), "(season, id) or gid not unique")
    notes["ids_repeated_between_seasons"] = len({i for _, i in ids}) < len(ids)
    bad = 0
    for e in idx["games"]:
        with open(os.path.join(a.public, "playback", e["file"]), "rb") as f:
            raw = f.read()
        doc = json.loads(raw)
        if (doc["gid"], doc["season"], doc["id"]) != (e["gid"], e["season"], e["id"]) or doc["final"] != {"away": e["away_score"], "home": e["home_score"]} \
                or hashlib.sha256(raw).hexdigest() != e["sha256"]:
            bad += 1
    check(bad == 0, "%d playback files do not match their index entry" % bad)
    notes.update(games_public_at_end=len(idx["games"]), games_season2_public=sum(1 for e in idx["games"] if e["season"] != plan1["season"]),
                 replays_public=len(rep["games"]), honors_parts_public=len(hon["parts"]), carry=os.path.exists(os.path.join(store, S.CARRY)))
    check(notes["carry"], "no hand-over happened")
    # a second-season result must not be public before its reveal
    future = [k for k in b2["events"] if int(k) > last_events]
    leak = []
    for k in future:
        rel = b2["events"][k]["release"]
        if os.path.exists(os.path.join(a.public, "releases", rel)):
            leak.append(rel)
    check(not leak, "releases not yet due are public: %s" % leak[:3])
    future_games = [g for g, r in b2["games"].items() if int(r["seq"]) > last_events]
    check(not any(os.path.exists(os.path.join(a.public, "playback", e["file"])) for g, r in b2["games"].items() if int(r["seq"]) > last_events for e in [r["entry"]]),
          "a replay file of an event not yet revealed is public")
    notes["future_season2_games_checked"] = len(future_games)
    rep_out = {"ok": not problems, "problems": problems, "notes": notes, "wall_s": round(time.time() - started, 1), "season": plan1["season"]}
    if a.report:
        with open(a.report, "w") as f:
            json.dump(rep_out, f, indent=1, sort_keys=True)
    print(json.dumps(rep_out, sort_keys=True))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
