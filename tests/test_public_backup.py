"""Public-folder backup and restore (tools/public_backup.py, docs/PUBLIC_BACKUP.md).

Small SYNTHETIC two-season stores in virtual time (the Rig of tests/test_multiseason.py): these tests prove behaviour and ordering, not size or speed. Real-size measurements are in
docs/PUBLIC_BACKUP.md (separate section, taken on the real two-season tree).

Cases: byte-for-byte round trip; a backup made WHILE the daemon publishes (the daemon is advanced from inside the backup, between files) and across a season hand-over; the original
public folder gone and publication continuing from the restored one; a backup killed or failing half way is never valid; damaged, missing, extra and edited files, a damaged
manifest and a damaged source are all reported; the private store's profile pairs with it.
"""
import hashlib
import http.server
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'tests'))
import public_backup as PBK
from dflsite import broadcast as B, playback as PB, season as S
from test_multiseason import Rig, run, T0


def tree_of(base, skip=()):
    out = {}
    for d, _, names in os.walk(base):
        for n in names:
            p = os.path.join(d, n)
            rel = os.path.relpath(p, base).replace(os.sep, '/')
            if rel in skip or n.startswith('.tmp-'):
                continue
            out[rel] = hashlib.sha256(open(p, 'rb').read()).hexdigest()
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        B.FSYNC = False
        self.addCleanup(setattr, B, 'FSYNC', True)
        self.r = Rig(os.path.join(self.tmp.name, 'rig'))
        os.makedirs(self.r.store, exist_ok=True)
        self.r.y1.build(4)
        self.r.y2.build(4)

    def out(self, name='backup'):
        return os.path.join(self.tmp.name, name)

    def make(self, name='backup', **kw):
        kw.setdefault('fsync', False)
        return PBK.create(self.r.public, self.out(name), 'public', **kw)

    def control_tree(self, until):
        q = Rig(os.path.join(self.tmp.name, 'control'))
        q.y1.build(4)
        q.y2.build(4)
        run(q.started(), 0, until)
        t = q.tree()
        shutil.rmtree(os.path.join(self.tmp.name, 'control'))
        return t


class RoundTrip(Base):
    def test_a_restored_folder_has_the_same_files_names_and_bytes(self):
        r = self.r
        run(r.started(), 0, r.end1 + r.y2.plan['total_s'] + 100)
        res = self.make()
        self.assertTrue(res['ok'] and res['quiescent'])
        v = PBK.verify(self.out())
        self.assertTrue(v['ok'], v['problems'])
        dest = self.out('restored')
        rr = PBK.restore(self.out(), dest, fsync=False)
        self.assertTrue(rr['ok'], rr)
        self.assertEqual(tree_of(dest), tree_of(r.public), 'every file, name and byte')
        names = {p for p in tree_of(dest) if p.startswith('playback/games/')}
        self.assertEqual(len(names), 6)
        for d, dirs, files in os.walk(dest):
            self.assertEqual(os.stat(d).st_mode & 0o777, 0o755, d)
            for f in files:
                self.assertEqual(os.stat(os.path.join(d, f)).st_mode & 0o777, 0o644)
        self.assertEqual(PBK.CLOSURE['public'](PBK.Tree(dest)), [], 'the live folder check agrees')

    def test_historical_links_and_replay_loading_from_the_restored_folder(self):
        r = self.r
        run(r.started(), 0, r.end1 + r.y2.plan['total_s'] + 100)
        self.make()
        dest = self.out('restored')
        self.assertTrue(PBK.restore(self.out(), dest, fsync=False)['ok'])
        shutil.rmtree(r.public)                                           # the original is unavailable
        handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=dest, **k)
        srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        srv.RequestHandlerClass.log_message = lambda *a, **k: None
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        base = 'http://127.0.0.1:%d/' % srv.server_address[1]

        def get(path):
            with urllib.request.urlopen(base + path) as f:
                return f.read()
        idx = json.loads(get('playback/index.json'))
        self.assertEqual(len(idx['games']), 6)
        for e in idx['games']:                              # the published replay URL: same name, bytes equal the entry's SHA-256, loadable as a public replay
            raw = get('playback/' + e['file'])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), e['sha256'])
            PB.check_public(json.loads(raw))
        lib = json.loads(get('broadcast/replays/index.json'))
        for g in lib['games']:
            self.assertEqual(get('broadcast/replays/' + g['replay']), get('playback/games/' + g['replay']), 'the two public copies of a replay are the same bytes')
            m = json.loads(get('broadcast/live/%s/manifest.json' % B.safe(g['gid'])))
            self.assertEqual(m['state'], 'final')
            get('broadcast/live/%s/s%04d.json' % (B.safe(g['gid']), m['segments'] - 1))
        latest = json.loads(get('latest.json'))
        json.loads(get('releases/%s/manifest.json' % latest['release']))
        for p in json.loads(get('broadcast/honors/index.json'))['parts']:
            json.loads(get('broadcast/honors/' + p['file']))

    def test_publication_continues_from_the_restored_folder(self):
        r = self.r
        end = r.end1 + r.y2.plan['total_s'] + 100
        mid = r.end1 + r.y2.t['r1'] + 30                              # season two has begun and its first results are public
        run(r.started(), 0, mid)
        self.make()
        shutil.rmtree(r.public)                                       # the original is gone; the private state (clock, plan, builds, carry) is still there
        dest = r.public
        self.assertTrue(PBK.restore(self.out(), dest, fsync=False)['ok'])
        before = tree_of(dest)
        run(r.daemon(), mid + 1, end)
        got = r.tree()
        want = self.control_tree(end)
        self.assertEqual(sorted(got), sorted(want))
        for k, v in want.items():
            if k not in ('broadcast/schedule.json',):
                self.assertEqual(got[k], v, k)
        for k, v in before.items():                                   # nothing restored was altered by going on, but what the daemon rewrites by design
            if not k.endswith('index.json') and k not in ('latest.json', 'status.json', 'broadcast/schedule.json', 'broadcast/clock.json') and '/live/' not in k:
                self.assertEqual(tree_of(dest)[k], v, k)


class WhilePublishing(Base):
    def advancing(self, bc, start, every, step, state):
        def hook(event, **info):
            if event == 'file' and info['n'] % every == 0:
                state['t'] += step
                bc.tick(T0 + state['t'])
        return hook

    def test_a_backup_made_while_the_daemon_publishes_is_closed_and_restorable(self):
        r = self.r
        t0 = r.end1 + r.y2.t['w0'] - 100                           # season two: the opening event, a live window, then results while we copy
        bc = r.started()
        run(bc, 0, t0)
        state = {'t': t0}
        res = self.make(hook=self.advancing(bc, t0, 40, 250, state))
        self.assertTrue(res['ok'])
        self.assertGreater(state['t'], t0, 'the daemon really did move while the backup ran')
        v = PBK.verify(self.out())
        self.assertTrue(v['ok'], v['problems'])
        dest = self.out('restored')
        self.assertTrue(PBK.restore(self.out(), dest, fsync=False)['ok'])
        self.assertEqual(PBK.CLOSURE['public'](PBK.Tree(dest)), [])

    def test_a_hand_over_during_the_backup(self):
        r = self.r
        t0 = r.end1 - 60
        bc = r.started()
        run(bc, 0, t0)
        self.assertFalse(os.path.exists(os.path.join(r.store, S.CARRY)))
        state = {'t': t0}
        seen = {'carry': False}

        def hook(event, **info):
            if event == 'file' and info['n'] in (5, 60, 200):
                state['t'] += 45
                bc.tick(T0 + state['t'])
                seen['carry'] = seen['carry'] or os.path.exists(os.path.join(r.store, S.CARRY))
        res = self.make(hook=hook)
        self.assertTrue(seen['carry'], 'the season hand-over happened while the backup was copying')
        self.assertTrue(res['ok'])
        v = PBK.verify(self.out())
        self.assertTrue(v['ok'], v['problems'])
        # restore and carry on: the end state is the uninterrupted run's
        end = r.end1 + r.y2.plan['total_s'] + 100
        shutil.rmtree(r.public)
        self.assertTrue(PBK.restore(self.out(), r.public, fsync=False)['ok'])
        run(r.daemon(), state['t'] + 1, end)
        want = self.control_tree(end)
        got = r.tree()
        self.assertEqual(sorted(got), sorted(want))
        for k, vv in want.items():
            if k != 'broadcast/schedule.json':
                self.assertEqual(got[k], vv, k)

    def test_a_folder_that_never_stops_publishing_is_refused_when_quiet_is_required(self):
        r = self.r
        t0 = r.end1 + r.y2.t['w0'] - 100
        bc = r.started()
        run(bc, 0, t0)
        state = {'t': t0}

        def hook(event, **info):
            if event == 'file' and info['n'] % 3 == 0 and info['n'] > 0:
                state['t'] += 200
                bc.tick(T0 + state['t'])
        with self.assertRaises(PBK.Failed):
            self.make(hook=hook, max_passes=3, require_quiescent=True)
        self.assertFalse(os.path.exists(self.out()), 'nothing was committed')
        self.assertTrue(os.path.isdir(self.out() + '.partial'))
        self.assertFalse(PBK.verify(self.out())['ok'])


class Interrupted(Base):
    def setUp(self):
        super().setUp()
        run(self.r.started(), 0, self.r.end1 + 3000)

    def test_a_failure_half_way_leaves_nothing_valid(self):
        def boom(event, **info):
            if event == 'file' and info['n'] == 30:
                raise RuntimeError('disk full')
        with self.assertRaises(RuntimeError):
            self.make(hook=boom)
        self.assertFalse(os.path.exists(self.out()))
        v = PBK.verify(self.out())
        self.assertFalse(v['ok'])
        self.assertIn('unfinished', ' '.join(v['problems']))
        self.assertFalse(PBK.restore(self.out(), self.out('dest'), fsync=False)['ok'])
        self.assertFalse(os.path.exists(self.out('dest')))
        with self.assertRaises(PBK.Failed):                                    # a new attempt never silently reuses or deletes the leftover
            self.make()
        self.assertTrue(self.make(discard_partial=True)['ok'])
        self.assertTrue(PBK.verify(self.out())['ok'])

    def test_a_failure_just_before_the_commit_leaves_nothing_valid(self):
        def boom(event, **info):
            if event == 'before_commit':
                raise RuntimeError('killed')
        with self.assertRaises(RuntimeError):
            self.make(hook=boom)
        self.assertFalse(PBK.verify(self.out())['ok'])

    def test_a_partial_folder_is_never_valid_even_with_a_complete_manifest(self):
        self.make()
        os.rename(self.out(), self.out('copy.partial'))
        v = PBK.verify(self.out('copy.partial'))
        self.assertFalse(v['ok'])
        self.assertIn('unfinished', ' '.join(v['problems']))

    def test_a_process_killed_with_sigkill_half_way(self):
        code = ('import sys,os,signal\nsys.path.insert(0,%r)\nimport public_backup as P\n'
                'def hook(event, **i):\n    if event=="file" and i["n"]==40: os.kill(os.getpid(), signal.SIGKILL)\n'
                'P.create(%r, %r, "public", fsync=False, hook=hook)\n') % (str(ROOT / 'tools'), self.r.public, self.out())
        p = subprocess.run([sys.executable, '-c', code])
        self.assertEqual(p.returncode, -signal.SIGKILL)
        self.assertFalse(os.path.exists(self.out()))
        self.assertTrue(os.path.isdir(self.out() + '.partial'))
        self.assertFalse(PBK.verify(self.out())['ok'])
        cli = subprocess.run([sys.executable, str(ROOT / 'tools/public_backup.py'), 'verify', self.out()], capture_output=True, text=True)
        self.assertNotEqual(cli.returncode, 0)
        self.assertFalse(json.loads(cli.stdout)['ok'])

    def test_a_restore_that_fails_half_way_leaves_no_destination(self):
        self.make()

        def boom(event, **info):
            if event == 'file' and info['n'] == 20:
                raise RuntimeError('killed')
        dest = self.out('dest')
        with self.assertRaises(RuntimeError):
            PBK.restore(self.out(), dest, fsync=False, hook=boom)
        self.assertFalse(os.path.exists(dest), 'the destination appears only when the restore is complete')
        rr = PBK.restore(self.out(), dest, fsync=False)
        self.assertFalse(rr['ok'], 'a leftover .restoring folder is not silently reused')
        shutil.rmtree(dest + '.restoring')
        self.assertTrue(PBK.restore(self.out(), dest, fsync=False)['ok'])


class Damage(Base):
    def setUp(self):
        super().setUp()
        run(self.r.started(), 0, self.r.end1 + self.r.y2.plan['total_s'] + 100)
        self.make()
        self.files = os.path.join(self.out(), 'files')

    def assertInvalid(self, text=None):
        v = PBK.verify(self.out())
        self.assertFalse(v['ok'])
        if text:
            self.assertIn(text, ' '.join(v['problems']))
        dest = self.out('dest')
        rr = PBK.restore(self.out(), dest, fsync=False)
        self.assertFalse(rr['ok'])
        self.assertFalse(os.path.exists(dest), 'a damaged backup is never restored')
        return v

    def a_replay(self):
        return os.path.join(self.files, 'playback/games', sorted(os.listdir(os.path.join(self.files, 'playback/games')))[0])

    def test_a_flipped_byte(self):
        p = self.a_replay()
        raw = bytearray(open(p, 'rb').read())
        raw[100] ^= 1
        open(p, 'wb').write(bytes(raw))
        self.assertInvalid('SHA-256 differs')

    def test_a_truncated_file(self):
        p = self.a_replay()
        open(p, 'wb').write(open(p, 'rb').read()[:-10])
        self.assertInvalid('size differs')

    def test_a_missing_file(self):
        os.unlink(self.a_replay())
        self.assertInvalid('missing')

    def test_a_missing_live_segment(self):
        seg = [os.path.join(d, n) for d, _, ns in os.walk(os.path.join(self.files, 'broadcast/live')) for n in ns if n.startswith('s0')][0]
        os.unlink(seg)
        self.assertInvalid('missing')

    def test_an_extra_file(self):
        open(os.path.join(self.files, 'playback/games', 'stray.json'), 'w').write('{}')
        self.assertInvalid('extra file')

    def test_no_manifest(self):
        os.unlink(os.path.join(self.out(), PBK.MANIFEST))
        self.assertInvalid('no BACKUP_MANIFEST.json')

    def test_a_truncated_manifest(self):
        mp = os.path.join(self.out(), PBK.MANIFEST)
        open(mp, 'wb').write(open(mp, 'rb').read()[:200])
        self.assertInvalid()

    def test_an_edited_manifest(self):
        mp = os.path.join(self.out(), PBK.MANIFEST)
        m = json.loads(open(mp, 'rb').read())
        m['files'][0]['sha256'] = '0' * 64
        open(mp, 'w').write(json.dumps(m))
        self.assertInvalid('digest')

    def test_a_manifest_marked_incomplete(self):
        mp = os.path.join(self.out(), PBK.MANIFEST)
        m = json.loads(open(mp, 'rb').read())
        m['complete'] = False
        open(mp, 'w').write(json.dumps(m))
        self.assertInvalid()

    def test_a_replaced_replay_with_a_manifest_rewritten_to_match_is_still_caught_by_the_closure(self):
        """The whole backup rewritten consistently (file, size, SHA-256, manifest digest): the index entry's own SHA-256 and the file's name still disagree."""
        p = self.a_replay()
        rel = os.path.relpath(p, self.files).replace(os.sep, '/')
        doc = json.loads(open(p, 'rb').read())
        doc['final'] = {'away': 99, 'home': 0}
        raw = json.dumps(doc, sort_keys=True, separators=(',', ':')).encode()
        open(p, 'wb').write(raw)
        mp = os.path.join(self.out(), PBK.MANIFEST)
        m = json.loads(open(mp, 'rb').read())
        for f in m['files']:
            if f['path'] == rel:
                f['size'], f['sha256'] = len(raw), hashlib.sha256(raw).hexdigest()
        m['manifest_digest'] = hashlib.sha256(PBK.canon({'files': m['files'], 'profile': m['profile'], 'snapshot': {k: v for k, v in m['snapshot'].items() if k in ('quiescent', 'cumulative')}})).hexdigest()
        open(mp, 'w').write(json.dumps(m))
        self.assertInvalid('differs from its entry')

    def test_a_damaged_source_is_not_backed_up(self):
        r = self.r
        victim = os.path.join(r.public, 'playback', json.loads(open(os.path.join(r.public, 'playback/index.json')).read())['games'][0]['file'])
        os.unlink(victim)
        with self.assertRaises(PBK.Failed) as c:
            self.make('second')
        self.assertIn('reference closure', str(c.exception))
        self.assertFalse(os.path.exists(self.out('second')))

    def test_a_symbolic_link_in_the_source_is_refused(self):
        os.symlink('/etc/hostname', os.path.join(self.r.public, 'playback', 'link.json'))
        with self.assertRaises(PBK.Failed):
            self.make('second')

    def test_a_destination_that_is_not_empty_is_refused(self):
        d = self.out('dest')
        os.makedirs(d)
        open(os.path.join(d, 'x'), 'w').write('1')
        rr = PBK.restore(self.out(), d, fsync=False)
        self.assertFalse(rr['ok'])
        self.assertEqual(os.listdir(d), ['x'])

    def test_an_existing_backup_is_never_overwritten(self):
        with self.assertRaises(PBK.Failed):
            self.make()


class WithThePrivateState(Base):
    def test_the_private_store_profile_and_a_full_restore_of_both_continue_publishing(self):
        r = self.r
        end = r.end1 + r.y2.plan['total_s'] + 100
        mid = r.end1 + r.y2.t['r1'] + 30
        run(r.started(), 0, mid)
        pub = PBK.create(r.public, self.out('pub'), 'public', fsync=False)
        priv = PBK.create(r.store, self.out('priv'), 'store', fsync=False)
        self.assertTrue(pub['ok'] and priv['ok'])
        self.assertTrue(PBK.verify(self.out('priv'))['ok'])
        shutil.rmtree(r.public)
        shutil.rmtree(r.store)                                      # both are gone
        self.assertTrue(PBK.restore(self.out('pub'), r.public, fsync=False)['ok'])
        self.assertTrue(PBK.restore(self.out('priv'), r.store, fsync=False)['ok'])
        run(r.daemon(), mid + 1, end)
        want = self.control_tree(end)
        got = r.tree()
        self.assertEqual(sorted(got), sorted(want))
        for k, vv in want.items():
            if k != 'broadcast/schedule.json':
                self.assertEqual(got[k], vv, k)

    def test_the_store_closure_catches_a_missing_release_or_game(self):
        r = self.r
        run(r.started(), 0, r.end1 + 3000)
        res = PBK.create(r.store, self.out('priv'), 'store', fsync=False)
        self.assertTrue(res['ok'])
        os.unlink(os.path.join(self.out('priv'), 'files', 'games', 'S901-reg-w01-g01', 's0003.json'))
        self.assertFalse(PBK.verify(self.out('priv'))['ok'])
        shutil.rmtree(os.path.join(r.store, 'feed/releases', sorted(os.listdir(os.path.join(r.store, 'feed/releases')))[0]))
        with self.assertRaises(PBK.Failed):
            PBK.create(r.store, self.out('priv2'), 'store', fsync=False)


if __name__ == '__main__':
    unittest.main()
