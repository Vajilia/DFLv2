"""A backup that was taken WHILE the daemon published, restored with the originals gone, and then resumed: publication AND building (synthetic two-season store in virtual time).

The flow of every scenario: the daemon (with its real publication guard, one tick a second) runs to a chosen moment; a public backup is taken while the daemon keeps publishing
(so its passes see different states and it is NOT quiescent), then the private store is taken (the recovery set's order: public first, then store); a CONTROL copy of the
originals as they stand is made, and the originals are deleted. The backups are restored; then the control and the restored copy go on from the same instant, after the same
downtime, with the same builder steps, in lock step, and at every checkpoint:
  * the public tree of the restored copy equals the control's, file for file (so nothing that should be public is missing, and nothing else is there);
  * the viewers' clock is the control's, and never jumps (no unreleased action is skipped);
  * no result, Honors part or later-season game is public before its own release position (checked against the plan, not only against the control);
  * every file that was public when the originals were lost stays byte-identical, on the control and on the restored copy, except the controls and append-only indexes the
    daemon is designed to replace (tests/published_bytes.py states the rule for each; no field of any file is ever normalised).
The real-engine version of the season hand-over is tests/test_backup_resume_real.py (opt-in: it needs a finished first season).
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'tests'))
import public_backup as PBK
import recovery_set as RS
from dflsite import broadcast as B, season as S
from test_multiseason import Rig, Year, T0
import published_bytes as PUB

VOLATILE = ('broadcast/clock.json', 'status.json', 'broadcast/schedule.json')
END1 = 4220                                                    # the synthetic season's length in show seconds (tests/test_multiseason.py: Year.plan['total_s'])
GROWING = ('latest.json', 'playback/index.json', 'broadcast/replays/index.json', 'broadcast/honors/index.json')


def tree(public):
    out = {}
    for d, _, names in os.walk(public):
        for n in names:
            if n.startswith('.tmp-'):
                continue
            p = os.path.join(d, n)
            rel = os.path.relpath(p, public).replace(os.sep, '/')
            if rel in VOLATILE:
                continue
            with open(p, 'rb') as f:
                out[rel] = hashlib.sha256(f.read()).hexdigest()
    return out


def text_of(public):
    out = []
    for d, _, names in os.walk(public):
        for n in names:
            with open(os.path.join(d, n), 'rb') as f:
                out.append(f.read().decode('utf-8', 'replace'))
    return '\n'.join(out)


class Side:
    """One copy of the broadcast being resumed: its builder (the synthetic Year writers, re-attached to this copy's store) and its daemon."""

    def __init__(self, store, public, built1, built2):
        self.store, self.public = store, public
        self.y1, self.y2 = Year(store, 901, 0, rooted=True), Year(store, 902, 4, rooted=False)
        self.y1.build(built1)
        self.y2.build(built2)
        self.bc = S.SeasonBroadcaster(store, public, guard_publication=True)

    def build(self, n1, n2):
        self.y1.build(n1)
        self.y2.build(n2)


class Resume(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        B.FSYNC = False
        self.addCleanup(setattr, B, 'FSYNC', True)

    # ---------------------------------------------------------------------------------------------------------------------- the scenario
    def take(self, built1, built2, t_b, adv, between=0, store_first=False, max_passes=1):
        """Run to t_b, take the backups while the daemon goes on, copy the control, delete the originals. Returns (t_crash, public backup result, store result, control dir)."""
        w = self.tmp.name
        orig = os.path.join(w, 'orig')
        r = Rig(orig)
        os.makedirs(r.store, exist_ok=True)
        r.y1.build(built1)
        r.y2.build(built2)
        bc = r.started(guard=True)
        state = {'t': 0}
        for t in range(0, t_b):
            bc.tick(T0 + t)
        state['t'] = t_b - 1

        def advance(sec):
            for _ in range(sec):
                state['t'] += 1
                bc.tick(T0 + state['t'])

        moved = set()

        def hook(event, **info):                                  # the daemon moves inside every pass, after the cumulative controls were copied (so no pass can be quiet)
            if event == 'file' and adv and info['pass_no'] not in moved and info['n'] >= 6:
                moved.add(info['pass_no'])
                advance(adv)
        if store_first:                                           # the WRONG order, kept only to show why the order is what it is
            sto = PBK.create(r.store, os.path.join(w, 'set', 'store'), 'store', fsync=False)
            advance(between)
            pub = PBK.create(r.public, os.path.join(w, 'set', 'public'), 'public', fsync=False, max_passes=max_passes, hook=hook)
        else:
            pub = PBK.create(r.public, os.path.join(w, 'set', 'public'), 'public', fsync=False, max_passes=max_passes, hook=hook)
            advance(between)                                       # the daemon goes on between the two captures
            sto = PBK.create(r.store, os.path.join(w, 'set', 'store'), 'store', fsync=False)
        self.control_dir = os.path.join(w, 'control')
        shutil.copytree(orig, self.control_dir)
        shutil.rmtree(orig)                                        # the originals are gone
        self.assertFalse(os.path.exists(orig))
        return state['t'], pub, sto

    def restore(self):
        dest = os.path.join(self.tmp.name, 'restored')
        for comp in ('store', 'public'):
            r = PBK.restore(os.path.join(self.tmp.name, 'set', comp), os.path.join(dest, comp), fsync=False)
            self.assertTrue(r['ok'], r)
        return os.path.join(dest, 'store'), os.path.join(dest, 'public')

    def rules(self, pub, sto):
        files = lambda c: os.path.join(self.tmp.name, 'set', c, PBK.FILES)
        built, season = RS.built_events(files('store'))
        pev, pseason = RS.public_state(files('public'))
        return RS.rules(built, built, season, pev, pseason, sto['quiescent'], True, True)

    # ---------------------------------------------------------------------------------------------------------------------- the lock step
    def release_pos(self, offset2):
        t = {'w0': 200, 'r1': 200 + 960}
        t['w1'], t['r2'] = t['r1'] + 900, t['r1'] + 900 + 960
        t['h'] = t['r2'] + 600
        rel = [100, t['r1'], t['r2'], t['h'] + 300]
        return {n: rel[n - 1] for n in (1, 2, 3, 4)}, {n + 4: offset2 + rel[n - 1] for n in (1, 2, 3, 4)}

    def lockstep(self, ctl, rest, t_crash, downtime, seconds, builds, check_every=60):
        """Both sides tick the same instants; `builds` = {second after resuming: (events built of season 1, of season 2)} applied to both."""
        end1 = END1
        rel1, rel2 = self.release_pos(end1)
        rel = {**rel1, **rel2}
        start = t_crash + 1 + downtime
        last = {id(ctl): None, id(rest): None}
        first_pos = None
        published = PUB.snapshot(ctl.public)                       # everything public when the originals were lost
        for k in range(seconds):
            now = T0 + start + k
            if k in builds:
                ctl.build(*builds[k])
                rest.build(*builds[k])
            ctl.bc.tick(now)
            rest.bc.tick(now)
            pc, pr = ctl.bc.clock.position(now), rest.bc.clock.position(now)
            self.assertAlmostEqual(pc, pr, delta=1e-6, msg='the viewers\' clock differs at +%d s' % k)
            for side, p in ((ctl, pc), (rest, pr)):
                if last[id(side)] is not None:
                    self.assertLessEqual(p - last[id(side)], 1.0001, 'the clock jumped (an unreleased stretch was skipped) at +%d s' % k)
                    self.assertGreaterEqual(p, last[id(side)] - 1e-9)
                last[id(side)] = p
            if k < 3 or k % check_every == 0 or k == seconds - 1:
                a, b = tree(ctl.public), tree(rest.public)
                self.assertEqual(sorted(a), sorted(b), 'the public file lists differ at +%d s (clock %.0f): missing %s, extra %s' % (
                    k, pr, sorted(set(a) - set(b))[:4], sorted(set(b) - set(a))[:4]))
                for rel_name, h in a.items():
                    self.assertEqual(b[rel_name], h, '%s differs at +%d s' % (rel_name, k))
                text = text_of(rest.public)
                for n, at in rel.items():
                    if 'RESULT-OF-EVENT-%d' % n in text:
                        self.assertGreaterEqual(pr, at - 1.5, 'event %d is public at clock %.0f, before its release position %d' % (n, pr, at))
        for side, name in ((ctl, 'control'), (rest, 'restored')):
            self.assertEqual(PUB.changed(published, side.public), [], 'public before the originals were lost, changed afterwards (%s)' % name)
        return tree(rest.public)

    # ---------------------------------------------------------------------------------------------------------------------- ordinary play
    def test_ordinary_play_resumes_with_nothing_skipped_early_or_changed(self):
        # season 1, two of four events built (the builder is behind); the backup is taken during the first live window and spans the end of two games
        t_crash, pub, sto = self.take(built1=2, built2=0, t_b=1100, adv=200)
        self.assertFalse(pub['quiescent'], 'the public backup is assembled from several publication states (passes %d)' % pub['passes'])
        self.assertEqual(self.rules(pub, sto), [])
        rs, rp = self.restore()
        ctl = Side(os.path.join(self.control_dir, 'store'), os.path.join(self.control_dir, 'public'), 2, 0)
        rest = Side(rs, rp, 2, 0)
        self.lockstep(ctl, rest, t_crash, downtime=300, seconds=3600, builds={400: (3, 0), 1500: (4, 0)})       # (checks every previously public file, byte for byte, at its end)

    def test_a_short_downtime_and_a_long_one_both_resume_exactly(self):
        for downtime in (0, 40, 7200):
            with self.subTest(downtime=downtime):
                self.tmp.cleanup()
                self.tmp = tempfile.TemporaryDirectory()
                t_crash, pub, sto = self.take(built1=4, built2=0, t_b=1100, adv=200)
                rs, rp = self.restore()
                ctl = Side(os.path.join(self.control_dir, 'store'), os.path.join(self.control_dir, 'public'), 4, 0)
                rest = Side(rs, rp, 4, 0)
                self.lockstep(ctl, rest, t_crash, downtime=downtime, seconds=900, builds={}, check_every=150)

    # ---------------------------------------------------------------------------------------------------------------------- a season hand-over
    def hand_over(self, **kw):
        t_crash, pub, sto = self.take(built1=4, built2=1, **kw)
        return t_crash, pub, sto

    def test_a_hand_over_during_the_public_copy_resumes_and_the_builder_finishes_the_next_season(self):
        t_crash, pub, sto = self.hand_over(t_b=END1 - 150, adv=300)
        self.assertGreater(t_crash, 4220, 'the daemon crossed the end of the season while the backup was being taken')
        self.assertTrue(os.path.exists(os.path.join(self.control_dir, 'store', S.CARRY)))
        self.assertFalse(pub['quiescent'])
        self.assertEqual(self.rules(pub, sto), [])
        rs, rp = self.restore()
        ctl = Side(os.path.join(self.control_dir, 'store'), os.path.join(self.control_dir, 'public'), 4, 1)
        rest = Side(rs, rp, 4, 1)
        final = self.lockstep(ctl, rest, t_crash, downtime=200, seconds=4500, builds={100: (4, 2), 900: (4, 3), 2000: (4, 4)}, check_every=150)
        text = text_of(rest.public)
        for n in (5, 6, 7, 8):
            self.assertIn('RESULT-OF-EVENT-%d' % n, text, 'event %d is public at the end' % n)

    def test_a_hand_over_between_the_public_and_the_store_capture(self):
        """The recovery set's order is what makes this recoverable: the store (which the daemon writes carry.json into) is captured AFTER the public folder, so it is the newer."""
        t_crash, pub, sto = self.hand_over(t_b=END1 - 250, adv=0, between=300)
        self.assertGreater(t_crash, 4220)
        self.assertEqual(self.rules(pub, sto), [], 'the store shows the hand-over, the public folder does not yet: allowed (S3 only forbids the opposite)')
        files = os.path.join(self.tmp.name, 'set', 'store', PBK.FILES)
        self.assertTrue(os.path.exists(os.path.join(files, S.CARRY)))
        rs, rp = self.restore()
        ctl = Side(os.path.join(self.control_dir, 'store'), os.path.join(self.control_dir, 'public'), 4, 1)
        rest = Side(rs, rp, 4, 1)
        self.lockstep(ctl, rest, t_crash, downtime=30, seconds=1500, builds={50: (4, 2)}, check_every=150)

    def test_the_opposite_order_would_be_refused_by_the_rules(self):
        """Store first, then the hand-over, then the public folder: the public folder shows season 2 while the store's season on air is still 1 (rule S3)."""
        t_crash, pub, sto = self.take(built1=4, built2=4, t_b=END1 - 250, adv=0, between=1600, store_first=True)
        problems = self.rules(pub, sto)
        self.assertTrue(problems and problems[0].startswith('S3'), problems)
        # and the rule is right: restored anyway, the viewers' clock (in the older store) is far behind the public folder, which already shows season two's results
        rs, rp = self.restore()
        ctl = Side(os.path.join(self.control_dir, 'store'), os.path.join(self.control_dir, 'public'), 4, 4)
        rest = Side(rs, rp, 4, 4)
        now = T0 + t_crash + 31
        ctl.bc.tick(now)
        rest.bc.tick(now)
        self.assertLess(rest.bc.clock.position(now), ctl.bc.clock.position(now) - 1000, 'the clock is rewound by 1600 s: the results already public would be shown again as future')


class PublishedBytesCheck(unittest.TestCase):
    """The check that every previously published file keeps its bytes can fail: it is not a formality."""

    def test_a_changed_removed_or_rewritten_file_is_reported(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pub = os.path.join(tmp.name, 'public')
        for rel, data in (('playback/games/a.json', b'A'), ('releases/e1/manifest.json', b'{"generated_at":"t0"}'), ('latest.json', b'{}'),
                          ('playback/index.json', b'{"games":["a","b"]}'), ('broadcast/replays/index.json', b'{"games":[]}'), ('broadcast/honors/index.json', b'{"parts":[]}')):
            os.makedirs(os.path.dirname(os.path.join(pub, rel)), exist_ok=True)
            open(os.path.join(pub, rel), 'wb').write(data)
        before = PUB.snapshot(pub)
        self.assertEqual(PUB.changed(before, pub), [])
        open(os.path.join(pub, 'latest.json'), 'wb').write(b'{"release":"e2"}')                                     # a control: allowed
        open(os.path.join(pub, 'playback/index.json'), 'wb').write(b'{"games":["a","b","c"]}')                      # appended: allowed
        self.assertEqual(PUB.changed(before, pub), [])
        open(os.path.join(pub, 'releases/e1/manifest.json'), 'wb').write(b'{"generated_at":"t1"}')                  # a release manifest is NOT excused
        self.assertEqual(len(PUB.changed(before, pub)), 1)
        open(os.path.join(pub, 'releases/e1/manifest.json'), 'wb').write(b'{"generated_at":"t0"}')
        open(os.path.join(pub, 'playback/games/a.json'), 'wb').write(b'B')
        self.assertIn('changed', PUB.changed(before, pub)[0])
        open(os.path.join(pub, 'playback/games/a.json'), 'wb').write(b'A')
        open(os.path.join(pub, 'playback/index.json'), 'wb').write(b'{"games":["b","a"]}')                          # reordered: not append-only
        self.assertIn('reordered', PUB.changed(before, pub)[0])
        open(os.path.join(pub, 'playback/index.json'), 'wb').write(b'{"games":["a","b"]}')
        os.unlink(os.path.join(pub, 'playback/games/a.json'))
        self.assertIn('gone', PUB.changed(before, pub)[0])


if __name__ == '__main__':
    unittest.main()
