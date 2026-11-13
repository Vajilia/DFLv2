#!/usr/bin/env python3
"""Release configuration of the alert package: render the units and drop-ins from templates, and keep a manifest of exactly what is released.

The alert package is not part of the candidate, but it is part of what runs in production, so it is treated as release configuration: the candidate's own unit files stay byte-identical to the pin,
and the files beside them (the alert unit, the poll service and timer, one drop-in per protected unit, the tool, the templates) are listed with SHA-256 in RELEASE_MANIFEST.json together with the
hash of every file they render to, for both the validation names and the production names. A change to any of them changes the manifest, and the pin gate says which validation stages it repeats.

  render   --kind vtest|production [--out DIR]     print (or write under DIR) the files a host would receive, with their SHA-256
  manifest [--write]                               verify RELEASE_MANIFEST.json against the folder (non-zero exit on any difference); --write regenerates it

Rendering is deterministic and reads nothing from the host. Nothing here installs, enables or starts anything: the harness's `install-alerts` does that on a disposable machine, and a person does it in production.
"""
import argparse
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = "RELEASE_MANIFEST.json"
UNIT_DIR = "/etc/systemd/system"
KINDS = ("vtest", "production")


def default_params(kind):
    """The names for a validation machine (every name starts with dfl-vtest-, which the harness's guard requires) or for production (the candidate's real unit names)."""
    if kind == "vtest":
        return {"kind": "vtest", "prefix": "dfl-vtest-", "daemon_unit": "dfl-vtest-daemon.service", "monitor_unit": "dfl-vtest-monitor.service",
                "state_name": "dfl-vtest-alerts", "env_file": "/etc/dfl-vtest/alert.env", "bin_dir": "/opt/dfl-vtest-alert"}
    if kind == "production":
        return {"kind": "production", "prefix": "dfl-", "daemon_unit": "dfl-broadcast-daemon.service", "monitor_unit": "dfl-broadcast-monitor.service",
                "state_name": "dfl-alerts", "env_file": "/etc/dflv2/alert.env", "bin_dir": "/opt/dflv2-alert"}
    raise ValueError("kind must be one of %s" % (KINDS,))


def _substitute(text, p):
    table = {"@PREFIX@": p["prefix"], "@DAEMON_UNIT@": p["daemon_unit"], "@MONITOR_UNIT@": p["monitor_unit"], "@STATE_NAME@": p["state_name"],
             "@ENV_FILE@": p["env_file"], "@BIN_DIR@": p["bin_dir"]}
    for k, v in table.items():
        text = text.replace(k, v)
    left = re.findall(r"@[A-Z_]+@", text)
    if left:
        raise ValueError("unsubstituted placeholder(s): %s" % ", ".join(sorted(set(left))))
    return text


def _template(name, templates_dir):
    with open(os.path.join(templates_dir, name), encoding="utf-8") as f:
        return f.read()


def render(p, templates_dir=None):
    """-> {absolute install path: text}. Five files: the alert unit, the poll service, the poll timer, and one drop-in for each protected unit."""
    td = templates_dir or os.path.join(HERE, "templates")
    pre = p["prefix"]
    files = {
        "%s/%salert@.service" % (UNIT_DIR, pre): _substitute(_template("alert@.service.in", td), p),
        "%s/%salert-poll.service" % (UNIT_DIR, pre): _substitute(_template("alert-poll.service.in", td), p),
        "%s/%salert-poll.timer" % (UNIT_DIR, pre): _substitute(_template("alert-poll.timer.in", td), p),
    }
    dropin = _template("protected-unit-dropin.conf.in", td)
    for unit in (p["daemon_unit"], p["monitor_unit"]):
        files["%s/%s.d/50-dfl-alert.conf" % (UNIT_DIR, unit)] = _substitute(dropin, p)
    return files


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def rendered_hashes(p, templates_dir=None):
    return {path: sha256_text(text) for path, text in sorted(render(p, templates_dir).items())}


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def package_files(folder):
    """Relative names (posix) of every file the package consists of: everything under the folder except the manifest, caches and compiled files."""
    out = []
    for root, dirs, names in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for n in sorted(names):
            if n == MANIFEST or n.endswith((".pyc", ".pyo")):
                continue
            out.append(os.path.relpath(os.path.join(root, n), folder).replace(os.sep, "/"))
    return sorted(out)


def build_manifest(folder):
    td = os.path.join(folder, "templates")
    return {"schema": 1,
            "note": "Release configuration of the alert package. Regenerate with: python3 alerts/release_config.py manifest --write",
            "files": {n: _sha256_file(os.path.join(folder, n)) for n in package_files(folder)},
            "rendered": {k: rendered_hashes(default_params(k), td) for k in KINDS}}


def verify_manifest(folder):
    """-> list of problems (empty = the folder is exactly what the manifest says)."""
    folder = str(folder)
    path = os.path.join(folder, MANIFEST)
    try:
        with open(path, encoding="utf-8") as f:
            recorded = json.load(f)
    except (OSError, ValueError) as e:
        return ["%s unreadable: %s" % (MANIFEST, e)]
    problems = []
    try:
        now = build_manifest(folder)
    except (OSError, ValueError) as e:
        return ["cannot build the current state: %s" % e]
    rec_files, now_files = recorded.get("files", {}), now["files"]
    for n in sorted(set(rec_files) | set(now_files)):
        if n not in now_files:
            problems.append("missing: %s" % n)
        elif n not in rec_files:
            problems.append("not in the manifest: %s" % n)
        elif rec_files[n] != now_files[n]:
            problems.append("changed: %s" % n)
    for k in KINDS:
        a, b = recorded.get("rendered", {}).get(k, {}), now["rendered"][k]
        for n in sorted(set(a) | set(b)):
            if a.get(n) != b.get(n):
                problems.append("rendered %s differs: %s" % (k, n))
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(prog="release_config", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render")
    r.add_argument("--kind", choices=KINDS, required=True)
    r.add_argument("--out")
    m = sub.add_parser("manifest")
    m.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "render":
        files = render(default_params(a.kind))
        for path, text in sorted(files.items()):
            print("%s  %s" % (sha256_text(text), path))
            if a.out:
                dest = os.path.join(a.out, path.lstrip("/"))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "w", encoding="utf-8") as f:
                    f.write(text)
        return 0
    if a.write:
        with open(os.path.join(HERE, MANIFEST), "w", encoding="utf-8") as f:
            json.dump(build_manifest(HERE), f, indent=2, sort_keys=True)
            f.write("\n")
        print("wrote %s" % MANIFEST)
        return 0
    problems = verify_manifest(HERE)
    for pr in problems:
        print("MANIFEST: " + pr)
    print("manifest %s" % ("matches" if not problems else "DIFFERS"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
