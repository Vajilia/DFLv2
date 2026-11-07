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
        sc = SU.SCENARIOS['daemon-first-after-downtime']
        self.assertEqual([u for u, _ in sc['order']], ['dfl-broadcast-daemon', 'dfl-broadcast-monitor'])
        self.assertGreater(sc['hb'], BR.GUARD_STALE_S + BR.GUARD_GRACE_S, 'the heartbeat left behind must be older than both limits, or it proves nothing')

    def test_the_restart_scenarios_use_the_real_grace_and_watch_past_it(self):
        for name in ('daemon-restart-monitor-back-within-grace', 'daemon-restart-dead-monitor'):
            sc = SU.SCENARIOS[name]
            self.assertGreater(sc['hb'], BR.GUARD_STALE_S, name)                        # the heartbeat left behind is stale by the guard's own limit
            self.assertGreater(sc['run_s'], BR.GUARD_GRACE_S + 30, name)                # the run lasts past the 240 s grace, or "no trip" would prove nothing
        late = dict(SU.SCENARIOS['daemon-restart-dead-monitor']['order'])['dfl-broadcast-monitor']
        self.assertGreater(late, BR.GUARD_GRACE_S, 'the dead-monitor monitor must start only after the trip')
        early = dict(SU.SCENARIOS['daemon-restart-monitor-back-within-grace']['order'])['dfl-broadcast-monitor']
        self.assertLess(early, BR.GUARD_GRACE_S, 'the recovery monitor must start inside the grace')
        self.assertNotIn('--guard-grace', SU.unit_argv('dfl-broadcast-daemon', '/r', '/p', '/g', 'http://x'), 'the unit does not override the 240 s default')


if __name__ == '__main__':
    unittest.main()
