#!/usr/bin/env python3
"""Restore soak: does a league that is killed over and over and restored reach exactly the same state as one that never stops?

  python3 -I tools/run_restore_soak.py --years 1 --seed 1 --kills 4 --play-level --report out/restore_soak.md

1. The reference: a plain `Universe` stepped event by event, no database, in this process.
2. The subject: a league file driven by separate processes (`tools/league_store.py`), each with different hash randomisation, each killed
   without clean-up at a seeded random event (alternately between events and in the middle of a commit), the file verified after every
   death, then resumed by a fresh process. One backup is taken part-way and restored at the end and run to the finish as well.
3. Compared: the journal digest after every event, the exhaustive digest (`persist.deep_digest`) of the final league, and the digest of
   the permanent archive (every game and trace as played), for the uninterrupted run, the killed-and-resumed run and the restored backup
   (league file and archive restored together).
Exit status 0 only if all three agree and every verification passed.
"""
import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
import time
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl import archive, persist  # noqa: E402
from dfl.year import Universe  # noqa: E402

TOOL = os.path.join(ROOT, "tools", "league_store.py")
EVENTS_PER_YEAR = 49


def tool(*args, hashseed):
    env = dict(os.environ, PYTHONHASHSEED=str(hashseed))
    r = subprocess.run([sys.executable, "-I", TOOL, *args], capture_output=True, text=True, cwd=ROOT, env=env)
    return r.returncode, (json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {}), r.stderr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=1)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--kills", type=int, default=4)
    ap.add_argument("--snapshot-every", type=int, default=4)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--play-level", action="store_true")
    g.add_argument("--placeholder", action="store_true")
    ap.add_argument("--report")
    a = ap.parse_args()
    total = a.years * EVENTS_PER_YEAR
    kw = {"engine": False} if a.placeholder else {"spatial": not a.play_level}
    mode = "placeholder" if a.placeholder else ("play-level" if a.play_level else "22-player")
    t0 = time.time()
    problems = []

    # 1. the reference
    u = Universe(os.path.join(ROOT, "data", "season1"), seed=a.seed, archive=True, **kw)
    d = tempfile.mkdtemp(prefix="dflrestore")
    ref_arch = archive.Archive.open_or_create(os.path.join(d, "reference.archive"), {"fingerprint": "reference", "how": "soak", "seq": 0})
    ref = []
    for n in range(total):
        u.step_event()
        ref.append(persist.quick_digest(u))
        recs = u.take_archive()
        if recs:
            ref_arch.put_batch(n + 1, recs)
    ref_deep = persist.deep_digest(u)
    ref_arch.close()
    ref_archive_digest = archive.digest(os.path.join(d, "reference.archive"))
    t_ref = time.time() - t0

    # 2. the subject, killed and resumed
    rng = random.Random(f"restore-soak|{a.seed}")
    points = sorted(rng.sample(range(2, total - 1), min(a.kills, total - 3)))
    path, bak, restored = (os.path.join(d, n) for n in ("league.db", "bak", "restored"))
    init = ["init", path, "--seed", str(a.seed), "--snapshot-every", str(a.snapshot_every)]
    init += ["--placeholder"] if a.placeholder else (["--play-level"] if a.play_level else [])
    rc, out, err = tool(*init, hashseed=1)
    if rc:
        problems.append(f"init failed: {out or err}")
    log, done, backed_up_at = [], 0, None
    for n, k in enumerate(points):
        style = ("--die-after", k - done) if n % 2 == 0 else ("--die-inside", k - done)
        rc, _, err = tool("run", path, "--events", str(total - done), *map(str, style), hashseed=100 + n)
        if rc != 9:
            problems.append(f"expected the process to die at event {k} ({style[0]}), exit status {rc}: {err[-300:]}")
        rc, st, _ = tool("status", path, hashseed=200 + n)
        done = st.get("events", done)
        rc, v, _ = tool("verify", path, "--deep", hashseed=300 + n)
        if rc or not v.get("ok"):
            problems.append(f"verify after death at event {k}: {v}")
        log.append({"died_at": k, "style": style[0], "events_in_file": done, "verified": bool(v.get("ok"))})
        if backed_up_at is None and n == len(points) // 2:
            rc, _, err = tool("backup-all", path, bak, hashseed=400)
            backed_up_at = done
            if rc:
                problems.append(f"backup failed: {err[-300:]}")
    rc, fin, err = tool("run", path, "--events", str(total - done), "--deep", hashseed=999)
    if rc:
        problems.append(f"final run failed: {fin or err[-300:]}")
    db = sqlite3.connect(path)
    journal = [x for (x,) in db.execute("SELECT digest FROM journal ORDER BY seq")]
    db.close()
    if journal != ref:
        bad = next((i + 1 for i, (x, y) in enumerate(zip(journal, ref)) if x != y), min(len(journal), len(ref)) + 1)
        problems.append(f"the journal differs from the uninterrupted run, first at event {bad}")
    if fin.get("deep") != ref_deep:
        problems.append("the final league differs from the uninterrupted run (exhaustive digest)")
    rc, ad, _ = tool("archive-digest", path + ".archive", hashseed=700)
    archive_same = ad.get("digest") == ref_archive_digest
    if not archive_same:
        problems.append("the archive of the killed-and-resumed run differs from the uninterrupted run's")
    rc, av, _ = tool("archive-verify", path + ".archive", hashseed=701)
    if rc or not av.get("ok"):
        problems.append(f"archive verify: {av}")

    # 3. the backup, restored and run to the end
    if backed_up_at is not None:
        rc, rr, err = tool("restore-all", bak, restored, hashseed=500)
        rpath = os.path.join(restored, "league.db")
        rc2, fin2, err2 = tool("run", rpath, "--events", str(total - backed_up_at), "--deep", hashseed=501)
        if rc or rc2 or fin2.get("deep") != ref_deep:
            problems.append(f"the restored backup did not reach the same league: {rr} {fin2 or err2[-300:]}")
        rc3, ad2, _ = tool("archive-digest", os.path.join(restored, "archive.db"), hashseed=502)
        restored_archive_same = ad2.get("digest") == ref_archive_digest
        if not restored_archive_same:
            problems.append("the restored backup's archive differs from the uninterrupted run's")
    rc, v, _ = tool("verify", path, "--deep", hashseed=600)
    if rc or not v.get("ok"):
        problems.append(f"final verify: {v}")

    ok = not problems
    lines = [f"# Restore soak: {a.years} league year(s), seed {a.seed}, engine {mode}", "",
             f"- result: {'PASS' if ok else 'FAIL'}",
             f"- events: {total}; kills: {len(points)} at events {points} (alternately between events and inside a commit)",
             f"- snapshot every {a.snapshot_every} events; backup taken after event {backed_up_at}, restored and run to the end",
             f"- journal digests identical for all {total} events: {journal == ref}",
             f"- exhaustive final digest identical (uninterrupted / resumed / restored backup): {ref_deep[:16]}",
             f"- archive digest identical (uninterrupted / resumed{' / restored backup' if backed_up_at is not None else ''}): {ref_archive_digest[:16]}"
             f" (resumed {archive_same})",
             f"- run time: {time.time() - t0:.0f}s (reference alone {t_ref:.0f}s)", ""]
    lines += [f"- deaths: {json.dumps(x)}" for x in log]
    lines += [f"- PROBLEM: {p}" for p in problems]
    text = "\n".join(lines) + "\n"
    print(text)
    if a.report:
        os.makedirs(os.path.dirname(a.report) or ".", exist_ok=True)
        with open(a.report, "w", encoding="utf-8") as f:
            f.write(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
