#!/usr/bin/env python3
"""The PRIVATE per-event builder of a broadcast season (docs/BROADCAST_PLAN.md, "Season integration").

  broadcast_build.py init   --root R --seed N [--league-name NAME]      a fresh isolated league, the season plan, the event-0 feed release
  broadcast_build.py step   --root R [--events K]                        fire and build the next K events (default: all up to the end of the year)
  broadcast_build.py next-season --root R                                prepare the next season (store/seasons/sNNN/) once the current one is completely built
  broadcast_build.py status --root R

Everything it makes is PRIVATE. Under the root R:

  league/       the isolated development league (league.db, its archive) and its runner's control directory (ctl/); never a production league
  store/        the broadcast store: plan.json, builds.json, feed/ (a private feed root: one release and one pointer per event), playback/ (full replay
                files), games/ (live recordings), honors/ (parts), the clock and its log
  build.log.jsonl   what each event cost

The PUBLIC directory is a different folder that this program never receives, never writes and never derives a path into: only the daemon
(tools/broadcast_run.py) copies a finished piece of the store into it, when the broadcast clock reaches the time it belongs to. A copy of R is therefore
a copy of the future; a copy of the public directory is a copy of the past.

For each event N it does, in order, and each step leaves the store consistent:
  1. fires event N with the league runner (`--until-events N`), the one runner, one event at a time (the simulation is exactly the runner's, byte for byte);
  2. publishes the feed for N into the PRIVATE feed root with tools/export_public_feed.py, and keeps that release's pointer under its own name;
  3. exports the full replay of every game of N with tools/export_playback.py (into the private playback folder) and builds each game's live recording;
  4. at the Ambassador Bowl and at the year close, extracts the honours recorded by the engine (src/dflsite/honors.py);
  5. only then rewrites builds.json (atomically): the daemon sees an event only when everything belonging to it is on disk.
A builder killed anywhere restarts from the last builds.json. It never touches the engine, `src/dfl` or `data`.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from dflsite import broadcast as B  # noqa: E402
from dflsite import season as S  # noqa: E402

PY = sys.executable


def paths(root):
    root = os.path.abspath(root)
    P = {"root": root, "league": os.path.join(root, "league", "league.db"), "ctl": os.path.join(root, "league", "ctl"), "store": os.path.join(root, "store"),
         "feed": os.path.join(root, "store", "feed"), "playback": os.path.join(root, "store", "playback"), "honors": os.path.join(root, "store", "honors"),
         "log": os.path.join(root, "build.log.jsonl")}
    P["bdir"], P["season_n"] = target(P["store"])
    return P


def target(store):
    """The season being built: the folder holding its plan and builds, and its number. The first season's are at the store root; each later season's are in
    store/seasons/sNNN/ (made by `next-season`). The builder always works on the highest one."""
    best, n = store, 1
    d = os.path.join(store, S.SEASONS)
    if os.path.isdir(d):
        for name in sorted(os.listdir(d)):
            if len(name) == 4 and name[0] == "s" and name[1:].isdigit() and os.path.exists(os.path.join(d, name, S.PLAN)) and int(name[1:]) > n:
                best, n = os.path.join(d, name), int(name[1:])
    return best, n


def run(args, what):
    p = subprocess.run([PY] + args, capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit("%s failed (%d): %s" % (what, p.returncode, (p.stderr or p.stdout)[-600:]))
    return p.stdout


def load_builds(P):
    path = os.path.join(P["bdir"], S.BUILDS)
    if os.path.exists(path):
        return B.get(path)
    return {"format": S.BUILDS_FORMAT, "format_version": 1, "events": {}, "games": {}, "honors": {}, "league_id": None}


def league_events(P):
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from dflsite import status as ST
    return ST.league_events(P["league"])


def event_key(P, seq):
    """The engine's own name for event `seq`, from the runner's private event log."""
    with open(os.path.join(P["ctl"], "events.jsonl"), encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec["seq"] == seq:
                return rec["key"]
    raise SystemExit("event %d is not in the runner's log" % seq)


def export_feed(P, seq):
    """Publish the league's present state (event `seq`) into the PRIVATE feed root; return (release, pointer path inside the store)."""
    out = json.loads(run([os.path.join(ROOT, "tools", "export_public_feed.py"), P["league"], "--out", P["feed"], "--keep", "200", "--no-status",
                          "--runner-status", os.path.join(P["ctl"], "runner_status.json")], "feed export").strip().splitlines()[-1])
    if out.get("events") != seq:
        raise SystemExit("the exported feed has %s events, expected %d (the league moved?)" % (out.get("events"), seq))
    ptr_dir = os.path.join(P["feed"], "pointers")
    os.makedirs(ptr_dir, exist_ok=True)
    name = "e%05d.json" % seq
    shutil.copyfile(os.path.join(P["feed"], "latest.json"), os.path.join(ptr_dir, name))
    return out["release"], "feed/pointers/" + name


def build_games(P, builds, seq, key, slot_s, season=None):
    """Export every game of event `seq` that is not yet recorded and build its live recording in the store. Returns the new gids.
    The export covers the league's whole archive, so only this season's games are taken (an earlier season's belong to its own builds)."""
    run([os.path.join(ROOT, "tools", "export_playback.py"), "--archive", P["league"] + ".archive", "--feed", P["feed"], "--out", P["playback"]], "playback export")
    idx = B.get(os.path.join(P["playback"], "index.json"))
    new = []
    for e in idx["games"]:
        if e["gid"] in builds["games"] or (season is not None and int(e["season"]) != int(season)):
            continue
        path = os.path.join(P["playback"], e["file"])
        raw = open(path, "rb").read()
        game = json.loads(raw)
        rec = B.store_game(P["store"], game, slot_s, replay_name=os.path.basename(e["file"]))
        with open(os.path.join(P["store"], "games", B.safe(e["gid"]), "replay.json"), "wb") as f:     # the published file, byte for byte
            f.write(raw)
        builds["games"][e["gid"]] = {"seq": seq, "key": key, "away": game["teams"]["away"]["abbr"], "home": game["teams"]["home"]["abbr"], "segments": rec["segments"],
                                     "length_s": rec["length_s"], "delay_s": B.DELAY_S, "replay": rec["replay"], "entry": e}
        new.append(e["gid"])
    builds["league_id"] = idx.get("league_id") or builds.get("league_id")
    return new


def build_honors(P, builds, key, plan, teams):
    parts = [p["part"] for it in plan["items"] if it["kind"] == "block" for p in it["parts"] if p["event"] == key]
    if not parts:
        return []
    from dflsite import honors as H
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from dfl import persist
    from dfl.engine.production import tables
    rules = {"hall_min_seasons": int(tables()["a"]["hall_min_seasons"]), "fan_candidates": int(tables()["a"]["fan_candidates"])}
    tmp = tempfile.mkdtemp(prefix="dfl-honors-")
    try:
        copy = os.path.join(tmp, "league.db")
        persist.backup(P["league"], copy)
        r = persist.Runner.open(copy, heal=False, with_archive=False)
        try:
            for part in parts:
                doc = H.build_part(r.u, part, plan["season"], teams, rules)
                H.check_public(doc)
                rel = "s%03d/%s.json" % (plan["season"], part)
                B.put(os.path.join(P["honors"], rel), doc)
                builds["honors"][part] = rel
        finally:
            r.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return parts


def teams_of(P, release):
    rel = os.path.join(P["feed"], "releases", release)
    return {t["city"]: {"abbr": t["abbr"], "name": t["full_name"]} for t in B.get(os.path.join(rel, "teams.json"))["teams"]}


def cmd_init(a):
    P = paths(a.root)
    if os.path.exists(P["league"]) or os.path.exists(P["store"]):
        raise SystemExit("%s already holds a league or a store; choose a new folder" % P["root"])
    for d in (os.path.dirname(P["league"]), P["ctl"], P["store"]):
        os.makedirs(d, exist_ok=True)
    run([os.path.join(ROOT, "tools", "league_store.py"), "init", P["league"], "--seed", str(a.seed)], "league init")
    import broadcast_plan as BP
    plan = BP.build_plan(season=1)
    B.put(os.path.join(P["store"], S.PLAN), plan)
    release, ptr = export_feed(P, 0)
    builds = load_builds(P)
    builds["genesis"] = {"release": release, "events": 0, "pointer": ptr}
    B.put(os.path.join(P["bdir"], S.BUILDS), builds)
    print(json.dumps({"root": P["root"], "genesis": release, "events_in_plan": len(plan["events"])}))
    return 0


def step_one(P, plan, slot_s):
    builds = load_builds(P)
    base = int(plan.get("event_base", 0))                      # league events of the seasons before this one
    done = max([int(k) for k in builds["events"]] or [base])
    seq = done + 1
    t0 = time.time()
    have = league_events(P)
    if have == done:
        run([os.path.join(ROOT, "tools", "league_runner.py"), P["league"], "--until-events", str(seq), "--control-dir", P["ctl"]], "league runner")
    elif have != seq:
        raise SystemExit("the league has %d events but %d are built: the builder fires the league one event at a time and nothing else may" % (have, done))
    t1 = time.time()
    key = event_key(P, seq)
    if key != plan["events"][seq - base - 1]:
        raise SystemExit("event %d is %r but the plan expects %r" % (seq, key, plan["events"][seq - base - 1]))
    release, ptr = export_feed(P, seq)
    t2 = time.time()
    new = build_games(P, builds, seq, key, slot_s, plan["season"])
    t3 = time.time()
    parts = build_honors(P, builds, key, plan, teams_of(P, release))
    t4 = time.time()
    builds["events"][str(seq)] = {"key": key, "release": release, "events": seq, "pointer": ptr}
    B.put(os.path.join(P["bdir"], S.BUILDS), builds)
    rec = {"seq": seq, "key": key, "release": release, "games": len(new), "honors": parts,
           "seconds": {"league": round(t1 - t0, 1), "feed": round(t2 - t1, 1), "games": round(t3 - t2, 1), "honors": round(t4 - t3, 1)}, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    with open(P["log"], "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
    print(json.dumps(rec, sort_keys=True), flush=True)
    return seq


def cmd_step(a):
    P = paths(a.root)
    plan = B.get(os.path.join(P["bdir"], S.PLAN))
    slot = plan["params"]["slot_s"]
    base = int(plan.get("event_base", 0))
    n = 0
    while True:
        done = max([int(k) for k in load_builds(P)["events"]] or [base])
        if done >= base + len(plan["events"]) or (a.events is not None and n >= a.events):
            break
        step_one(P, plan, slot)
        n += 1
    return 0


def cmd_next_season(a):
    """Prepare the next season in store/seasons/sNNN/ once the one being built is complete. The league has fired exactly the events built so far, so the next
    season's first event is the league's next one; its plan carries the league's own event numbers (event_base). Nothing of it is public: only the daemon, at the
    end of the season on air, starts it (src/dflsite/season.py)."""
    P = paths(a.root)
    plan = B.get(os.path.join(P["bdir"], S.PLAN))
    builds = load_builds(P)
    base = int(plan.get("event_base", 0))
    done = max([int(k) for k in builds["events"]] or [base])
    if done < base + len(plan["events"]):
        raise SystemExit("season %d is not completely built (%d of %d events): build it first" % (plan["season"], done - base, len(plan["events"])))
    if league_events(P) != done:
        raise SystemExit("the league has %d events but %d are built" % (league_events(P), done))
    n = int(plan["season"]) + 1
    d = os.path.join(P["store"], S.SEASONS, "s%03d" % n)
    if os.path.exists(d):
        raise SystemExit("season %d is already prepared: %s" % (n, d))
    import broadcast_plan as BP
    os.makedirs(d)
    B.put(os.path.join(d, S.PLAN), BP.build_plan(season=n, event_base=done))
    B.put(os.path.join(d, S.BUILDS), {"format": S.BUILDS_FORMAT, "format_version": 1, "events": {}, "games": {}, "honors": {}, "league_id": builds.get("league_id")})
    print(json.dumps({"season": n, "dir": d, "event_base": done}))
    return 0


def cmd_status(a):
    P = paths(a.root)
    b = load_builds(P)
    print(json.dumps({"season": P["season_n"], "events_built": len(b["events"]), "games_built": len(b["games"]), "honors": sorted(b["honors"])}))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--root", required=True)
    p.add_argument("--seed", type=int, required=True)
    p.set_defaults(fn=cmd_init)
    p = sub.add_parser("step")
    p.add_argument("--root", required=True)
    p.add_argument("--events", type=int)
    p.set_defaults(fn=cmd_step)
    p = sub.add_parser("next-season")
    p.add_argument("--root", required=True)
    p.set_defaults(fn=cmd_next_season)
    p = sub.add_parser("status")
    p.add_argument("--root", required=True)
    p.set_defaults(fn=cmd_status)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
