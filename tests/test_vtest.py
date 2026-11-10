"""The validation harness (tools/vtest/, docs/MEMORY_MONITOR_VALIDATION_PLAN.md). What can be tested without systemd is tested here: the pin and the change-impact rule, the preflight on fake
machines, the safety rules, the unit rendering, every stage's pure evaluator on synthetic samples, the recorder and the stop-on-failure rule, the loaders and the sampler.
NOT tested here, and said so: that the stages behave under real systemd and cgroup v2. That is what running the harness on a disposable machine is for."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
VT = ROOT / 'tools' / 'vtest'
sys.path.insert(0, str(VT))
sys.path.insert(0, str(ROOT / 'tools'))
import common as C                      # noqa: E402
import broadcast_probe as BP            # noqa: E402
import evaluate as E                    # noqa: E402
import evidence as EV                   # noqa: E402
import preflight as PF                  # noqa: E402
import render as RN                     # noqa: E402
import sampler as SM                    # noqa: E402
import stage_v1, stage_v2, stage_v3, stage_v4   # noqa: E402

MB = 1_000_000
MX = 192 * 1024 * 1024


def rec(t, current, pinned, anon=30 * MB, mx=MX, events=None, stat=None, full60=0.0, some60=0.0, with_pressure=True, readable=True):
    s = dict(current=current, max=mx, anon=anon, file=max(0, current - anon), kernel=0,
             pressure={"source": "cgroup", "some": {"avg10": some60, "avg60": some60, "avg300": 0, "total": 5}, "full": {"avg10": full60, "avg60": full60, "avg300": 0, "total": 5}} if with_pressure else None)
    return {"t": t, "exists": readable, "readable": readable, "sample": s, "pinned": pinned, "reclaimable": current - pinned, "events": events or {"max": 0, "oom": 0, "oom_kill": 0}, "stat": stat or {"pgscan": 0, "pgsteal": 0}}


class Pin(unittest.TestCase):
    def test_pin_is_the_approved_candidate(self):
        self.assertEqual(C.read_pin(), 'ecf62ea6c709903ff6a6eb8d95426834803da8dc')
        self.assertEqual(subprocess.run(['git', '-C', str(ROOT), 'cat-file', '-t', C.read_pin()], capture_output=True, text=True).stdout.strip(), 'commit')

    def test_the_code_under_test_has_not_changed_since_the_pin(self):
        a = C.affected(str(ROOT), C.read_pin())
        self.assertFalse(a['code_changed'], a['changed'])

    def test_the_guard_constants_the_checks_use_are_the_pinned_trees(self):
        import vtest
        self.assertEqual(vtest.constants_check(str(ROOT), C.read_pin()), [])

    def test_classification(self):
        cases = {'docs/anything.md': ('docs', set()), 'tests/test_other.py': ('none', set()), 'tests/test_season.py': ('code', set(C.STAGES)), 'tests/fixtures/x.json': ('code', set(C.STAGES)),
                 'tools/rehearsal_check.py': ('code', set(C.STAGES)), 'tools/broadcast_run.py': ('code', set(C.STAGES)), 'src/dflsite/season.py': ('code', set(C.STAGES)), 'deploy/supervisor/dfl-broadcast-daemon.service': ('code', set(C.STAGES)),
                 'tools/something_new.py': ('code', set(C.STAGES)), 'tools/vtest/stage_v2.py': ('harness', {'V2'}), 'tools/vtest/stage_v4.py': ('harness', {'V4'}), 'tools/vtest/loaders.py': ('harness', set(C.STAGES)),
                 'tools/vtest/sampler.py': ('harness', set(C.STAGES)), 'tools/vtest/PIN': ('harness', set(C.STAGES)), 'tools/vtest/vtest.py': ('none', set()), 'tools/vtest/preflight.py': ('none', set()), 'README.md': ('none', set())}
        for path, want in cases.items():
            self.assertEqual(C.classify(path), want, path)

    def _repo(self):
        d = tempfile.mkdtemp()
        self.addCleanup(subprocess.run, ['rm', '-rf', d])
        def g(*a):
            subprocess.run(['git', '-C', d, '-c', 'user.name=t', '-c', 'user.email=t@t', *a], check=True, capture_output=True)
        g('init', '-q')
        for p in ('docs/a.md', 'tools/rehearsal_check.py', 'tools/vtest/PIN', 'tools/vtest/stage_v2.py', 'tools/vtest/vtest.py', 'tests/test_x.py', 'src/m.py'):
            os.makedirs(os.path.dirname(os.path.join(d, p)), exist_ok=True)
            Path(d, p).write_text('v1\n')
        Path(d, 'tools/vtest/PIN').write_text('candidate=' + '0' * 40 + '\n')
        g('add', '-A')
        g('commit', '-q', '-m', 'pin')
        pin = subprocess.run(['git', '-C', d, 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
        return d, g, pin

    def test_documentation_only_changes_invalidate_nothing_and_code_changes_invalidate_everything(self):
        d, g, pin = self._repo()
        Path(d, 'docs/a.md').write_text('v2\n'); Path(d, 'tests/test_x.py').write_text('v2\n'); Path(d, 'tools/vtest/vtest.py').write_text('v2\n')
        g('commit', '-qam', 'docs, a test, the driver')
        a = C.affected(d, pin)
        self.assertEqual((a['repeat'], a['code_changed']), ([], False))
        Path(d, 'tools/vtest/stage_v2.py').write_text('v2\n')
        g('commit', '-qam', 'stage 2 harness')
        a = C.affected(d, pin)
        self.assertEqual((a['repeat'], a['code_changed']), (['V2'], False))
        Path(d, 'tools/rehearsal_check.py').write_text('v2\n')
        g('commit', '-qam', 'code')
        a = C.affected(d, pin)
        self.assertEqual((a['repeat'], a['code_changed']), (list(C.STAGES), True))

    def test_pin_check_refuses_changed_code_and_a_dirty_tree(self):
        d, g, pin = self._repo()
        self.assertTrue(C.pin_check(d, pin)[0])
        Path(d, 'src/m.py').write_text('v2\n')
        ok, msgs = C.pin_check(d, pin)
        self.assertFalse(ok)
        self.assertIn('not clean', ' '.join(msgs))
        g('commit', '-qam', 'code')
        ok, msgs = C.pin_check(d, pin)
        self.assertFalse(ok)
        self.assertIn('code under test differs', ' '.join(msgs))


class FakeEnv:
    """A machine as a dict of files and a few command answers."""
    def __init__(self, files=None, units='', sysrun='running', ver='systemd 255 (255.4)', free=50 * 2**30, uid=0):
        self.files, self.units, self.sysrun, self.ver, self.free, self.uid = dict(files or {}), units, sysrun, ver, free, uid

    def read(self, p, default=None):
        return self.files.get(p, default)

    def exists(self, p):
        return p in self.files or any(k.startswith(p + '/') for k in self.files)

    def euid(self):
        return self.uid

    def free_bytes(self, p):
        return self.free

    def sc(self, verb, *a, timeout=60):
        if verb == 'is-system-running':
            return 0, self.sysrun + '\n', ''
        if verb == '--version':
            return 0, self.ver + '\n', ''
        if verb == 'list-unit-files':
            return 0, self.units, ''
        if verb == 'list-units':
            return 0, '', ''
        return 0, '', ''

    def run(self, argv, timeout=60, input=None):
        return 0, 'True\n', ''


def good_files():
    return {C.MARKER: 'x', '/proc/1/comm': 'systemd\n', '/run/systemd/system/x': '', '/proc/mounts': 'cgroup2 /sys/fs/cgroup cgroup2 rw,nosuid,nodev,noexec 0 0\n', '/sys/fs/cgroup/cgroup.controllers': 'cpuset cpu io memory pids\n',
            '/sys/fs/cgroup/cgroup.subtree_control': 'cpu io memory pids\n', '/sys/fs/cgroup/system.slice/cgroup.controllers': 'cpu memory pids\n', '/proc/swaps': 'Filename Type Size Used Priority\n',
            '/proc/sys/kernel/osrelease': '6.8.0-1\n', '/sys/fs/cgroup/memory.pressure': 'x', '/proc/pressure/memory': 'x', '/proc/meminfo': 'MemTotal:  2039000 kB\n', '/proc/cpuinfo': 'processor : 0\nprocessor : 1\n'}


class Preflight(unittest.TestCase):
    def status(self, env):
        rows = PF.preflight(env)
        return PF.ready(rows), {c: s for c, s, _ in rows}

    def test_a_proper_disposable_machine_is_ready(self):
        ok, st = self.status(FakeEnv(good_files()))
        self.assertTrue(ok, st)

    def test_each_missing_requirement_is_a_failure_of_its_own_check(self):
        cases = {'disposable_marker': lambda f: f.pop(C.MARKER), 'systemd_pid1': lambda f: f.update({'/proc/1/comm': 'bash\n'}), 'cgroup_v2': lambda f: f.update({'/proc/mounts': 'cgroup /sys/fs/cgroup/memory cgroup rw 0 0\n'}),
                 'cgroup_writable': lambda f: f.update({'/proc/mounts': 'cgroup2 /sys/fs/cgroup cgroup2 ro,nosuid 0 0\n'}), 'memory_controller': lambda f: f.update({'/sys/fs/cgroup/cgroup.subtree_control': 'cpu io\n'}),
                 'swap_off': lambda f: f.update({'/proc/swaps': 'Filename Type Size Used Priority\n/dev/sda2 partition 100 0 -2\n'}), 'ram': lambda f: f.update({'/proc/meminfo': 'MemTotal:  400000 kB\n'})}
        for cid, mutate in cases.items():
            f = good_files()
            mutate(f)
            ok, st = self.status(FakeEnv(f))
            self.assertFalse(ok, cid)
            self.assertEqual(st[cid], 'fail', (cid, st))

    def test_old_systemd_non_root_and_little_disk_fail(self):
        for env, cid in ((FakeEnv(good_files(), ver='systemd 237 (237)'), 'systemd_version'), (FakeEnv(good_files(), uid=1000), 'root'), (FakeEnv(good_files(), free=2 * 2**30), 'disk'), (FakeEnv(good_files(), sysrun='offline'), 'systemd_state')):
            ok, st = self.status(env)
            self.assertEqual(st[cid], 'fail', cid)

    def test_a_machine_with_real_dfl_units_or_paths_is_refused(self):
        for env in (FakeEnv(good_files(), units='dfl-broadcast-daemon.service enabled enabled\n'), FakeEnv(dict(good_files(), **{'/srv/dflv2/feed/x': ''})), FakeEnv(dict(good_files(), **{'/etc/systemd/system/dfl-rehearsal.service': ''}))):
            ok, st = self.status(env)
            self.assertEqual(st['not_production'], 'fail')
            self.assertFalse(ok)

    def test_no_kernel_reclaim_or_pressure_is_a_warning_not_a_failure(self):
        f = good_files()
        f['/proc/sys/kernel/osrelease'] = '5.4.0\n'
        f.pop('/proc/pressure/memory'); f.pop('/sys/fs/cgroup/memory.pressure')
        ok, st = self.status(FakeEnv(f))
        self.assertTrue(ok, st)
        self.assertEqual((st['memory_reclaim'], st['psi']), ('warn', 'warn'))

    def test_preflight_changes_nothing(self):
        src = (VT / 'preflight.py').read_text()
        self.assertNotRegex(src, r'env\.write|cg_write|os\.makedirs|unlink|rmtree|"start"|"stop"')


class Safety(unittest.TestCase):
    def test_writes_are_confined(self):
        e = C.Env()
        for bad in ('/etc/passwd', '/srv/dflv2/feed/x', '/etc/systemd/system/dfl-broadcast-daemon.service', '/tmp/x', C.BASE + '/../../etc/x'):
            with self.assertRaises(C.Unsafe, msg=bad):
                e.write(bad, 'x')
        for good in (C.BASE + '/state/x', C.UNIT_DIR + '/dfl-vtest-daemon.service', C.ETC + '/broadcast.env', C.SRC_BASE + '/abc/x'):
            self.assertTrue(C.allowed_path(good), good)

    def test_systemctl_may_change_only_vtest_units(self):
        e = C.Env()
        for verb, unit in (('stop', 'dfl-broadcast-daemon.service'), ('restart', 'sshd.service'), ('kill', 'dfl-rehearsal.service'), ('start', 'dfl-vtest-daemon')):
            with self.assertRaises(C.Unsafe, msg=(verb, unit)):
                e.sc(verb, unit)
        with self.assertRaises(C.Unsafe):
            e.sc('stop')
        with self.assertRaises(C.Unsafe):
            e.sc('poweroff')
        with self.assertRaises(C.Unsafe):
            e.sc('set-property', 'dfl-vtest-daemon.service', 'MemoryMax=1')

    def test_cgroup_writes_are_two_files_under_vtest_units(self):
        e = C.Env()
        for bad in ('/sys/fs/cgroup/system.slice/dfl-broadcast-daemon.service/memory.reclaim', '/sys/fs/cgroup/system.slice/dfl-vtest-daemon.service/memory.max', '/sys/fs/cgroup/memory.reclaim',
                    '/sys/fs/cgroup/system.slice/dfl-vtest-daemon.service/x/cgroup.procs'):
            with self.assertRaises(C.Unsafe, msg=bad):
                e.cg_write(bad, '1')

    def test_no_stage_deletes_anything(self):
        for f in VT.glob('*.py'):
            if f.name in ('render.py', 'selftest.py'):
                continue
            src = f.read_text()
            self.assertNotRegex(src, r'rmtree|os\.unlink|os\.remove|\brm -|shutil\.move', f.name)


class Render(unittest.TestCase):
    PIN = 'ecf62ea6c709903ff6a6eb8d95426834803da8dc'

    def templates(self):
        return {n: (ROOT / p).read_text() for n, p in (('daemon', RN.DAEMON_TEMPLATE), ('monitor', RN.MONITOR_TEMPLATE))}

    def test_units_are_the_templates_with_only_the_layout_changed(self):
        for name, tpl in self.templates().items():
            text, changes = RN.render_unit(tpl, self.PIN)
            self.assertEqual(RN.invariants(tpl, text), [], name)
            self.assertTrue(RN.exec_flags_unchanged(tpl, text), name)
            self.assertNotRegex(text, r'__[A-Z_]+__')
            for forbidden in ('/opt/dflv2-src', '/etc/dflv2', '/srv/dflv2', 'diamondfootballleague', 'https://'):
                self.assertNotIn(forbidden, text, (name, forbidden))
            self.assertNotRegex(text, r'(?m)^(User|Group)=')
            self.assertNotIn('[Install]', text)
            self.assertIn('MemoryMax=192M' if name == 'daemon' else 'MemoryMax=512M', text)
            self.assertIn('OOMPolicy=stop', text)
            self.assertIn('Restart=on-failure', text)
        mon, _ = RN.render_unit(self.templates()['monitor'], self.PIN)
        self.assertIn('--cgroup /sys/fs/cgroup/system.slice/dfl-vtest-daemon.service', mon)
        self.assertIn('--url http://127.0.0.1:%d' % C.PORT, mon)

    def test_a_tampered_limit_or_flag_is_caught(self):
        tpl = self.templates()['daemon']
        text, _ = RN.render_unit(tpl, self.PIN)
        self.assertNotEqual(RN.invariants(tpl, text.replace('MemoryMax=192M', 'MemoryMax=600M')), [])
        self.assertNotEqual(RN.invariants(tpl, text.replace('OOMPolicy=stop', 'OOMPolicy=continue')), [])
        self.assertNotEqual(RN.invariants(tpl, text.replace('RestartSec=5', 'RestartSec=1')), [])
        mon = self.templates()['monitor']
        mtext, _ = RN.render_unit(mon, self.PIN)
        self.assertFalse(RN.exec_flags_unchanged(mon, mtext.replace('--cgroup', '--nocgroup')))

    def test_a_user_is_set_only_when_asked(self):
        text, _ = RN.render_unit(self.templates()['daemon'], self.PIN, user='vtestuser')
        self.assertIn('User=vtestuser', text)

    def test_env_file_lowers_the_disk_floor_to_fit_a_small_machine(self):
        text, floor = RN.env_file(8.0)
        self.assertEqual(floor, 4)
        self.assertIn('FLOOR_BROADCAST_GB=4', text)
        self.assertEqual(RN.env_file(100.0)[1], 6)

    def test_the_pinned_tree_is_extracted_and_verified_against_git(self):
        d = tempfile.mkdtemp()
        self.addCleanup(subprocess.run, ['rm', '-rf', d])
        with mock.patch.object(C, 'SRC_BASE', d), mock.patch.object(RN, 'SRC_BASE', d):
            ok, msgs = RN.extract_tree(C.Env(), str(ROOT), self.PIN)
            self.assertTrue(ok, msgs)
            self.assertIn('files match', msgs[0])
            self.assertTrue(os.path.exists(os.path.join(d, self.PIN[:12], 'tools', 'rehearsal_check.py')))
            ok2, msgs2 = RN.extract_tree(C.Env(), str(ROOT), self.PIN)
            self.assertFalse(ok2)
            self.assertIn('refusing to overwrite', msgs2[0])

    def test_teardown_prints_and_does_nothing_without_yes(self):
        lines = []
        env = mock.Mock()
        env.sc.return_value = (0, 'dfl-vtest-daemon.service enabled\n', '')
        env.exists.side_effect = lambda p: p == C.STATE
        self.assertEqual(RN.teardown(env, yes=False, echo=lines.append), 0)
        self.assertTrue(any('dry run' in l for l in lines))
        self.assertFalse([c for c in env.sc.call_args_list if c[0][0] in ('stop', 'daemon-reload')])


ACTIVE = {'ActiveState': 'active', 'NRestarts': 0}


class V1Evaluator(unittest.TestCase):
    CFG = dict(stage_v1.DEFAULTS['qualifying'], need_above_s=100)

    def healthy(self, n=200):
        out = []
        for i in range(n):
            out.append(rec(1000 + i, current=int(MX * 0.99), pinned=45 * MB, events={'max': i * 40, 'oom': 0, 'oom_kill': 0}, stat={'pgscan': i * 900, 'pgsteal': i * 800}, full60=0.3, some60=0.5))
        return out

    def run_eval(self, load=None, base=None, probes=None, latch=None, loader=None, watch=None, moved=True, after=None, cfg=None):
        base = base or [rec(900 + i, current=40 * MB, pinned=36 * MB) for i in range(30)]
        st = {'ActiveState': 'active', 'NRestarts': 0}
        loader = loader or {'alive': True, 'rss_peak': 14 * MB, 'bytes_read_last_60s': 10 ** 9}
        watch = watch or {'violations': [], 'heartbeats': 10, 'mem_lines': 10, 'mem_consistent': 10}
        probes = [{'ok': True}] * 10 if probes is None else probes
        rows = stage_v1.evaluate_v1(load or self.healthy(), base, probes, {'daemon': st, 'monitor': st}, after or {'daemon': st, 'monitor': st}, latch, loader, watch, moved, cfg or self.CFG)
        return {r[0]: r[1] for r in rows}

    def test_a_healthy_run_passes_every_check(self):
        st = self.run_eval()
        self.assertEqual(set(st.values()), {'pass', 'info'}, st)
        self.assertEqual(st['V1.pressure_diagnostic'], 'info')

    def test_a_workload_that_never_reached_the_limit_is_invalid_not_a_pass(self):
        low = [rec(1000 + i, current=int(MX * 0.5), pinned=45 * MB, stat={'pgscan': 0, 'pgsteal': 0}) for i in range(200)]
        st = self.run_eval(load=low)
        self.assertEqual((st['V1.workload_was_real'], st['V1.kernel_reclaimed']), ('invalid', 'invalid'))

    def test_a_latch_or_a_violation_or_a_restart_fails_it(self):
        self.assertEqual(self.run_eval(latch={'kind': 'memory_high'})['V1.no_trip'], 'fail')
        self.assertEqual(self.run_eval(watch={'violations': [{'violation': 'memory_high'}], 'heartbeats': 10, 'mem_lines': 10, 'mem_consistent': 10})['V1.no_trip'], 'fail')
        st = {'ActiveState': 'active', 'NRestarts': 1}
        self.assertEqual(self.run_eval(after={'daemon': st, 'monitor': st})['V1.survival'], 'fail')
        self.assertEqual(self.run_eval(moved=False)['V1.survival'], 'fail')
        dead = {'alive': False, 'rss_peak': 14 * MB, 'bytes_read_last_60s': 0}
        self.assertEqual(self.run_eval(loader=dead)['V1.survival'], 'fail')

    def test_an_oom_event_fails_survival(self):
        load = self.healthy()
        load[-1]['events']['oom_kill'] = 1
        self.assertEqual(self.run_eval(load=load)['V1.survival'], 'fail')

    def test_the_estimate_is_checked_against_the_known_input_not_against_a_true_value(self):
        load = self.healthy()
        for r in load[100:]:
            r['pinned'] = int(MX * 0.5)
        rows = {r[0]: r for r in stage_v1.evaluate_v1(load, [rec(900, 40 * MB, 36 * MB)] * 5, [{'ok': True}] * 10, {'daemon': {}, 'monitor': {}}, {'daemon': {}, 'monitor': {}}, None, {'alive': True, 'rss_peak': 14 * MB, 'bytes_read_last_60s': 1}, {'violations': [], 'heartbeats': 1, 'mem_lines': 1, 'mem_consistent': 1}, True, self.CFG)}
        self.assertEqual(rows['V1.estimate_consistent_with_known_input'][1], 'fail')
        self.assertIn('NOT an error measurement', rows['V1.estimate_consistent_with_known_input'][2])
        for r in rows.values():
            self.assertNotRegex(r[2].lower(), r'estimation error of|error was|exact')

    def test_pressure_is_a_diagnostic_and_never_decides_the_stage(self):
        load = self.healthy()
        for r in load:
            r['sample']['pressure']['full']['avg60'] = 40.0          # far above the old 5% gate
            r['sample']['pressure']['some']['avg60'] = 41.0
        rows = {r[0]: r for r in stage_v1.evaluate_v1(load, [rec(900 + i, current=40 * MB, pinned=36 * MB) for i in range(30)], [{'ok': True}] * 10, {'daemon': ACTIVE, 'monitor': ACTIVE}, {'daemon': ACTIVE, 'monitor': ACTIVE},
                                                       None, {'alive': True, 'rss_peak': 14 * MB, 'bytes_read_last_60s': 10 ** 9}, {'violations': [], 'heartbeats': 10, 'mem_lines': 10, 'mem_consistent': 10}, True, self.CFG)}
        self.assertNotIn('V1.pressure', rows, 'the old gating row is gone')
        row = rows['V1.pressure_diagnostic']
        self.assertEqual((row[1], row[4]), ('info', False))
        self.assertIn('cannot be attributed to the daemon', row[2])
        d = tempfile.mkdtemp()
        self.addCleanup(subprocess.run, ['rm', '-rf', d])
        r = C.Recorder('V1', d, {}, echo=lambda *_: None)
        r.add_all(list(rows.values()))
        self.assertEqual(r.overall(), 'pass', 'high pressure alone does not fail the stage')
        none = [rec(1000 + i, current=int(MX * 0.99), pinned=45 * MB, events={'max': i * 40, 'oom': 0, 'oom_kill': 0}, stat={'pgscan': i * 900, 'pgsteal': i * 800}, with_pressure=False) for i in range(200)]
        self.assertEqual(self.run_eval(load=none)['V1.pressure_diagnostic'], 'not_run')

    def test_reclaim_probes(self):
        self.assertEqual(self.run_eval(probes=[{'ok': True}] * 7 + [{'ok': False}] * 3)['V1.reclaim_probes'], 'fail')
        self.assertEqual(self.run_eval(probes=[{'ok': True}] * 8 + [{'ok': False}] * 2)['V1.reclaim_probes'], 'pass')
        self.assertEqual(self.run_eval(probes=[])['V1.reclaim_probes'], 'not_run')

    def test_heartbeat_breakdown_must_be_present_and_consistent(self):
        self.assertEqual(self.run_eval(watch={'violations': [], 'heartbeats': 10, 'mem_lines': 3, 'mem_consistent': 3})['V1.heartbeat_records_breakdown'], 'fail')
        self.assertEqual(self.run_eval(watch={'violations': [], 'heartbeats': 10, 'mem_lines': 10, 'mem_consistent': 5})['V1.heartbeat_records_breakdown'], 'fail')


class V2Evaluator(unittest.TestCase):
    def plateau(self, cross=1000.0):
        out = [rec(cross - 30 + i, current=60 * MB, pinned=50 * MB) for i in range(30)]
        out += [rec(cross + i, current=int(MX * 0.94), pinned=int(MX * 0.93), anon=int(MX * 0.9)) for i in range(120)]
        return out

    def evidence(self, **kw):
        ev = dict(current=int(MX * 0.94), max=MX, anon=int(MX * 0.9), file=1, file_dirty=0, file_writeback=0, file_mapped=0, shmem=0, kernel=1, events={}, pressure=None, reclaimable_estimate=1, pinned_estimate=1, pinned_fraction=0.93,
                  rule={}, recent=[1, 2, 3, 4], reason='sustained_unreclaimable')
        ev.update(kw)
        return {'evidence': ev}

    def a(self, delay=65.0, kind='memory_high', source='monitor', ev=None, pause=None, alive=True, recs=None):
        recs = recs or self.plateau()
        latch = None if delay is None else {'kind': kind, 'source': source, 'at': 1000.0 + delay}
        pause = pause or {'pause_file': True, 'pos_a': 10.0, 'pos_b': 10.2, 'dt': 15}
        return {r[0]: r[1] for r in stage_v2.evaluate_v2a(recs, latch, ev if ev is not None else self.evidence(), pause, {'alive_at_trip': alive}, {})}

    def test_a_correct_plateau_trip_passes(self):
        st = self.a()
        self.assertEqual(set(st.values()), {'pass'}, st)

    def test_too_early_too_late_wrong_kind_no_latch(self):
        self.assertEqual(self.a(delay=20)['V2a.tripped_on_the_sustained_rule'], 'fail')
        self.assertEqual(self.a(delay=150)['V2a.tripped_on_the_sustained_rule'], 'fail')
        self.assertEqual(self.a(kind='daemon_silent', source='daemon')['V2a.tripped_on_the_sustained_rule'], 'fail')
        self.assertEqual(self.a(delay=None)['V2a.tripped'], 'fail')

    def test_the_evidence_must_carry_the_breakdown(self):
        bad = self.evidence()
        del bad['evidence']['anon']
        self.assertEqual(self.a(ev=bad)['V2a.evidence_breakdown'], 'fail')
        self.assertEqual(self.a(ev=self.evidence(reason='oom_event'))['V2a.evidence_breakdown'], 'fail')
        self.assertEqual(self.a(ev=self.evidence(anon=1))['V2a.evidence_breakdown'], 'fail')

    def test_the_daemon_must_be_held_and_nothing_killed_before_the_trip(self):
        self.assertEqual(self.a(pause={'pause_file': False, 'pos_a': 1.0, 'pos_b': 1.0, 'dt': 15})['V2a.daemon_paused'], 'fail')
        self.assertEqual(self.a(pause={'pause_file': True, 'pos_a': 1.0, 'pos_b': 16.0, 'dt': 15})['V2a.daemon_paused'], 'fail')
        self.assertEqual(self.a(alive=False)['V2a.tripped_before_any_kill'], 'fail')
        killed = self.plateau()
        killed[-1]['events']['oom_kill'] = 1
        self.assertEqual(self.a(recs=killed)['V2a.tripped_before_any_kill'], 'fail')

    def test_a_load_that_never_crossed_the_line_is_invalid(self):
        low = [rec(1000 + i, current=60 * MB, pinned=50 * MB) for i in range(100)]
        self.assertEqual(self.a(recs=low)['V2a.line_crossed'], 'invalid')

    DAEMON_PID, LOADER_PID = 10, 77
    GOOD_LATCH = {'kind': 'memory_high', 'source': 'monitor', 'at': 1010.0}
    GOOD_INCIDENT = {'sha_before': 'a' * 64, 'sha_after': 'a' * 64, 'paused': True, 'pos_a': 140.0, 'pos_b': 140.2, 'dt': 15.0, 'new_evidence': ['trip-1-memory_high']}

    def victim(self, pid, comm='python3'):
        return {'pid': pid, 'comm': comm, 'line': 'x'}

    def b(self, **over):
        d = dict(kills=1, journal="Failed with result 'oom-kill'.", pre={'NRestarts': 0, 'MainPID': 10}, post={'ActiveState': 'active', 'NRestarts': 1, 'MainPID': 22}, tl=[], latch=dict(self.GOOD_LATCH),
                 inv_before={'a': (1, 'x')}, inv_after={'a': (1, 'x'), 'b': (2, 'y')}, partial=[], procs_new=['/usr/bin/python3 -I /x/tools/broadcast_run.py run'], mon_before={'NRestarts': 0}, mon_after={'ActiveState': 'active', 'NRestarts': 0},
                 old_alive=[], mem_end=40 * MB, mx=MX, pos_before=100.0, pos_after=140.0, elapsed=45.0, cfg={},
                 victims=[self.victim(self.DAEMON_PID)], daemon_pids=[self.DAEMON_PID], loader_pid=self.LOADER_PID, incident=dict(self.GOOD_INCIDENT), t0=1000.0)
        d.update(over)
        rows = stage_v2.evaluate_v2b(d['kills'], d['journal'], d['pre'], d['post'], d['tl'], d['latch'], d['inv_before'], d['inv_after'], d['partial'], d['procs_new'], d['mon_before'], d['mon_after'], d['old_alive'], d['mem_end'], d['mx'],
                                     d['pos_before'], d['pos_after'], d['elapsed'], d['cfg'], victims=d['victims'], daemon_pids=d['daemon_pids'], loader_pid=d['loader_pid'], incident=d['incident'], t0=d['t0'])
        return {r[0]: r[1] for r in rows}, {r[0]: r for r in rows}

    def overall(self, rows_by_id):
        r = C.Recorder('V2', tempfile.mkdtemp(), {}, echo=lambda *_: None)
        self.addCleanup(subprocess.run, ['rm', '-rf', r.outdir])
        r.add_all([v for v in rows_by_id.values()])
        return r.overall()

    def stage_overall(self, rows_b, rows_c=None):
        """The stage verdict as run() computes it: V2b rows, V2c rows, and the summary check."""
        rb = list(rows_b.values())
        rc = list((rows_c or {}).values())
        summary = stage_v2.daemon_oom_summary(rb, rc)
        r = C.Recorder('V2', tempfile.mkdtemp(), {}, echo=lambda *_: None)
        self.addCleanup(subprocess.run, ['rm', '-rf', r.outdir])
        r.add_all(rb + rc + [summary])
        return r.overall(), summary

    def c(self, selection=None, after=None, **over):
        """The V2c rows: the same evaluation as V2b under the V2c prefix, plus the checks only V2c has."""
        st, rows = self.b(**over)
        renamed = stage_v2.rename_rows(list(rows.values()), 'V2b', 'V2c')
        applied = {10: {'ok': True, 'readback': 1000}} if selection is None else selection
        renamed += stage_v2.evaluate_v2c_extra([10], applied, {22: 0} if after is None else after)
        return {r[0]: r[1] for r in renamed}, {r[0]: r for r in renamed}

    def test_a_daemon_oom_with_a_record_and_a_hold_passes_everything(self):
        st, rows = self.b()
        self.assertEqual({v for k, v in st.items() if k != 'V2b.daemon_oom_exercised'}, {'pass'}, st)
        self.assertEqual(st['V2b.daemon_oom_exercised'], 'pass')
        verdict, summary = self.stage_overall(rows)
        self.assertEqual((verdict, summary[1]), ('pass', 'pass'))

    def test_an_oom_without_a_persistent_incident_record_fails_the_stage(self):
        st, rows = self.b(latch=None)
        self.assertEqual(st['V2b.incident_record_and_hold'], 'fail')
        self.assertIn('no latch', rows['V2b.incident_record_and_hold'][2])
        self.assertIn('DEFECT', rows['V2b.incident_record_and_hold'][2])
        self.assertEqual(self.stage_overall(rows)[0], 'fail', 'a clean systemd recovery must not outweigh a missing incident record')
        self.assertNotIn('V2b.monitor_response', st, 'there is no longer an information-only outcome for a missing latch')

    def test_a_record_that_is_not_a_hold_fails(self):
        for name, over in (('unparsable latch', dict(latch={'unreadable': True})), ('stale latch', dict(latch={'kind': 'memory_high', 'at': 500.0})),
                           ('no evidence', dict(incident=dict(self.GOOD_INCIDENT, new_evidence=[]))), ('not paused', dict(incident=dict(self.GOOD_INCIDENT, paused=False))),
                           ('clock advanced', dict(incident=dict(self.GOOD_INCIDENT, pos_b=150.0))), ('latch changed', dict(incident=dict(self.GOOD_INCIDENT, sha_after='b' * 64))),
                           ('latch vanished', dict(incident=dict(self.GOOD_INCIDENT, sha_before=None, sha_after=None))), ('hold too short', dict(incident=dict(self.GOOD_INCIDENT, dt=3.0))),
                           ('hold never observed', dict(incident=None)), ('clock unreadable', dict(incident=dict(self.GOOD_INCIDENT, pos_b=None)))):
            st, rows = self.b(**over)
            self.assertEqual(st['V2b.incident_record_and_hold'], 'fail', name)
            self.assertEqual(self.stage_overall(rows)[0], 'fail', name)

    def test_the_requirement_cannot_be_tested_without_a_confirmed_oom(self):
        st, _ = self.b(kills=0, journal='nothing', latch=None, incident=None)
        self.assertEqual(st['V2b.incident_record_and_hold'], 'invalid')

    def test_killing_only_the_injected_load_leaves_daemon_oom_validation_incomplete_never_passed(self):
        st, rows = self.b(victims=[self.victim(self.LOADER_PID)])
        self.assertEqual(st['V2b.victim_reported'], 'pass')
        self.assertIn('pid 77', rows['V2b.victim_reported'][2])
        self.assertEqual(st['V2b.daemon_oom_exercised'], 'not_run')
        self.assertIn('INCOMPLETE', rows['V2b.daemon_oom_exercised'][2])
        self.assertIn('NOT done', rows['V2b.daemon_oom_exercised'][2])
        others = {k: v for k, v in st.items() if k != 'V2b.daemon_oom_exercised'}
        self.assertEqual(set(others.values()), {'pass'}, 'everything else can pass; it must still not add up to a pass')
        verdict, summary = self.stage_overall(rows)
        self.assertEqual((verdict, summary[1]), ('review', 'not_run'), 'an incomplete stage is not a pass, so it blocks the full run')

    def test_an_unknown_victim_is_incomplete_too(self):
        for victims in (None, []):
            st, rows = self.b(victims=victims)
            self.assertEqual((st['V2b.victim_reported'], st['V2b.daemon_oom_exercised']), ('not_run', 'not_run'))
            self.assertEqual(self.stage_overall(rows)[0], 'review')

    def test_another_process_as_victim_does_not_count_as_the_daemon(self):
        st, _ = self.b(victims=[self.victim(999, 'sleep')])
        self.assertEqual(st['V2b.daemon_oom_exercised'], 'not_run')

    def test_a_daemon_child_counts_as_the_daemon_and_the_daemon_plus_load_is_reported_in_full(self):
        st, rows = self.b(victims=[self.victim(11, 'python3'), self.victim(self.LOADER_PID)], daemon_pids=[10, 11])
        self.assertEqual(st['V2b.daemon_oom_exercised'], 'pass')
        self.assertIn('pid 11', rows['V2b.victim_reported'][2])
        self.assertIn('pid 77', rows['V2b.victim_reported'][2])

    def test_each_way_the_recovery_can_be_wrong(self):
        self.assertEqual(self.b(journal='nothing relevant', kills=0)[0]['V2b.kernel_killed'], 'invalid')
        self.assertEqual(self.b(journal='nothing relevant')[0]['V2b.systemd_recognised_the_oom'], 'fail')
        self.assertEqual(self.b(post={'ActiveState': 'failed', 'NRestarts': 1, 'MainPID': 0})[0]['V2b.unit_recovered_as_configured'], 'fail')
        self.assertEqual(self.b(post={'ActiveState': 'active', 'NRestarts': 0, 'MainPID': 10})[0]['V2b.unit_recovered_as_configured'], 'fail')
        self.assertEqual(self.b(old_alive=[1234])[0]['V2b.no_survivors'], 'fail')
        self.assertEqual(self.b(procs_new=['python3 loaders.py anon'])[0]['V2b.no_survivors'], 'fail')
        self.assertEqual(self.b(mon_after={'ActiveState': 'active', 'NRestarts': 1})[0]['V2b.monitor_stayed_up'], 'fail')
        self.assertEqual(self.b(inv_after={'a': (1, 'CHANGED')})[0]['V2b.public_files_intact'], 'fail')
        self.assertEqual(self.b(inv_after={})[0]['V2b.public_files_intact'], 'fail')
        self.assertEqual(self.b(partial=['x.tmp'])[0]['V2b.public_files_intact'], 'fail')
        self.assertEqual(self.b(pos_after=50.0)[0]['V2b.clock_continuity'], 'fail')
        self.assertEqual(self.b(pos_after=900.0)[0]['V2b.clock_continuity'], 'fail')
        self.assertEqual(self.b(mem_end=int(MX * 0.9))[0]['V2b.memory_recovered'], 'fail')

    def test_victims_are_read_from_the_kernel_log(self):
        log = ('kernel: oom-kill:constraint=CONSTRAINT_MEMCG,nodemask=(null),cpuset=/,mems_allowed=0,oom_memcg=/system.slice/dfl-vtest-daemon.service,task_memcg=/system.slice/dfl-vtest-daemon.service,task=python3,pid=4242,uid=0\n'
               'kernel: Memory cgroup out of memory: Killed process 4242 (python3) total-vm:1 kB\n'
               'kernel: oom-kill:constraint=CONSTRAINT_MEMCG,oom_memcg=/system.slice/other.service,task_memcg=/system.slice/other.service,task=x,pid=7,uid=0\n'
               'kernel: Memory cgroup out of memory: Killed process 7 (x) total-vm:1 kB\n')
        v = E.parse_oom_victims(log, 'dfl-vtest-daemon.service')
        self.assertEqual([(x['pid'], x['comm']) for x in v], [(4242, 'python3')], 'another unit\'s victim and the duplicate line are not reported')
        self.assertEqual(E.parse_oom_victims('Killed process 55 (old-kernel-format) total', 'u'), [{'pid': 55, 'comm': 'old-kernel-format', 'line': 'Killed process 55 (old-kernel-format) total'}])
        for empty in ('', None, 'nothing about oom here'):
            self.assertEqual(E.parse_oom_victims(empty, 'u'), [], 'no victim line means UNKNOWN, not a guess')
        c = E.classify_victims([{'pid': 4242, 'comm': 'p'}, {'pid': 10, 'comm': 'd'}, {'pid': 3, 'comm': 'o'}], [10], 4242)
        self.assertEqual(({k: [x['pid'] for x in l] for k, l in c.items()}), {'daemon': [10], 'loader': [4242], 'other': [3]})


    # ---- V2c: the isolated daemon-victim scenario
    def test_v2c_with_kernel_evidence_that_the_daemon_died_completes_the_check(self):
        _, rb = self.b(victims=[self.victim(self.LOADER_PID)])
        st, rc = self.c()
        self.assertEqual({v for v in st.values()}, {'pass'}, st)
        verdict, summary = self.stage_overall(rb, rc)
        self.assertEqual((verdict, summary[1]), ('pass', 'pass'))
        self.assertEqual(summary[3]['scenario'], 'V2c')

    def test_v2c_without_kernel_evidence_is_incomplete_never_passed(self):
        _, rb = self.b(victims=[self.victim(self.LOADER_PID)])
        for name, over in (('log unreadable', dict(victims=None)), ('kernel still chose the load', dict(victims=[self.victim(self.LOADER_PID)])), ('another process', dict(victims=[self.victim(999)]))):
            st, rc = self.c(**over)
            self.assertEqual(st['V2c.daemon_oom_exercised'], 'not_run', name)
            verdict, summary = self.stage_overall(rb, rc)
            self.assertEqual((verdict, summary[1]), ('review', 'not_run'), name)
            self.assertIn('NOT validated', summary[2])

    def test_v2c_whose_selection_step_was_not_applied_cannot_complete_the_check(self):
        _, rb = self.b(victims=[self.victim(self.LOADER_PID)])
        for sel in ({10: {'ok': False, 'readback': 0}}, {10: {'ok': True, 'readback': 500}}, {}, {11: {'ok': True, 'readback': 1000}}):
            st, rc = self.c(selection=sel)
            self.assertEqual(st['V2c.victim_selection_applied'], 'invalid', sel)
            verdict, summary = self.stage_overall(rb, rc)
            self.assertNotEqual(verdict, 'pass', sel)
            self.assertEqual(summary[1], 'not_run', sel)

    def test_v2c_still_requires_the_incident_record_and_the_hold(self):
        _, rb = self.b(victims=[self.victim(self.LOADER_PID)])
        st, rc = self.c(latch=None)
        self.assertEqual(st['V2c.incident_record_and_hold'], 'fail')
        self.assertEqual(self.stage_overall(rb, rc)[0], 'fail')

    def test_v2c_the_adjustment_must_end_with_the_old_process(self):
        self.assertEqual(self.c(after={22: 1000})[0]['V2c.adjustment_not_persistent'], 'fail')
        self.assertEqual(self.c(after={})[0]['V2c.adjustment_not_persistent'], 'not_run')
        self.assertEqual(self.c(after={22: None})[0]['V2c.adjustment_not_persistent'], 'not_run')
        _, rb = self.b(victims=[self.victim(self.LOADER_PID)])
        self.assertEqual(self.stage_overall(rb, self.c(after={22: 1000})[1])[0], 'fail')

    def test_a_natural_daemon_kill_in_v2b_also_completes_the_check_and_v2c_adds_nothing_false(self):
        _, rb = self.b()
        self.assertEqual(self.stage_overall(rb)[1][1], 'pass')
        _, rc = self.c(victims=None)
        self.assertEqual(self.stage_overall(rb, rc)[1][1], 'pass', 'one scenario with kernel evidence is enough; the other one is still judged on its own rows')

    def test_the_summary_is_incomplete_when_a_scenario_never_ran(self):
        self.assertEqual(stage_v2.daemon_oom_summary([], [])[1], 'not_run')

    def test_the_v2c_rows_use_the_v2c_prefix_and_the_summary_is_required(self):
        st, rows = self.c()
        self.assertTrue(all(k.startswith('V2c.') for k in st), st)
        self.assertFalse(any(r[-1] is False for r in [stage_v2.daemon_oom_summary(list(self.b()[1].values()), [])]), 'a tuple ending in False marks a row as not required')
        self.assertFalse(self.b()[1]['V2b.daemon_oom_exercised'][-1], 'the per-scenario row is not required on its own; the summary is')


class Oom_adjust_is_confined(unittest.TestCase):
    CG = C.SLICE + '/' + C.UNIT['daemon'] + '/cgroup.procs'

    def env_with_procs(self, pids):
        orig = C.Env.read
        return mock.patch.object(C.Env, 'read', lambda self, p, default=None: ('\n'.join(map(str, pids)) + '\n') if p == Oom_adjust_is_confined.CG else orig(self, p, default))

    def test_values_that_could_make_a_process_unkillable_or_out_of_range_are_refused(self):
        with self.env_with_procs([os.getpid()]):
            for bad in (-1000, -1, 1001, 5000, True, '1000', 1.5, None):
                with self.assertRaises(C.Unsafe, msg=repr(bad)):
                    C.Env().oom_adjust(os.getpid(), bad)

    def test_the_range_guard_holds_on_its_own_not_only_through_the_no_lowering_guard(self):
        ghost = 4_000_000 + 12345                      # no such process: a write that slipped through could not hurt anything, and would not raise
        orig = C.Env.read
        cg = Oom_adjust_is_confined.CG

        def fake(self, p, default=None):
            if p == cg:
                return '%d\n' % ghost
            if p == '/proc/%d/oom_score_adj' % ghost:
                return '-1000\n'                      # the process already sits at the floor, so "lowering" is not what stops -1000
            return orig(self, p, default)
        with mock.patch.object(C.Env, 'read', fake):
            for bad in (-1000, -999, -1):
                with self.assertRaises(C.Unsafe, msg=bad):
                    C.Env().oom_adjust(ghost, bad)

    def test_only_processes_in_the_daemons_disposable_cgroup_may_be_touched(self):
        with self.env_with_procs([4242]):
            for pid in (1, os.getpid(), 4243):
                with self.assertRaises(C.Unsafe, msg=pid):
                    C.Env().oom_adjust(pid, 1000)
        with self.env_with_procs([]):
            with self.assertRaises(C.Unsafe):
                C.Env().oom_adjust(os.getpid(), 1000)

    def test_the_monitors_cgroup_is_not_accepted(self):
        monitor_cg = C.SLICE + '/' + C.UNIT['monitor'] + '/cgroup.procs'
        orig = C.Env.read
        with mock.patch.object(C.Env, 'read', lambda self, p, default=None: ('%d\n' % os.getpid()) if p == monitor_cg else orig(self, p, default)):
            with self.assertRaises(C.Unsafe):
                C.Env().oom_adjust(os.getpid(), 1000)

    def test_it_raises_a_real_process_and_refuses_to_lower_it_again(self):
        child = subprocess.Popen(['sleep', '30'])
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        with self.env_with_procs([child.pid]):
            ok, err = C.Env().oom_adjust(child.pid, 1000)
            if not ok:
                self.skipTest('this sandbox does not allow writing oom_score_adj (errno %s)' % err)
            self.assertEqual(open('/proc/%d/oom_score_adj' % child.pid).read().strip(), '1000')
            with self.assertRaises(C.Unsafe):
                C.Env().oom_adjust(child.pid, 500)
            self.assertEqual(open('/proc/%d/oom_score_adj' % child.pid).read().strip(), '1000', 'a refused call changes nothing')
        self.assertEqual(open('/proc/%d/oom_score_adj' % os.getpid()).read().strip(), open('/proc/self/oom_score_adj').read().strip())

    def test_the_scenario_helper_touches_only_the_daemons_cgroup_and_reports_the_readback(self):
        calls = []

        class FakeEnv2:
            def oom_adjust(self, pid, value):
                calls.append((pid, value))
                return True, None
        fake = mock.Mock()
        fake.env = FakeEnv2()
        fake.daemon_pids.return_value = [10, 11]
        fake.oom_adj_of.side_effect = lambda pids: {p: 1000 for p in pids}
        import scenarios
        res = scenarios.Ctx.prefer_daemon_as_oom_victim(fake)
        self.assertEqual(calls, [(10, 1000), (11, 1000)])
        self.assertEqual({k: v['readback'] for k, v in res.items()}, {10: 1000, 11: 1000})

    def test_the_loader_is_never_adjusted_and_the_write_lives_in_one_place(self):
        allowed = {'common.py', 'scenarios.py', 'stage_v2.py', 'selftest.py'}
        for f in VT.glob('*.py'):
            if f.name not in allowed:
                self.assertNotIn('oom_score_adj', f.read_text(), f.name)
        self.assertNotIn('oom_score_adj', (VT / 'loaders.py').read_text(), 'the injected load keeps the default, so it stays killable')
        self.assertNotIn('.oom_adjust(', (VT / 'stage_v2.py').read_text(), 'stages go through Ctx.prefer_daemon_as_oom_victim only')
        self.assertEqual(len(re.findall(r'open\(path, "w"\)', (VT / 'common.py').read_text().split('def oom_adjust', 1)[1].split('def sc', 1)[0])), 1)


class V3Evaluator(unittest.TestCase):
    def test_kill9_must_restart_without_tripping(self):
        ok = stage_v3.evaluate_v3a({'NRestarts': 0}, {'NRestarts': 1, 'ActiveState': 'active'}, 25.0, None, False, True, 150)
        self.assertEqual([r[1] for r in ok], ['pass'] * 3)
        self.assertEqual(stage_v3.evaluate_v3a({'NRestarts': 0}, {'NRestarts': 0, 'ActiveState': 'active'}, 25.0, None, False, True, 150)[0][1], 'fail')
        self.assertEqual(stage_v3.evaluate_v3a({'NRestarts': 0}, {'NRestarts': 1, 'ActiveState': 'active'}, 110.0, None, False, True, 150)[1][1], 'fail')
        self.assertEqual(stage_v3.evaluate_v3a({'NRestarts': 0}, {'NRestarts': 1, 'ActiveState': 'active'}, 25.0, {'kind': 'x'}, False, True, 150)[2][1], 'fail')
        self.assertEqual(stage_v3.evaluate_v3a({'NRestarts': 0}, {'NRestarts': 1, 'ActiveState': 'active'}, 25.0, None, True, True, 150)[2][1], 'fail')

    def silent(self, **over):
        d = dict(latch={'kind': 'monitor_silent', 'source': 'daemon', 'at': 1125.0}, hb=1000.0, paused=True, pos_a=50.0, pos_b=50.2, inv_a={'a': 1}, inv_b={'a': 1}, recovery={'release_rc': 0, 'resume_rc': 0, 'clock_advances': True})
        d.update(over)
        return {r[0]: r[1] for r in stage_v3.evaluate_silent('V3b', d['latch'], d['hb'], d['paused'], d['pos_a'], d['pos_b'], d['inv_a'], d['inv_b'], d['recovery'])}

    def test_a_silent_monitor_trips_the_daemon_and_pauses_it_safely(self):
        self.assertEqual(set(self.silent().values()), {'pass'})
        self.assertEqual(self.silent(latch=None)['V3b.daemon_tripped'], 'fail')
        self.assertEqual(self.silent(latch={'unreadable': True})['V3b.daemon_tripped'], 'fail')
        self.assertEqual(self.silent(latch={'kind': 'monitor_silent', 'source': 'daemon', 'at': 1030.0})['V3b.daemon_tripped'], 'fail')
        self.assertEqual(self.silent(latch={'kind': 'memory_high', 'source': 'monitor', 'at': 1125.0})['V3b.daemon_tripped'], 'fail')
        self.assertEqual(self.silent(paused=False)['V3b.paused_safely'], 'fail')
        self.assertEqual(self.silent(pos_b=80.0)['V3b.paused_safely'], 'fail')
        self.assertEqual(self.silent(inv_b={'a': 1, 'b': 2})['V3b.paused_safely'], 'fail')
        self.assertEqual(self.silent(recovery={'release_rc': 4, 'resume_rc': 3, 'clock_advances': False})['V3b.recoverable_the_documented_way'], 'fail')

    def test_repeated_monitor_oom_must_end_in_a_failed_unit_and_a_daemon_trip(self):
        iters = [{'restarted': True, 'latch': False, 'restart_s': 12}] + [{'restarted': True, 'latch': False}] * 3
        rec_ok = {'release_rc': 0, 'resume_rc': 0, 'clock_advances': True}
        latch = {'kind': 'monitor_silent', 'source': 'daemon', 'at': 1125.0}
        rows = {r[0]: r[1] for r in stage_v3.evaluate_v3d(iters, None, {'ActiveState': 'failed'}, latch, 1000.0, True, 5.0, 5.0, {}, {}, rec_ok)}
        self.assertEqual(set(rows.values()), {'pass'}, rows)
        rows = {r[0]: r[1] for r in stage_v3.evaluate_v3d(iters, None, {'ActiveState': 'active'}, latch, 1000.0, True, 5.0, 5.0, {}, {}, rec_ok)}
        self.assertEqual(rows['V3d.start_limit_left_the_unit_failed'], 'fail')
        early = [{'restarted': True, 'latch': True, 'restart_s': 12}]
        rows = {r[0]: r[1] for r in stage_v3.evaluate_v3d(early, None, {'ActiveState': 'failed'}, latch, 1000.0, True, 5.0, 5.0, {}, {}, rec_ok)}
        self.assertEqual(rows['V3d.one_oom_restarts_without_a_trip'], 'fail')


class V4Evaluator(unittest.TestCase):
    FP = {'sha256': 'a' * 64, 'size': 120, 'mtime': 1.0}

    def d(self, **over):
        a = dict(step='daemon_restart', fp0=self.FP, fp1=self.FP, paused=True, pos_a=10.0, pos_b=10.1, inv0={'a': 1}, inv1={'a': 1}, resume_rc=3, units_active=True, trips0=[{'at': 1}], trips1=[{'at': 1}])
        a.update(over)
        return stage_v4.evaluate_disturbance(a['step'], a['fp0'], a['fp1'], a['paused'], a['pos_a'], a['pos_b'], a['inv0'], a['inv1'], a['resume_rc'], a['units_active'], a['trips0'], a['trips1'])[1]

    def test_a_latch_that_survives_a_disturbance_passes(self):
        self.assertEqual(self.d(), 'pass')

    def test_every_way_the_latch_can_be_lost_or_ignored_fails(self):
        for over in (dict(fp1=None), dict(fp1=dict(self.FP, sha256='b' * 64)), dict(fp1=dict(self.FP, mtime=2.0)), dict(paused=False), dict(pos_b=40.0), dict(inv1={'a': 1, 'b': 2}), dict(resume_rc=0), dict(units_active=False), dict(trips1=[{'at': 9}]), dict(trips1=[{'at': 1}, {'at': 2, 'kind': 'unaccounted_stop'}]), dict(trips1=[{'at': 1}, {'at': 2, 'kind': 'oom_kill'}]), dict(trips1=[])):
            self.assertEqual(self.d(**over), 'fail', over)

    def test_a_line_the_disturbance_may_legitimately_add_is_allowed_only_when_named(self):
        new = [{'at': 1}, {'at': 2, 'kind': 'unaccounted_stop'}]
        a = dict(step='after_hard_reset', fp0=self.FP, fp1=self.FP, paused=True, pos_a=10.0, pos_b=10.1, inv0={'a': 1}, inv1={'a': 1}, resume_rc=3, units_active=True, trips0=[{'at': 1}], trips1=new)
        args = (a['step'], a['fp0'], a['fp1'], a['paused'], a['pos_a'], a['pos_b'], a['inv0'], a['inv1'], a['resume_rc'], a['units_active'], a['trips0'], a['trips1'])
        self.assertEqual(stage_v4.evaluate_disturbance(*args, allow_new=('unaccounted_stop',))[1], 'pass')
        self.assertEqual(stage_v4.evaluate_disturbance(*args)[1], 'fail')
        self.assertEqual(stage_v4.evaluate_disturbance(*args, allow_new=('oom_kill',))[1], 'fail')

    def test_refusals(self):
        rows = {r[0]: r[1] for r in stage_v4.evaluate_refusals(3, 2, 4, self.FP, [self.FP, self.FP])}
        self.assertEqual(set(rows.values()), {'pass'})
        for args in ((0, 2, 4), (3, 0, 4), (3, 2, 0)):
            self.assertIn('fail', {r[1] for r in stage_v4.evaluate_refusals(*args, self.FP, [self.FP])})
        self.assertIn('fail', {r[1] for r in stage_v4.evaluate_refusals(3, 2, 4, self.FP, [dict(self.FP, size=1)])})

    def test_release_must_keep_everything(self):
        orig = {'kind': 'memory_high', 'detail': 'd', 'source': 'monitor', 'at': 5.0}
        released = [dict(orig, released_at=9.0, released_reason='a reason that is long enough')]
        before = {'TRIPPED': 1, 'trips.jsonl': 1, 'evidence/trip-1/evidence.json': 1}
        after = dict(before, **{'evidence/released/x-TRIPPED.json': 1})
        after.pop('TRIPPED')
        good = {r[0]: r[1] for r in stage_v4.evaluate_release(0, 0, None, released, before, after, orig, True, True)}
        self.assertEqual(set(good.values()), {'pass'}, good)
        lost = dict(after)
        lost.pop('evidence/trip-1/evidence.json')
        self.assertEqual({r[0]: r[1] for r in stage_v4.evaluate_release(0, 0, None, released, before, lost, orig, True, True)}['V4.release.nothing_deleted'], 'fail')
        self.assertEqual({r[0]: r[1] for r in stage_v4.evaluate_release(0, 0, None, [], before, after, orig, True, True)}['V4.release.latch_kept_in_evidence'], 'fail')
        self.assertEqual({r[0]: r[1] for r in stage_v4.evaluate_release(0, 3, None, released, before, after, orig, False, True)}['V4.release.resume_works'], 'fail')
        self.assertEqual({r[0]: r[1] for r in stage_v4.evaluate_release(0, 0, None, released, before, after, orig, True, False)}['V4.release.held_until_resume'], 'fail')


class RecorderAndStop(unittest.TestCase):
    def mk(self):
        d = tempfile.mkdtemp()
        self.addCleanup(subprocess.run, ['rm', '-rf', d])
        return C.Recorder('V9', d, {'pin': 'p'}, echo=lambda *_: None), d

    def test_overall_is_the_worst_required_check(self):
        r, _ = self.mk()
        r.check('a', 'pass', 'x')
        r.check('b', 'info', 'x', required=False)
        r.check('c', 'not_run', 'x', required=False)
        self.assertEqual(r.overall(), 'pass')
        r.check('d', 'not_run', 'x')
        self.assertEqual(r.overall(), 'review')
        r.check('e', 'invalid', 'x')
        self.assertEqual(r.overall(), 'invalid')
        r.check('f', 'fail', 'x')
        self.assertEqual(r.overall(), 'fail')
        with self.assertRaises(C.StageStop):
            r.stop_if_failed()

    def test_the_manifest_hashes_every_evidence_file(self):
        r, d = self.mk()
        Path(d, 'samples.jsonl').write_text('{"a":1}\n')
        r.check('a', 'pass', 'x')
        r.finish()
        lines = Path(d, 'MANIFEST.sha256').read_text().splitlines()
        self.assertEqual({l.split('  ')[1] for l in lines}, {'samples.jsonl', 'result.json'})
        for l in lines:
            self.assertEqual(l.split('  ')[0], C.sha256_file(os.path.join(d, l.split('  ')[1])))

    def test_a_failed_stage_stops_the_run_and_a_later_run_is_refused_until_acknowledged(self):
        import vtest
        tmp = tempfile.mkdtemp()
        self.addCleanup(subprocess.run, ['rm', '-rf', tmp])
        fake = mock.Mock(STAGE='V1', PLAN=[], DEFAULTS={'qualifying': {}, 'smoke': {}})
        order = []

        def failing(ctx):
            order.append('V1')
            ctx.rec.check('V1.x', 'fail', 'a check failed')
            ctx.rec.stop_if_failed()
        fake.run = failing
        second = mock.Mock(STAGE='V2', PLAN=[], DEFAULTS={'qualifying': {}, 'smoke': {}}, run=lambda ctx: order.append('V2'))
        mods = {'V1': fake, 'V2': second}
        install = json.dumps({'pin': C.read_pin(), 'deviations': []})
        with mock.patch.object(C, 'BASE', tmp), mock.patch.object(C, 'EVID', tmp + '/evidence'), mock.patch.object(C, 'STATE', tmp + '/state'), \
                mock.patch.object(vtest.C, 'pin_check', lambda *a, **k: (True, [])), mock.patch.object(vtest, 'constants_check', lambda *a: []), \
                mock.patch.object(vtest.PF, 'preflight', lambda env: [('x', 'pass', '')]), mock.patch.object(vtest, 'stage_module', lambda n: mods[n]):
            os.makedirs(tmp + '/state')
            Path(tmp, 'state', 'install.json').write_text(install)
            code = vtest.main(['run', '--stages', 'V1,V2', '--run-id', 'r1'])
            self.assertEqual((code, order), (2, ['V1']), 'V2 must not run after V1 failed')
            self.assertTrue(os.path.exists(tmp + '/evidence/r1/STOPPED.json'))
            self.assertEqual(json.load(open(tmp + '/evidence/r1/SUMMARY.json'))['stopped_at'], 'V1')
            self.assertEqual(vtest.main(['run', '--stages', 'V1', '--run-id', 'r2']), 2, 'refused: an earlier stop is unacknowledged')
            self.assertEqual(vtest.main(['run', '--stages', 'V1', '--run-id', 'r3', '--acknowledge-stop', 'short']), 2)
            self.assertFalse(os.path.exists(tmp + '/evidence/r2'), 'a refused run leaves nothing behind')
            self.assertTrue(os.path.exists(tmp + '/evidence/r1/V1/MANIFEST.sha256'), 'the stopped stage kept its evidence and a manifest')
            out = Path(tmp, 'evidence', 'r1', 'command_output.txt').read_text()
            self.assertIn('V1.x', out, 'everything the run printed is kept with its evidence')
            self.assertIn('STOPPED at V1', out)

    def test_a_safety_refusal_inside_a_stage_is_a_failed_stage(self):
        import vtest
        tmp = tempfile.mkdtemp()
        self.addCleanup(subprocess.run, ['rm', '-rf', tmp])

        def bad(ctx):
            ctx.env.sc('stop', 'sshd.service')
        mod = mock.Mock(STAGE='V1', PLAN=[], DEFAULTS={'qualifying': {}, 'smoke': {}}, run=bad)
        with mock.patch.object(C, 'BASE', tmp), mock.patch.object(C, 'EVID', tmp + '/evidence'), mock.patch.object(C, 'STATE', tmp + '/state'), \
                mock.patch.object(vtest.C, 'pin_check', lambda *a, **k: (True, [])), mock.patch.object(vtest, 'constants_check', lambda *a: []), \
                mock.patch.object(vtest.PF, 'preflight', lambda env: [('x', 'pass', '')]), mock.patch.object(vtest, 'stage_module', lambda n: mod):
            os.makedirs(tmp + '/state')
            Path(tmp, 'state', 'install.json').write_text(json.dumps({'pin': C.read_pin()}))
            self.assertEqual(vtest.main(['run', '--stages', 'V1', '--run-id', 'r1']), 2)
            res = json.load(open(tmp + '/evidence/r1/V1/result.json'))
            self.assertEqual(res['overall'], 'fail')
            self.assertIn('safety', res['checks'][-1]['id'])


class PlanMatchesCode(unittest.TestCase):
    def test_every_planned_step_appears_in_its_stage_and_nothing_is_unplanned(self):
        for mod in (stage_v1, stage_v2, stage_v3, stage_v4):
            src = Path(mod.__file__).read_text()
            for sid, text in mod.PLAN:
                self.assertIn('"%s"' % sid, src.split('PLAN = [', 1)[1].split(']\n', 1)[1] if sid in src else src, '%s is planned but never appears in the code' % sid)
                self.assertTrue(text.strip())
            marks = set(re.findall(r'ctx\.mark\("([A-Za-z0-9._]+)"', src))
            planned = {s for s, _ in mod.PLAN}
            self.assertTrue(planned <= marks | {s for s in planned if s in src.split('def run', 1)[1]}, (mod.STAGE, planned - marks))

    def test_the_four_requested_checks_are_the_four_stages(self):
        self.assertEqual(C.STAGES, ('V1', 'V2', 'V3', 'V4'))
        self.assertIn('cache', stage_v1.__doc__.lower())
        self.assertIn('anonymous', stage_v2.__doc__.lower())
        self.assertIn('hang', stage_v3.__doc__.lower())
        self.assertIn('latch', stage_v4.__doc__.lower())


class LoadersAndSampler(unittest.TestCase):
    def test_the_loaders_log_their_own_ground_truth(self):
        d = tempfile.mkdtemp()
        self.addCleanup(subprocess.run, ['rm', '-rf', d])
        py = sys.executable
        subprocess.run([py, str(VT / 'loaders.py'), 'dataset', '--dir', d + '/ds', '--bytes', str(8 << 20)], check=True, capture_output=True)
        subprocess.run([py, str(VT / 'loaders.py'), 'cache', '--cgroup', 'none', '--dir', d + '/ds', '--log', d + '/c.jsonl', '--seconds', '1'], check=True, capture_output=True)
        subprocess.run([py, str(VT / 'loaders.py'), 'anon', '--cgroup', 'none', '--target-bytes', str(12 << 20), '--rate-bps', str(48 << 20), '--hold-s', '0.5', '--log', d + '/a.jsonl'], check=True, capture_output=True)
        c, a = E.loader_summary(C.jsonl_read(d + '/c.jsonl'), 1e12, False), E.loader_summary(C.jsonl_read(d + '/a.jsonl'), 1e12, False)
        self.assertGreater(c['bytes_read_last'], 8 << 20)
        self.assertEqual(a['allocated_last'], 12 << 20)
        self.assertGreaterEqual(a['rss_peak'], 12 << 20, 'every allocated page was touched, so it is really resident')

    def test_the_sampler_reads_v2_files_with_the_pinned_monitors_own_reader_and_survives_a_missing_cgroup(self):
        rc = SM.load_rc(str(ROOT))
        snap = SM.snapshot(rc, str(ROOT / 'tests' / 'fixtures' / 'cgroup_v2_synthetic'), 5.0)
        self.assertTrue(snap['readable'])
        self.assertEqual((snap['sample']['max'], snap['events']['oom_kill']), (640_000_000, 0))
        self.assertLess(snap['pinned'], snap['sample']['current'])
        self.assertEqual(SM.snapshot(rc, '/nonexistent/cgroup', 6.0), {'t': 6.0, 'exists': False})

    def test_event_deltas_survive_a_recreated_cgroup(self):
        recs = [rec(1, 1, 1, events={'oom_kill': 0}), rec(2, 1, 1, events={'oom_kill': 1}), rec(3, 0, 0, readable=False), rec(4, 1, 1, events={'oom_kill': 0}), rec(5, 1, 1, events={'oom_kill': 2})]
        self.assertEqual(E.event_delta(recs, 'oom_kill'), 1 + 0 + 2)


if __name__ == '__main__':
    unittest.main()
