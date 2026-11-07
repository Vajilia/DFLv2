"""tools/rehearsal_check.py: the monitor finds an early file, a missing file and a wrong pointer and keeps first-failure evidence; the HTTP rule check passes a server
that follows the rules and fails one that does not. The monitor is exercised over the synthetic year of test_season in virtual time."""
import argparse
import http.server
import json
import os
import socketserver
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'tests'))
from dflsite import broadcast as B, season as S
import rehearsal_check as RC
from test_season import Rig, run, T0


def args(root, public, tmp, **kw):
    d = dict(root=root, public=public, url=None, log=os.path.join(tmp, 'watch.log'), evidence=os.path.join(tmp, 'evidence'), every=1.0, full_every=1.0, min_free_gb=0.0, max_public_gb=3.0)
    d.update(kw)
    return argparse.Namespace(**d)


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        B.FSYNC = False
        self.addCleanup(setattr, B, 'FSYNC', True)
        self.pubdir = tempfile.mkdtemp()
        self.rig = Rig(self.tmp.name)
        self.rig.public = self.pubdir
        self.bc = self.rig.daemon()
        self.bc.clock.start(T0, 0.0, B.LEASE_S, ready_until=0.0)

    def watch(self, at):
        run(self.bc, T0, T0 + at)
        w = RC.Watch(args(self.tmp.name, self.pubdir, self.tmp.name))
        return w

    def lines(self):
        return [json.loads(x) for x in open(os.path.join(self.tmp.name, 'watch.log'))]

    def check(self, w, at):
        with mock.patch.object(RC.time, 'time', lambda: T0 + at):
            pos = w.cheap(T0 + at)
            w.full(T0 + at, pos)

    def test_a_correct_tree_has_no_findings_at_several_positions(self):
        for at in (50, 150, 420, 600, 1300, 2100):
            self.setUp()
            w = self.watch(at)
            self.check(w, at)
            self.assertEqual(w.found, 0, (at, self.lines()))
            self.assertTrue(self.lines()[-1]['ok'])

    def test_an_early_file_is_found_and_its_evidence_kept(self):
        w = self.watch(150)
        # a later release placed into the public folder before its time
        later = 'releases/e00003-0000000003'
        os.makedirs(os.path.join(self.pubdir, later), exist_ok=True)
        B.put(os.path.join(self.pubdir, later, 'manifest.json'), {'release': 'e00003-0000000003'})
        self.check(w, 150)
        kinds = [l['violation'] for l in self.lines() if 'violation' in l]
        self.assertIn('early_or_unknown_file', kinds)
        ev = [f for f in os.listdir(os.path.join(self.tmp.name, 'evidence')) if f.endswith('early_or_unknown_file.json')]
        self.assertEqual(len(ev), 1)
        first = open(os.path.join(self.tmp.name, 'evidence', ev[0])).read()
        self.assertIn('releases/e00003-0000000003/manifest.json', first)
        self.check(w, 150)                                                   # a second sighting does not replace the first record
        self.assertEqual(open(os.path.join(self.tmp.name, 'evidence', ev[0])).read(), first)
        self.assertEqual(len([f for f in os.listdir(os.path.join(self.tmp.name, 'evidence'))]), 1)

    def test_a_missing_file_and_a_wrong_pointer_are_found(self):
        w = self.watch(1300)
        os.unlink(os.path.join(self.pubdir, 'broadcast', 'schedule.json'))
        B.put(os.path.join(self.pubdir, 'latest.json'), {'release': 'e00001-0000000001'})         # the position allows the week-1 release (event 2): this is an old pointer
        self.check(w, 1300)
        kinds = {l['violation'] for l in self.lines() if 'violation' in l}
        self.assertIn('missing_file', kinds)
        self.assertIn('pointer_wrong', kinds)

    def test_stale_debris_and_a_silent_daemon_are_found(self):
        w = self.watch(150)
        junk = os.path.join(self.pubdir, 'broadcast', '.tmp-clock')
        open(junk, 'w').write('x')
        os.utime(junk, (T0 - 500, T0 - 500))
        with mock.patch.object(RC.time, 'time', lambda: T0 + 400):
            w.cheap(T0 + 400)                                                # the lease (90 s) ended long ago: nobody ticked since T0+150
        kinds = {l['violation'] for l in self.lines() if 'violation' in l}
        self.assertIn('stale_tmp', kinds)
        self.assertIn('daemon_silent', kinds)

    def test_a_pause_is_not_a_silent_daemon(self):
        w = self.watch(150)
        open(os.path.join(self.tmp.name, 'store', S.PAUSE), 'w').close()
        run(self.bc, T0 + 151, T0 + 400)
        with mock.patch.object(RC.time, 'time', lambda: T0 + 400):
            w.cheap(T0 + 400)
        self.assertEqual(w.found, 0, self.lines())


class RuleServer(http.server.SimpleHTTPRequestHandler):
    """A stand-in for the production server, with the rules the Caddy block is meant to give; `broken` switches one rule off."""
    broken = set()

    def log_message(self, *a):
        pass

    def end_headers(self):
        p = self.path.split('?')[0]
        cc = 'no-cache'
        if p.endswith(('/latest.json', '/status.json', '/clock.json', '/schedule.json', '/index.json', '/manifest.json')) and '/releases/' not in p:
            cc = 'no-store'
        if 'immutable_everything' in self.broken:
            cc = 'public, max-age=31536000, immutable'
        elif any(x in p for x in ('/releases/', '/replays/', '/live/')) and not p.endswith(('manifest.json', 'index.json')) or '/releases/' in p:
            cc = 'public, max-age=31536000, immutable' if os.path.exists(self.translate_path(p)) else 'no-cache'
        self.send_header('Cache-Control', cc)
        super().end_headers()

    def list_directory(self, path):
        if 'listing' in self.broken:
            return super().list_directory(path)
        self.send_error(404)

    def do_POST(self):
        self.send_error(405)
    do_PUT = do_DELETE = do_PATCH = do_POST


def serve(directory, broken=()):
    import functools
    handler = type('H', (RuleServer,), {'broken': set(broken)})
    srv = socketserver.ThreadingTCPServer(('127.0.0.1', 0), functools.partial(handler, directory=directory))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class HttpRules(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        B.FSYNC = False
        self.addCleanup(setattr, B, 'FSYNC', True)
        self.web = os.path.join(self.tmp.name, 'web')
        self.rig = Rig(self.tmp.name)
        self.rig.public = os.path.join(self.web, 'feed')
        os.makedirs(self.web)
        open(os.path.join(self.web, 'index.html'), 'w').write('<!doctype html><script src="a.js"></script>')
        bc = self.rig.daemon()
        bc.clock.start(T0, 0.0, B.LEASE_S, ready_until=0.0)
        run(bc, T0, T0 + 700)
        self.store = os.path.join(self.tmp.name, 'store')

    def test_a_server_that_follows_the_rules_passes(self):
        srv = serve(self.web)
        try:
            with mock.patch.object(RC.time, 'time', lambda: T0 + 700):
                res = RC.http_rules('http://127.0.0.1:%d' % srv.server_address[1], store=self.store, sample=20)
        finally:
            srv.shutdown()
        failed = [w for ok, w in res if not ok]
        # the clock-skew line compares the Date header with this machine's real clock; the store position uses the patched one: only the skew line may differ
        failed = [w for w in failed if not w.startswith('Date header')]
        self.assertEqual(failed, [], failed)
        self.assertTrue(any('private files are not yet due' in w for _, w in res))

    def test_a_listing_and_an_immutable_pointer_fail(self):
        for broken, needle in (('listing', 'is not listed'), ('immutable_everything', 'Cache-Control')):
            srv = serve(self.web, [broken])
            try:
                res = RC.http_rules('http://127.0.0.1:%d' % srv.server_address[1])
            finally:
                srv.shutdown()
            self.assertTrue(any((not ok) and needle in w for ok, w in res), (broken, [w for ok, w in res if not ok]))


class Preflight(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def fails(self, root, public, **kw):
        return [w for ok, w in RC.preflight(root, public, **kw) if not ok]

    def test_the_folders_must_be_separate_and_not_production(self):
        root = os.path.join(self.tmp.name, 'private'); os.makedirs(os.path.join(root, 'store'))
        self.assertTrue(any('separate' in w for w in self.fails(root, os.path.join(root, 'public'))))
        self.assertTrue(any('production' in w for w in self.fails(root, '/srv/dflv2/feed')))
        self.assertTrue(any('production' in w for w in self.fails('/srv/dflv2/league', os.path.join(self.tmp.name, 'pub'))))

    def test_a_folder_the_daemon_did_not_make_is_refused(self):
        root = os.path.join(self.tmp.name, 'private'); os.makedirs(os.path.join(root, 'store'))
        pub = os.path.join(self.tmp.name, 'pub'); os.makedirs(pub); open(os.path.join(pub, 'somebody-elses-file'), 'w').write('x')
        self.assertTrue(any('new or was made by the daemon' in w for w in self.fails(root, pub)))
        os.unlink(os.path.join(pub, 'somebody-elses-file'))
        self.assertFalse(any('new or was made by the daemon' in w for w in self.fails(root, pub)))

    def test_an_incomplete_store_fails_and_a_past_deadline_fails(self):
        rig = Rig(self.tmp.name, built=2)                                  # two of three events built
        self.assertTrue(any('every one of the plan' in w for w in self.fails(self.tmp.name, os.path.join(self.tmp.name + '-pub'))))
        import time as _t
        self.assertTrue(any('deadline' in w for w in self.fails(self.tmp.name, self.tmp.name + '-pub', deadline=_t.time() - 5)))
        self.assertTrue(any('deadline' in w for w in self.fails(self.tmp.name, self.tmp.name + '-pub', deadline=_t.time() + 90 * 3600)))


if __name__ == '__main__':
    unittest.main()


class TemporaryFileRace(unittest.TestCase):
    """Rehearsal trip 2026-11-06: `monitor_error: FileNotFoundError` on `broadcast/live/<game>/.tmp-<pid>-<name>.json`. The daemon publishes atomically (write `.tmp-...`, then
    `os.replace` onto the final name), so a monitor that LISTS a tree and then STATS a name it listed can find the temporary file already renamed. That is normal
    operation, not a failure: the monitor must not trip on it. It must still find a published file that really disappeared, a temporary file that stays, and a genuine
    inspection failure (anything but 'the temporary file is gone')."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        B.FSYNC = False
        self.addCleanup(setattr, B, 'FSYNC', True)
        self.pubdir = tempfile.mkdtemp()
        self.rig = Rig(self.tmp.name)
        self.rig.public = self.pubdir
        self.bc = self.rig.daemon()
        self.bc.clock.start(T0, 0.0, B.LEASE_S, ready_until=0.0)
        run(self.bc, T0, T0 + 600)
        self.w = RC.Watch(args(self.tmp.name, self.pubdir, self.tmp.name))
        self.live = os.path.join(self.pubdir, 'broadcast', 'live', 'S001-tour-r1-g01')
        os.makedirs(self.live, exist_ok=True)
        self.tmpf = os.path.join(self.live, '.tmp-630207-s0084.json')
        self.final = os.path.join(self.live, 's0084.json')

    def kinds(self):
        p = os.path.join(self.tmp.name, 'watch.log')
        return [v for v in (json.loads(x).get('violation') for x in open(p)) if v] if os.path.exists(p) else []

    def publish_during(self, owner, name):
        """Wrap `owner.name` so that the daemon's atomic rename happens right AFTER the monitor has listed the tree and BEFORE it looks at what it listed."""
        real = getattr(owner, name)

        def wrapper(*a, **k):
            result = real(*a, **k)
            if os.path.exists(self.tmpf):
                os.replace(self.tmpf, self.final)
            return result
        return mock.patch.object(owner, name, wrapper)

    def racing_walk(self):
        real = os.walk

        def walk(top, *a, **k):
            for d, dirs, names in real(top, *a, **k):
                if os.path.exists(self.tmpf) and os.path.basename(self.tmpf) in names:
                    os.replace(self.tmpf, self.final)
                yield d, dirs, names
        return mock.patch.object(RC.os, 'walk', walk)

    def test_a_temporary_file_renamed_after_it_was_listed_is_not_an_error_in_the_cheap_pass(self):
        open(self.tmpf, 'w').write('{}')
        with mock.patch.object(RC.time, 'time', lambda: T0 + 600), self.racing_walk():
            self.w.cheap(T0 + 600)                                           # used to raise FileNotFoundError out of os.path.getmtime
        self.assertNotIn('stale_tmp', self.kinds())
        self.assertNotIn('monitor_error', self.kinds())
        self.assertEqual(self.w.found, 0)

    def test_a_temporary_file_renamed_after_it_was_listed_is_not_an_error_in_the_full_pass(self):
        open(self.tmpf, 'w').write('{}')
        with mock.patch.object(RC.time, 'time', lambda: T0 + 600), self.publish_during(RC.SC, 'list_public'):
            pos = self.w.cheap(T0 + 600)
            self.w.full(T0 + 600, pos)                                       # used to raise FileNotFoundError out of os.path.getsize
        self.assertNotIn('monitor_error', self.kinds())

    def test_a_temporary_file_that_is_merely_in_flight_is_not_a_finding(self):
        open(self.tmpf, 'w').write('{}')                                     # fresh and still there: not stale, not an error
        with mock.patch.object(RC.time, 'time', lambda: T0 + 600):
            self.w.cheap(T0 + 600)
        self.assertNotIn('stale_tmp', self.kinds())

    def test_a_temporary_file_that_stays_is_still_stale_debris(self):
        open(self.tmpf, 'w').write('{}')
        os.utime(self.tmpf, (T0 + 100, T0 + 100))
        with mock.patch.object(RC.time, 'time', lambda: T0 + 600):
            self.w.cheap(T0 + 600)
        self.assertIn('stale_tmp', self.kinds())

    def test_a_published_file_that_disappears_is_still_reported_not_swallowed(self):
        victim = os.path.join(self.pubdir, 'latest.json')
        self.assertTrue(os.path.exists(victim))
        real = RC.SC.list_public

        def vanish_after_listing(public):
            out = real(public)
            os.unlink(victim)                                                # a PUBLISHED file, not a temporary one
            return out
        with mock.patch.object(RC.time, 'time', lambda: T0 + 600), mock.patch.object(RC.SC, 'list_public', vanish_after_listing):
            pos = self.w.cheap(T0 + 600)
            try:
                self.w.full(T0 + 600, pos)
            except FileNotFoundError:
                return                                                       # raised out of full: the run loop turns it into monitor_error and trips, as before
        self.assertTrue(set(self.kinds()) & {'missing_file', 'monitor_error'}, self.kinds())

    def test_a_genuine_inspection_failure_on_a_temporary_file_is_still_raised(self):
        open(self.tmpf, 'w').write('{}')
        with mock.patch.object(RC.time, 'time', lambda: T0 + 600), mock.patch.object(RC.os.path, 'getmtime', side_effect=PermissionError(13, 'denied')):
            with self.assertRaises(PermissionError):
                self.w.cheap(T0 + 600)

    def test_the_run_loop_still_trips_on_an_unexpected_error(self):
        """The `except Exception` of the run loop is untouched: any error that is not 'the temporary file is gone' still becomes monitor_error."""
        src = open(os.path.join(os.path.dirname(RC.__file__), 'rehearsal_check.py')).read()
        self.assertIn('self.violation("monitor_error"', src)

    # --- the same cause one step later: a temporary file that is merely PRESENT in the listing of the full pass --------------------------------------------------------
    def tmp_of(self, rel):
        d, n = os.path.split(os.path.join(self.pubdir, rel))
        return os.path.join(d, '.tmp-630207-' + n)

    def full_pass(self, at=600):
        with mock.patch.object(RC.time, 'time', lambda: T0 + at):
            pos = self.w.cheap(T0 + at)
            self.w.full(T0 + at, pos)

    def test_an_in_flight_temporary_file_of_an_allowed_name_is_not_an_early_file(self):
        t = self.tmp_of('latest.json')
        open(t, 'w').write('{}')
        os.utime(t, (T0 + 590, T0 + 590))
        self.full_pass()
        self.assertEqual(self.kinds(), [], 'the daemon was in the middle of publishing latest.json')

    def test_a_temporary_file_for_a_name_the_position_does_not_allow_is_still_an_early_file(self):
        future = sorted(self.w.oracle.expected(10 ** 9)[0] - self.w.oracle.expected(1000)[0])
        self.assertTrue(future)
        t = self.tmp_of(future[0])
        os.makedirs(os.path.dirname(t), exist_ok=True)
        open(t, 'w').write('{}')
        os.utime(t, (T0 + 590, T0 + 590))
        self.full_pass()
        self.assertIn('early_or_unknown_file', self.kinds())

    def test_a_stale_temporary_file_is_still_reported_by_the_full_pass(self):
        t = self.tmp_of('latest.json')
        open(t, 'w').write('{}')
        os.utime(t, (T0 + 100, T0 + 100))
        self.full_pass()
        self.assertIn('stale_tmp', self.kinds())
        self.assertIn('early_or_unknown_file', self.kinds(), 'a temporary file that does not go away is still a finding, as before')

    def test_a_non_temporary_unknown_file_is_still_an_early_file(self):
        open(os.path.join(self.pubdir, 'broadcast', 'surprise.json'), 'w').write('{}')
        self.full_pass()
        self.assertIn('early_or_unknown_file', self.kinds())

    # --- every temporary-name pattern the daemon's own code writes (src/dflsite/broadcast.py put and copy, src/dflsite/status.py write_atomic) -------------------------
    def test_the_status_writers_temporary_name_is_in_flight_not_an_early_file(self):
        """status.json is rewritten every second as `.tmp-status.json-<pid>` (the pid LAST), with an fsync before the rename."""
        d = self.pubdir
        self.assertTrue(os.path.exists(os.path.join(d, 'status.json')))
        t = os.path.join(d, '.tmp-status.json-4242')
        open(t, 'w').write('{}')
        os.utime(t, (T0 + 590, T0 + 590))
        self.full_pass()
        self.assertEqual(self.kinds(), [])

    def test_the_copy_writers_temporary_name_without_a_pid_is_in_flight(self):
        """broadcast.py copies a due file through `.tmp-<name>` in the same folder (no pid)."""
        rel = sorted(f for f in RC.SC.list_public(self.pubdir) if f.startswith('releases/') and f.endswith('manifest.json'))[0]
        d, n = os.path.split(os.path.join(self.pubdir, rel))
        t = os.path.join(d, '.tmp-' + n)
        open(t, 'w').write('{}')
        os.utime(t, (T0 + 590, T0 + 590))
        self.full_pass()
        self.assertEqual(self.kinds(), [])

    def test_a_temporary_name_whose_target_is_not_allowed_is_still_flagged_for_every_pattern(self):
        future = sorted(self.w.oracle.expected(10 ** 9)[0] - self.w.oracle.expected(1000)[0])
        d, n = os.path.split(os.path.join(self.pubdir, future[0]))
        os.makedirs(d, exist_ok=True)
        for t in (os.path.join(d, '.tmp-4242-' + n), os.path.join(d, '.tmp-' + n), os.path.join(d, '.tmp-%s-4242' % n)):
            open(t, 'w').write('{}')
            os.utime(t, (T0 + 590, T0 + 590))
        self.full_pass()
        self.assertIn('early_or_unknown_file', self.kinds())
