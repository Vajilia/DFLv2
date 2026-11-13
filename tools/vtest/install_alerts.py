"""Install the alert package (alerts/) on a disposable validation machine, and record exactly what was installed and what systemd then reports.

The package is release configuration (docs/ALERT_DESIGN_2026-11-12.md): the candidate's own unit files are not touched; one drop-in per protected unit, the alert unit, the poll service and timer,
and the tool are installed beside them. Before anything is written the folder is checked against alerts/RELEASE_MANIFEST.json (every file's SHA-256, and the hash of every rendered file for
the validation names); a package that differs from its manifest is refused. Afterwards the record (STATE/alerts_install.json) holds the git revisions (the harness revision, the tree ids of alerts/
and of the candidate's unit folder), the manifest and rendered hashes, and, read back from systemd, the EFFECTIVE settings of the daemon, the monitor and the alert units, with `systemctl cat`
for each: what is in force, not what the templates say.

The delivery channel here is a file inside the alert folder (ALERT_SINK): no credential exists, nothing leaves the machine. The mode file ALERT_MODE ("ok") is how V5 makes a hand-off fail.
Nothing is enabled; the poll timer is started (it is what V5 and the repeats of V3/V4 run beside).
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common as C                                                    # noqa: E402

ALERTS_DIR = os.path.join(C.REPO, "alerts")
sys.path.insert(0, ALERTS_DIR)
import release_config as RC                                           # noqa: E402



def record_path():
    return C.STATE + "/alerts_install.json"


EFFECTIVE_PROPS = ("Id", "LoadState", "ActiveState", "SubState", "FragmentPath", "DropInPaths", "OnFailure", "OnFailureJobMode", "User", "Restart", "RestartUSec", "RestartPreventExitStatus",
                   "StartLimitIntervalUSec", "StartLimitBurst", "StartLimitAction", "TimeoutStartUSec", "TimeoutStopUSec", "MemoryMax", "MemoryHigh", "MemorySwapMax", "TasksMax", "OOMPolicy",
                   "ProtectSystem", "StateDirectory", "EnvironmentFiles", "ExecStart")


def env_file_text():
    """The environment file of the validation machine: the file channel, the monitor's heartbeat, a host label. Nothing secret (there is nothing to be secret)."""
    deliver = "/bin/sh -c 'grep -qx ok %s && cat >> %s'" % (C.ALERT_MODE, C.ALERT_SINK)
    return ("# vtest: the delivery channel is a FILE inside the alert folder; nothing leaves the machine and no credential exists.\n"
            "DFL_ALERT_DELIVER_CMD=\"%s\"\nDFL_ALERT_HEARTBEAT=%s/heartbeat\nDFL_ALERT_HOST_LABEL=vtest\n" % (deliver, C.GUARD))


def git_ids(repo):
    out = {}
    for label, ref in (("harness_revision", "HEAD"), ("alerts_tree", "HEAD:alerts"), ("candidate_unit_folder_tree", "HEAD:deploy/supervisor")):
        rc, o, _ = C.git(repo, "rev-parse", ref)
        out[label] = o if rc == 0 else None
    return out


def read_effective(env, units):
    """-> {unit: {"show": {prop: value}, "cat": text}}, read-only (`systemctl show`, `systemctl cat`)."""
    out = {}
    for u in units:
        rc, so, se = env.sc("show", u, *["-p" + p for p in EFFECTIVE_PROPS])
        show = {}
        for line in (so or "").splitlines():
            k, _, v = line.partition("=")
            show[k] = v
        rc2, cat, ce = env.sc("cat", u)
        out[u] = {"show": show, "cat": cat if rc2 == 0 else "UNAVAILABLE: " + (ce or "rc %s" % rc2), "show_rc": rc}
    return out


def install_alerts(env, repo, echo=print):
    """-> the record (also written to RECORD). Raises C.Unsafe when the package is not what its manifest says, or the validation machine is not installed."""
    inst = json.loads(env.read(C.STATE + "/install.json") or "{}")
    if not inst.get("pin"):
        raise C.Unsafe("run `vtest.py install` first: the candidate's units are not installed (no install record)")
    alerts = os.path.join(repo, "alerts")
    problems = RC.verify_manifest(alerts)
    if problems:
        raise C.Unsafe("the alert package differs from alerts/RELEASE_MANIFEST.json (%s); nothing was installed" % "; ".join(problems[:5]))
    manifest = json.load(open(os.path.join(alerts, RC.MANIFEST)))
    params = RC.default_params("vtest")
    rendered = RC.render(params, os.path.join(alerts, "templates"))
    if {p: RC.sha256_text(t) for p, t in rendered.items()} != manifest["rendered"]["vtest"]:
        raise C.Unsafe("the rendered units differ from the manifest; nothing was installed")
    tool = open(os.path.join(alerts, "dfl_alert.py")).read()
    env.write(C.ALERT_BIN + "/dfl_alert.py", tool, 0o644)
    env.write(C.ETC + "/alert.env", env_file_text(), 0o600)
    os.makedirs(C.ALERT_STATE, mode=0o700, exist_ok=True)
    env.write(C.ALERT_MODE, "ok\n", 0o600)
    dests = {}
    for path, text in sorted(rendered.items()):
        dest = C.UNIT_DIR + path[len(RC.UNIT_DIR):]           # the same layout, under the harness's own unit folder
        env.write(dest, text)
        dests[dest] = path
    env.sc("daemon-reload")
    timer = params["prefix"] + "alert-poll.timer"
    env.sc("start", timer)
    units = [C.UNIT["daemon"], C.UNIT["monitor"], params["prefix"] + "alert@probe.service", params["prefix"] + "alert-poll.service", timer]
    eff = read_effective(env, units)
    record = {"installed_at": time.time(), "git": git_ids(repo), "pin": inst.get("pin"), "package_manifest_sha256": RC.sha256_text(open(os.path.join(alerts, RC.MANIFEST)).read()),
              "package_files": manifest["files"], "rendered": manifest["rendered"]["vtest"], "tool_installed_sha256": RC.sha256_text(tool), "effective": eff,
              "channel": "file sink %s (no credential, nothing leaves the machine)" % C.ALERT_SINK}
    env.write(record_path(), json.dumps(record, indent=1, sort_keys=True, default=str))
    for dest in sorted(dests):
        echo("installed %s" % dest)
    echo("poll timer started: %s; record: %s" % (timer, record_path()))
    return record
