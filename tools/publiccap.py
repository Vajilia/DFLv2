"""The public folder's size limit for a broadcast that goes on season after season. Shared by the daemon (broadcast_run.py), the monitor (rehearsal_check.py) and the
supervisor's health report (broadcast_supervisor.py) so all three always mean the same number. Pure functions; nothing here writes or deletes anything.

WHY A FIXED NUMBER DOES NOT WORK. The rehearsal ran one season and stopped the broadcast above a fixed 3 GB (the daemon's `--max-public-gb 3`, the monitor's `--max-public-gb 3`).
The public folder never shrinks (every release, replay and recording stays: docs/STORAGE_RETENTION.md rule R1), and each season adds about 0.78 to 0.95 GB (measured). A fixed
cap therefore stops a correct broadcast at the end of season 3 or during season 4 with tens of GB of disk free. That is not a safeguard, it is a timer.

WHAT REPLACES IT, AND WHAT IT STILL CATCHES. The allowance is per season on air, not per run:

    cap = base + per_season x (seasons that have been on air, the current one included)        and never more than the ceiling, if one is given

"Seasons on air" is read from store/carry.json (the one atomic record the daemon writes at each hand-over: `seasons` lists the finished ones, `season` is the one on air now);
without a carry record it is 1. The cap therefore moves only at a hand-over, and a hand-over only happens for a season the builder finished. What the cap still catches:

  * a runaway season: a publisher bug, a duplicated tree or a leaking copy that grows a season far past its allowance is stopped in that season (the daemon exits 70, the
    monitor trips the guard), as before, at a size proportional to how many seasons have honestly been published
  * the whole-volume limit: the optional CEILING is an absolute number for what the owner has budgeted for the public folder, however many seasons there are (it bounds the slack
    below), and the free-disk floors of the monitor (`--min-free-gb`) and of the supervisor (`--floor-gb`) still stop everything long before the disk is full

WHAT IT DOES NOT CATCH, stated plainly: the cap is cumulative, so what a season leaves unused (allowance minus what it really published) carries forward and could later hide a
small overrun. With a per-season allowance 30% above the measured maximum the carried slack is at most 0.3 GB a season, so after ten seasons an overrun of about 3 GB is still
under the cap. The ceiling, the free-disk floors and the supervisor's `public_cap_near` warning (at 85% of the cap) are what bound it; a per-season measurement would need the
daemon to record the folder's size at each hand-over, which would add a second writer to the store and was not built.
"""
import json
import os

GB = 1e9                                    # the daemon and the monitor have always counted decimal gigabytes for this cap (docs/BROADCAST_REHEARSAL.md)
NEAR = 0.85                                 # the supervisor reports `public_cap_near` at this fraction of the cap


def tree_bytes(path):
    total, stack = 0, [path]
    while stack:
        try:
            it = os.scandir(stack.pop())
        except FileNotFoundError:
            continue
        with it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                    elif e.is_file(follow_symlinks=False):
                        total += e.stat(follow_symlinks=False).st_size
                except FileNotFoundError:
                    continue                # renamed into place or replaced while we looked (atomic publication)
    return total


def seasons_on_air(store):
    """How many seasons have been on air (the current one included): the carry record's finished seasons plus the one on air; 1 without a record."""
    try:
        with open(os.path.join(store, "carry.json"), "rb") as f:
            carry = json.load(f)
        return len(carry.get("seasons", [])) + 1
    except (OSError, ValueError):
        return 1


def cap_bytes(store, per_season_gb, base_gb=0.0, ceiling_gb=None):
    """The public folder's limit in bytes right now."""
    cap = (float(base_gb) + float(per_season_gb) * seasons_on_air(store)) * GB
    if ceiling_gb:
        cap = min(cap, float(ceiling_gb) * GB)
    return int(cap)


def describe(store, per_season_gb, base_gb=0.0, ceiling_gb=None):
    n = seasons_on_air(store)
    return {"seasons_on_air": n, "per_season_gb": per_season_gb, "base_gb": base_gb, "ceiling_gb": ceiling_gb, "cap_bytes": cap_bytes(store, per_season_gb, base_gb, ceiling_gb)}


def add_arguments(p):
    p.add_argument("--max-public-gb-per-season", type=float, default=0.0,
                   help="recurring operation: public folder limit = base + this x seasons on air (store/carry.json); replaces the fixed --max-public-gb, which stops a correct broadcast in season 3 or 4")
    p.add_argument("--public-base-gb", type=float, default=0.25, help="fixed part of the per-season limit (the site's own files, indexes, a season's worth of slack for the live season)")
    p.add_argument("--max-public-ceiling-gb", type=float, default=0.0, help="absolute upper bound on the per-season limit: what the owner has budgeted for the public folder (0 = none)")


def check_arguments(a, parser=None):
    """A fixed cap and a per-season cap together are ambiguous (the fixed one would silently win in season 4): refuse the combination."""
    fixed = getattr(a, "max_public_gb", 0.0)
    per = getattr(a, "max_public_gb_per_season", 0.0)
    if fixed and per:
        msg = "--max-public-gb (fixed) and --max-public-gb-per-season cannot be combined: choose the rehearsal cap or the recurring one"
        if parser is not None:
            parser.error(msg)
        raise SystemExit(msg)
    if getattr(a, "max_public_ceiling_gb", 0.0) and not per:
        msg = "--max-public-ceiling-gb only bounds --max-public-gb-per-season; for a fixed cap use --max-public-gb"
        if parser is not None:
            parser.error(msg)
        raise SystemExit(msg)
