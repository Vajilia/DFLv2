#!/usr/bin/env python3
"""Run a WHOLE broadcast year in virtual time against a fully built private store, and check the public directory the whole way (docs/BROADCAST_PLAN.md).

  broadcast_season_check.py --root R --public P [--report FILE] [--scan-every 600] [--force-overrun KEY:SLOT_S ...] [--no-scenarios] [--limit-pos S]

R is a root made by tools/broadcast_build.py (every event built). P must be a NEW folder. Time is virtual (one tick per virtual second, the clock's own formula);
nothing here sleeps, serves the network or touches a league, and the store is only read.

What it forces, all in one year (each at a position in the broadcast, reported with what happened):
  * OVERRUNS      chosen windows get a shorter nominal slot than their game needs (the games are the real ones), so the real games overrun by hundreds of seconds
  * PAUSES        one in the middle of a game, one between windows (the PAUSE file)
  * PUBLISH FAILURES   a failing release copy, and failing writes of live segments in the middle of a game
  * RESTART       the daemon vanishes in the middle of a game, comes back 700 virtual seconds later as a new process
  * BUILDER BEHIND   the next event's data is withheld for several minutes just before it is needed

What it checks, continuously and at every boundary ±1 s and every --scan-every seconds:
  * COVERAGE      at every virtual second, a viewer's clock (the public clock.json, the client's formula) never stands at a time for which a live game's released
                  segments do not reach (so a dead or failing publisher or a slow builder can never make a viewer skip anything)
  * EXACT TREE    the set of files in the public directory equals, path for path, what the broadcast position allows, derived independently from the private truth
  * NO LEAKS      no public file names or quotes a future release, replay, hash or game; unannounced postseason participants are absent from schedule.json
  * GUESSED URLS  at sample instants, a static server over the public directory is asked for the next unreleased things by their true names, for private files
                  and for directories: every one must answer 404
  * STRUCTURE     every window starts exactly nominal + the overruns before it; blocks keep their length; the finish moves by exactly the sum of the overruns
Prints one JSON report; exit status 1 if any check failed.
"""
import argparse
import http.client
import http.server
import json
import math
import os
import shutil
import socketserver
import sys
import threading
import time
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dflsite import broadcast as B  # noqa: E402
from dflsite import season as S  # noqa: E402

T0 = 1_800_000_000.0
DEFAULT_FORCE = ["w002:450", "w041:450", "w070:450", "w104:450", "w105:450"]


def viewer_pos(public, now):
    c = B.get(os.path.join(public, "broadcast", "clock.json"))
    if c["state"] != "running":
        return float(c["anchor_pos"]), c
    return float(c["anchor_pos"]) + max(0.0, min(now, B.clock_limit(c)) - c["anchor_wall"]), c


# ---------------------------------------------------------------------------------------------------------------------------------- the independent oracle
class Oracle:
    """What the public directory may hold at broadcast position p, derived from the plan, the builds and the games' own lengths: not from reconcile."""

    def __init__(self, store, view, builds):
        self.store, self.view, self.builds = store, view, builds
        self.rel_files = {}
        self.games = {}
        for w in view.windows:
            for g in w["games"]:
                gid = g["gid"]
                self.games[gid] = (w, g)
        self.reveal_files = []              # (at_s, [paths], group, dst)
        for r in view.reveals:
            if r.get("group"):
                continue
            src = os.path.join(store, r["src"])
            if os.path.isdir(src):
                paths = [os.path.join(r["dst"], os.path.relpath(os.path.join(d, n), src)) for d, _, ns in os.walk(src) for n in ns]
            else:
                paths = [r["dst"]]
            self.reveal_files.append((r["at_s"], paths))
        self.group = {}
        for r in view.reveals:
            if r.get("group"):
                self.group.setdefault(r["dst"], []).append(r)

    def live_files(self, gid, p):
        w, g = self.games[gid]
        t = p - w["start_s"]
        if t < 0:
            return set(), None
        base = "broadcast/live/" + B.safe(gid) + "/"
        n = min(g["segments"], int(t // B.SEG_S) + 1)
        out = {base + "head.json", base + "manifest.json"} | {base + "s%04d.json" % i for i in range(n)}
        ended = t >= g["length_s"] + B.DELAY_S
        if ended:
            out |= {base + "end.json", "broadcast/replays/" + g["replay"]}
        return out, ended

    def expected(self, p):
        exp = {"broadcast/clock.json", "broadcast/schedule.json", "broadcast/replays/index.json", "status.json"}
        for at, paths in self.reveal_files:
            if p >= at:
                exp |= set(paths)
        for dst, rs in self.group.items():
            if any(p >= r["at_s"] for r in rs):
                exp.add(dst)
        ended = []
        for gid in self.games:
            f, e = self.live_files(gid, p)
            exp |= f
            if e:
                ended.append(gid)
        return exp, ended

    def latest_pointer(self, p):
        due = [r for r in self.group.get("latest.json", []) if p >= r["at_s"]]
        return max(due, key=lambda r: (r["at_s"], r["order"])) if due else None


class FollowingOracle:
    """The oracle for a broadcast that goes on past one season, read from the store while it runs (the monitor of a recurring broadcast, tools/rehearsal_check.py).

    `Oracle` is a snapshot of ONE season whose builds were all in place. This one follows the store the way the daemon does: the season on air is the one named by
    store/carry.json (the root's season when there is none) and its builds file is read again whenever it, or the carry record, changes (the builder is still adding events).
    What the earlier seasons made public stays expected for ever: each finished season's whole file set is part of `expected()`, so a historical file that vanishes is
    reported as missing and a file nothing accounts for is reported as unknown. The refresh happens on every call, AFTER the caller has listed the public folder, so a
    file published for an event built a moment after the listing is never mistaken for an early one.
    Same interface as Oracle: expected(p), latest_pointer(p), view, games, builds."""

    def __init__(self, store):
        self.store = store
        self.first = int(B.get(os.path.join(store, S.PLAN))["season"])            # the first season stays at the root (as the daemon reads it)
        self._key = None
        self._past = {}                                   # finished season -> its complete file set (immutable once finished)
        self._cur = None
        self.refresh()

    def _stat(self, path):
        try:
            st = os.stat(path)
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _dir(self, season):
        return self.store if season == self.first else os.path.join(self.store, S.SEASONS, "s%03d" % season)

    def _load(self, season, carry):
        d = self._dir(season)
        plan = B.get(os.path.join(d, S.PLAN))
        bpath = os.path.join(d, S.BUILDS)
        builds = B.get(bpath) if os.path.exists(bpath) else {"format": S.BUILDS_FORMAT, "events": {}, "games": {}, "honors": {}}
        return Oracle(self.store, S.materialize(plan, builds, carry), builds)

    def refresh(self):
        cpath = os.path.join(self.store, S.CARRY)
        carry = B.get(cpath) if os.path.exists(cpath) else None
        season = int(carry["season"]) if carry else self.first
        key = (self._stat(cpath), self._stat(os.path.join(self._dir(season), S.BUILDS)), self._stat(os.path.join(self._dir(season), S.PLAN)))
        if key == self._key:
            return
        for k in (carry["seasons"] if carry else []):
            if k not in self._past:
                self._past[k] = self._load(k, None).expected(10 ** 9)[0]
        self._cur = self._load(season, carry)
        self._key = key

    @property
    def view(self):
        return self._cur.view

    @property
    def games(self):
        return self._cur.games

    @property
    def builds(self):
        return self._cur.builds

    def expected(self, p):
        self.refresh()
        exp, ended = self._cur.expected(p)
        for files in self._past.values():
            exp = exp | files
        return exp, ended

    def latest_pointer(self, p):
        self.refresh()
        return self._cur.latest_pointer(p)


def list_public(public):
    out = set()
    for d, _, names in os.walk(public):
        for n in names:
            out.add(os.path.relpath(os.path.join(d, n), public))
    return out


# ---------------------------------------------------------------------------------------------------------------------------------- static server for guessed URLs
class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def list_directory(self, path):
        self.send_error(404)
        return None


def serve(public):
    handler = lambda *a, **k: Quiet(*a, directory=public, **k)
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def http_status(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        c.request("GET", path)
        r = c.getresponse()
        body = r.read()
        return r.status, body
    finally:
        c.close()


# ---------------------------------------------------------------------------------------------------------------------------------- the run
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", required=True)
    ap.add_argument("--public", required=True)
    ap.add_argument("--report")
    ap.add_argument("--scan-every", type=int, default=600)
    ap.add_argument("--force-overrun", action="append", help="KEY:SLOT_S: give this window a shorter nominal slot so its real games overrun")
    ap.add_argument("--no-scenarios", action="store_true")
    ap.add_argument("--limit-pos", type=float, help="stop when the broadcast position reaches this (a partial run; the store may be only partly built)")
    a = ap.parse_args(argv)
    store = os.path.join(os.path.abspath(a.root), "store")
    public = os.path.abspath(a.public)
    if os.path.exists(public) and os.listdir(public):
        raise SystemExit("the public folder must be new")
    if os.path.realpath(public).startswith(os.path.realpath(a.root)):
        raise SystemExit("the public folder must be outside the private root")
    B.FSYNC = False                                                           # a simulated day must not wait for the disk 180,000 times
    plan = B.get(os.path.join(store, S.PLAN))
    builds_full = B.get(os.path.join(store, S.BUILDS))
    forced = {}
    for spec in (a.force_overrun if a.force_overrun is not None else DEFAULT_FORCE):
        k, s = spec.split(":")
        forced[k] = int(s)
    for it in plan["items"]:
        if it["kind"] == "window" and it["key"] in forced:
            it["slot_s"] = forced[it["key"]]
    full = S.materialize(plan, builds_full)
    assert full.horizon is None or a.limit_pos is not None, "the store is not fully built: %s" % full.horizon_why
    oracle = Oracle(store, full, builds_full)
    nominal_total = plan["total_s"]
    overruns = {w["key"]: w["overrun_s"] for w in full.windows if w["overrun_s"]}
    total_overrun = sum(overruns.values())
    report = {"forced": forced, "overruns_s": overruns, "violations": [], "scenarios": [], "scans": 0, "probes": 0, "ticks": 0}
    fail = lambda msg: (report["violations"].append(msg) if len(report["violations"]) < 60 else None)

    # builder-behind control: a stand-in for builds.json that withholds events from `withheld_from` on, while `stall` is true
    stall = {"on": False, "from_seq": None}

    def builds_fn():
        if not stall["on"]:
            return ("full",), builds_full
        n = stall["from_seq"]
        cut = dict(builds_full)
        cut["events"] = {k: v for k, v in builds_full["events"].items() if int(k) < n}
        cut["games"] = {g: r for g, r in builds_full["games"].items() if r["seq"] < n}
        cut["honors"] = {k: v for k, v in builds_full["honors"].items() if k == "rings" and n > 45}
        return ("cut", n), cut

    state_dir = os.path.join(os.path.dirname(public), os.path.basename(public) + "-clockstate")
    os.makedirs(state_dir)
    mk = lambda: S.SeasonBroadcaster(store, public, plan=plan, builds_fn=builds_fn, state_dir=state_dir)
    bc = mk()
    bc.clock.start(T0, 0.0, B.LEASE_S, ready_until=0.0)
    srv = serve(public)
    port = srv.server_address[1]

    # scenarios, each triggered when the broadcast position first reaches `at`
    windows = {w["key"]: w for w in full.windows}
    horizon_full = full.horizon if full.horizon is not None else float("inf")
    reveal_times = sorted({r["at_s"] for r in full.reveals if r.get("group") == "latest"})

    def next_reveal_after(pos):
        return next(t for t in reveal_times if t > pos)
    scen = []
    if not a.no_scenarios:
        def win(k):
            return windows.get(k)
        cands = [
            ("pause in the middle of a game", "w020", 300, "pause", 1800),
            ("pause between windows", "w050", None, "pause", 600),
            ("live segment writes fail in the middle of a game", "w075", 200, "fail_live", 90),
            ("daemon dies in the middle of a game and returns as a new process", "w080", 100, "restart", 700)]
        for name, k, off, kind, wall_s in cands:
            w = win(k)
            if w:
                scen.append({"name": name, "at": w["start_s"] + (off if off is not None else w["slot_s"] + 250), "kind": kind, "wall": wall_s})
        after = [t for t in reveal_times if t > 60000]
        if after:
            scen.append({"name": "release copy fails", "at": after[0] - 10, "kind": "fail_copy", "wall": 240})
        scen.sort(key=lambda x: x["at"])
        rj = next((p for p in full.pointers if p["at_s"] >= (110000 if a.limit_pos is None else 6000)), None)
        if rj:
            ns = next(w["start_s"] for w in full.windows if w["start_s"] > rj["at_s"] + 10)
            scen.append({"name": "builder behind: the next week's events are not built when the broadcast reaches them", "at": rj["at_s"] + 10, "kind": "builder_behind",
                         "wall": int(ns - rj["at_s"]) + 500, "from_seq": rj["events"] + 1})
            scen.sort(key=lambda x: x["at"])
    pending = [x for x in scen if x["at"] + 3000 < horizon_full and (a.limit_pos is None or x["at"] < a.limit_pos - 4000)]
    active = None
    wall = 0
    dead = False
    pos = 0.0
    last_scan = -10 ** 9
    boundaries = sorted({r["at_s"] for r in full.reveals} | {w["start_s"] for w in full.windows} | {w["start_s"] + w["slot_s"] for w in full.windows}
                        | {w["start_s"] + max(math.ceil(g["length_s"] + B.DELAY_S) for g in w["games"]) for w in full.windows})
    bidx = 0
    scan_gate = set()
    live_windows = [w for w in full.windows]
    t_start = time.time()
    probes_at = {int(x) for x in (nominal_total * f for f in (0.003, 0.05, 0.15, 0.31, 0.47, 0.62, 0.77, 0.9, 0.995))}
    probed = set()
    max_wall = int(nominal_total + total_overrun + 60000)
    max_pos_seen = 0.0
    coverage_checked = 0
    while wall < max_wall:
        now = T0 + wall
        # ---------------- scenario control
        if active and wall >= active["until"]:
            kind = active["kind"]
            report["scenarios"][-1]["ended_wall"] = wall
            if kind == "pause":
                os.unlink(os.path.join(state_dir, S.PAUSE))
            elif kind == "builder_behind":
                stall["on"] = False
            elif kind == "restart":
                bc = mk()
                dead = False
            elif kind in ("fail_copy", "fail_live"):
                for p in active["patches"]:
                    p.stop()
            report["scenarios"][-1]["pos_after"] = round(viewer_pos(public, now)[0], 1) if kind != "restart" else None
            active = None
        if not active and pending and pos >= pending[0]["at"]:
            sc = pending.pop(0)
            rec = {"name": sc["name"], "kind": sc["kind"], "started_wall": wall, "pos_before": round(pos, 1), "wall_s": sc["wall"]}
            active = dict(sc, until=wall + sc["wall"], patches=[])
            if sc["kind"] == "pause":
                open(os.path.join(state_dir, S.PAUSE), "w").close()
            elif sc["kind"] == "builder_behind":
                stall.update(on=True, from_seq=sc["from_seq"])
                rec["withheld_from_event"] = sc["from_seq"]
            elif sc["kind"] == "restart":
                dead = True
                rec["ready_until_at_death"] = bc.clock.state().get("ready_until")
            elif sc["kind"] == "fail_copy":
                def boom(*x, **k):
                    raise OSError(28, "No space left on device")
                active["patches"] = [mock.patch.object(B.shutil, "copytree", boom), mock.patch.object(B.shutil, "copyfile", boom)]
                for p in active["patches"]:
                    p.start()
            elif sc["kind"] == "fail_live":
                real_put = B.put

                def flaky(path, obj, _real=real_put):
                    if os.sep + "live" + os.sep in path and os.path.basename(path).startswith("s0"):
                        raise OSError(5, "Input/output error")
                    return _real(path, obj)
                active["patches"] = [mock.patch.object(B, "put", flaky)]
                for p in active["patches"]:
                    p.start()
            report["scenarios"].append(rec)
        # ---------------- the daemon
        if not dead:
            try:
                r = bc.tick(now)
                pos = r["pos"]
                if active and active["kind"] in ("fail_copy", "fail_live"):
                    report["scenarios"][-1].setdefault("failures_recovered", 0)
            except OSError as e:
                report["scenarios"][-1]["errors"] = report["scenarios"][-1].get("errors", 0) + 1
                pos = bc.clock.position(now) or pos
            report["ticks"] += 1
        # ---------------- viewer coverage, every virtual second
        vpos, clk = viewer_pos(public, now)
        if vpos < max_pos_seen - 1e-6:
            fail("viewer clock ran backwards at wall %d" % wall)
        max_pos_seen = max(max_pos_seen, vpos)
        for w in live_windows:
            if w["start_s"] <= vpos < w["start_s"] + 1500:
                for g in w["games"]:
                    d = os.path.join(public, "broadcast", "live", B.safe(g["gid"]))
                    tau = vpos - w["start_s"] - B.DELAY_S
                    if tau < 0:
                        continue
                    try:
                        man = B.get(os.path.join(d, "manifest.json"))
                        have = man["segments"]
                    except OSError:
                        have = 0
                    need = min(g["segments"], int(tau // B.SEG_S) + 1)
                    coverage_checked += 1
                    if have < need:
                        fail("COVERAGE: wall %d viewer at %.1f needs %d segments of %s, the public tree has %d" % (wall, vpos, need, g["gid"], have))
        # ---------------- scans
        if not dead and (pos - last_scan >= a.scan_every or (bidx < len(boundaries) and pos >= boundaries[bidx] - 1)):
            while bidx < len(boundaries) and pos >= boundaries[bidx] - 1:
                if pos >= boundaries[bidx] + 1:
                    bidx += 1
                else:
                    break
            key = int(pos)
            if key not in scan_gate:
                scan_gate.add(key)
                last_scan = pos
                report["scans"] += 1
                exp, ended = oracle.expected(pos)
                have = list_public(public)
                if not (active and active["kind"] in ("fail_copy", "fail_live", "builder_behind")):
                    if have != exp:
                        fail("TREE at pos %.1f wall %d: unexpected %s ; missing %s" % (pos, wall, sorted(have - exp)[:5], sorted(exp - have)[:5]))
                else:
                    extra = have - exp
                    if extra:
                        fail("TREE (during a fault) at pos %.1f: unexpected %s" % (pos, sorted(extra)[:5]))
                lp = oracle.latest_pointer(pos)
                if lp:
                    if open(os.path.join(public, "latest.json"), "rb").read() != open(os.path.join(store, lp["src"]), "rb").read() and not (active and active["kind"] == "fail_copy"):
                        fail("latest.json is not the pointer due at %.1f" % pos)
                elif os.path.exists(os.path.join(public, "latest.json")):
                    fail("latest.json exists before any release is due")
                sched = B.get(os.path.join(public, "broadcast", "schedule.json"))
                for sw in sched["windows"]:
                    src_w = next((w for w in full.windows if w["label"] == sw["label"]), None)
                    if src_w and src_w.get("announce_s") is not None and pos < src_w["announce_s"] and sw["games"]:
                        fail("participants of %s announced at %.1f, before %.1f" % (sw["label"], pos, src_w["announce_s"]))
                # tokens of everything not yet due must appear nowhere in the small public files
                future = []
                for gid, (w, g) in oracle.games.items():
                    if gid not in ended:
                        future.append(g["replay"])
                        future.append(builds_full["games"][gid]["entry"]["sha256"][:16])
                for p in full.pointers:
                    if p["at_s"] > pos:
                        future.append(p["release"])
                for f in have:
                    fp = os.path.join(public, f)
                    if f.startswith(("broadcast/replays/S", "playback/games/")) or (f.startswith("releases/") and not f.endswith("manifest.json")) or os.path.getsize(fp) > 400000:
                        continue
                    if f.startswith("broadcast/live/") and not f.endswith(("manifest.json", "head.json", "end.json")):
                        continue
                    text = open(fp, "rb").read().decode("utf-8", "replace")
                    for tkn in future:
                        if tkn in text:
                            fail("LEAK: %s names a future artifact %s at pos %.1f" % (f, tkn, pos))
                            break
        # ---------------- guessed URLs
        for target in probes_at:
            if target not in probed and pos >= target and not dead and not active:
                probed.add(target)
                exp, ended = oracle.expected(pos)
                guesses = []
                nxt_rel = [p["release"] for p in full.pointers if p["at_s"] > pos][:3]
                for rel in nxt_rel:
                    guesses += ["/releases/%s/manifest.json" % rel, "/releases/%s/standings.json" % rel]
                notdone = [g for g in oracle.games if g not in ended][:200]
                for gid in notdone[:6] + notdone[-3:]:
                    w, g = oracle.games[gid]
                    guesses += ["/broadcast/replays/" + g["replay"], "/playback/games/" + builds_full["games"][gid]["entry"]["file"].split("/")[-1]]
                    if pos < w["start_s"]:
                        guesses += ["/broadcast/live/%s/head.json" % B.safe(gid), "/broadcast/live/%s/manifest.json" % B.safe(gid), "/broadcast/live/%s/s0000.json" % B.safe(gid)]
                    else:
                        base = "/broadcast/live/%s/" % B.safe(gid)
                        t = pos - w["start_s"]
                        ahead = int(t // B.SEG_S) + 3
                        guesses += [base + "end.json"] + ([base + "s%04d.json" % ahead] if ahead < g["segments"] else [])
                for part in ("rings", "tiara", "halls", "fan_entrant"):
                    if "broadcast/honors/s001/%s.json" % part not in exp:
                        guesses.append("/broadcast/honors/s001/%s.json" % part)
                guesses += ["/store/builds.json", "/builds.json", "/plan.json", "/store/plan.json", "/../store/plan.json", "/%2e%2e/store/plan.json", "/..%2fstore/builds.json",
                            "/.tmp-latest.json", "/broadcast/.tmp-clock.json", "/releases/", "/broadcast/", "/broadcast/live/", "/playback/", "/playback/games/", "/clock_log.jsonl",
                            "/broadcast/clock_log.jsonl", "/schedule.json", "/reveals.json", "/feed/latest.json", "/honors/s001/tiara.json", "/league.db"]
                for g in guesses:
                    st, _ = http_status(port, g)
                    report["probes"] += 1
                    if st == 200 and g not in ("/broadcast/schedule.json",):
                        # a guess is only allowed to succeed if the tree legitimately holds it
                        if g.lstrip("/") not in exp:
                            fail("GUESS %s answered 200 at pos %.1f" % (g, pos))
                for must in ("/broadcast/clock.json", "/broadcast/schedule.json", "/status.json"):
                    st, _ = http_status(port, must)
                    report["probes"] += 1
                    if st != 200:
                        fail("%s answered %d" % (must, st))
        wall += 1
        if a.limit_pos is not None and pos >= a.limit_pos:
            break
        if pos >= nominal_total + total_overrun + 5 and not pending and not active:
            break
    srv.shutdown()
    # ---------------- end of year
    report["wall_virtual_s"] = wall
    report["final_pos"] = round(pos, 1)
    report["real_seconds"] = round(time.time() - t_start, 1)
    if a.limit_pos is None:
        exp, ended = oracle.expected(pos + 1e-6)
        have = list_public(public)
        if have != exp:
            fail("END TREE: unexpected %s ; missing %s" % (sorted(have - exp)[:5], sorted(exp - have)[:5]))
        last_rel = full.pointers[-1]["release"]
        if B.get(os.path.join(public, "latest.json"))["release"] != last_rel:
            fail("the final pointer is not the last release")
        report["end"] = {"files": len(have), "games_ended": len(ended), "playback_games": len([f for f in have if f.startswith("playback/games/")]),
                         "releases": len([f for f in have if f.startswith("releases/") and f.endswith("manifest.json")]), "honors": sorted(f for f in have if "honors" in f),
                         "status": json.load(open(os.path.join(public, "status.json")))}
        if len(ended) != 400:
            fail("%d games ended, expected 400" % len(ended))
        # structure: every window moved by exactly the overruns before it, blocks keep their length
        shift = 0
        for it in plan["items"]:
            if it["kind"] == "window":
                w = windows[it["key"]]
                if w["start_s"] != it["start_s"] + shift:
                    fail("window %s starts %s, expected %s" % (it["key"], w["start_s"], it["start_s"] + shift))
                shift += w["overrun_s"]
        report["finish_s"] = nominal_total + shift
        report["overrun_total_s"] = shift
        # reveal times recomputed from the games' own lengths and the plan, without materialize: block reveal = block start + overruns of windows before it + offset
        by_key = {}
        for gid, rec in builds_full["games"].items():
            k = S.window_of(plan, gid)
            if k is not None:
                by_key.setdefault(k, []).append(rec)
        for rk, sizes in plan["round_sizes"].items():
            recs = sorted([(g, r) for g, r in builds_full["games"].items() if r["key"] == rk], key=lambda x: S._po_order(x[0]))
            i = 0
            for k, n in zip(plan["rounds"][rk], sizes):
                by_key[k] = [r for _, r in recs[i:i + n]]
                i += n
        seq = {v["key"]: int(k) for k, v in builds_full["events"].items()}
        shift = 0
        pointer_at = {p["release"]: p["at_s"] for p in full.pointers}
        checked = 0
        for it in plan["items"]:
            if it["kind"] == "window":
                D = max(math.ceil(r["length_s"] + B.DELAY_S) for r in by_key[it["key"]])
                shift += max(0, D - it["slot_s"])
            else:
                rel = builds_full["events"][str(seq[it["events"][-1]])]["release"]
                want = it["start_s"] + shift + it["events_at"]
                if pointer_at.get(rel) != want:
                    fail("release %s is revealed at %s, independently computed %s" % (rel, pointer_at.get(rel), want))
                checked += 1
        report["reveal_times_checked"] = checked
    log = [json.loads(x) for x in open(os.path.join(state_dir, "clock_log.jsonl"))]
    kinds = {}
    for rec in log:
        kinds[rec["why"]] = kinds.get(rec["why"], 0) + 1
    report["clock_log"] = kinds
    report["coverage_checks"] = coverage_checked
    if pending:
        fail("scenarios never reached: %s" % [x["name"] for x in pending])
    report["ok"] = not report["violations"]
    out = json.dumps(report, indent=1, sort_keys=True, default=str)
    if a.report:
        with open(a.report, "w") as f:
            f.write(out)
    print(out)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
