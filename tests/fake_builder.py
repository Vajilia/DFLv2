#!/usr/bin/env python3
"""A synthetic stand-in for the engine part of tools/broadcast_build.py, for the supervisor tests (tests/test_broadcast_supervisor.py). Nothing here is used by production.

It is the REAL builder command line (the same lock, the same argument handling, the same `step` loop, `next-season` and `status`, the same season selection and the same
completeness refusals) with exactly three things replaced: what one event costs (`step_one` writes the synthetic four-event "year" of tests/test_multiseason.py instead of
firing the engine), the plan a new season gets (`broadcast_plan.build_plan`), and the league's event count (taken from what is built). It is steered by files in --ctl:

  delay            seconds each event takes
  fail_at          an event number (within the season) that fails, every time, while the file exists
  fail_next_season `next-season` fails while the file exists
  enospc           every event fails with "No space left on device" while the file exists
  crash_next_season  the process dies (os._exit) between the two writes `next-season` makes, once (the file is removed first)
  calls.log        one line "<verb> <pid> <season> <seq> <time>" at the start and end of every event (the tests read overlaps and counts from it)
"""
import errno
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)
import broadcast_build as BB  # noqa: E402
import broadcast_plan as BP  # noqa: E402
import test_multiseason as TM  # noqa: E402
from dflsite import broadcast as B  # noqa: E402
from dflsite import season as S  # noqa: E402

B.FSYNC = False
CTL = None


def ctl(name):
    p = os.path.join(CTL, name)
    if not os.path.exists(p):
        return None
    return open(p).read().strip() or "1"


def log(verb, season, seq):
    with open(os.path.join(CTL, "calls.log"), "a") as f:
        f.write("%s %d %d %d %.3f\n" % (verb, os.getpid(), season, seq, time.time()))


_real_fixture_game = TM.fixture_game
TM.fixture_game = lambda season, rid, bump: _real_fixture_game(season, rid, 1 + season % 5)      # the synthetic seasons are 1, 2, 3 ... not 901, 902


def make_year(store, season, base):
    return TM.Year(store, season, base, rooted=(season == 1))


def synthetic_plan(params=None, season=1, event_base=0):
    if ctl("fail_next_season"):
        raise RuntimeError("synthetic next-season failure")
    with tempfile.TemporaryDirectory() as tmp:
        return TM.Year(os.path.join(tmp, "store"), season, event_base, rooted=True).plan


def done_of(P):
    b = BB.load_builds(P)
    plan = B.get(os.path.join(P["bdir"], S.PLAN))
    return max([int(k) for k in b["events"]] or [int(plan.get("event_base", 0))])


def step_one(P, plan, slot_s):
    season, base = int(plan["season"]), int(plan.get("event_base", 0))
    seq = done_of(P) + 1
    n = seq - base
    log("start", season, seq)
    try:
        d = ctl("delay")
        if d:
            time.sleep(float(d))
        if ctl("enospc"):
            raise OSError(errno.ENOSPC, "No space left on device")
        if ctl("fail_at") and int(ctl("fail_at")) == n:
            raise SystemExit("synthetic failure at event %d of season %d" % (n, season))
        y = make_year(P["store"], season, base)
        y.build(n)
    finally:
        log("end", season, seq)
    return seq


def crashing_put(real_put):
    state = {"n": 0}

    def put(path, obj):
        real_put(path, obj)
        if os.sep + "seasons" + os.sep in path and os.path.basename(path) == S.PLAN and ctl("crash_next_season"):
            os.unlink(os.path.join(CTL, "crash_next_season"))
            os._exit(9)
    return put


def main():
    global CTL
    argv = sys.argv[1:]
    if not argv or argv[0] != "--ctl":
        raise SystemExit("usage: fake_builder.py --ctl DIR <broadcast_build.py arguments>")
    CTL = argv[1]
    os.makedirs(CTL, exist_ok=True)
    BB.step_one = step_one
    BB.BP = BP
    BP.build_plan = synthetic_plan
    BB.league_events = lambda P: done_of(P)
    B.put = crashing_put(B.put)
    return BB.main(argv[2:])


if __name__ == "__main__":
    sys.exit(main())
