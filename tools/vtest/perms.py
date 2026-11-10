"""Who the services run as, what they may touch, and what systemd actually applied. Pure judgments over observed values, plus the few observers and the installer's layout step that read or set them.

The intended production shape (deploy/supervisor/*.service, docs/INTEGRATION_CANDIDATE_2026-11-07.md): the units run as an unprivileged user with ProtectSystem=strict, ProtectHome, PrivateTmp,
NoNewPrivileges; the daemon may write only <root>/store, the public folder and the guard folder; the monitor only the guard folder; the code is read-only; the daemon's user owns the public folder
and the guard. The templates run the daemon and the monitor as ONE user (`dflv2`); the harness mirrors that with one dedicated user (SERVICE_USER, created by the operator, never by the harness).
The privileged parts of a run (workload injection into the daemon's cgroup, kernel-log collection, `chattr`, unit control) stay with the harness, which runs as root and is never a child of a service.
A row is (check id, status, detail, data[, required]).
"""
import os
import re

# path -> (owner, mode); owner 'svc' or 'root'. `mode` is exact for directories the service needs to reach or own; 'noworld' means: root-owned and not writable by group or other.
# The root folder is root-owned (the builder's, in production): the service user owns only its private store inside it.
def layout_paths(tree):
    """The folders the layout and the installer's ownership step name, from the harness's own constants. One definition for the installer and for V0."""
    from common import BASE, ETC, EVID, GUARD, LOADDIR, PROBE, PUBLIC, ROOT_DIR, STATE, WEB_DIR
    return {"BASE": BASE, "ROOT_DIR": ROOT_DIR, "WEB_DIR": WEB_DIR, "PUBLIC": PUBLIC, "GUARD": GUARD, "TREE": tree.rstrip("/"), "ETC": ETC, "EVID": EVID, "STATE": STATE, "LOADDIR": LOADDIR, "PROBE": PROBE}


def layout(paths):
    return [(paths["BASE"], "root", 0o755), (paths["ROOT_DIR"], "root", 0o755), (paths["ROOT_DIR"] + "/store", "svc", 0o700), (paths["WEB_DIR"], "root", 0o755), (paths["PUBLIC"], "svc", 0o755),
            (paths["GUARD"], "svc", 0o700), (paths["TREE"], "root", "noworld"), (paths["ETC"] + "/broadcast.env", "root", 0o644),
            (paths["EVID"], "root", "noworld"), (paths["STATE"], "root", "noworld"), (paths["LOADDIR"], "root", "noworld"), (paths["PROBE"], "root", "noworld")]


def judge_service_user(facts):
    """facts: {name, uid, gid, shell, groups, exists, home_exists}. Dedicated, unprivileged, no login, no extra groups."""
    rows = []
    if not facts.get("exists"):
        return [("V0.user.exists", "fail", "the service user %r does not exist: the operator creates it first (README, setup)" % facts.get("name"), facts)]
    uid, gid = facts.get("uid"), facts.get("gid")
    rows.append(("V0.user.unprivileged", "pass" if uid not in (None, 0) and gid not in (None, 0) and 100 <= uid < 65534 else "fail",
                 "uid %s gid %s (a dedicated system uid: not root, not nobody)" % (uid, gid), facts))
    shell = facts.get("shell") or ""
    rows.append(("V0.user.no_login", "pass" if shell.endswith(("nologin", "false")) else "fail", "login shell %s" % (shell or "none"), None))
    groups = sorted(set(facts.get("groups") or []) - {facts.get("name")})
    risky = sorted(set(groups) & {"root", "sudo", "wheel", "adm", "systemd-journal", "disk", "docker", "lxd", "shadow"})
    rows.append(("V0.user.no_extra_groups", "pass" if not groups else "fail", "supplementary groups: %s%s" % (groups or "none", ("; privileged: %s" % risky) if risky else ""), {"groups": groups}))
    return rows


def judge_layout(observed, expected, user):
    """observed: {path: {"owner": name, "mode": int} or None}. expected: layout(); `user`: the service user. -> rows, one per path (a missing path fails)."""
    rows = []
    for path, who, mode in expected:
        o = observed.get(path)
        if o is None:
            rows.append(("V0.layout:" + path, "fail", "does not exist", None))
            continue
        want_owner = "root" if who == "root" else user
        problems = []
        if o["owner"] != want_owner:
            problems.append("owner %s, expected %s" % (o["owner"], want_owner))
        if mode == "noworld":
            if o["mode"] & 0o022:
                problems.append("mode %o is writable by group or other" % o["mode"])
        elif (o["mode"] & 0o7777) != mode:
            problems.append("mode %o, expected %o" % (o["mode"] & 0o7777, mode))
        rows.append(("V0.layout:" + path, "fail" if problems else "pass", "; ".join(problems) or "owner %s mode %o" % (o["owner"], o["mode"] & 0o7777), o))
    return rows


def judge_access(probes, must_write, must_not_write):
    """probes: {path: 'ok' | errno name | other text}, from creating and removing a file AS the service user. The folders it must write, it can; every other, it cannot (EACCES / EROFS / EPERM)."""
    rows = []
    for p in must_write:
        r = probes.get(p)
        rows.append(("V0.access.can_write:" + p, "pass" if r == "ok" else "fail", "as the service user: %s" % r, None))
    for p in must_not_write:
        r = probes.get(p)
        rows.append(("V0.access.cannot_write:" + p, "pass" if r in ("EACCES", "EROFS", "EPERM") else "fail", "as the service user: %s%s" % (r, " (it could WRITE here)" if r == "ok" else ""), None))
    return rows


def parse_proc_status(text):
    d = {}
    for l in (text or "").splitlines():
        k, _, v = l.partition(":")
        d[k.strip()] = v.strip()
    uid = [int(x) for x in (d.get("Uid") or "").split()] if d.get("Uid") else []
    gid = [int(x) for x in (d.get("Gid") or "").split()] if d.get("Gid") else []
    return {"uid": uid, "gid": gid, "cap_eff": int(d["CapEff"], 16) if d.get("CapEff") else None, "cap_bnd": int(d["CapBnd"], 16) if d.get("CapBnd") else None,
            "no_new_privs": int(d["NoNewPrivs"]) if d.get("NoNewPrivs", "").isdigit() else None, "groups": d.get("Groups", "").split()}


def judge_process(unit, status, uid, gid):
    """The running main process as the kernel sees it: all four uids and gids the service user's, no capabilities, no_new_privs, no supplementary groups."""
    p = status
    rows = []
    rows.append(("V0.process.%s.uid" % unit, "pass" if p["uid"] and set(p["uid"]) == {uid} and p["gid"] and set(p["gid"]) == {gid} else "fail", "real/effective/saved/fs uid %s, gid %s (expected only %s / %s)" % (p["uid"], p["gid"], uid, gid), p))
    rows.append(("V0.process.%s.no_capabilities" % unit, "pass" if p["cap_eff"] == 0 else "fail", "effective capabilities %s" % (("%x" % p["cap_eff"]) if p["cap_eff"] is not None else "unknown"), None))
    rows.append(("V0.process.%s.no_new_privileges" % unit, "pass" if p["no_new_privs"] == 1 else "fail", "NoNewPrivs=%s" % p["no_new_privs"], None))
    rows.append(("V0.process.%s.no_supplementary_groups" % unit, "pass" if not p["groups"] else "fail", "supplementary groups: %s" % (p["groups"] or "none"), None))
    return rows


SANDBOX_SCALARS = ("User", "Group", "ProtectSystem", "ProtectHome", "NoNewPrivileges", "PrivateTmp", "OOMPolicy", "UMask", "TasksMax", "MemoryMax")
SANDBOX_PATHS = ("ReadWritePaths", "ReadOnlyPaths")
SANDBOX_REQUIRED = ("ProtectSystem", "ProtectHome", "NoNewPrivileges", "PrivateTmp", "ReadWritePaths", "User", "Group")


def _norm(v):
    v = (v or "").strip()
    return {"true": "yes", "1": "yes", "on": "yes", "false": "no", "0": "no", "off": "no"}.get(v.lower(), v)


def unit_file_values(text):
    d = {}
    for l in (text or "").splitlines():
        m = re.match(r"([A-Za-z]+)=(.*)$", l)
        if m:
            d.setdefault(m.group(1), []).append(m.group(2))
    return d


def sandbox_declared(template, rendered):
    """The production template's sandbox directives must all survive rendering (paths aside): same value for every scalar, the same NUMBER of paths for every path list; and the required ones must be present.
    -> list of problems (empty if fine)."""
    t, r = unit_file_values(template), unit_file_values(rendered)
    problems = []
    for k in SANDBOX_REQUIRED:
        if k not in r:
            problems.append("%s is missing from the rendered unit" % k)
    for k in SANDBOX_SCALARS:
        if k in t and k not in ("User", "Group"):
            if [_norm(x) for x in t[k]] != [_norm(x) for x in r.get(k, [])]:
                problems.append("%s: template %s, rendered %s" % (k, t[k], r.get(k)))
    for k in SANDBOX_PATHS:
        if k in t and len(" ".join(t[k]).split()) != len(" ".join(r.get(k, [])).split()):
            problems.append("%s lists %d paths in the template and %d rendered" % (k, len(" ".join(t[k]).split()), len(" ".join(r.get(k, [])).split())))
    return problems


def parse_show(text):
    d = {}
    for l in (text or "").splitlines():
        k, _, v = l.partition("=")
        if k:
            d[k] = v
    return d


def judge_applied_sandbox(unit, rendered_text, shown, user):
    """What `systemctl show` says systemd applied, against what the installed unit file says (and against the service user for User=/Group=)."""
    want = unit_file_values(rendered_text)
    rows = []
    bad = []
    for k in SANDBOX_SCALARS:
        if k in ("User", "Group"):
            if _norm(shown.get(k)) != user:
                bad.append("%s applied %r, expected %s" % (k, shown.get(k), user))
        elif k in want and _norm(shown.get(k)) != _norm(want[k][-1]):
            if k in ("MemoryMax",):                    # systemd prints bytes
                continue
            bad.append("%s applied %r, unit file %r" % (k, shown.get(k), want[k][-1]))
    for k in SANDBOX_PATHS:
        w = set(" ".join(want.get(k, [])).split())
        a = {x.lstrip("-") for x in (shown.get(k) or "").split()}
        if w != a:
            bad.append("%s applied %s, unit file %s" % (k, sorted(a), sorted(w)))
    rows.append(("V0.sandbox.%s.applied" % unit, "fail" if bad else "pass", "; ".join(bad) or "systemd applied the unit file's sandbox (User, Group, ProtectSystem, ProtectHome, NoNewPrivileges, PrivateTmp, OOMPolicy, UMask, TasksMax, ReadWritePaths, ReadOnlyPaths)", {"shown": {k: shown.get(k) for k in SANDBOX_SCALARS + SANDBOX_PATHS}}))
    return rows


def judge_kernel_log(restrict, service_can_read, harness_lines):
    """The kernel log, two readers. The SERVICE USER probably cannot read it (production: the incident record says the victim is unknown: allowed). The HARNESS, as root, collects it independently and
    must be able to: it is the test's evidence of the victim. -> rows."""
    rows = [("V0.kernel_log.harness_collects", "pass" if harness_lines else "fail",
             "the harness (root) reads the kernel log independently of the service: %d lines" % (harness_lines or 0) if harness_lines else
             "the harness cannot read the kernel log: the OOM victim could not be identified for V2, so V2 would be incomplete", {"lines": harness_lines}),
            ("V0.kernel_log.service_user_access", "info", ("the service user CAN read the kernel log (dmesg_restrict=%s): the production incident record may identify the victim itself" if service_can_read else
                                                            "the service user cannot read the kernel log (dmesg_restrict=%s): the production incident record is expected to say the victim is UNKNOWN; the test's victim comes from the harness") % restrict,
             {"dmesg_restrict": restrict, "service_can_read": service_can_read}, False)]
    return rows


def judge_marker_before_start(marker, invocation_id, main_start_wall, pre_exit_status, tol=1.0):
    """The run marker is written by ExecStartPre= before the daemon's main process starts, for THIS invocation of the unit.
    marker: lifecycle.marker_view dict; `invocation_id`: the unit's InvocationID; `main_start_wall`: when the main process started (wall clock)."""
    rows = []
    rows.append(("V0.marker.present_for_this_run", "pass" if marker.get("state") == "open" and invocation_id and marker.get("invocation_id") == invocation_id else "fail",
                 "marker %s, invocation %s; the unit's InvocationID %s" % (marker.get("state"), marker.get("invocation_id"), invocation_id), {"marker": marker, "invocation_id": invocation_id}))
    if main_start_wall is None or marker.get("opened_at") is None:
        rows.append(("V0.marker.written_before_the_daemon_started", "not_run", "INCOMPLETE: no start time for the daemon's main process or no open time in the marker", None))
    else:
        lead = main_start_wall - marker["opened_at"]
        rows.append(("V0.marker.written_before_the_daemon_started", "pass" if lead >= -tol else "fail", "the marker was opened %.1f s before the daemon's main process started" % lead if lead >= -tol else
                     "the marker was opened %.1f s AFTER the daemon's main process started" % -lead, {"opened_at": marker.get("opened_at"), "main_start_wall": main_start_wall}))
    rows.append(("V0.marker.start_check_exited_clean", "pass" if pre_exit_status == 0 else "fail" if pre_exit_status is not None else "not_run", "ExecStartPre= exit status %s" % pre_exit_status, None))
    return rows


def parse_exec_pre_status(show_text):
    """The last ExecStartPre= run's exit status from `systemctl show -p ExecStartPre`, or None."""
    m = re.search(r"ExecStartPre=.*?status=(\d+)", show_text or "")
    return int(m.group(1)) if m else None


# ------------------------------------------------------------------------------------------------------------------------------------------- observers and the layout step
def observe(paths, stat=os.stat, owner_of=None):
    """-> {path: {"owner": user name, "mode": int}} (None for a missing path). `owner_of(uid)` -> name."""
    import pwd
    owner_of = owner_of or (lambda uid: pwd.getpwuid(uid).pw_name if uid is not None else None)
    out = {}
    for p in paths:
        try:
            st = stat(p)
        except OSError:
            out[p] = None
            continue
        out[p] = {"owner": owner_of(st.st_uid), "mode": st.st_mode & 0o7777}
    return out


def tree_writable_by_others(root, limit=20000):
    """Paths in a tree that are group- or other-writable (the code must be read-only to the service). -> list (capped)."""
    bad = []
    n = 0
    for d, dirs, files in os.walk(root):
        for name in dirs + files:
            n += 1
            if n > limit:
                return bad
            p = os.path.join(d, name)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if not os.path.islink(p) and st.st_mode & 0o022:
                bad.append(p)
    return bad


def apply_layout(user, paths, allowed, chown=os.chown, chmod=os.chmod, getpw=None):
    """The installer's step: ownership and modes of the layout above. Every path must satisfy `allowed` (the harness's own folders). Does not create users. -> list of what was done."""
    import pwd
    pw = (getpw or pwd.getpwnam)(user)
    done = []

    def own(path, uid, gid, mode=None, recursive=False):
        if not allowed(path):
            raise PermissionError("refusing to change %s" % path)
        targets = [path]
        if recursive:
            for d, dirs, files in os.walk(path):
                targets += [os.path.join(d, n) for n in dirs + files]
        for t in targets:
            if os.path.islink(t):
                continue
            chown(t, uid, gid)
        if mode is not None:
            chmod(path, mode)
        done.append((path, uid, oct(mode) if mode is not None else None, recursive))

    def readonly_code(path):
        if not allowed(path):
            raise PermissionError("refusing to change %s" % path)
        for d, dirs, files in os.walk(path):
            for n in dirs + files + ["."]:
                t = os.path.join(d, n)
                if os.path.islink(t):
                    continue
                st = os.stat(t)
                chown(t, 0, 0)
                chmod(t, (st.st_mode & 0o7777 | 0o444 | (0o111 if (os.path.isdir(t) or st.st_mode & 0o100) else 0)) & ~0o022)
        done.append((path, 0, "go-w,a+rX", True))

    own(paths["BASE"], 0, 0, 0o755)
    own(paths["ROOT_DIR"], 0, 0, 0o755, recursive=True)                   # the builder's (root's) folder: read-only to the service
    store = paths["ROOT_DIR"] + "/store"
    if os.path.isdir(store):
        own(store, pw.pw_uid, pw.pw_gid, 0o700, recursive=True)           # its private store: the daemon writes it
    own(paths["WEB_DIR"], 0, 0, 0o755)
    own(paths["PUBLIC"], pw.pw_uid, pw.pw_gid, 0o755, recursive=True)
    own(paths["GUARD"], pw.pw_uid, pw.pw_gid, 0o700, recursive=True)
    readonly_code(paths["TREE"])
    envfile = paths["ETC"] + "/broadcast.env"
    if os.path.isfile(envfile):
        if not allowed(envfile):
            raise PermissionError("refusing to change %s" % envfile)
        chown(envfile, 0, 0)
        chmod(envfile, 0o644)
        done.append((envfile, 0, "0o644", False))
    for k in ("EVID", "STATE", "LOADDIR", "PROBE"):
        if os.path.isdir(paths[k]):
            chmod(paths[k], os.stat(paths[k]).st_mode & 0o7777 & ~0o022)
    return done
