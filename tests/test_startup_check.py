"""The start-up reproduction (tools/startup_check.py) runs the processes with the unit files' own argument lists, and the start orders it reports are the ones the docs name.

Required here (cheap; the processes themselves are run by the tool, not by the suite):
  * every ExecStart renders with no placeholder left, starts with the interpreter and the tool the unit names, and parses with that tool's own parser
  * the monitor and the daemon get the same cap numbers the unit files give them, from the one environment file
  * the scenarios include the order the docs document (daemon, then monitor) and the host-outage case (an old heartbeat file) that tripped the daemon before the fix
"""
import os
import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))
import broadcast_run as BR          # noqa: E402
import broadcast_supervisor as SUP  # noqa: E402
import rehearsal_check as RC        # noqa: E402
import startup_check as SU          # noqa: E402

TOOLS = {'dfl-broadcast-daemon': (BR, 'broadcast_run.py'), 'dfl-broadcast-monitor': (RC, 'rehearsal_check.py'), 'dfl-supervisor': (SUP, 'broadcast_supervisor.py')}


class Rendering(unittest.TestCase):
    def argv(self, unit):
        return SU.unit_argv(unit, '/r', '/p', '/g', 'http://127.0.0.1:1')

    def test_each_unit_renders_completely_and_runs_this_checkouts_tool(self):
        for unit, (mod, tool) in TOOLS.items():
            a = self.argv(unit)
            self.assertEqual((a[0], a[1]), (sys.executable, '-I'), unit)
            self.assertEqual(a[2], str(ROOT / 'tools' / tool), unit)
            self.assertFalse([x for x in a if '__' in x or '${' in x], (unit, a))

    def test_the_arguments_parse_with_the_tools_own_parser(self):
        d = self.argv('dfl-broadcast-daemon')
        self.assertIn('--guard', d)
        self.assertEqual(d[d.index('--root') + 1:d.index('--root') + 2], ['/r'])
        m = self.argv('dfl-broadcast-monitor')
        self.assertEqual(m[m.index('--url') + 1], 'http://127.0.0.1:1')
        self.assertEqual(m[m.index('--max-public-gb-per-season') + 1], d[d.index('--max-public-gb-per-season') + 1])

    def test_the_scenarios_cover_the_documented_order_and_the_outage_case(self):
        names = set(SU.SCENARIOS)
        self.assertIn('daemon-first-after-downtime', names)
        self.assertIn('together-after-downtime', names)
        what, hb_age, order = SU.SCENARIOS['daemon-first-after-downtime']
        self.assertEqual([u for u, _ in order], ['dfl-broadcast-daemon', 'dfl-broadcast-monitor'])
        self.assertGreater(hb_age, BR.GUARD_STALE_S + BR.GUARD_GRACE_S, 'the heartbeat left behind must be older than both limits, or it proves nothing')


if __name__ == '__main__':
    unittest.main()
