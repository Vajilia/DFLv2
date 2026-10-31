#!/usr/bin/env python3
"""Probe the public website the way a visitor would, and keep a private log of what it saw (for the capped development-league trial).

  probe_site.py --log FILE URL [URL ...]      one probe of each site: appends one JSON line per site to FILE and prints it
  probe_site.py --summary FILE                summarize a log: probes, failures, slowest and median answer times, and any disagreement between sites

Each probe is read-only (GET): the page, /feed/latest.json and /feed/status.json. It records the HTTP status and milliseconds of each, the live release, the
rules edition is NOT read (that is the feed's business), and how old the exporter's heartbeat is. A site "disagrees" when two sites that were probed together
name different releases. Never run against anything but the DFL websites; nothing is sent but plain GET requests.
"""
import argparse
import calendar
import json
import statistics
import sys
import time
import urllib.error
import urllib.request


def get(url, timeout=20):
    t0 = time.time()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Cache-Control": "no-cache", "User-Agent": "dfl-site-probe/1"}), timeout=timeout) as r:
            body = r.read()
            return r.status, round((time.time() - t0) * 1000), body
    except urllib.error.HTTPError as e:
        return e.code, round((time.time() - t0) * 1000), b""
    except Exception as e:                                           # timeouts, DNS, TLS: recorded, never raised
        return type(e).__name__, round((time.time() - t0) * 1000), b""


def probe(base, now=None):
    base = base.rstrip("/")
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now or time.time())), "site": base.split("//")[-1]}
    for key, path in (("page", "/"), ("latest", "/feed/latest.json"), ("status", "/feed/status.json")):
        code, ms, body = get(base + path)
        rec[key] = {"code": code, "ms": ms}
        if code == 200 and key == "latest":
            try:
                rec["release"] = json.loads(body)["release"]
            except (ValueError, KeyError):
                rec["latest"]["code"] = "bad_json"
        if code == 200 and key == "status":
            try:
                st = json.loads(body)
                rec["export_ok"] = bool(st["export"]["ok"])
                rec["league_events"], rec["published_events"] = st.get("league_events"), st.get("published_events")
                rec["runner_state"] = (st.get("runner") or {}).get("state")
                chk = st.get("checked_at")
                rec["heartbeat_age_s"] = round((now or time.time()) - calendar.timegm(time.strptime(chk, "%Y-%m-%dT%H:%M:%SZ"))) if chk else None
            except (ValueError, KeyError, TypeError):
                rec["status"]["code"] = "bad_json"
    rec["ok"] = all(rec[k]["code"] == 200 for k in ("page", "latest", "status")) and rec.get("export_ok") is True
    return rec


def summarize(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    out = {"probes": len(rows), "failures": [r for r in rows if not r["ok"]][:10], "failure_count": sum(1 for r in rows if not r["ok"])}
    for site in sorted({r["site"] for r in rows}):
        ms = [r[k]["ms"] for r in rows if r["site"] == site for k in ("page", "latest", "status")]
        out[site] = {"probes": sum(1 for r in rows if r["site"] == site), "median_ms": statistics.median(ms) if ms else None, "max_ms": max(ms) if ms else None,
                     "max_heartbeat_age_s": max([r.get("heartbeat_age_s") or 0 for r in rows if r["site"] == site] or [0])}
    by_time = {}
    for r in rows:
        by_time.setdefault(r["at"][:16], {})[r["site"]] = r.get("release")
    out["disagreements"] = [t for t, d in sorted(by_time.items()) if len({v for v in d.values() if v}) > 1][:10]
    out["first"], out["last"] = (rows[0]["at"], rows[-1]["at"]) if rows else (None, None)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--log")
    ap.add_argument("--summary")
    ap.add_argument("urls", nargs="*")
    a = ap.parse_args(argv)
    if a.summary:
        print(json.dumps(summarize(a.summary), indent=1, sort_keys=True))
        return 0
    if not a.log or not a.urls:
        ap.error("give --log FILE and at least one URL (or --summary FILE)")
    with open(a.log, "a", encoding="utf-8") as f:
        for u in a.urls:
            rec = probe(u)
            f.write(json.dumps(rec, sort_keys=True) + "\n")
            print(json.dumps(rec, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
