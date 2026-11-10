"""The recurring-operation service templates (deploy/supervisor/) against the tools they start. Nothing is installed or run: each ExecStart line is rendered with placeholder values
and parsed by the real tool's own argument parser, so a flag that does not exist, or a cap number that the three processes do not share, is found here and not on the host.

Required here:
  * every ExecStart parses with the tool it names (an unknown flag would make the unit fail at start)
  * the daemon, the monitor and the supervisor are given the SAME public-cap numbers, all from the one environment file
  * every memory limit is a measured number: no leftover 1500M placeholder, and the three add up to less than the 2 GB machine
  * the daemon is not given a fixed cap or a deadline, which would stop a correct recurring broadcast
  * a process killed for going over MemoryMax takes its whole unit down (OOMPolicy=stop, set explicitly: the default depends on the distribution) so Restart= brings it back clean
"""
import argparse
import os
import re
import shlex
import shutil
import subprocess
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

    def test_a_memory_kill_stops_the_whole_unit_and_the_unit_is_restarted(self):
        for unit in UNITS:
            text = (DEPLOY / (unit + '.service')).read_text()
            self.assertRegex(text, r'(?m)^OOMPolicy=stop$', unit)
            if unit != 'dfl-supervisor':
                self.assertRegex(text, r'(?m)^Restart=on-failure$', unit)
        sup = (DEPLOY / 'dfl-supervisor.service').read_text()
        self.assertRegex(sup, r'(?m)^KillMode=control-group$')

    def test_the_daemon_records_an_oom_stop_before_it_can_be_restarted(self):
        """ExecStopPost= runs after the unit stopped and before Restart= starts it again; systemd gives it $SERVICE_RESULT (oom-kill when OOMPolicy=stop ended the unit because ANY process of
        the cgroup was OOM-killed). The recorder writes the latch and the hold there, so the restarted daemon cannot advance the broadcast (tools/broadcast_run.py incident)."""
        for path, cgroup in ((DEPLOY / 'dfl-broadcast-daemon.service', 'dfl-broadcast-daemon.service'), (ROOT / 'deploy' / 'rehearsal' / 'dfl-rehearsal.service', 'dfl-rehearsal.service')):
            with self.subTest(unit=path.name):
                text = path.read_text()
                self.assertRegex(text, r'(?m)^OOMPolicy=stop$', 'set explicitly: the default depends on the distribution')
                self.assertRegex(text, r'(?m)^Restart=on-failure$')
                lines = [l for l in text.splitlines() if l.startswith('ExecStopPost=')]
                self.assertEqual(len(lines), 1, lines)
                line = lines[0][len('ExecStopPost='):]
                self.assertFalse(line.startswith('-'), 'a recorder that fails must show as a failure, not be ignored')
                self.assertFalse(line.startswith('+'), 'no extra privileges: it runs as the service user, inside the same sandbox')
                for k, v in (('__COMMIT__', 'x'), ('__ROOT__', '/r'), ('__PUBLIC__', '/p'), ('__GUARD__', '/g')):
                    line = line.replace(k, v)
                argv = shlex.split(line)
                self.assertIn('broadcast_run.py', argv[argv.index('-I') + 1] if '-I' in argv else '', 'python -I: isolated, as the daemon is')
                got = {}

                def grab(self_, a=None, ns=None):
                    got['ns'] = ArgParse(self_, a, ns)
                    raise Stop
                ArgParse = argparse.ArgumentParser.parse_args
                with mock.patch.object(argparse.ArgumentParser, 'parse_args', grab):
                    try:
                        BR.main(argv[[i for i, a in enumerate(argv) if a.endswith('broadcast_run.py')][0] + 1:])
                    except Stop:
                        pass
                ns = got['ns']
                self.assertEqual(ns.cmd, 'incident')
                self.assertEqual(ns.cgroup, '/sys/fs/cgroup/system.slice/' + cgroup, 'the unit\'s own cgroup, which still exists while ExecStopPost runs')
                run = shlex.split([l for l in text.splitlines() if l.startswith('ExecStart=')][0].replace('__COMMIT__', 'x').replace('__ROOT__', '/r').replace('__PUBLIC__', '/p').replace('__GUARD__', '/g'))
                self.assertEqual(ns.guard, run[run.index('--guard') + 1], 'the same guard folder the daemon obeys and the monitor writes')
                self.assertEqual(ns.root, run[run.index('--root') + 1], 'the same private root')
                self.assertEqual(ns.public, run[run.index('--public') + 1])
                tout = int(re.search(r'(?m)^TimeoutStopSec=(\d+)$', text).group(1))
                self.assertGreaterEqual(tout, 30, 'ExecStopPost shares the stop timeout: the recorder (retries, a 5 s log read) must fit')

    def test_the_daemon_cannot_start_over_an_unaccounted_stop(self):
        """ExecStartPre= `gate` opens the run marker and turns a marker left by an unaccounted stop into a latch, or fails (the daemon then does not start). Without a "-" prefix, on purpose."""
        for path in (DEPLOY / 'dfl-broadcast-daemon.service', ROOT / 'deploy' / 'rehearsal' / 'dfl-rehearsal.service'):
            with self.subTest(unit=path.name):
                text = path.read_text()
                pre = [l for l in text.splitlines() if l.startswith('ExecStartPre=')]
                self.assertEqual(len(pre), 1, pre)
                self.assertFalse(pre[0][len('ExecStartPre='):].startswith(('-', '+')), 'a failing gate must stop the start, with no extra privileges')
                sub = lambda l: shlex.split(l.replace('__COMMIT__', 'x').replace('__ROOT__', '/r').replace('__PUBLIC__', '/p').replace('__GUARD__', '/g'))
                gate, run, stop = sub(pre[0].split('=', 1)[1]), sub([l for l in text.splitlines() if l.startswith('ExecStart=')][0].split('=', 1)[1]), sub([l for l in text.splitlines() if l.startswith('ExecStopPost=')][0].split('=', 1)[1])
                self.assertIn('gate', gate)
                for flag in ('--root', '--public', '--guard'):
                    self.assertEqual(gate[gate.index(flag) + 1], run[run.index(flag) + 1], flag + ' must be the daemon\'s own')
                    self.assertEqual(gate[gate.index(flag) + 1], stop[stop.index(flag) + 1], flag + ' must be the hook\'s own: the marker is opened and closed in one place')
                self.assertLess(text.index('ExecStartPre='), text.index('ExecStart='))

    def test_the_marker_is_closed_before_storage_goes_away_at_shutdown(self):
        """A clean reboot must close the marker (ExecStopPost= runs at shutdown), or every reboot would hold the broadcast. The unit therefore depends on the mounts it writes to."""
        for path, needs in ((DEPLOY / 'dfl-broadcast-daemon.service', ('__ROOT__', '__PUBLIC__', '__GUARD__')), (ROOT / 'deploy' / 'rehearsal' / 'dfl-rehearsal.service', ('/srv/dflv2-rehearsal',))):
            with self.subTest(unit=path.name):
                text = path.read_text()
                unit_section = text.split('[Unit]', 1)[1].split('[Service]', 1)[0]
                m = re.search(r'(?m)^RequiresMountsFor=(.+)$', unit_section)
                self.assertIsNotNone(m, 'RequiresMountsFor= is a [Unit] setting (in [Service] systemd ignores it with a warning): it orders the unit before the unmount of what it writes to')
                for need in needs:
                    self.assertIn(need, m.group(1))

    @unittest.skipUnless(shutil.which('systemd-analyze'), 'needs systemd-analyze (parses unit files offline; no systemd needs to be running)')
    def test_systemd_itself_parses_the_units_without_ignoring_a_setting(self):
        """`systemd-analyze verify --root=` is systemd's own parser. It does not RUN anything. What it catches here: a directive in the wrong section ("Unknown key ... ignoring"), a misspelled key, a bad value.
        Its other messages (no python3 under the fake root, no sysinit.target) are artefacts of the fake root and are ignored."""
        import tempfile
        for path, name in ((DEPLOY / 'dfl-broadcast-daemon.service', 'dfl-broadcast-daemon.service'), (ROOT / 'deploy' / 'rehearsal' / 'dfl-rehearsal.service', 'dfl-rehearsal.service')):
            with self.subTest(unit=name), tempfile.TemporaryDirectory() as root:
                os.makedirs(os.path.join(root, 'etc', 'systemd', 'system'))
                text = path.read_text()
                for k, v in (('__COMMIT__', 'c'), ('__ROOT__', '/srv/x'), ('__PUBLIC__', '/srv/x/public'), ('__GUARD__', '/srv/x/guard')):
                    text = text.replace(k, v)
                with open(os.path.join(root, 'etc', 'systemd', 'system', name), 'w') as f:
                    f.write(text)
                r = subprocess.run(['systemd-analyze', 'verify', '--root=' + root, name], capture_output=True, text=True, timeout=60)
                bad = [l for l in (r.stdout + r.stderr).splitlines() if l.strip() and 'is not executable' not in l and 'sysinit.target' not in l]
                self.assertEqual(bad, [], 'systemd would ignore or reject something in %s' % name)

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
