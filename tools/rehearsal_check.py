#!/usr/bin/env python3
"""Checks for the capped real-time broadcast rehearsal (docs/BROADCAST_REHEARSAL.md). Read-only: it never writes to the public folder or the store.

  rehearsal_check.py http  --url https://HOST [--store R/store] [--sample 40]
        One pass over the SERVER'S RULES: cache headers per kind of file, no directory listings, nothing private served, write methods refused, a usable Date
        header, and (with --store, run on the server) a sample of URLs that are named in the private store but not yet released, which must all answer 404.
  rehearsal_check.py watch --root R --public P [--url URL] [--log FILE] [--evidence DIR] [--every 10] [--full-every 300] [--min-free-gb 5] [--max-public-gb 3]
        A monitor for the whole rehearsal. Every cycle: the clock and status files agree, nothing moves backwards, the daemon's lease is alive (unless paused),
        no stale `.tmp-` debris, disk space. Every --full-every seconds: the whole public tree is compared with what the broadcast position allows
        (derived from the private plan and builds by tools/broadcast_season_check.py's independent Oracle): nothing early, nothing missing, and with --url a
        sample of guessed URLs. Every finding is one JSON line in the log, and the FIRST full detail of each kind is kept under --evidence (file listings,
        positions, response headers) so a failure is never lost to a rerun. Exit status 1 if any finding was logged.
  rehearsal_check.py preflight --root R --public P [--min-free-gb 6] [--deadline T]
        Before the daemon is ever started: the private store is complete (every event built, every window and honors part present and consistent with the plan,
        no running clock yet), the public folder is empty or made by the daemon, the two are separate, neither is a production path, there is disk, and the
        deadline (if given) is in the future and not more than 60 hours away. Prints PASS/FAIL lines; exit 1 on any FAIL.
  Stop `watch` with SIGTERM or Ctrl-C.
"""
import argparse
import http.client
import json
import os
import random
import shutil
import signal
import ssl
import sys
import time
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from dflsite import broadcast as B  # noqa: E402
from dflsite import season as S  # noqa: E402
import broadcast_season_check as SC  # noqa: E402

FEED = "/feed"
TOL_S = 6.0           # a viewer's clock and the daemon's tick differ by up to a second or two; files are placed within a tick of their time


# ---------------------------------------------------------------------------------------------------------------------------------- HTTP
def request(base, path, method="GET", timeout=20):
    u = urlsplit(base)
    cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    kw = {"context": ssl.create_default_context()} if u.scheme == "https" else {}
    c = cls(u.hostname, u.port, timeout=timeout, **kw)
    try:
        c.request(method, path, headers={"User-Agent": "dfl-rehearsal-check", "Accept-Encoding": "identity"})
        r = c.getresponse()
        body = r.read() if method != "HEAD" else b""
        return r.status, {k.lower(): v for k, v in r.getheaders()}, body
    finally:
        c.close()


def cache_kind(cc):
    cc = (cc or "").lower()
    if "no-store" in cc:
        return "no-store"
    if "immutable" in cc:
        return "immutable"
    if "no-cache" in cc:
        return "no-cache"
    return "other:" + cc


def http_rules(base, store=None, sample=40, before_start=False):
    """Returns (results, failures). Each result is (ok, what)."""
    res = []

    def check(ok, what):
        res.append((bool(ok), what))
        return ok

    def get(path, **kw):
        return request(base, path, **kw)

    # ---- never-cached pointers and clocks: also when they answer 404 (a browser must not keep a "not yet" for long)
    for rel in ("latest.json", "status.json", "playback/index.json", "broadcast/clock.json", "broadcast/schedule.json", "broadcast/replays/index.json", "broadcast/honors/index.json"):
        st, h, body = get(FEED + "/" + rel)
        check(st in (200, 404) and cache_kind(h.get("cache-control")) in ("no-store", "no-cache"),
              "%s -> %d, Cache-Control %r (must be no-store or no-cache: never immutable, never a max-age)" % (rel, st, h.get("cache-control")))
    st, h, body = get(FEED + "/status.json")
    d = h.get("date")
    try:
        skew = abs(parsedate_to_datetime(d).timestamp() - time.time())
    except Exception:
        skew = None
    check(skew is not None and skew < 5, "Date header present and within 5 s of this machine's clock (skew %s)" % (skew,))
    if before_start:
        for rel in ("latest.json", "status.json", "broadcast/clock.json", "broadcast/schedule.json"):
            st, h, body = get(FEED + "/" + rel)
            check(st == 404, "%s does not exist before the daemon starts (got %d)" % (rel, st))
    for rel in () if before_start else ("latest.json", "status.json", "broadcast/clock.json", "broadcast/schedule.json"):
        st, h, body = get(FEED + "/" + rel)
        check(st == 200 and cache_kind(h.get("cache-control")) == "no-store", "%s must be 200 and Cache-Control no-store (got %d, %r)" % (rel, st, h.get("cache-control")))
    # ---- immutable files: only if they exist (a 404 must never be told immutable)
    if not before_start:
        st, h, body = get(FEED + "/latest.json")
        try:
            ptr = json.loads(body)
            st2, h2, _ = get(FEED + "/releases/%s/manifest.json" % ptr["release"])
            check(st2 == 200 and cache_kind(h2.get("cache-control")) == "immutable", "a released directory file is immutable (got %d %r)" % (st2, h2.get("cache-control")))
        except Exception as e:
            check(False, "latest.json is not JSON: %s" % e)
        st, h, body = get(FEED + "/broadcast/replays/index.json")
        if st == 200:
            games = json.loads(body).get("games") or []
            if games:
                s3, h3, _ = get(FEED + "/broadcast/replays/" + games[0]["replay"])
                check(s3 == 200 and cache_kind(h3.get("cache-control")) == "immutable", "a finished broadcast replay file is immutable (got %d %r)" % (s3, h3.get("cache-control")))
        st, h, body = get(FEED + "/broadcast/schedule.json")
        stc, hc, bc = get(FEED + "/broadcast/clock.json")
        if st == 200 and stc == 200:
            pos = B.position(json.loads(bc), time.time())
            began = [w for w in json.loads(body).get("windows", []) if w.get("games") and w["start_s"] <= pos]
            for w in began[-1:]:
                slug = B.safe(w["games"][0]["gid"])
                for name, want in (("manifest.json", "no-store"), ("head.json", "immutable"), ("s0000.json", "immutable")):
                    s4, h4, _ = get(FEED + "/broadcast/live/%s/%s" % (slug, name))
                    check(s4 == 200 and cache_kind(h4.get("cache-control")) == want, "live/%s/%s is 200 and %s (got %d %r)" % (slug, name, want, s4, h4.get("cache-control")))
    # ---- directories are never listed
    for d in ("/", FEED, FEED + "/", FEED + "/releases", FEED + "/releases/", FEED + "/broadcast", FEED + "/broadcast/", FEED + "/broadcast/live", FEED + "/broadcast/live/",
              FEED + "/broadcast/replays/", FEED + "/broadcast/honors/", FEED + "/broadcast/honors/s001/", FEED + "/playback/", FEED + "/playback/games/"):
        st, h, body = get(d)
        listing = b"<title>Index of" in body[:2000] or b"Name</a>" in body[:4000] or b"Last modified" in body[:4000] or (st == 200 and "json" in h.get("content-type", "") and d != "/")
        if d == "/":
            check(st == 200 and b"<script" in body[:20000] and not listing, "site root serves the page, not a listing")
        elif st in (301, 302, 307, 308) and h.get("location", "").endswith("/") and not listing:
            check(True, "%s only redirects to its slash form (%d); the slash form is checked next" % (d, st))
        else:
            check(st in (403, 404) and not listing, "%s is not listed (got %d)" % (d, st))
    # ---- private and temporary names are never served
    for p in ("/builds.json", "/plan.json", "/store/builds.json", "/store/plan.json", "/private/store/builds.json", FEED + "/builds.json", FEED + "/plan.json",
              FEED + "/store/plan.json", FEED + "/daemon.lock", FEED + "/PAUSE", FEED + "/broadcast/PAUSE", FEED + "/broadcast/clock_log.jsonl", FEED + "/clock_log.jsonl",
              FEED + "/.tmp-x", FEED + "/broadcast/.tmp-x", FEED + "/broadcast/.lock", "/.git/config", "/../private/store/plan.json", "/%2e%2e/private/store/plan.json",
              FEED + "/..%2fprivate/store/plan.json", FEED + "/broadcast/honors/../../plan.json"):
        st, h, body = get(p)
        check(st in (400, 403, 404) and b'"format"' not in body[:200], "%s is not served (got %d)" % (p, st))
    # ---- no writes
    for m in ("POST", "PUT", "DELETE", "PATCH"):
        st, _, _ = get(FEED + "/status.json", method=m)
        check(st in (405, 403), "%s is refused (got %d)" % (m, st))
    # ---- guessed URLs of things that exist privately but are not yet released
    if store:
        clock = B.Clock(store, os.path.join(store, "_unused"))
        stt = clock.state()
        pos = clock.position(time.time()) if stt else 0.0
        orc = make_oracle(store)
        allowed_now, _ = orc.expected(pos + 10)
        everything, _ = orc.expected(10 ** 9)
        future = sorted(everything - allowed_now)
        random.Random(pos).shuffle(future)
        check(True, "%d private files are not yet due at position %.0f; probing %d of them" % (len(future), pos, min(sample, len(future))))
        for rel in future[:sample]:
            st, _, _ = get(FEED + "/" + rel)
            check(st == 404, "unreleased %s answers 404 (got %d)" % (rel, st))
    return res


def make_oracle(store):
    plan = B.get(os.path.join(store, S.PLAN))
    builds = B.get(os.path.join(store, S.BUILDS))
    return SC.Oracle(store, S.materialize(plan, builds), builds)


# ---------------------------------------------------------------------------------------------------------------------------------- monitor
class Watch:
    def __init__(self, a):
        self.a = a
        self.store = os.path.join(os.path.abspath(a.root), "store")
        self.public = os.path.abspath(a.public)
        self.log = open(a.log, "a") if a.log else sys.stdout
        self.evidence = a.evidence
        self.found = 0
        self.last_release = ""
        self.kinds = set()
        self.last = {"published": -1, "release": "", "epoch": 0}
        self.oracle = make_oracle(self.store)
        self.clock = B.Clock(self.store, os.path.join(self.store, "_unused"))

    def emit(self, **kw):
        kw["at"] = round(time.time(), 1)
        self.log.write(json.dumps(kw, sort_keys=True) + "\n")
        self.log.flush()

    def violation(self, kind, detail, evidence=None):
        self.found += 1
        self.emit(violation=kind, detail=detail)
        if self.evidence and kind not in self.kinds:                         # the FIRST full record of each kind is kept; reruns never replace it
            self.kinds.add(kind)
            os.makedirs(self.evidence, exist_ok=True)
            path = os.path.join(self.evidence, "%s-%s.json" % (time.strftime("%Y%m%dT%H%M%S", time.gmtime()), kind))
            with open(path, "w") as f:
                json.dump({"kind": kind, "detail": detail, "evidence": evidence, "at": time.time()}, f, indent=1, sort_keys=True, default=str)

    def cheap(self, now):
        st = self.clock.state()
        if st is None:
            return None
        pos = self.clock.position(now)
        pub_clock = B.get(os.path.join(self.public, "broadcast", "clock.json")) if os.path.exists(os.path.join(self.public, "broadcast", "clock.json")) else None
        status = B.get(os.path.join(self.public, "status.json")) if os.path.exists(os.path.join(self.public, "status.json")) else None
        if st["state"] == "running" and now > st["lease_until"] + 5 and not os.path.exists(os.path.join(self.store, S.PAUSE)):
            self.violation("daemon_silent", "the clock's lease ran out %.0f s ago and nothing renewed it" % (now - st["lease_until"]), {"clock": st})
        if pub_clock and pub_clock.get("epoch") != st.get("epoch"):
            self.violation("clock_mismatch", "public clock.json epoch %s, store clock epoch %s" % (pub_clock.get("epoch"), st.get("epoch")), {"public": pub_clock, "store": st})
        if status:
            n = status.get("published_events", status.get("league_events"))
            if n is not None and n < self.last["published"]:
                self.violation("went_backwards", "published events %s after %s" % (n, self.last["published"]), {"status": status})
            if n is not None:
                self.last["published"] = max(self.last["published"], n)
            rel = status.get("release") or ""
            if rel and rel < self.last["release"]:
                self.violation("went_backwards", "release %s after %s" % (rel, self.last["release"]), {"status": status})
            self.last["release"] = max(self.last["release"], rel)
            paused = st["state"] == "paused" or os.path.exists(os.path.join(self.store, S.PAUSE))
            if paused and status["runner"]["state"] == "running" and now - os.path.getmtime(os.path.join(self.public, "status.json")) > 8:
                self.violation("status_stale_running", "the clock is paused but status.json still says running", {"status": status, "clock": st})
        # debris
        for d, _, names in os.walk(self.public):
            for n in names:
                if n.startswith(".tmp-") or n.endswith(".tmp"):
                    p = os.path.join(d, n)
                    if now - os.path.getmtime(p) > 120:
                        self.violation("stale_tmp", "temporary file %s is %.0f s old" % (os.path.relpath(p, self.public), now - os.path.getmtime(p)))
        free = shutil.disk_usage(self.public).free / 1e9
        if free < self.a.min_free_gb:
            self.violation("disk_low", "%.1f GB free" % free, {"free_gb": free})
        return pos

    def full(self, now, pos_before):
        listing = SC.list_public(self.public)
        pos_after = self.clock.position(time.time())
        size = sum(os.path.getsize(os.path.join(self.public, f)) for f in listing)
        if size > self.a.max_public_gb * 1e9:
            self.violation("public_too_large", "%.2f GB" % (size / 1e9), {"bytes": size})
        early_allowed, _ = self.oracle.expected(pos_after + TOL_S)
        due, _ = self.oracle.expected(max(0.0, pos_before - TOL_S))
        # the grouped files (latest.json, playback/index.json, honors/index.json) are in `expected` by name only: their content is checked below
        unexpected = sorted(listing - early_allowed)
        missing = sorted(due - listing)
        if unexpected:
            self.violation("early_or_unknown_file", "%d files the position does not allow, e.g. %s" % (len(unexpected), unexpected[:3]),
                           {"pos": pos_after, "unexpected": unexpected[:200], "count": len(unexpected)})
        if missing:
            self.violation("missing_file", "%d due files absent, e.g. %s" % (len(missing), missing[:3]), {"pos": pos_before, "missing": missing[:200], "count": len(missing)})
        try:
            if not os.path.exists(os.path.join(self.public, "latest.json")):
                raise FileNotFoundError                                      # absent is judged by the file-set comparison above
            ptr = B.get(os.path.join(self.public, "latest.json"))
            names = set()
            for p in (pos_before - TOL_S, pos_after + TOL_S):
                r = self.oracle.latest_pointer(max(0.0, p))
                if r:
                    names.add(B.get(os.path.join(self.store, r["src"]))["release"])
            if names and ptr["release"] not in names | {self.last_release}:
                self.violation("pointer_wrong", "latest.json says %s; the position allows %s" % (ptr["release"], sorted(names)), {"pos": pos_after, "pointer": ptr})
            self.last_release = ptr["release"]
        except FileNotFoundError:
            pass
        except Exception as e:
            self.violation("pointer_unreadable", str(e))
        info = {"pos": round(pos_after, 1), "public_files": len(listing), "public_gb": round(size / 1e9, 3), "free_gb": round(shutil.disk_usage(self.public).free / 1e9, 1)}
        if self.a.url:
            fut = sorted(self.oracle.expected(10 ** 9)[0] - self.oracle.expected(pos_after + 10)[0])
            random.shuffle(fut)
            bad = []
            for rel in fut[:15]:
                try:
                    st, _, _ = request(self.a.url, FEED + "/" + rel)
                except Exception as e:
                    self.violation("http_error", "%s: %s" % (rel, e)); continue
                if st != 404:
                    bad.append((rel, st))
            if bad:
                self.violation("future_url_served", "%d guessed future URLs were not 404: %s" % (len(bad), bad[:3]), {"pos": pos_after, "bad": bad})
            info["future_probes"] = min(15, len(fut))
        self.emit(ok=not (unexpected or missing), **info)

    def run(self):
        stop = {"f": False}
        signal.signal(signal.SIGTERM, lambda *_: stop.update(f=True))
        signal.signal(signal.SIGINT, lambda *_: stop.update(f=True))
        self.emit(start=True, root=self.a.root, public=self.public, url=self.a.url)
        last_full = 0.0
        while not stop["f"]:
            now = time.time()
            try:
                pos = self.cheap(now)
                if pos is not None and now - last_full >= self.a.full_every:
                    last_full = now
                    self.full(now, pos)
            except Exception as e:                                           # a monitor that dies silently proves nothing
                self.violation("monitor_error", "%s: %s" % (type(e).__name__, e))
            if self.a.url:
                try:
                    for rel in ("status.json", "broadcast/clock.json"):
                        st, h, _ = request(self.a.url, FEED + "/" + rel)
                        if st != 200 or cache_kind(h.get("cache-control")) != "no-store":
                            self.violation("http_rule", "%s -> %d %r" % (rel, st, h.get("cache-control")), {"headers": h})
                except Exception as e:
                    self.violation("http_error", str(e))
            time.sleep(max(0.0, self.a.every - (time.time() - now)))
        self.emit(stop=True, findings=self.found)
        return 1 if self.found else 0


PRODUCTION_PATHS = ("/srv/dflv2/feed", "/srv/dflv2/league", "/srv/dflv2/site", "/var/www/dfl", "/srv/dflv2/repo")


def preflight(root, public, min_free_gb=6.0, deadline=None):
    out = []

    def check(ok, what):
        out.append((bool(ok), what))
        return ok

    root, public = os.path.realpath(root), os.path.realpath(public)
    store = os.path.join(root, "store")
    check(public != root and not public.startswith(root + os.sep) and not root.startswith(public + os.sep), "the public folder and the private root are separate folders")
    for path, name in ((root, "private root"), (public, "public folder")):
        bad = [x for x in PRODUCTION_PATHS if path == x or path.startswith(x + os.sep) or x.startswith(path + os.sep)]
        check(not bad, "the %s is not, inside or around a production path (%s)" % (name, bad or "none"))
    empty = (not os.path.isdir(public)) or not os.listdir(public) or os.path.exists(os.path.join(public, "broadcast", "clock.json"))
    check(empty, "the public folder is new or was made by the daemon")
    try:
        plan, builds = B.get(os.path.join(store, S.PLAN)), B.get(os.path.join(store, S.BUILDS))
    except Exception as e:
        check(False, "plan.json and builds.json are readable (%s)" % e)
        return out
    check(plan.get("format") == S.PLAN_FORMAT and builds.get("format") == S.BUILDS_FORMAT, "plan and builds have the expected formats")
    n_events = len(plan.get("events", []))
    check(n_events > 0 and sorted(int(k) for k in builds["events"]) == list(range(1, n_events + 1)), "every one of the plan's %d events is built (%d in builds.json)" % (n_events, len(builds["events"])))
    view = S.materialize(plan, builds)
    in_windows = sum(len(w["games"]) for w in view.windows)
    check(in_windows == len(builds["games"]) and in_windows >= len(plan.get("games", {})), "every built game (%d) sits in a broadcast window (%d in windows; the plan names %d regular-season and Tour games, the rest are postseason)" % (len(builds["games"]), in_windows, len(plan.get("games", {}))))
    check(view.horizon is None, "nothing is unbuilt: the broadcast would never wait for the builder (horizon %s)" % view.horizon)
    missing = []
    gen = builds.get("genesis") or {}
    for rel in [gen.get("pointer")] + [e["pointer"] for e in builds["events"].values()]:
        if not rel or not os.path.exists(os.path.join(store, rel)):
            missing.append(rel)
    for rel in [gen.get("release")] + [e["release"] for e in builds["events"].values()]:
        if not rel or not os.path.isdir(os.path.join(store, "feed", "releases", rel)):
            missing.append("feed/releases/%s" % rel)
    for gid, g in builds["games"].items():
        d = os.path.join(store, "games", B.safe(gid))
        need = ["head.json", "end.json", "replay.json"] + ["s%04d.json" % i for i in range(g["segments"])]
        missing += ["games/%s/%s" % (B.safe(gid), n) for n in need if not os.path.exists(os.path.join(d, n))]
        if not os.path.exists(os.path.join(store, "playback", "games", os.path.basename(g["entry"]["file"]))):
            missing.append("playback/games/%s" % os.path.basename(g["entry"]["file"]))
    for part, rel in builds.get("honors", {}).items():
        if not os.path.exists(os.path.join(store, "honors", rel)):
            missing.append("honors/" + rel)
    check(not missing, "every release, pointer, game recording and honors file named in builds.json exists (%d missing %s)" % (len(missing), missing[:3]))
    check(len(builds.get("honors", {})) >= 1, "honors parts are built (%s)" % sorted(builds.get("honors", {})))
    check(not os.path.exists(os.path.join(store, "clock.json")) and not os.path.exists(os.path.join(store, S.PAUSE)), "no clock and no PAUSE file yet: this store has never been broadcast")
    check(abs(plan.get("total_s", 0) - 172800) < 4 * 3600, "planned length %.1f h (about 48)" % (plan.get("total_s", 0) / 3600.0))
    probe = public
    while not os.path.isdir(probe):
        probe = os.path.dirname(probe)
    free = shutil.disk_usage(probe).free / 1e9
    check(free >= min_free_gb, "%.1f GB free (need %.1f)" % (free, min_free_gb))
    if deadline is not None:
        left = deadline - time.time()
        check(0 < left <= 60 * 3600, "deadline is %.1f h from now (must be in the future and within 60 h)" % (left / 3600.0))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("http")
    p.add_argument("--url", required=True)
    p.add_argument("--store")
    p.add_argument("--sample", type=int, default=40)
    p.add_argument("--before-start", action="store_true", help="the daemon has not started: only the rules that hold with an empty feed (everything is 404)")
    p = sub.add_parser("preflight")
    p.add_argument("--root", required=True)
    p.add_argument("--public", required=True)
    p.add_argument("--min-free-gb", type=float, default=6.0)
    p.add_argument("--deadline")
    p = sub.add_parser("watch")
    p.add_argument("--root", required=True)
    p.add_argument("--public", required=True)
    p.add_argument("--url")
    p.add_argument("--log")
    p.add_argument("--evidence")
    p.add_argument("--every", type=float, default=10.0)
    p.add_argument("--full-every", type=float, default=300.0)
    p.add_argument("--min-free-gb", type=float, default=5.0)
    p.add_argument("--max-public-gb", type=float, default=3.0)
    a = ap.parse_args(argv)
    if a.cmd == "http":
        res = http_rules(a.url, a.store, a.sample, a.before_start)
        bad = [w for ok, w in res if not ok]
        for ok, w in res:
            if w:
                print(("PASS " if ok else "FAIL ") + w)
        print("%d checks, %d failed" % (len([1 for _, w in res if w]), len(bad)))
        return 1 if bad else 0
    if a.cmd == "preflight":
        import broadcast_run as BR
        res = preflight(a.root, a.public, a.min_free_gb, BR.parse_deadline(a.deadline) if a.deadline else None)
        for ok, w in res:
            if w:
                print(("PASS " if ok else "FAIL ") + w)
        bad = [w for ok, w in res if not ok]
        print("%d checks, %d failed" % (len([1 for _, w in res if w]), len(bad)))
        return 1 if bad else 0
    return Watch(a).run()


if __name__ == "__main__":
    sys.exit(main())
