"""A small model of the parts of `journalctl` that V5's journal collector depends on, for tests that cannot run a real journal.

It is NOT an invention: each rule below was observed by running real journalctl (systemd 255.4, the version on the validation VM) against a private journald (docs/V5_LATCH_ATTRIBUTION_2026-11-17.md,
"The alert-journal collection failure"):

  * `journalctl -u 'pattern@*'` is expanded against the unit names the journal HOLDS in its index: the fields _SYSTEMD_UNIT, OBJECT_SYSTEMD_UNIT, COREDUMP_UNIT and UNIT, journal-wide (not limited by
    --since, and not by whether the unit is still loaded: an instance that finished still counts while its entries remain).
  * If every -u given is a glob and none matches a held name, it exits 1 with 'Failed to add filter for units: No data available'. Any exact -u beside the glob (even a unit that never existed) makes it
    exit 0, as does one glob that matches.
  * An exact `-u NAME` never fails: with no entries it exits 0 and prints '-- No entries --'.
  * `-F FIELD` lists the values of a field journal-wide, ignores --since, exits 0 with no output when the field is absent, and also exits 0 (silently empty) for a field that does not exist.
"""
import datetime
import fnmatch

NO_MATCH_ERROR = "Failed to add filter for units: No data available\n"
UNIT_FIELDS = ("_SYSTEMD_UNIT", "OBJECT_SYSTEMD_UNIT", "COREDUMP_UNIT", "UNIT")


def stamp(at):
    return datetime.datetime.fromtimestamp(at, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


class FakeJournal:
    def __init__(self):
        self.entries = []                       # (epoch, field, unit name, text)
        self.calls = []
        self.fail_all = None                    # (rc, stderr): every call fails (journal unreadable)
        self.fail_if = []                       # [(predicate(argv), rc, stderr)]: calls that fail

    def add(self, unit, text="entry", at=1000.0, field="_SYSTEMD_UNIT"):
        self.entries.append((at, field, unit, text))
        return self

    def names(self, field):
        return sorted({u for _, f, u, _ in self.entries if f == field})

    def all_names(self):
        return sorted({u for _, _, u, _ in self.entries})

    def run(self, argv, timeout=60, input=None):
        self.calls.append(tuple(argv))
        if not argv or argv[0] != "journalctl":
            return 0, "", ""
        if self.fail_all:
            return self.fail_all[0], "", self.fail_all[1]
        for pred, rc, err in self.fail_if:
            if pred(argv):
                return rc, "", err
        if "-F" in argv:
            field = argv[argv.index("-F") + 1]
            return 0, "".join(n + "\n" for n in self.names(field)), ""
        since = 0.0
        if "--since" in argv:
            since = float(argv[argv.index("--since") + 1].lstrip("@"))
        pats = [argv[i + 1] for i, a in enumerate(argv) if a == "-u"]
        chosen, globs_matched = set(), False
        for p in pats:
            if any(c in p for c in "*?["):
                hits = [n for n in self.all_names() if fnmatch.fnmatchcase(n, p)]
                globs_matched = globs_matched or bool(hits)
                chosen.update(hits)
            else:
                chosen.add(p)
        if pats and all(any(c in p for c in "*?[") for p in pats) and not globs_matched:
            return 1, "", NO_MATCH_ERROR
        rows = sorted((at, u, t) for at, _, u, t in self.entries if (not pats or u in chosen) and at >= since)
        if not rows:
            return 0, "-- No entries --\n", ""
        return 0, "".join("%s host unit[1]: %s\n" % (stamp(at), t) for at, _, t in rows), ""


def good_alert_journal(instance=False, since=1000.0):
    """The alert-journal record the collector produces on a healthy capture (the poll ran; with `instance` a hook instance also ran and finished), for fixtures that need a complete one."""
    import types
    import common as C
    import stage_v5 as V5
    j = FakeJournal().add(C.PREFIX + "alert-poll.service", "poll ran", at=1020.0)
    if instance:
        j.add(C.PREFIX + "alert@" + C.UNIT["monitor"] + ".service", "Finished", at=1010.0)
    return V5.collect_alert_journal(types.SimpleNamespace(env=types.SimpleNamespace(run=j.run)), since)


def good_query(units=("x.service",), since=1000.0):
    """The record `_journal` produces for a successful exact-name query with an entry."""
    return {"state": "present", "text": "2027-01-15T00:04:37.100000+00:00 host unit[1]: entry\n",
            "query": {"argv": ["journalctl", "--since", "@%d" % since, "-u"] + list(units), "rc": 0, "stderr": ""}}


def good_status(since=0.0):
    """alert_journal_status.json for a healthy poll-only run."""
    import common as C
    poll = good_query((C.PREFIX + "alert-poll.service",), since)
    units = [C.PREFIX + "alert-poll.service"] + sorted(__import__("attribution").expected_alert_instances())
    return {"schema": 1, "alert": good_alert_journal(since=since), "poll": poll,
            "combined": {"argv": ["journalctl", "--no-pager", "-o", "short-iso-precise"] + [x for u in units for x in ("-u", u)], "rc": 0, "units": units, "stderr": ""}}
