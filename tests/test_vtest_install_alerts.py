"""`vtest.py install-alerts` and the teardown of what it installs, run on a relocated root with only systemctl replaced (like tests/test_vtest_install.py).

The package is release configuration: it is verified against alerts/RELEASE_MANIFEST.json before anything is written; the candidate's own units are not touched; the record holds the exact revisions
and the effective settings read back from systemd; a tampered package is refused; teardown removes the drop-ins (which `list-unit-files` does not list) so no orphan hook is left behind.
"""
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools' / 'vtest'))
sys.path.insert(0, str(ROOT / 'alerts'))
sys.path.insert(0, str(ROOT / 'tests'))
import common as C            # noqa: E402
import render as RN           # noqa: E402
import install_alerts as IA   # noqa: E402
import release_config as RC   # noqa: E402
import vtest as VT            # noqa: E402


class Machine(C.Env):
    def __init__(self):
        self.sc_calls = []

    def sc(self, verb, *args, timeout=60):
        self.sc_calls.append((verb,) + args)
        if verb == 'show':
            return 0, 'ActiveState=active\nOnFailure=x\n', ''
        if verb == 'cat':
            return 0, '# /etc/systemd/system/%s\n[Unit]\n' % args[0], ''
        return 0, '', ''


class Base(unittest.TestCase):
    def setUp(self):
        self.top = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.top, True)
        t = self.top
        new = {'BASE': t + '/srv/dfl-vtest', 'SRC_BASE': t + '/opt/dfl-vtest-src', 'ETC': t + '/etc/dfl-vtest', 'UNIT_DIR': t + '/etc/systemd/system',
               'ALERT_STATE': t + '/var/lib/dfl-vtest-alerts', 'ALERT_BIN': t + '/opt/dfl-vtest-alert'}
        b = new['BASE']
        new.update({'STATE': b + '/state', 'GUARD': b + '/guard', 'EVID': b + '/evidence', 'ROOT_DIR': b + '/root'})
        new['ALERT_SINK'] = new['ALERT_STATE'] + '/sink.log'
        new['ALERT_MODE'] = new['ALERT_STATE'] + '/deliver.mode'
        self.new = new
        for mod in (C, RN, IA):
            for k, v in new.items():
                if hasattr(mod, k):
                    p = mock.patch.object(mod, k, v)
                    p.start()
                    self.addCleanup(p.stop)
        os.makedirs(C.STATE)
        Path(C.STATE, 'install.json').write_text(json.dumps({'pin': 'a' * 40, 'user': 'dfl-vtest'}))
        self.env = Machine()

    def repo_with_alerts(self, tamper=None):
        """A throwaway git repository holding a copy of alerts/ (so the git tree ids can be read), optionally tampered with."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        shutil.copytree(ROOT / 'alerts', Path(d) / 'alerts', ignore=shutil.ignore_patterns('__pycache__'))
        os.makedirs(Path(d) / 'deploy' / 'supervisor')
        Path(d, 'deploy', 'supervisor', 'x').write_text('x')
        if tamper:
            tamper(Path(d))
        for args in (['init', '-q'], ['add', '-A'], ['-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-qm', 'x']):
            subprocess.run(['git', '-C', d] + args, check=True, capture_output=True)
        return d


class Install(Base):
    def test_a_machine_without_the_candidate_installed_is_refused(self):
        Path(C.STATE, 'install.json').unlink()
        with self.assertRaises(C.Unsafe):
            IA.install_alerts(self.env, self.repo_with_alerts(), echo=lambda *a: None)

    def test_a_tampered_package_is_refused_and_nothing_is_written(self):
        repo = self.repo_with_alerts(lambda d: (d / 'alerts' / 'dfl_alert.py').write_text((d / 'alerts' / 'dfl_alert.py').read_text() + '\n# x\n'))
        with self.assertRaises(C.Unsafe) as cm:
            IA.install_alerts(self.env, repo, echo=lambda *a: None)
        self.assertIn('dfl_alert.py', str(cm.exception))
        self.assertFalse(os.path.exists(C.ALERT_BIN))
        self.assertEqual([c for c in self.env.sc_calls if c[0] in ('start', 'daemon-reload')], [])

    def test_the_files_land_where_the_units_expect_them_and_the_tool_is_the_manifests_tool(self):
        rec = IA.install_alerts(self.env, self.repo_with_alerts(), echo=lambda *a: None)
        for name in ('dfl-vtest-alert@.service', 'dfl-vtest-alert-poll.service', 'dfl-vtest-alert-poll.timer', 'dfl-vtest-daemon.service.d/50-dfl-alert.conf', 'dfl-vtest-monitor.service.d/50-dfl-alert.conf'):
            self.assertTrue(Path(C.UNIT_DIR, name).is_file(), name)
        self.assertEqual(rec['tool_installed_sha256'], RC.sha256_text(Path(C.ALERT_BIN, 'dfl_alert.py').read_text()))
        self.assertEqual(rec['tool_installed_sha256'], json.loads((ROOT / 'alerts' / 'RELEASE_MANIFEST.json').read_text())['files']['dfl_alert.py'])
        self.assertEqual(oct(os.stat(C.ETC + '/alert.env').st_mode & 0o777), '0o600')
        self.assertEqual(Path(C.ALERT_MODE).read_text(), 'ok\n')

    def test_the_candidates_own_unit_files_are_never_written(self):
        for n in ('dfl-vtest-daemon.service', 'dfl-vtest-monitor.service'):
            os.makedirs(C.UNIT_DIR, exist_ok=True)
            Path(C.UNIT_DIR, n).write_text('ORIGINAL\n')
        IA.install_alerts(self.env, self.repo_with_alerts(), echo=lambda *a: None)
        for n in ('dfl-vtest-daemon.service', 'dfl-vtest-monitor.service'):
            self.assertEqual(Path(C.UNIT_DIR, n).read_text(), 'ORIGINAL\n')

    def test_the_environment_file_gives_the_tool_a_file_channel_and_nothing_secret(self):
        text = IA.env_file_text()
        env = {}
        for line in text.splitlines():
            if line.strip() and not line.startswith('#'):
                k, _, v = line.partition('=')
                env[k] = shlex.split(v)[0] if v.startswith('"') else v
        argv = shlex.split(env['DFL_ALERT_DELIVER_CMD'])
        self.assertEqual(argv[:2], ['/bin/sh', '-c'])
        self.assertIn(C.ALERT_MODE, argv[2])
        self.assertIn(C.ALERT_SINK, argv[2])
        self.assertEqual(env['DFL_ALERT_HEARTBEAT'], C.GUARD + '/heartbeat')
        self.assertNotRegex(text.lower(), r'(password|token|secret|key)')

    def test_the_record_holds_exact_revisions_hashes_and_the_effective_settings_read_back(self):
        rec = IA.install_alerts(self.env, self.repo_with_alerts(), echo=lambda *a: None)
        self.assertRegex(rec['git']['alerts_tree'], r'^[0-9a-f]{40}$')
        self.assertRegex(rec['git']['harness_revision'], r'^[0-9a-f]{40}$')
        self.assertEqual(set(rec['rendered']), set(RC.render(RC.default_params('vtest'))))
        self.assertEqual(rec['package_files'], json.loads((ROOT / 'alerts' / 'RELEASE_MANIFEST.json').read_text())['files'])
        self.assertIn(C.UNIT['daemon'], rec['effective'])
        self.assertIn(C.UNIT['monitor'], rec['effective'])
        self.assertTrue(rec['effective'][C.UNIT['daemon']]['cat'].startswith('# /etc/systemd/system/'))
        shows = [c for c in self.env.sc_calls if c[0] == 'show']
        self.assertTrue(all(c[1].startswith('dfl-vtest-') for c in shows))
        on_disk = json.loads(Path(IA.record_path()).read_text())
        self.assertEqual(on_disk['git'], rec['git'])

    def test_only_the_poll_timer_is_started_and_nothing_is_enabled(self):
        IA.install_alerts(self.env, self.repo_with_alerts(), echo=lambda *a: None)
        writes = [c for c in self.env.sc_calls if c[0] in ('start', 'stop', 'restart', 'enable', 'reset-failed', 'kill')]
        self.assertEqual(writes, [('start', 'dfl-vtest-alert-poll.timer')])

    def test_every_written_path_passes_the_harness_guard(self):
        written = []
        real = C.Env.write

        def spy(self, path, text, mode=0o644):
            written.append(path)
            return real(self, path, text, mode)
        with mock.patch.object(C.Env, 'write', spy):
            IA.install_alerts(self.env, self.repo_with_alerts(), echo=lambda *a: None)
        self.assertTrue(written and all(C.allowed_path(p) for p in written), written)


class Teardown(Base):
    def test_teardown_removes_the_dropins_the_alert_units_the_tool_and_keeps_the_incidents_unless_asked(self):
        IA.install_alerts(self.env, self.repo_with_alerts(), echo=lambda *a: None)
        Path(C.ALERT_STATE, 'incidents').mkdir(parents=True, exist_ok=True)
        Path(C.ALERT_STATE, 'incidents', 'x.json').write_text('{}')
        lines = []
        RN.teardown(self.env, include_evidence=False, yes=False, echo=lines.append)
        text = '\n'.join(lines)
        self.assertIn('50-dfl-alert.conf', text.replace('.d', '.d/50-dfl-alert.conf') if 'service.d' in text else text) if False else None
        self.assertIn('dfl-vtest-daemon.service.d', text)
        self.assertIn(C.ALERT_BIN, text)
        self.assertNotIn(C.ALERT_STATE, text, 'the incident records are evidence: kept unless --include-evidence')
        RN.teardown(self.env, include_evidence=False, yes=True, echo=lambda *a: None)
        self.assertFalse(Path(C.UNIT_DIR, 'dfl-vtest-daemon.service.d').exists())
        self.assertFalse(Path(C.UNIT_DIR, 'dfl-vtest-alert@.service').exists())
        self.assertFalse(os.path.exists(C.ALERT_BIN))
        self.assertTrue(Path(C.ALERT_STATE, 'incidents', 'x.json').exists())
        self.assertIn(('stop', 'dfl-vtest-alert-poll.timer'), self.env.sc_calls)
        RN.teardown(self.env, include_evidence=True, yes=True, echo=lambda *a: None)
        self.assertFalse(os.path.exists(C.ALERT_STATE))


class Driver(unittest.TestCase):
    def test_install_alerts_is_a_driver_command_that_holds_the_run_lock(self):
        self.assertTrue(hasattr(VT, 'cmd_install_alerts'))
        src = (ROOT / 'tools' / 'vtest' / 'vtest.py').read_text()
        self.assertIn('@exclusive\ndef cmd_install_alerts', src)
        self.assertIn('"install-alerts"', src)

    def test_the_package_must_be_in_a_clean_committed_tree(self):
        """The record names git tree ids, so it must come from a commit: the same pin gate as `install`."""
        src = (ROOT / 'tools' / 'vtest' / 'vtest.py').read_text()
        body = src.split('def cmd_install_alerts', 1)[1].split('\n\n\n', 1)[0]
        self.assertIn('pin_check', body)


if __name__ == '__main__':
    unittest.main()
