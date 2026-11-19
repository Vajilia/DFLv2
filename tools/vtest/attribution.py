"""V5 attribution evidence: what the harness preserves BEFORE recovery changes the guard folder, and the rules that say it is complete.

Why: the first real V5 failure ("the latch changed: None -> <hash>") could be explained only from the daemon's journal, because the latch file, the trips line, the heartbeat time, timestamped alert/poll
journals and the alert units' effective sandbox properties were not kept; the stage recovery had already moved the latch. A latch that appears during a hold is judged only against evidence captured
at the end of the hold and saved at once (`V5/attribution_<scenario>.json`), together with `V5/alert_sandbox.json` and the whole-run `V5/alert_journal_timestamped.txt`.

Pure functions, no machine access: the stage uses them to FAIL on a gap, the evidence manifest uses them to report MISSING, and the judge uses `preserved_matches` to refuse to accept a latch the
preserved evidence does not reproduce. A gap can never turn into a pass: it fails the scenario row (required), makes the archive incomplete, and removes the only basis for accepting an independent latch.
"""
import hashlib
import json
import re

SCHEMA = 1
PRESENT, ABSENT, UNREADABLE, EMPTY = "present", "absent", "unreadable", "empty"
# The scenarios that hold after an alert, the timeline event that says the hold began, and the file that must then exist.
SCENARIOS = {"monitor_stopped": "attribution_monitor_stopped.json", "daemon_start_limit": "attribution_daemon_start_limit.json",
             "monitor_exit2": "attribution_monitor_exit2.json", "delivery_failure": "attribution_delivery_failure.json"}
SANDBOX_FILE, JOURNAL_FILE = "alert_sandbox.json", "alert_journal_timestamped.txt"
SANDBOX_PROPS = ("Id", "FragmentPath", "DropInPaths", "User", "Group", "DynamicUser", "ProtectSystem", "ProtectHome", "ReadWritePaths", "ReadOnlyPaths", "InaccessiblePaths", "NoNewPrivileges",
                 "PrivateTmp", "StateDirectory", "CapabilityBoundingSet", "RestrictAddressFamilies", "ExecStart")
ISO_LINE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d")
KEY_JOURNALS = ("alert", "poll", "daemon", "monitor")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def journal_lines(text):
    """The content lines of a journal slice: journalctl's own '-- No entries --' / '-- Boot ... --' markers are not content."""
    return [l for l in (text or "").splitlines() if l.strip() and not l.startswith("-- ")]


def timestamped_problems(text, label="journal"):
    """-> problems: the text is a journal in ISO timestamps with at least one content line, and every content line carries a timestamp. 'UNAVAILABLE:' text is a failed capture."""
    if text is None or text.startswith("UNAVAILABLE:"):
        return ["%s could not be read on the machine" % label]
    lines = journal_lines(text)
    if not lines:
        return ["%s has no entries" % label]
    bad = [l for l in lines if not ISO_LINE.match(l)]
    # journald continuation lines of multi-line messages are not timestamped; only a journal with NO timestamped line is rejected, plus lines that look like a different format entirely
    if len(bad) == len(lines):
        return ["%s has no timestamped lines (wrong output format?)" % label]
    return []


def attribution_problems(doc):
    """-> list of reasons the attribution capture of one scenario is incomplete or unusable ([] = complete)."""
    p = []
    if not isinstance(doc, dict):
        return ["the capture is not a JSON object"]
    if doc.get("error"):
        p.append("the capture itself failed: %s" % str(doc["error"])[:200])
    if doc.get("schema") != SCHEMA:
        p.append("unknown capture schema %r" % (doc.get("schema"),))
    if not isinstance(doc.get("scenario"), str) or not _num(doc.get("captured_at")):
        p.append("scenario or capture time missing")
    lat = doc.get("latch") or {}
    if lat.get("state") not in (PRESENT, ABSENT):
        p.append("latch state is %r (the latch file must be read: present or absent)" % (lat.get("state"),))
    elif lat["state"] == PRESENT:
        if not isinstance(lat.get("text"), str) or not lat["text"].strip() or lat.get("sha256") != sha256_text(lat["text"]):
            p.append("latch text missing or its hash does not match")
        if not _num(lat.get("mtime")):
            p.append("latch modification time missing")
    trips = doc.get("trips") or {}
    if trips.get("state") not in (PRESENT, ABSENT):
        p.append("trips.jsonl state is %r (it must be read: present or absent)" % (trips.get("state"),))
    elif trips["state"] == PRESENT and not isinstance(trips.get("text"), str):
        p.append("trips.jsonl text missing")
    if (doc.get("pause") or {}).get("state") not in (PRESENT, ABSENT):
        p.append("PAUSE state missing")
    hb = doc.get("heartbeat") or {}
    if hb.get("state") != PRESENT or not _num(hb.get("mtime")) or not _num(hb.get("age")):
        p.append("the monitor's heartbeat file time was not captured (state %r)" % (hb.get("state"),))
    rel = doc.get("released") or {}
    if rel.get("state") not in (PRESENT, ABSENT) or (rel.get("state") == PRESENT and not isinstance(rel.get("names"), list)):
        p.append("the released-evidence listing was not captured")
    if not isinstance(doc.get("guard_listing"), list) or not doc["guard_listing"]:
        p.append("the guard folder listing was not captured")
    jr = doc.get("journals") or {}
    for k in KEY_JOURNALS:
        st = (jr.get(k) or {}).get("state")
        if st not in (PRESENT, EMPTY):
            p.append("the %s journal was not read (state %r)" % (k, st))
    for k in ("alert", "poll"):
        j = jr.get(k) or {}
        if j.get("state") == PRESENT:
            p += timestamped_problems(j.get("text"), "the %s journal" % k)
    if (jr.get("poll") or {}).get("state") != PRESENT:
        p.append("the poll journal is empty: the poll timer runs every minute, so a hold of a minute or more must contain a run")
    for k in ("daemon", "monitor"):
        if not isinstance((doc.get("units") or {}).get(k), dict) or not (doc["units"][k] or {}).get("ActiveState"):
            p.append("the %s unit's state was not captured" % k)
    return p


def sandbox_problems(doc):
    """-> reasons the preserved sandbox record is incomplete ([] = complete)."""
    p = []
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        return ["the sandbox record is missing or has an unknown schema"]
    units = doc.get("units")
    if not isinstance(units, dict) or len(units) < 3:
        return ["the sandbox record must cover the alert unit for each protected unit and the poll unit (got %s)" % (sorted(units) if isinstance(units, dict) else units)]
    for name, props in sorted(units.items()):
        if not isinstance(props, dict):
            p.append("%s: no properties" % name)
            continue
        miss = [k for k in SANDBOX_PROPS if k not in props]
        if miss:
            p.append("%s: properties not captured: %s" % (name, ", ".join(miss)))
    return p


def sandbox_findings(doc, guard):
    """-> reasons the alert units' effective sandbox would let them write the guard folder. Empty list = the guard is protected as designed (ProtectSystem=strict and no writable path covering it)."""
    out = []
    g = guard.rstrip("/")
    for name, props in sorted((doc.get("units") or {}).items()):
        if props.get("ProtectSystem") != "strict":
            out.append("%s: ProtectSystem=%r (want strict: the rest of the filesystem read-only)" % (name, props.get("ProtectSystem")))
        for key in ("ReadWritePaths",):
            for tok in str(props.get(key) or "").split():
                t = tok.lstrip("-+!").rstrip("/") or "/"
                if t == "/" or g == t or g.startswith(t + "/") or t.startswith(g + "/"):
                    out.append("%s: %s lists %s, which covers the guard folder %s" % (name, key, tok, g))
    return out


def preserved_matches(after, cap):
    """Does the preserved evidence reproduce the snapshot the judge looked at? -> (ok, why). Used before a new latch is accepted as the daemon's own: the preserved latch file must hash to the
    snapshot's latch, say the same thing, and the preserved trips.jsonl must be the same records the judge counted."""
    if not isinstance(cap, dict) or attribution_problems(cap):
        return False, "the preserved attribution evidence is missing or incomplete: %s" % ("; ".join(attribution_problems(cap)[:3]) if isinstance(cap, dict) else "none")
    lat = cap["latch"]
    if lat["state"] != PRESENT:
        return False, "the preserved evidence holds no latch file"
    if lat["sha256"] != after.get("latch_sha"):
        return False, "the preserved latch file does not hash to the latch the judge saw"
    try:
        doc = json.loads(lat["text"])
    except ValueError:
        return False, "the preserved latch file is not JSON"
    if doc != after.get("latch"):
        return False, "the preserved latch file says something other than the snapshot's latch"
    if (cap.get("trips") or {}).get("state") != PRESENT:
        return False, "no preserved trips.jsonl"
    recs = []
    for line in cap["trips"]["text"].splitlines():
        if line.strip():
            try:
                recs.append(json.loads(line))
            except ValueError:
                pass
    if recs != (after.get("trip_records") or []):
        return False, "the preserved trips.jsonl is not the set of records the judge counted"
    if cap["pause"]["state"] != PRESENT:
        return False, "the preserved evidence shows no PAUSE"
    return True, "the preserved latch file, trips.jsonl and PAUSE reproduce the judged snapshot"
