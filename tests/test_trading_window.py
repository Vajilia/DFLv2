#!/usr/bin/env python3
"""Item 14 (owner ruling): the ledger keeps the trading window itself, and older saved state is handled explicitly.

Window (section 12): opens at the close of hiring, 1 February; ends Friday 23:59 before numbered week 9. January, the end of the
season and the days to the next 1 February are closed. Compatibility: ledger state written before the ledger kept the window has no
`trading` entry; neutral.ADAPTERS fills it by the rule written in `neutral._ledger_trading_window`.
"""
import copy
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl import neutral, persist  # noqa: E402
from dfl.ledger.ledger import Ledger, Rejected  # noqa: E402


def old_style(ledger):
    """The neutral document of this ledger as code before the change wrote it: no `trading` entry."""
    doc = neutral.encode(ledger)
    doc = copy.deepcopy(doc)
    doc["s"] = [kv for kv in doc["s"] if kv[0] != "trading"]
    return doc


def strip_window(node):
    """Remove every ledger's `trading` entry from a neutral document, as code before the change would have written it."""
    if isinstance(node, dict):
        if node.get("$o") == "dfl.ledger.ledger.Ledger":
            node["s"] = [kv for kv in node["s"] if kv[0] != "trading"]
        for v in node.values():
            strip_window(v)
    elif isinstance(node, list):
        for v in node:
            strip_window(v)
    return node


def legacy_tree(dest):
    """A copy of the code as it was before the ledger kept the window: the window derived from phase and week alone."""
    os.makedirs(dest)
    shutil.copytree(os.path.join(ROOT, "src", "dfl"), os.path.join(dest, "src", "dfl"), ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(os.path.join(ROOT, "data"), os.path.join(dest, "data"))

    def edit(rel, pairs):
        path = os.path.join(dest, "src", "dfl", rel)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for a, b in pairs:
            assert a in text, (rel, a[:60])
            text = text.replace(a, b)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    with open(os.path.join(ROOT, "src", "dfl", "ledger", "ledger.py"), encoding="utf-8") as f:
        L = f.read()
    a = L.index("    def open_trading(self):")
    b = L.index("    def trade(self, a, a_gives")
    edit("ledger/ledger.py", [(L[a:b], "    def trade_open(self):\n        return self.phase != \"season\" or self.week <= R.TRADE_LAST_PAID_WEEK\n\n"),
                              ("    trading = False                    # class default", "    _legacy = False                    # class default"),
                              ("        self.trading = False           # the trading window", "        _ = False                      # the trading window"),
                              ("        if self.week > R.TRADE_LAST_PAID_WEEK:\n            self.trading = False\n", ""),
                              ("        self.phase = \"post\"\n        self.trading = False\n", "        self.phase = \"post\"\n"),
                              ("        self.trading = False\n        self.fa_moves = []", "        self.fa_moves = []")])
    edit("year.py", [("        self.L.open_trading()\n", ""), ("        self.L.close_trading()\n", "")])
    edit("ledger/world.py", [("        self.L.open_trading()            # the soak driver has no calendar: its offseason is taken to start inside the window\n", "")])


class UpgradeFromOlderCode(unittest.TestCase):
    """A league file written by the code before this change is carried to this code by the real upgrade path; the window it gets is
    the one the calendar gives it, and the old league and the new one go on to the same trades only where the old rule was right."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.tree = os.path.join(cls.tmp, "older")
        legacy_tree(cls.tree)
        cls.old = os.path.join(cls.tmp, "old.db")
        K = 6                                           # past trading_open: the old file is in the middle of the open window
        code = (f"import sys; sys.path.insert(0, {cls.tree + '/src'!r}); from dfl import persist\n"
                f"r = persist.Runner.create({cls.old!r}, {cls.tree + '/data/season1'!r}, seed=1, snapshot_every=5, engine=False); "
                f"r.run_events({K}); print(r.u.L.trade_open(), hasattr(r.u.L, 'trading')); r.close()")
        p = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
        assert p.returncode == 0, p.stderr[-1500:]
        cls.old_says = p.stdout.split()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_older_code_really_has_no_stored_window(self):
        self.assertEqual(self.old_says, ["True", "False"])

    def test_upgrade_gives_the_league_the_window_the_calendar_gives_it(self):
        new = os.path.join(self.tmp, "new.db")
        persist.upgrade(self.old, new, run_originating_code=True, accept_state_change=True)
        r = persist.Runner.open(new)
        try:
            self.assertTrue(r.u.L.trade_open())             # trading_open has fired and trade_close has not
            self.assertIs(r.u.L.trading, True)
            while "trade_close" not in [f[2] for f in r.u.fired]:
                r.run_events(1)
            self.assertFalse(r.u.L.trade_open())            # and from the close the ledger itself refuses
            r.run_events(1)
            self.assertFalse(r.u.L.trade_open())
        finally:
            r.close()

    def test_the_upgrade_says_plainly_that_the_state_changed(self):
        new = os.path.join(self.tmp, "needs_flag.db")
        with self.assertRaises(persist.StateMismatch):
            persist.upgrade(self.old, new, run_originating_code=True)      # a new entry in the state is a state change: it must be accepted knowingly
        self.assertFalse(os.path.exists(new))


class OlderState(unittest.TestCase):
    def test_a_bare_older_ledger_is_closed_until_the_calendar_opens_it(self):
        L = Ledger(["A", "B"])
        L.open_trading()
        got = neutral.decode(strip_window(copy.deepcopy(neutral.encode(L))))
        self.assertIsNone(got.trading)                                  # unknown, and treated as closed
        self.assertFalse(got.trade_open())
        got.open_trading()
        self.assertTrue(got.trade_open())

    def test_a_current_state_round_trips_with_its_window(self):
        L = Ledger(["A", "B"])
        L.open_trading()
        self.assertTrue(neutral.decode(neutral.encode(L)).trade_open())
        L.close_trading()
        self.assertFalse(neutral.decode(neutral.encode(L)).trade_open())

    def test_the_window_cannot_be_opened_during_the_season(self):
        L = Ledger(["A", "B"])
        L.start_camp(); L.begin_season()
        with self.assertRaises(Rejected):
            L.open_trading()

    def test_an_older_league_state_gets_the_window_the_calendar_gives_it_at_every_stage_of_the_year(self):
        from dfl.year import Universe
        u = Universe(os.path.join(ROOT, "data", "season1"), seed=3, engine=False, spatial=False)
        want = {"offseason_open": False, "hiring_close": False, "trading_open": True, "camp_open": True, "week_1": True, "week_8": True,
                "trade_close": False, "week_9": False, "wild_card": False, "coronation": False, "year_close": False}
        seen = set()
        while len(seen) < len(want):
            key = u.step_event()
            if key in want:
                self.assertEqual(u.L.trade_open(), want[key], key)
                legacy = neutral.decode(strip_window(copy.deepcopy(neutral.encode(u))))
                self.assertEqual(legacy.L.trade_open(), want[key], ("older state", key))
                seen.add(key)


if __name__ == "__main__":
    unittest.main()
