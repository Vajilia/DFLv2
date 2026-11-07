"""Integration candidate: the supervisor, the daemon and the monitor TOGETHER over two short synthetic seasons, with faults injected (docs/UNATTENDED_OPERATION.md, section 8).

Virtual time (one virtual second per unit; nothing sleeps). The REAL pieces: the daemon (src/dflsite/season.py, with the daemon's own Guard from tools/broadcast_run.py), the
monitor (tools/rehearsal_check.py Watch, with its guard and its independent oracle), the supervisor (tools/broadcast_supervisor.py) running the REAL builder command line as
a child process (tests/fake_builder.py replaces only what one event costs). The synthetic "year" is the four-event season of tests/test_multiseason.py. Nothing here touches the
engine, a league, the Droplet or the rehearsal.

  BASELINE   no fault: two seasons, no violation, no trip, the supervisor one season ahead, the broadcast ends at the end of season 2
  EVERY FAULT in one run, in this order:
      monitor trips on LOW DISK              the daemon holds (paused), nothing advances, the evidence is kept, `release` + `resume` and it goes on
      BUILDER FAILURE                        an event fails again and again: the broadcast holds at the first unbuilt item, health says so, it recovers by itself
      DAEMON DEATH                           the daemon vanishes in the middle of the broadcast and comes back later as a new process
      LOW DISK, then LOW INODES (supervisor) nothing is built, nothing is changed; the broadcast holds at the END of season 1 (the next season is not prepared) until room appears
  What must hold in both runs, at every step (not only at the end):
      * the viewers' position never goes back, never moves faster than time, and never passes the first unbuilt item (nothing skipped, nothing shown that does not exist)
      * NO EARLY RESULTS: no public file contains a result (an event's release marker, the Honors marker) before the position at which the plan releases it
      * the monitor logs nothing but the violations the injected fault is expected to cause, and trips only for the fault that is meant to trip it
  And at the end:
      * the earlier season's public files and private records are byte for byte what they were when the season closed, and no public file of either season is missing
      * the public tree is the SAME as the baseline's (faults delay the broadcast; they never change what is published)
      * nothing was deleted: every file that existed at any check still exists (except the daemon's own atomically rewritten status files)
"""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'tests'))
import broadcast_run as BR                              # noqa: E402
import rehearsal_check as RC                            # noqa: E402
import test_broadcast_supervisor as TS                  # noqa: E402
import test_multiseason as TM                           # noqa: E402
from dflsite import broadcast as B, season as S         # noqa: E402
from test_monitor_multiseason import watch_args         # noqa: E402

T0 = TS.T0
TICK = 2
EVERY = 30                                              # the supervisor and the monitor look every 30 virtual seconds
TOL = 6.0
VOLATILE = ('broadcast/clock.json', 'status.json', 'broadcast/schedule.json')
GB = TS.GB


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def tree(base, skip=()):
    out = {}
    for d, _, names in os.walk(base):
        for n in names:
            if n.startswith('.tmp-'):
                continue
            p = os.path.join(d, n)
            rel = os.path.relpath(p, base)
            if rel in skip:
                continue
            try:
                out[rel] = sha(p)
            except FileNotFoundError:
                pass
    return out


class Sim:
    """The three processes in one virtual timeline. `faults` is a dict of callables run when virtual time passes their key."""

    def __init__(self, tmp, built=0, **cfg):
        self.tmp = tmp
        self.h = TS.Harness(tmp, built=built)
        h = self.h
        self.guard_dir = os.path.join(tmp, 'guard')
        os.makedirs(self.guard_dir)
        base = dict(backoff_base=30, backoff_max=300, escalate_after=3, capacity_retry_s=60, stall_s=300, hold_critical_s=1800, public_gb_per_season=None)
        base.update(cfg)
        self.sup = h.sup(**base)
        self.bc = h.daemon(pos=0.0)
        self.guard = BR.Guard(self.guard_dir, 10 ** 12, 10 ** 12, T0)             # the heartbeat rule is tested elsewhere; here the latch is what matters
        self.mon = RC.Watch(watch_args(h.root, h.public, tmp, guard=self.guard_dir, evidence=os.path.join(tmp, 'evidence'), full_every=EVERY))
        self.t = 0
        self.dead = False
        self.last = None                                    # (t, pos)
        self.log, self.problems, self.health = [], [], None
        self.maxpos = 0.0
        self.horizon = None
        self.seen = set()                                   # every public file path that ever existed (nothing may disappear)
        self.private_seen = {}
        self.events = []
        self.holds = []                                     # (t, horizon_why) once per check while the supervisor says the broadcast holds
        self.obs = []
        self.closed = None                                  # snapshots taken when season 1 closed
        self.offset2 = None

    # ---------------------------------------------------------------------------------------------------------------- the clock of the world
    def now(self):
        return T0 + self.t

    def pos(self):
        return self.bc.clock.position(self.now())

    def kill_daemon(self):
        self.dead = True
        self.events.append((self.t, 'daemon killed', self.pos()))

    def restart_daemon(self):
        self.bc = S.SeasonBroadcaster(self.h.store, self.h.public, guard_publication=True)
        self.dead = False
        self.events.append((self.t, 'daemon restarted', self.pos()))

    def release_and_resume(self, reason='the injected low-disk fault is over: released by the test'):
        self.mon.beat()                                                          # `release` needs a fresh heartbeat (real time)
        self.assertEqualCode(RC.release(__import__('argparse').Namespace(guard=self.guard_dir, reason=reason, evidence=os.path.join(self.tmp, 'evidence'), drill=False)), 0)
        self.assertEqualCode(BR.cmd_pause(__import__('argparse').Namespace(root=self.h.root, guard=self.guard_dir), False), 0)

    @staticmethod
    def assertEqualCode(got, want):
        assert got == want, 'exit code %r, wanted %r' % (got, want)

    # ---------------------------------------------------------------------------------------------------------------- one virtual tick
    def step(self):
        now = self.now()
        if not self.dead:
            self.guard.step(self.h.store, now)
            try:
                self.bc.tick(now)
            except Exception as e:                                               # the daemon keeps trying (tools/broadcast_run.py); a test fault must not hide in here
                self.problems.append((self.t, 'daemon error %s: %s' % (type(e).__name__, e)))
            v = self.bc.view()
            self.horizon = v.horizon
        pos = self.pos()
        if self.last is not None:
            dt, dp = self.t - self.last[0], pos - self.last[1]
            if dp < -1e-6:
                self.problems.append((self.t, 'the position went back by %.1f s' % -dp))
            if dp > dt + 1e-6:
                self.problems.append((self.t, 'the position moved %.1f s in %d s of time: viewing time was skipped' % (dp, dt)))
        if self.horizon is not None and pos > self.horizon + 1e-6:
            self.problems.append((self.t, 'the position %.1f passed the first unbuilt item at %.1f' % (pos, self.horizon)))
        self.last = (self.t, pos)
        self.maxpos = max(self.maxpos, pos)
        if self.t % EVERY == 0:
            self.supervise(now)
            self.monitor(now, pos)
        self.t += TICK

    def supervise(self, now):
        self.health = self.sup.cycle(now)
        self.obs.append((self.t, self.health['status'], [p['code'] for p in self.health['problems']], self.health['builder']['state'], self.health['broadcast']['holding']))
        if self.health['broadcast']['holding']:
            self.holds.append((self.t, self.health['broadcast']['horizon_why']))

    def monitor(self, now, pos):
        before = self.mon.found
        with mock.patch.object(RC.time, 'time', lambda: now):
            p = self.mon.cheap(now)
            if p is not None:
                self.mon.full(now, p)
        self.scan_for_early_results(pos)
        self.scan_nothing_disappeared()
        self.snapshot_when_season_one_closes(pos)

    # ---------------------------------------------------------------------------------------------------------------- the independent checks
    def release_time(self, seq):
        """The broadcast position at which the plan releases event `seq`'s data, from the synthetic plan's own constants (not from the monitor's oracle)."""
        y = self.h.y1
        rel = {1: 100, 2: y.t['r1'], 3: y.t['r2'], 4: y.t['h'] + 300}
        if seq <= 4:
            return rel[seq]
        if seq > 8:
            return float('inf')                                                   # season 3 is prepared but must never be public in these runs
        self.note_offset()
        return float('inf') if self.offset2 is None else self.offset2 + rel[seq - 4]

    def scan_for_early_results(self, pos):
        text = []
        for d, _, names in os.walk(self.h.public):
            for n in names:
                try:
                    text.append(Path(d, n).read_bytes().decode('utf-8', 'replace'))
                except FileNotFoundError:
                    pass
        text = '\n'.join(text)
        for seq in range(1, 13):
            if 'RESULT-OF-EVENT-%d' % seq in text and self.release_time(seq) > pos + TOL:
                self.problems.append((self.t, 'EARLY RESULT: event %d is public at position %.1f, its release is %s' % (seq, pos, self.release_time(seq))))
        for season, seq in ((1, 4), (2, 8)):
            honors_at = self.release_time(seq) - 300                              # the tiara part is released at the start of the Honors block, the year's last event 300 s later
            if 'HONORS-OF-%d' % season in text and honors_at > pos + TOL:
                self.problems.append((self.t, 'EARLY HONORS of season %d at position %.1f (released at %s)' % (season, pos, honors_at)))

    def scan_nothing_disappeared(self):
        now = {os.path.relpath(os.path.join(d, n), self.h.public) for d, _, names in os.walk(self.h.public) for n in names if not n.startswith('.tmp-')}
        gone = sorted(f for f in self.seen - now if f not in VOLATILE)
        if gone:
            self.problems.append((self.t, 'public files disappeared: %s' % gone[:3]))
        self.seen |= now
        for d, _, names in os.walk(self.h.root):
            for n in names:
                rel = os.path.relpath(os.path.join(d, n), self.h.root)
                if rel.startswith(('supervisor', '.builder')) or '.tmp-' in n or rel.endswith(('.lock', 'calls.log')):
                    continue
                self.private_seen[rel] = True

    def snapshot_when_season_one_closes(self, pos):
        carry = os.path.join(self.h.store, S.CARRY)
        if self.closed is None and os.path.exists(carry):
            offset = B.get(carry)['offset_s']
            if pos >= offset + 40:                                               # the hand-over is done and season 2's first second has been published
                self.closed = {'public': tree(self.h.public, skip=VOLATILE + ('latest.json',)), 'at': self.t,
                               'private': {k: sha(os.path.join(self.h.root, k)) for k in ('store/plan.json', 'store/builds.json')}}
                self.closed['public'] = {k: v for k, v in self.closed['public'].items() if not k.endswith('index.json')}

    # ---------------------------------------------------------------------------------------------------------------- driving
    def run_until(self, t_end):
        while self.t < t_end:
            self.step()

    def note_offset(self):
        """Season 2's offset on the broadcast clock, remembered when the hand-over to it happens (store/carry.json is replaced by the next hand-over)."""
        if self.offset2 is None:
            try:
                c = B.get(os.path.join(self.h.store, S.CARRY))
            except OSError:
                return
            if int(c['season']) == 2:
                self.offset2 = c['offset_s']

    def end_pos(self):
        """The position at which season 2's last item is released (its Honors show ends)."""
        return self.offset2 + self.h.y1.plan['total_s']

    def run_to_the_end(self, limit):
        """Until the broadcast is 10 s from the end of season 2 (just before it would hand over to a third: every item of season 2 is public by then)."""
        while self.t < limit:
            self.step()
            self.note_offset()
            if self.offset2 is not None and self.pos() >= self.end_pos() - 10:
                break
        self.run_until(self.t + 4)
        return self.t < limit

    def violations(self):
        p = os.path.join(self.tmp, 'watch.log')
        return [json.loads(x) for x in open(p) if '"violation"' in x] if os.path.exists(p) else []


_BASE = {}


def baseline():
    """The public tree of an uninterrupted two-season run (computed once)."""
    if 'tree' not in _BASE:
        with tempfile.TemporaryDirectory() as tmp:
            B.FSYNC = False
            sim = Sim(tmp)
            sim.finished = sim.run_to_the_end(30000)
            _BASE['sim'] = {'finished': sim.finished, 'problems': list(sim.problems), 'violations': sim.violations(), 'tripped': os.path.exists(os.path.join(sim.guard_dir, 'TRIPPED')),
                            'health': sim.health, 'seasons': sim.h.seasons(), 'holds': list(sim.holds), 'obs': list(sim.obs), 't': sim.t, 'end_pos': sim.end_pos(), 'closed': sim.closed is not None}
            _BASE['tree'] = tree(sim.h.public, skip=VOLATILE)
            _BASE['mon_found'] = sim.mon.found
    return _BASE


class Base(unittest.TestCase):
    def setUp(self):
        B.FSYNC = False
        self.addCleanup(setattr, B, 'FSYNC', True)
        self.tmpd = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpd.cleanup)

    def assertClean(self, sim, allowed_kinds=()):
        self.assertEqual(sim.problems, [], 'invariants broken: %s' % sim.problems[:5])
        bad = [v for v in sim.violations() if v['violation'] not in allowed_kinds]
        self.assertEqual(bad, [], 'the monitor found something it should not have: %s' % bad[:3])


class Baseline(Base):
    def test_two_seasons_with_no_fault_publish_cleanly_and_the_supervisor_stays_one_season_ahead(self):
        b = baseline()['sim']
        self.assertTrue(b['finished'], 'the broadcast reached the end of season 2 (t=%s)' % b['t'])
        self.assertEqual(b['problems'], [])
        self.assertEqual(b['violations'], [])
        self.assertFalse(b['tripped'])
        self.assertEqual(b['seasons'], ['s002', 's003'], 'with season 2 on air season 3 is prepared, and no season 4: never more than one season ahead of the one on air')
        self.assertTrue(b['closed'], 'the season-1 snapshot was taken after the hand-over')
        holding = {why for t, why in b['holds']}
        self.assertTrue(all(w.startswith(('window', 'block')) for w in holding), 'the only holds are the builder catching up at the start: %s' % holding)


class EveryFault(Base):
    def test_two_seasons_survive_monitor_low_disk_builder_failure_daemon_death_low_disk_and_low_inodes(self):
        base = baseline()
        self.assertTrue(base['sim']['finished'])
        sim = Sim(self.tmpd.name)
        h = sim.h
        y1 = h.y1
        mon = sim.mon
        notes = {}

        h.set('fail_at', '3')                                                    # the builder fails at event 3 from the start (section 2 below)
        # ---- 1. the monitor's own low-disk finding trips the guard: the daemon holds (paused) and nothing advances
        sim.run_until(400)
        pos_before = sim.pos()
        mon.a.min_free_gb = 10 ** 9
        sim.run_until(430)
        self.assertEqual(json.load(open(os.path.join(sim.guard_dir, 'TRIPPED')))['kind'], 'disk_low')
        self.assertTrue(os.path.exists(os.path.join(h.store, S.PAUSE)), 'the daemon obeyed the latch')
        frozen = sim.pos()
        sim.run_until(1000)
        self.assertEqual(sim.pos(), frozen, 'nothing advanced while the broadcast was held by the guard')
        self.assertTrue(os.path.isdir(os.path.join(sim.tmp, 'evidence')) and any(n.startswith('trip-') for n in os.listdir(os.path.join(sim.tmp, 'evidence'))), 'the evidence was kept')
        self.assertTrue(any(c == 'broadcast_paused' for c in sum((o[2] for o in sim.obs if 430 <= o[0] <= 1000), [])), 'the supervisor said so')
        mon.a.min_free_gb = 0.0
        sim.release_and_resume()
        sim.run_until(1060)
        self.assertGreater(sim.pos(), frozen, 'after release and resume the broadcast goes on from where it stopped')
        self.assertLess(sim.pos() - frozen, 62, 'from where it stopped: nothing was made up or skipped')
        notes['guard_hold_s'] = 1000 - 430

        # ---- 2. the builder has been failing at event 3 (season 1) since the start: the broadcast has reached the first unbuilt item and holds there; then it recovers by itself
        sim.run_until(3300)
        failing = [o for o in sim.obs if 1060 <= o[0] <= 3300]
        self.assertTrue(any('builder_failed' in o[2] for o in failing), 'builder failure reported')
        self.assertTrue(any('builder_failed' in o[2] and o[1] == 'critical' for o in failing), 'critical after repeated failures')
        held = [o for o in failing if o[4]]
        self.assertTrue(held, 'the broadcast held at the first unbuilt item while the builder was failing')
        self.assertTrue(any('broadcast_holding' in o[2] for o in held))
        hold_pos = sim.pos()
        self.assertAlmostEqual(hold_pos, sim.horizon, delta=1.6, msg='the clock stands at the horizon')
        h.clear('fail_at')

        # ---- 3. season 1 completes; then low disk (supervisor) for a long time: nothing is built and nothing is changed
        for _ in range(400):
            if h.builds(1) and len(h.builds(1)['events']) == 4:
                break
            sim.step()
        self.assertEqual(sorted(int(k) for k in h.builds(1)['events']), [1, 2, 3, 4], 'recovered by itself, every event built exactly once')
        self.assertEqual(sim.health['builder']['consecutive_failures'], 0)
        starts_before = len(h.starts())
        sim.sup.cfg.capacity_fn = TS.small(1)                                    # 1 GiB free against a floor of 4 GiB (+ margins): nothing may be built
        sim.sup.cfg.floor_gb = 4
        t_low = sim.t
        sim.run_until(sim.t + 60)

        # ---- 4. the daemon dies in the middle of the broadcast and comes back 700 virtual seconds later
        pos_at_death = sim.pos()
        epoch_before = B.get(os.path.join(h.store, 'clock.json'))['epoch']
        sim.kill_daemon()
        sim.run_until(sim.t + 700)
        self.assertTrue([v for v in sim.violations() if v['violation'] == 'daemon_silent'], 'the monitor noticed that the daemon was gone')
        self.assertLessEqual(sim.pos() - pos_at_death, B.LEASE_S + 1, 'a dead daemon stops the clock when its lease runs out: the viewers are not carried on')
        sim.restart_daemon()
        sim.run_until(sim.t + 60)
        self.assertGreater(B.get(os.path.join(h.store, 'clock.json'))['epoch'], epoch_before, 'the gap was recorded as a pause')
        self.assertLessEqual(sim.pos() - pos_at_death, B.LEASE_S + 70, 'the broadcast went on from where the viewers stopped, not from where the world is')

        # ---- the broadcast reaches the END of season 1 and holds there: the successor is not prepared because there is no room
        end1 = y1.plan['total_s']
        for _ in range(4000):
            if sim.pos() >= end1 - 1:
                break
            sim.step()
        sim.run_until(sim.t + 240)
        t_low_end = sim.t
        self.assertEqual(h.seasons(), [], 'no room: no next season was prepared')
        self.assertEqual(len(h.starts()), starts_before, 'not one builder action ran while capacity was insufficient')
        low = [o for o in sim.obs if t_low + EVERY <= o[0] <= t_low_end]
        self.assertTrue(low and all('capacity_insufficient' in o[2] for o in low), 'capacity_insufficient was reported throughout')
        self.assertTrue(all(o[1] == 'critical' for o in low))
        self.assertAlmostEqual(sim.pos(), sim.horizon, delta=1.6)
        self.assertAlmostEqual(sim.pos(), end1, delta=2, msg='held at the end of season 1')
        self.assertTrue(any('broadcast_holding' in o[2] for o in low[-4:]), 'the supervisor says the broadcast is held')
        self.assertFalse(os.path.exists(os.path.join(h.store, S.CARRY)), 'no hand-over to a season that does not exist')

        # ---- 5. low inodes instead of low disk: the same stop; then room appears and everything resumes by itself
        sim.sup.cfg.capacity_fn = TS.small(500, inodes=100)
        t_ino = sim.t
        sim.run_until(sim.t + 300)
        self.assertEqual(h.seasons(), [])
        self.assertEqual(len(h.starts()), starts_before, 'nothing built with too few inodes either')
        self.assertTrue(all('capacity_insufficient' in o[2] for o in sim.obs if t_ino + EVERY <= o[0] <= sim.t))
        self.assertAlmostEqual(sim.pos(), end1, delta=2, msg='still held at the end of season 1')
        sim.sup.cfg.capacity_fn = TS.PLENTY
        sim.sup.cfg.floor_gb = 1
        finished = sim.run_to_the_end(40000)
        self.assertTrue(finished, 'the broadcast reached the end of season 2 (t=%s, position %.0f)' % (sim.t, sim.pos()))

        # ---- the substantive checks
        self.assertClean(sim, allowed_kinds=('disk_low', 'daemon_silent'))
        kinds = {v['violation'] for v in sim.violations()}
        self.assertEqual(kinds - {'disk_low', 'daemon_silent'}, set(), 'only the faults that were injected')
        self.assertEqual(h.seasons(), ['s002', 's003'], 'season 3 prepared while season 2 is on air, and no season 4')
        self.assertEqual(sorted(int(k) for k in h.builds(2)['events']), [5, 6, 7, 8])
        self.assertIsNotNone(sim.closed, 'season 1 closed and was snapshotted')
        final = tree(h.public, skip=VOLATILE)
        for k, v in sim.closed['public'].items():
            self.assertEqual(final.get(k), v, 'the earlier season changed or vanished after it closed: ' + k)
        for k, v in sim.closed['private'].items():
            self.assertEqual(sha(os.path.join(h.root, k)), v, 'a finished season record was rewritten: ' + k)
        expected = base['tree']
        self.assertEqual(sorted(set(expected) - set(final)), [], 'files the uninterrupted run published are missing')
        self.assertEqual(sorted(set(final) - set(expected)), [], 'files the uninterrupted run never published')
        self.assertEqual([k for k in expected if expected[k] != final[k]], [], 'same files, different bytes')
        self.assertEqual(sim.health['seasons']['interrupted_evidence'], [])
        for rel in list(sim.private_seen):
            self.assertTrue(os.path.exists(os.path.join(h.root, rel)) or rel.endswith(('PAUSE',)), 'a private file vanished: ' + rel)
        trips = [json.loads(x) for x in open(os.path.join(sim.guard_dir, 'trips.jsonl'))]
        self.assertEqual({t['kind'] for t in trips}, {'disk_low'}, 'every trip is the injected one (the monitor repeats the line at each check while the fault lasts)')
        self.assertEqual(len(os.listdir(os.path.join(sim.tmp, 'evidence', 'released'))), 1, 'one latch, released once, kept')
        self.assertFalse(os.path.exists(os.path.join(sim.guard_dir, 'TRIPPED')))


PROBE = r"""
import errno, json, os, sys
pub, victim, victim2 = sys.argv[1], sys.argv[2], sys.argv[3]
out = {}
def t(name, f):
    try:
        f()
        out[name] = 'ALLOWED'
    except OSError as e:
        out[name] = errno.errorcode.get(e.errno, e.errno)
t('create', lambda: open(os.path.join(pub, 'NEW.txt'), 'w').close())
t('overwrite', lambda: open(victim, 'r+b').write(b'x'))
t('rename', lambda: os.rename(victim, victim + '.moved'))
t('delete', lambda: os.unlink(victim2))
t('mkdir', lambda: os.mkdir(os.path.join(pub, 'NEWDIR')))
print(json.dumps(out))
"""


def as_user(uid, argv, **kw):
    import subprocess

    def drop():
        os.setgid(uid)
        os.setuid(uid)
    env = dict(os.environ, HOME='/nonexistent')
    return subprocess.run(argv, preexec_fn=drop, capture_output=True, text=True, env=env, **kw)


def meta(base):
    """Everything a file system comparison can see: content, size, mode, owner, inode and modification time."""
    out = {}
    for d, _, names in os.walk(base):
        for n in names:
            p = os.path.join(d, n)
            try:
                st = os.stat(p)
                out[os.path.relpath(p, base)] = (sha(p), st.st_size, st.st_mode, st.st_uid, st.st_ino, st.st_mtime_ns)
            except FileNotFoundError:
                pass
    return out


@unittest.skipUnless(os.geteuid() == 0, 'needs root to run the supervisor as an unprivileged user')
class Permissions(Base):
    """The substantive check that the supervisor and the builder cannot touch the public record: they run as a DIFFERENT, unprivileged user that owns the private root and has
    no write permission anywhere in the public folder (the production layout: the daemon user writes the public folder; the unit mounts it read-only for the supervisor too).
    A source scan for deletion calls is supplementary; this compares every public file (content, size, mode, owner, inode, modification time) before and after, and probes the
    permissions directly. A control, as the daemon's user, shows the probe would have succeeded had the permissions allowed it."""
    BUILDER, DAEMON = 61002, 61001

    def test_the_supervisor_and_builder_as_their_own_user_cannot_change_the_public_record_or_a_finished_season(self):
        import subprocess
        tmp = self.tmpd.name
        os.chmod(tmp, 0o755)
        h = TS.Harness(tmp, built=4)                                              # season 1 fully built; season 2 not yet prepared
        bc = h.daemon(pos=0.0)
        for t in range(0, h.y1.plan['total_s'] - 5, 2):                           # the daemon publishes the whole of season 1
            bc.tick(T0 + t)
        for d in (h.public,):
            subprocess.check_call(['chown', '-R', '%d:%d' % (self.DAEMON, self.DAEMON), d])
        for d in (h.root, h.ctl):
            subprocess.check_call(['chown', '-R', '%d:%d' % (self.BUILDER, self.BUILDER), d])
        os.chmod(h.public, 0o755)
        public_before = meta(h.public)
        self.assertGreater(len(public_before), 50, 'there is a real season-1 public record to protect')
        finished = {k: v for k, v in meta(h.root).items() if k in ('store/plan.json', 'store/builds.json') or k.startswith('store/feed/releases/e0000')}
        ends = sorted(k for k in public_before if k.startswith('broadcast/live/') and k.endswith('end.json'))
        victim, victim2 = os.path.join(h.public, ends[0]), os.path.join(h.public, ends[1])

        # the supervisor, as the builder's user, prepares season 2 and builds it, through the real builder command line
        cmd = [sys.executable, str(ROOT / 'tools' / 'broadcast_supervisor.py'), 'once', '--root', h.root, '--public', h.public, '--floor-gb', '0.01', '--step-margin-gb', '0.01',
               '--season-gb', '0.01', '--inode-floor', '10', '--step-inodes', '10', '--season-inodes', '10', '--builder-cmd', json.dumps([sys.executable, TS.FAKE, '--ctl', h.ctl])]
        for i in range(6):
            r = as_user(self.BUILDER, cmd)
            self.assertEqual(r.returncode, 0, r.stderr[-400:])
        doc = json.loads(r.stdout)
        self.assertEqual(doc['builder']['state'], 'ahead', doc['builder'])
        self.assertEqual(h.seasons(), ['s002'])
        self.assertEqual(sorted(int(k) for k in h.builds(2)['events']), [5, 6, 7, 8], 'the unprivileged supervisor really did its job')
        self.assertEqual(os.stat(os.path.join(h.root, 'store', 'seasons', 's002', 'plan.json')).st_uid, self.BUILDER)

        # nothing in the public record changed: content, size, mode, owner, inode, modification time
        self.assertEqual(meta(h.public), public_before, 'the public folder is exactly as the daemon left it')
        for k, v in finished.items():
            self.assertEqual(meta(h.root)[k], v, 'a finished season record was rewritten: ' + k)

        # the permissions themselves: as the supervisor's user every way of changing the public record is refused
        r = as_user(self.BUILDER, [sys.executable, '-c', PROBE, h.public, victim, victim2])
        refused = json.loads(r.stdout)
        self.assertEqual(refused, {'create': 'EACCES', 'overwrite': 'EACCES', 'rename': 'EACCES', 'delete': 'EACCES', 'mkdir': 'EACCES'}, r.stderr[-300:])
        self.assertEqual(meta(h.public), public_before)

        # the control: the daemon's user is allowed, so the refusals above were the permissions and nothing else
        control = os.path.join(tmp, 'control')
        os.makedirs(control)
        control_file, control_file2 = os.path.join(control, 'f'), os.path.join(control, 'g')
        Path(control_file).write_text('x')
        Path(control_file2).write_text('y')
        subprocess.check_call(['chown', '-R', '%d:%d' % (self.DAEMON, self.DAEMON), control])
        r = as_user(self.DAEMON, [sys.executable, '-c', PROBE, control, control_file, control_file2])
        self.assertEqual(set(json.loads(r.stdout).values()), {'ALLOWED'}, r.stderr[-300:])

        # and the daemon goes on to hand over and publish season 2 into the same folder without disturbing season 1
        for t in range(h.y1.plan['total_s'] - 5, h.y1.plan['total_s'] + 200, 2):
            bc.tick(T0 + t)
        self.assertEqual(B.get(os.path.join(h.store, S.CARRY))['season'], 2)
        after = meta(h.public)
        for k, v in public_before.items():
            if k.endswith('index.json') or k in VOLATILE or k == 'latest.json':
                continue
            self.assertEqual(after[k][:3], v[:3], 'season 1 record changed after the hand-over: ' + k)


if __name__ == '__main__':
    unittest.main()
