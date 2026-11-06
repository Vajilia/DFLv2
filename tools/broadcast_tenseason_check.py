#!/usr/bin/env python3
"""Bounded many-season continuity test in VIRTUAL time, with measurements (docs/BROADCAST_CONTINUITY.md). Development tool; touches only the folder given.

  broadcast_tenseason_check.py --workdir D [--seasons 10] [--step 5] [--kill-at 2,5,9] [--report FILE]

D must be new. It builds N synthetic seasons (the checked-in playback fixture under fresh season identities; every season REPEATS the same game ids with other scores,
four events a season) into one private store, then runs the real daemon (src/dflsite/season.py, unguarded virtual clock) through all of them. At every boundary it checks
  * every public file seen at an earlier boundary is still byte-identical at the end (the playback, replay-library and Honors indexes and latest.json excepted, which
    are compared entry by entry instead)
  * the cumulative indexes hold every earlier season's entries, unchanged and in order, and never shrink
  * (season, id) and gid are unique although ids repeat in every season; every playback file equals its entry (season, gid, score, SHA-256)
  * nothing of a later season is public before its own time (no result marker, no Honors, no playback file, no replay-library entry of a season not yet on air)
  * latest.json never moves back and the clock never jumps
and measures, at each boundary: resident memory of the daemon process, index and carry-record sizes, public tree size and file count, and the time of a tick.
Then, for each season boundary named in --kill-at, it runs the same virtual timeline again with the daemon killed and restarted around that boundary (40 s before, 1 s
after and 700 s after) and requires the final public tree to equal the uninterrupted run's. Exit 0 and a JSON report when every check passes.
"""
import argparse
import hashlib
import json
import os
import shutil
import statistics
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
from dflsite import broadcast as B  # noqa: E402
from dflsite import season as S  # noqa: E402
import test_multiseason as M  # noqa: E402  (the synthetic season builder; its constants are the timeline)

T0 = 1000.0
VOLATILE = ("broadcast/schedule.json", "broadcast/clock.json", "status.json", "latest.json")
INDEXES = ("playback/index.json", "broadcast/replays/index.json", "broadcast/honors/index.json")


def rss_kb():
    out = {}
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith(("VmRSS", "VmHWM")):
                out[line.split(":")[0]] = int(line.split()[1])
    return out


def tree(public):
    out = {}
    for d, _, names in os.walk(public):
        for n in names:
            p = os.path.join(d, n)
            rel = os.path.relpath(p, public)
            if n.startswith(".tmp-") or rel.endswith(".tmp"):
                continue
            with open(p, "rb") as f:
                out[rel] = hashlib.sha256(f.read()).hexdigest()
    return out


def all_text(public):
    out = []
    for d, _, names in os.walk(public):
        for n in names:
            with open(os.path.join(d, n), "rb") as f:
                out.append(f.read().decode("utf-8", "replace"))
    return "\n".join(out)


def du(path):
    n = b = 0
    for d, _, names in os.walk(path):
        for x in names:
            n += 1
            b += os.path.getsize(os.path.join(d, x))
    return n, b


def js(public, rel):
    with open(os.path.join(public, rel), "rb") as f:
        return json.loads(f.read())


def make_store(base, seasons):
    store = os.path.join(base, "store")
    years = []
    for i in range(seasons):
        y = M.Year(store, 901 + i, 4 * i, rooted=(i == 0))
        y.build(4)
        years.append(y)
    return store, years


def season_len(years):
    return [y.plan["total_s"] for y in years]


class Timeline:
    """Boundary positions: season k ends at sum(total_s[:k])."""

    def __init__(self, years):
        self.ends, t = [], 0
        for y in years:
            t += y.plan["total_s"]
            self.ends.append(t)


def run(bc, a, b, step, hook=None):
    t = a
    while t <= b:
        bc.tick(T0 + t)
        if hook:
            hook(t)
        t += step


def uninterrupted(base, seasons, step, report=None):
    store, years = make_store(base, seasons)
    public = os.path.join(base, "public")
    B.FSYNC = False
    bc = S.SeasonBroadcaster(store, public, guard_publication=False)
    bc.clock.start(T0, 0.0, B.LEASE_S)
    tl = Timeline(years)
    problems, notes = [], {"boundaries": []}
    seen = {}
    prev_entries = {}
    last_ev = [0]
    tick_times = []
    mem0 = rss_kb()
    notes["rss_start_kb"] = mem0

    def check(ok, msg):
        if not ok:
            problems.append(msg)

    def sanity(t):
        # latest never moves back; no later season visible early
        if os.path.exists(os.path.join(public, "latest.json")):
            ev = int(js(public, "latest.json")["release"][1:6])
            check(ev >= last_ev[0], "latest.json moved backwards at %d s" % t)
            last_ev[0] = ev

    def on_air(t):
        for k, e in enumerate(tl.ends):
            if t < e:
                return k + 1
        return len(tl.ends)

    def boundary(k, t):
        """just before season k hands over (t = its end - 1 tick): check, snapshot and measure."""
        tr = tree(public)
        for rel, h in tr.items():
            if rel in VOLATILE or rel in INDEXES:
                continue
            if rel in seen and seen[rel] != h:
                problems.append("%s changed after the season-%d boundary" % (rel, k))
            seen.setdefault(rel, h)
        idx = js(public, "playback/index.json")["games"]
        rep = js(public, "broadcast/replays/index.json")["games"]
        hon = js(public, "broadcast/honors/index.json")["parts"]
        ids = [(e["season"], e["id"]) for e in idx]
        check(len(set(ids)) == len(ids) and len({e["gid"] for e in idx}) == len(idx), "(season, id) or gid not unique at boundary %d" % k)
        check(len(idx) == 3 * k, "boundary %d: %d playback entries, expected %d" % (k, len(idx), 3 * k))
        check(len(rep) == 3 * k, "boundary %d: %d replay-library entries, expected %d" % (k, len(rep), 3 * k))
        check(len(hon) == k, "boundary %d: %d honors parts, expected %d" % (k, len(hon), k))
        for name, cur, key in (("playback", idx, "gid"), ("replays", rep, "gid"), ("honors", hon, "season")):
            old = prev_entries.get(name, [])
            check(all(o in cur for o in old), "%s index lost or changed an earlier entry at boundary %d" % (name, k))
            prev_entries[name] = cur
        bad = 0
        for e in idx:
            with open(os.path.join(public, "playback", e["file"]), "rb") as f:
                raw = f.read()
            doc = json.loads(raw)
            if (doc["gid"], doc["season"], doc["id"]) != (e["gid"], e["season"], e["id"]) or hashlib.sha256(raw).hexdigest() != e["sha256"] \
                    or doc["final"] != {"away": e["away_score"], "home": e["home_score"]}:
                bad += 1
        check(bad == 0, "%d playback files do not match their entry at boundary %d" % (bad, k))
        nfiles, nbytes = du(public)
        notes["boundaries"].append({
            "season_done": 901 + k - 1, "at_s": t, "public_files": nfiles, "public_bytes": nbytes,
            "playback_index_bytes": os.path.getsize(os.path.join(public, INDEXES[0])), "replay_index_bytes": os.path.getsize(os.path.join(public, INDEXES[1])),
            "honors_index_bytes": os.path.getsize(os.path.join(public, INDEXES[2])),
            "carry_bytes": os.path.getsize(os.path.join(store, S.CARRY)) if os.path.exists(os.path.join(store, S.CARRY)) else 0,
            "rss_kb": rss_kb(), "tick_ms_mean": round(1000 * statistics.mean(tick_times[-200:]), 2) if tick_times else None,
            "tick_ms_max": round(1000 * max(tick_times[-2000:]), 1) if tick_times else None})

    started = time.time()
    t = 0
    k = 1
    final_t = tl.ends[-1] + 300
    while t <= final_t:
        a = time.perf_counter()
        bc.tick(T0 + t)
        tick_times.append(time.perf_counter() - a)
        sanity(t)
        # the season on air is never behind the clock, and no later season is public
        if t % (step * 24) == 0 or (k <= len(tl.ends) and t >= tl.ends[k - 1] - step):
            cur = on_air(t)
            idx = js(public, "playback/index.json")["games"] if os.path.exists(os.path.join(public, "playback/index.json")) else []
            late = [e for e in idx if e["season"] > 900 + cur]
            check(not late, "a season not yet on air is in the results index at %d s: %s" % (t, late[:1]))
            if os.path.exists(os.path.join(public, "broadcast/honors/index.json")):
                hp = js(public, "broadcast/honors/index.json")["parts"]
                check(not [p for p in hp if p["season"] > 900 + cur], "honors of a season not yet on air at %d s" % t)
        if k <= len(tl.ends) and t >= tl.ends[k - 1] - step and t < tl.ends[k - 1]:
            boundary(k, t)
            if k < len(tl.ends):                    # nothing of season k+1 (events 4k+1 to 4k+4, its Honors) may be public yet
                text = all_text(public)
                for n in range(1, 5):
                    check("RESULT-OF-EVENT-%d" % (4 * k + n) not in text, "RESULT-OF-EVENT-%d public before season %d begins" % (4 * k + n, 901 + k))
                check("HONORS-OF-%d" % (901 + k) not in text, "honors of season %d public before it begins" % (901 + k))
            k += 1
        t += step
    notes["wall_s"] = round(time.time() - started, 1)
    notes["virtual_s"] = final_t
    notes["tick_ms_overall_mean"] = round(1000 * statistics.mean(tick_times), 2)
    notes["tick_ms_overall_max"] = round(1000 * max(tick_times), 1)
    notes["rss_end_kb"] = rss_kb()
    # final state: every season on air in order, indexes complete
    idx = js(public, "playback/index.json")["games"]
    hon = js(public, "broadcast/honors/index.json")["parts"]
    rep = js(public, "broadcast/replays/index.json")["games"]
    check(len(idx) == 3 * seasons and len(rep) == 3 * seasons and len(hon) == seasons, "final indexes incomplete: %d/%d/%d" % (len(idx), len(rep), len(hon)))
    check(len({(e["season"], e["id"]) for e in idx}) == len(idx) and len({e["id"] for e in idx}) == 3, "identities: ids must repeat in every season and (season, id) stay unique")
    final = tree(public)
    for rel, h in seen.items():
        check(final.get(rel) == h, "%s changed or vanished by the end" % rel)
    carry = B.get(os.path.join(store, S.CARRY))
    notes["carry_final"] = {"season": carry["season"], "seasons": len(carry["seasons"]), "offset_s": carry["offset_s"], "bytes": os.path.getsize(os.path.join(store, S.CARRY))}
    nfiles, nbytes = du(public)
    notes.update(public_files=nfiles, public_bytes=nbytes, store_bytes=du(store)[1])
    return final, problems, notes


def killed(base, seasons, step, boundary_k, ref, final_t):
    """The same timeline with the daemon killed and restarted around the boundary that ends season `boundary_k`."""
    store, years = make_store(base, seasons)
    public = os.path.join(base, "public")
    tl = Timeline(years)
    B.FSYNC = False
    out = {}
    end = tl.ends[boundary_k - 1]
    for dt in (-40, 1, 700):
        shutil.rmtree(public, ignore_errors=True)
        for f in (S.CARRY,):
            p = os.path.join(store, f)
            if os.path.exists(p):
                os.unlink(p)
        for f in os.listdir(store):
            if f.startswith("clock") or f in ("clock.json",) or f.endswith(".log"):
                try:
                    os.unlink(os.path.join(store, f))
                except OSError:
                    pass
        bc = S.SeasonBroadcaster(store, public, guard_publication=False)
        bc.clock.start(T0, 0.0, B.LEASE_S)
        run(bc, 0, end + dt, step)
        bc2 = S.SeasonBroadcaster(store, public, guard_publication=False)          # a new process after a gap: the viewers' clock stood still for 400 s
        gap = 400
        t = end + dt + step
        while t <= final_t:
            bc2.tick(T0 + t + gap)
            t += step
        got = tree(public)
        diff = [k for k in set(ref) | set(got) if ref.get(k) != got.get(k) and k not in VOLATILE]
        out["season%d_boundary%+d" % (900 + boundary_k, dt)] = {"differences": sorted(diff)[:5], "n": len(diff)}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--seasons", type=int, default=10)
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--kill-at", default="")
    ap.add_argument("--report")
    a = ap.parse_args(argv)
    if os.path.exists(a.workdir) and os.listdir(a.workdir):
        raise SystemExit("%s must be new" % a.workdir)
    os.makedirs(a.workdir, exist_ok=True)
    base = os.path.join(a.workdir, "main")
    os.makedirs(base)
    final, problems, notes = uninterrupted(base, a.seasons, a.step)
    rec = {}
    final_t = notes["virtual_s"]
    for k in [int(x) for x in a.kill_at.split(",") if x]:
        b = os.path.join(a.workdir, "kill%d" % k)
        os.makedirs(b)
        res = killed(b, a.seasons, a.step, k, final, final_t)
        rec.update(res)
        for name, r in res.items():
            if r["n"]:
                problems.append("restart %s: final tree differs in %d files, e.g. %s" % (name, r["n"], r["differences"]))
        shutil.rmtree(b, ignore_errors=True)
    out = {"ok": not problems, "problems": problems, "notes": notes, "restarts": rec, "seasons": a.seasons}
    if a.report:
        with open(a.report, "w") as f:
            json.dump(out, f, indent=1, sort_keys=True)
    print(json.dumps({"ok": out["ok"], "problems": problems[:10], "wall_s": notes["wall_s"], "public_bytes": notes["public_bytes"]}))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
