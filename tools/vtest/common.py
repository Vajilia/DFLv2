"""Shared parts of the validation harness (docs/MEMORY_MONITOR_VALIDATION_PLAN.md): the pin, the environment facade, the recorder and the safety rules.

Everything the harness does to a machine goes through `Env`, so that tests can substitute a fake and so that the safety rules live in one place:
  * writes are allowed only under the harness's own directories and `dfl-vtest-*` unit files;
  * systemctl verbs that change anything are allowed only on `dfl-vtest-*` units;
  * nothing here deletes anything except `teardown`, which is run by a person with --yes and never by a stage.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
REPO = os.path.dirname(TOOLS)
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

BASE = "/srv/dfl-vtest"                       # fixtures, loaders' files, guard folder, public folder, evidence
SRC_BASE = "/opt/dfl-vtest-src"               # the pinned tree, extracted from git
ETC = "/etc/dfl-vtest"                        # the environment file
MARKER = "/etc/dfl-vtest-disposable"          # the operator's declaration that this machine may be abused
UNIT_DIR = "/etc/systemd/system"
PREFIX = "dfl-vtest-"
UNIT = {"daemon": PREFIX + "daemon.service", "monitor": PREFIX + "monitor.service", "site": PREFIX + "site.service"}
PORT = 18080
CGROOT = "/sys/fs/cgroup"
SLICE = CGROOT + "/system.slice"
EXPORT = BASE + "/export"                     # archives made by `vtest.py archive` (kept by teardown)
ROOT_DIR, WEB_DIR, PUBLIC, GUARD, EVID, LOADDIR, STATE = (BASE + "/root", BASE + "/web", BASE + "/web/feed", BASE + "/guard", BASE + "/evidence", BASE + "/load", BASE + "/state")

STATUSES = ("pass", "fail", "invalid", "review", "not_run", "info")
STAGES = ("V0", "V1", "V2", "V3", "V4")
SERVICE_USER = "dfl-vtest"                    # the dedicated unprivileged user (and group of the same name) the daemon and monitor run as; created by the operator, never by the harness
PROBE = BASE + "/probe"                       # scratch folders of the lifecycle probe (V0): the real gate and hook run there under strace, never over the live guard folder

# What the daemon's own guard does (tools/broadcast_run.py): numbers the checks compare with. Read from the pinned tree at run time when possible; these are the fallbacks and are
# asserted equal to the tree's by `vtest.py pin-check`.
GUARD_STALE_S, GUARD_GRACE_S = 120.0, 240.0
MON_EVERY_S, MEM_SUSTAIN_S, MEM_FRAC = 10.0, 60.0, 0.90
# The broadcast design's own numbers (src/dflsite/broadcast.py) the broadcast-performance criteria rest on; asserted equal to the pinned tree's by `vtest.py pin-check`.
BC_SEG_S, BC_DELAY_S, BC_LEASE_S = 5, 3, 90


class Unsafe(Exception):
    """A step tried to touch something outside the harness's own territory."""


class Busy(Exception):
    """Another harness command holds the run lock."""


class RunLock:
    """An exclusive, no-wait lock taken by every command that changes the machine or reads what a run is writing (install, run, teardown, start-units, archive).
    Found 2026-11-11: two `run --run-id smoke-2` commands overlapped and overwrote one another's evidence. flock is released by the kernel when the holder exits or is killed, and the
    descriptor is not inherited by child processes, so a stage's helpers cannot keep it alive. The file lives in BASE, which teardown never removes."""
    def __init__(self, label):
        self.label, self.fd = label, None

    @staticmethod
    def path():
        return BASE + "/run.lock"

    def __enter__(self):
        import fcntl
        os.makedirs(BASE, exist_ok=True)
        fd = os.open(self.path(), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            try:
                holder = os.pread(fd, 400, 0).decode("utf-8", "replace").strip()
            finally:
                os.close(fd)
            raise Busy("another vtest command holds the run lock (%s). This command does not wait: let that one finish, or look at `ps` for it. Nothing was changed." % (holder or "holder unknown"))
        os.ftruncate(fd, 0)
        os.pwrite(fd, ("pid %d: %s (since %s)" % (os.getpid(), self.label, time.strftime("%Y-%m-%dT%H:%M:%S%z"))).encode(), 0)
        self.fd = fd
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)                     # closing releases the flock
            self.fd = None
        return False


class StageStop(Exception):
    """An acceptance check failed (or the stage cannot continue): stop, keep everything as it is."""


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def read_pin(repo=REPO):
    for line in open(os.path.join(repo, "tools", "vtest", "PIN")):
        m = re.match(r"candidate=([0-9a-f]{40})\s*$", line)
        if m:
            return m.group(1)
    raise SystemExit("tools/vtest/PIN has no candidate=<40 hex> line")


def allowed_path(path):
    p = os.path.realpath(path) if os.path.exists(path) else os.path.abspath(path)
    for root in (BASE, SRC_BASE, ETC):
        if p == root or p.startswith(root + "/"):
            return True
    return bool(re.match(re.escape(UNIT_DIR) + r"/" + re.escape(PREFIX) + r"[A-Za-z0-9@._-]*(\.d(/[A-Za-z0-9._-]+)?)?$", p))


SC_READ = {"show", "is-active", "is-enabled", "is-failed", "status", "list-units", "list-unit-files", "is-system-running", "--version", "daemon-reload", "cat"}
SC_WRITE = {"start", "stop", "restart", "kill", "reset-failed", "disable", "mask", "unmask", "enable"}
CG_WRITE_FILES = ("cgroup.procs", "memory.reclaim")


class Env:
    """The real machine."""
    def read(self, path, default=None):
        try:
            with open(path) as f:
                return f.read()
        except (OSError, UnicodeDecodeError):
            return default

    def exists(self, path):
        return os.path.exists(path)

    def listdir(self, path):
        try:
            return sorted(os.listdir(path))
        except OSError:
            return []

    def euid(self):
        return os.geteuid()

    def free_bytes(self, path):
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize

    def now(self):
        return time.time()

    def sleep(self, s):
        time.sleep(s)

    def run(self, argv, timeout=60, input=None):
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, input=input)
            return r.returncode, r.stdout, r.stderr
        except FileNotFoundError:
            return 127, "", "not found: %s" % argv[0]
        except subprocess.TimeoutExpired:
            return 124, "", "timeout after %ss: %s" % (timeout, " ".join(argv))

    def popen(self, argv, **kw):
        return subprocess.Popen(argv, **kw)

    def write(self, path, text, mode=0o644):
        if not allowed_path(path):
            raise Unsafe("refusing to write %s" % path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        os.chmod(path, mode)

    def cg_write(self, path, text):
        """Only the two control files a stage needs, only under a dfl-vtest unit's cgroup. -> (ok, errno or None)."""
        unit = os.path.basename(os.path.dirname(path))
        if not (os.path.basename(path) in CG_WRITE_FILES and os.path.dirname(os.path.dirname(path)) == SLICE and unit.startswith(PREFIX)):
            raise Unsafe("refusing to write cgroup file %s" % path)
        try:
            with open(path, "w") as f:
                f.write(text)
            return True, None
        except OSError as e:
            return False, e.errno

    def oom_adjust(self, pid, value):
        """Raise (never lower) the oom_score_adj of ONE process that is at this moment inside the DAEMON's dfl-vtest cgroup, to a value in 0..1000. This is the only way a stage influences which process the
        kernel's OOM killer picks, and only inside that disposable group: not the monitor's cgroup, not the host, nothing persistent (a restarted process starts at its default). -> (ok, errno or None)."""
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1000:
            raise Unsafe("refusing oom_score_adj %r: only 0..1000 (never a value that makes a process unkillable)" % (value,))
        pid = int(pid)
        if str(pid) not in (self.read("%s/%s/cgroup.procs" % (SLICE, UNIT["daemon"])) or "").split():
            raise Unsafe("refusing to touch pid %d: it is not in the daemon's dfl-vtest cgroup" % pid)
        path = "/proc/%d/oom_score_adj" % pid
        cur = (self.read(path) or "").strip()
        if not cur.lstrip("-").isdigit():
            return False, None
        if value < int(cur):
            raise Unsafe("refusing to lower oom_score_adj of pid %d from %s to %d" % (pid, cur, value))
        try:
            with open(path, "w") as f:
                f.write(str(value))
            return True, None
        except OSError as e:
            return False, e.errno

    def monotonic(self):
        return time.monotonic()

    def user_exists(self, name):
        import pwd
        try:
            pwd.getpwnam(name)
            return True
        except KeyError:
            return False

    def which(self, name):
        import shutil
        return shutil.which(name)

    service_user = None                       # set by the driver from the install record; None: the units run as root (a recorded deviation, never a qualifying run)

    def as_service(self, argv):
        """argv wrapped to run as the service user with no supplementary groups: how an operator runs the documented commands (`release`, `resume`), so that the files they create are the service's.
        Without a service user: argv unchanged."""
        if not self.service_user:
            return list(argv)
        import pwd
        pw = pwd.getpwnam(self.service_user)
        return ["setpriv", "--reuid", str(pw.pw_uid), "--regid", str(pw.pw_gid), "--clear-groups", "--"] + list(argv)

    def operator(self, argv, timeout=60, input=None):
        return self.run(self.as_service(argv), timeout=timeout, input=input)

    def boot_id(self):
        return (self.read("/proc/sys/kernel/random/boot_id") or "").strip() or None

    def immutable(self, path, on):
        """The one fault the harness injects on the filesystem: the guard folder made unwritable with `chattr +i` / `-i` (no entry can be created, renamed or removed in it, even by root).
        Only the harness's own guard folder; read back with lsattr. -> (ok, detail)."""
        if path != GUARD:
            raise Unsafe("refusing to change attributes of %s: only the guard folder %s may be made immutable" % (path, GUARD))
        rc, out, err = self.run(["chattr", "+i" if on else "-i", path])
        if rc:
            return False, "chattr rc %s: %s" % (rc, (err or out).strip()[:200])
        rc, out, err = self.run(["lsattr", "-d", path])
        flags = (out or "").split()[0] if out and out.split() else ""
        if rc or (("i" in flags) != bool(on)):
            return False, "lsattr after the change says %r" % (flags or (err or "").strip()[:100])
        return True, flags

    def sc(self, verb, *args, timeout=60):
        if verb in SC_WRITE:
            bad = [a for a in args if not (a.startswith(PREFIX) and a.endswith((".service", ".timer", ".scope")))]
            if bad or not args:
                raise Unsafe("systemctl %s on a unit that is not a dfl-vtest unit: %s" % (verb, bad or "(none named)"))
        elif verb not in SC_READ:
            raise Unsafe("systemctl %s is not allowed" % verb)
        return self.run(["systemctl", verb, *args], timeout=timeout)


# ---------------------------------------------------------------------------------------------------------------------------------- pin
def git(repo, *args):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


# Which paths are what. `docs` and `none` never invalidate a result; `stages` names the stages whose results a change to the path invalidates; code under test invalidates all.
CODE_UNDER_TEST_DEFAULT = "any other path under src/, tools/ or deploy/ (the safe default: when unsure, repeat)"
HARNESS_STAGE = {"stage_v0.py": {"V0"}, "stage_v1.py": {"V1"}, "stage_v2.py": {"V2"}, "stage_v3.py": {"V3"}, "stage_v4.py": {"V4"}}
# Harness modules whose code is used by named stages only (their judgments), not by the machinery that runs every stage.
HARNESS_JUDGES = {"lifecycle.py": {"V2", "V3", "V4"}, "perms.py": {"V0"}, "durability.py": {"V0"}}
HARNESS_ALL = {"common.py", "render.py", "sampler.py", "loaders.py", "site_server.py", "evaluate.py", "scenarios.py", "PIN"}
HARNESS_NONE = {"vtest.py", "preflight.py", "evidence.py", "README.md"}      # the driver and the go/no-go gate: they decide whether to run, they do not measure

# What each file of the code under test is read by. A stage is repeated when a changed file is one it depends on; a file nobody has analysed repeats everything (said, not hidden).
# V0 (setup facts: users, permissions, applied sandbox, lifecycle probe) is cheap and follows every change to code that runs under the units.
CODE_BEHAVIOUR = {
    "tools/broadcast_run.py": ({"V0", "V2", "V3", "V4"}, "the daemon, its start gate, its stop hook and its own guard; the monitor's memory rule is not in this file (V1)"),
    "tools/rehearsal_check.py": ({"V0", "V1", "V2", "V3", "V4"}, "the monitor: the memory rule (V1), its heartbeat and trips (V2, V3) and `release` (V4)"),
}
UNIT_LIFECYCLE = ("ExecStartPre", "ExecStopPost", "RequiresMountsFor", "OOMPolicy", "TimeoutStopSec", "Restart", "RestartSec", "RestartPreventExitStatus", "StartLimitIntervalSec", "StartLimitBurst")
UNIT_RESOURCE = ("MemoryMax", "MemoryHigh", "MemorySwapMax", "TasksMax", "Nice")
DAEMON_UNITS = ("deploy/supervisor/dfl-broadcast-daemon.service", "deploy/supervisor/dfl-broadcast-monitor.service")


def unit_diff_stages(diff_text):
    """Which stages a change to a unit template invalidates, read from the changed lines: lifecycle directives -> V0, V2-V4; resource limits also V1; comments nothing; anything else (sandbox, user, paths,
    ExecStart) -> every stage. -> (stages, why)"""
    changed = [l[1:].strip() for l in diff_text.splitlines() if l[:1] in "+-" and l[:3] not in ("+++", "---") and l[1:].strip()]
    changed = [l for l in changed if not l.startswith("#")]
    stages, why = set(), []
    for l in changed:
        key = l.split("=", 1)[0].strip()
        if key in UNIT_LIFECYCLE:
            stages |= {"V0", "V2", "V3", "V4"}
            why.append(key + " (lifecycle)")
        elif key in UNIT_RESOURCE:
            stages |= {"V0", "V1", "V2", "V3", "V4"}
            why.append(key + " (resource limit)")
        else:
            return set(STAGES), [key + " (not analysed: every stage)"]
    return stages, why


def classify(path, diff=None):
    """-> (kind, stages): kind is 'docs', 'none', 'harness' or 'code'; stages the set of stages a change invalidates. `diff`: the file's diff, used for unit templates."""
    return classify_why(path, diff)[:2]


def classify_why(path, diff=None):
    """-> (kind, stages, why)."""
    if path.startswith("docs/"):
        return "docs", set(), "documentation"
    if path.startswith("tools/vtest/"):
        name = path[len("tools/vtest/"):]
        if name in HARNESS_STAGE:
            return "harness", set(HARNESS_STAGE[name]), "that stage's own steps"
        if name in HARNESS_JUDGES:
            return "harness", set(HARNESS_JUDGES[name]), "judgments used only by %s" % ", ".join(sorted(HARNESS_JUDGES[name]))
        if name in HARNESS_NONE:
            return "none", set(), "the driver / go-no-go gate: decides whether to run, measures nothing"
        return "harness", set(STAGES), "shared harness machinery (runs every stage)"
    if path == "tests/test_season.py" or path.startswith("tests/fixtures/"):
        return "code", set(STAGES), "the fixture generator: what the daemon runs on"
    if path.startswith("tests/"):
        return "none", set(), "tests"
    if path in CODE_BEHAVIOUR:
        return "code", set(CODE_BEHAVIOUR[path][0]), CODE_BEHAVIOUR[path][1]
    if path in DAEMON_UNITS and diff is not None:
        st, why = unit_diff_stages(diff)
        return "code", st, "unit template: " + ", ".join(why or ["comments only"])
    if path.startswith(("src/", "tools/", "deploy/")):
        return "code", set(STAGES), "not analysed: every stage"
    return "none", set(), "not part of the code under test"


def changed_files(repo, a, b="HEAD"):
    rc, out, err = git(repo, "diff", "--name-only", a, b)
    if rc:
        raise SystemExit("git diff %s %s failed: %s" % (a, b, err))
    return [x for x in out.splitlines() if x]


def affected(repo, recorded_rev, current="HEAD"):
    """-> {"changed": [(path, kind)], "repeat": [stages], "code_changed": bool, "why": {path: [stages, reason]}}. Documentation-only changes repeat nothing; a stage is repeated only if a changed file is one it depends on."""
    rows, stages, why = [], set(), {}
    for p in changed_files(repo, recorded_rev, current):
        diff = None
        if p in DAEMON_UNITS:
            diff = git(repo, "diff", recorded_rev, current, "--", p)[1]
        kind, st, reason = classify_why(p, diff)
        rows.append((p, kind))
        stages |= st
        if st:
            why[p] = [sorted(st), reason]
    return {"changed": rows, "repeat": [s for s in STAGES if s in stages], "code_changed": any(k == "code" for _, k in rows), "why": why}


def pin_check(repo=REPO, pin=None):
    """-> (ok, [messages]). The pinned commit exists, is an ancestor of HEAD, and HEAD differs from it only in ways that cannot change what is under test."""
    pin = pin or read_pin(repo)
    msgs = []
    rc, _, _ = git(repo, "cat-file", "-e", pin + "^{commit}")
    if rc:
        return False, ["pinned commit %s is not in this repository" % pin]
    rc, _, _ = git(repo, "merge-base", "--is-ancestor", pin, "HEAD")
    if rc:
        return False, ["pinned commit %s is not an ancestor of HEAD" % pin]
    a = affected(repo, pin)
    code = [p for p, k in a["changed"] if k == "code"]
    ok = True
    if code:
        ok = False
        msgs.append("code under test differs from the pin in: %s (the harness installs the PINNED tree, but a changed pin line or changed code means this is not the approved candidate)" % ", ".join(code[:8]))
    rc, out, _ = git(repo, "status", "--porcelain")
    dirty = [l for l in out.splitlines() if l.strip()]
    if dirty:
        ok = False
        msgs.append("working tree is not clean (%d paths): commit or discard first so the harness revision is a real commit" % len(dirty))
    return ok, msgs or ["HEAD differs from the pin only in docs / harness / tests: the code under test is the pinned tree"]


def harness_rev(repo=REPO):
    rc, out, _ = git(repo, "rev-parse", "HEAD")
    return out if not rc else "unknown"


# ---------------------------------------------------------------------------------------------------------------------------------- results
class Recorder:
    """The checks of one stage, written as they are made. A stage's result is `pass` only if every required check passed."""
    def __init__(self, stage, outdir, meta, echo=print):
        self.stage, self.outdir, self.meta, self.echo = stage, outdir, dict(meta), echo
        self.checks, self.started = [], time.time()
        os.makedirs(outdir, exist_ok=True)

    def check(self, cid, status, detail, data=None, required=True):
        assert status in STATUSES, status
        self.checks.append({"id": cid, "status": status, "detail": detail, "data": data, "required": required, "t": round(time.time(), 1)})
        self.echo("  [%s] %s: %s" % (status.upper(), cid, detail))
        self._flush()
        return status

    def add_all(self, rows):
        for r in rows:
            self.check(*r)

    def failed(self):
        return [c for c in self.checks if c["status"] in ("fail", "invalid")]

    def stop_if_failed(self):
        bad = self.failed()
        if bad:
            raise StageStop("%s: %s" % (bad[0]["id"], bad[0]["detail"]))

    def overall(self):
        st = [c for c in self.checks if c["required"] or c["status"] in ("fail", "invalid")]
        if any(c["status"] == "fail" for c in st):
            return "fail"
        if any(c["status"] == "invalid" for c in st):
            return "invalid"
        if any(c["status"] in ("review", "not_run") for c in st):
            return "review"
        return "pass" if st else "not_run"

    def _flush(self):
        with open(os.path.join(self.outdir, "result.json"), "w") as f:
            json.dump(dict(self.meta, stage=self.stage, checks=self.checks, overall=self.overall(), started=self.started, finished=None), f, indent=1, sort_keys=True)

    def finish(self, note=None):
        res = dict(self.meta, stage=self.stage, checks=self.checks, overall=self.overall(), started=self.started, finished=time.time(), note=note)
        with open(os.path.join(self.outdir, "result.json"), "w") as f:
            json.dump(res, f, indent=1, sort_keys=True)
        write_manifest(self.outdir)
        return res


def write_manifest(outdir):
    """sha256 of every file in the stage's evidence folder (the manifest itself excluded): evidence is only ever added to."""
    lines = []
    for d, _, names in sorted(os.walk(outdir)):
        for n in sorted(names):
            if n == "MANIFEST.sha256":
                continue
            p = os.path.join(d, n)
            lines.append("%s  %s" % (sha256_file(p), os.path.relpath(p, outdir)))
    with open(os.path.join(outdir, "MANIFEST.sha256"), "w") as f:
        f.write("\n".join(lines) + "\n")


def jsonl_read(path):
    out = []
    try:
        for line in open(path):
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass                                   # a torn last line (the writer was killed): skip it
    except OSError:
        pass
    return out
