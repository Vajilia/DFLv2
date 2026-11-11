"""V0: service user, permissions, applied sandbox, marker order, durability probe. Judges are tested on synthetic observations (good and each deliberately wrong); the durability probe also once for real
(setpriv as `nobody` + strace against the pinned code) when this machine can do that. Nothing here talks to systemd."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
VT = ROOT / 'tools' / 'vtest'
sys.path.insert(0, str(VT))

import common as C            # noqa: E402
import durability as D        # noqa: E402
import perms as P             # noqa: E402
import stage_v0               # noqa: E402

PATHS = stage_v0.paths('/opt/dfl-vtest-src/abc')
USER = 'dfl-vtest'


def ids(rows, status=None):
    return {r[0] for r in rows if status is None or r[1] == status}


class ServiceUser(unittest.TestCase):
    GOOD = {'name': USER, 'exists': True, 'uid': 995, 'gid': 995, 'shell': '/usr/sbin/nologin', 'groups': [USER]}

    def test_a_dedicated_unprivileged_user_passes(self):
        self.assertEqual(ids(P.judge_service_user(self.GOOD), 'fail'), set())

    def test_each_defect_fails_its_own_row(self):
        for change, row in ((dict(uid=0, gid=0), 'V0.user.unprivileged'), (dict(uid=65534, gid=65534), 'V0.user.unprivileged'), (dict(shell='/bin/bash'), 'V0.user.no_login'),
                            (dict(groups=[USER, 'sudo']), 'V0.user.no_extra_groups'), (dict(groups=[USER, 'staff']), 'V0.user.no_extra_groups')):
            rows = P.judge_service_user(dict(self.GOOD, **change))
            self.assertIn(row, ids(rows, 'fail'), change)

    def test_a_missing_user_is_one_failed_row(self):
        self.assertEqual(ids(P.judge_service_user({'name': USER, 'exists': False}), 'fail'), {'V0.user.exists'})


class Layout(unittest.TestCase):
    def observed(self, **over):
        o = {}
        for path, who, mode in P.layout(PATHS):
            o[path] = {'owner': 'root' if who == 'root' else USER, 'mode': 0o755 if mode == 'noworld' else mode}
        o.update(over)
        return o

    def judge(self, o):
        return P.judge_layout(o, P.layout(PATHS), USER)

    def test_the_intended_layout_passes(self):
        self.assertEqual(ids(self.judge(self.observed()), 'fail'), set())

    def test_the_service_owns_exactly_its_store_the_public_folder_and_the_guard(self):
        own = [p for p, who, _ in P.layout(PATHS) if who == 'svc']
        self.assertEqual(sorted(own), sorted([PATHS['ROOT_DIR'] + '/store', PATHS['PUBLIC'], PATHS['GUARD']]))

    def test_wrong_owner_or_mode_fails_that_path_only(self):
        cases = {PATHS['GUARD']: {'owner': USER, 'mode': 0o755}, PATHS['PUBLIC']: {'owner': 'root', 'mode': 0o755}, PATHS['ROOT_DIR']: {'owner': USER, 'mode': 0o755},
                 PATHS['TREE']: {'owner': 'root', 'mode': 0o775}, PATHS['STATE']: {'owner': 'root', 'mode': 0o777}, PATHS['ROOT_DIR'] + '/store': {'owner': USER, 'mode': 0o755}}
        for path, bad in cases.items():
            self.assertEqual(ids(self.judge(self.observed(**{path: bad})), 'fail'), {'V0.layout:' + path}, path)

    def test_a_missing_path_fails(self):
        self.assertEqual(ids(self.judge(self.observed(**{PATHS['GUARD']: None})), 'fail'), {'V0.layout:' + PATHS['GUARD']})

    def test_the_tree_scan_finds_group_or_other_writable_paths(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        Path(d, 'a.py').write_text('x')
        os.chmod(d + '/a.py', 0o644)
        self.assertEqual(P.tree_writable_by_others(d), [])
        os.chmod(d + '/a.py', 0o664)
        self.assertEqual(P.tree_writable_by_others(d), [d + '/a.py'])
        os.symlink('/etc/passwd', d + '/link')                      # a symlink's own mode is not a finding
        self.assertEqual(P.tree_writable_by_others(d), [d + '/a.py'])


class ApplyLayout(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        j = lambda *a: os.path.join(self.d, *a)
        self.paths = {'BASE': j('b'), 'ROOT_DIR': j('b', 'root'), 'WEB_DIR': j('b', 'web'), 'PUBLIC': j('b', 'web', 'public'), 'GUARD': j('b', 'guard'), 'TREE': j('tree'),
                      'ETC': j('etc'), 'EVID': j('b', 'evidence'), 'STATE': j('b', 'state'), 'LOADDIR': j('b', 'load'), 'PROBE': j('b', 'probe')}
        for k, p in self.paths.items():
            if k != 'ETC':
                os.makedirs(p, exist_ok=True)
        os.makedirs(self.paths['ROOT_DIR'] + '/store')
        Path(self.paths['TREE'], 'tools').mkdir()
        Path(self.paths['TREE'], 'tools', 'x.py').write_text('x')
        os.chmod(self.paths['TREE'] + '/tools/x.py', 0o775)
        self.owned, self.modes = {}, {}
        self.pw = mock.Mock(pw_uid=995, pw_gid=996)

    def apply(self, allowed=None):
        return P.apply_layout(USER, self.paths, allowed or (lambda p: p.startswith(self.d)), chown=lambda p, u, g: self.owned.__setitem__(p, (u, g)),
                              chmod=lambda p, m: self.modes.__setitem__(p, m & 0o7777), getpw=lambda n: self.pw)

    def test_ownership_and_modes_are_the_layout(self):
        self.apply()
        p = self.paths
        for path in (p['ROOT_DIR'] + '/store', p['PUBLIC'], p['GUARD']):
            self.assertEqual(self.owned[path], (995, 996), path)
        for path in (p['BASE'], p['ROOT_DIR'], p['WEB_DIR'], p['TREE'] + '/.', p['TREE'] + '/tools/x.py'):
            self.assertEqual(self.owned[path], (0, 0), path)
        self.assertEqual(self.modes[p['GUARD']], 0o700)
        self.assertEqual(self.modes[p['ROOT_DIR'] + '/store'], 0o700)
        self.assertEqual(self.modes[p['PUBLIC']], 0o755)
        self.assertEqual(self.modes[p['TREE'] + '/tools/x.py'] & 0o022, 0, 'the code is not writable by group or other')

    def test_the_installers_own_path_set_is_enough_for_the_ownership_step(self):
        """Found on the first real install: the installer's path dict lacked a key the ownership step reads. Both now take their paths from one function."""
        keys = P.layout_paths('/t')
        self.assertEqual(set(keys), set(PATHS))
        j = lambda *a: os.path.join(self.d, 'x', *a)
        paths = {k: j(k) for k in keys}
        for k, p in paths.items():
            os.makedirs(p, exist_ok=True)
        os.makedirs(paths['ROOT_DIR'] + '/store', exist_ok=True)
        Path(paths['ETC'], 'broadcast.env').write_text('X=1\n')
        P.apply_layout(USER, paths, lambda p: p.startswith(self.d), chown=lambda p, u, g: None, chmod=lambda p, m: None, getpw=lambda n: self.pw)

    def test_render_install_uses_that_function(self):
        src = (VT / 'render.py').read_text()
        self.assertIn('perms.layout_paths(t)', src)

    def test_the_installer_creates_every_folder_the_v0_layout_check_judges(self):
        """Found by the local rehearsal of the real installer: V0 judged the probe folder before anything had created it, so V0 would have stopped on 'does not exist'."""
        ps = P.layout_paths('/opt/dfl-vtest-src/abc')
        built_elsewhere = {ps['TREE'], ps['ROOT_DIR'] + '/store', ps['ETC'] + '/broadcast.env'}
        made = set(P.install_dirs(ps))
        missing = [p for p, _, _ in P.layout(ps) if p not in built_elsewhere and p not in made]
        self.assertEqual(missing, [])

    def test_render_install_makes_its_folders_from_that_list(self):
        self.assertIn('perms.install_dirs(', (VT / 'render.py').read_text())

    def test_teardown_removes_the_probe_folder_too(self):
        import render as RN

        class FakeEnv:
            def exists(self, p): return True
            def sc(self, *a, **k): return 0, '', ''
        lines = []
        RN.teardown(FakeEnv(), include_evidence=False, yes=False, echo=lines.append)
        self.assertTrue(any(l.split()[-1] == C.PROBE for l in lines if l.startswith('remove tree')), lines)

    def test_the_environment_file_is_roots_and_world_readable_not_writable(self):
        os.makedirs(self.paths['ETC'])
        Path(self.paths['ETC'], 'broadcast.env').write_text('X=1\n')
        self.apply()
        self.assertEqual(self.owned[self.paths['ETC'] + '/broadcast.env'], (0, 0))
        self.assertEqual(self.modes[self.paths['ETC'] + '/broadcast.env'], 0o644)

    def test_it_refuses_a_path_outside_the_harness_folders(self):
        with self.assertRaises(PermissionError):
            self.apply(allowed=lambda p: False)
        self.assertEqual(self.owned, {}, 'nothing was changed before the refusal')

    def test_it_does_not_follow_symlinks_out_of_the_tree(self):
        os.symlink('/etc', self.paths['PUBLIC'] + '/out')
        self.apply()
        self.assertNotIn(self.paths['PUBLIC'] + '/out', self.owned)


class Access(unittest.TestCase):
    def test_must_write_must_not(self):
        probes = {'/w': 'ok', '/n': 'EACCES'}
        self.assertEqual(ids(P.judge_access(probes, ['/w'], ['/n']), 'fail'), set())
        self.assertEqual(ids(P.judge_access({'/w': 'EACCES', '/n': 'EACCES'}, ['/w'], ['/n']), 'fail'), {'V0.access.can_write:/w'})
        self.assertEqual(ids(P.judge_access({'/w': 'ok', '/n': 'ok'}, ['/w'], ['/n']), 'fail'), {'V0.access.cannot_write:/n'})
        self.assertEqual(ids(P.judge_access({'/w': 'ok', '/n': 'ENOENT'}, ['/w'], ['/n']), 'fail'), {'V0.access.cannot_write:/n'}, 'a path that does not exist proves nothing')

    def test_the_probe_creates_nothing_to_clean_up(self):
        src = stage_v0.ACCESS_SNIPPET
        self.assertIn('O_TMPFILE', src)
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        out = subprocess.run([sys.executable, '-I', '-c', src, d], capture_output=True, text=True).stdout.strip()
        self.assertIn(out, ('ok', 'EOPNOTSUPP', 'EINVAL'))           # a filesystem without unnamed files says so (the VM's disk has them)
        self.assertEqual(os.listdir(d), [])
        self.assertEqual(subprocess.run([sys.executable, '-I', '-c', src, d + '/missing'], capture_output=True, text=True).stdout.strip(), 'ENOENT')


class Process(unittest.TestCase):
    STATUS = "Uid:\t995\t995\t995\t995\nGid:\t996\t996\t996\t996\nCapEff:\t0000000000000000\nCapBnd:\t0000000000000000\nNoNewPrivs:\t1\nGroups:\t\n"

    def rows(self, text=None):
        return P.judge_process('daemon', P.parse_proc_status(text or self.STATUS), 995, 996)

    def test_unprivileged_process_passes(self):
        self.assertEqual(ids(self.rows(), 'fail'), set())

    def test_each_defect_is_caught(self):
        for old, new, row in (("Uid:\t995\t995\t995\t995", "Uid:\t995\t995\t0\t995", 'uid'), ("Uid:\t995\t995\t995\t995", "Uid:\t0\t0\t0\t0", 'uid'), ("CapEff:\t0000000000000000", "CapEff:\t0000000000000400", 'no_capabilities'),
                              ("NoNewPrivs:\t1", "NoNewPrivs:\t0", 'no_new_privileges'), ("Groups:\t", "Groups:\t4 24", 'no_supplementary_groups')):
            self.assertIn('V0.process.daemon.' + row, ids(self.rows(self.STATUS.replace(old, new)), 'fail'), new)


class Sandbox(unittest.TestCase):
    TPL = (ROOT / 'deploy/supervisor/dfl-broadcast-daemon.service').read_text()

    def rendered(self):
        return self.TPL.replace('User=dflv2', 'User=' + USER).replace('Group=dflv2', 'Group=' + USER).replace('__ROOT__', '/r').replace('__PUBLIC__', '/p').replace('__GUARD__', '/g').replace('__COMMIT__', 'abc')

    def test_the_production_directives_survive_rendering(self):
        self.assertEqual(P.sandbox_declared(self.TPL, self.rendered()), [])

    def test_dropping_or_changing_a_directive_is_caught(self):
        for line in ('ProtectSystem=strict', 'NoNewPrivileges=true', 'PrivateTmp=true', 'ProtectHome=true'):
            r = self.rendered().replace(line + '\n', '')
            self.assertTrue(any(line.split('=')[0] in p for p in P.sandbox_declared(self.TPL, r)), line)
        r = self.rendered().replace('ProtectSystem=strict', 'ProtectSystem=full')
        self.assertTrue(P.sandbox_declared(self.TPL, r))

    def test_the_applied_sandbox_is_compared_with_the_unit_file(self):
        text = self.rendered()
        shown = {'User': USER, 'Group': USER, 'ProtectSystem': 'strict', 'ProtectHome': 'yes', 'NoNewPrivileges': 'yes', 'PrivateTmp': 'yes', 'OOMPolicy': 'stop', 'UMask': '0022', 'TasksMax': '32',
                 'MemoryMax': '201326592', 'ReadWritePaths': '/r/store /p /g', 'ReadOnlyPaths': '/opt/dflv2-src'}
        self.assertEqual(ids(P.judge_applied_sandbox('daemon', text, shown, USER), 'fail'), set())
        for k, v in (('User', 'root'), ('ProtectSystem', 'no'), ('NoNewPrivileges', 'no'), ('PrivateTmp', 'no'), ('ReadWritePaths', '/r /p /g'), ('ReadWritePaths', '/r/store /p /g /etc'), ('ProtectHome', 'no')):
            self.assertTrue(ids(P.judge_applied_sandbox('daemon', text, dict(shown, **{k: v}), USER), 'fail'), (k, v))

    def test_parse_show(self):
        self.assertEqual(P.parse_show('A=1\nB=x=y\n'), {'A': '1', 'B': 'x=y'})


class KernelLog(unittest.TestCase):
    def test_victim_unknown_to_the_service_is_allowed_but_not_to_the_harness(self):
        rows = P.judge_kernel_log('1', False, 120)
        self.assertEqual(ids(rows, 'fail'), set())
        self.assertEqual({r[1] for r in rows if r[0].endswith('service_user_access')}, {'info'})
        self.assertEqual(ids(P.judge_kernel_log('1', True, 0), 'fail'), {'V0.kernel_log.harness_collects'}, 'the test cannot name the victim without its own collection')
        self.assertEqual(ids(P.judge_kernel_log('0', False, 0), 'fail'), {'V0.kernel_log.harness_collects'})


class MarkerBeforeStart(unittest.TestCase):
    OPEN = {'state': 'open', 'invocation_id': 'inv-9', 'opened_at': 1000.0}

    def test_marker_for_this_invocation_written_before_main_start(self):
        self.assertEqual(ids(P.judge_marker_before_start(self.OPEN, 'inv-9', 1000.5, 0), 'fail'), set())

    def test_defects(self):
        self.assertEqual(ids(P.judge_marker_before_start(self.OPEN, 'inv-8', 1000.5, 0), 'fail'), {'V0.marker.present_for_this_run'})
        self.assertEqual(ids(P.judge_marker_before_start({'state': 'absent'}, 'inv-9', 1000.5, 0), 'fail'), {'V0.marker.present_for_this_run'})
        self.assertEqual(ids(P.judge_marker_before_start(self.OPEN, 'inv-9', 990.0, 0), 'fail'), {'V0.marker.written_before_the_daemon_started'})
        self.assertEqual(ids(P.judge_marker_before_start(self.OPEN, 'inv-9', 1000.5, 1), 'fail'), {'V0.marker.start_check_exited_clean'})

    def test_missing_times_are_incomplete_not_a_pass(self):
        rows = P.judge_marker_before_start(self.OPEN, 'inv-9', None, 0)
        self.assertIn('V0.marker.written_before_the_daemon_started', ids(rows, 'not_run'))

    def test_exec_pre_status_is_read_from_systemctl_show(self):
        self.assertEqual(P.parse_exec_pre_status('ExecStartPre={ path=/usr/bin/python3 ; argv[]=x ; status=0/0 }'), 0)
        self.assertEqual(P.parse_exec_pre_status('ExecStartPre={ path=/x ; status=3/EXCEPTION }'), 3)
        self.assertIsNone(P.parse_exec_pre_status(''))


# ---------------------------------------------------------------------------------------------------------------------------------------------------------- durability, synthetic
G = '/g'


def trace(*steps):
    """steps like ('write', '/g/t') ('fsync', '/g/t') ('fsync', '/g') ('rename', '/g/t', '/g/RUN_OPEN') ('link', a, b) ('unlink', p) -> strace -yy text."""
    out = []
    for i, s in enumerate(steps):
        op, args = s[0], s[1:]
        if op in ('write',):
            out.append('write(3<%s>, "x", 1) = 1' % args[0])
        elif op == 'fsync':
            out.append('fsync(3<%s>) = 0' % args[0])
        elif op in ('rename', 'link'):
            out.append('%s("%s", "%s") = 0' % (op, args[0], args[1]))
        elif op == 'unlink':
            out.append('unlink("%s") = 0' % args[0])
    return "\n".join(out)


WRITE = [('write', G + '/.t'), ('fsync', G + '/.t'), ('rename', G + '/.t', G + '/RUN_OPEN'), ('fsync', G)]
REMOVE = [('unlink', G + '/RUN_OPEN'), ('fsync', G)]
LATCH = [('write', G + '/.l'), ('fsync', G + '/.l'), ('link', G + '/.l', G + '/TRIPPED'), ('fsync', G)]


def ev(steps):
    return D.parse_strace(trace(*steps))


class Durability(unittest.TestCase):
    def test_parse_reads_real_strace_shapes(self):
        text = ('1234  openat(AT_FDCWD, "/g/.t", O_WRONLY|O_CREAT|O_EXCL, 0600) = 3</g/.t>\n[pid 5] fsync(4</g>) = 0\n'
                'rename("/g/.t", "/g/RUN_OPEN") = 0\nunlink("/g/RUN_OPEN") = -1 ENOENT (No such file or directory)\nmmap(NULL, 1) = 0\n')
        e = D.parse_strace(text)
        self.assertEqual([x['op'] for x in e], ['open', 'fsync', 'rename', 'unlink'])
        self.assertFalse(e[3]['ok'])
        self.assertEqual(e[1]['paths'], ['/g'])

    def test_marker_written_in_order_passes_and_each_omission_fails(self):
        self.assertEqual(D.judge_marker_written(ev(WRITE), G), [])
        no_file_fsync = [WRITE[0], WRITE[2], WRITE[3]]
        no_dir_fsync = WRITE[:3]
        no_write = WRITE[1:]
        for steps in (no_file_fsync, no_dir_fsync, no_write, []):
            self.assertTrue(D.judge_marker_written(ev(steps), G), steps)
        # fsync of the folder BEFORE the rename does not count
        self.assertTrue(D.judge_marker_written(ev([WRITE[0], WRITE[1], ('fsync', G), WRITE[2]]), G))

    def test_marker_removal_must_be_followed_by_a_folder_fsync(self):
        self.assertEqual(D.judge_marker_removed(ev(REMOVE), G), [])
        self.assertTrue(D.judge_marker_removed(ev(REMOVE[:1]), G))
        self.assertTrue(D.judge_marker_removed(ev([]), G))
        self.assertTrue(D.judge_marker_removed(ev([('fsync', G)] + REMOVE[:1]), G))

    def test_an_oom_is_durable_before_the_marker_goes(self):
        self.assertEqual(D.judge_removed_after_record(ev(LATCH + REMOVE), G), [])
        self.assertTrue(D.judge_removed_after_record(ev(REMOVE + LATCH), G), 'marker removed first')
        self.assertTrue(D.judge_removed_after_record(ev(LATCH[:3] + REMOVE), G), 'latch not flushed before the removal')
        self.assertTrue(D.judge_removed_after_record(ev(LATCH), G), 'marker never removed')
        self.assertTrue(D.judge_removed_after_record(ev(REMOVE), G), 'no latch')

    def test_a_gate_that_finds_an_open_marker_latches_before_it_replaces_it(self):
        self.assertEqual(D.judge_replaced_after_latch(ev(LATCH + WRITE), G), [])
        self.assertTrue(D.judge_replaced_after_latch(ev(WRITE + LATCH), G))
        self.assertTrue(D.judge_replaced_after_latch(ev(WRITE), G))
        self.assertTrue(D.judge_replaced_after_latch(ev(LATCH[:3] + WRITE), G))

    def test_an_unaccounted_stop_leaves_the_marker_alone(self):
        self.assertEqual(D.judge_marker_untouched(ev(LATCH), G), [])
        self.assertTrue(D.judge_marker_untouched(ev(REMOVE), G))
        self.assertTrue(D.judge_marker_untouched(ev(WRITE), G))
        failed = D.parse_strace('unlink("/g/RUN_OPEN") = -1 EACCES (Permission denied)')
        self.assertEqual(D.judge_marker_untouched(failed, G), [], 'a refused removal did not remove it')

    def test_a_scenario_with_no_trace_is_incomplete_not_a_pass(self):
        s, d, _ = D.judge_scenario('x', 0, [], D.judge_marker_removed, G, 0)
        self.assertEqual(s, 'not_run')

    def test_exit_status_is_part_of_the_scenario(self):
        e = ev(REMOVE)
        self.assertEqual(D.judge_scenario('x', 0, e, D.judge_marker_removed, G, 0)[0], 'pass')
        self.assertEqual(D.judge_scenario('x', 2, e, D.judge_marker_removed, G, 0)[0], 'fail')
        e2 = ev(LATCH)
        self.assertEqual(D.judge_scenario('x', 1, e2, D.judge_marker_untouched, G, 'nonzero')[0], 'pass')
        self.assertEqual(D.judge_scenario('x', 0, e2, D.judge_marker_untouched, G, 'nonzero')[0], 'fail', 'an unrecorded OOM must be reported')

    def test_the_probe_command_points_at_scratch_and_keeps_the_units_own_arguments(self):
        unit = (ROOT / 'deploy/supervisor/dfl-broadcast-daemon.service').read_text().replace('__COMMIT__', 'abc').replace('__ROOT__', '/r').replace('__PUBLIC__', '/p').replace('__GUARD__', '/g')
        sc = {'root': '/s/root', 'public': '/s/pub', 'guard': '/s/guard', 'cgroup': '/s/cg'}
        a = D.probe_command(unit, 'ExecStopPost', sc)
        self.assertEqual(a[a.index('--guard') + 1], '/s/guard')
        self.assertEqual(a[a.index('--cgroup') + 1], '/s/cg')
        self.assertIn('incident', a)
        self.assertNotIn('/g', a)
        b = D.probe_command(unit, 'ExecStartPre', sc)
        self.assertIn('gate', b)
        with self.assertRaises(ValueError):
            D.probe_command('[Service]\n', 'ExecStopPost', sc)

    def test_the_six_scenarios_cover_the_design(self):
        self.assertEqual([s[0] for s in D.SCENARIOS], ['gate_first_start', 'gate_finds_an_open_marker', 'stop_that_was_not_an_oom', 'stop_after_an_oom_recorded',
                                                        'stop_after_an_oom_not_recorded', 'stop_without_a_result_from_systemd'])


# ------------------------------------------------------------------------------------------------------------------------------------------------ durability, real (when this machine can)
@unittest.skipUnless(os.geteuid() == 0 and shutil.which('strace') and shutil.which('setpriv'), 'needs root, strace and setpriv')
class DurabilityReal(unittest.TestCase):
    """The six scenarios against the pinned code itself, as `nobody`, under strace. Proves the probe works on this machine and judges the real program; says nothing about the VM."""

    def setUp(self):
        self.base = tempfile.mkdtemp()
        os.chmod(self.base, 0o755)
        self.addCleanup(shutil.rmtree, self.base, True)

    class Env:
        def as_service(self, argv):
            return ['setpriv', '--reuid', '65534', '--regid', '65534', '--clear-groups', '--'] + argv

        def run(self, argv, timeout=60, input=None):
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
            return p.returncode, p.stdout, p.stderr

        def read(self, p, default=None):
            return Path(p).read_text() if os.path.exists(p) else default

    def unit(self, tree):
        t = (ROOT / 'deploy/supervisor/dfl-broadcast-daemon.service').read_text()
        return t.replace('/opt/dflv2-src/__COMMIT__', tree)

    def test_the_real_gate_and_hook_pass_all_six(self):
        results = {}
        for spec in D.SCENARIOS:
            results[spec[0]] = D.run_scenario(self.Env(), spec, self.unit(str(ROOT)), self.base, 'nobody')
        self.assertEqual({k: v[0] for k, v in results.items()}, {k: 'pass' for k in results}, {k: v[1] for k, v in results.items() if v[0] != 'pass'})

    def test_a_marker_removed_too_early_would_be_caught(self):
        """The judge, fed the real trace of the real program with its marker removal moved before the latch, fails."""
        spec = [s for s in D.SCENARIOS if s[0] == 'stop_after_an_oom_recorded'][0]
        s, d, data = D.run_scenario(self.Env(), spec, self.unit(str(ROOT)), self.base, 'nobody')
        self.assertEqual(s, 'pass', d)
        log = Path(self.base, spec[0], 'strace.log').read_text().splitlines()
        un = [i for i, l in enumerate(log) if 'unlink' in l and 'RUN_OPEN' in l and ' = 0' in l]
        self.assertTrue(un)
        moved = [log[un[0]]] + log[:un[0]] + log[un[0] + 1:]
        self.assertTrue(D.judge_removed_after_record(D.parse_strace("\n".join(moved)), str(Path(self.base, spec[0], 'guard'))))


# ------------------------------------------------------------------------------------------------------------------------------------------------- retests follow the changed behaviour
class RetestSelection(unittest.TestCase):
    def repo(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)

        def g(*a):
            subprocess.run(['git', '-C', d, '-c', 'user.name=t', '-c', 'user.email=t@t', *a], check=True, capture_output=True)
        g('init', '-q')
        for p, t in (('tools/broadcast_run.py', 'a\n'), ('tools/rehearsal_check.py', 'a\n'), ('deploy/supervisor/dfl-broadcast-daemon.service', '# note\nOOMPolicy=stop\nMemoryMax=192M\nProtectSystem=strict\n'),
                     ('tools/vtest/PIN', 'candidate=' + '0' * 40 + '\n'), ('tools/vtest/stage_v3.py', 'a\n'), ('tools/vtest/perms.py', 'a\n'), ('tools/vtest/lifecycle.py', 'a\n'), ('docs/x.md', 'a\n')):
            os.makedirs(os.path.dirname(os.path.join(d, p)), exist_ok=True)
            Path(d, p).write_text(t)
        g('add', '-A')
        g('commit', '-q', '-m', 'base')
        base = subprocess.run(['git', '-C', d, 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
        return d, g, base

    def after(self, change):
        d, g, base = self.repo()
        for path, text in change.items():
            Path(d, path).write_text(text)
        g('commit', '-qam', 'change')
        a = C.affected(d, base)
        return a['repeat'], a['code_changed']

    def test_the_marker_and_hook_code_leaves_the_memory_rule_stage_alone(self):
        self.assertEqual(self.after({'tools/broadcast_run.py': 'b\n'}), (['V0', 'V2', 'V3', 'V4'], True))

    def test_the_monitor_code_repeats_everything_it_runs_in(self):
        self.assertEqual(self.after({'tools/rehearsal_check.py': 'b\n'}), (list(C.STAGES), True))

    def test_a_judgment_file_repeats_only_the_stages_that_use_it(self):
        self.assertEqual(self.after({'tools/vtest/perms.py': 'b\n'}), (['V0'], False))
        self.assertEqual(self.after({'tools/vtest/lifecycle.py': 'b\n'}), (['V2', 'V3', 'V4'], False))
        self.assertEqual(self.after({'tools/vtest/stage_v3.py': 'b\n'}), (['V3'], False))

    def test_a_unit_comment_repeats_nothing_a_lifecycle_line_repeats_the_lifecycle_stages_a_limit_adds_the_memory_stage(self):
        u = 'deploy/supervisor/dfl-broadcast-daemon.service'
        self.assertEqual(self.after({u: '# other note\nOOMPolicy=stop\nMemoryMax=192M\nProtectSystem=strict\n'}), ([], True))
        self.assertEqual(self.after({u: '# note\nOOMPolicy=kill\nMemoryMax=192M\nProtectSystem=strict\n'}), (['V0', 'V2', 'V3', 'V4'], True))
        self.assertEqual(self.after({u: '# note\nOOMPolicy=stop\nMemoryMax=256M\nProtectSystem=strict\n'}), (list(C.STAGES), True))

    def test_an_unanalysed_unit_line_or_file_repeats_everything_and_says_so(self):
        u = 'deploy/supervisor/dfl-broadcast-daemon.service'
        rep, code = self.after({u: '# note\nOOMPolicy=stop\nMemoryMax=192M\nProtectSystem=full\n'})
        self.assertEqual((rep, code), (list(C.STAGES), True))
        rep, _ = self.after({'tools/vtest/PIN': 'candidate=' + '1' * 40 + '\n'})
        self.assertEqual(rep, list(C.STAGES))

    def test_the_reason_is_given_per_file(self):
        d, g, base = self.repo()
        Path(d, 'tools/broadcast_run.py').write_text('b\n')
        g('commit', '-qam', 'c')
        why = C.affected(d, base)['why']['tools/broadcast_run.py']
        self.assertEqual(why[0], ['V0', 'V2', 'V3', 'V4'])
        self.assertIn('monitor', why[1])


# ------------------------------------------------------------------------------------------------------------------------------------------------------------------ Env and install
class EnvAndInstall(unittest.TestCase):
    def test_service_commands_run_as_the_service_user_without_groups(self):
        e = C.Env()
        self.assertEqual(e.as_service(['x']), ['x'], 'without a service user nothing is wrapped')
        e.service_user = 'nobody'
        a = e.as_service(['/bin/true'])
        self.assertEqual(a[:2], ['setpriv', '--reuid'])
        self.assertIn('--clear-groups', a)
        self.assertEqual(a[-1], '/bin/true')

    @unittest.skipUnless(os.geteuid() == 0 and shutil.which('setpriv'), 'needs root and setpriv')
    def test_operator_really_drops_privileges(self):
        e = C.Env()
        e.service_user = 'nobody'
        rc, out, _ = e.operator(['id', '-u'])
        self.assertEqual((rc, out.strip()), (0, '65534'))
        rc, out, _ = e.operator(['id', '-G'])
        self.assertEqual(out.strip(), '65534')

    def test_install_refuses_root_and_a_missing_user(self):
        import render as RN
        from tests.test_vtest import FakeEnv
        e = FakeEnv()
        with self.assertRaises(C.Unsafe):
            RN.install(e, str(ROOT), user='root')
        e.missing_users = ('ghost',)
        with self.assertRaises(C.Unsafe):
            RN.install(e, str(ROOT), user='ghost')

    def test_run_refuses_an_install_without_a_user_or_from_another_harness_revision(self):
        import vtest
        from tests.test_vtest import FakeEnv
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        for inst, want in (({'pin': C.read_pin()}, 'unprivileged service user'), ({'pin': C.read_pin(), 'user': 'root', 'harness_rev': C.harness_rev()}, 'unprivileged service user'),
                           ({'pin': C.read_pin(), 'user': 'dfl-vtest', 'harness_rev': '0' * 40}, 'harness revision')):
            with mock.patch.object(C, 'BASE', tmp), mock.patch.object(C, 'EVID', tmp + '/evidence'), mock.patch.object(C, 'STATE', tmp + '/state'), \
                    mock.patch.object(vtest.C, 'pin_check', lambda *a, **k: (True, [])), mock.patch.object(vtest, 'constants_check', lambda *a: []), \
                    mock.patch.object(vtest.PF, 'preflight', lambda env: [('x', 'pass', '')]), mock.patch('builtins.print') as pr:
                os.makedirs(tmp + '/state', exist_ok=True)
                Path(tmp, 'state', 'install.json').write_text(json.dumps(inst))
                code = vtest.main(['run', '--stages', 'V0', '--run-id', 'r1'])
                self.assertEqual(code, 1)
                self.assertIn(want, ' '.join(str(c.args[0]) for c in pr.call_args_list))
                self.assertFalse(os.path.exists(tmp + '/evidence/r1'))

    def test_preflight_requires_strace_setpriv_and_the_service_user(self):
        import preflight as PF
        from tests.test_vtest import FakeEnv
        e = FakeEnv()
        rows = {r[0]: r[1] for r in PF.preflight(e)}
        self.assertTrue(any('strace' in k for k in rows))
        e.missing_tools = ('strace',)
        e.missing_users = (C.SERVICE_USER,)
        bad = {k for k, v in {r[0]: r[1] for r in PF.preflight(e)}.items() if v == 'fail'}
        self.assertTrue(any('strace' in k for k in bad), bad)
        self.assertTrue(any('user' in k for k in bad), bad)


# ------------------------------------------------------------------------------------------------------------------------------------------------------ the V0 stage's glue, on a scripted machine
class V0Machine:
    """A machine that satisfies the design; each test changes one thing."""
    UID, GID = 995, 996

    def __init__(self):
        self.t = 5000.0
        self.service_user_can_write = {PATHS['ROOT_DIR'] + '/store', PATHS['PUBLIC'], PATHS['GUARD']}
        self.status = "Uid:\t995\t995\t995\t995\nGid:\t996\t996\t996\t996\nCapEff:\t0000000000000000\nCapBnd:\t0000000000000000\nNoNewPrivs:\t1\nGroups:\t\n"
        self.marker_closed_by_stop = True
        self.marker_opened_at = 4990.0
        self.invocation = 'inv-7'
        self.kernel_lines = 'kernel: x\n' * 30
        self.can_read_dmesg = False
        self.calls = []
        import render as RN
        self.unit = RN.render_unit((ROOT / 'deploy/supervisor/dfl-broadcast-daemon.service').read_text(), C.read_pin(), USER)[0]
        self.monitor = RN.render_unit((ROOT / 'deploy/supervisor/dfl-broadcast-monitor.service').read_text(), C.read_pin(), USER)[0]
        self.stopped = False

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s

    def monotonic(self):
        return self.t

    def read(self, p, default=None):
        if p.endswith('install.json'):
            return json.dumps({'user': USER})
        if p.endswith('dfl-vtest-daemon.service'):
            return self.unit
        if p.endswith('dfl-vtest-monitor.service'):
            return self.monitor
        if p.endswith('dfl-broadcast-daemon.service'):
            return (ROOT / 'deploy/supervisor/dfl-broadcast-daemon.service').read_text()
        if p.endswith('dfl-broadcast-monitor.service'):
            return (ROOT / 'deploy/supervisor/dfl-broadcast-monitor.service').read_text()
        if p.startswith('/proc/') and p.endswith('/status'):
            return self.status
        if p.endswith('dmesg_restrict'):
            return '1\n'
        return default

    def operator(self, argv, timeout=60, input=None):
        if argv[0] == 'dmesg':
            return (0, 'kernel: x\n', '') if self.can_read_dmesg else (1, '', 'dmesg: read kernel buffer failed: Operation not permitted')
        if argv[0] == 'journalctl':
            return 1, '', 'not seeing messages'
        path = argv[-1]
        return 0, ('ok' if path in self.service_user_can_write else 'EACCES') + '\n', ''

    def sc(self, verb, *a, timeout=60):
        self.calls.append((verb, a))
        if verb == 'stop':
            self.stopped = True
        if verb == 'start':
            self.stopped = False
        if verb == 'show' and a and 'InvocationID' in " ".join(a):
            return 0, 'InvocationID=%s\nExecMainStartTimestampMonotonic=%d\nExecStartPre={ path=/usr/bin/python3 ; status=0/0 }\n' % (self.invocation, int(5000.0 * 1e6)), ''
        if verb == 'show':
            text = self.unit if 'daemon' in a[0] else self.monitor
            v = {k: ' '.join(P.unit_file_values(text).get(k, [''])) for k in P.SANDBOX_SCALARS + P.SANDBOX_PATHS}
            v.update(User=USER, Group=USER, ProtectHome='yes', NoNewPrivileges='yes', PrivateTmp='yes')
            v['MemoryMax'] = '0'
            return 0, "\n".join('%s=%s' % kv for kv in v.items()), ''
        return 0, '', ''

    def run(self, argv, timeout=60, input=None):
        return 0, 'u 995 g 996', ''


class V0Stage(unittest.TestCase):
    def go(self, m=None, user_facts=None, observed=None, writable_tree=(), kernel=None, scenarios_status='pass'):
        import scenarios
        m = m or V0Machine()
        rows = []

        class Rec:
            def check(self, cid, status, detail, data=None, required=True):
                rows.append((cid, status, detail))

            def add_all(self, rs):
                for r in rs:
                    self.check(*r)

            def stop_if_failed(self):
                bad = [r for r in rows if r[1] in ('fail', 'invalid')]
                if bad:
                    raise C.StageStop('%s: %s' % (bad[0][0], bad[0][2]))
        rec = Rec()
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        ctx = scenarios.Ctx(m, rec, dict(stage_v0.DEFAULTS['smoke'], mode='smoke'), PATHS['TREE'], d)
        ctx.ensure_baseline = lambda reason='': None
        ctx.kernel_log = lambda since: m.kernel_lines
        ctx.show = lambda k, props=(): {'MainPID': 4242, 'ActiveState': 'active'}
        ctx.active = lambda k: not m.stopped
        ctx.run_marker = lambda: ({'state': 'absent'} if m.stopped and m.marker_closed_by_stop else {'state': 'open', 'invocation_id': m.invocation, 'opened_at': m.marker_opened_at})
        good = {'name': USER, 'exists': True, 'uid': V0Machine.UID, 'gid': V0Machine.GID, 'shell': '/usr/sbin/nologin', 'groups': [USER]}
        obs = {}
        for path, who, mode in P.layout(PATHS):
            obs[path] = {'owner': 'root' if who == 'root' else USER, 'mode': 0o755 if mode == 'noworld' else mode}
        obs.update(observed or {})
        err = None
        with mock.patch.object(stage_v0, 'user_facts', lambda env, name: user_facts or good), mock.patch.object(P, 'observe', lambda paths, **k: obs), \
                mock.patch.object(P, 'tree_writable_by_others', lambda t: list(writable_tree)), mock.patch.object(stage_v0.D, 'run_scenario', lambda *a, **k: (scenarios_status, 'x', {})), \
                mock.patch.object(stage_v0.D, 'SCENARIOS', stage_v0.D.SCENARIOS[:2]), mock.patch.object(stage_v0.os, 'makedirs', lambda *a, **k: None):
            try:
                stage_v0.run(ctx)
            except C.StageStop as e:
                err = e
        return m, rows, err

    def failing(self, rows):
        return {r[0] for r in rows if r[1] in ('fail', 'invalid')}

    def test_a_machine_that_follows_the_design_passes_every_row(self):
        m, rows, err = self.go()
        self.assertIsNone(err)
        self.assertEqual(self.failing(rows), set())
        self.assertEqual({r[1] for r in rows if r[0].startswith('V0.kernel_log.service_user_access')}, {'info'})
        got = {r[0].split('.')[1].split(':')[0] for r in rows}
        self.assertEqual(got, {'user', 'layout', 'access', 'units', 'kernel_log', 'process', 'marker', 'durability'} | {'sandbox'})

    def test_every_service_that_runs_as_root_or_the_wrong_user_stops_the_stage_early(self):
        m, rows, err = self.go(user_facts={'name': USER, 'exists': False})
        self.assertIsNotNone(err)
        self.assertEqual(self.failing(rows), {'V0.user.exists'})
        self.assertFalse(any(r[0].startswith('V0.layout') for r in rows), 'nothing else is judged against a user that is not there')

    def test_a_service_user_that_can_write_the_code_or_the_evidence_fails(self):
        for bad in (PATHS['TREE'], PATHS['EVID'], PATHS['BASE'], PATHS['STATE'], PATHS['PROBE']):
            m = V0Machine()
            m.service_user_can_write.add(bad)
            _, rows, err = self.go(m)
            self.assertIn('V0.access.cannot_write:' + bad, self.failing(rows), bad)
            self.assertIsNotNone(err)

    def test_a_service_user_that_cannot_write_its_own_folders_fails(self):
        m = V0Machine()
        m.service_user_can_write.discard(PATHS['GUARD'])
        _, rows, err = self.go(m)
        self.assertEqual(self.failing(rows), {'V0.access.can_write:' + PATHS['GUARD']})

    def test_a_pinned_tree_writable_by_others_fails(self):
        _, rows, err = self.go(writable_tree=[PATHS['TREE'] + '/tools/x.py'])
        self.assertEqual(self.failing(rows), {'V0.layout.code_read_only'})

    def test_a_process_with_root_or_capabilities_fails_and_the_stage_stops_before_the_marker_step(self):
        m = V0Machine()
        m.status = m.status.replace('CapEff:\t0000000000000000', 'CapEff:\t0000000000000400')
        _, rows, err = self.go(m)
        self.assertIn('V0.process.daemon.no_capabilities', self.failing(rows))
        self.assertFalse(any(r[0].startswith('V0.marker') for r in rows))

    def test_a_clean_stop_that_leaves_the_marker_open_fails(self):
        m = V0Machine()
        m.marker_closed_by_stop = False
        _, rows, err = self.go(m)
        self.assertIn('V0.marker.closed_by_a_clean_stop', self.failing(rows))

    def test_a_marker_opened_after_the_daemon_started_or_for_another_invocation_fails(self):
        m = V0Machine()
        m.marker_opened_at = 5100.0
        self.assertIn('V0.marker.written_before_the_daemon_started', self.failing(self.go(m)[1]))
        m = V0Machine()
        m.invocation = 'inv-other'
        m2 = V0Machine()
        m2.invocation = 'inv-7'
        m.marker_closed_by_stop = True
        _, rows, _ = self.go(m)
        self.assertEqual(self.failing(rows), set(), 'the marker view follows the machine, so a consistent machine passes')

    def test_a_harness_that_cannot_read_the_kernel_log_fails_but_a_service_that_cannot_does_not(self):
        m = V0Machine()
        m.kernel_lines = ''
        self.assertIn('V0.kernel_log.harness_collects', self.failing(self.go(m)[1]))
        m = V0Machine()
        m.can_read_dmesg = True
        self.assertEqual(self.failing(self.go(m)[1]), set())

    def test_a_failed_durability_scenario_fails_the_stage_row(self):
        _, rows, _ = self.go(scenarios_status='fail')
        self.assertTrue(any(r[0].startswith('V0.durability.') and r[1] == 'fail' for r in rows))

    def test_the_stage_never_writes_anywhere_itself(self):
        m = V0Machine()
        m.write = mock.Mock()
        self.go(m)
        m.write.assert_not_called()
        verbs = {v for v, _ in m.calls}
        self.assertTrue(verbs <= {'show', 'stop', 'start', 'reset-failed'}, verbs)


if __name__ == '__main__':
    unittest.main()
