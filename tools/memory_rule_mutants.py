#!/usr/bin/env python3
"""Mutation check for the memory rule's tests (tests/test_memory_rule.py): break the rule in each way that matters and require the tests to notice.

  memory_rule_mutants.py [--out FILE]

A test that still passes against a broken rule proves nothing. Each mutant below is applied in memory (nothing on disk is changed) and the test module is run; the mutant is KILLED if at
least one test fails. Exit status 1 if any mutant survives.
"""
import argparse
import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
for p in (os.path.join(ROOT, "src"), HERE, os.path.join(ROOT, "tests")):
    sys.path.insert(0, p)
import rehearsal_check as RC       # noqa: E402

REAL_EST = RC.memory_estimates
REAL_INIT = RC.MemoryJudge.__init__


def est_old_rule(s):                                                  # memory.current itself: the earlier rule
    return {"reclaimable": 0, "pinned": int(s["current"])}


def est_all_file_cache(s):                                            # "subtract all file cache": dirty, writeback, mapped and shmem discounted too
    rec = min(int(s["current"]), s["file"] + s["slab_reclaimable"])
    return {"reclaimable": rec, "pinned": int(s["current"]) - rec}


def est_all_slab(s):                                                  # all kernel memory treated as reclaimable
    clean = max(0, s["file"] - s["file_dirty"] - s["file_writeback"] - s["file_mapped"] - s["shmem"])
    rec = min(int(s["current"]), clean + s["kernel"])
    return {"reclaimable": rec, "pinned": int(s["current"]) - rec}


def init_no_sustain(self, frac=RC.MEM_PINNED_FRAC, sustain_s=RC.MEM_SUSTAIN_S, min_samples=RC.MEM_MIN_SAMPLES):
    REAL_INIT(self, frac, 0.0, 1)                                     # one high sample trips


def init_no_min_samples(self, frac=RC.MEM_PINNED_FRAC, sustain_s=RC.MEM_SUSTAIN_S, min_samples=RC.MEM_MIN_SAMPLES):
    REAL_INIT(self, frac, sustain_s, 1)                               # time alone, however few samples


def init_higher_line(self, frac=RC.MEM_PINNED_FRAC, sustain_s=RC.MEM_SUSTAIN_S, min_samples=RC.MEM_MIN_SAMPLES):
    REAL_INIT(self, 1.01, sustain_s, min_samples)                     # a line above the limit: the rule can never fire (what "just raise the limit" amounts to)


def observe_ignores_events(self, s, now):
    return REAL_OBSERVE(self, dict(s, events=dict.fromkeys(("low", "high", "max", "oom", "oom_kill"), 0)), now)


def observe_trips_on_max_events(self, s, now):
    v = REAL_OBSERVE(self, s, now)
    if v is None and (s.get("events") or {}).get("max", 0) > 0:
        return {"reason": "max_event", "detail": "max events", "evidence": {}}
    return v


REAL_OBSERVE = RC.MemoryJudge.observe

MUTANTS = [
    ("earlier rule: footprint = memory.current (no discount)", [(RC, "memory_estimates", est_old_rule)]),
    ("subtract ALL file cache (dirty, writeback, mapped, shmem too)", [(RC, "memory_estimates", est_all_file_cache)]),
    ("treat all kernel memory as reclaimable", [(RC, "memory_estimates", est_all_slab)]),
    ("no sustain window: one high sample trips", [(RC.MemoryJudge, "__init__", init_no_sustain)]),
    ("window by time only (no minimum number of samples)", [(RC.MemoryJudge, "__init__", init_no_min_samples)]),
    ("raise the line above the limit (the rule can never fire)", [(RC.MemoryJudge, "__init__", init_higher_line)]),
    ("ignore the kernel's OOM events", [(RC.MemoryJudge, "observe", observe_ignores_events)]),
    ("trip on `max` events (the kernel reclaiming)", [(RC.MemoryJudge, "observe", observe_trips_on_max_events)]),
]


def run_tests():
    import test_memory_rule as T
    suite = unittest.defaultTestLoader.loadTestsFromModule(T)
    res = unittest.TextTestRunner(stream=open(os.devnull, "w"), verbosity=0).run(suite)
    return res.testsRun, sorted({str(t[0]).split(" ")[0] for t in res.failures + res.errors})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    n, failed = run_tests()
    out = {"baseline": {"tests": n, "failed": failed}, "mutants": []}
    print("baseline: %d tests, %d failing" % (n, len(failed)))
    ok = not failed
    for name, patches in MUTANTS:
        ctxs = [mock.patch.object(t, attr, new) for t, attr, new in patches]
        for c in ctxs:
            c.start()
        try:
            n, failed = run_tests()
        finally:
            for c in ctxs:
                c.stop()
        killed = bool(failed)
        ok = ok and killed
        out["mutants"].append({"mutant": name, "killed": killed, "failing_tests": failed})
        print("%-70s %s (%d tests fail)" % (name, "KILLED" if killed else "SURVIVED", len(failed)))
    out["ok"] = ok
    if a.out:
        with open(a.out, "w") as f:
            json.dump(out, f, indent=1)
    print("RESULT: %s" % ("every mutant was killed by the tests" if ok else "A MUTANT SURVIVED OR THE BASELINE FAILS"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
