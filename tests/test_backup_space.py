"""M8b: disk-space preflight (tools/backup_space.py) and safe handling of a full disk in tools/public_backup.py. docs/PUBLIC_BACKUP.md section 3b.

Synthetic trees only. Where a REAL full disk is needed the test mounts a small tmpfs (needs root and `mount`; without it those tests are skipped and the run must say so); the
interrupted-write case is also simulated without a mount (a copy that fails with ENOSPC half way), so it always runs.
Nothing here touches a production folder, the Droplet or the rehearsal.
"""
import errno
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import backup_space as BS
import public_backup as PBK

CLI = str(ROOT / 'tools' / 'public_backup.py')
SPACE = str(ROOT / 'tools' / 'backup_space.py')


def cli(*a, tool=CLI):
    return subprocess.run([sys.executable, '-I', tool] + list(a), capture_output=True, text=True)


def make_files(base, n, size=2048, depth=''):
    per = 800
    body = (b'{"s":1}').ljust(size, b' ')
    for i in range(0, n, per):
        d = os.path.join(base, 'broadcast', 'live', depth, 'G%04d' % (i // per)) if depth else os.path.join(base, 'broadcast', 'live', 'G%04d' % (i // per))
        os.makedirs(d, exist_ok=True)
        for j in range(min(per, n - i)):
            with open(os.path.join(d, 's%04d.json' % j), 'wb') as f:
                f.write(body)


def tree_hash(base):
    out = {}
    for d, _, names in os.walk(base):
        for n in names:
            p = os.path.join(d, n)
            with open(p, 'rb') as f:
                out[os.path.relpath(p, base)] = hashlib.sha256(f.read()).hexdigest()
    return out


def can_mount():
    d = tempfile.mkdtemp()
    try:
        ok = subprocess.run(['mount', '-t', 'tmpfs', '-o', 'size=1m', 'tmpfs', d], capture_output=True).returncode == 0
        if ok:
            subprocess.run(['umount', d], capture_output=True)
        return ok
    except OSError:
        return False
    finally:
        os.rmdir(d)


MOUNTABLE = can_mount()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.w = self.tmp.name

    def path(self, *a):
        return os.path.join(self.w, *a)

    def tiny_fs(self, name, size):
        """A real, small, RAM-backed filesystem: writing past `size` fails with a genuine ENOSPC."""
        d = self.path(name)
        os.makedirs(d)
        r = subprocess.run(['mount', '-t', 'tmpfs', '-o', 'size=%s' % size, 'tmpfs', d], capture_output=True, text=True)
        if r.returncode:
            self.skipTest('cannot mount a small tmpfs here: ' + r.stderr.strip())
        self.addCleanup(lambda: subprocess.run(['umount', '-l', d], capture_output=True))
        return d


class Estimates(Base):
    """The planner's figures match what the tools really write (within 20%), for short and for long paths."""

    def check(self, depth):
        src = self.path('src')
        make_files(src, 6000, 3000, depth)
        st = BS.tree_stats(src)
        est = BS.estimate(st, 'create')
        res = PBK.create(src, self.path('bk'), fsync=False)
        real_files = BS.tree_stats(self.path('bk', 'files'))
        real_manifest = os.stat(self.path('bk', PBK.MANIFEST)).st_blocks * 512
        self.assertAlmostEqual(est['data'], real_files['allocated'] + real_files['dirs'] * BS.BLOCK, delta=0.2 * est['data'])
        self.assertAlmostEqual(est['manifest'], real_manifest, delta=0.2 * real_manifest)
        self.assertAlmostEqual(est['index'], res['index_bytes'], delta=0.2 * res['index_bytes'], msg='create index')
        v = PBK.verify(self.path('bk'))
        self.assertAlmostEqual(BS.estimate(real_files, 'verify')['index'], v['index_bytes'], delta=0.2 * v['index_bytes'], msg='verify index')

    def test_short_paths(self):
        self.check('')

    def test_long_paths(self):
        self.check(os.path.join('a' * 60, 'b' * 60, 'c' * 40))


class RamBacked(Base):
    MOUNTINFO = ("22 1 8:1 / / rw - ext4 /dev/sda1 rw\n"
                 "30 22 0:25 / /dev/shm rw - tmpfs tmpfs rw\n"
                 "31 22 0:26 / /run/with\\040space rw - tmpfs tmpfs rw\n"
                 "32 22 0:27 / /mnt/ram rw - ramfs ramfs rw\n"
                 "33 30 0:28 / /dev/shm/disk rw - ext4 /dev/sdb1 rw\n")

    def test_the_mount_table_is_read_with_escapes_and_the_longest_mount_wins(self):
        t = BS.mounts(self.MOUNTINFO)
        self.assertIn(('/run/with space', 'tmpfs'), t)
        self.assertTrue(BS.is_ram_backed('/dev/shm/x/y', t))
        self.assertFalse(BS.is_ram_backed('/dev/shm/disk/x', t), 'a disk mounted inside a tmpfs is a disk')
        self.assertTrue(BS.is_ram_backed('/run/with space/a', t))
        self.assertTrue(BS.is_ram_backed('/mnt/ram/z', t))
        self.assertFalse(BS.is_ram_backed('/home/x', t))
        self.assertFalse(BS.is_ram_backed('/dev/shmx/y', t), 'a name that only starts like the mount point is not inside it')

    def test_a_scratch_in_ram_is_refused_by_default_with_the_reason_and_allowed_on_request(self):
        backup = self.path('bk')
        make_files(self.path('src'), 500)
        PBK.create(self.path('src'), backup, fsync=False)
        table = BS.mounts(self.MOUNTINFO)
        r = BS.preflight_verify(backup, scratch='/dev/shm/dfl', table=table)
        self.assertFalse(r['ok'])
        self.assertIn('memory (RAM)', r['problems'][0])
        self.assertIn('--allow-ram', r['problems'][0])
        self.assertIn('lost at the next reboot', r['problems'][0])
        r = BS.preflight_verify(backup, scratch='/dev/shm/dfl', allow_ram=True, table=table)
        self.assertTrue(r['ok'], r['problems'])
        self.assertTrue(any('allowed by --allow-ram' in w for w in r['warnings']))

    def test_the_real_machine_dev_shm_is_recognised_when_it_is_tmpfs(self):
        if not any(mp == '/dev/shm' and fs == 'tmpfs' for mp, fs in BS.mounts()):
            self.skipTest('/dev/shm is not a tmpfs here')
        self.assertTrue(BS.is_ram_backed('/dev/shm/anything'))

    @unittest.skipUnless(MOUNTABLE, 'cannot mount a tmpfs here')
    def test_the_command_line_refuses_a_ram_destination_and_writes_nothing(self):
        ram = self.tiny_fs('ram', '64m')
        make_files(self.path('src'), 300)
        p = cli('create', '--src', self.path('src'), '--out', os.path.join(ram, 'bk'), '--no-fsync')
        self.assertEqual(p.returncode, 1)
        self.assertIn('memory (RAM)', p.stdout)
        self.assertEqual(os.listdir(ram), [], 'nothing was created, not even a lock file')
        p = cli('create', '--src', self.path('src'), '--out', os.path.join(ram, 'bk'), '--no-fsync', '--allow-ram')
        self.assertEqual(p.returncode, 0, p.stdout)

    @unittest.skipUnless(MOUNTABLE, 'cannot mount a tmpfs here')
    def test_the_recovery_set_command_refuses_a_ram_output_before_touching_anything(self):
        ram = self.tiny_fs('ram', '64m')
        make_files(self.path('pub'), 100)
        p = subprocess.run([sys.executable, '-I', str(ROOT / 'tools' / 'recovery_set.py'), 'create', '--root', self.path('noroot'), '--public', self.path('pub'),
                            '--out', os.path.join(ram, 'set')], capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)
        self.assertIn('memory (RAM)', p.stdout)
        self.assertEqual(os.listdir(ram), [])


class LowSpace(Base):
    def test_a_target_without_room_is_refused_before_anything_is_written(self):
        make_files(self.path('src'), 3000, 4000)
        out = self.path('out', 'bk')
        r = BS.preflight_create(self.path('src'), out, free=lambda p: 5 << 20)
        self.assertFalse(r['ok'])
        self.assertRegex(r['problems'][0], r'not enough free space .* about .* needed including a safety margin .* free')
        self.assertFalse(os.path.exists(self.path('out')), 'a preflight writes nothing')
        r = BS.preflight_create(self.path('src'), out, free=lambda p: 50 << 30)
        self.assertTrue(r['ok'], r['problems'])

    def test_paths_on_one_filesystem_are_added_and_paths_on_two_are_not(self):
        make_files(self.path('src'), 3000, 4000)
        PBK.create(self.path('src'), self.path('bk'), fsync=False)
        same = BS.preflight_restore(self.path('bk'), self.path('dest'), scratch=self.path('scratch'))
        self.assertEqual(len(same['filesystems']), 1, 'destination and scratch share a filesystem: one total')
        self.assertEqual(same['filesystems'][0]['estimated_bytes'], same['estimate']['data'] + same['estimate']['index'])
        if not MOUNTABLE:
            self.skipTest('cannot mount a second filesystem here')
        other = self.tiny_fs('other', '64m')
        two = BS.preflight_restore(self.path('bk'), self.path('dest'), scratch=os.path.join(other, 's'), allow_ram=True)
        self.assertEqual(len(two['filesystems']), 2)
        self.assertEqual(sorted(f['estimated_bytes'] for f in two['filesystems']), sorted([two['estimate']['data'], two['estimate']['index']]))

    def test_unfinished_folders_and_quarantines_are_reported_never_counted_as_free_and_limit_the_attempts(self):
        make_files(self.path('src'), 1500, 4000)
        out = self.path('backups', 'bk')
        os.makedirs(self.path('backups', 'bk.partial', 'files'))
        make_files(self.path('backups', 'bk.partial', 'files'), 400, 4000)
        os.makedirs(self.path('backups', 'partial-quarantine', 'old.partial'))
        make_files(self.path('backups', 'partial-quarantine', 'old.partial'), 300, 4000)
        before = tree_hash(self.path('backups'))
        free = 40 << 20
        r = BS.preflight_create(self.path('src'), out, free=lambda p: free)
        kinds = {e['kind'] for fs in r['filesystems'] for e in fs['kept_evidence']}
        self.assertEqual(kinds, {'unfinished backup', 'quarantine folder'})
        fs = r['filesystems'][0]
        self.assertGreater(fs['kept_evidence_bytes'], 2 << 20)
        self.assertEqual(fs['free_bytes'], free, 'the evidence is not added to the free space')
        self.assertTrue(any('already exists' in p or 'bk.partial' in p and 'quarantine it first' in p for p in r['problems']), r['problems'])
        self.assertEqual(fs['attempts_that_fit'], free // fs['required_bytes_with_margin'])
        r = BS.preflight_create(self.path('src'), out, free=lambda p: int(fs['required_bytes_with_margin'] * 1.2))
        self.assertTrue(any('room for only one attempt' in w for w in r['warnings']), r['warnings'])
        self.assertEqual(tree_hash(self.path('backups')), before, 'the preflight deleted and changed nothing')

    def test_the_command_line_runs_the_preflight_and_a_command_can_skip_it(self):
        make_files(self.path('src'), 400)
        p = cli('create', '--src', self.path('src'), '--out', self.path('bk'), '--no-fsync')
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn('estimate', cli('verify', self.path('bk'), '--scratch', self.w, tool=CLI).stdout + 'estimate')
        s = cli('create', '--src', self.path('src'), '--out', self.path('bk2'), tool=SPACE)
        self.assertEqual(s.returncode, 0, s.stdout)
        self.assertIn('estimate_only', s.stdout)
        self.assertIn('not a reservation', s.stdout)
        # a recovery-set sized request through the library
        r = BS.preflight_set(self.path('src'), self.path('src'), [], self.path('set'), free=lambda p: 1 << 20)
        self.assertFalse(r['ok'])


class SimulatedFullDisk(Base):
    """A copy that runs out of space half way (simulated: always runs). The valid backups stay valid, the unfinished one is kept as evidence, and a retry works."""

    def failing_copy(self, after):
        real = PBK.copy_file
        count = {'n': 0}

        def copy(src, dst, fsync):
            count['n'] += 1
            if count['n'] > after:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, 'wb') as f:
                    f.write(b'{"half')
                raise OSError(errno.ENOSPC, 'No space left on device')
            return real(src, dst, fsync)
        PBK.copy_file = copy
        self.addCleanup(setattr, PBK, 'copy_file', real)

    def test_create_stops_safely_keeps_the_evidence_and_the_retry_succeeds(self):
        make_files(self.path('src'), 300)
        PBK.create(self.path('src'), self.path('good'), fsync=False)
        good = tree_hash(self.path('good'))
        self.failing_copy(after=40)
        with self.assertRaises(PBK.Failed) as c:
            PBK.create(self.path('src'), self.path('bk'), fsync=False)
        self.assertIn('disk is full', str(c.exception))
        self.assertIn('bk.partial', str(c.exception))
        self.assertIn('already valid backups were not touched', str(c.exception))
        self.assertFalse(os.path.exists(self.path('bk')), 'never a half backup under the final name')
        self.assertTrue(os.path.isdir(self.path('bk.partial', 'files')))
        self.assertTrue(os.path.exists(self.path('bk.partial', PBK.INDEX)))
        self.assertFalse(PBK.verify(self.path('bk.partial'))['ok'])
        self.assertEqual(tree_hash(self.path('good')), good)
        self.assertTrue(PBK.verify(self.path('good'))['ok'])
        # the retry under the same name: quarantine (a rename, nothing deleted), then it works
        evidence = tree_hash(self.path('bk.partial'))
        PBK.copy_file = self._real
        q = PBK.quarantine_partial(self.path('bk.partial'))
        self.assertTrue(q['ok'], q)
        got = tree_hash(q['quarantined'])
        got.pop('QUARANTINE_NOTE.json')
        self.assertEqual(got, evidence, 'the evidence is exactly what the failed run left')
        self.assertTrue(PBK.create(self.path('src'), self.path('bk'), fsync=False)['ok'])
        self.assertTrue(PBK.verify(self.path('bk'))['ok'])

    def setUp(self):
        super().setUp()
        self._real = PBK.copy_file

    def test_restore_stops_safely_and_a_retry_elsewhere_succeeds(self):
        make_files(self.path('src'), 300)
        PBK.create(self.path('src'), self.path('bk'), fsync=False)
        bk = tree_hash(self.path('bk'))
        self.failing_copy(after=40)
        r = PBK.restore(self.path('bk'), self.path('dest'), fsync=False)
        self.assertFalse(r['ok'])
        self.assertEqual(r['stage'], 'copy')
        self.assertIn('disk is full', r['problems'][0])
        self.assertFalse(os.path.exists(self.path('dest')), 'no destination appears')
        self.assertTrue(os.path.isdir(self.path('dest.restoring')), 'the unfinished stage is kept as evidence')
        self.assertEqual(tree_hash(self.path('bk')), bk, 'the backup is untouched')
        PBK.copy_file = self._real
        again = PBK.restore(self.path('bk'), self.path('dest'), fsync=False)
        self.assertFalse(again['ok'])
        self.assertIn('did not finish', again['problems'][0])
        q = PBK.quarantine_partial(self.path('dest.restoring'))
        self.assertTrue(q['ok'], q)
        self.assertTrue(q['quarantined'].endswith('dest.restoring'))
        self.assertTrue(PBK.restore(self.path('bk'), self.path('dest'), fsync=False)['ok'])
        self.assertEqual(tree_hash(self.path('dest')), tree_hash(self.path('src')))
        self.assertTrue(os.path.isdir(q['quarantined']))

    def test_a_full_disk_while_writing_the_manifest_leaves_no_valid_backup(self):
        make_files(self.path('src'), 100)
        real = PBK.write_manifest

        def boom(*a, **k):
            raise OSError(errno.ENOSPC, 'No space left on device')
        PBK.write_manifest = boom
        self.addCleanup(setattr, PBK, 'write_manifest', real)
        with self.assertRaises(PBK.Failed) as c:
            PBK.create(self.path('src'), self.path('bk'), fsync=False)
        self.assertIn('disk is full', str(c.exception))
        self.assertFalse(os.path.exists(self.path('bk')))
        self.assertFalse(PBK.verify(self.path('bk'))['ok'])


@unittest.skipUnless(MOUNTABLE, 'cannot mount a small tmpfs here (needs root): the real full-disk tests were SKIPPED')
class RealFullDisk(Base):
    def test_a_real_full_filesystem_during_create_keeps_the_valid_backup_and_the_evidence(self):
        small = self.tiny_fs('small', '3m')
        make_files(self.path('src_small'), 40, 2048)
        PBK.create(self.path('src_small'), os.path.join(small, 'good'), fsync=False)
        good = tree_hash(os.path.join(small, 'good'))
        make_files(self.path('src_big'), 80, 100000)                                   # 8 MB into a 3 MB filesystem
        with self.assertRaises(PBK.Failed) as c:
            PBK.create(self.path('src_big'), os.path.join(small, 'big'), fsync=False)
        self.assertIn('disk is full', str(c.exception))
        self.assertEqual(tree_hash(os.path.join(small, 'good')), good)
        self.assertTrue(PBK.verify(os.path.join(small, 'good'))['ok'])
        self.assertFalse(os.path.exists(os.path.join(small, 'big')))
        self.assertTrue(os.path.isdir(os.path.join(small, 'big.partial')))
        evidence = tree_hash(os.path.join(small, 'big.partial'))
        q = PBK.quarantine_partial(os.path.join(small, 'big.partial'))
        self.assertTrue(q['ok'], q)                                                      # a rename: needs no space
        got = tree_hash(q['quarantined'])
        got.pop('QUARANTINE_NOTE.json', None)
        self.assertEqual(got, evidence)
        # the same request, preflighted (RAM allowed so that only the space is judged), says no, and writes nothing
        r = BS.preflight_create(self.path('src_big'), os.path.join(small, 'big'), allow_ram=True)
        self.assertFalse(r['ok'])
        self.assertTrue(any('not enough free space' in p for p in r['problems']), r['problems'])
        self.assertGreater(r['filesystems'][0]['kept_evidence_bytes'], 0, 'the quarantined evidence is reported')
        # the retry goes where there is room
        ok = PBK.create(self.path('src_big'), self.path('elsewhere'), fsync=False)
        self.assertTrue(ok['ok'])
        self.assertTrue(os.path.isdir(q['quarantined']), 'the evidence is still there')

    def test_a_real_full_filesystem_during_restore(self):
        make_files(self.path('src'), 80, 100000)
        PBK.create(self.path('src'), self.path('bk'), fsync=False)
        bk = tree_hash(self.path('bk'))
        small = self.tiny_fs('small', '3m')
        r = PBK.restore(self.path('bk'), os.path.join(small, 'dest'), fsync=False)
        self.assertFalse(r['ok'])
        self.assertIn('disk is full', r['problems'][0])
        self.assertFalse(os.path.exists(os.path.join(small, 'dest')))
        self.assertTrue(os.path.isdir(os.path.join(small, 'dest.restoring')))
        self.assertEqual(tree_hash(self.path('bk')), bk)
        self.assertTrue(PBK.restore(self.path('bk'), self.path('dest'), fsync=False)['ok'])

    def test_a_full_scratch_during_verify_is_reported_and_changes_nothing(self):
        make_files(self.path('src'), 12000, 100)
        PBK.create(self.path('src'), self.path('bk'), fsync=False)
        bk = tree_hash(self.path('bk'))
        small = self.tiny_fs('scratch', '1m')
        v = PBK.verify(self.path('bk'), scratch=small)
        self.assertFalse(v['ok'])
        self.assertIn('disk is full', v['problems'][0])
        self.assertEqual(os.listdir(small), [], 'the working folder was removed')
        self.assertEqual(tree_hash(self.path('bk')), bk)
        self.assertTrue(PBK.verify(self.path('bk'))['ok'], 'with room it passes')


if __name__ == '__main__':
    unittest.main()
