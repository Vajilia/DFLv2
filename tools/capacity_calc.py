#!/usr/bin/env python3
"""Disk capacity for a recurring broadcast: what the volume must hold after N seasons, and what public ceiling that leaves (docs/INTEGRATION_CANDIDATE_2026-11-07.md, section 13).

  capacity_calc.py [--disk-gb D] [--other-used-gb O] [--seasons N] [--set JSON] [--json]

Pure arithmetic: it reads nothing from the host, writes nothing, deletes nothing, and it does NOT choose the ceiling. The defaults are the figures measured on 2026-11-06 on a scratch host
(docs/STORAGE_RETENTION.md and docs/PUBLIC_BACKUP.md: one real season, 400 games, 47 events); every one of them is to be replaced by a measurement on the target host before the ceiling is
chosen (section 13 lists what to measure and how). Gigabytes are decimal (10^9), as everywhere else in the tools.

The model, for N seasons on air (the volume must satisfy the last line):

  permanent recordings    public   = public_base + N x public_per_season          (nothing in it is ever removed: rule R1)
                          private  = private_fixed + N x private_per_season       (league file, archive, replay export, feed releases, and the live recordings until a retention
                                                                                   decision removes them: R2)
  backup workspace        sets kept on this volume x (N x set_per_season)         (a recovery set = league + store + public; ~ the sum of the two above)
                          + one restore/verify destination (a second copy) if the restore test runs on this volume
                          + temporary index/scratch of a running backup or verify (per file)
  retained evidence       monitor trip folders, interrupted `sNNN.partial` set-asides, quarantined backup `.partial` folders: an allowance, measured per item and counted
  free-disk reserve       max(the monitor's floor, the supervisor's rule to start a new season: floor + step margin + season allowance)
  everything else         OTHER (system, code, caches, operator rollback copies and tarballs: measured on the host, never assumed)

  need(N) = permanent + backup + evidence + reserve + OTHER                 must be <= the disk.

The CEILING is the public folder's share of that budget: ceiling(N) = public(N). `max_seasons` is the largest N whose need(N) fits; the ceiling for a horizon of N seasons is public(N)
plus a margin the owner chooses. Choosing N is the owner's decision (it depends on the retention decision, M8), so nothing here picks one.
"""
import argparse
import json
import sys

GB = 1e9

DEFAULTS = {
    "disk_gb": None,                      # the volume's size: measure on the host (df)
    "other_used_gb": None,                # everything on the volume that is not the broadcast: measure on the host (du), never assume
    "public_base_gb": 0.25,               # the site's own files, indexes, the live season's growth before it completes (the cap's base: tools/publiccap.py)
    "public_per_season_gb": 0.955,        # du, 2026-11-06: playback/games 251 + broadcast/replays 251 + broadcast/live 403 + releases 49 + honors 1 (MB); 0.78 GB by bytes
    "private_fixed_gb": 0.011,            # league.db 9 to 11 MB (rolling snapshots: does not grow)
    "private_per_season_gb": 1.073,       # archive 145 + feed releases 50 + playback export 244 + store/games 633 + honors/plans/builds 1 (MB). Removing store/games (633) is R2, undecided
    "backup_sets_kept": 1,                # recovery sets kept ON THIS VOLUME (a set kept off the host costs nothing here)
    "restore_test_on_volume": True,       # the restore test needs a destination as big as the set
    "set_per_season_gb": None,            # None: public_per_season + private_per_season (a measured set was 1.95 GB per season on disk; the sum is the safe side)
    "files_per_season": 118_000,          # public ~59,300 + store ~59,300 files a season
    "backup_scratch_kb_per_file": 0.27,   # the working index of create (0.24 for verify and restore), docs/PUBLIC_BACKUP.md section 6a
    "evidence_allowance_gb": 0.1,         # an ALLOWANCE for trip folders and set-asides. Measured: one monitor trip leaves 277 bytes of evidence (tools/oom_check.py); a set-aside `sNNN.partial` holds a plan and builds file (34 KB + 196 KB for a full season 1; less while partial). 0.1 GB is far above those
    "quarantined_partials": 1,            # backup `.partial` folders quarantined and not yet looked at (each up to one set in size): counted at one set each
    "monitor_floor_gb": 6.0,              # the monitor trips the broadcast below this much free disk (deploy/supervisor/broadcast.env.template FLOOR_BROADCAST_GB)
    "build_floor_gb": 8.0,                # the supervisor stops building below this (FLOOR_BUILD_GB). NOTE: the supervisor's three numbers are GiB (2^30), the monitor's floor is GB (10^9)
    "step_margin_gb": 0.5,                # the supervisor's margin for one event (its --step-margin-gb), GiB
    "season_gb": 2.0,                     # the supervisor's allowance to START a season (its --season-gb), GiB: it needs floor + margin + this free
}


GIB = 2 ** 30 / GB                      # the supervisor counts binary gigabytes (broadcast_supervisor.GB = 2**30); everything else here is decimal


def reserve_gb(i):
    """The free space that is never available to the broadcast's data (decimal GB): the larger of the monitor's floor and what the supervisor needs to start a season."""
    return max(i["monitor_floor_gb"], (i["build_floor_gb"] + i["step_margin_gb"] + i["season_gb"]) * GIB)


def need(i, n):
    """-> {component: GB} for n seasons on air (n >= 1)."""
    pub = i["public_base_gb"] + n * i["public_per_season_gb"]
    priv = i["private_fixed_gb"] + n * i["private_per_season_gb"]
    set_per = i["set_per_season_gb"] if i["set_per_season_gb"] is not None else i["public_per_season_gb"] + i["private_per_season_gb"]
    one_set = n * set_per
    scratch = i["files_per_season"] * n * i["backup_scratch_kb_per_file"] * 1e3 / GB
    backup = i["backup_sets_kept"] * one_set + (one_set if i["restore_test_on_volume"] else 0.0) + scratch
    evidence = i["evidence_allowance_gb"] + i["quarantined_partials"] * one_set
    other = i["other_used_gb"] if i["other_used_gb"] is not None else 0.0
    out = {"public_permanent": pub, "private_stores": priv, "backup_workspace": backup, "retained_evidence": evidence, "free_disk_reserve": reserve_gb(i), "other_on_volume": other}
    out["total"] = sum(out.values())
    return out


def max_seasons(i, limit=200):
    """The largest n whose need fits the disk (0 if even one season does not)."""
    if i["disk_gb"] is None:
        return None
    best = 0
    for n in range(1, limit + 1):
        if need(i, n)["total"] <= i["disk_gb"]:
            best = n
        else:
            break
    return best


def ceiling_gb(i, n, margin_gb=0.0):
    """The public folder's budget at a horizon of n seasons: what it will really hold then (plus the owner's margin). A starting point for the owner, not a decision."""
    return need(i, n)["public_permanent"] + margin_gb


def table(i, seasons):
    rows = []
    for n in seasons:
        d = need(i, n)
        d["seasons"] = n
        d["fits"] = None if i["disk_gb"] is None else d["total"] <= i["disk_gb"]
        d["headroom"] = None if i["disk_gb"] is None else i["disk_gb"] - d["total"]
        rows.append(d)
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--disk-gb", type=float)
    ap.add_argument("--other-used-gb", type=float)
    ap.add_argument("--seasons", type=int, default=12, help="print the table for 1..N seasons")
    ap.add_argument("--set", help="JSON object overriding any input (see DEFAULTS)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    i = dict(DEFAULTS)
    if a.set:
        extra = json.loads(a.set)
        unknown = sorted(set(extra) - set(i))
        if unknown:
            ap.error("unknown input(s): %s" % ", ".join(unknown))
        i.update(extra)
    if a.disk_gb is not None:
        i["disk_gb"] = a.disk_gb
    if a.other_used_gb is not None:
        i["other_used_gb"] = a.other_used_gb
    rows = table(i, range(1, a.seasons + 1))
    out = {"inputs": i, "reserve_gb": reserve_gb(i), "max_seasons": max_seasons(i), "rows": rows}
    if a.json:
        json.dump(out, sys.stdout, indent=1, sort_keys=True)
        print()
        return 0
    cols = ("public_permanent", "private_stores", "backup_workspace", "retained_evidence", "free_disk_reserve", "other_on_volume", "total")
    print("%7s" % "seasons" + "".join("%12s" % c[:11] for c in cols) + ("  fits" if i["disk_gb"] is not None else ""))
    for r in rows:
        print("%7d" % r["seasons"] + "".join("%12.2f" % r[c] for c in cols) + ("  %s" % ("yes" if r["fits"] else "NO") if i["disk_gb"] is not None else ""))
    print("free-disk reserve %.2f GB; " % out["reserve_gb"] + ("seasons that fit on %.1f GB: %s" % (i["disk_gb"], out["max_seasons"]) if i["disk_gb"] is not None else "no disk size given: nothing is said about fit"))
    if i["other_used_gb"] is None:
        print("other_used_gb is not set: 'other_on_volume' is 0, which understates every total. Measure it on the host.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
