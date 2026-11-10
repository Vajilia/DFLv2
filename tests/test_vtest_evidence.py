"""The evidence manifest and archive (tools/vtest/evidence.py, `vtest.py archive`, the teardown gate): what must be left behind, what is reported MISSING, and that an archive is read back before anyone cleans up."""
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tools' / 'vtest'), str(ROOT / 'tools'), str(ROOT / 'src')]
import common as C      # noqa: E402
import evidence as EV   # noqa: E402
import vtest as VT      # noqa: E402

RID = 'run1'


class HostEnv(C.Env):
    """The real filesystem rules, but no real systemd: every command answers from a table. `bad` lists commands that fail."""
    def __init__(self, bad=()):
        self.bad = set(bad)

    def run(self, argv, timeout=60, input=None):
        if argv[0] in self.bad or (argv[0] == 'journalctl' and '-k' in argv and 'journalctl-k' in self.bad):
            return 1, '', 'Failed to read journal'
        if argv[0] == 'journalctl':
            return 0, 'Nov 09 line from %s\n' % (argv[2] if argv[1] == '-u' else 'kernel') + ('Nov 09 kernel: Out of memory: Killed process 42 (loader)\nNov 09 kernel: eth0 up\n' if '-k' in argv else ''), ''
        return 0, '%s ok\n' % argv[0], ''

    def sc(self, verb, *args, timeout=60):
        return 0, 'ActiveState=active\n', ''


def populate(rdir, skip=(), stopped=False):
    """A run folder as the harness leaves it for a V1 that passed (or, with stopped, one that failed before the cache phase)."""
    files = ['run.json', 'SUMMARY.json', 'command_output.txt']
    stage = ['result.json', 'MANIFEST.sha256', 'timeline.jsonl'] + [n for n, _ in EV.STAGE_SAMPLES['V1']]
    if stopped:
        stage = [n for n in stage if n not in [x for x, w in EV.STAGE_SAMPLES['V1'] if w == 'later']]
    for rel in files + ['V1/' + n for n in stage]:
        if rel in skip:
            continue
        p = os.path.join(rdir, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        body = {'run.json': '{"started": 1000.0}', 'result.json': json.dumps({'overall': 'fail' if stopped else 'pass'}), 'SUMMARY.json': '{"results": {"V1": "pass"}}'}.get(rel.split('/')[-1], 'data of %s\n' % rel)
        open(p, 'w').write(body)


class Evidence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = os.path.join(self.tmp.name, 'dfl-vtest')
        self.rdir = os.path.join(self.base, 'evidence', RID)
        os.makedirs(self.rdir)
        os.makedirs(os.path.join(self.base, 'state'))
        open(os.path.join(self.base, 'state', 'install.json'), 'w').write('{"installed": true}')
        patches = [mock.patch.object(C, 'BASE', self.base), mock.patch.object(C, 'EVID', os.path.join(self.base, 'evidence')), mock.patch.object(C, 'STATE', os.path.join(self.base, 'state')),
                   mock.patch.object(C, 'EXPORT', os.path.join(self.base, 'export')), mock.patch.object(EV, 'EVID', os.path.join(self.base, 'evidence')), mock.patch.object(EV, 'STATE', os.path.join(self.base, 'state'))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def archive(self, env=None, **kw):
        out = io.StringIO()
        with mock.patch.object(C, 'Env', lambda: env or HostEnv()), redirect_stdout(out):
            rc = VT.cmd_archive(SimpleNamespace(run_id=RID, out=kw.get('out')))
        return rc, out.getvalue()

    def manifest(self):
        return json.load(open(os.path.join(self.rdir, 'EVIDENCE_MANIFEST.json')))

    # -- the manifest
    def test_a_complete_run_is_archived_verified_and_every_required_item_is_listed(self):
        populate(self.rdir)
        rc, text = self.archive()
        self.assertEqual(rc, 0, text)
        m = self.manifest()
        self.assertTrue(m['complete'])
        paths = {i['path']: i['status'] for i in m['required']}
        for want in ('command_output.txt', 'host/journal-daemon.txt', 'host/journal-monitor.txt', 'host/kernel.txt', 'host/kernel-oom.txt', 'V1/daemon_cgroup.jsonl', 'V1/broadcast_probe.jsonl', 'V1/clock_log.jsonl', 'V1/result.json'):
            self.assertEqual(paths.get(want), 'present', want)
        self.assertIn('Killed process 42', open(os.path.join(self.rdir, 'host', 'kernel-oom.txt')).read())
        self.assertNotIn('eth0', open(os.path.join(self.rdir, 'host', 'kernel-oom.txt')).read(), 'only the OOM-related lines are extracted')
        rec = json.load(open(EV.archive_record_path(RID)))
        self.assertTrue(rec['verified'] and rec['complete'])
        self.assertTrue(os.path.isfile(os.path.join(self.base, 'export', 'evidence-%s.tar.gz' % RID)))

    def test_a_missing_required_file_is_named_and_stops_cleanup(self):
        populate(self.rdir, skip=('V1/broadcast_probe.jsonl',))
        rc, text = self.archive()
        self.assertEqual(rc, 3)
        self.assertIn('DO NOT CLEAN UP THE HOST', text)
        self.assertIn('MISSING', text)
        self.assertEqual(self.manifest()['missing'], ['V1/broadcast_probe.jsonl'])
        self.assertTrue(json.load(open(EV.archive_record_path(RID)))['verified'], 'the archive itself is intact; it is the evidence that is incomplete')

    def test_a_stage_that_stopped_early_lists_the_later_files_as_not_produced_and_is_not_missing(self):
        populate(self.rdir, stopped=True)
        rc, text = self.archive()
        m = self.manifest()
        self.assertEqual(rc, 0, text)
        self.assertIn('V1/cache_loader.jsonl', m['not_produced'])
        self.assertNotIn('V1/cache_loader.jsonl', m['missing'])
        self.assertTrue(m['complete'])
        self.assertIn('NOT_PRODUCED', open(os.path.join(self.rdir, 'EVIDENCE_MANIFEST.txt')).read())

    def test_a_file_that_should_exist_even_when_the_stage_stopped_is_missing_not_not_produced(self):
        populate(self.rdir, stopped=True, skip=('V1/daemon_cgroup.jsonl',))
        m = EV.build_manifest(self.rdir, RID)
        self.assertIn('V1/daemon_cgroup.jsonl', m['missing'])

    def test_a_host_record_the_machine_could_not_read_is_reported_missing_with_its_reason(self):
        populate(self.rdir)
        rc, text = self.archive(env=HostEnv(bad=('journalctl', 'dmesg')))
        self.assertEqual(rc, 3)
        m = self.manifest()
        gone = {i['path']: i for i in m['required'] if i['status'] == 'MISSING'}
        self.assertIn('host/journal-daemon.txt', gone)
        self.assertIn('could not be read on the machine', gone['host/journal-daemon.txt']['reason'])
        self.assertIn('host/kernel-oom.txt', gone, 'no kernel log means "no OOM lines" is not evidence of anything')

    def test_an_empty_oom_file_is_evidence_that_there_were_none(self):
        populate(self.rdir)
        env = HostEnv()
        env.run = lambda argv, timeout=60, input=None: (0, 'Nov 09 kernel: eth0 up\n' if argv[0] == 'journalctl' and '-k' in argv else ('line\n' if argv[0] == 'journalctl' else 'ok\n'), '')
        rc, _ = self.archive(env=env)
        self.assertEqual(rc, 0)
        self.assertEqual(open(os.path.join(self.rdir, 'host', 'kernel-oom.txt')).read().strip(), '')
        self.assertEqual({i['path']: i['status'] for i in self.manifest()['required']}['host/kernel-oom.txt'], 'present')

    # -- the archive
    def test_the_archive_is_read_back_and_a_tampered_one_does_not_verify(self):
        populate(self.rdir)
        self.assertEqual(self.archive()[0], 0)
        m = self.manifest()
        path = os.path.join(self.base, 'export', 'evidence-%s.tar.gz' % RID)
        ok, problems = EV.verify_archive(path, m, RID)
        self.assertTrue(ok, problems)
        bad = os.path.join(self.tmp.name, 'bad.tar.gz')
        with tarfile.open(path) as src, tarfile.open(bad, 'w:gz') as dst:
            for mem in src.getmembers():
                data = src.extractfile(mem).read() if mem.isfile() else None
                if mem.name.endswith('V1/clock_log.jsonl'):
                    data = b'tampered'
                    mem.size = len(data)
                if mem.name.endswith('V1/daemon_cgroup.jsonl'):
                    continue
                dst.addfile(mem, io.BytesIO(data) if data is not None else None)
        ok, problems = EV.verify_archive(bad, m, RID)
        self.assertFalse(ok)
        self.assertTrue(any('clock_log' in p and 'differs' in p for p in problems))
        self.assertTrue(any('daemon_cgroup' in p and 'not in the archive' in p for p in problems))
        junk = os.path.join(self.tmp.name, 'junk.tar.gz')
        open(junk, 'wb').write(b'not an archive')
        self.assertFalse(EV.verify_archive(junk, m, RID)[0])

    def test_the_archive_holds_the_install_record_and_both_manifests(self):
        populate(self.rdir)
        self.archive()
        with tarfile.open(os.path.join(self.base, 'export', 'evidence-%s.tar.gz' % RID)) as tf:
            names = tf.getnames()
        for n in ('state/install.json', 'evidence/%s/EVIDENCE_MANIFEST.json' % RID, 'evidence/%s/EVIDENCE_MANIFEST.txt' % RID, 'evidence/%s/V1/result.json' % RID):
            self.assertIn(n, names)

    def test_the_archive_command_refuses_a_destination_outside_the_harness_territory_and_an_unknown_run(self):
        populate(self.rdir)
        rc, text = self.archive(out='/etc/evidence.tar.gz')
        self.assertEqual(rc, 1)
        out = io.StringIO()
        with mock.patch.object(C, 'Env', lambda: HostEnv()), redirect_stdout(out):
            self.assertEqual(VT.cmd_archive(SimpleNamespace(run_id='nope', out=None)), 1)

    # -- the teardown gate
    def test_evidence_cannot_be_removed_before_a_verified_archive_exists_or_after_it_has_changed(self):
        populate(self.rdir)
        ok, problems = EV.archive_gate([RID], echo=lambda *_: None)
        self.assertFalse(ok)
        self.assertIn('no verified archive', problems[0])
        self.archive()
        self.assertEqual(EV.archive_gate([RID], echo=lambda *_: None), (True, []))
        open(os.path.join(self.rdir, 'V1', 'late.txt'), 'w').write('added after the archive')
        ok, problems = EV.archive_gate([RID], echo=lambda *_: None)
        self.assertFalse(ok)
        self.assertIn('changed after the archive', problems[0])

    def test_an_unverified_archive_record_blocks_removal(self):
        populate(self.rdir)
        self.archive()
        p = EV.archive_record_path(RID)
        rec = json.load(open(p))
        rec['verified'] = False
        json.dump(rec, open(p, 'w'))
        self.assertFalse(EV.archive_gate([RID], echo=lambda *_: None)[0])

    def test_teardown_with_include_evidence_and_yes_is_refused_without_a_verified_archive(self):
        populate(self.rdir)
        called = []
        out = io.StringIO()
        a = SimpleNamespace(include_evidence=True, yes=True)
        with mock.patch.object(C, 'Env', lambda: HostEnv()), mock.patch.object(VT.RN, 'teardown', lambda *x, **k: called.append(k) or 0), redirect_stdout(out):
            rc = VT.cmd_teardown(a)
            self.assertEqual((rc, called), (4, []), out.getvalue())
            self.archive()
            self.assertEqual(VT.cmd_teardown(a), 0)
        self.assertEqual(len(called), 1)

    def test_the_manifest_text_lists_the_hash_of_every_present_item(self):
        populate(self.rdir)
        m = EV.build_manifest(self.rdir, RID)
        text = EV.render_manifest(m)
        for i in m['required']:
            self.assertIn(i['path'], text)
            if i['status'] == 'present':
                self.assertIn(i['sha256'], text)


class Plan(unittest.TestCase):
    def test_plan_prints_an_estimate_for_every_stage_in_both_modes(self):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = VT.cmd_plan(SimpleNamespace(stages=[]))
        self.assertEqual(rc, 0)
        self.assertIn('rough wall-clock', out.getvalue())
        for st in C.STAGES:
            mod = VT.stage_module(st)
            for mode in ('qualifying', 'smoke'):
                self.assertGreater(VT.estimate(mod, mode), 0, (st, mode))

    def test_the_v1_estimate_includes_the_publication_window(self):
        v1 = VT.stage_module('V1')
        d = v1.DEFAULTS['smoke']
        self.assertEqual(VT.estimate(v1, 'smoke'), d['baseline_s'] + 900 + d['duration_s'] + 300)
        self.assertGreater(VT.estimate(v1, 'smoke'), 880, 'the smoke run must be long enough to reach the end of the first window (position about 878 s)')


if __name__ == '__main__':
    unittest.main()
