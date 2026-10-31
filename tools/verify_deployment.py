#!/usr/bin/env python3
"""Check a running copy of the website from the outside, the way a visitor (or a careful person with a laptop) would. Read-only: it only sends GET,
HEAD, and one POST that must be refused.

  verify_deployment.py https://preview.diamondfootballleague.com                       full check of the staging site (Caddy, HTTPS)
  verify_deployment.py http://127.0.0.1:8080 --profile local                           the same content checks against `tools/preview_site.py`
  verify_deployment.py URL --production https://diamondfootballleague.com --save-baseline prod.json     remember what the live site looks like now
  verify_deployment.py URL --production https://diamondfootballleague.com --baseline prod.json         ...and later prove it has not changed

Standard library only (no league, no engine, no numpy), so it runs on the server, a laptop, or anywhere with Python 3.9+.
Exit status 0 when nothing FAILED (warnings are listed), 1 otherwise.
"""
import argparse
import hashlib
import http.client
import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def fetch(url, method="GET", follow=True, timeout=20, data=None):
    """-> (status, headers (lower-case keys), body bytes). HTTP errors are returned, not raised; network failures raise OSError."""
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(NoRedirect)
    req = urllib.request.Request(url, method=method, data=data, headers={"User-Agent": "dfl-verify/1"})
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()


class Report:
    def __init__(self):
        self.rows = []

    def add(self, level, name, detail=""):
        self.rows.append((level, name, detail))
        print(f"{level:5} {name}" + (f"  -- {detail}" if detail else ""), flush=True)

    def ok(self, name, detail=""):
        self.add("PASS", name, detail)

    def warn(self, name, detail=""):
        self.add("WARN", name, detail)

    def fail(self, name, detail=""):
        self.add("FAIL", name, detail)

    def check(self, cond, name, detail_ok="", detail_bad=""):
        (self.ok if cond else self.fail)(name, detail_ok if cond else detail_bad)
        return cond

    def failed(self):
        return [r for r in self.rows if r[0] == "FAIL"]


def sha(b):
    return hashlib.sha256(b).hexdigest()


def check_site(base, rep, profile, require_map):
    base = base.rstrip("/")
    st, h, body = fetch(base + "/")
    rep.check(st == 200, "home page answers 200", f"{len(body)} bytes", f"HTTP {st}")
    page = body.decode("utf-8", "replace")
    rep.check("assets/app.js" in page and 'id="app"' in page, "home page is the DFL viewer", "", "index.html does not look like the viewer")
    rep.check("text/html" in h.get("content-type", ""), "home page is served as HTML", h.get("content-type", ""), h.get("content-type", "(none)"))
    for p in ("assets/app.js", "assets/base.css", "assets/site.css"):
        st, h2, b2 = fetch(f"{base}/{p}")
        rep.check(st == 200 and len(b2) > 100, f"/{p} answers 200", f"{len(b2)} bytes", f"HTTP {st}")
    # ---- the feed, whole and consistent
    st, hp, raw_ptr = fetch(base + "/feed/latest.json")
    if not rep.check(st == 200, "feed pointer answers 200", "", f"HTTP {st}"):
        return None
    ptr = json.loads(raw_ptr)
    rep.check(ptr.get("format") == "dfl-public-feed" and ptr.get("format_version") == 1, "feed pointer is dfl-public-feed version 1",
              f"release {ptr.get('release')}", json.dumps(ptr)[:120])
    rel = ptr["release"]
    st, _, raw_m = fetch(f"{base}/feed/releases/{rel}/manifest.json")
    if not rep.check(st == 200, "manifest answers 200", "", f"HTTP {st}"):
        return None
    rep.check(sha(raw_m) == ptr["manifest_sha256"], "manifest matches the pointer's SHA-256")
    manifest = json.loads(raw_m)
    files, bad = {"manifest.json": manifest}, []
    for name, entry in manifest["files"].items():
        st, _, b = fetch(f"{base}/feed/releases/{rel}/{name}")
        if st != 200 or len(b) != entry["bytes"] or sha(b) != entry["sha256"]:
            bad.append(f"{name} (HTTP {st})")
        else:
            files[name] = json.loads(b)
    rep.check(not bad, f"all {len(manifest['files'])} feed files match the manifest (size and SHA-256)", "", "; ".join(bad[:5]))
    try:
        from dflsite import feed
        errs = feed.check_public(files)
        rep.check(not errs, "the served feed passes the public-data check (no hidden fields)", "", "; ".join(errs[:3]))
    except ImportError as e:                                           # pragma: no cover
        rep.warn("public-data check skipped", str(e))
    rep.ok("simulation date and last published update", f"simulation date {manifest.get('simulation_date')}; published {manifest.get('generated_at')}; "
           f"events {manifest.get('events_done')} of {manifest.get('events_total')}; label: {manifest['league']['label'][:60]}...")
    rep.check(manifest["league"].get("kind") == "development", "the feed says it is a development league")
    # ---- nothing can be written through the site
    for method in ("POST", "PUT", "DELETE"):
        st, _, _ = fetch(base + "/", method=method, data=b"x" if method != "DELETE" else None)
        rep.check(st in (405, 501, 403), f"{method} is refused", f"HTTP {st}", f"HTTP {st} (a write method must not succeed)")
    # ---- the map
    st, _, b = fetch(base + "/assets/map/map.json")
    if st == 200:
        try:
            m = json.loads(b)
            if m.get("file") is None:
                raise LookupError("no map")
            st2, h3, b3 = fetch(f"{base}/assets/map/{m['file']}")
            rep.check(st2 == 200 and h3.get("content-type", "").startswith(("image/", "application/pdf")), "the official map file is served",
                      f"{m['file']}, {len(b3)} bytes, {h3.get('content-type')}", f"HTTP {st2} {h3.get('content-type')}")
        except LookupError:
            if require_map:
                rep.fail("the official map is installed", "assets/map/map.json says file: null")
            else:
                rep.warn("the official map is not installed yet", "the Map page will say so")
        except (ValueError, KeyError):
            rep.fail("the map description is readable")
    elif require_map:
        rep.fail("the official map is installed", f"assets/map/map.json answers HTTP {st}")
    else:
        rep.warn("the official map is not installed yet", "the Map page will say so")
    return ptr


def check_caddy(base, host, rep):
    """What the Caddy configuration promises: headers, caching, HTTPS."""
    base = base.rstrip("/")
    st, h, _ = fetch(base + "/")
    rep.check("noindex" in h.get("x-robots-tag", ""), "search engines are told not to index (X-Robots-Tag)", h.get("x-robots-tag", ""), "header missing")
    csp = h.get("content-security-policy", "")
    rep.check("default-src 'none'" in csp and "script-src 'self'" in csp, "Content-Security-Policy is the strict one", "", csp[:80] or "header missing")
    rep.check(h.get("x-content-type-options", "").lower() == "nosniff", "X-Content-Type-Options: nosniff")
    st, h, _ = fetch(base + "/feed/latest.json")
    rep.check("no-store" in h.get("cache-control", ""), "the pointer is never cached", h.get("cache-control", ""), h.get("cache-control", "(none)"))
    ptr = json.loads(fetch(base + "/feed/latest.json")[2])
    st, h, _ = fetch(f"{base}/feed/releases/{ptr['release']}/manifest.json")
    rep.check("immutable" in h.get("cache-control", ""), "a published release is cacheable for good", h.get("cache-control", ""), h.get("cache-control", "(none)"))
    st, _, b = fetch(base + "/feed/.publish.lock")
    rep.check(st == 404, "the publisher lock file is not served", f"HTTP {st}", f"HTTP {st}: {b[:60]!r}")
    st, h, b = fetch(base + "/feed/releases/../../etc/passwd")
    rep.check(st in (400, 403, 404) and b"root:" not in b, "path traversal is refused", f"HTTP {st}", f"HTTP {st}")
    if base.startswith("https://"):
        hostname = urllib.parse.urlparse(base).hostname
        try:
            ctx = ssl.create_default_context()
            with socket.create_connection((hostname, 443), timeout=15) as s, ctx.wrap_socket(s, server_hostname=hostname) as t:
                cert = t.getpeercert()
            na = ssl.cert_time_to_seconds(cert["notAfter"])
            days = int((na - time.time()) // 86400)
            issuer = dict(x[0] for x in cert["issuer"]).get("organizationName", "?")
            sans = [v for k, v in cert.get("subjectAltName", ()) if k == "DNS"]
            rep.check(hostname in sans and days > 14, "HTTPS certificate is valid for this host and not about to expire",
                      f"issuer {issuer}, {days} days left, names {sans}", f"names {sans}, {days} days left")
        except (ssl.SSLError, OSError) as e:
            rep.fail("HTTPS certificate verifies", str(e))
        try:
            c = http.client.HTTPConnection(hostname, 80, timeout=15)
            c.request("GET", "/")
            r = c.getresponse()
            loc = r.getheader("Location", "")
            rep.check(r.status in (301, 302, 307, 308) and loc.startswith("https://"), "plain http redirects to https", f"HTTP {r.status} -> {loc}",
                      f"HTTP {r.status} {loc}")
            c.close()
        except OSError as e:
            rep.fail("plain http on port 80 answers", str(e))
    else:
        rep.warn("HTTPS not checked", "the address given is not https://")


def production_fingerprint(url):
    fp = {"url": url, "taken": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    st, h, body = fetch(url)
    fp.update(status=st, sha256=sha(body), bytes=len(body), etag=h.get("etag"), last_modified=h.get("last-modified"), content_type=h.get("content-type"))
    return fp


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("url", nargs="?", help="the site to check (omit it to only save/compare a production baseline)")
    ap.add_argument("--profile", choices=("caddy", "local"), default="caddy", help="caddy: also check the headers and HTTPS the server configuration promises")
    ap.add_argument("--require-map", action="store_true")
    ap.add_argument("--production", help="the live site's address, to prove it is untouched")
    ap.add_argument("--save-baseline")
    ap.add_argument("--baseline")
    a = ap.parse_args(argv)
    rep = Report()
    if a.url:
        try:
            check_site(a.url, rep, a.profile, a.require_map)
            if a.profile == "caddy":
                check_caddy(a.url, urllib.parse.urlparse(a.url).hostname, rep)
        except OSError as e:
            rep.fail("the site can be reached", f"{type(e).__name__}: {e}")
    if a.production:
        try:
            fp = production_fingerprint(a.production)
            if a.save_baseline:
                with open(a.save_baseline, "w") as f:
                    json.dump(fp, f, indent=1)
                rep.ok("production baseline saved", f"{a.save_baseline}: HTTP {fp['status']}, {fp['bytes']} bytes, sha256 {fp['sha256'][:12]}")
            if a.baseline:
                with open(a.baseline) as f:
                    was = json.load(f)
                same = (was["status"], was["sha256"]) == (fp["status"], fp["sha256"])
                rep.check(same, "the production site is unchanged since the baseline", f"{was['taken']} -> now: same status and content",
                          f"was HTTP {was['status']} sha {was['sha256'][:12]}, now HTTP {fp['status']} sha {fp['sha256'][:12]} (expected only after an approved cutover)")
        except OSError as e:
            rep.fail("the production site can be reached", f"{type(e).__name__}: {e}")
    f = rep.failed()
    print(f"\n{len(rep.rows) - len(f)} not failed, {len(f)} FAILED, {sum(1 for r in rep.rows if r[0] == 'WARN')} warnings")
    return 1 if f else 0


if __name__ == "__main__":
    sys.exit(main())
