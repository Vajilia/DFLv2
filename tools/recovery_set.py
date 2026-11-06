#!/usr/bin/env python3
"""The broadcast RECOVERY SET: one league backup + one private-store backup + one public-folder backup, taken and checked as a unit (docs/PUBLIC_BACKUP.md section 5).

  recovery_set.py create  --root R --public P --out SET [--note TEXT]
  recovery_set.py verify  SET [--deep]
  recovery_set.py restore SET DEST                 -> DEST/league/{league.db, league.db.archive, ctl/}, DEST/store, DEST/public (a NEW directory)
  recovery_set.py quarantine SET.partial [--to DIR]  move an unfinished set aside, whole (a rename; nothing is deleted), then retry

R is a builder root (R/league/league.db, R/league/ctl, R/store); P is the public folder the daemon writes. Read-only on both except the lock file ROOT/.builder.lock; neither the daemon nor the builder is signalled.
The BUILDER IS EXCLUDED while a set is taken: `create` holds the root's exclusive lock (tools/rootlock.py, ROOT/.builder.lock) that every changing `broadcast_build.py`
command also takes, and refuses to start if the builder holds it (or a league runner holds league/ctl/runner.lock); a builder started during the set is refused (exit 75). The
DAEMON may keep publishing. Order: public folder, then the private store, then the league. The daemon writes both the public folder and, in the store, the viewers' clock and
the hand-over record, so a store captured after the public folder is never behind it (a restored daemon re-derives what is missing); the reverse order can leave the public
folder ahead of the stored clock and is refused by rule S3. The league and the store do not move once the builder is excluded.

Rules a set must satisfy (recomputed from the component manifests by `verify`, not trusted from SET/RECOVERY_SET.json):
  S1 the league's events equal the builds' last event, or exceed it by one (an event fired but not yet built)
  S2 no event is public that is not built
  S3 no public playback, replay-library or Honors entry belongs to a later season than the season on air in the store (a public folder newer than its store cannot be continued)
  S4 the store backup and the public backup are each consistent (reference closure, tools/public_backup.py) and the store backup was quiet
The set is committed like a public backup: written under SET.partial, RECOVERY_SET.json last, renamed.
"""
import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import public_backup as PBK  # noqa: E402
import rootlock as RL  # noqa: E402

FORMAT = "dfl-recovery-set"
DESCRIPTOR = "RECOVERY_SET.json"
CTL_FILES = ("events.jsonl", "runner_status.json")


class Failed(Exception):
    pass



def _slurp(path):
    with open(path, "rb") as f:
        return f.read()


def _jload(path):
    return json.loads(_slurp(path))

def py(args, check=True):
    p = subprocess.run([sys.executable, "-I"] + args, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise Failed("%s failed: %s" % (os.path.basename(args[0]), (p.stderr or p.stdout)[-500:]))
    return p


def sha(path):
    return PBK.sha_file(path)


def built_events(store):
    """-> (highest event built, season on air) read from the private store."""
    last, season = 0, 1
    files = [os.path.join(store, "builds.json")]
    sd = os.path.join(store, "seasons")
    if os.path.isdir(sd):
        files += [os.path.join(sd, n, "builds.json") for n in sorted(os.listdir(sd))]
    for bf in files:
        if os.path.exists(bf):
            b = _jload(bf)
            last = max([last] + [int(k) for k in b.get("events", {})])
    carry = os.path.join(store, "carry.json")
    if os.path.exists(carry):
        season = int(_jload(carry)["season"])
    else:
        plan = os.path.join(store, "plan.json")
        if os.path.exists(plan):
            season = int(_jload(plan).get("season", 1))
    return last, season


def league_events(league):
    return json.loads(py([os.path.join(ROOT, "tools", "league_store.py"), "status", league]).stdout)["events"]


def public_state(pub_files):
    """-> (events public, highest season in any public cumulative index) from a public folder or the `files` folder of its backup."""
    ev, seasons = 0, [0]
    p = os.path.join(pub_files, "latest.json")
    if os.path.exists(p):
        ev = int(_jload(p)["release"][1:6])
    for rel, key in (("playback/index.json", "games"), ("broadcast/replays/index.json", "games"), ("broadcast/honors/index.json", "parts")):
        q = os.path.join(pub_files, rel)
        if os.path.exists(q):
            seasons += [int(e["season"]) for e in _jload(q).get(key, []) if e.get("season") is not None]
    return ev, max(seasons)


def rules(league_ev, built, season_on_air, pub_ev, pub_season, store_quiet, pub_ok, store_ok):
    out = []
    if league_ev not in (built, built + 1):
        out.append("S1: the league has %d events but %d are built (the builder must be idle and the league must not be ahead by more than one)" % (league_ev, built))
    if pub_ev > built:
        out.append("S2: %d events are public but only %d are built" % (pub_ev, built))
    if pub_season > season_on_air:
        out.append("S3: the public folder shows season %d but the store's season on air is %d (the public copy is newer than the store)" % (pub_season, season_on_air))
    if not store_quiet:
        out.append("S4: the store was still changing while it was copied (is the builder running?)")
    if not (pub_ok and store_ok):
        out.append("S4: a component does not satisfy its reference closure")
    return out


def create(root, public, out, note=None, fsync=True, hook=None, pub_hook=None, max_passes=8):
    """Take a recovery set. The builder is EXCLUDED, not merely checked: the root's exclusive lock (tools/rootlock.py, the one every changing `broadcast_build.py` command
    takes) is held from before the first event count is read until the set is committed or has failed, and a league runner that is running (`league/ctl/runner.lock`) is
    refused too. A builder that is running refuses the set at once and nothing is copied; a builder started during the set is refused (exit 75) and changes nothing.
    `pub_hook` is passed to the public backup (tests only)."""
    root, public, out = os.path.abspath(root), os.path.abspath(public), os.path.abspath(out)
    with PBK.name_lock(out, "recovery set create"):             # one operation per set name: another create, quarantine or restore of it is refused ("in progress") and touches nothing
        return _locked_create(root, public, out, note, fsync, hook, pub_hook, max_passes)


def _locked_create(root, public, out, note, fsync, hook, pub_hook, max_passes):
    try:
        lock = RL.hold(root, "recovery set (tools/recovery_set.py create)")
        lock.__enter__()
    except RL.Busy as e:
        raise Failed("the builder is not idle, so no recovery set was started: %s" % e)
    runner = None
    try:
        rl = os.path.join(root, "league", "ctl", "runner.lock")
        if os.path.isdir(os.path.dirname(rl)):
            runner = os.open(rl, os.O_RDWR | os.O_CREAT, 0o644)
            try:
                fcntl.flock(runner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise Failed("a league runner is running (%s is held), so no recovery set was started" % rl)
        return _create(root, public, out, note, fsync, hook, pub_hook, max_passes)
    finally:
        if runner is not None:
            os.close(runner)
        lock.__exit__(None, None, None)


def _component(out, name, make):
    """Run one component backup inside the set. A full disk is re-reported for the SET: the component's own message names its inner folder, but the folder to
    quarantine is the outer `SET.partial` (quarantining the inner one would leave the outer one blocking the name; runbook rehearsal, finding 1)."""
    try:
        return make()
    except PBK.Failed as e:
        if "disk is full" not in str(e):
            raise
        raise Failed("the disk is full while copying the %s part of the recovery set: nothing was committed. %s.partial is kept as evidence (what was written so far, the working index of the "
                     "part being copied included), already valid sets were not touched, and nothing was deleted. Free space or choose another destination, move the unfinished set aside "
                     "with `recovery_set.py quarantine %s.partial`, and retry." % (name, out, out))


def _create(root, public, out, note, fsync, hook, pub_hook, max_passes):
    hook = hook or (lambda *a, **k: None)
    if os.path.exists(out) or os.path.exists(out + ".partial"):
        raise Failed("%s (or its .partial) exists: a set is never overwritten. An unfinished %s.partial is kept as evidence with `recovery_set.py quarantine %s.partial`, then retry" % (out, out, out))
    league = os.path.join(root, "league", "league.db")
    store = os.path.join(root, "store")
    ctl = os.path.join(root, "league", "ctl")
    part = out + ".partial"
    os.makedirs(part)
    t0 = time.time()
    ev0, built0 = league_events(league), built_events(store)
    hook("start")
    pub = _component(out, "public", lambda: PBK.create(public, os.path.join(part, "public"), "public", fsync=fsync, hook=pub_hook, max_passes=max_passes, lock=False))
    hook("after_public")
    sto = _component(out, "store", lambda: PBK.create(store, os.path.join(part, "store"), "store", fsync=fsync, lock=False))
    hook("after_store")
    includes = []
    for n in CTL_FILES:
        if os.path.exists(os.path.join(ctl, n)):
            includes += ["--include", os.path.join(ctl, n)]
    lb = py([os.path.join(ROOT, "tools", "league_backup.py"), "create", league, os.path.join(part, "league")] + includes + ["--note", note or "recovery set"])
    hook("after_league")
    ev1, built1 = league_events(league), built_events(store)
    if (ev0, built0[0]) != (ev1, built1[0]):                       # (the season on air is the DAEMON's: it changes at a hand-over, which is allowed)
        raise Failed("the league or the builds moved while the set was being made (league %d to %d events, built %d to %d): something changed the builder's root without the "
                     "lock; nothing was recorded" % (ev0, ev1, built0[0], built1[0]))
    mf = _jload(os.path.join(part, "league", "BACKUP_MANIFEST.json"))
    league_ev = mf["journal_head"]["seq"]
    built, season = built_events(os.path.join(part, "store", PBK.FILES))
    pev, pseason = public_state(os.path.join(part, "public", PBK.FILES))
    problems = rules(league_ev, built, season, pev, pseason, sto["quiescent"], True, True)
    if problems:
        raise Failed("; ".join(problems))
    desc = {"format": FORMAT, "format_version": 1, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "note": note, "order": ["public", "store", "league"],
            "components": {
                "league": {"dir": "league", "manifest_sha256": sha(os.path.join(part, "league", "BACKUP_MANIFEST.json")), "events": league_ev, "journal_head": mf["journal_head"]},
                "store": {"dir": "store", "manifest_digest": sto["manifest_digest"], "built_events": built, "season_on_air": season, "quiescent": sto["quiescent"]},
                "public": {"dir": "public", "manifest_digest": pub["manifest_digest"], "public_events": pev, "highest_public_season": pseason, "quiescent": pub["quiescent"]}},
            "rules_checked": ["S1", "S2", "S3", "S4"], "seconds": round(time.time() - t0, 1)}
    hook("before_commit")
    p = os.path.join(part, DESCRIPTOR)
    with open(p + ".tmp", "w") as f:
        json.dump(desc, f, indent=1, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(p + ".tmp", p)
    os.replace(part, out)
    PBK.fsync_dir(os.path.dirname(out))
    return {"ok": True, "set": out, **{k: desc["components"][k] for k in desc["components"]}, "seconds": desc["seconds"]}


def quarantine(path, to=None):
    """Move an unfinished SET.partial aside, whole (tools/public_backup.py quarantine_partial): a rename, nothing is deleted."""
    return PBK.quarantine_partial(path, to)


def verify(setdir, deep=False):
    setdir = os.path.abspath(setdir)
    problems = []
    if setdir.endswith(".partial") or (not os.path.exists(setdir) and os.path.isdir(setdir + ".partial")):
        return {"ok": False, "problems": ["%s is an unfinished recovery set: incomplete, not valid" % setdir]}
    dp = os.path.join(setdir, DESCRIPTOR)
    if not os.path.isfile(dp):
        return {"ok": False, "problems": ["%s has no %s: incomplete, not valid" % (setdir, DESCRIPTOR)]}
    try:
        desc = _jload(dp)
    except ValueError:
        return {"ok": False, "problems": ["%s is not readable JSON" % DESCRIPTOR]}
    c = desc.get("components", {})
    pub = PBK.verify(os.path.join(setdir, "public"))
    sto = PBK.verify(os.path.join(setdir, "store"))
    problems += ["public: " + x for x in pub["problems"]] + ["store: " + x for x in sto["problems"]]
    lg_manifest = os.path.join(setdir, "league", "BACKUP_MANIFEST.json")
    if not os.path.isfile(lg_manifest):
        problems.append("league: BACKUP_MANIFEST.json is missing")
    else:
        if sha(lg_manifest) != c.get("league", {}).get("manifest_sha256"):
            problems.append("league: the league manifest is not the one this set recorded")
        args = [os.path.join(ROOT, "tools", "league_backup.py"), "verify", os.path.join(setdir, "league")] + (["--deep"] if deep else [])
        p = py(args, check=False)
        if p.returncode != 0:
            problems.append("league: verify failed: %s" % (p.stdout or p.stderr)[-300:])
    if pub["ok"] and pub["manifest_digest"] != c.get("public", {}).get("manifest_digest"):
        problems.append("public: not the backup this set recorded")
    if sto["ok"] and sto["manifest_digest"] != c.get("store", {}).get("manifest_digest"):
        problems.append("store: not the backup this set recorded")
    if not problems:
        mf = _jload(lg_manifest)
        built, season = built_events(os.path.join(setdir, "store", PBK.FILES))
        pev, pseason = public_state(os.path.join(setdir, "public", PBK.FILES))
        sm, pm = PBK.load_manifest(os.path.join(setdir, "store")), PBK.load_manifest(os.path.join(setdir, "public"))
        problems += rules(mf["journal_head"]["seq"], built, season, pev, pseason, sm["snapshot"]["quiescent"], True, True)
    return {"ok": not problems, "problems": problems[:30], "set": setdir, "components": c}


def restore(setdir, dest, fsync=True):
    setdir, dest = os.path.abspath(setdir), os.path.abspath(dest)
    try:
        with PBK.name_lock(dest, "recovery set restore"):
            return _restore(setdir, dest, fsync)
    except PBK.Busy as e:
        return {"ok": False, "stage": "lock", "problems": [str(e)]}


def _restore(setdir, dest, fsync):
    v = verify(setdir)
    if not v["ok"]:
        return {"ok": False, "stage": "verify set", "problems": v["problems"]}
    if os.path.exists(dest) and (not os.path.isdir(dest) or os.listdir(dest)):
        return {"ok": False, "stage": "destination", "problems": ["%s exists and is not empty" % dest]}
    stage = dest + ".restoring"
    if os.path.exists(stage):
        return {"ok": False, "stage": "destination", "problems": ["%s exists (an earlier restore did not finish)" % stage]}
    os.makedirs(os.path.join(stage, "league", "ctl"))
    tmp = os.path.join(stage, "_league")
    p = py([os.path.join(ROOT, "tools", "league_backup.py"), "restore", os.path.join(setdir, "league"), tmp], check=False)
    if p.returncode != 0:
        return {"ok": False, "stage": "restore league", "problems": [(p.stdout or p.stderr)[-400:]]}
    os.replace(os.path.join(tmp, "league.db"), os.path.join(stage, "league", "league.db"))
    if os.path.exists(os.path.join(tmp, "archive.db")):
        # league_backup restores the pair as league.db + archive.db and the league records "archive.db". The live layout (and the builder, the runner status and the
        # export tools) use league.db.archive, so rename it and re-point the league's own record, exactly as persist.restore does for an archive_path.
        os.replace(os.path.join(tmp, "archive.db"), os.path.join(stage, "league", "league.db.archive"))
        sys.path.insert(0, os.path.join(ROOT, "src"))
        from dfl import persist  # noqa: E402
        db = persist.connect(os.path.join(stage, "league", "league.db"))
        try:
            with db:
                db.execute("UPDATE meta SET value = ? WHERE key = 'archive'", (json.dumps({"file": "league.db.archive"}, sort_keys=True),))
        finally:
            db.close()
    for n in CTL_FILES:
        q = os.path.join(setdir, "league", "config", n)             # (league_backup keeps these under config/ and lists them, with SHA-256, in its manifest)
        if os.path.exists(q):
            shutil.copyfile(q, os.path.join(stage, "league", "ctl", n))
    shutil.rmtree(tmp)
    for comp in ("store", "public"):
        r = PBK.restore(os.path.join(setdir, comp), os.path.join(stage, comp), fsync=fsync, lock=False)
        if not r["ok"]:
            return {"ok": False, "stage": "restore " + comp, "problems": r["problems"]}
    if os.path.isdir(dest):
        os.rmdir(dest)
    os.replace(stage, dest)
    return {"ok": True, "dest": dest, "root": dest, "public": os.path.join(dest, "public"), "league": os.path.join(dest, "league", "league.db")}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--root", required=True)
    c.add_argument("--public", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--note")
    c.add_argument("--no-preflight", action="store_true", help="skip the disk-space and RAM check (tools/backup_space.py)")
    c.add_argument("--allow-ram", action="store_true", help="accept an output folder on tmpfs/ramfs")
    v = sub.add_parser("verify")
    v.add_argument("set")
    v.add_argument("--deep", action="store_true")
    r = sub.add_parser("restore")
    r.add_argument("set")
    r.add_argument("dest")
    q = sub.add_parser("quarantine")
    q.add_argument("partial")
    q.add_argument("--to")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "create":
            if not a.no_preflight:
                import backup_space as BS
                lg = os.path.join(a.root, "league")
                pre = BS.preflight_set(a.public, os.path.join(a.root, "store"), [os.path.join(lg, "league.db"), os.path.join(lg, "league.db.archive")], a.out, a.allow_ram)
                if not pre["ok"]:
                    print(json.dumps(pre, sort_keys=True))
                    return 1
            res = create(a.root, a.public, a.out, a.note)
        elif a.cmd == "verify":
            res = verify(a.set, a.deep)
        elif a.cmd == "quarantine":
            res = quarantine(a.partial, a.to)
        else:
            res = restore(a.set, a.dest)
    except (Failed, PBK.Failed) as e:
        res = {"ok": False, "problems": [str(e)]}
    print(json.dumps(res, sort_keys=True))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
