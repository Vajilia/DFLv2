"""V4's recovery after the guard-folder fault must follow the documented order: start the monitor, see a fresh heartbeat, only then release and resume.

q3b (2026-11-12, harness `e224d3a`): during the immutable-guard fault the monitor saw the daemon's OOM, could not write its latch and exited 2 by design (`rehearsal_check.py`, `watch`: a monitor that cannot
trip stops being a monitor, so the heartbeat goes silent). The unit has `RestartPreventExitStatus=1 2`, so it stayed dead after the folder was restored. The harness then released anyway: the release
refused (rc 4, stale heartbeat), resume refused (rc 3), and V4.immutable_guard.recoverable_the_documented_way failed. The documented refusal says "start the monitor first".

The owner chose B (2026-11-12): the harness follows the documented sequence, and the monitor's death stays visible as a FINDING. These tests fail on the code before that change.
What must hold:
  * a dead monitor is started (after reset-failed) and a fresh heartbeat is SEEN before `release` is attempted;
  * if no fresh heartbeat appears, `release` is not attempted at all, the recovery check FAILS and the stage stops;
  * the monitor's death is recorded as its own row, never as a pass and never hidden among the info rows; the criterion `recoverable_the_documented_way` itself is unchanged;
  * a monitor that survived the fault is recorded as survived, and no finding is made.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_vtest_lifecycle as TL      # noqa: E402
from test_vtest_lifecycle import ScriptedMachine, Rec, make_ctx   # noqa: E402

C, stage_v4 = TL.C, TL.stage_v4
MONITOR = C.UNIT['monitor']


def monitor_ctx(monitor_up_at_start, starts_ok=True):
    """The scripted machine of the existing glue tests, plus a monitor unit: up or dead when recovery begins, and brought up by a `start` call (if starts_ok)."""
    m = ScriptedMachine()
    rec = Rec()
    ctx = make_ctx(m, rec)
    mon = {'up': monitor_up_at_start}
    order = []
    real_sc = m.sc

    def sc(verb, *a, timeout=60):
        if verb == 'start' and MONITOR in a and starts_ok:
            mon['up'] = True
        if MONITOR in a:
            order.append(('sc', verb))
        return real_sc(verb, *a, timeout=timeout)
    m.sc = sc
    base_show = ctx.show

    def show(k, props=()):
        if k == 'monitor':
            return ({'ActiveState': 'active', 'SubState': 'running', 'Result': 'success', 'ExecMainStatus': 0, 'NRestarts': 0} if mon['up']
                    else {'ActiveState': 'failed', 'SubState': 'failed', 'Result': 'exit-code', 'ExecMainStatus': 2, 'NRestarts': 0})
        return base_show(k, props)
    ctx.show = show
    ctx.active = lambda k: show(k).get('ActiveState') == 'active'
    ctx.heartbeat_age = lambda: 3.0 if mon['up'] else 900.0
    ctx.journal = lambda k, since: ('{"gate": "refused", "why": "x"}\n' if (k == 'daemon' and m.fault and m.journal_has_refusal) else 'started')

    def release_and_resume(why):
        order.append(('release', ctx.heartbeat_age()))
        return (0, 0, '') if (ctx.heartbeat_age() or 999) < 30 else (4, 3, 'refusing: the monitor\'s heartbeat is stale or missing; start the monitor first')
    ctx.release_and_resume = release_and_resume
    ctx.position = lambda: m.t if any(o[0] == 'release' for o in order) else 7.0
    return m, rec, ctx, order, mon


def run_scenario(ctx, rec):
    try:
        stage_v4._immutable_guard_scenario(ctx, rec)
        return None
    except C.StageStop as e:
        return e


def rows(rec):
    return {r[0]: r for r in rec.rows}


class MonitorDiedDuringTheFault(unittest.TestCase):
    def test_the_monitor_is_started_and_a_fresh_heartbeat_seen_before_release_is_attempted(self):
        m, rec, ctx, order, mon = monitor_ctx(False)
        err = run_scenario(ctx, rec)
        self.assertIsNone(err, err)
        kinds = [o[0] + ':' + str(o[1]) for o in order]
        self.assertIn('sc:start', kinds, 'the dead monitor was never started')
        rel = [o for o in order if o[0] == 'release']
        self.assertEqual(len(rel), 1)
        self.assertLess(rel[0][1], 20, 'release was attempted with a stale heartbeat (%s s)' % rel[0][1])
        self.assertLess(kinds.index('sc:start'), kinds.index([k for k in kinds if k.startswith('release')][0]))
        self.assertEqual(rows(rec)['V4.immutable_guard.recoverable_the_documented_way'][1], 'pass')

    def test_a_failed_unit_is_reset_before_it_is_started(self):
        m, rec, ctx, order, mon = monitor_ctx(False)
        run_scenario(ctx, rec)
        verbs = [v for k, v in order if k == 'sc']
        self.assertEqual(verbs[:2], ['reset-failed', 'start'])

    def test_the_death_is_recorded_as_a_finding_not_as_a_pass_and_not_as_info(self):
        m, rec, ctx, order, mon = monitor_ctx(False)
        run_scenario(ctx, rec)
        r = rows(rec).get('V4.immutable_guard.finding.monitor_exited_during_the_fault')
        self.assertIsNotNone(r, 'no finding row: the monitor\'s death would be invisible in the evidence')
        self.assertEqual(r[1], 'review')
        self.assertIn('2', r[2])                                  # the exit status it died with
        self.assertIn('RestartPreventExitStatus', r[2])           # why it stayed dead

    def test_the_finding_does_not_make_a_failing_recovery_pass_and_does_not_weaken_the_criterion(self):
        m, rec, ctx, order, mon = monitor_ctx(False)
        run_scenario(ctx, rec)
        self.assertIn('V4.immutable_guard.recoverable_the_documented_way', rows(rec))
        # the criterion is still release rc 0, resume rc 0, clock advances: a stub that fails the release must fail the row
        m2, rec2, ctx2, order2, mon2 = monitor_ctx(False)
        ctx2.release_and_resume = lambda why: (1, 1, 'refused for another reason')
        run_scenario(ctx2, rec2)
        self.assertEqual(rows(rec2)['V4.immutable_guard.recoverable_the_documented_way'][1], 'fail')


class MonitorCannotBeBroughtBack(unittest.TestCase):
    def test_release_is_not_attempted_without_a_fresh_heartbeat_and_the_check_fails(self):
        m, rec, ctx, order, mon = monitor_ctx(False, starts_ok=False)
        ctx.start = lambda *ks: None                              # the unit "started" but never became useful: the heartbeat stays stale
        err = run_scenario(ctx, rec)
        self.assertIsNotNone(err)
        self.assertEqual([o for o in order if o[0] == 'release'], [], 'release was attempted although no fresh heartbeat was seen')
        r = rows(rec)['V4.immutable_guard.recoverable_the_documented_way']
        self.assertEqual(r[1], 'fail')
        self.assertIn('release was NOT attempted', r[2])


class MonitorSurvivedTheFault(unittest.TestCase):
    def test_no_finding_is_made_and_the_survival_is_recorded(self):
        m, rec, ctx, order, mon = monitor_ctx(True)
        err = run_scenario(ctx, rec)
        self.assertIsNone(err, err)
        ids = rows(rec)
        self.assertNotIn('V4.immutable_guard.finding.monitor_exited_during_the_fault', ids)
        self.assertIn('V4.immutable_guard.monitor_survived_the_fault', ids)
        self.assertNotIn(('sc', 'start'), order)
        self.assertEqual(ids['V4.immutable_guard.recoverable_the_documented_way'][1], 'pass')


class StaleButRunningMonitor(unittest.TestCase):
    def test_a_running_monitor_whose_heartbeat_is_stale_is_waited_for_not_released_against(self):
        m, rec, ctx, order, mon = monitor_ctx(True)
        age = {'v': 400.0}
        ctx.heartbeat_age = lambda: age['v']
        real_sleep = m.sleep

        def sleep(s):
            real_sleep(s)
            if m.t > 1100:                                         # it recovers on its own after a while
                age['v'] = 2.0
        m.sleep = sleep
        err = run_scenario(ctx, rec)
        rel = [o for o in order if o[0] == 'release']
        if rel:
            self.assertLess(rel[0][1], 20)
        self.assertIsNone(err, err)


if __name__ == '__main__':
    unittest.main()


class FindingsStayVisible(unittest.TestCase):
    """A finding row (status review, not required) must not change a stage's verdict, and must be listed in the run summary so it cannot be lost among the info rows."""
    def test_recorder_lists_findings_and_the_verdict_is_unchanged(self):
        import tempfile
        r = C.Recorder('V4', tempfile.mkdtemp(), {}, echo=lambda *_: None)
        r.check('V4.a', 'pass', 'ok')
        r.check('V4.immutable_guard.finding.monitor_exited_during_the_fault', 'review', 'FINDING: x', None, False)
        r.check('V4.b', 'info', 'note', None, False)
        self.assertEqual(r.overall(), 'pass')
        self.assertEqual([f['id'] for f in r.findings()], ['V4.immutable_guard.finding.monitor_exited_during_the_fault'])

    def test_the_run_summary_carries_the_findings(self):
        src = (Path(C.__file__).parent / 'vtest.py').read_text()
        self.assertGreaterEqual(src.count('"findings"'), 2, 'both SUMMARY.json writes must carry the findings')
