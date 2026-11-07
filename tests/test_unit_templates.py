"""The recurring-operation service templates (deploy/supervisor/) against the tools they start. Nothing is installed or run: each ExecStart line is rendered with placeholder values
and parsed by the real tool's own argument parser, so a flag that does not exist, or a cap number that the three processes do not share, is found here and not on the host.

Required here:
  * every ExecStart parses with the tool it names (an unknown flag would make the unit fail at start)
  * the daemon, the monitor and the supervisor are given the SAME public-cap numbers, all from the one environment file
  * every memory limit is a measured number: no leftover 1500M placeholder, and the three add up to less than the 2 GB machine
  * the daemon is not given a fixed cap or a deadline, which would stop a correct recurring broadcast
"""
import argparse
import os
import re
import shlex
import sys
import unittest
from pathlib import Path
from unittest import mock
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))
import broadcast_run as BR                      # noqa: E402
import broadcast_supervisor as SUP              # noqa: E402
import rehearsal_check as RC                    # noqa: E402

UNITS = {'dfl-broadcast-daemon': (BR, 'broadcast_run.py'), 'dfl-broadcast-monitor': (RC, 'rehearsal_check.py'), 'dfl-supervisor': (SUP, 'broadcast_supervisor.py')}
DEPLOY = ROOT / 'deploy' / 'supervisor'


class Stop(Exception):
    pass


def env_values():
    d = dict(l.split('=', 1) for l in (DEPLOY / 'broadcast.env.template').read_text().splitlines() if re.match(r'^[A-Z_]+=', l))
    d['PUBLIC_CEILING_GB'] = '6'                # the owner's number; the template carries a placeholder on purpose
    return d


def exec_args(unit, tool):
    line = [l for l in (DEPLOY / (unit + '.service')).read_text().splitlines() if l.startswith('ExecStart=')][0][len('ExecStart='):]
    for k, v in env_values().items():
        line = line.replace('${%s}' % k, v)
    for k, v in (('__COMMIT__', 'x'), ('__ROOT__', '/r'), ('__PUBLIC__', '/p'), ('__GUARD__', '/g'), ('__HOST__', 'h.example')):
        line = line.replace(k, v)
    argv = shlex.split(line)
    return argv[[i for i, a in enumerate(argv) if a.endswith(tool)][0] + 1:]


def parsed(unit):
    mod, tool = UNITS[unit]
    got = {}

    def grab(self, a=None, ns=None):
        got['ns'] = ArgParse(self, a, ns)
        raise Stop
    ArgParse = argparse.ArgumentParser.parse_args
    with mock.patch.object(argparse.ArgumentParser, 'parse_args', grab):
        try:
            mod.main(exec_args(unit, tool))
        except Stop:
            pass
    return got['ns']


class Templates(unittest.TestCase):
    def test_every_exec_start_parses_with_the_tool_it_names(self):
        for unit in UNITS:
            with self.subTest(unit=unit):
                self.assertIsNotNone(parsed(unit))

    def test_no_placeholder_is_left_in_a_number(self):
        for unit in UNITS:
            text = (DEPLOY / (unit + '.service')).read_text()
            self.assertNotIn('${', text.replace('EnvironmentFile', '').split('ExecStart=')[0], unit)
        self.assertIn('__CEILING_GB__', (DEPLOY / 'broadcast.env.template').read_text(), 'the ceiling is the owner\'s number: the template must not invent one')

    def test_the_three_processes_share_the_same_cap_numbers(self):
        d, m, s = parsed('dfl-broadcast-daemon'), parsed('dfl-broadcast-monitor'), parsed('dfl-supervisor')
        self.assertEqual((d.max_public_gb_per_season, d.public_base_gb, d.max_public_ceiling_gb), (1.25, 0.25, 6.0))
        self.assertEqual((m.max_public_gb_per_season, m.public_base_gb, m.max_public_ceiling_gb), (d.max_public_gb_per_season, d.public_base_gb, d.max_public_ceiling_gb))
        self.assertEqual((s.public_cap_per_season_gb, s.public_base_gb, s.public_ceiling_gb), (d.max_public_gb_per_season, d.public_base_gb, d.max_public_ceiling_gb))
        for unit in UNITS:
            self.assertIn('EnvironmentFile=/etc/dflv2/broadcast.env', (DEPLOY / (unit + '.service')).read_text())

    def test_the_recurring_daemon_has_neither_a_fixed_cap_nor_a_deadline(self):
        d = parsed('dfl-broadcast-daemon')
        self.assertEqual(d.max_public_gb, 0.0)
        self.assertIsNone(d.deadline)

    def test_the_monitor_floor_is_below_the_builders_so_building_stops_first(self):
        m, s = parsed('dfl-broadcast-monitor'), parsed('dfl-supervisor')
        self.assertLess(m.min_free_gb, s.floor_gb)

    def test_memory_limits_are_measured_numbers_that_fit_the_machine(self):
        mb = {}
        for unit in UNITS:
            mb[unit] = int(re.search(r'^MemoryMax=(\d+)M', (DEPLOY / (unit + '.service')).read_text(), re.M).group(1))
        self.assertNotIn(1500, mb.values())
        self.assertEqual(mb, {'dfl-broadcast-daemon': 192, 'dfl-broadcast-monitor': 512, 'dfl-supervisor': 768})
        self.assertLess(sum(mb.values()), 2048 * 0.8, 'the three limits together leave room for the system')


if __name__ == '__main__':
    unittest.main()
