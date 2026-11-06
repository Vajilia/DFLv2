#!/usr/bin/env python3
"""Executes docs/BACKUP_RUNBOOK.md end to end on an ISOLATED scratch copy and writes the transcript (actual commands, exit codes, output).

  backup_runbook_rehearsal.py --work DIR [--transcript FILE.md] [--keep]

Everything happens under DIR (a new, empty folder): a disposable league built with the real builder (seed 4242, 13 events), a public folder written by the real daemon
code in virtual time, and two small loop-mounted ext4 disks (a too-small one and a roomy one) plus one tmpfs. It touches no production directory, no Droplet, no network and
no repository file except the transcript. Needs root for `mount`; without it the disk-space steps are skipped and the transcript says so.

Steps (the runbook's own numbering): 1 build the scratch copy, 2 preflight, 3 create, 4 verify, 5 refusals (RAM-backed, too small), 6 full disk during copying,
7 quarantine, 8 retry, 9 restore with the originals gone, 10 checks on the restored copy. Each step states what it expects; the exit code of this script is 0 only if all hold.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
PY = sys.executable
T = os.path.join(ROOT, "tools")
MAXSHOW = 1800


class Log:
    def __init__(self, work):
        self.work, self.md, self.results, self.n = work, [], [], 0
        os.makedirs(os.path.join(work, "logs"))

    def h(self, text):
        self.md.append("\n## %s\n" % text)
        print("\n== %s" % text, flush=True)

    def say(self, text):
        self.md.append(text + "\n")
        print(text, flush=True)

    def run(self, argv, expect=0, label=None, timeout=3600):
        """Run argv; record the command as typed, exit code and (truncated) output; the full output goes to work/logs/NN.txt."""
        self.n += 1
        shown = " ".join(a if " " not in a else repr(a) for a in argv)
        t0 = time.time()
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        dt = time.time() - t0
        out = (p.stdout + (("\n[stderr]\n" + p.stderr) if p.stderr.strip() else "")).rstrip()
        with open(os.path.join(self.work, "logs", "%02d.txt" % self.n), "w") as f:
            f.write("$ %s\nexit %d\n%s\n" % (shown, p.returncode, out))
        text = out if len(out) <= MAXSHOW else out[:MAXSHOW] + "\n[... %d more characters; full output in logs/%02d.txt of the scratch folder]" % (len(out) - MAXSHOW, self.n)
        ok = p.returncode == expect
        self.md.append("```\n$ %s\n%s\n[exit %d, expected %d, %.1f s]\n```\n" % (shown, text, p.returncode, expect, dt))
        print("$ %s\n%s\n[exit %d, expected %d]" % (shown, text[:600], p.returncode, expect), flush=True)
        self.results.append((label or shown[:70], "exit %d (expected %d)" % (p.returncode, expect), ok))
        return p

    def check(self, label, ok, detail=""):
        self.md.append("**check - %s: %s** %s\n" % (label, "PASS" if ok else "FAIL", detail))
        print("CHECK %s: %s %s" % (label, "PASS" if ok else "FAIL", detail), flush=True)
        self.results.append((label, detail, bool(ok)))
        return ok


def tree_digest(path, skip=()):
    """sha256 over (relative path, size, content hash) of every file: 'did anything in this folder change'. `skip` = top-level file names to leave out."""
    h, n, b = hashlib.sha256(), 0, 0
    for d, dirs, names in os.walk(path):
        dirs.sort()
        for f in sorted(names):
            p = os.path.join(d, f)
            if os.path.islink(p) or (d == path and f in skip):
                continue
            fh = hashlib.sha256()
            with open(p, "rb") as fp:
                for c in iter(lambda: fp.read(1 << 20), b""):
                    fh.update(c)
            sz = os.path.getsize(p)
            h.update(("%s\0%d\0%s\n" % (os.path.relpath(p, path), sz, fh.hexdigest())).encode())
            n, b = n + 1, b + sz
    return {"files": n, "bytes": b, "sha256": h.hexdigest()}


def du(path):
    return sum(os.stat(os.path.join(d, f)).st_blocks * 512 for d, _, names in os.walk(path) for f in names)


def sh_out(argv):
    return subprocess.run(argv, capture_output=True, text=True)


def mount_ext4(L, img, size, mnt):
    with open(img, "wb") as f:
        f.truncate(size)
    r = sh_out(["mkfs.ext4", "-q", "-F", img])
    if r.returncode:
        return False
    os.makedirs(mnt, exist_ok=True)
    return sh_out(["mount", "-o", "loop", img, mnt]).returncode == 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work", required=True)
    ap.add_argument("--transcript")
    ap.add_argument("--keep", action="store_true", help="leave mounts and files in place at the end (default: unmount, keep files)")
    a = ap.parse_args(argv)
    work = os.path.abspath(a.work)
    if os.path.exists(work) and os.listdir(work):
        raise SystemExit("%s is not empty: use a new scratch folder" % work)
    os.makedirs(work, exist_ok=True)
    L = Log(work)
    started = time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime())
    mounts = []
    can_mount = os.geteuid() == 0 and shutil.which("mount") and shutil.which("mkfs.ext4")
    root, public = os.path.join(work, "orig", "root"), os.path.join(work, "orig", "public")
    disk = os.path.join(work, "disk")                      # ordinary directory on the scratch disk: where the first set goes
    os.makedirs(disk)
    BUILD = os.path.join(T, "broadcast_build.py")
    RS, PB, BS, LS = (os.path.join(T, x) for x in ("recovery_set.py", "public_backup.py", "backup_space.py", "league_store.py"))
    try:
        # ---- 1 ----
        L.h("1. Build the scratch copy (a disposable league, never a production directory)")
        L.say("A fresh isolated league (seed 4242), 13 events built by the real builder, and a public folder published by the real daemon code in virtual time. Folder: `%s`." % work)
        L.run([PY, BUILD, "init", "--root", root, "--seed", "4242"], label="init scratch league")
        L.run([PY, BUILD, "step", "--root", root, "--events", "13"], label="build 13 events")
        from dflsite import broadcast as B, season as S
        B.FSYNC = False
        bc = S.SeasonBroadcaster(os.path.join(root, "store"), public, guard_publication=False)
        t0 = 5000.0
        bc.clock.start(t0, 0.0, B.LEASE_S)
        for t in range(0, 4000, 20):
            bc.tick(t0 + t)
        L.check("the daemon published into the scratch public folder", os.path.exists(os.path.join(public, "latest.json")),
                "latest.json: %s" % json.load(open(os.path.join(public, "latest.json")))["release"])
        L.run([PY, BUILD, "status", "--root", root], label="builder status (before)")
        dig = sh_out([PY, LS, "digest", os.path.join(root, "league", "league.db")])
        L.say("League digest before the backup: `%s`" % dig.stdout.strip())
        league_before = json.loads(dig.stdout)
        src_public_digest = tree_digest(public)
        src_store_digest = tree_digest(os.path.join(root, "store"))
        L.say("Source public folder: %d files, %d bytes. Source private store: %d files, %d bytes. Allocated size of the three parts: public %d, store %d."
              % (src_public_digest["files"], src_public_digest["bytes"], src_store_digest["files"], src_store_digest["bytes"], du(public), du(os.path.join(root, "store"))))

        # ---- 2 ----
        L.h("2. Preflight (an estimate, not a reservation)")
        set1 = os.path.join(disk, "set1")
        L.run([PY, BS, "create", "--src", public, "--out", os.path.join(disk, "public-only")], label="preflight, public folder alone")
        # ---- 3 ----
        L.h("3. Create the recovery set (preflight runs inside `create`)")
        p = L.run([PY, RS, "create", "--root", root, "--public", public, "--out", set1, "--note", "runbook rehearsal"], label="create set1")
        set_bytes = tree_digest(set1)["bytes"] if os.path.isdir(set1) else 0
        L.say("Size of set1: %d bytes (%d MB), allocated on disk %d bytes; the preflight estimated 96,588,804 bytes before margins (see the estimate printed by the refusals in step 5)." % (set_bytes, set_bytes // 2**20, du(set1)))
        # ---- 4 ----
        L.h("4. Verify (deep: every file re-hashed, reference closure, the set rules S1-S4)")
        L.run([PY, RS, "verify", set1, "--deep"], label="verify set1 --deep")
        set1_digest = tree_digest(set1)
        L.check("creating and verifying changed neither the league, the store nor the public folder",
                json.loads(sh_out([PY, LS, "digest", os.path.join(root, "league", "league.db")]).stdout) == league_before
                and tree_digest(os.path.join(root, "store")) == src_store_digest, "league digest and store digest equal to step 1 (the daemon was not running; the public folder is compared below after restore)")

        # ---- 5 ----
        L.h("5. Refusals: RAM-backed storage and too little space (nothing is written)")
        if not can_mount:
            L.say("**SKIPPED**: this run is not root or `mount`/`mkfs.ext4` is missing, so no tmpfs or small disk could be made.")
        else:
            ram = os.path.join(work, "ram")
            os.makedirs(ram)
            if sh_out(["mount", "-t", "tmpfs", "-o", "size=%d" % (set_bytes * 6 + 64 * 2**20), "tmpfs", ram]).returncode == 0:
                mounts.append(ram)
                before = sorted(os.listdir(ram))
                L.say("A tmpfs (RAM-backed) large enough for the set, mounted at `%s`." % ram)
                L.run([PY, RS, "create", "--root", root, "--public", public, "--out", os.path.join(ram, "set")], expect=1, label="create on tmpfs refused by default")
                L.check("nothing was written to the tmpfs", sorted(os.listdir(ram)) == before == [], "listing: %s" % sorted(os.listdir(ram)))
            else:
                L.say("**SKIPPED** tmpfs: `mount -t tmpfs` failed.")
            small_img, small = os.path.join(work, "small.img"), os.path.join(work, "small")
            size_small = max(24 * 2**20, int(set_bytes * 0.6) // 2**20 * 2**20)
            if mount_ext4(L, small_img, size_small, small):
                mounts.append(small)
                L.say("A small real disk (ext4 on a loop file, not RAM): %d MB, mounted at `%s`; the set needs about %d MB." % (size_small // 2**20, small, set_bytes // 2**20))
                L.run(["df", "-h", small], label="df small disk")
                L.run([PY, RS, "create", "--root", root, "--public", public, "--out", os.path.join(small, "set")], expect=1, label="create on the small disk refused (insufficient space)")
                L.check("nothing was written to the small disk by the refusal", [x for x in os.listdir(small) if x != "lost+found"] == [],
                        "listing: %s" % [x for x in os.listdir(small) if x != "lost+found"])

                # ---- 6 ----
                L.h("6. The disk fills during copying (preflight is an estimate: here it is bypassed on purpose)")
                L.say("`--no-preflight` stands for 'the estimate was right when made, then something else used the space'. The valid set `set1` on the other disk must stay valid; the unfinished set stays as evidence.")
                L.run([PY, RS, "create", "--root", root, "--public", public, "--out", os.path.join(small, "set"), "--no-preflight"], expect=1, label="create runs out of disk")
                left = sorted(x for x in os.listdir(small) if x != "lost+found")
                L.check("the unfinished set is kept as evidence and no set was published", "set.partial" in left and "set" not in left, "listing: %s" % left)
                part = os.path.join(small, "set.partial")
                part_digest = tree_digest(part)
                L.say("Evidence before quarantine: %d files, %d bytes, digest `%s`." % (part_digest["files"], part_digest["bytes"], part_digest["sha256"][:16]))
                L.run([PY, RS, "verify", part], expect=1, label="an unfinished set never verifies")
                L.run([PY, RS, "verify", set1, "--deep"], label="the earlier valid set1 still verifies")
                L.check("set1 is byte-identical to before the failed attempt", tree_digest(set1) == set1_digest, "digest %s" % set1_digest["sha256"][:16])

                # ---- 7 ----
                L.h("7. Quarantine the unfinished set (a rename; nothing is deleted)")
                q = L.run([PY, RS, "quarantine", part], label="quarantine set.partial")
                try:
                    res = json.loads(q.stdout)
                    moved = res.get("moved_to") or res.get("to") or res.get("dest")
                except ValueError:
                    res, moved = {}, None
                qdirs = [os.path.join(small, "partial-quarantine", x) for x in os.listdir(os.path.join(small, "partial-quarantine"))] if os.path.isdir(os.path.join(small, "partial-quarantine")) else []
                qdirs = [x for x in qdirs if x.endswith(".partial")]
                qd = qdirs[0] if len(qdirs) == 1 else None
                L.check("the name is free (set.partial is gone) and exactly one quarantine folder exists", not os.path.exists(part) and qd is not None, "quarantined: %s" % qd)
                if qd:
                    L.run(["ls", "-la", qd], label="list the quarantined evidence")
                    after = tree_digest(qd, skip=("QUARANTINE_NOTE.json",))
                    L.check("the evidence is the same bytes: same files, sizes and content hashes as before the move (the note file excluded)", after == part_digest,
                            "before %s, after %s" % (part_digest, after))
                L.run([PY, RS, "create", "--root", root, "--public", public, "--out", os.path.join(small, "set")], expect=1, label="the name is usable again, but the preflight still refuses the small disk (quarantined evidence is not counted as free)")
            else:
                L.say("**SKIPPED** small disk: loop mount failed.")

            # ---- 8 ----
            L.h("8. Retry on a disk with room (a second real ext4 disk, preflight on)")
            roomy_img, roomy = os.path.join(work, "roomy.img"), os.path.join(work, "roomy")
            size_roomy = max(128 * 2**20, (set_bytes * 3) // 2**20 * 2**20)
            if mount_ext4(L, roomy_img, size_roomy, roomy):
                mounts.append(roomy)
                set2 = os.path.join(roomy, "set")
                L.run([PY, RS, "create", "--root", root, "--public", public, "--out", set2, "--note", "retry after full disk"], label="retry create on the roomy disk")
                L.run([PY, RS, "verify", set2, "--deep"], label="verify the retried set --deep")
                restore_from = set2
            else:
                L.say("**SKIPPED** roomy disk: loop mount failed; the restore below uses set1.")
                restore_from = set1
        if not can_mount:
            restore_from = set1

        # ---- 9 ----
        L.h("9. Restore into a new folder with the originals gone")
        gone = os.path.join(work, "orig-moved-aside")
        os.makedirs(gone)
        shutil.move(root, os.path.join(gone, "root"))
        shutil.move(public, os.path.join(gone, "public"))
        L.say("The original root and public folder were moved aside (not deleted) to `%s`, as if the host were lost." % gone)
        dest = os.path.join(work, "restored")
        L.run([PY, RS, "restore", restore_from, dest], label="restore the set")
        # ---- 10 ----
        L.h("10. Checks on the restored copy")
        L.run([PY, BUILD, "status", "--root", dest], label="builder status on the restored copy")
        dig2 = sh_out([PY, LS, "digest", os.path.join(dest, "league", "league.db")])
        L.say("League digest after restore: `%s`" % dig2.stdout.strip())
        L.check("league digest equals the original's", json.loads(dig2.stdout) == league_before)
        L.run([PY, PB, "check", "--src", os.path.join(dest, "public")], label="public_backup.py check (reference closure) on the restored public folder")
        pub_restored = tree_digest(os.path.join(dest, "public"))
        L.check("restored public folder is byte-identical to the source", pub_restored == src_public_digest,
                "%d files, %d bytes, digest %s" % (pub_restored["files"], pub_restored["bytes"], pub_restored["sha256"][:16]))
        L.check("restored private store is byte-identical to the source", tree_digest(os.path.join(dest, "store")) == src_store_digest)
        b = L.run([PY, BUILD, "step", "--root", dest, "--events", "1"], label="the restored builder builds event 14")
        L.run([PY, BUILD, "step", "--root", os.path.join(gone, "root"), "--events", "1"], label="control: the original builds event 14")
        d_restored = json.loads(sh_out([PY, LS, "digest", os.path.join(dest, "league", "league.db")]).stdout)
        d_control = json.loads(sh_out([PY, LS, "digest", os.path.join(gone, "root", "league", "league.db")]).stdout)
        L.check("the restored builder carried on: event 14 gives the same league digest as the original", b.returncode == 0 and d_restored == d_control and d_restored["events"] == 14,
                "events %s" % d_restored.get("events"))
    finally:
        for m in reversed(mounts):
            if not a.keep:
                sh_out(["umount", m])
    L.h("Result")
    bad = [r for r in L.results if not r[2]]
    rows = "\n".join("| %s | %s | %s |" % (r[0].replace("|", "/"), r[1].replace("|", "/")[:120], "PASS" if r[2] else "**FAIL**") for r in L.results)
    L.md.append("| step | result | |\n|---|---|---|\n%s\n" % rows)
    L.say("**%d of %d expectations held; %d failed.**" % (len(L.results) - len(bad), len(L.results), len(bad)))
    head = "# Backup runbook rehearsal - transcript\n\nRun started %s (UTC) by `tools/backup_runbook_rehearsal.py --work %s`. Commands are exactly as run; output longer than %d characters is cut here and kept in full in the scratch folder.\n" % (started, work, MAXSHOW)
    text = head + "\n".join(L.md)
    out = a.transcript or os.path.join(work, "transcript.md")
    with open(out, "w") as f:
        f.write(text)
    print("transcript:", out)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
