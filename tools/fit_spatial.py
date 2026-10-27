"""Fit the spatial engine's tunables (data/engine/spatial/PHYSICS.csv) to the play-level NFL rates by coordinate search.

Usage: fit_spatial.py [--n N] [--passes P] [--seed S] [--write] [--procs K] [--log FILE]
Each pass tries every tunable up and down by a step (a share of its range, shrinking each pass) and keeps what lowers the loss, the sum over
the rates in calibrate_spatial.py of the squared distance from the NFL value in half-band widths. The same seeds are used for every
evaluation, so the loss is a smooth function of the tunables. With --write the best values go back into PHYSICS.csv.
"""
import argparse
import io
import os
import re
import sys
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import calibrate_spatial as C  # noqa: E402

# name: (low, high, kinds affected)
RUN, PASS, BOTH = ("run",), ("pass",), ("run", "pass")
PARAMS = {
    "fill_depth": (1.2, 5.0, RUN), "eng_noise": (0.2, 1.4, BOTH), "fill_sd": (0.0, 3.0, RUN), "eng_arm_p": (0.0, 1.0, RUN), "gap_lb": (1.5, 5.0, RUN), "gap_w": (1.0, 4.0, RUN), "eng_win": (0.6, 1.6, BOTH), "eng_drive_run": (0.8, 3.0, RUN), "carry_horizon": (0.7, 1.8, RUN), "eng_bias_run": (0.1, 1.2, RUN), "eng_bias_perim": (0.0, 1.6, RUN), "run_read_extra": (-0.2, 0.5, RUN),
    "tackle_base": (0.72, 0.97, BOTH), "tackle_reach": (0.95, 1.7, BOTH), "tackle_behind": (0.5, 1.0, BOTH), "carry_f": (0.8, 0.99, BOTH),
    "pursuit_pred": (0.6, 1.0, BOTH),
    "catch_slow": (0.3, 0.95, PASS), "touch_lane": (0.08, 0.5, PASS), "int_lane": (0.08, 0.4, PASS), "int_contested": (0.15, 0.5, PASS),
    "open_margin": (0.5, 3.5, PASS), "depth_risk": (4.0, 40.0, PASS), "acc_err_dist": (0.02, 0.09, PASS), "adjust": (0.1, 0.7, PASS),
    "ball_react": (0.05, 0.4, PASS), "ball_lead": (0.2, 1.4, PASS), "eng_bias_pass": (-0.3, 0.7, PASS), "catch_free": (0.85, 0.99, PASS),
    "catch_contested": (0.4, 0.85, PASS), "contest_gain": (0.002, 0.012, PASS), "yac_block": (0.0, 1.0, PASS),
    "press_near": (4.0, 10.0, PASS), "press_go": (2.5, 8.0, PASS), "press_min": (0.4, 3.0, PASS), "press_bail": (0.0, 0.8, PASS), "perc_close": (0.1, 0.9, PASS), "check_pen": (0.0, 3.0, PASS), "scramble_base": (0.1, 0.7, PASS), "stick_pen": (0.0, 3.0, PASS),
}
WEIGHT = {"scramble": 0.5, "throwaway": 0.5, "fumble": 0.3, "secs": 0.5}


def loss_of(kind, m):
    tgt = C.RUN_T if kind == "run" else C.PASS_T
    tot = 0.0
    for k, (nfl, lo, hi) in tgt.items():
        half = (hi - lo) / 2.0
        tot += WEIGHT.get(k, 1.0) * ((m.get(k, 0.0) - nfl) / half) ** 2
    return tot


def evaluate(kind, n, seed, ov, procs):
    res = C.run(kind, n, seed, ov, procs)
    buf = io.StringIO()
    with redirect_stdout(buf):
        C.report(kind, res)
    return C.report.last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1500)
    ap.add_argument("--passes", type=int, default=3)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--procs", type=int, default=2)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--only", nargs="*", default=None)
    a = ap.parse_args()
    from dfl.engine.spatial.core import S
    cur = {k: float(S[k]) for k in PARAMS if a.only is None or k in a.only}
    cache = {}

    def lk(kind, ov):
        key = (kind, tuple(sorted((k, round(v, 6)) for k, v in ov.items() if kind in PARAMS[k][2])))
        if key not in cache:
            cache[key] = loss_of(kind, evaluate(kind, a.n, a.seed, {k: v for k, v in ov.items() if kind in PARAMS[k][2]}, a.procs))
        return cache[key]

    best = lk("run", cur) + lk("pass", cur)
    print(f"start loss {best:.2f}  (run {lk('run', cur):.2f}, pass {lk('pass', cur):.2f})", flush=True)
    step = 0.2
    for ps in range(a.passes):
        for name in list(cur):
            lo, hi, kinds = PARAMS[name]
            for sgn in (1, -1):
                trial = dict(cur)
                trial[name] = min(hi, max(lo, cur[name] + sgn * step * (hi - lo)))
                if trial[name] == cur[name]:
                    continue
                l = sum(lk(k, trial) for k in BOTH)
                if l < best - 1e-9:
                    best, cur = l, trial
                    print(f"  pass {ps + 1}: {name} -> {cur[name]:.4f}  loss {best:.2f}", flush=True)
                    break
        step *= 0.55
        print(f"after pass {ps + 1}: loss {best:.2f} (run {lk('run', cur):.2f}, pass {lk('pass', cur):.2f})", flush=True)
    print("best:", {k: round(v, 4) for k, v in cur.items()}, flush=True)
    if a.write:
        path = os.path.join(ROOT, "data", "engine", "spatial", "PHYSICS.csv")
        t = open(path).read()
        for k, v in cur.items():
            t, n = re.subn(rf"^{k},[^,]*,", f"{k},{round(v, 4)},", t, flags=re.M)
            assert n == 1, k
        open(path, "w").write(t)
        print("written", flush=True)


if __name__ == "__main__":
    main()
