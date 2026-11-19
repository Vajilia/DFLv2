"""V5, the alert drill, and the installation of the alert package on a validation machine. Written before tools/vtest/stage_v5.py and tools/vtest/install_alerts.py existed.

What V5 must judge (owner, 2026-11-12):
  * a monitor that fails or is not running, and a daemon whose start budget is exhausted, are DETECTED without the monitor, recorded, and delivered once;
  * delivery attempted is not notification received: only an acknowledgement makes it received, and a failed hand-off is recorded and retried, never dropped;
  * the alert path NEVER releases a latch, resumes publication, resets a failure or restarts a unit: after the alert has gone out, the failed unit is still failed, until a person acts;
  * the website keeps answering while the simulation is down (website uptime does not prove simulation health);
  * the evidence says which exact revisions and effective systemd settings were in force.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools' / 'vtest'))
sys.path.insert(0, str(ROOT / 'alerts'))
import common as C            # noqa: E402
import stage_v5 as V5         # noqa: E402
import install_alerts as IA   # noqa: E402
import release_config as RC   # noqa: E402

ALERT_UNIT = 'dfl-vtest-alert@%n.service'


def incident(event='monitor_not_running', unit=C.UNIT['monitor'], first=1000.0, state='attempted_ok', received=None, attempts=None, cleared=None, iid=None, severity='critical'):
    iid = iid or '20270115T000000Z-%s-%s' % (event, unit.replace('.service', ''))
    att = attempts if attempts is not None else [{'at': first + 1, 'kind': 'initial', 'ok': state == 'attempted_ok', 'detail': 'exit 0'}]
    return {'id': iid, 'signature': '%s:%s' % (event, unit), 'event': event, 'unit': unit, 'severity': severity, 'first_seen': first, 'last_seen': first, 'observed': {},
            'commands_run': [['systemctl', 'show', unit]], 'delivery': {'state': state, 'attempts': att, 'next_retry_at': None}, 'notification_received': received, 'cleared': cleared}


def sink_for(*incs, extra=''):
    return ''.join('[DFL CRITICAL] vtest: %s\nWhat: x.\nIncident: %s\nDo not reset blindly.\n' % (i['unit'], i['id']) for i in incs) + extra


def by_id(rows):
    return {r[0]: r for r in rows}


def status(rows, cid):
    return by_id(rows)[cid][1]


class Installed(unittest.TestCase):
    def good(self):
        d = {'OnFailure': ALERT_UNIT.replace('%n', C.UNIT['daemon']), 'DropInPaths': '/etc/systemd/system/%s.d/50-dfl-alert.conf' % C.UNIT['daemon'], 'FragmentPath': '/etc/systemd/system/' + C.UNIT['daemon'], 'ActiveState': 'active'}
        m = {'OnFailure': ALERT_UNIT.replace('%n', C.UNIT['monitor']), 'DropInPaths': '/etc/systemd/system/%s.d/50-dfl-alert.conf' % C.UNIT['monitor'], 'FragmentPath': '/etc/systemd/system/' + C.UNIT['monitor'], 'ActiveState': 'active'}
        t = {'ActiveState': 'active', 'SubState': 'waiting', 'NextElapseUSecRealtime': 'x'}
        return d, m, t

    def test_installed_package_passes(self):
        d, m, t = self.good()
        rows = V5.evaluate_installed(d, m, t, 'dfl-vtest-', record_ok=True)
        self.assertTrue(all(r[1] == 'pass' for r in rows), rows)

    def test_each_missing_piece_fails_on_its_own_row(self):
        d, m, t = self.good()
        for label, args, cid in (('daemon without OnFailure', (dict(d, OnFailure=''), m, t), 'V5.installed.daemon_dropin'),
                                 ('monitor without a drop-in', (d, dict(m, DropInPaths=''), t), 'V5.installed.monitor_dropin'),
                                 ('timer not running', (d, m, dict(t, ActiveState='inactive')), 'V5.installed.poll_timer')):
            rows = V5.evaluate_installed(*args, 'dfl-vtest-', record_ok=True)
            self.assertEqual(status(rows, cid), 'fail', label)
        rows = V5.evaluate_installed(d, m, t, 'dfl-vtest-', record_ok=False)
        self.assertEqual(status(rows, 'V5.installed.release_record'), 'fail')

    def test_a_dropin_that_names_another_unit_is_not_accepted(self):
        d, m, t = self.good()
        rows = V5.evaluate_installed(dict(d, OnFailure='dfl-vtest-alert@dfl-vtest-site.service'), m, t, 'dfl-vtest-', record_ok=True)
        self.assertEqual(status(rows, 'V5.installed.daemon_dropin'), 'fail')


class Baseline(unittest.TestCase):
    def test_a_healthy_period_with_the_timer_running_raises_nothing(self):
        rows = V5.evaluate_baseline([], {'last_poll': 1000.0, 'open': []}, 1030.0, 150)
        self.assertTrue(all(r[1] == 'pass' for r in rows), rows)

    def test_a_false_alarm_fails(self):
        rows = V5.evaluate_baseline([incident()], {'last_poll': 1000.0, 'open': []}, 1030.0, 150)
        self.assertEqual(status(rows, 'V5.baseline.no_false_alarm'), 'fail')

    def test_a_timer_that_never_ran_or_has_gone_quiet_fails(self):
        self.assertEqual(status(V5.evaluate_baseline([], None, 1030.0, 150), 'V5.baseline.poll_ran'), 'fail')
        self.assertEqual(status(V5.evaluate_baseline([], {'last_poll': 100.0, 'open': []}, 1030.0, 150), 'V5.baseline.poll_ran'), 'fail')


class Detection(unittest.TestCase):
    def rows(self, new, sink=None, t_fault=1000.0, events=('monitor_not_running',), journal='', via_event_expected=False):
        sink = sink_for(*new) if sink is None else sink
        return V5.evaluate_detection('monitor_stopped', events, new, sink, t_fault, max_s=300, alert_journal=journal)

    def test_detected_recorded_delivered_once_and_not_claimed_received(self):
        rows = self.rows([incident(first=1100.0)])
        for cid in ('recorded', 'delivered_once', 'not_claimed_received', 'within_time'):
            self.assertEqual(status(rows, 'V5.monitor_stopped.' + cid), 'pass', (cid, rows))

    def test_nothing_recorded_fails_and_says_what_was_there(self):
        rows = self.rows([])
        self.assertEqual(status(rows, 'V5.monitor_stopped.recorded'), 'fail')

    def test_too_late_fails(self):
        self.assertEqual(status(self.rows([incident(first=1500.0)]), 'V5.monitor_stopped.within_time'), 'fail')

    def test_an_incident_of_another_kind_does_not_count(self):
        self.assertEqual(status(self.rows([incident(event='daemon_failed', unit=C.UNIT['daemon'], first=1100.0)]), 'V5.monitor_stopped.recorded'), 'fail')

    def test_two_messages_for_one_incident_fail_the_dedupe_check(self):
        i = incident(first=1100.0)
        self.assertEqual(status(self.rows([i], sink=sink_for(i, i)), 'V5.monitor_stopped.delivered_once'), 'fail')

    def test_two_incidents_for_one_condition_fail_the_dedupe_check(self):
        a, b = incident(first=1100.0), incident(first=1110.0, iid='other-id')
        self.assertEqual(status(self.rows([a, b], sink=sink_for(a, b)), 'V5.monitor_stopped.delivered_once'), 'fail')

    def test_handoff_ok_but_no_message_in_the_sink_fails(self):
        self.assertEqual(status(self.rows([incident(first=1100.0)], sink=''), 'V5.monitor_stopped.delivered_once'), 'fail')

    def test_an_incident_that_claims_to_be_received_without_anyone_acknowledging_fails(self):
        i = incident(first=1100.0, received={'at': 1101.0, 'by': 'alert', 'via': 'auto'})
        self.assertEqual(status(self.rows([i]), 'V5.monitor_stopped.not_claimed_received'), 'fail')

    def test_the_detection_path_is_reported_not_judged(self):
        rows = by_id(self.rows([incident(first=1100.0)], journal='Starting DFL failure alert for %s...' % C.UNIT['monitor']))
        self.assertEqual(rows['V5.monitor_stopped.detection_path'][1], 'info')
        self.assertIn('failure hook', rows['V5.monitor_stopped.detection_path'][2])
        rows = by_id(self.rows([incident(first=1100.0)], journal=''))
        self.assertIn('poll', rows['V5.monitor_stopped.detection_path'][2])
        self.assertFalse(rows['V5.monitor_stopped.detection_path'][4])


UNIT_BEFORE = {'ActiveState': 'failed', 'SubState': 'failed', 'Result': 'exit-code', 'InvocationID': 'abc', 'NRestarts': 0, 'MainPID': 0}


class NeverRecovers(unittest.TestCase):
    def state(self, **over):
        return {'unit': dict(UNIT_BEFORE), 'latch_sha': 'f' * 64, 'trips': 3, 'released': 1, 'paused': False, **over}

    def test_nothing_changed_passes(self):
        self.assertEqual(status(V5.evaluate_never_recovers('s', self.state(), self.state(), 90), 'V5.s.alert_did_not_recover'), 'pass')

    def test_each_recovery_action_is_caught(self):
        for label, after in (('restarted', self.state(unit=dict(UNIT_BEFORE, ActiveState='active', InvocationID='new', Result='success'))),
                             ('reset-failed', self.state(unit=dict(UNIT_BEFORE, ActiveState='inactive', Result='success'))),
                             ('new invocation', self.state(unit=dict(UNIT_BEFORE, InvocationID='other'))),
                             ('latch released', self.state(latch_sha=None)),
                             ('latch replaced', self.state(latch_sha='0' * 64)),
                             ('release record added', self.state(released=2)),
                             ('trips rewritten', self.state(trips=1))):
            rows = V5.evaluate_never_recovers('s', self.state(), after, 90)
            self.assertEqual(status(rows, 'V5.s.alert_did_not_recover'), 'fail', label)

    def test_a_pause_that_appears_is_reported_not_blamed(self):
        """The daemon pauses itself when the monitor's heartbeat goes stale (its own rule): not the alert path's doing."""
        rows = by_id(V5.evaluate_never_recovers('s', self.state(), self.state(paused=True), 90))
        self.assertEqual(rows['V5.s.alert_did_not_recover'][1], 'pass')
        self.assertEqual(rows['V5.s.pause_observed'][1], 'info')

class IndependentSafetyTrip(unittest.TestCase):
    """The daemon trips itself (kind monitor_silent, source daemon) once the monitor's heartbeat has been silent for GUARD_STALE_S: its own safety rule, not the alert path's.
    A stopped monitor stays stopped for the hold, so that latch can legitimately appear during the hold. The judge accepts it only with PROOF of who wrote it, why and when;
    anything else that changes the latch is still the alert path (or something unexplained) acting, and fails."""
    SINCE, LAST_HB = 950.0, 947.0
    DOWN = {'since': 950.0, 'last_heartbeat': 947.0, 'stale_s': 120.0}
    REC = {'kind': 'monitor_silent', 'detail': 'no heartbeat from the monitor for 121 s', 'source': 'daemon', 'at': 1070.2}

    def before(self, **over):
        return {'unit': dict(UNIT_BEFORE), 'latch_sha': None, 'latch': None, 'trips': 0, 'trip_records': [], 'released': 1, 'paused': False, 'at': 1040.0, **over}

    def after(self, rec=None, **over):
        rec = dict(self.REC) if rec is None else rec
        return {'unit': dict(UNIT_BEFORE), 'latch_sha': 'a' * 64, 'latch': rec, 'trips': 1, 'trip_records': [dict(rec)], 'released': 1, 'paused': True, 'at': 1100.0, **over}

    def judge(self, after, before=None, down='default'):
        down = self.DOWN if down == 'default' else down
        rows = V5.evaluate_never_recovers('s', before or self.before(), after, 60, monitor_down=down)
        return by_id(rows)

    def test_the_daemons_own_stale_heartbeat_trip_during_the_hold_passes_and_is_recorded_with_its_proof(self):
        rows = self.judge(self.after())
        self.assertEqual(rows['V5.s.alert_did_not_recover'][1], 'pass', rows['V5.s.alert_did_not_recover'][2])
        r = rows['V5.s.independent_safety_trip']
        self.assertEqual(r[1], 'info')
        self.assertFalse(r[4], 'recorded, not itself a required check')
        self.assertIn('daemon', r[2])
        self.assertIn('monitor_silent', r[2])
        self.assertEqual(r[3]['latch']['at'], 1070.2)

    def test_without_a_monitor_that_was_stopped_by_the_harness_the_same_latch_is_unproven_and_fails(self):
        for down in (None, {}):
            r = self.judge(self.after(), down=down)['V5.s.alert_did_not_recover']
            self.assertEqual(r[1], 'fail', down)
            self.assertIn('latch changed', r[2])

    def test_each_failure_of_the_proof_fails(self):
        base = dict(self.REC)
        cases = {
            'written by the alert path': self.after(dict(base, source='alert')),
            'written by an operator': self.after(dict(base, source='operator')),
            'no writer recorded': self.after({k: v for k, v in base.items() if k != 'source'}),
            'an OOM latch': self.after(dict(base, kind='oom_kill', source='systemd')),
            'an unaccounted-stop latch': self.after(dict(base, kind='unaccounted_stop')),
            'a different reason for the daemon trip': self.after(dict(base, detail='the monitor wrote no new heartbeat in 300 s since this daemon started')),
            'written before the hold began': self.after(dict(base, at=1039.0)),
            'written after the hold ended': self.after(dict(base, at=1103.0)),
            'written before the heartbeat could have been stale': self.after(dict(base, at=1060.0)),
            'no time on the latch': self.after({k: v for k, v in base.items() if k != 'at'}),
            'the latch could not be read': self.after({'unreadable': True}),
            'no latch document at all': self.after(None, latch=None),
            'no new line in trips.jsonl': self.after(trips=0, trip_records=[]),
            'two new lines in trips.jsonl': self.after(trips=2, trip_records=[dict(base), dict(base, at=1071.0)]),
            'the new trips line differs from the latch': self.after(trip_records=[dict(base, detail='other')]),
            'no PAUSE with the latch': self.after(paused=False),
        }
        for label, after in cases.items():
            r = self.judge(after)['V5.s.alert_did_not_recover']
            self.assertEqual(r[1], 'fail', label)

    def test_the_monitors_last_heartbeat_must_be_known(self):
        self.assertEqual(self.judge(self.after(), down=dict(self.DOWN, last_heartbeat=None))['V5.s.alert_did_not_recover'][1], 'fail')

    def test_a_proven_trip_does_not_excuse_a_recovery_action_made_at_the_same_time(self):
        for label, after in (('release record added', self.after(released=2)),
                             ('unit restarted', self.after(unit=dict(UNIT_BEFORE, ActiveState='active', InvocationID='new', Result='success'))),
                             ('unit reset', self.after(unit=dict(UNIT_BEFORE, ActiveState='inactive', Result='success'))),
                             ('earlier trips lost', self.after(trips=0, trip_records=[]))):
            self.assertEqual(self.judge(after)['V5.s.alert_did_not_recover'][1], 'fail', label)

    def test_an_existing_latch_that_is_altered_or_cleared_is_never_excused(self):
        existing = dict(self.REC, at=900.0)
        before = self.before(latch_sha='b' * 64, latch=existing, trips=1, trip_records=[existing], paused=True)
        legit_looking = dict(self.REC)
        replaced = self.after(legit_looking, trips=2, trip_records=[existing, legit_looking])
        cleared = self.after(None, latch_sha=None, latch=None, trips=1, trip_records=[existing])
        self.assertEqual(self.judge(replaced, before=before)['V5.s.alert_did_not_recover'][1], 'fail')
        self.assertEqual(self.judge(cleared, before=before)['V5.s.alert_did_not_recover'][1], 'fail')
        unchanged = dict(before, at=1100.0)
        self.assertEqual(self.judge(unchanged, before=before)['V5.s.alert_did_not_recover'][1], 'pass')

    def test_the_latch_vanishing_is_never_excused_even_with_a_stopped_monitor(self):
        before = self.before(latch_sha='b' * 64, latch=dict(self.REC, at=900.0), trips=1, trip_records=[dict(self.REC, at=900.0)])
        after = self.after(None, latch_sha=None, latch=None, trips=1, trip_records=[dict(self.REC, at=900.0)])
        self.assertEqual(self.judge(after, before=before)['V5.s.alert_did_not_recover'][1], 'fail')


class Retry(unittest.TestCase):
    def failed_then_ok(self, retry_ok=True, received=None, extra_sink=0):
        i = incident(first=1100.0, state='attempted_ok' if retry_ok else 'attempted_failed', received=received,
                     attempts=[{'at': 1101.0, 'kind': 'initial', 'ok': False, 'detail': 'exit 1'}] + ([{'at': 1170.0, 'kind': 'retry', 'ok': True, 'detail': 'exit 0'}] if retry_ok else []))
        return i

    def test_a_failed_handoff_is_recorded_then_retried_and_delivered_once(self):
        i = self.failed_then_ok()
        mid = incident(first=1100.0, state='attempted_failed', attempts=[{'at': 1101.0, 'kind': 'initial', 'ok': False, 'detail': 'exit 1'}])
        rows = V5.evaluate_retry('s', mid, '', i, sink_for(i))
        for cid in ('failure_recorded', 'retried_and_delivered', 'delivered_once_after_retry', 'still_not_received'):
            self.assertEqual(status(rows, 'V5.s.' + cid), 'pass', (cid, rows))

    def test_a_failure_that_was_not_recorded_fails(self):
        i = self.failed_then_ok()
        mid = incident(first=1100.0, state='attempted_ok')
        self.assertEqual(status(V5.evaluate_retry('s', mid, '', i, sink_for(i)), 'V5.s.failure_recorded'), 'fail')

    def test_a_message_that_reached_the_sink_while_the_channel_was_down_fails(self):
        i = self.failed_then_ok()
        mid = incident(first=1100.0, state='attempted_failed', attempts=[{'at': 1101.0, 'kind': 'initial', 'ok': False, 'detail': 'exit 1'}])
        self.assertEqual(status(V5.evaluate_retry('s', mid, sink_for(i), i, sink_for(i)), 'V5.s.failure_recorded'), 'fail')

    def test_no_retry_fails(self):
        i = self.failed_then_ok(retry_ok=False)
        mid = incident(first=1100.0, state='attempted_failed', attempts=[{'at': 1101.0, 'kind': 'initial', 'ok': False, 'detail': 'exit 1'}])
        self.assertEqual(status(V5.evaluate_retry('s', mid, '', i, ''), 'V5.s.retried_and_delivered'), 'fail')


class Acknowledgement(unittest.TestCase):
    def test_only_an_acknowledgement_makes_it_received(self):
        before = incident(first=1100.0)
        after = incident(first=1100.0, received={'at': 1200.0, 'by': 'vtest-harness', 'via': 'harness'})
        rows = V5.evaluate_ack('s', before, after)
        self.assertEqual(status(rows, 'V5.s.received_only_after_ack'), 'pass')
        self.assertIn('not a person', by_id(rows)['V5.s.received_only_after_ack'][2])

    def test_unchanged_after_ack_fails(self):
        self.assertEqual(status(V5.evaluate_ack('s', incident(), incident()), 'V5.s.received_only_after_ack'), 'fail')

    def test_already_received_before_ack_fails(self):
        r = {'at': 1.0, 'by': 'x', 'via': 'y'}
        self.assertEqual(status(V5.evaluate_ack('s', incident(received=r), incident(received=r)), 'V5.s.received_only_after_ack'), 'fail')


class SiteVsSimulation(unittest.TestCase):
    def test_site_up_while_the_clock_is_frozen_is_recorded_as_the_demonstration(self):
        rows = V5.evaluate_site_vs_simulation(200, 120.0, 120.2, daemon_active=False)
        self.assertEqual(rows[0][1], 'info')
        self.assertIn('does not prove', rows[0][2])
        self.assertFalse(rows[0][4])

    def test_a_site_that_did_not_answer_is_invalid_not_a_finding(self):
        self.assertEqual(V5.evaluate_site_vs_simulation(None, 1.0, 1.0, daemon_active=False)[0][1], 'invalid')

    def test_a_daemon_still_running_makes_the_demonstration_invalid(self):
        self.assertEqual(V5.evaluate_site_vs_simulation(200, 1.0, 1.0, daemon_active=True)[0][1], 'invalid')


class MonitorFault(unittest.TestCase):
    """The exit-2 drill needs a monitor that FAILED while the guard folder was unwritable. Whether the exit status is the 2 the design predicts is a finding about the candidate, not a condition of the alert drill."""
    def test_a_failed_monitor_with_exit_2_makes_the_fault_and_no_finding(self):
        rows = by_id(V5.evaluate_monitor_fault({'ActiveState': 'failed', 'Result': 'exit-code', 'ExecMainStatus': 2}))
        self.assertEqual(rows['V5.monitor_exit2.fault_produced'][1], 'pass')
        self.assertEqual(rows['V5.monitor_exit2.exit_status'][1], 'info')

    def test_a_failed_monitor_with_another_status_still_tests_the_alert_but_is_recorded_for_review(self):
        rows = by_id(V5.evaluate_monitor_fault({'ActiveState': 'failed', 'Result': 'exit-code', 'ExecMainStatus': 1}))
        self.assertEqual(rows['V5.monitor_exit2.fault_produced'][1], 'pass')
        self.assertEqual(rows['V5.monitor_exit2.exit_status'][1], 'review')
        self.assertFalse(rows['V5.monitor_exit2.exit_status'][4], 'not a condition of the alert drill')

    def test_a_monitor_that_did_not_fail_leaves_the_case_untested(self):
        rows = by_id(V5.evaluate_monitor_fault({'ActiveState': 'active', 'Result': 'success', 'ExecMainStatus': 0}))
        self.assertEqual(rows['V5.monitor_exit2.fault_produced'][1], 'invalid')
        self.assertIn('NOT tested', rows['V5.monitor_exit2.fault_produced'][2])


class Plan(unittest.TestCase):
    def test_every_planned_row_exists_in_the_code_and_the_stage_is_described(self):
        src = (ROOT / 'tools' / 'vtest' / 'stage_v5.py').read_text()
        self.assertIn('alert', V5.__doc__.lower())
        self.assertEqual(V5.STAGE, 'V5')
        for sid, text in V5.PLAN:
            self.assertTrue(text.strip())
            self.assertIn(sid.split('.', 1)[1], src.split('DEFAULTS = ', 1)[1], '%s is planned but its name never appears in the code' % sid)
        self.assertEqual(set(V5.DEFAULTS), {'qualifying', 'smoke'})

    def test_the_qualifying_waits_are_not_shorter_than_the_design_needs(self):
        q = V5.DEFAULTS['qualifying']
        self.assertGreaterEqual(q['detect_max_s'], 240, 'two one-minute polls plus margin')
        self.assertGreaterEqual(q['hold_s'], 90)
        self.assertGreaterEqual(q['retry_wait_s'], 90, 'the first back-off is 60 s')


class ScriptedAlertMachine:
    """A machine that follows the design: a stopped monitor is seen by the poll after ~2 minutes, the alert tool records it and hands it to the sink; nothing recovers by itself."""
    def __init__(self, alert_detects=True, alert_recovers=False, delivery_ok=True, detect_after=130.0, daemon_trips=False, rogue_latch=None):
        self.t = 1000.0
        self.daemon_trips, self.rogue_latch = daemon_trips, rogue_latch
        self.alert_detects, self.alert_recovers, self.delivery_ok, self.detect_after = alert_detects, alert_recovers, delivery_ok, detect_after
        self.calls, self.files, self.monitor_stopped_at = [], {}, None
        self.monitor_up = True
        self.hold_started = None
        self.log = []

    def now(self):
        return self.t

    def _latch(self, rec):
        import hashlib
        text = json.dumps(rec, sort_keys=True) + '\n'
        self.files[C.GUARD + '/TRIPPED'] = text
        self.files[C.GUARD + '/trips.jsonl'] = self.files.get(C.GUARD + '/trips.jsonl', '') + text
        self.files[C.ROOT_DIR + '/store/PAUSE'] = ''
        self.latch_sha = hashlib.sha256(text.encode()).hexdigest()

    def sleep(self, s):
        self.t += s
        hb = (self.monitor_stopped_at or 0) - 3.0                       # the stubbed heartbeat was 3 s old when the monitor was stopped
        if self.daemon_trips and not self.monitor_up and self.monitor_stopped_at and C.GUARD + '/TRIPPED' not in self.files and self.t >= hb + 121.0:
            self._latch({'kind': 'monitor_silent', 'detail': 'no heartbeat from the monitor for 121 s', 'source': 'daemon', 'at': hb + 121.0})
        if self.rogue_latch and not self.monitor_up and self.incident_seen_at() is not None and self.t >= self.incident_seen_at() + 10 and C.GUARD + '/TRIPPED' not in self.files:
            self._latch(dict(self.rogue_latch, at=self.rogue_latch.get('at', self.t)))
        if self.alert_recovers and not self.monitor_up and self.monitor_stopped_at and self.t - self.monitor_stopped_at > self.detect_after + 20:
            self.monitor_up = True
            self.log.append(('alert-restarted-monitor', self.t))

    def incident_seen_at(self):
        ts = [float(json.loads(v)['first_seen']) for k, v in self.files.items() if '/incidents/' in k]
        return min(ts) if ts else None

    def monotonic(self):
        return self.t

    def boot_id(self):
        return 'boot'

    def sc(self, verb, *a, timeout=60):
        self.calls.append((self.t, verb, a))
        if verb == 'stop' and C.UNIT['monitor'] in a:
            self.monitor_up = False
            self.monitor_stopped_at = self.t
        if verb == 'start' and C.UNIT['monitor'] in a:
            self.monitor_up = True
        return 0, '', ''

    def read(self, p, default=None):
        return self.files.get(p, default)

    def write(self, p, text, mode=0o644):
        self.files[p] = text

    def exists(self, p):
        return p in self.files

    def listdir(self, p):
        pre = p.rstrip('/') + '/'
        return sorted({k[len(pre):].split('/')[0] for k in self.files if k.startswith(pre)})

    def run(self, argv, timeout=60, input=None):
        self.calls.append((self.t, 'run', tuple(argv)))
        if argv[:2] == ['/bin/sh', '-c'] and ' ack ' in argv[2]:
            iid = argv[2].split(' ack ')[1].split()[0]
            for k, v in list(self.files.items()):
                if '/incidents/' in k and json.loads(v)['id'] == iid:
                    d = json.loads(v)
                    d['notification_received'] = {'at': self.t, 'by': 'vtest-harness', 'via': 'harness'}
                    self.files[k] = json.dumps(d)
        return 0, '', ''

    def operator(self, argv, timeout=60, input=None):
        return self.run(argv, timeout=timeout, input=input)

    # what the alert tool would have written
    def alert_tick(self):
        if self.alert_detects and self.monitor_stopped_at and self.t - self.monitor_stopped_at >= self.detect_after and not any(k.startswith(C.ALERT_STATE + '/incidents/') for k in self.files):
            i = incident(first=self.t, state='attempted_ok' if self.delivery_ok else 'attempted_failed')
            self.files[C.ALERT_STATE + '/incidents/%s.json' % i['id']] = json.dumps(i)
            if self.delivery_ok:
                self.files[C.ALERT_SINK] = sink_for(i)
        if self.monitor_up:
            for k, v in list(self.files.items()):
                if '/incidents/' in k and json.loads(v)['cleared'] is None:
                    d = json.loads(v)
                    d['cleared'] = {'at': self.t, 'notified': True}
                    self.files[k] = json.dumps(d)


class ScenarioGlue(unittest.TestCase):
    def ctx(self, m):
        import scenarios
        import tempfile

        class Rec:
            def __init__(self):
                self.rows = []

            def check(self, cid, st, detail, data=None, required=True):
                self.rows.append((cid, st, detail, data, required))

            def add_all(self, rows):
                for r in rows:
                    self.check(*r)

            def stop_if_failed(self):
                bad = [r for r in self.rows if r[1] in ('fail', 'invalid') and r[4]]
                if bad:
                    raise C.StageStop('%s: %s' % (bad[0][0], bad[0][2]))
        rec = Rec()
        ctx = scenarios.Ctx(m, rec, dict(V5.DEFAULTS['smoke'], mode='smoke'), '/opt/x', tempfile.mkdtemp())
        orig_sleep = m.sleep

        def sleep(s):
            orig_sleep(s)
            m.alert_tick()
        m.sleep = sleep
        ctx.show = lambda k, props=(): ({'ActiveState': 'active', 'SubState': 'running', 'Result': 'success', 'InvocationID': 'i1', 'NRestarts': 0, 'MainPID': 5} if (k != 'monitor' or m.monitor_up)
                                         else {'ActiveState': 'inactive', 'SubState': 'dead', 'Result': 'success', 'InvocationID': '', 'NRestarts': 0, 'MainPID': 0})
        ctx.active = lambda k: ctx.show(k).get('ActiveState') == 'active'
        ctx.latch = lambda: (None, None)
        ctx.trips = lambda: []
        ctx.paused = lambda: False
        ctx.heartbeat_age = lambda: 3.0 if m.monitor_up else m.t - (m.monitor_stopped_at - 3.0)         # the monitor's last heartbeat was 3 s before it was stopped
        ctx.ensure_baseline = lambda reason='': None
        ctx.release_and_resume = lambda why: (0, 0, '')
        ctx.journal = lambda k, since: ''
        ctx.position = lambda: m.t
        return ctx, rec

    def test_the_stopped_monitor_scenario_waits_for_the_alert_and_does_not_act_before_it_has_held(self):
        m = ScriptedAlertMachine()
        ctx, rec = self.ctx(m)
        V5.scenario_monitor_stopped(ctx, rec, fail_delivery=False)
        by = {r[0]: r for r in rec.rows}
        self.assertEqual(by['V5.monitor_stopped.recorded'][1], 'pass', rec.rows)
        self.assertEqual(by['V5.monitor_stopped.alert_did_not_recover'][1], 'pass')
        stop_t = m.monitor_stopped_at
        starts = [c for c in m.calls if c[1] == 'start' and C.UNIT['monitor'] in c[2]]
        self.assertTrue(starts, 'the harness restores the monitor (the documented recovery) at the end')
        incident_t = min(float(json.loads(v)['first_seen']) for k, v in m.files.items() if '/incidents/' in k)
        hold = V5.DEFAULTS['smoke']['hold_s']
        self.assertGreaterEqual(starts[0][0], incident_t + hold - 1, 'the harness must not recover before the alert had its chance to (wrongly) do so and the hold passed')
        self.assertGreater(starts[0][0], stop_t)

    def wire_guard(self, ctx, m):
        """Latch, trips and PAUSE as the scripted machine's files hold them (the stub context otherwise reports none)."""
        import hashlib

        def latch():
            text = m.files.get(C.GUARD + '/TRIPPED')
            return (None, None) if text is None else (json.loads(text), hashlib.sha256(text.encode()).hexdigest())
        ctx.latch = latch
        ctx.trips = lambda: [json.loads(l) for l in m.files.get(C.GUARD + '/trips.jsonl', '').splitlines() if l.strip()]
        ctx.paused = lambda: (C.ROOT_DIR + '/store/PAUSE') in m.files

    def test_the_observed_sequence_the_daemon_trips_itself_inside_the_hold_while_the_monitor_is_stopped_passes_with_its_proof(self):
        """V5 on aef65af (real-host smoke): 'the latch changed: None -> <hash>', PAUSE false -> true. A monitor stopped for longer than GUARD_STALE_S makes the daemon latch and pause by itself;
        the alert was recorded ~70 s in and the 60 s hold spans that moment."""
        for fail_delivery in (False, True):
            m = ScriptedAlertMachine(detect_after=70.0, daemon_trips=True, delivery_ok=not fail_delivery)
            ctx, rec = self.ctx(m)
            self.wire_guard(ctx, m)
            scn = 'delivery_failure' if fail_delivery else 'monitor_stopped'
            try:
                V5.scenario_monitor_stopped(ctx, rec, fail_delivery=fail_delivery)
            except C.StageStop:
                pass
            by = {r[0]: r for r in rec.rows}
            self.assertIn(C.GUARD + '/TRIPPED', m.files, 'the scripted daemon must have tripped inside the run')
            self.assertEqual(by['V5.%s.alert_did_not_recover' % scn][1], 'pass', by['V5.%s.alert_did_not_recover' % scn][2])
            self.assertEqual(by['V5.%s.independent_safety_trip' % scn][1], 'info')

    def test_a_latch_written_during_the_hold_by_anything_but_the_daemons_own_rule_still_fails(self):
        for label, rogue in (('the alert path', {'kind': 'monitor_silent', 'detail': 'no heartbeat from the monitor for 121 s', 'source': 'alert'}),
                             ('an unexplained oom latch', {'kind': 'oom_kill', 'detail': 'x', 'source': 'systemd'}),
                             ('a daemon-looking latch before the heartbeat could be stale', {'kind': 'monitor_silent', 'detail': 'no heartbeat from the monitor for 121 s', 'source': 'daemon', 'at': 1090.0})):
            m = ScriptedAlertMachine(detect_after=70.0, rogue_latch=rogue)
            ctx, rec = self.ctx(m)
            self.wire_guard(ctx, m)
            with self.assertRaises(C.StageStop, msg=label):
                V5.scenario_monitor_stopped(ctx, rec, fail_delivery=False)
            by = {r[0]: r for r in rec.rows}
            self.assertEqual(by['V5.monitor_stopped.alert_did_not_recover'][1], 'fail', label)
            self.assertNotIn('V5.monitor_stopped.independent_safety_trip', by, label)

    def test_a_trip_inside_the_hold_does_not_excuse_an_alert_path_that_also_restarts_the_unit(self):
        m = ScriptedAlertMachine(detect_after=70.0, daemon_trips=True, alert_recovers=True)
        ctx, rec = self.ctx(m)
        self.wire_guard(ctx, m)
        with self.assertRaises(C.StageStop):
            V5.scenario_monitor_stopped(ctx, rec, fail_delivery=False)
        self.assertEqual({r[0]: r for r in rec.rows}['V5.monitor_stopped.alert_did_not_recover'][1], 'fail')

    def test_only_the_scenario_in_which_the_harness_stopped_the_monitor_offers_the_exemption(self):
        seen = []
        orig = V5.evaluate_never_recovers
        V5.evaluate_never_recovers = lambda scn, b, a, h, monitor_down=None: (seen.append((scn, monitor_down)), orig(scn, b, a, h, monitor_down))[1]
        try:
            m = ScriptedAlertMachine(detect_after=70.0)
            ctx, rec = self.ctx(m)
            V5.scenario_monitor_stopped(ctx, rec, fail_delivery=False)
        finally:
            V5.evaluate_never_recovers = orig
        self.assertEqual(len(seen), 1)
        down = seen[0][1]
        self.assertEqual(down['stale_s'], C.GUARD_STALE_S)
        self.assertAlmostEqual(down['last_heartbeat'], down['since'] - 3.0)
        self.assertIsNotNone(down['last_heartbeat'])
        import inspect
        for fn in (V5.scenario_daemon_start_limit, V5.scenario_monitor_exit2):
            self.assertNotIn('monitor_down', inspect.getsource(fn), fn.__name__ + ': a failed daemon or an immutable guard folder is not explained by the stale-heartbeat rule')

    def test_the_last_heartbeat_is_read_after_the_stop_returns_because_the_monitor_keeps_refreshing_it_until_it_exits(self):
        """On the real host the stop took 9 s; a heartbeat read before it would put the earliest legitimate trip too early."""
        m = ScriptedAlertMachine(detect_after=70.0)
        ctx, rec = self.ctx(m)
        orig_sc = m.sc

        def sc(verb, *a, timeout=60):
            r = orig_sc(verb, *a, timeout=timeout)
            if verb == 'stop' and C.UNIT['monitor'] in a:
                m.t += 9.0                                                   # the stop takes 9 s; the last heartbeat is written 5 s into it
                m.final_hb = m.monitor_stopped_at + 5.0
            return r
        m.sc = sc
        ctx.heartbeat_age = lambda: 3.0 if m.monitor_up else m.t - m.final_hb
        seen = []
        orig = V5.evaluate_never_recovers
        V5.evaluate_never_recovers = lambda scn, b, a, h, monitor_down=None: (seen.append(monitor_down), orig(scn, b, a, h, monitor_down))[1]
        try:
            V5.scenario_monitor_stopped(ctx, rec, fail_delivery=False)
        finally:
            V5.evaluate_never_recovers = orig
        self.assertAlmostEqual(seen[0]['last_heartbeat'], m.final_hb)

    def test_no_alert_within_the_time_limit_fails_and_the_monitor_is_still_restored(self):
        m = ScriptedAlertMachine(alert_detects=False)
        ctx, rec = self.ctx(m)
        with self.assertRaises(C.StageStop):
            V5.scenario_monitor_stopped(ctx, rec, fail_delivery=False)
        self.assertEqual({r[0]: r for r in rec.rows}['V5.monitor_stopped.recorded'][1], 'fail')
        self.assertTrue(any(c[1] == 'start' and C.UNIT['monitor'] in c[2] for c in m.calls), 'a failed check must not leave the machine broken')

    def test_an_alert_path_that_restarts_the_unit_is_caught(self):
        m = ScriptedAlertMachine(alert_recovers=True)
        ctx, rec = self.ctx(m)
        with self.assertRaises(C.StageStop):
            V5.scenario_monitor_stopped(ctx, rec, fail_delivery=False)
        self.assertEqual({r[0]: r for r in rec.rows}['V5.monitor_stopped.alert_did_not_recover'][1], 'fail')

    def test_the_harness_never_starts_resets_or_restarts_a_unit_other_than_in_its_own_recovery_step(self):
        m = ScriptedAlertMachine()
        ctx, rec = self.ctx(m)
        V5.scenario_monitor_stopped(ctx, rec, fail_delivery=False)
        verbs = [(c[1], c[2][0]) for c in m.calls if c[1] in ('start', 'restart', 'reset-failed', 'stop')]
        self.assertEqual(verbs[0], ('stop', C.UNIT['monitor']), 'the only fault is stopping the monitor')
        self.assertTrue(all(u == C.UNIT['monitor'] for v, u in verbs if v == 'stop'))

    def test_a_delivery_failure_run_switches_the_channel_off_first_and_on_again_even_when_a_check_fails(self):
        m = ScriptedAlertMachine(delivery_ok=False, alert_detects=False)
        ctx, rec = self.ctx(m)
        with self.assertRaises(C.StageStop):
            V5.scenario_monitor_stopped(ctx, rec, fail_delivery=True)
        self.assertEqual(m.files.get(C.ALERT_MODE), 'ok\n', 'the channel is always restored')


class InstalledRecord(unittest.TestCase):
    def test_properties_recorded_cover_the_settings_the_owner_named(self):
        for p in ('OnFailure', 'DropInPaths', 'FragmentPath', 'Restart', 'StartLimitBurst', 'StartLimitIntervalUSec', 'StartLimitAction', 'RestartPreventExitStatus', 'MemoryMax', 'OOMPolicy', 'TimeoutStopUSec', 'User'):
            self.assertIn(p, IA.EFFECTIVE_PROPS)

    def test_constants_agree_with_the_release_configuration(self):
        p = RC.default_params('vtest')
        self.assertEqual(('/var/lib/' + p['state_name'], p['bin_dir'], p['env_file']), (C.ALERT_STATE, C.ALERT_BIN, C.ETC + '/alert.env'))
        self.assertEqual(p['daemon_unit'], C.UNIT['daemon'])


if __name__ == '__main__':
    unittest.main()
