"""V5's alert-journal collection (the real failure of 2026-11-19: `Failed to add filter for units: No data available`).

On the real validation VM the V5-only smoke on harness aaddb43 stopped at V5.monitor_stopped.attribution_preserved because the collector ran `journalctl -u 'dfl-vtest-alert@*'`. A glob unit filter that
matches no unit name in the journal makes journalctl exit 1. The monitor had been STOPPED cleanly (not failed), so the failure hook never started an alert instance and the poll found the incident: there
was nothing for the glob to match. The old helper also turned any error into an empty string, so the detection-path row would have said "poll only" without evidence.

What the collector must do instead (owner, 2026-11-19): tell a successful query with legitimately no entries from an unreadable or failed one; never accept an empty or failed journal as complete; never
miss an alert instance, including one that finished before the capture; keep timestamps, unit identity and each query's status. The journalctl behavior modelled in journal_model.py was reproduced against
a real private journald (systemd 255.4).
"""
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools' / 'vtest'))
sys.path.insert(0, str(ROOT / 'alerts'))
import common as C                          # noqa: E402
import stage_v5 as V5                       # noqa: E402
import attribution as AT                    # noqa: E402
from journal_model import FakeJournal, NO_MATCH_ERROR, UNIT_FIELDS   # noqa: E402
import test_vtest_v5 as T                   # noqa: E402

POLL = C.PREFIX + 'alert-poll.service'
MON_INST = C.PREFIX + 'alert@' + C.UNIT['monitor'] + '.service'
DAE_INST = C.PREFIX + 'alert@' + C.UNIT['daemon'] + '.service'


def journal(poll=True):
    """A journal as on the validation VM after a clean monitor stop: the poll runs every minute; no alert instance has ever run."""
    j = FakeJournal()
    if poll:
        j.add(POLL, 'poll ran', at=1020.0)
    return j


def ctx_for(j):
    return types.SimpleNamespace(env=types.SimpleNamespace(run=j.run))


class TheModelIsWhatRealJournalctlDid(unittest.TestCase):
    """journal_model.py records what real journalctl 255.4 did in the local reproduction; these pin the rules the collector is built on."""
    def test_a_glob_alone_with_no_held_match_fails_with_the_observed_message(self):
        j = journal()
        rc, out, err = j.run(['journalctl', '-u', C.PREFIX + 'alert@*', '--since', '@0'])
        self.assertEqual((rc, out, err), (1, '', NO_MATCH_ERROR))

    def test_a_glob_beside_any_exact_unit_or_with_a_held_match_succeeds(self):
        j = journal()
        self.assertEqual(j.run(['journalctl', '-u', C.PREFIX + 'alert@*', '-u', 'never-existed.service'])[0], 0)
        j.add(MON_INST, 'Finished', at=1010.0)
        self.assertEqual(j.run(['journalctl', '-u', C.PREFIX + 'alert@*'])[0], 0)

    def test_an_exact_unit_with_no_entries_succeeds_and_says_so(self):
        rc, out, _ = journal().run(['journalctl', '-u', MON_INST, '--since', '@0'])
        self.assertEqual((rc, out), (0, '-- No entries --\n'))


class CollectAlertJournal(unittest.TestCase):
    def collect(self, j, since=1000.0):
        return V5.collect_alert_journal(ctx_for(j), since)

    def test_poll_only_detection_is_a_successful_empty_collection_not_an_error(self):
        """The real failure: the monitor was stopped cleanly, no hook instance exists, the poll found it."""
        j = journal()
        res = self.collect(j)
        self.assertEqual(res['state'], 'empty', res)
        self.assertEqual(set(res['instances']), {MON_INST, DAE_INST}, 'the two hook instances are always queried by exact name')
        for name, inst in res['instances'].items():
            self.assertEqual((inst['state'], inst['query']['rc']), ('empty', 0), name)
        for f in UNIT_FIELDS:
            self.assertEqual(res['enumeration']['fields'][f]['rc'], 0, f)
        self.assertEqual(AT.alert_journal_problems(res), [])

    def test_no_wildcard_unit_filter_is_ever_sent_to_journalctl(self):
        for j in (journal(), journal().add(MON_INST, 'Finished', at=1010.0)):
            self.collect(j)
            self.assertTrue(j.calls)
            for argv in j.calls:
                self.assertFalse([a for a in argv if any(c in a for c in '*?[')], argv)

    def test_an_instance_that_finished_before_the_capture_is_found_with_its_identity_and_timestamps(self):
        j = journal().add(MON_INST, 'Starting DFL failure alert', at=1010.0).add(MON_INST, 'Finished', at=1011.0)
        j.add(MON_INST, 'pid1 note', at=1012.0, field='UNIT')            # PID 1 logs about a unit under UNIT=
        res = self.collect(j)
        self.assertEqual(res['state'], 'present')
        inst = res['instances'][MON_INST]
        self.assertEqual(inst['state'], 'present')
        self.assertEqual(AT.timestamped_problems(inst['text']), [])                      # ISO timestamps are kept
        self.assertIn(MON_INST, res['text'])                               # and the unit identity heads its part of the text
        self.assertEqual(AT.alert_journal_problems(res), [])

    def test_an_instance_for_an_unexpected_unit_is_found_through_the_journals_own_unit_index_in_any_of_its_fields(self):
        for field in UNIT_FIELDS:
            other = C.PREFIX + 'alert@some-other.service.service'
            j = journal().add(other, 'Finished', at=1010.0, field=field)
            res = self.collect(j)
            self.assertIn(other, res['instances'], field)
            self.assertEqual(res['instances'][other]['state'], 'present', field)
            self.assertEqual(res['state'], 'present', field)

    def test_an_instance_whose_entries_are_all_older_than_the_window_is_still_listed_and_queried_but_empty(self):
        j = journal().add(MON_INST, 'Finished', at=500.0)
        res = self.collect(j, since=1000.0)
        self.assertEqual(res['state'], 'empty')
        self.assertEqual(res['instances'][MON_INST]['query']['rc'], 0)
        self.assertIn(MON_INST, res['enumeration']['listed'])
        self.assertEqual(AT.alert_journal_problems(res), [])

    def test_other_units_named_like_something_else_are_not_taken_for_alert_instances(self):
        j = journal().add('some-other-thing@x.service', 'entry', at=1010.0).add(C.PREFIX + 'alert-poll.service', 'x', at=1030.0)
        res = self.collect(j)
        self.assertEqual(set(res['instances']), {MON_INST, DAE_INST})

    def test_a_failed_enumeration_is_unreadable_never_empty(self):
        for f in UNIT_FIELDS:
            j = journal()
            j.fail_if.append((lambda a, f=f: '-F' in a and a[a.index('-F') + 1] == f, 1, 'Failed to enumerate\n'))
            res = self.collect(j)
            self.assertEqual(res['state'], 'unreadable', f)
            self.assertIn(f, res['detail'])
            self.assertTrue(AT.alert_journal_problems(res), f)

    def test_a_failed_exact_query_is_unreadable_even_when_other_instances_have_entries(self):
        j = journal().add(DAE_INST, 'Finished', at=1010.0)
        j.fail_if.append((lambda a: '-u' in a and a[a.index('-u') + 1] == MON_INST, 1, 'boom\n'))
        res = self.collect(j)
        self.assertEqual(res['state'], 'unreadable')
        self.assertIn(MON_INST, res['detail'])
        self.assertEqual(res['instances'][MON_INST]['state'], 'unreadable')
        self.assertTrue(AT.alert_journal_problems(res))

    def test_an_unreadable_journal_is_unreadable(self):
        j = journal()
        j.fail_all = (1, 'Failed to read journal')
        res = self.collect(j)
        self.assertEqual(res['state'], 'unreadable')
        self.assertTrue(AT.alert_journal_problems(res))

    def test_an_enumeration_that_does_not_even_list_the_poll_unit_is_not_a_proof_of_anything(self):
        """The poll runs every minute, so a unit index without it was not read (journalctl -F also exits 0, empty, for a field that does not exist)."""
        res = self.collect(journal(poll=False))
        self.assertEqual(res['state'], 'unreadable')
        self.assertIn('poll', res['detail'])
        self.assertTrue(AT.alert_journal_problems(res))

    def test_the_query_status_of_every_call_is_kept(self):
        res = self.collect(journal())
        for inst in res['instances'].values():
            self.assertIn('rc', inst['query'])
            self.assertIn('argv', inst['query'])
        self.assertTrue(all('rc' in v for v in res['enumeration']['fields'].values()))


class AlertJournalCompleteness(unittest.TestCase):
    """A gap in the alert journal evidence can never be a pass: the validators reject every way the claim 'nothing to see' could be hollow."""
    def good(self, instance=False):
        j = journal()
        if instance:
            j.add(MON_INST, 'Finished', at=1010.0)
        return V5.collect_alert_journal(ctx_for(j), 1000.0)

    def test_the_real_shapes_are_complete(self):
        for inst in (False, True):
            self.assertEqual(AT.alert_journal_problems(self.good(inst)), [], inst)

    def test_the_old_shape_without_query_evidence_is_rejected_whether_it_says_empty_or_present(self):
        self.assertTrue(AT.alert_journal_problems({'state': 'empty'}))
        self.assertTrue(AT.alert_journal_problems({'state': 'present', 'text': '2027-01-15T00:00:00.000000+00:00 h u[1]: x\n'}))
        self.assertTrue(AT.alert_journal_problems(None))

    def test_each_hollow_claim_is_rejected(self):
        def mut(fn):
            d = json.loads(json.dumps(self.good()))
            fn(d)
            return d
        cases = {
            'enumeration field failed': lambda d: d['enumeration']['fields']['UNIT'].update(rc=1),
            'enumeration field missing': lambda d: d['enumeration']['fields'].pop('OBJECT_SYSTEMD_UNIT'),
            'enumeration missing': lambda d: d.pop('enumeration'),
            'instance query failed': lambda d: d['instances'][MON_INST]['query'].update(rc=1),
            'instance query status missing': lambda d: d['instances'][MON_INST].pop('query'),
            'expected instance not queried': lambda d: d['instances'].pop(DAE_INST),
            'index names an instance that was not queried': lambda d: d['enumeration']['fields']['UNIT']['names'].append(C.PREFIX + 'alert@x.service.service'),
            'listed instances disagree with the index': lambda d: d['enumeration']['listed'].append(C.PREFIX + 'alert@y.service.service'),
            'poll unit not in the index': lambda d: [v.update(names=[n for n in v.get('names', []) if n != POLL]) for v in d['enumeration']['fields'].values()],
            'state empty but an instance has text': lambda d: d['instances'][MON_INST].update(state='present', text='2027-01-15T00:00:00.000000+00:00 h u[1]: x\n'),
            'state present but no instance has entries': lambda d: d.update(state='present', text='2027-01-15T00:00:00.000000+00:00 h u[1]: x\n'),
            'unknown state': lambda d: d.update(state='unknown'),
            'unreadable claimed complete': lambda d: d.update(state='unreadable'),
        }
        for label, fn in cases.items():
            self.assertTrue(AT.alert_journal_problems(mut(fn)), label)

    def test_a_present_journal_must_be_timestamped(self):
        d = json.loads(json.dumps(self.good(instance=True)))
        d['text'] = 'no stamp here\n'
        d['instances'][MON_INST]['text'] = 'no stamp here\n'
        self.assertTrue(AT.alert_journal_problems(d))

    def test_the_full_capture_uses_these_rules(self):
        cap = T.preserved_for({'at': 1100.0, 'paused': False})
        self.assertEqual(AT.attribution_problems(cap), [])
        cap['journals']['alert'] = {'state': 'empty'}
        self.assertTrue(AT.attribution_problems(cap))
        cap = T.preserved_for({'at': 1100.0, 'paused': False})
        cap['journals']['alert']['enumeration']['fields']['UNIT']['rc'] = 1
        self.assertTrue(any('unit index' in p for p in AT.attribution_problems(cap)))
        cap = T.preserved_for({'at': 1100.0, 'paused': False})
        cap['journals']['poll'].pop('query')
        self.assertTrue(any('poll journal' in p for p in AT.attribution_problems(cap)))

    def test_the_expected_instances_are_the_two_failure_hooks(self):
        self.assertEqual(set(AT.expected_alert_instances()), {MON_INST, DAE_INST})


class DetectionPath(unittest.TestCase):
    """The detection-path row says "poll only" only when the journal was read and showed no hook instance for the unit; unreadable is "undetermined"."""
    def row(self, jr):
        rows = {r[0]: r for r in V5.evaluate_detection('monitor_stopped', ('monitor_not_running',), [T.incident(first=1100.0)], None, 1000.0, max_s=300, alert_journal=jr, expect_delivery=False)}
        return rows['V5.monitor_stopped.detection_path']

    def test_poll_only_when_the_journal_was_read_and_shows_no_hook_instance(self):
        r = self.row(V5.collect_alert_journal(ctx_for(journal()), 998.0))
        self.assertIn('poll only', r[2])
        self.assertEqual(r[1], 'info')

    def test_hook_when_the_instance_for_that_unit_has_entries_in_the_window(self):
        j = journal().add(MON_INST, 'Starting DFL failure alert', at=1005.0)
        r = self.row(V5.collect_alert_journal(ctx_for(j), 998.0))
        self.assertIn('failure hook ran', r[2])
        self.assertTrue(r[3]['journal_has_hook'])

    def test_an_instance_of_another_unit_is_not_this_units_hook(self):
        j = journal().add(DAE_INST, 'Starting DFL failure alert', at=1005.0)
        self.assertIn('poll only', self.row(V5.collect_alert_journal(ctx_for(j), 998.0))[2])

    def test_an_unreadable_journal_is_undetermined_not_poll_only(self):
        j = journal()
        j.fail_all = (1, 'Failed to read journal')
        r = self.row(V5.collect_alert_journal(ctx_for(j), 998.0))
        self.assertIn('undetermined', r[2])
        self.assertNotIn('poll only', r[2])
        self.assertIsNone(r[3]['journal_has_hook'])

    def test_a_missing_journal_argument_is_undetermined_too(self):
        self.assertIn('undetermined', self.row(None)[2])


class ScenarioLevel(unittest.TestCase):
    """The scripted machine's journal behaves like journalctl does (tests/journal_model.py): the stopped monitor is found by the poll, no hook instance exists."""
    def ctx(self, m):
        return T.ScenarioGlue.ctx(None, m)

    def attribution(self, ctx, scn='monitor_stopped'):
        with open(os.path.join(ctx.dir, 'attribution_%s.json' % scn)) as f:
            return json.load(f)

    def test_a_poll_detected_stop_passes_the_attribution_row_and_the_journal_is_a_proved_empty(self):
        m = T.ScriptedAlertMachine(detect_after=70.0)
        ctx, rec = self.ctx(m)
        V5.scenario_monitor_stopped(ctx, rec, fail_delivery=False)
        by = {r[0]: r for r in rec.rows}
        self.assertEqual(by['V5.monitor_stopped.attribution_preserved'][1], 'pass', by['V5.monitor_stopped.attribution_preserved'])
        self.assertIn('poll only', by['V5.monitor_stopped.detection_path'][2])
        cap = self.attribution(ctx)
        self.assertEqual(cap['journals']['alert']['state'], 'empty')
        self.assertEqual(AT.attribution_problems(cap), [])
        self.assertEqual(cap['journals']['poll']['state'], 'present')

    def test_a_failing_collection_still_fails_the_scenario_and_is_never_reported_as_poll_only(self):
        m = T.ScriptedAlertMachine(detect_after=70.0)
        m.journal_fails = True
        ctx, rec = self.ctx(m)
        with self.assertRaises(C.StageStop):
            V5.scenario_monitor_stopped(ctx, rec, fail_delivery=False)
        by = {r[0]: r for r in rec.rows}
        self.assertEqual(by['V5.monitor_stopped.attribution_preserved'][1], 'fail')
        self.assertIn('undetermined', by['V5.monitor_stopped.detection_path'][2])

    def test_a_hook_instance_that_ran_and_finished_is_captured_with_its_identity(self):
        m = T.ScriptedAlertMachine(detect_after=70.0)
        m.journal.add(MON_INST, 'Starting DFL failure alert for %s' % C.UNIT['monitor'], at=1030.0)
        m.journal.add(MON_INST, 'Finished', at=1031.0)
        ctx, rec = self.ctx(m)
        V5.scenario_monitor_stopped(ctx, rec, fail_delivery=False)
        cap = self.attribution(ctx)
        self.assertEqual(cap['journals']['alert']['state'], 'present')
        self.assertEqual(cap['journals']['alert']['instances'][MON_INST]['state'], 'present')
        self.assertEqual(AT.attribution_problems(cap), [])

    def test_the_whole_run_files_say_unavailable_when_the_query_failed_and_never_empty(self):
        m = T.ScriptedAlertMachine()
        m.journal_fails = True
        ctx, rec = self.ctx(m)
        V5.save_evidence(ctx)
        for name in ('alert_journal.txt', AT.JOURNAL_FILE):
            text = Path(ctx.dir, name).read_text()
            self.assertTrue(text.startswith('UNAVAILABLE:'), (name, text))
        status = json.load(open(os.path.join(ctx.dir, AT.JOURNAL_STATUS_FILE)))
        self.assertTrue(AT.journal_status_problems(status))

    def test_the_whole_run_files_for_a_poll_only_run_are_a_proved_poll_journal_with_query_status(self):
        m = T.ScriptedAlertMachine()
        m.sleep(130.0)                                                                # two polls have run
        ctx, rec = self.ctx(m)
        V5.save_evidence(ctx)
        stamped = Path(ctx.dir, AT.JOURNAL_FILE).read_text()
        self.assertEqual(AT.timestamped_problems(stamped, 'the journal'), [])
        legacy = Path(ctx.dir, 'alert_journal.txt').read_text()
        self.assertNotEqual(legacy.strip(), '(empty)')
        self.assertIn('no entries', legacy.lower())                                   # a successful, empty collection says so and names what was queried
        status = json.load(open(os.path.join(ctx.dir, AT.JOURNAL_STATUS_FILE)))
        self.assertEqual(AT.journal_status_problems(status), [])
        self.assertFalse([a for argv in m.journal.calls for a in argv if any(c in a for c in '*?[')], 'no wildcard anywhere')


if __name__ == '__main__':
    unittest.main()
