#!/usr/bin/env python3
"""Controlled restart of the league service at the cap, with its memory sampled (run as root on the server).

  python3 -I tools/measure_restart.py --league /srv/dflv2/league/league.db --ctl /srv/dflv2/league --expect-events 141

Refuses to restart unless ALL of these hold, so that a restart cannot advance the league: the journal's head is exactly --expect-events, the
settings file /etc/dflv2/league.env sets DFL_UNTIL to that same number, the runner's last status says it stopped at the limit, and the PAUSED marker
is present (a second guard; the limit is checked first and holds on its own). Then it restarts `dflv2-league.service`, samples the new process's
VmRSS and VmHWM every 50 ms until it exits, and prints: the largest resident size seen, the process's own high-water mark, how long it lived, its
exit status, the runner status it left and the journal head afterwards. Exit status 1 if the head moved, the service did not exit 0, or any
guard failed. It writes nothing but what the runner itself writes (its status file)."""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time

UNIT = "dflv2-league.service"


def head(league):
    db = sqlite3.connect(f"file:{os.path.abspath(league)}?mode=ro", uri=True)
    try:
        return db.execute("SELECT seq, key, digest, chain FROM journal ORDER BY seq DESC LIMIT 1").fetchone()
    finally:
        db.close()


def show(*props):
    out = subprocess.run(["systemctl", "show", UNIT, "-p", ",".join(props)], capture_output=True, text=True).stdout
    return dict(l.split("=", 1) for l in out.splitlines() if "=" in l)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", required=True)
    ap.add_argument("--ctl", required=True)
    ap.add_argument("--expect-events", type=int, required=True)
    ap.add_argument("--env", default="/etc/dflv2/league.env")
    ap.add_argument("--dry-run", action="store_true", help="check the guards and stop")
    a = ap.parse_args(argv)
    h0 = head(a.league)
    guards = []
    if h0[0] != a.expect_events:
        guards.append(f"journal head is {h0[0]}, expected exactly {a.expect_events}")
    env = open(a.env).read() if os.path.exists(a.env) else ""
    m = re.search(r"^\s*DFL_UNTIL=\"?(\w+)\"?", env, re.M)
    if not m or m.group(1) != str(a.expect_events):
        guards.append(f"{a.env} does not set DFL_UNTIL={a.expect_events} (found {m.group(1) if m else None})")
    try:
        st = json.load(open(os.path.join(a.ctl, "runner_status.json")))
    except (OSError, ValueError):
        st = {}
    if not (st.get("state") == "stopped" and st.get("limit_reached") and st.get("seq") == a.expect_events):
        guards.append(f"runner status is not 'stopped at the limit at event {a.expect_events}': {({k: st.get(k) for k in ('state', 'limit_reached', 'seq')})}")
    if not os.path.exists(os.path.join(a.ctl, "PAUSED")):
        guards.append("the PAUSED marker is not present")
    if guards:
        print("REFUSED:\n  " + "\n  ".join(guards))
        return 1
    print(f"guards ok: head {h0[0]} ({h0[1]}), limit {a.expect_events}, status stopped/limit_reached, PAUSED present")
    if a.dry_run:
        return 0
    samples, t0 = [], time.time()
    subprocess.run(["systemctl", "restart", UNIT], check=True)
    pid = None
    deadline = time.time() + 120
    while time.time() < deadline:
        s = show("MainPID", "ActiveState", "SubState")
        pid = int(s.get("MainPID", 0)) or pid
        if pid and os.path.exists(f"/proc/{pid}/status"):
            try:
                with open(f"/proc/{pid}/status") as f:
                    v = {l.split(":")[0]: int(l.split()[1]) for l in f if l.startswith(("VmRSS", "VmHWM"))}
                samples.append((time.time() - t0, v.get("VmRSS", 0) / 1024, v.get("VmHWM", 0) / 1024))
            except OSError:
                pass
        elif pid and s.get("ActiveState") in ("inactive", "failed"):
            break
        elif samples:
            break
        time.sleep(0.05)
    time.sleep(1)
    fin = show("ActiveState", "SubState", "Result", "ExecMainStatus", "NRestarts", "ExecMainPID")
    h1 = head(a.league)
    try:
        st1 = json.load(open(os.path.join(a.ctl, "runner_status.json")))
    except (OSError, ValueError):
        st1 = {}
    res = {"pid": pid, "samples": len(samples), "max_resident_mb": round(max((s[1] for s in samples), default=0), 1),
           "process_peak_mb": round(max((s[2] for s in samples), default=0), 1), "lived_seconds": round(samples[-1][0] - samples[0][0], 2) if samples else None,
           "service": fin, "runner_status_after": {k: st1.get(k) for k in ("state", "seq", "limit_reached", "until_events", "error")},
           "head_before": list(h0), "head_after": list(h1), "head_unchanged": tuple(h0) == tuple(h1), "paused_marker_still_present": os.path.exists(os.path.join(a.ctl, "PAUSED"))}
    print(json.dumps(res, indent=1))
    ok = res["head_unchanged"] and fin.get("ExecMainStatus") == "0" and fin.get("ActiveState") in ("inactive",) and st1.get("limit_reached") is True
    print("RESTART OK: stopped again at the limit, nothing advanced" if ok else "RESTART CHECK FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
