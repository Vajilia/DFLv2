#!/usr/bin/env python3
"""Direct measurements of what viewers depend on, taken from the daemon's own files (docs/MEMORY_MONITOR_VALIDATION_CRITERIA.md, sections 3 and 5).

  broadcast_probe.py --store DIR --out FILE.jsonl [--interval 1] [--seconds N]     sample the broadcast clock once a second (read-only)

Everything else in this module is pure: it judges those samples, the live-segment files' modification times and the clock log, with the PINNED tree's own clock arithmetic
(`dflsite.broadcast.position` / `clock_limit`), so the harness never keeps a second copy of the rule. Nothing here changes the code under test or the daemon's cgroup.

What is measured, and the design fact each check rests on (all from the pinned tree):
  * clock stand-still (DIAGNOSTIC, not a gate)  after each tick the clock may run at most DELAY_S ahead (`ready_until = position + DELAY_S`); a later tick stops it, which is the design's
                   protection against running viewers past what is published. Observed as: the clock is running, not paused, and the wall time is more than one sampling interval past
                   `clock_limit(state)`. Tick age (file age of clock.json) is reported alongside. Neither decides pass or fail.
  * lease          the clock stops when `lease_until` passes (seen directly in the samples); a failed publication is logged by the daemon as `publish_failure`. The daemon's `downtime` entry records ANY
                   stand-still past `clock_limit`, whatever its cause, so it is counted under the stall check, not as a lease lapse.
  * publication    segment n of a game is needed by viewers when the clock reaches `start + n * SEG_S + DELAY_S`; its file must exist by then.
  * errors         a publication failure is logged by the daemon as JSON with an `error` key.
"""
import argparse
import importlib
import json
import os
import re
import signal
import sys
import time

TOL_S = 1.0                                   # the resolution of the observation (the sampling interval), not a tolerance on the design
BAD_WHY = ("publish_failure",)                # the daemon also logs `downtime` for ANY stand-still past clock_limit (a late tick included): that is B1's subject, reported but not a lease lapse


def load_broadcast(tree):
    """The broadcast module of the tree under test."""
    p = os.path.join(tree, "src")
    if p not in sys.path:
        sys.path.insert(0, p)
    return importlib.import_module("dflsite.broadcast")


# ---------------------------------------------------------------------------------------------------------------------------------- sampling
def sample(store, now):
    rec = {"t": round(now, 3), "paused_file": os.path.exists(os.path.join(store, "PAUSE")), "state": None}
    path = os.path.join(store, "clock.json")
    try:
        rec["mtime"] = round(os.stat(path).st_mtime, 3)
        with open(path) as f:
            rec["state"] = json.load(f)
    except (OSError, ValueError):
        pass
    return rec


def scan_live(public):
    """{game folder: {"manifest": dict or None, "segments": {n: mtime}}} for every live game folder in the public tree."""
    base = os.path.join(public, "broadcast", "live")
    out = {}
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return out
    for name in names:
        d = os.path.join(base, name)
        if not os.path.isdir(d):
            continue
        segs, man = {}, None
        for fn in os.listdir(d):
            m = re.match(r"s(\d{4})\.json$", fn)
            if m:
                try:
                    segs[int(m.group(1))] = os.stat(os.path.join(d, fn)).st_mtime
                except OSError:
                    pass
        try:
            with open(os.path.join(d, "manifest.json")) as f:
                man = json.load(f)
        except (OSError, ValueError):
            pass
        out[name] = {"manifest": man, "segments": segs}
    return out


def first_window(plan, builds, delay_s=3, seg_s=5):
    """The first live window of a plan: when it starts, the broadcast position by which all its games have ended and their final files are out, where the next window starts, and
    {game id: segments}. None if the plan has no window."""
    wins = sorted((i for i in plan.get("items", []) if i.get("kind") == "window"), key=lambda i: i["start_s"])
    if not wins:
        return None
    w = wins[0]
    games = {gid: rec for gid, rec in (builds.get("games") or {}).items() if (plan.get("games") or {}).get(gid.split("/", 1)[-1]) == w["key"]}
    if not games:
        return None
    length = max(rec["length_s"] for rec in games.values())
    return {"start_s": w["start_s"], "end_pos": w["start_s"] + length + delay_s + seg_s, "next_start_s": wins[1]["start_s"] if len(wins) > 1 else float("inf"),
            "segments": {gid: rec["segments"] for gid, rec in games.items()}}


# ---------------------------------------------------------------------------------------------------------------------------------- pure judging
def running(s):
    st = s.get("state")
    return bool(st) and st.get("state") == "running" and not s.get("paused_file")


def pct(values, q):
    v = sorted(values)
    return v[min(len(v) - 1, int(q * len(v)))] if v else None


def wall_at(B, samples, P):
    """The wall time at which the clock reached broadcast position P, from the samples; None if it did not."""
    prev = None
    for s in samples:
        if not s.get("state"):
            prev = None
            continue
        if B.position(s["state"], s["t"]) >= P - 1e-9:
            if prev is not None and prev["state"].get("state") == "running":
                t = prev["state"]["anchor_wall"] + (P - prev["state"]["anchor_pos"])
                if prev["t"] <= t <= s["t"]:
                    return t
            return s["t"]
        prev = s
    return None


def clock_rows(prefix, B, samples, clock_log, t0, t1):
    """The clock stand-still diagnostic (never gates) and B2 (lease) for the samples of one phase. -> rows."""
    ss = [s for s in samples if t0 <= s["t"] <= t1]
    if not ss or not all(s.get("state") for s in ss):
        return [(prefix + ".clock_stall", "invalid", "the clock could not be read for the whole phase (%d samples)" % len(ss), None), (prefix + ".lease", "invalid", "no clock samples", None)]
    idle = [s["t"] for s in ss if not running(s)]
    stalls = [(s["t"], round(s["t"] - B.clock_limit(s["state"]), 2)) for s in ss if running(s) and s["t"] - B.clock_limit(s["state"]) > TOL_S]
    age = [s["t"] - s["mtime"] for s in ss if s.get("mtime") is not None]
    margin = [s["state"]["lease_until"] - s["t"] for s in ss if running(s)]
    lapses = [s["t"] for s in ss if running(s) and s["state"]["lease_until"] <= s["t"]]
    down = [e for e in clock_log if t0 <= e.get("at", 0) <= t1 and e.get("why") in BAD_WHY]
    stands = [e for e in clock_log if t0 <= e.get("at", 0) <= t1 and e.get("why") == "downtime"]
    rows = []
    if idle:
        rows.append((prefix + ".clock_stall", "invalid", "the clock was not running (or PAUSE existed) at %d of %d samples: this phase did not test the broadcast under load" % (len(idle), len(ss)), {"idle_samples": len(idle)}))
    else:
        data = {"samples": len(ss), "stalled_samples": len(stalls), "worst_over_limit_s": max([o for _, o in stalls] or [0]), "last_tick_age_s": {"p50": pct(age, .5), "p99": pct(age, .99), "max": max(age or [0])}}
        # Diagnostic, never a gate: the pinned design stands the clock still at the edge of what is published ("Nothing is skipped or shown early", docs/BROADCAST_INTEGRATION.md). Loss of
        # viewing coverage is B3 (publication deadline); a lapsed lease is B2. Tick lateness (the tick age above) is reported the same way.
        rows.append((prefix + ".clock_stall", "info", ("the clock stood still past its publication limit at %d of %d samples (worst %.2f s over); reported, not a gate" % (len(stalls), len(ss), data["worst_over_limit_s"]))
                     if stalls else "no stand-still in %d samples; the last tick was never older than %.2f s" % (len(ss), data["last_tick_age_s"]["max"]), data, False))
    rows.append((prefix + ".lease", "fail" if (lapses or down) else "pass",
                 ("the lease lapsed at %d samples and %d publish_failure entries were logged" % (len(lapses), len(down))) if (lapses or down) else "the lease never lapsed and no publish failure was logged; smallest margin seen %.1f s" % min(margin or [0]),
                 {"lapsed_samples": len(lapses), "logged": down[:5], "logged_standstills": len(stands), "margin_min_s": min(margin or [0]), "margin_p50_s": pct(margin, .5)}))
    return rows


def publication_row(prefix, B, samples, live, expected, t0, t1, tol=TOL_S):
    """B3: every segment of every game of the window published before viewers needed it. `expected` is {game id: number of segments}; `live` is scan_live() at the end of the phase."""
    ss = [s for s in samples if t0 <= s["t"] <= t1 and s.get("state")]
    if not ss or not expected:
        return (prefix + ".deadline", "invalid", "no clock samples or no expected games: publication could not be judged", None)
    first = B.position(ss[0]["state"], ss[0]["t"])
    late, missing, checked, worst, unreached, not_final = [], [], 0, None, 0, []
    total_expected = sum(expected.values())
    games = [v for v in live.values() if v["manifest"]]
    if len(games) < len(expected):
        return (prefix + ".deadline", "fail", "%d games were expected on air and %d live folders exist" % (len(expected), len(games)), {"expected": len(expected), "found": len(games)})
    for g in games:
        man = g["manifest"]
        start, delay, seg_s = man["start_s"], man["delay_s"], man["seg_s"]
        if first > start + 1e-9:
            return (prefix + ".deadline", "invalid", "the probe started at broadcast position %.1f s, after the window's start (%s s): earlier segments cannot be judged" % (first, start), None)
        if man.get("state") != "final":
            not_final.append(man["gid"])
        n_exp = expected.get(man["gid"], man["segments"])
        for n in range(n_exp):
            W = wall_at(B, ss, start + n * seg_s + delay)
            if W is None:
                unreached += 1
                continue
            checked += 1
            T = g["segments"].get(n)
            if T is None:
                missing.append((man["gid"], n))
                continue
            lateness = T - W
            worst = lateness if worst is None else max(worst, lateness)
            if lateness > tol:
                late.append((man["gid"], n, round(lateness, 2)))
    data = {"segments_checked": checked, "segments_expected": total_expected, "late": late[:10], "late_count": len(late), "missing": missing[:10], "missing_count": len(missing), "not_final": not_final,
            "worst_lateness_s": None if worst is None else round(worst, 3), "unreached": unreached}
    if not checked:
        return (prefix + ".deadline", "invalid", "the clock never reached any segment's deadline: the window was not exercised", data)
    bad = late or missing or not_final or unreached
    return (prefix + ".deadline", "fail" if bad else "pass",
            ("%d of %d segments late (worst %.2f s after the viewer's deadline), %d missing, %d games not final, %d deadlines never reached" % (len(late), checked, worst or 0, len(missing), len(not_final), unreached)) if bad
            else "all %d segments of %d games were published before the clock reached the position viewers need them; worst margin %.2f s (resolution %.1f s)" % (checked, len(games), -(worst or 0), tol), data)


def error_row(prefix, journal_text):
    """B4: the daemon's journal for the phase holds no JSON error line."""
    errs = []
    for line in (journal_text or "").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if "error" in d:
                errs.append(d)
    return (prefix + ".errors", "fail" if errs else "pass", ("%d error lines from the daemon: %s" % (len(errs), [e.get("error") for e in errs][:3])) if errs else "no error line from the daemon in this phase", {"errors": errs[:5]})


# ---------------------------------------------------------------------------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--seconds", type=float, default=0.0)
    a = ap.parse_args(argv)
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    signal.signal(signal.SIGINT, lambda *_: stop.append(1))
    end = time.time() + a.seconds if a.seconds else None
    nxt = time.time()
    with open(a.out, "a") as f:
        while not stop and (end is None or time.time() < end):
            f.write(json.dumps(sample(a.store, time.time()), sort_keys=True) + "\n")
            f.flush()
            nxt += a.interval
            time.sleep(max(0.0, nxt - time.time()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
