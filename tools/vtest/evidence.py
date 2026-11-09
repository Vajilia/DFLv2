"""Evidence manifest and archive completeness (docs/MEMORY_MONITOR_VALIDATION_CRITERIA.md, section 9).

  collect_host   read-only: the unit journals, the kernel log and its OOM lines, unit states, failed units and machine facts for the run's time span, kept inside the run's evidence folder
  build_manifest every item a run must leave, each with its size and SHA-256, or the explicit status MISSING (it should exist and does not) or NOT_PRODUCED (the stage stopped before the step that makes it)
  make_archive   one compressed archive of the run's evidence and the install record
  verify_archive read the archive back and check every `present` item inside it against the manifest

Nothing here deletes anything or copies anything off the machine. Getting the archive off the machine is the owner's approved private method; this module only says whether the archive is complete.
"""
import hashlib
import json
import os
import re
import tarfile
import time

from common import EVID, STATE, UNIT, sha256_file

PRESENT, MISSING, NOT_PRODUCED = "present", "MISSING", "NOT_PRODUCED"
RUN_FILES = [("run.json", "the run record: pin, harness revision, preflight rows, deviations"), ("SUMMARY.json", "the run's stage results"), ("command_output.txt", "everything the run command printed"),
             ("host/install.json", "what `install` did (copy of the install record)"), ("host/machine.txt", "machine facts: kernel, systemd, cgroup controllers, memory, cpus, disk"),
             ("host/units.txt", "the test units' states, restart counts and results at the end"), ("host/failed-units.txt", "systemctl --failed at the end"),
             ("host/journal-daemon.txt", "the daemon unit's journal for the run"), ("host/journal-monitor.txt", "the monitor unit's journal for the run"), ("host/journal-site.txt", "the loopback site unit's journal for the run"),
             ("host/kernel.txt", "the kernel log for the run"), ("host/kernel-oom.txt", "the OOM-related lines of the kernel log (an empty file is evidence that there were none)")]
STAGE_ALWAYS = ["result.json", "MANIFEST.sha256", "timeline.jsonl"]
STAGE_SAMPLES = {   # (file, when): "always" must exist; "later" must exist unless the stage stopped before the step that writes it
    "V1": [("daemon_cgroup.jsonl", "always"), ("broadcast_probe.jsonl", "always"), ("segment_inventory_publish.json", "always"), ("journal_daemon_publish.txt", "always"),
           ("cache_loader.jsonl", "later"), ("segment_inventory_cache.json", "later"), ("journal_daemon_cache.txt", "later"), ("clock_log.jsonl", "later"), ("monitor_watch.log", "later"), ("monitor_trips.jsonl", "later")],
    "V2": [("v2a_cgroup.jsonl", "always"), ("v2a_anon.jsonl", "always"), ("v2b_cgroup.jsonl", "later"), ("v2b_anon.jsonl", "later"), ("v2c_cgroup.jsonl", "later"), ("v2c_anon.jsonl", "later")],
    "V3": [], "V4": []}
OOM = re.compile(r"oom|out of memory|killed process", re.I)


# ---------------------------------------------------------------------------------------------------------------------------------- host records
def collect_host(env, rdir, since, echo=print):
    """Write the host records for a run. A record that cannot be read is written as `UNAVAILABLE: reason`, which the manifest reports as MISSING."""
    out = {}

    def put(name, text, ok=True):
        out[name] = text if ok else "UNAVAILABLE: " + (text or "no output")

    def cmd(argv, timeout=120):
        rc, so, se = env.run(argv, timeout=timeout)
        return rc == 0 and bool((so or "").strip()), (so if rc == 0 else (se or so or "rc %s" % rc))

    for k in ("daemon", "monitor", "site"):
        ok, t = cmd(["journalctl", "-u", UNIT[k], "--since", "@%d" % int(since), "--no-pager", "-o", "short-iso"])
        put("host/journal-%s.txt" % k, t, ok)
    ok, kern = cmd(["journalctl", "-k", "--since", "@%d" % int(since), "--no-pager", "-o", "short-iso"])
    if not ok:
        ok, kern = cmd(["dmesg", "-T"])
    put("host/kernel.txt", kern, ok)
    out["host/kernel-oom.txt"] = ("\n".join(l for l in kern.splitlines() if OOM.search(l)) + "\n") if ok else "UNAVAILABLE: the kernel log could not be read"
    units = []
    for k in ("daemon", "monitor", "site"):
        rc, so, se = env.sc("show", UNIT[k], "-pActiveState", "-pSubState", "-pResult", "-pNRestarts", "-pMainPID", "-pExecMainStatus", "-pMemoryMax", "-pOOMPolicy")
        units.append("[%s]\n%s" % (UNIT[k], so if rc == 0 else "UNAVAILABLE: " + (se or "rc %s" % rc)))
    put("host/units.txt", "\n".join(units), all("UNAVAILABLE" not in u for u in units))
    rc, so, se = env.run(["systemctl", "--failed", "--no-pager", "--no-legend"])
    out["host/failed-units.txt"] = (so if rc == 0 else "UNAVAILABLE: " + (se or "rc %s" % rc)) or "(none)\n"
    facts = []
    for label, argv in (("uname", ["uname", "-a"]), ("systemd", ["systemctl", "--version"]), ("disk", ["df", "-h", "/srv"])):
        rc, so, se = env.run(argv)
        facts.append("== %s\n%s" % (label, so if rc == 0 else "UNAVAILABLE: " + (se or "rc %s" % rc)))
    for label, path in (("os-release", "/etc/os-release"), ("cgroup.controllers", "/sys/fs/cgroup/cgroup.controllers"), ("meminfo (head)", "/proc/meminfo"), ("cpuinfo (processors)", "/proc/cpuinfo")):
        txt = env.read(path)
        if txt is None:
            facts.append("== %s\nUNAVAILABLE: not readable" % label)
        elif path.endswith("meminfo"):
            facts.append("== %s\n%s" % (label, "\n".join(txt.splitlines()[:3])))
        elif path.endswith("cpuinfo"):
            facts.append("== %s\n%d" % (label, sum(1 for l in txt.splitlines() if l.startswith("processor"))))
        else:
            facts.append("== %s\n%s" % (label, txt))
    out["host/machine.txt"] = "\n".join(facts) + "\n"
    inst = env.read(STATE + "/install.json")
    out["host/install.json"] = inst if inst is not None else "UNAVAILABLE: no install record"
    for name, text in out.items():
        path = os.path.join(rdir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        echo("  host record %-28s %6d bytes%s" % (name, len(text), "   UNAVAILABLE" if text.startswith("UNAVAILABLE") else ""))
    return out


# ---------------------------------------------------------------------------------------------------------------------------------- manifest
def tree_digest(rdir):
    """One hash for everything in the run's folder except the manifest files themselves (what teardown compares to be sure nothing was added after the archive)."""
    lines = []
    for d, _, names in sorted(os.walk(rdir)):
        for n in sorted(names):
            if n.startswith("EVIDENCE_MANIFEST"):
                continue
            p = os.path.join(d, n)
            lines.append("%s  %s" % (sha256_file(p), os.path.relpath(p, rdir)))
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def item(rdir, rel, why, when="always", stopped=False):
    p = os.path.join(rdir, rel)
    if os.path.isfile(p):
        with open(p, "rb") as f:
            head = f.read(12)
        if head.startswith(b"UNAVAILABLE:"):
            return {"path": rel, "what": why, "status": MISSING, "reason": "the record could not be read on the machine: " + open(p).read(200).strip()}
        return {"path": rel, "what": why, "status": PRESENT, "size": os.path.getsize(p), "sha256": sha256_file(p)}
    if when == "later" and stopped:
        return {"path": rel, "what": why, "status": NOT_PRODUCED, "reason": "the stage stopped before the step that writes it"}
    return {"path": rel, "what": why, "status": MISSING, "reason": "the file does not exist"}


def build_manifest(rdir, run_id):
    """-> dict. `complete` is True when no item is MISSING (NOT_PRODUCED is listed but does not count: the stage stopped, as designed)."""
    items = [item(rdir, rel, why) for rel, why in RUN_FILES]
    summary = {}
    try:
        summary = json.load(open(os.path.join(rdir, "SUMMARY.json")))
    except (OSError, ValueError):
        pass
    stages = sorted({n for n in os.listdir(rdir) if re.fullmatch(r"V\d", n)} | set((summary.get("results") or {}).keys()))
    for st in stages:
        sdir = os.path.join(rdir, st)
        try:
            overall = json.load(open(os.path.join(sdir, "result.json"))).get("overall")
        except (OSError, ValueError):
            overall = None
        stopped = overall != "pass"
        for n in STAGE_ALWAYS:
            items.append(item(rdir, "%s/%s" % (st, n), "stage %s: %s" % (st, {"result.json": "its checks and result", "MANIFEST.sha256": "the hashes of its evidence files", "timeline.jsonl": "its step marks"}[n])))
        for n, when in STAGE_SAMPLES.get(st, []):
            items.append(item(rdir, "%s/%s" % (st, n), "stage %s sample or record" % st, when, stopped))
    listed = {i["path"] for i in items}
    extra = []
    for d, _, names in sorted(os.walk(rdir)):
        for n in sorted(names):
            rel = os.path.relpath(os.path.join(d, n), rdir)
            if rel not in listed and not rel.startswith("EVIDENCE_MANIFEST"):
                extra.append({"path": rel, "what": "recorded, not required", "status": PRESENT, "size": os.path.getsize(os.path.join(d, n)), "sha256": sha256_file(os.path.join(d, n))})
    missing = [i["path"] for i in items if i["status"] == MISSING]
    return {"run_id": run_id, "created": time.time(), "stages": stages, "required": items, "extra": extra, "missing": missing, "not_produced": [i["path"] for i in items if i["status"] == NOT_PRODUCED], "complete": not missing}


def render_manifest(m):
    lines = ["EVIDENCE MANIFEST for run %s   complete: %s" % (m["run_id"], "YES" if m["complete"] else "NO"), ""]
    for i in m["required"]:
        lines.append("%-12s %-44s %s" % (i["status"], i["path"], ("%d bytes  %s" % (i["size"], i["sha256"])) if i["status"] == PRESENT else i["reason"]))
    if m["extra"]:
        lines += ["", "also recorded (%d files, listed in EVIDENCE_MANIFEST.json)" % len(m["extra"])]
    if m["missing"]:
        lines += ["", "MISSING: " + ", ".join(m["missing"])]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------------------------------------------------- archive
def make_archive(rdir, run_id, out_path, install_path=None):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with tarfile.open(out_path, "w:gz") as tf:
        for d, dirs, names in os.walk(rdir):
            dirs.sort()
            for n in sorted(names):
                p = os.path.join(d, n)
                tf.add(p, arcname=os.path.join("evidence", run_id, os.path.relpath(p, rdir)), recursive=False)
        if install_path and os.path.isfile(install_path):
            tf.add(install_path, arcname="state/install.json", recursive=False)
    return sha256_file(out_path), os.path.getsize(out_path)


def verify_archive(archive_path, manifest, run_id):
    """Read the archive back. -> (ok, problems): every `present` item (required and extra) is inside it with the manifest's size and hash, and both manifest files are in it."""
    problems, seen = [], {}
    try:
        with tarfile.open(archive_path, "r:gz") as tf:
            for m in tf.getmembers():
                if m.isfile():
                    h = hashlib.sha256()
                    f = tf.extractfile(m)
                    for b in iter(lambda: f.read(1 << 20), b""):
                        h.update(b)
                    seen[m.name] = (m.size, h.hexdigest())
    except (OSError, tarfile.TarError) as e:
        return False, ["the archive cannot be read back: %s" % e]
    for i in manifest["required"] + manifest["extra"]:
        if i["status"] != PRESENT:
            continue
        name = "evidence/%s/%s" % (run_id, i["path"])
        got = seen.get(name)
        if got is None:
            problems.append("%s is not in the archive" % i["path"])
        elif got != (i["size"], i["sha256"]):
            problems.append("%s differs in the archive (size/hash)" % i["path"])
    for n in ("EVIDENCE_MANIFEST.json", "EVIDENCE_MANIFEST.txt"):
        if "evidence/%s/%s" % (run_id, n) not in seen:
            problems.append("%s is not in the archive" % n)
    return not problems, problems


def archive_record_path(run_id):
    return os.path.join(STATE, "archive-%s.json" % run_id)


def archive_gate(runs, echo=print):
    """For teardown --include-evidence: every run folder has a verified archive record and nothing was added since. -> (ok, problems)."""
    problems = []
    for r in runs:
        rec_path = archive_record_path(r)
        try:
            rec = json.load(open(rec_path))
        except (OSError, ValueError):
            problems.append("%s: no verified archive (run `vtest.py archive --run-id %s`)" % (r, r))
            continue
        if not rec.get("verified"):
            problems.append("%s: the archive did not verify" % r)
        elif rec.get("tree_digest") != tree_digest(os.path.join(EVID, r)):
            problems.append("%s: evidence changed after the archive was made (archive again)" % r)
        elif not rec.get("complete"):
            echo("WARNING %s: the archive is verified but INCOMPLETE; missing: %s" % (r, ", ".join(rec.get("missing", []))))
    return not problems, problems
