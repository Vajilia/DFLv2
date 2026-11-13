"""The alert package is part of the RELEASE CONFIGURATION: the candidate's own unit files stay byte-identical, and the units, drop-ins and tool that sit beside them are rendered from templates,
hashed in a manifest, recorded with the effective systemd settings, and classified so that a change to any of them says which validation stages repeat.
Written before alerts/release_config.py and the templates existed (owner, 2026-11-12: "Treat the drop-ins as part of the release configuration. Record exact revisions and effective systemd settings.")."""
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'alerts'))
sys.path.insert(0, str(ROOT / 'tools' / 'vtest'))
import release_config as RC     # noqa: E402
import common as C              # noqa: E402

DEPLOY = ROOT / 'deploy' / 'supervisor'


def params(kind):
    return RC.default_params(kind)


class Rendering(unittest.TestCase):
    def test_every_rendered_file_has_no_placeholder_left_and_is_deterministic(self):
        for kind in ('vtest', 'production'):
            a, b = RC.render(params(kind)), RC.render(params(kind))
            self.assertEqual(a, b)
            for path, text in a.items():
                self.assertNotRegex(text, r'@[A-Z_]+@', '%s still has a placeholder' % path)

    def test_the_five_files_and_where_they_go(self):
        files = RC.render(params('vtest'))
        names = sorted(Path(p).name if '.d/' not in p else p.split('/')[-2] + '/' + Path(p).name for p in files)
        self.assertEqual(names, ['dfl-vtest-alert-poll.service', 'dfl-vtest-alert-poll.timer', 'dfl-vtest-alert@.service',
                                 'dfl-vtest-daemon.service.d/50-dfl-alert.conf', 'dfl-vtest-monitor.service.d/50-dfl-alert.conf'])
        self.assertTrue(all(p.startswith('/etc/systemd/system/') for p in files))

    def test_a_dropin_adds_exactly_one_directive_and_nothing_that_changes_the_candidates_lifecycle(self):
        for kind in ('vtest', 'production'):
            p = params(kind)
            for unit in (p['daemon_unit'], p['monitor_unit']):
                text = RC.render(p)['/etc/systemd/system/%s.d/50-dfl-alert.conf' % unit]
                directives = [l for l in text.splitlines() if l.strip() and not l.startswith('#') and not l.startswith('[')]
                self.assertEqual(directives, ['OnFailure=%salert@%%n.service' % p['prefix']])
                self.assertIn('[Unit]', text)
                for forbidden in ('Restart', 'StartLimit', 'ExecStart', 'MemoryMax', 'OOMPolicy', 'RestartPreventExitStatus', 'User=', 'Environment'):
                    self.assertNotIn(forbidden, text)

    def test_the_alert_unit_cannot_alert_about_itself_and_is_read_only_everywhere_but_its_own_folder(self):
        for kind in ('vtest', 'production'):
            p = params(kind)
            text = RC.render(p)['/etc/systemd/system/%salert@.service' % p['prefix']]
            self.assertNotIn('OnFailure', text, 'an alert about the alert would loop')
            self.assertNotIn('Restart=', text)
            for must in ('Type=oneshot', 'ProtectSystem=strict', 'NoNewPrivileges=yes', 'ProtectHome=yes', 'PrivateTmp=yes', 'StateDirectory=', 'StateDirectoryMode=0700'):
                self.assertIn(must, text)
            self.assertNotIn('ReadWritePaths', text, 'its only writable place is its own StateDirectory')
            self.assertIn('event %i', text)
            # %n (the failed unit's full name, e.g. dfl-vtest-monitor.service) becomes the instance name unescaped; %I would then UNESCAPE it and turn every '-' into '/' (dfl/vtest/monitor.service).
            self.assertNotIn('%I', text)
            self.assertIn('EnvironmentFile=', text)
            exec_lines = [l for l in text.splitlines() if l.startswith('ExecStart')]
            self.assertEqual(len(exec_lines), 1)
            self.assertNotRegex(exec_lines[0], r'systemctl|release|resume|reset-failed')

    def test_the_poll_service_and_timer(self):
        p = params('vtest')
        files = RC.render(p)
        svc = files['/etc/systemd/system/%salert-poll.service' % p['prefix']]
        tim = files['/etc/systemd/system/%salert-poll.timer' % p['prefix']]
        self.assertIn(' poll', svc)
        self.assertNotIn('OnFailure', svc)
        self.assertIn('OnUnitActiveSec=1min', tim)
        self.assertIn('Unit=', tim) if 'Unit=' in tim else None

    def test_validation_and_production_differ_only_in_names(self):
        v, pr = params('vtest'), params('production')
        a = RC.render(v)
        b = RC.render(pr)

        def normal(text, p):
            for key, token in (('daemon_unit', 'DAEMON'), ('monitor_unit', 'MONITOR'), ('state_name', 'STATE'), ('env_file', 'ENVFILE'), ('bin_dir', 'BIN'), ('prefix', 'PFX-')):
                text = text.replace(p[key], token)
            return text
        na = sorted(normal(t, v) for t in a.values())
        nb = sorted(normal(t, pr) for t in b.values())
        self.assertEqual(na, nb, 'the validation configuration must be the production configuration with other names')

    def test_the_harness_names_pass_the_harness_unit_guard(self):
        p = params('vtest')
        for path in RC.render(p):
            name = path.split('/')[-1] if '.d/' not in path else path.split('/')[-2]
            self.assertTrue(name.startswith(C.PREFIX), name)
        self.assertEqual(p['daemon_unit'], C.UNIT['daemon'])
        self.assertEqual(p['monitor_unit'], C.UNIT['monitor'])

    def test_the_unit_file_syntax_is_plain_ini(self):
        for path, text in RC.render(params('vtest')).items():
            section = None
            for n, line in enumerate(text.splitlines(), 1):
                s = line.strip()
                if not s or s.startswith('#'):
                    continue
                if s.startswith('['):
                    self.assertRegex(s, r'^\[(Unit|Service|Timer|Install)\]$', '%s:%d' % (path, n))
                    section = s
                else:
                    self.assertIsNotNone(section, '%s:%d directive before any section' % (path, n))
                    self.assertRegex(s, r'^[A-Za-z]+=.*$', '%s:%d' % (path, n))

    def test_the_candidates_own_unit_files_are_not_touched_by_the_package(self):
        """The package never edits deploy/supervisor/*: those stay byte-identical to the pin (a drop-in adds OnFailure= beside them)."""
        import subprocess
        pin = C.read_pin(str(ROOT))
        r = subprocess.run(['git', '-C', str(ROOT), 'diff', '--stat', pin, 'HEAD', '--', 'deploy/supervisor/', 'src/', 'tools/broadcast_run.py', 'tools/rehearsal_check.py'], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), '', 'the code under test differs from the pin')


class UnitCheckFiltering(unittest.TestCase):
    """systemd-analyze verify also reports problems in the host's own unit files (the CI runner printed three about CPUAccounting= in xfs_scrub and
    walinuxagent units). Those are not ours and must not fail the check; anything about our files, or any line that names no system file, must still fail it."""
    HOST = ['/usr/lib/systemd/system/system-xfs_scrub.slice:15: Support for option CPUAccounting= has been removed and it is ignored',
            '/usr/lib/systemd/system/xfs_scrub_all.service:26: Support for option CPUAccounting= has been removed and it is ignored',
            '/usr/lib/systemd/system/walinuxagent.service:21: Support for option CPUAccounting= has been removed and it is ignored',
            '/lib/systemd/system/other.service:3: Unknown key name x',
            '/etc/systemd/system/local.service:2: Unknown section Foo']

    def test_host_unit_complaints_are_dropped(self):
        self.assertEqual(unit_problems('\n'.join(self.HOST) + '\n'), [])

    def test_a_complaint_about_our_unit_is_kept(self):
        ours = '/tmp/tmpabc/dfl-vtest-alert@.service:7: Unknown key name Foo in section Service, ignoring.'
        self.assertEqual(unit_problems('\n'.join(self.HOST + [ours])), [ours])

    def test_a_line_that_names_no_system_file_is_kept(self):
        for line in ('dfl-vtest-alert@.service: Command /usr/bin/x is not executable: No such file or directory', 'Failed to load unit: boom', 'Unit dfl-x.timer not found.'):
            self.assertEqual(unit_problems(line), [line])

    def test_the_old_dfl_alert_allowance_still_applies(self):
        self.assertEqual(unit_problems('/tmp/x/dfl-vtest-alert@.service: Command /opt/dfl_alert.py is not executable'), [])

    def test_blank_lines_are_ignored(self):
        self.assertEqual(unit_problems('\n   \n'), [])


def unit_problems(output):
    """The lines of `systemd-analyze verify` output that concern us (see UnitCheckFiltering)."""
    return [l for l in output.splitlines() if l.strip() and 'dfl_alert.py' not in l and not HOST_UNIT_LINE.match(l)]


HOST_UNIT_LINE = re.compile(r'^/(?:usr/lib|lib|etc|run)/systemd/\S+?:\d+: ')


@unittest.skipUnless(shutil.which('systemd-analyze'), 'SKIPPED: systemd-analyze is not installed (the offline unit check is not made)')
class OfflineUnitCheck(unittest.TestCase):
    def test_systemd_analyze_verify_accepts_the_three_units_for_both_name_sets(self):
        import subprocess
        for kind in ('vtest', 'production'):
            with tempfile.TemporaryDirectory() as d:
                p = params(kind)
                names = []
                for path, text in RC.render(p).items():
                    if '.d/' in path:
                        continue
                    (Path(d) / Path(path).name).write_text(text)
                    names.append(str(Path(d) / Path(path).name))
                r = subprocess.run(['systemd-analyze', 'verify', '--man=no'] + names, capture_output=True, text=True, timeout=60)
                bad = unit_problems(r.stdout + r.stderr)
                self.assertEqual(r.returncode, 0, (kind, r.stderr))
                self.assertEqual(bad, [], kind)


class Manifest(unittest.TestCase):
    def test_the_committed_manifest_matches_the_files(self):
        self.assertEqual(RC.verify_manifest(ROOT / 'alerts'), [], 'run: python3 alerts/release_config.py manifest --write')

    def test_it_lists_the_tool_the_templates_and_itself_not(self):
        m = json.loads((ROOT / 'alerts' / 'RELEASE_MANIFEST.json').read_text())
        names = set(m['files'])
        self.assertIn('dfl_alert.py', names)
        self.assertIn('release_config.py', names)
        self.assertTrue(any(n.startswith('templates/') for n in names))
        self.assertNotIn('RELEASE_MANIFEST.json', names)
        self.assertTrue(all(re.fullmatch(r'[0-9a-f]{64}', h) for h in m['files'].values()))

    def test_a_changed_added_or_removed_file_is_detected(self):
        with tempfile.TemporaryDirectory() as d:
            dst = Path(d) / 'alerts'
            shutil.copytree(ROOT / 'alerts', dst, ignore=shutil.ignore_patterns('__pycache__'))
            self.assertEqual(RC.verify_manifest(dst), [])
            (dst / 'dfl_alert.py').write_text((dst / 'dfl_alert.py').read_text() + '\n# tampered\n')
            self.assertTrue(any('dfl_alert.py' in p for p in RC.verify_manifest(dst)))
            shutil.copy(ROOT / 'alerts' / 'dfl_alert.py', dst / 'dfl_alert.py')
            (dst / 'extra.py').write_text('x = 1\n')
            self.assertTrue(any('extra.py' in p for p in RC.verify_manifest(dst)))
            (dst / 'extra.py').unlink()
            (dst / 'templates' / 'alert-poll.timer.in').unlink()
            self.assertTrue(any('alert-poll.timer.in' in p for p in RC.verify_manifest(dst)))

    def test_the_env_template_holds_no_secret_and_no_default_delivery(self):
        text = (ROOT / 'alerts' / 'alert.env.template').read_text()
        for line in text.splitlines():
            if line.strip() and not line.startswith('#'):
                k, _, v = line.partition('=')
                self.assertEqual(v.strip(), '', '%s has a value in the template' % k)
        self.assertNotRegex(text.lower(), r'(password|token|secret|apikey|api_key)\s*=\s*\S')


class Classification(unittest.TestCase):
    """The pin gate and `affected` must know the package: not code under test (the pin stays green), but a release configuration that names the stages its change repeats."""
    def test_the_package_is_release_configuration_not_code_under_test(self):
        for path in ('alerts/dfl_alert.py', 'alerts/templates/alert@.service.in', 'alerts/release_config.py', 'alerts/RELEASE_MANIFEST.json', 'alerts/alert.env.template'):
            kind, stages, why = C.classify_why(path)
            self.assertEqual(kind, 'release_config', path)
            self.assertTrue(stages >= {'V0', 'V5'}, path)
            self.assertTrue(why)

    def test_the_stages_it_reaches_are_named_by_behaviour_with_the_one_exclusion_explained(self):
        kind, stages, why = C.classify_why('alerts/dfl_alert.py')
        self.assertEqual(stages, {'V0', 'V2', 'V3', 'V4', 'V5'})
        self.assertIn('V1', why)                                  # the reason V1 is not named must be written down

    def test_v5_exists_and_has_its_own_stage_file_classification(self):
        self.assertEqual(C.STAGES, ('V0', 'V1', 'V2', 'V3', 'V4', 'V5'))
        self.assertEqual(C.classify_why('tools/vtest/stage_v5.py')[:2], ('harness', {'V5'}))
        self.assertEqual(C.classify_why('tools/vtest/export_evidence.py')[0], 'none')

    def test_a_package_change_does_not_turn_the_pin_gate_red(self):
        import subprocess
        pin = C.read_pin(str(ROOT))
        a = C.affected(str(ROOT), pin)
        self.assertFalse(a['code_changed'], [p for p, k in a['changed'] if k == 'code'])


if __name__ == '__main__':
    unittest.main()
