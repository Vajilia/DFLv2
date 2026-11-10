"""The harness's judgments of the candidate's unit lifecycle design (tools/vtest/lifecycle.py, the lifecycle rows of stages V2-V4, the renderer's check that the units carry the design, the guard-folder fault).
Every verdict is tested on synthetic values: a good case passes, and each way the design can fail is its own failing case.
NOT tested here, and said so: that systemd really runs ExecStartPre= / ExecStopPost= in the assumed order, with the assumed environment, or that a clean shutdown closes the marker. That is what a run on a disposable machine is for."""
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
VT = ROOT / 'tools' / 'vtest'
sys.path.insert(0, str(VT))
sys.path.insert(0, str(ROOT / 'tools'))
import common as C                      # noqa: E402
import lifecycle as LC                  # noqa: E402
import render as RN                     # noqa: E402
import stage_v2, stage_v3, stage_v4     # noqa: E402

PIN = C.read_pin()
BOOT = 'b00t-1'


def marker(inv='inv-1', opened=1000.0, boot=BOOT, state='open'):
    return {'state': state, 'invocation_id': inv if state == 'open' else None, 'boot_id': boot if state == 'open' else None, 'opened_at': opened if state == 'open' else None, 'pid': 5}


ABSENT = marker(state='absent')
LATCH = {'kind': 'oom_kill', 'source': 'systemd', 'at': 1100.0, 'detail': 'x'}
EVID = {'kind': 'oom_kill', 'evidence': {'victims': [{'pid': 77, 'comm': 'python3'}], 'victim_status': 'identified', 'daemon_main_pid': 10, 'daemon_was_victim': False}}
HV = [{'pid': 77, 'comm': 'python3'}]


class Helpers(unittest.TestCase):
    def test_new_lines_is_an_append_only_view(self):
        self.assertEqual(LC.new_lines([{'a': 1}], [{'a': 1}, {'b': 2}]), [{'b': 2}])
        self.assertEqual(LC.new_lines([{'a': 1}], [{'a': 1}]), [])
        self.assertIsNone(LC.new_lines([{'a': 1}], [{'a': 9}]))
        self.assertIsNone(LC.new_lines([{'a': 1}], []))

    def test_systemds_monotonic_start_becomes_a_wall_time(self):
        self.assertAlmostEqual(LC.wall_time_of_monotonic_us(5_000_000, 2000.0, 100.0), 1905.0)
        self.assertIsNone(LC.wall_time_of_monotonic_us(0, 2000.0, 100.0))
        self.assertIsNone(LC.wall_time_of_monotonic_us(None, 2000.0, 100.0))


class RecordedByTheHook(unittest.TestCase):
    def test_only_the_stop_hooks_record_passes(self):
        self.assertEqual(LC.judge_recorded_by_hook(LATCH)[0], 'pass')

    def test_the_gates_fail_safe_is_not_the_design_working(self):
        st, d = LC.judge_recorded_by_hook({'kind': 'unaccounted_stop', 'source': 'systemd', 'at': 1.0})
        self.assertEqual(st, 'fail')
        self.assertIn('fail-safe', d)

    def test_other_latches_and_no_latch_fail(self):
        for latch in (None, {'unreadable': True}, {'kind': 'memory_high', 'source': 'monitor'}, {'kind': 'oom_kill', 'source': 'monitor'}):
            self.assertEqual(LC.judge_recorded_by_hook(latch)[0], 'fail', latch)


class BeforeTheRestart(unittest.TestCase):
    def test_ordering(self):
        self.assertEqual(LC.judge_before_restart(1100.0, 1105.0)[0], 'pass')
        self.assertEqual(LC.judge_before_restart(1100.0, 1100.2)[0], 'pass')            # within the tolerance
        self.assertEqual(LC.judge_before_restart(1100.0, 1099.0)[0], 'fail')            # the restarted daemon came first
        self.assertEqual(LC.judge_before_restart(1100.0, None)[0], 'not_run')
        self.assertEqual(LC.judge_before_restart(None, 1105.0)[0], 'not_run')


class SingleIncident(unittest.TestCase):
    def test_exactly_one_oom_line(self):
        self.assertEqual(LC.judge_single_incident([{'kind': 'oom_kill'}])[0], 'pass')

    def test_none_two_other_or_a_lost_line_fail(self):
        for lines in ([], [{'kind': 'oom_kill'}, {'kind': 'unaccounted_stop'}], [{'kind': 'oom_kill'}, {'kind': 'oom_kill'}], [{'kind': 'unaccounted_stop'}], None):
            self.assertEqual(LC.judge_single_incident(lines)[0], 'fail', lines)


class MarkerLifecycle(unittest.TestCase):
    def test_closed_and_reopened_after_the_latch_passes(self):
        self.assertEqual(LC.judge_marker_reopened(marker(), marker('inv-2', opened=1105.0), BOOT, latch_at=1100.0), [])

    def test_each_way_it_can_go_wrong(self):
        cases = {'no marker before': (ABSENT, marker('inv-2', 1105.0)),
                 'no marker after': (marker(), ABSENT),
                 'the same run still open': (marker(), marker()),
                 'another boot': (marker(), marker('inv-2', 1105.0, boot='other')),
                 'opened before the latch': (marker(), marker('inv-2', 1090.0)),
                 'unreadable after': (marker(), marker(state='unreadable'))}
        for name, (b, a) in cases.items():
            self.assertTrue(LC.judge_marker_reopened(b, a, BOOT, latch_at=1100.0), name)

    def test_without_an_invocation_id_the_open_time_tells_runs_apart(self):
        b, a = marker(inv=None, opened=1000.0), marker(inv=None, opened=1000.0)
        self.assertTrue(LC.judge_marker_reopened(b, a, BOOT))
        self.assertEqual(LC.judge_marker_reopened(b, marker(inv=None, opened=1105.0), BOOT), [])

    def test_marker_view_reads_the_gates_document(self):
        v = LC.marker_view(('open', {'invocation_id': 'i', 'boot_id': 'b', 'opened_at': 3.0, 'pid': 9}))
        self.assertEqual(v, {'state': 'open', 'invocation_id': 'i', 'boot_id': 'b', 'opened_at': 3.0, 'pid': 9})
        self.assertEqual(LC.marker_view(('absent', None))['state'], 'absent')
        self.assertEqual(LC.marker_view(None)['state'], 'absent')


class Victim(unittest.TestCase):
    def test_explicit_and_consistent_passes(self):
        self.assertEqual(LC.judge_victim(EVID['evidence'], HV, 10)[0], 'pass')

    def test_unknown_is_allowed_but_never_a_guess(self):
        unknown = {'victims': [], 'victim_status': 'unknown', 'daemon_main_pid': 10, 'daemon_was_victim': None}
        self.assertEqual(LC.judge_victim(unknown, HV, 10)[0], 'pass')
        self.assertEqual(LC.judge_victim(dict(unknown, daemon_was_victim=True), HV, 10)[0], 'fail')

    def test_each_contradiction_fails(self):
        ev = EVID['evidence']
        cases = {'no status': dict(ev, victim_status=None),
                 'identified without victims': dict(ev, victims=[]),
                 'another pid than the kernel log': dict(ev, victims=[{'pid': 78, 'comm': 'x'}]),
                 'daemon_was_victim contradicts the log': dict(ev, daemon_was_victim=True),
                 'a daemon pid that was not the daemon': dict(ev, daemon_main_pid=99)}
        for name, e in cases.items():
            self.assertEqual(LC.judge_victim(e, HV, 10)[0], 'fail', name)

    def test_daemon_as_victim_must_be_recorded_as_such(self):
        ev = {'victims': [{'pid': 10, 'comm': 'python3'}], 'victim_status': 'identified', 'daemon_main_pid': 10, 'daemon_was_victim': True}
        self.assertEqual(LC.judge_victim(ev, [{'pid': 10, 'comm': 'python3'}], 10)[0], 'pass')
        self.assertEqual(LC.judge_victim(dict(ev, daemon_was_victim=False), [{'pid': 10, 'comm': 'python3'}], 10)[0], 'fail')

    def test_a_log_the_harness_cannot_read_leaves_the_record_unchallenged_but_says_so(self):
        st, d = LC.judge_victim(EVID['evidence'], [], 10)
        self.assertEqual(st, 'pass')
        self.assertIn('could not read the kernel log', d)


class V2Rows(unittest.TestCase):
    def rows(self, **over):
        a = dict(latch=dict(LATCH), evidence_doc=EVID, trips_before=[{'kind': 'memory_high'}], trips_after=[{'kind': 'memory_high'}, {'kind': 'oom_kill'}], marker_before=marker(), marker_after=marker('inv-2', 1105.0),
                 daemon_start_wall=1105.0, harness_victims=HV, pre_main_pid=10, boot_id=BOOT)
        a.update(over)
        return {r[0]: r[1] for r in LC.evaluate_v2_lifecycle('V2b', a['latch'], a['evidence_doc'], a['trips_before'], a['trips_after'], a['marker_before'], a['marker_after'], a['daemon_start_wall'], a['harness_victims'], a['pre_main_pid'], a['boot_id'])}

    def test_the_designed_outcome_passes_every_row(self):
        r = self.rows()
        self.assertEqual(set(r.values()), {'pass'}, r)
        self.assertEqual(len(r), 5)

    def test_the_gates_fail_safe_alone_fails_the_stop_hook_row(self):
        r = self.rows(latch=dict(LATCH, kind='unaccounted_stop'), trips_after=[{'kind': 'memory_high'}, {'kind': 'unaccounted_stop'}])
        self.assertEqual(r['V2b.recorded_by_the_stop_hook'], 'fail')
        self.assertEqual(r['V2b.single_incident_line'], 'fail')

    def test_no_latch_leaves_one_failing_row(self):
        self.assertEqual(self.rows(latch=None), {'V2b.recorded_by_the_stop_hook': 'fail'})

    def test_a_restart_that_beat_the_record_fails(self):
        self.assertEqual(self.rows(daemon_start_wall=1099.0)['V2b.recorded_before_the_restart'], 'fail')

    def test_an_unknown_start_time_is_incomplete_not_a_pass(self):
        self.assertEqual(self.rows(daemon_start_wall=None)['V2b.recorded_before_the_restart'], 'not_run')

    def test_a_marker_nobody_closed_fails(self):
        self.assertEqual(self.rows(marker_after=marker())['V2b.run_marker_closed_and_reopened'], 'fail')
        self.assertEqual(self.rows(marker_after=ABSENT)['V2b.run_marker_closed_and_reopened'], 'fail')

    def test_a_contradicted_victim_fails_and_a_missing_evidence_file_fails(self):
        bad = {'evidence': dict(EVID['evidence'], victims=[{'pid': 78, 'comm': 'x'}])}
        self.assertEqual(self.rows(evidence_doc=bad)['V2b.victim_recorded_explicitly'], 'fail')
        self.assertEqual(self.rows(evidence_doc=None)['V2b.victim_recorded_explicitly'], 'fail')

    def test_the_rows_are_renamed_for_the_daemon_victim_scenario(self):
        rows = stage_v2.rename_rows(LC.evaluate_v2_lifecycle('V2b', LATCH, EVID, [], [{'kind': 'oom_kill'}], marker(), marker('inv-2', 1105.0), 1105.0, HV, 10, BOOT), 'V2b', 'V2c')
        self.assertTrue(all(r[0].startswith('V2c.') for r in rows))


class V3Rows(unittest.TestCase):
    def case(self, **over):
        a = dict(restarted=True, latch=None, paused=False, trips_new=[], marker_before=marker(), marker_after=marker('inv-2', 1105.0), marker_while_stopped=None, advances=True)
        a.update(over)
        rows = LC.evaluate_daemon_negative_control('daemon_kill9', a['restarted'], a['latch'], a['paused'], a['trips_new'], a['marker_before'], a['marker_after'], a['marker_while_stopped'], a['advances'], BOOT)
        return {r[0]: r[1] for r in rows}

    def test_a_stop_that_is_not_an_oom_records_nothing(self):
        r = self.case()
        self.assertEqual(set(r.values()), {'pass'}, r)

    def test_every_false_incident_fails(self):
        for over in (dict(latch={'kind': 'oom_kill', 'source': 'systemd'}), dict(paused=True), dict(trips_new=[{'kind': 'unaccounted_stop'}]), dict(trips_new=None), dict(advances=False)):
            self.assertEqual(self.case(**over)['V3e.daemon_kill9.no_false_incident'], 'fail', over)

    def test_the_marker_must_cycle(self):
        self.assertEqual(self.case(marker_after=marker())['V3e.daemon_kill9.marker_cycle'], 'fail')
        self.assertEqual(self.case(marker_after=ABSENT)['V3e.daemon_kill9.marker_cycle'], 'fail')

    def test_a_clean_stop_closes_the_marker_while_stopped(self):
        self.assertEqual(self.case(marker_while_stopped=ABSENT)['V3e.daemon_kill9.marker_closed_by_the_stop_hook'], 'pass')
        self.assertEqual(self.case(marker_while_stopped=marker())['V3e.daemon_kill9.marker_closed_by_the_stop_hook'], 'fail')

    def test_monitor_failures_leave_the_daemons_run_alone(self):
        ok = LC.evaluate_monitor_failures_left_daemon_alone([{'kind': 'monitor_silent'}, {'kind': 'monitor_silent'}], {'MainPID': 10}, {'MainPID': 10}, marker(), marker())
        self.assertEqual({r[1] for r in ok}, {'pass'})
        for trips in ([{'kind': 'oom_kill'}], [{'kind': 'unaccounted_stop'}], None):
            self.assertEqual(LC.evaluate_monitor_failures_left_daemon_alone(trips, {'MainPID': 10}, {'MainPID': 10}, marker(), marker())[0][1], 'fail', trips)
        self.assertEqual(LC.evaluate_monitor_failures_left_daemon_alone([], {'MainPID': 10}, {'MainPID': 11}, marker(), marker())[1][1], 'fail')
        self.assertEqual(LC.evaluate_monitor_failures_left_daemon_alone([], {'MainPID': 10}, {'MainPID': 10}, marker(), marker('inv-2'))[1][1], 'fail')


class V4GateFault(unittest.TestCase):
    def refusal(self, **over):
        a = dict(window=[{'t': 1, 'ActiveState': 'activating', 'MainPID': 0}, {'t': 2, 'ActiveState': 'failed', 'MainPID': 0}], pre_main_pid=10, before=marker(), after=marker(),
                 journal='{"at": 1, "incident": "unrecorded"}\n{"gate": "refused", "why": "x"}', held_pos=(5.0, 5.1), paused=True)
        a.update(over)
        return {r[0]: r[1] for r in LC.evaluate_gate_refusal(a['window'], a['pre_main_pid'], a['before'], a['after'], a['journal'], a['held_pos'], a['paused'])}

    def test_the_refusal_passes(self):
        r = self.refusal()
        self.assertEqual(set(r.values()), {'pass'}, r)

    def test_a_new_daemon_over_an_unrecorded_incident_fails(self):
        self.assertEqual(self.refusal(window=[{'t': 1, 'ActiveState': 'active', 'MainPID': 22}])['V4.gate.no_daemon_while_storage_broken'], 'fail')

    def test_the_old_pid_active_is_not_a_new_daemon(self):
        self.assertEqual(self.refusal(window=[{'t': 1, 'ActiveState': 'active', 'MainPID': 10}])['V4.gate.no_daemon_while_storage_broken'], 'pass')

    def test_no_samples_is_invalid(self):
        self.assertEqual(self.refusal(window=[])['V4.gate.no_daemon_while_storage_broken'], 'invalid')

    def test_not_held_and_a_moving_clock_fail(self):
        self.assertEqual(self.refusal(paused=False)['V4.gate.held_while_storage_broken'], 'fail')
        self.assertEqual(self.refusal(held_pos=(5.0, 20.0))['V4.gate.held_while_storage_broken'], 'fail')

    def test_a_marker_that_was_closed_or_replaced_fails(self):
        self.assertEqual(self.refusal(after=ABSENT)['V4.gate.marker_left_open'], 'fail')
        self.assertEqual(self.refusal(after=marker('inv-9'))['V4.gate.marker_left_open'], 'fail')

    def test_a_journal_without_the_refusal_fails(self):
        self.assertEqual(self.refusal(journal='started')['V4.gate.refusal_is_in_the_journal'], 'fail')
        self.assertEqual(self.refusal(journal='{"gate":"refused"}')['V4.gate.refusal_is_in_the_journal'], 'pass')

    def recovery(self, **over):
        prev = marker()
        a = dict(latch={'kind': 'unaccounted_stop', 'source': 'systemd', 'at': 1200.0}, before=prev, after=marker('inv-3', 1210.0), pre=10, post=33,
                 doc={'evidence': {'previous_run': {'invocation_id': 'inv-1'}, 'same_boot': True}}, hold={'paused': True, 'pos_a': 7.0, 'pos_b': 7.1, 'dt': 15.0, 'sha_before': 'a' * 64, 'sha_after': 'a' * 64})
        a.update(over)
        return {r[0]: r[1] for r in LC.evaluate_gate_recovery(a['latch'], a['before'], a['after'], a['pre'], a['post'], a['doc'], a['hold'], BOOT, 'by itself')}

    def test_recovery_latches_and_starts_held(self):
        r = self.recovery()
        self.assertEqual(set(r.values()), {'pass'}, r)

    def test_each_recovery_failure(self):
        self.assertEqual(self.recovery(latch=None)['V4.gate.latched_when_storage_recovered'], 'fail')
        self.assertEqual(self.recovery(latch={'kind': 'oom_kill', 'source': 'systemd'})['V4.gate.latched_when_storage_recovered'], 'fail')
        self.assertEqual(self.recovery(doc={'evidence': {'previous_run': {'invocation_id': 'other'}, 'same_boot': True}})['V4.gate.names_the_previous_run'], 'fail')
        self.assertEqual(self.recovery(doc={'evidence': {'previous_run': {'invocation_id': 'inv-1'}, 'same_boot': False}})['V4.gate.names_the_previous_run'], 'fail')
        self.assertEqual(self.recovery(doc=None)['V4.gate.names_the_previous_run'], 'fail')
        self.assertEqual(self.recovery(hold={'paused': False, 'pos_a': 7.0, 'pos_b': 7.1, 'dt': 15.0, 'sha_before': 'a', 'sha_after': 'a'})['V4.gate.started_held'], 'fail')
        self.assertEqual(self.recovery(hold={'paused': True, 'pos_a': 7.0, 'pos_b': 30.0, 'dt': 15.0, 'sha_before': 'a', 'sha_after': 'a'})['V4.gate.started_held'], 'fail')
        self.assertEqual(self.recovery(hold={'paused': True, 'pos_a': 7.0, 'pos_b': 7.0, 'dt': 15.0, 'sha_before': 'a', 'sha_after': 'b'})['V4.gate.started_held'], 'fail')
        self.assertEqual(self.recovery(hold={'paused': True, 'pos_a': 7.0, 'pos_b': 7.0, 'dt': 3.0, 'sha_before': 'a', 'sha_after': 'a'})['V4.gate.started_held'], 'fail')
        self.assertEqual(self.recovery(post=10)['V4.gate.started_held'], 'fail')           # the old daemon, not a new one
        self.assertEqual(self.recovery(after=ABSENT)['V4.gate.new_marker_open'], 'fail')
        self.assertEqual(self.recovery(after=marker('inv-1'))['V4.gate.new_marker_open'], 'fail')


class Reboot(unittest.TestCase):
    def marker_rows(self, mode, post, pre=None, boot_now='b00t-2'):
        return {r[0]: r[1] for r in LC.evaluate_reboot_marker(mode, pre or marker(), post, BOOT, boot_now)}

    def test_a_clean_reboot_must_have_closed_the_marker(self):
        self.assertEqual(self.marker_rows('clean', ABSENT)['V4.reboot.clean_shutdown_closed_the_marker'], 'pass')
        r = LC.evaluate_reboot_marker('clean', marker(), marker(), BOOT, 'b00t-2')
        bad = [x for x in r if x[0] == 'V4.reboot.clean_shutdown_closed_the_marker'][0]
        self.assertEqual(bad[1], 'fail')
        self.assertIn('EVERY reboot', bad[2])

    def test_a_hard_reset_must_leave_the_marker(self):
        self.assertEqual(self.marker_rows('hard', marker())['V4.reboot.hard_reset_left_the_marker'], 'pass')
        self.assertEqual(self.marker_rows('hard', ABSENT)['V4.reboot.hard_reset_left_the_marker'], 'fail')
        self.assertEqual(self.marker_rows('hard', marker('inv-other'))['V4.reboot.hard_reset_left_the_marker'], 'fail')

    def test_no_reboot_no_verdict(self):
        self.assertEqual(self.marker_rows('clean', ABSENT, boot_now=BOOT)['V4.reboot.host_restarted'], 'invalid')
        self.assertNotIn('V4.reboot.clean_shutdown_closed_the_marker', self.marker_rows('clean', ABSENT, boot_now=BOOT))

    def test_no_marker_before_the_reboot_is_invalid_not_a_pass(self):
        self.assertEqual(self.marker_rows('clean', ABSENT, pre=ABSENT)['V4.reboot.marker'], 'invalid')

    def start_rows(self, mode, new, latch_pre=None, latch_now=None, after=None):
        return {r[0]: r[1] for r in LC.evaluate_after_reboot_start(mode, latch_pre, latch_now, new, None, after or marker('inv-9', 5000.0), marker())}

    def test_a_clean_start_records_nothing(self):
        self.assertEqual(set(self.start_rows('clean', [], {'at': 1}, {'at': 1}).values()), {'pass'})
        self.assertEqual(self.start_rows('clean', [{'kind': 'unaccounted_stop'}])['V4.reboot.clean_start_recorded_nothing'], 'fail')

    def test_a_hard_start_records_exactly_one_unclean_stop_and_says_the_host_restarted(self):
        good = [{'kind': 'unaccounted_stop', 'detail': 'the host restarted since (boot id changed)'}]
        self.assertEqual(set(self.start_rows('hard', good).values()), {'pass'})
        self.assertEqual(self.start_rows('hard', [])['V4.reboot.hard_start_recorded_the_stop'], 'fail')
        self.assertEqual(self.start_rows('hard', good * 2)['V4.reboot.hard_start_recorded_the_stop'], 'fail')
        self.assertEqual(self.start_rows('hard', [{'kind': 'unaccounted_stop', 'detail': 'same boot'}])['V4.reboot.hard_start_says_the_host_restarted'], 'fail')

    def test_the_latch_from_before_stays_first_and_the_new_run_has_a_new_marker(self):
        self.assertEqual(self.start_rows('clean', [], {'at': 1}, {'at': 2})['V4.reboot.first_latch_kept'], 'fail')
        self.assertEqual(self.start_rows('clean', [], after=ABSENT)['V4.reboot.new_marker_open'], 'fail')
        self.assertEqual(self.start_rows('clean', [], after=marker())['V4.reboot.new_marker_open'], 'fail')


class Renderer(unittest.TestCase):
    def tpl(self, name='daemon'):
        return (ROOT / ('deploy/supervisor/dfl-broadcast-%s.service' % name)).read_text()

    def render(self):
        return RN.render_unit(self.tpl(), PIN)[0]

    def test_the_pinned_daemon_unit_carries_the_design_after_rendering(self):
        self.assertEqual(RN.lifecycle_problems(self.tpl(), self.render()), [])
        text = self.render()
        self.assertRegex(text, r'(?m)^ExecStartPre=/usr/bin/python3 -I /opt/dfl-vtest-src/[0-9a-f]{12}/tools/broadcast_run\.py gate --root /srv/dfl-vtest/root --public /srv/dfl-vtest/web/feed --guard /srv/dfl-vtest/guard$')
        self.assertIn('--cgroup /sys/fs/cgroup/system.slice/dfl-vtest-daemon.service', text)

    def test_each_way_the_design_can_go_missing_is_caught(self):
        t, r = self.tpl(), self.render()
        cases = {'no gate in the template': (re.sub(r'(?m)^ExecStartPre=.*\n', '', t), re.sub(r'(?m)^ExecStartPre=.*\n', '', r)),
                 'gate dropped by rendering': (t, re.sub(r'(?m)^ExecStartPre=.*\n', '', r)),
                 'no stop hook': (re.sub(r'(?m)^ExecStopPost=.*\n', '', t), re.sub(r'(?m)^ExecStopPost=.*\n', '', r)),
                 'a failing gate would be ignored': (t, r.replace('ExecStartPre=/usr', 'ExecStartPre=-/usr')),
                 'hook flags changed': (t, r.replace(' --guard /srv/dfl-vtest/guard --cgroup', ' --cgroup')),
                 'OOMPolicy gone': (t, r.replace('OOMPolicy=stop', 'OOMPolicy=continue')),
                 'wrong cgroup': (t, r.replace('system.slice/dfl-vtest-daemon.service', 'system.slice/other.service')),
                 'RequiresMountsFor in [Service]': (t, re.sub(r'(?m)^RequiresMountsFor=.*\n', '', r).replace('[Service]\n', '[Service]\nRequiresMountsFor=/x\n', 1))}
        for name, (tt, rr) in cases.items():
            self.assertTrue(RN.lifecycle_problems(tt, rr), name)

    def test_the_first_candidates_template_is_refused_as_not_the_design(self):
        old = subprocess.run(['git', '-C', str(ROOT), 'show', 'bfa632b0e8961d9d576bdcded86abea90a3cc675:deploy/supervisor/dfl-broadcast-daemon.service'], capture_output=True, text=True).stdout
        self.assertTrue(old)
        text = RN.render_unit(old, 'bfa632b0e8961d9d576bdcded86abea90a3cc675')[0]
        self.assertTrue(RN.lifecycle_problems(old, text))

    def test_install_refuses_a_daemon_unit_without_the_design(self):
        env = mock.Mock()
        env.exists.return_value = True
        stripped = re.sub(r'(?m)^ExecStopPost=.*\n', '', self.tpl())
        real_open = open

        def fake_open(path, *a, **k):
            if str(path).endswith('dfl-broadcast-daemon.service'):
                import io
                return io.StringIO(stripped)
            return real_open(path, *a, **k)
        with mock.patch.object(RN, 'extract_tree', return_value=(True, ['ok'])), mock.patch('builtins.open', fake_open), mock.patch.object(RN.os, 'makedirs'):
            with self.assertRaises(C.Unsafe) as cm:
                RN.install(env, str(ROOT), pin=PIN)
        self.assertIn('lifecycle design', str(cm.exception))


class GuardFault(unittest.TestCase):
    def env(self, rc_chattr=0, flags='----i---------e-------'):
        e = C.Env()
        calls = []

        def run(argv, timeout=60, input=None):
            calls.append(argv)
            if argv[0] == 'chattr':
                return rc_chattr, '', 'Operation not supported' if rc_chattr else ''
            return 0, '%s %s\n' % (flags, argv[-1]), ''
        e.run = run
        return e, calls

    def test_only_the_guard_folder_can_be_made_immutable(self):
        e, calls = self.env()
        for bad in ('/srv/dfl-vtest/root', '/etc', '/srv/dfl-vtest/guard/evidence', '/'):
            with self.assertRaises(C.Unsafe):
                e.immutable(bad, True)
        self.assertEqual(calls, [])

    def test_the_fault_is_set_and_read_back(self):
        e, calls = self.env()
        self.assertEqual(e.immutable(C.GUARD, True)[0], True)
        self.assertEqual(calls[0], ['chattr', '+i', C.GUARD])

    def test_removal_is_verified_too(self):
        e, _ = self.env(flags='--------------e-------')
        self.assertEqual(e.immutable(C.GUARD, False)[0], True)
        e2, _ = self.env(flags='----i---------e-------')
        ok, why = e2.immutable(C.GUARD, False)
        self.assertFalse(ok)
        self.assertIn('lsattr', why)

    def test_a_filesystem_that_cannot_do_it_is_reported_not_ignored(self):
        e, _ = self.env(rc_chattr=1)
        ok, why = e.immutable(C.GUARD, True)
        self.assertFalse(ok)
        self.assertIn('chattr rc 1', why)

    def test_the_stage_never_leaves_the_folder_immutable(self):
        src = (VT / 'stage_v4.py').read_text()
        body = src.split('def _gate_failure', 1)[1].split('def evaluate_gate_refusal_rows', 1)[0]
        self.assertIn('finally:', body)
        self.assertIn('env.immutable(GUARD, False)', body.split('finally:', 1)[1].split('\n    if not ok2', 1)[0])
        self.assertIn('STILL IMMUTABLE', body)


class StageWiring(unittest.TestCase):
    def test_the_plans_name_the_new_steps(self):
        self.assertIn('V3e.daemon_kill9', [s for s, _ in stage_v3.PLAN])
        self.assertIn('V3.monitor_failures_summary', [s for s, _ in stage_v3.PLAN])
        self.assertIn('V4.gate', [s for s, _ in stage_v4.PLAN])

    def test_v4_defaults_carry_the_numbers_the_gate_step_reads(self):
        for mode in ('qualifying', 'smoke'):
            for k in ('overrun_rate', 'gate_watch_s', 'gate_wait_s', 'gate_hold_s'):
                self.assertIn(k, stage_v4.DEFAULTS[mode])

    def test_the_marker_row_for_a_monitor_only_disturbance_wants_the_same_run(self):
        same = stage_v4.marker_row('monitor_restart', marker(), marker(), False, BOOT)
        self.assertEqual(same[1], 'pass')
        self.assertEqual(stage_v4.marker_row('monitor_restart', marker(), marker('inv-2', 1105.0), False, BOOT)[1], 'fail')
        self.assertEqual(stage_v4.marker_row('daemon_kill9', marker(), marker('inv-2', 1105.0), True, BOOT)[1], 'pass')
        self.assertEqual(stage_v4.marker_row('daemon_kill9', marker(), marker(), True, BOOT)[1], 'fail')

    def test_the_command_line_has_a_hard_reset_flag(self):
        src = (VT / 'vtest.py').read_text()
        self.assertIn('--after-hard-reset', src)
        self.assertIn('run_after_reboot(ctx, "hard" if a.after_hard_reset else "clean")', src)


# ------------------------------------------------------------------------------------------------------------------------------ the stages' glue, driven by a scripted machine
import os          # noqa: E402
import tempfile    # noqa: E402
import scenarios   # noqa: E402


class ScriptedMachine:
    """Just enough of a machine for the stage glue: a clock that advances only when something sleeps, a guard folder that can be 'immutable', and a daemon unit that follows the design."""
    def __init__(self, restore_fails=False, fault_effect=True, journal_has_refusal=True):
        self.t, self.fault, self.restore_fails, self.journal_has_refusal = 1000.0, False, restore_fails, journal_has_refusal
        self.calls, self.fault_log = [], []
        self.t_fault_off = None
        self.started = False

    # Env facade
    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s

    def monotonic(self):
        return self.t

    def boot_id(self):
        return BOOT

    def immutable(self, path, on):
        self.fault_log.append(on)
        if on:
            self.fault = True
            return True, 'i'
        if self.restore_fails:
            return False, 'lsattr after the change says i'
        self.fault = False
        self.t_fault_off = self.t
        return True, '-'

    def sc(self, verb, *a, timeout=60):
        self.calls.append((verb, a))
        return 0, '', ''

    def run(self, argv, timeout=60, input=None):
        return 3, '', ''                                # `resume` is refused while a latch exists

    def exists(self, p):
        return p.endswith('evidence.json') or p.endswith('/evidence')

    def listdir(self, p):
        return ['trip-1-unaccounted_stop']

    def read(self, p, default=None):
        return json.dumps({'kind': 'unaccounted_stop', 'evidence': {'previous_run': {'invocation_id': 'inv-1'}, 'same_boot': True}})

    # what the daemon unit looks like
    def daemon_back(self):
        return self.t_fault_off is not None and self.t - self.t_fault_off >= 6


class Rec:
    def __init__(self):
        self.rows = []

    def check(self, cid, status, detail, data=None, required=True):
        self.rows.append((cid, status, detail))

    def add_all(self, rows):
        for r in rows:
            self.check(*r)

    def stop_if_failed(self):
        bad = [r for r in self.rows if r[1] in ('fail', 'invalid')]
        if bad:
            raise C.StageStop('%s: %s' % (bad[0][0], bad[0][2]))


def make_ctx(m, rec):
    d = tempfile.mkdtemp()
    cfg = dict(stage_v4.DEFAULTS['smoke'], mode='smoke')
    ctx = scenarios.Ctx(m, rec, cfg, '/opt/x', d)
    ctx.ensure_baseline = lambda reason='': None
    ctx.limit_of = lambda k: 192 * 1024 * 1024
    ctx.loader = lambda *a, **k: mock.Mock(stop=mock.Mock(), alive=lambda: True)
    ctx.run_marker = lambda: marker() if (m.fault or not m.daemon_back()) else marker('inv-3', m.t)
    ctx.trips = lambda: []
    resumed = {'v': False}
    ctx.position = lambda: m.t if resumed['v'] else 7.0
    ctx.paused = lambda: True
    ctx.heartbeat_age = lambda: 3.0

    def release_and_resume(why):
        resumed['v'] = True
        return 0, 0, ''
    ctx.release_and_resume = release_and_resume
    ctx.journal = lambda k, since: ('{"gate": "refused", "why": "x"}\n' if (m.fault and m.journal_has_refusal) else 'started')
    ctx.active = lambda k: ctx.show(k).get('ActiveState') == 'active'
    ctx.show = lambda k, props=(): ({'ActiveState': 'active', 'MainPID': 33} if m.daemon_back() else ({'ActiveState': 'activating', 'MainPID': 0} if m.t > 1000.0 else {'ActiveState': 'active', 'MainPID': 10}))
    sha = 'a' * 64
    ctx.latch = lambda: (({'kind': 'unaccounted_stop', 'source': 'systemd', 'at': 1100.0}, sha) if m.daemon_back() else (None, None))
    return ctx


class GateFailureGlue(unittest.TestCase):
    def run_it(self, **kw):
        m = ScriptedMachine(**kw)
        rec = Rec()
        ctx = make_ctx(m, rec)
        try:
            stage_v4._gate_failure(ctx, rec)
            err = None
        except C.StageStop as e:
            err = e
        return m, rec, err

    def test_the_designed_behaviour_passes_every_row_and_the_fault_is_always_removed(self):
        m, rec, err = self.run_it()
        self.assertIsNone(err)
        bad = [r for r in rec.rows if r[1] not in ('pass', 'info')]
        self.assertEqual(bad, [])
        self.assertEqual(m.fault_log, [True, False])
        self.assertIn('V4.gate.recoverable_the_documented_way', [r[0] for r in rec.rows])
        self.assertFalse(m.fault)

    def test_a_fault_that_cannot_be_removed_stops_the_stage_and_says_how_to_fix_it(self):
        m, rec, err = self.run_it(restore_fails=True)
        self.assertIsNotNone(err)
        self.assertIn('STILL IMMUTABLE', str(err))
        self.assertIn('chattr -i', str(err))

    def test_a_filesystem_that_cannot_make_the_fault_is_invalid_not_a_pass(self):
        m = ScriptedMachine()
        m.immutable = lambda path, on: (False, 'chattr rc 1: Operation not supported')
        rec = Rec()
        stage_v4._gate_failure(make_ctx(m, rec), rec)
        self.assertEqual([(r[0], r[1]) for r in rec.rows], [('V4.gate.fault_applied', 'invalid')])

    def test_an_exception_during_the_fault_still_removes_it(self):
        m = ScriptedMachine()
        rec = Rec()
        ctx = make_ctx(m, rec)
        ctx.loader = mock.Mock(side_effect=RuntimeError('boom'))
        with self.assertRaises(RuntimeError):
            stage_v4._gate_failure(ctx, rec)
        self.assertEqual(m.fault_log, [True, False])

    def test_a_unit_that_never_comes_back_by_itself_is_reset_the_documented_way(self):
        m = ScriptedMachine()
        rec = Rec()
        ctx = make_ctx(m, rec)
        orig = m.daemon_back
        m.daemon_back = lambda: bool(any(v == 'start' for v, _ in m.calls)) and orig()
        stage_v4._gate_failure(ctx, rec)
        verbs = [v for v, _ in m.calls]
        self.assertEqual(verbs[:2], ['reset-failed', 'start'])
        self.assertIn('after reset-failed and start', ' '.join(str(r[2]) for r in rec.rows))


class NegativeControlGlue(unittest.TestCase):
    def test_a_daemon_stop_that_is_not_an_oom_passes_when_nothing_is_recorded(self):
        m = ScriptedMachine()
        rec = Rec()
        ctx = make_ctx(m, rec)
        state = {'restarted': False}
        ctx.run_marker = lambda: marker('inv-2', m.t) if state['restarted'] else marker()
        ctx.show = lambda k, props=(): {'ActiveState': 'active', 'MainPID': 22 if state['restarted'] else 10}
        ctx.latch = lambda: (None, None)
        ctx.position = mock.Mock(side_effect=[1.0, 21.0])
        ctx.paused = lambda: False

        def act():
            state['restarted'] = True
        rows = stage_v3._daemon_case(ctx, 'daemon_kill9', act)
        self.assertEqual({r[1] for r in rows}, {'pass'}, rows)


class RebootGlue(unittest.TestCase):
    def go(self, mode, units_active=False, marker_post=ABSENT, trips_now=None):
        m = ScriptedMachine()
        m.boot_id = lambda: 'b00t-2'
        rec = Rec()
        ctx = make_ctx(m, rec)
        pre = {'fp': {'sha256': 'a' * 64, 'size': 1, 'mtime': 1.0}, 'trips': [{'kind': 'memory_high'}], 'marker': marker(), 'boot_id': BOOT, 'latch': {'kind': 'memory_high', 'at': 5.0}}
        m.read = lambda p, default=None: json.dumps(pre)
        started = {'v': units_active}
        ctx.start = lambda *ks: started.update(v=True)
        ctx.active = lambda k: started['v']
        ctx.run_marker = lambda: (marker_post if not started['v'] else marker('inv-9', 5000.0))
        ctx.latch = lambda: (pre['latch'], 'x')
        ctx.trips = lambda: pre['trips'] + (trips_now or [])
        ctx.public_inventory = lambda: {}
        m.exists = lambda p: True
        with mock.patch.object(stage_v4, 'fingerprint', return_value=pre['fp']):
            stage_v4.run_after_reboot(ctx, mode)
        return rec

    def test_a_clean_reboot_that_closed_the_marker_passes_before_and_after_the_start(self):
        rec = self.go('clean')
        ids = {r[0]: r[1] for r in rec.rows}
        self.assertEqual(ids['V4.reboot.clean_shutdown_closed_the_marker'], 'pass')
        self.assertEqual(ids['V4.reboot.clean_start_recorded_nothing'], 'pass')

    def test_a_clean_reboot_that_left_the_marker_fails_before_anything_starts(self):
        with self.assertRaises(C.StageStop):
            self.go('clean', marker_post=marker())

    def test_units_started_before_the_check_make_the_marker_rows_invalid(self):
        rec = self.go('clean', units_active=True)
        self.assertIn(('V4.reboot.marker', 'invalid'), [(r[0], r[1]) for r in rec.rows])

    def test_a_hard_reset_that_kept_the_marker_and_recorded_one_unclean_stop_passes(self):
        rec = self.go('hard', marker_post=marker(), trips_now=[{'kind': 'unaccounted_stop', 'detail': 'the host restarted since (boot id changed)'}])
        ids = {r[0]: r[1] for r in rec.rows}
        self.assertEqual(ids['V4.reboot.hard_reset_left_the_marker'], 'pass')
        self.assertEqual(ids['V4.reboot.hard_start_recorded_the_stop'], 'pass')


if __name__ == '__main__':
    unittest.main()
