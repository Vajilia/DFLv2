#!/usr/bin/env python3
"""Checks for the capped real-time broadcast rehearsal (docs/BROADCAST_REHEARSAL.md). Read-only: it never writes to the public folder or the store.

  rehearsal_check.py http  --url https://HOST [--store R/store] [--sample 40]
        One pass over the SERVER'S RULES: cache headers per kind of file, no directory listings, nothing private served, write methods refused, a usable Date
        header, and (with --store, run on the server) a sample of URLs that are named in the private store but not yet released, which must all answer 404.
  rehearsal_check.py watch --root R --public P [--url URL] [--log FILE] [--evidence DIR] [--guard DIR] [--canary FILE] [--cgroup DIR] [--every 10] [--full-every 30] [--min-free-gb 6] [--max-public-gb 3]
        A monitor for the whole rehearsal. Every cycle: the clock and status files agree, nothing moves backwards, the daemon's lease is alive (unless paused),
        no stale `.tmp-` debris, disk space. Every --full-every seconds: the whole public tree is compared with what the broadcast position allows
        (derived from the private plan and builds by tools/broadcast_season_check.py's independent Oracle): nothing early, nothing missing, and with --url a
        sample of guessed URLs. Every finding is one JSON line {"violation": KIND, ...} in the log, and the FIRST full detail of each kind is kept under --evidence
        (file listings, positions, response headers) so a failure is never lost to a rerun. The log ALSO holds normal lines: {"start"}, one {"heartbeat"} per full
        cycle, {"stop"}: a healthy log is full of heartbeats and holds ZERO "violation" lines. Exit status 1 if any violation was logged.
        With --guard DIR the monitor can STOP the broadcast: a spoiler-class violation (early file, a guessed future URL served, a wrong pointer, a private file
        reachable by path traversal), a resource violation (free disk under --min-free-gb, public folder over --max-public-gb, daemon memory over 90% of its cgroup limit)
        or the monitor's own failure (an exception, or the site unreachable three cycles in a row) writes DIR/TRIPPED, snapshots the evidence under
        --evidence/trip-<time>-<kind>/ and the daemon (run with the same --guard) pauses within a second. The monitor refreshes DIR/heartbeat every 5 s while it makes
        progress; if it dies or hangs the DAEMON trips itself (broadcast_run.py --guard). A trip is cleared only by `release` and then `broadcast_run.py resume`.
  rehearsal_check.py release --guard DIR --reason TEXT [--evidence DIR]
        Explicit resumption, step 1: moves DIR/TRIPPED to --evidence/released/ with the reason (nothing is deleted) and leaves the daemon paused. Refused without a
        reason of at least 10 characters, or while the monitor's heartbeat is stale (it would trip again at once). Step 2 is `broadcast_run.py resume --guard DIR`.
  rehearsal_check.py verdict --root R --public P --log FILE --guard DIR [--expect-events 47] [--expect-games 400]
        The result of the rehearsal. COMPLETE only if the broadcast reached its last item, the public tree is exactly what the plan releases (all events, all games,
        all honors parts, nothing missing, nothing extra) and the log holds zero violations and no trip was ever made. Reaching the deadline first is INCOMPLETE.
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
import gzip
import random
import shutil
import signal
import ssl
import sys
import threading
import time
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from dflsite import broadcast as B  # noqa: E402
from dflsite import season as S  # noqa: E402
import broadcast_season_check as SC  # noqa: E402
import broadcast_run as BR  # noqa: E402

FEED = "/feed"
# What stops the broadcast. Everything else a check can find is logged as a violation (and fails the rehearsal) but does not pause it.
SPOILER_KINDS = {"early_or_unknown_file", "future_url_served", "pointer_wrong", "private_exposed"}
RESOURCE_KINDS = {"disk_low", "public_too_large", "memory_high"}
MONITOR_KINDS = {"monitor_error", "monitor_blind"}
TRIP_KINDS = SPOILER_KINDS | RESOURCE_KINDS | MONITOR_KINDS
HEARTBEAT_EVERY_S = 5.0
HANG_S = 90.0                 # no progress for this long: the heartbeat stops and the daemon trips itself after BR.GUARD_STALE_S
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


def http_rules(base, store=None, sample=40, before_start=False, canary=None):
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
    # ---- a harmless private canary must be unreachable by any path, whatever the encoding
    if canary:
        check(os.path.isfile(canary) and bool(read_canary(canary)), "the canary file exists and holds a token (%s): without it the traversal test proves nothing" % canary)
        paths = traversal_paths(True)
        bad = traversal_probe(base, canary, True, paths)
        check(not bad, "%d traversal and canary paths tried (encoded, double-encoded, backslash, overlong, null, ..;): none reached private content%s" % (len(paths), "" if not bad else "; SERVED: %s" % bad[:5]))
        for rel in ("/feed/" + CANARY_NAME, "/" + CANARY_NAME):
            st, _, body = get(rel)
            check(st == 404, "%s is 404 (got %d)" % (rel, st))
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


CANARY_NAME = "CANARY.txt"
UPS = ("../", "%2e%2e/", "%2E%2E/", "..%2f", "..%2F", "%2e%2e%2f", ".%2e/", "%2e./", "%252e%252e%252f", "%252e%252e/", "..%5c", "..\\", "%c0%ae%c0%ae/", "....//", "..;/", "./../", "%2e%2e%5c", "..%00/")
TARGETS = ("private/store/" + CANARY_NAME, "private/store/plan.json", "private/store/builds.json", "private/store/clock.json", "monitor/watch.log", "etc/passwd")
PREFIXES = ("/feed/", "/feed/broadcast/", "/")


def traversal_paths(full=True):
    """Request paths that try to climb out of the feed or the site folder to the private store, in the encodings a careless server follows."""
    out = []
    for pre in PREFIXES:
        for up in UPS:
            for depth in (1, 2, 3):
                out.append(pre + up * depth + TARGETS[0])
    if full:
        for up in UPS[:8]:
            for t in TARGETS[1:]:
                out.append("/feed/" + up + t)
                out.append("/feed/" + up * 2 + t)
                out.append("/" + up + t)
    out += ["//" + TARGETS[0], "/feed//" + TARGETS[0], "/feed/" + CANARY_NAME, "/" + CANARY_NAME, "/private/store/" + CANARY_NAME, "/feed/private/store/" + CANARY_NAME,
            "/feed/./../" + TARGETS[0], "/feed/x/../../" + TARGETS[0]]
    return out


def read_canary(path):
    """The canary file's text (its random token), or None."""
    try:
        with open(path) as f:
            return f.read().strip() or None
    except OSError:
        return None


def traversal_probe(base, canary_path=None, full=True, paths=None):
    """-> list of (path, status) that were SERVED (200) or that returned private content with any status. Empty = nothing reached."""
    token = read_canary(canary_path) if canary_path else None
    bad = []
    for path in paths if paths is not None else traversal_paths(full):
        try:
            st, _, body = request(base, path)
        except (ValueError, http.client.InvalidURL, http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError):
            continue                                                          # the client cannot form the request, or the server dropped it: nothing was served
        leaked = (token and token.encode() in body) or b'"format": "dfl' in body[:400] or b'"format":"dfl' in body[:400] or b"root:x:0:0" in body
        if st == 200 or leaked:
            bad.append((path, st))
    return bad


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
        self.guard = getattr(a, "guard", None)
        self.canary = getattr(a, "canary", None)
        self.cgroup = getattr(a, "cgroup", None)
        self.progress_at = time.time()
        self.blind = 0
        self.stop = threading.Event()
        self.probe_i = 0

    def progress(self):
        self.progress_at = time.time()

    def beat(self):
        """The heartbeat: the daemon trips itself if this file stops being refreshed. Written only while the main loop makes progress, so a HUNG monitor is as silent as a dead one."""
        if not self.guard:
            return
        tmp = os.path.join(self.guard, ".heartbeat.tmp")
        with open(tmp, "w") as f:
            json.dump({"at": time.time(), "pid": os.getpid(), "progress_at": self.progress_at}, f)
        os.replace(tmp, os.path.join(self.guard, "heartbeat"))

    def beat_loop(self):
        while not self.stop.wait(HEARTBEAT_EVERY_S):
            if time.time() - self.progress_at < HANG_S:
                try:
                    self.beat()
                except OSError:
                    pass

    def emit(self, **kw):
        kw["at"] = round(time.time(), 1)
        self.log.write(json.dumps(kw, sort_keys=True) + "\n")
        self.log.flush()

    def violation(self, kind, detail, evidence=None):
        self.found += 1
        self.emit(violation=kind, detail=detail)
        if self.guard and kind in TRIP_KINDS:
            self.trip(kind, detail, evidence)
        if self.evidence and kind not in self.kinds:                         # the FIRST full record of each kind is kept; reruns never replace it
            self.kinds.add(kind)
            os.makedirs(self.evidence, exist_ok=True)
            path = os.path.join(self.evidence, "%s-%s.json" % (time.strftime("%Y%m%dT%H%M%S", time.gmtime()), kind))
            with open(path, "w") as f:
                json.dump({"kind": kind, "detail": detail, "evidence": evidence, "at": time.time()}, f, indent=1, sort_keys=True, default=str)

    def trip(self, kind, detail, evidence):
        """Stop the broadcast and keep everything needed to understand why. Never raises: a monitor that cannot trip exits, and the silent heartbeat trips the daemon."""
        now = time.time()
        latch_exists = os.path.exists(BR.trip_file(self.guard))
        d = None
        try:
            if not latch_exists:                                               # one full snapshot per latch; later trips of the same stoppage only add a line
                d = os.path.join(self.evidence or os.path.join(self.guard, "evidence"), "trip-%s-%s" % (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now)), kind))
                os.makedirs(d, exist_ok=True)
                snap = {"kind": kind, "detail": detail, "evidence": evidence, "at": now}
                for name, path in (("store_clock", os.path.join(self.store, "clock.json")), ("public_status", os.path.join(self.public, "status.json")),
                                   ("public_clock", os.path.join(self.public, "broadcast", "clock.json"))):
                    try:
                        snap[name] = B.get(path)
                    except Exception as e:
                        snap[name] = "unreadable: %s" % e
                snap["pause_file"] = os.path.exists(os.path.join(self.store, S.PAUSE))
                with open(os.path.join(d, "evidence.json"), "w") as f:
                    json.dump(snap, f, indent=1, sort_keys=True, default=str)
                with gzip.open(os.path.join(d, "public_listing.txt.gz"), "wt") as f:
                    for rel in sorted(SC.list_public(self.public)):
                        try:
                            st = os.stat(os.path.join(self.public, rel))
                            f.write("%s\t%d\t%.0f\n" % (rel, st.st_size, st.st_mtime))
                        except OSError:
                            f.write("%s\t?\t?\n" % rel)
        except Exception as e:
            self.emit(trip_evidence_error=str(e)[:200])
        try:
            made = BR.write_trip(self.guard, kind, detail, "monitor", now)
            self.emit(trip=kind, latch_created=made, evidence_dir=d)
        except Exception as e:
            self.emit(trip_failed=kind, detail=str(e)[:200])
            raise SystemExit(2)                                                # cannot stop the broadcast: stop being a monitor, so the heartbeat goes silent

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
            self.violation("disk_low", "%.1f GB free (the floor is %.1f GB)" % (free, self.a.min_free_gb), {"free_gb": free})
        if self.cgroup:
            try:
                cur = int(open(os.path.join(self.cgroup, "memory.current")).read())
                mx = open(os.path.join(self.cgroup, "memory.max")).read().strip()
                if mx.isdigit() and cur > 0.9 * int(mx):
                    self.violation("memory_high", "daemon memory %.0f MB of a %.0f MB limit" % (cur / 1e6, int(mx) / 1e6), {"current": cur, "max": int(mx)})
            except (OSError, ValueError):
                pass                                                           # no cgroup files (not systemd, or not yet started): the unit's MemoryMax still holds
        return pos

    def full(self, now, pos_before):
        self.progress()
        listing = SC.list_public(self.public)
        self.progress()
        pos_after = self.clock.position(time.time())
        size = sum(os.path.getsize(os.path.join(self.public, f)) for f in listing)
        if size > self.a.max_public_gb * 1e9:
            self.violation("public_too_large", "%.2f GB" % (size / 1e9), {"bytes": size})
        self.progress()
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
            self.progress()
            if self.canary:                                                   # a rotating slice of the traversal paths each cycle; the full set runs in `http`
                allp = traversal_paths(True)
                n = 12
                sl = [allp[(self.probe_i + k) % len(allp)] for k in range(n)]
                self.probe_i = (self.probe_i + n) % len(allp)
                try:
                    exposed = traversal_probe(self.a.url, self.canary, True, sl)
                except Exception as e:
                    self.violation("http_error", "traversal probe: %s" % e)
                else:
                    if exposed:
                        self.violation("private_exposed", "private content reachable: %s" % exposed[:3], {"exposed": exposed})
                    info["traversal_probes"] = len(sl)
        self.progress()
        self.emit(heartbeat=True, ok=not (unexpected or missing), **info)

    def run(self):
        stop = {"f": False}
        signal.signal(signal.SIGTERM, lambda *_: stop.update(f=True))
        signal.signal(signal.SIGINT, lambda *_: stop.update(f=True))
        self.emit(start=True, root=self.a.root, public=self.public, url=self.a.url, guard=self.guard)
        if self.guard:
            os.makedirs(self.guard, exist_ok=True)
            self.beat()
            threading.Thread(target=self.beat_loop, daemon=True).start()
        last_full = 0.0
        while not stop["f"] and not self.stop.is_set():
            now = time.time()
            self.progress()
            try:
                pos = self.cheap(now)
                if pos is not None and now - last_full >= self.a.full_every:
                    last_full = now
                    self.full(now, pos)
            except SystemExit:
                raise
            except Exception as e:                                           # a monitor that cannot do its job must stop the broadcast, not fall silent
                import traceback
                self.violation("monitor_error", "%s: %s" % (type(e).__name__, e), {"traceback": traceback.format_exc()[-1500:]})
            self.progress()
            if self.a.url:
                try:
                    for rel in ("status.json", "broadcast/clock.json"):
                        st, h, _ = request(self.a.url, FEED + "/" + rel)
                        if st != 200 or cache_kind(h.get("cache-control")) != "no-store":
                            if st == 404 and not os.path.exists(os.path.join(self.public, rel)):
                                continue                                      # not published yet (the daemon has not started)
                            self.violation("http_rule", "%s -> %d %r" % (rel, st, h.get("cache-control")), {"headers": h})
                    self.blind = 0
                except Exception as e:
                    self.blind += 1
                    self.violation("http_error", "%s (%d in a row)" % (e, self.blind))
                    if self.blind >= 3:                                       # cannot see the site: the spoiler protection cannot be checked
                        self.violation("monitor_blind", "the site could not be reached %d cycles in a row: %s" % (self.blind, e))
            time.sleep(max(0.0, self.a.every - (time.time() - now)))
        self.stop.set()
        self.emit(stop=True, findings=self.found)
        return 1 if self.found else 0


PRODUCTION_PATHS = ("/srv/dflv2/feed", "/srv/dflv2/league", "/srv/dflv2/site", "/var/www/dfl", "/srv/dflv2/repo")


def preflight(root, public, min_free_gb=6.0, deadline=None, growth_gb=0.0, require_canary=False, guard=None):
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
    check(free >= min_free_gb + growth_gb, "%.1f GB free (need %.1f: the %.1f GB floor the monitor stops the broadcast at, plus %.1f GB the public folder may grow to)" % (free, min_free_gb + growth_gb, min_free_gb, growth_gb))
    if require_canary:
        c = read_canary(os.path.join(store, CANARY_NAME))
        check(bool(c) and c.startswith("DFL-REHEARSAL-CANARY-"), "the private canary is in the store (it must never be served)")
    if guard:
        check(not os.path.exists(BR.trip_file(guard)) and not os.path.exists(os.path.join(guard, "trips.jsonl")), "no earlier trip in %s (a fresh rehearsal starts with a clean guard folder)" % guard)
    if deadline is not None:
        left = deadline - time.time()
        check(0 < left <= 60 * 3600, "deadline is %.1f h from now (must be in the future and within 60 h)" % (left / 3600.0))
    return out


def release(a):
    """Explicit resumption, step 1 (step 2 is `broadcast_run.py resume --guard`). Nothing is deleted: the latch moves into the evidence folder with the reason."""
    latch = BR.trip_file(a.guard)
    if not os.path.exists(latch):
        print("nothing to release: %s does not exist" % latch)
        return 0
    if len(a.reason.strip()) < 10:
        print("refusing: give a reason of at least 10 characters (what was found, what you did about it)", file=sys.stderr)
        return 2
    try:
        age = time.time() - os.path.getmtime(os.path.join(a.guard, "heartbeat"))
    except OSError:
        age = None
    if age is None or age > 30:
        print("refusing: the monitor's heartbeat is stale or missing; start the monitor first (otherwise the daemon trips again at once)", file=sys.stderr)
        return 4
    rec = json.load(open(latch))
    now = time.time()
    if getattr(a, "drill", False):
        if not (rec.get("kind") == "monitor_silent" and rec.get("source") == "daemon"):
            print("refusing --drill: only a trip made by the daemon because the monitor was deliberately stopped (monitor_silent) can be a drill; this one is %s from %s" % (rec.get("kind"), rec.get("source")), file=sys.stderr)
            return 2
        rec["drill"] = True
        with open(os.path.join(a.guard, "drills.jsonl"), "a") as f:
            f.write(json.dumps({"from": rec["at"], "to": now, "kind": rec["kind"], "reason": a.reason.strip()}) + "\n")
    rec["released_at"], rec["released_reason"] = now, a.reason.strip()
    dest = os.path.join(a.evidence or os.path.join(a.guard, "evidence"), "released")
    os.makedirs(dest, exist_ok=True)
    path = os.path.join(dest, "%s-TRIPPED.json" % time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now)))
    with open(path, "w") as f:
        json.dump(rec, f, indent=1, sort_keys=True)
    os.unlink(latch)
    print("released (kept as %s). The daemon is still PAUSED. To continue:  broadcast_run.py resume --root ... --guard %s" % (path, a.guard))
    return 0


def done_at(oracle):
    """The broadcast position at which the last item (game recording, reveal) is released."""
    ts = [r["at_s"] for r in oracle.view.reveals] + [w["start_s"] + g["length_s"] + B.DELAY_S for w, g in oracle.games.values()]
    return max(ts) if ts else 0.0


def verdict(a):
    """COMPLETE (exit 0): finished, exactly right, clean. INCOMPLETE (exit 2): clean so far but not finished (a deadline reached first is this). FAILED (exit 1): a violation or a trip."""
    store = os.path.join(os.path.abspath(a.root), "store")
    plan, builds = B.get(os.path.join(store, S.PLAN)), B.get(os.path.join(store, S.BUILDS))
    view = S.materialize(plan, builds)
    oracle = SC.Oracle(store, view, builds)
    out = []

    def line(ok, what):
        out.append((ok, what))

    line(len(builds["events"]) == a.expect_events and len(builds["games"]) == a.expect_games,
         "the store is the season that was approved: %d events, %d games (expected %d and %d)" % (len(builds["events"]), len(builds["games"]), a.expect_events, a.expect_games))
    viol, beats, first, last = [], 0, None, None
    try:
        for ln in open(a.log):
            try:
                rec = json.loads(ln)
            except ValueError:
                viol.append({"violation": "unparseable_log_line", "detail": ln[:100]})
                continue
            if "violation" in rec:
                viol.append(rec)
            if rec.get("heartbeat"):
                beats += 1
                first = first or rec["at"]
                last = rec["at"]
    except OSError as e:
        viol.append({"violation": "log_unreadable", "detail": str(e)})
    trips, drills = [], []
    try:
        drills = [json.loads(ln) for ln in open(os.path.join(a.guard, "drills.jsonl")) if ln.strip()]
    except (OSError, ValueError):
        pass
    try:
        for ln in open(os.path.join(a.guard, "trips.jsonl")):
            if ln.strip():
                t = json.loads(ln)
                if not any(d["from"] - 1 <= t["at"] <= d["to"] + 1 for d in drills):
                    trips.append(ln)
    except (OSError, ValueError):
        pass
    latched = os.path.exists(BR.trip_file(a.guard))
    clean = not viol and not trips and not latched
    line(beats > 0, "the monitor logged %d heartbeat lines%s (normal entries; they are not violations)" % (beats, "" if not beats else ", %s to %s" % (time.strftime("%Y-%m-%d %H:%M", time.gmtime(first)), time.strftime("%H:%M UTC", time.gmtime(last)))))
    kinds = sorted({v.get("violation") for v in viol})
    line(not viol, "violation entries in the log: %d%s" % (len(viol), "" if not viol else " (%s)" % ", ".join(kinds)))
    line(not trips and not latched, "trips (automatic pauses): %d%s%s" % (len(trips), " and one is still latched" if latched else "", "; %d planned monitor-stop drill(s) excluded" % len(drills) if drills else ""))
    clock = B.Clock(store, os.path.join(store, "_unused"))
    st = clock.state()
    pos = clock.position(time.time()) if st else 0.0
    need = done_at(oracle)
    finished = st is not None and pos >= need
    line(finished, "the broadcast reached its last item: position %.2f h of %.2f h (clock %s)" % (pos / 3600.0, need / 3600.0, st["state"] if st else "never started"))
    listing = SC.list_public(a.public)
    everything, ended = oracle.expected(10 ** 9)
    miss, extra = sorted(everything - listing), sorted(listing - everything)
    released = sum(1 for gid, (w, g) in oracle.games.items() if "broadcast/replays/" + g["replay"] in listing)
    line(released == a.expect_games, "games released with their replay: %d of %d" % (released, a.expect_games))
    try:
        stj = B.get(os.path.join(a.public, "status.json"))
        n = stj.get("published_events", stj.get("league_events"))
    except Exception:
        n = None
    line(n == a.expect_events, "events published: %s of %d" % (n, a.expect_events))
    line(not miss and not extra, "the public folder is exactly what the plan releases: %d files, %d missing, %d extra%s" % (len(listing), len(miss), len(extra), "" if not (miss or extra) else " e.g. %s" % (miss[:2] + extra[:2])))
    for ok, w in out:
        print(("PASS " if ok else "FAIL ") + w)
    if viol or trips or latched:
        print("RESULT: FAILED. Violations or automatic pauses occurred; keep the log and the evidence folder.")
        return 1
    if not (finished and released == a.expect_games and n == a.expect_events and not miss and not extra):
        print("RESULT: INCOMPLETE. Not a pass: every event and game must be released correctly. (Reaching the deadline first is an incomplete run.)")
        return 2
    if not all(ok for ok, _ in out):
        print("RESULT: FAILED. See the FAIL lines.")
        return 1
    print("RESULT: COMPLETE. All %d events and %d games released correctly, zero violations, no automatic pause." % (a.expect_events, a.expect_games))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("http")
    p.add_argument("--url", required=True)
    p.add_argument("--store")
    p.add_argument("--sample", type=int, default=40)
    p.add_argument("--before-start", action="store_true", help="the daemon has not started: only the rules that hold with an empty feed (everything is 404)")
    p.add_argument("--canary", help="the private canary file on this machine: tries to reach it by path traversal in many encodings (must never be served)")
    p = sub.add_parser("preflight")
    p.add_argument("--root", required=True)
    p.add_argument("--public", required=True)
    p.add_argument("--min-free-gb", type=float, default=6.0)
    p.add_argument("--growth-gb", type=float, default=0.0, help="extra free space required for the public folder to grow to its cap")
    p.add_argument("--require-canary", action="store_true")
    p.add_argument("--guard", help="the monitor's folder: must hold no earlier trip")
    p.add_argument("--deadline")
    p = sub.add_parser("watch")
    p.add_argument("--root", required=True)
    p.add_argument("--public", required=True)
    p.add_argument("--url")
    p.add_argument("--log")
    p.add_argument("--evidence")
    p.add_argument("--every", type=float, default=10.0)
    p.add_argument("--full-every", type=float, default=30.0)
    p.add_argument("--min-free-gb", type=float, default=6.0)
    p.add_argument("--max-public-gb", type=float, default=3.0)
    p.add_argument("--guard", help="the monitor's folder: write TRIPPED and the heartbeat here so the daemon (run with --guard) pauses")
    p.add_argument("--canary", help="the private canary file (its token must never be served)")
    p.add_argument("--cgroup", help="the daemon's cgroup folder (memory.current, memory.max)")
    p = sub.add_parser("release")
    p.add_argument("--guard", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--evidence")
    p.add_argument("--drill", action="store_true", help="the trip was the planned monitor-stop drill (only a daemon-made monitor_silent trip qualifies); the verdict excludes it")
    p = sub.add_parser("verdict")
    p.add_argument("--root", required=True)
    p.add_argument("--public", required=True)
    p.add_argument("--log", required=True)
    p.add_argument("--guard", required=True)
    p.add_argument("--expect-events", type=int, default=47)
    p.add_argument("--expect-games", type=int, default=400)
    a = ap.parse_args(argv)
    if a.cmd == "release":
        return release(a)
    if a.cmd == "verdict":
        return verdict(a)
    if a.cmd == "http":
        res = http_rules(a.url, a.store, a.sample, a.before_start, a.canary)
        bad = [w for ok, w in res if not ok]
        for ok, w in res:
            if w:
                print(("PASS " if ok else "FAIL ") + w)
        print("%d checks, %d failed" % (len([1 for _, w in res if w]), len(bad)))
        return 1 if bad else 0
    if a.cmd == "preflight":
        res = preflight(a.root, a.public, a.min_free_gb, BR.parse_deadline(a.deadline) if a.deadline else None, a.growth_gb, a.require_canary, a.guard)
        for ok, w in res:
            if w:
                print(("PASS " if ok else "FAIL ") + w)
        bad = [w for ok, w in res if not ok]
        print("%d checks, %d failed" % (len([1 for _, w in res if w]), len(bad)))
        return 1 if bad else 0
    return Watch(a).run()


if __name__ == "__main__":
    sys.exit(main())
