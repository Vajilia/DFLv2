"""M6, unattended operation: tools/broadcast_supervisor.py (docs/UNATTENDED_OPERATION.md).

Short synthetic seasons (the four-event "year" of tests/test_multiseason.py) and the REAL builder command line with its engine part replaced (tests/fake_builder.py); the daemon
(src/dflsite/season.py) runs in virtual time where the clock matters. Nothing here touches the engine, a league, the Droplet or the site.

What the supervisor must do:
  * prepare the next season before it is needed (once the season on air is fully built), build it, and stop there: never more than one season ahead
  * hold the broadcast at the prepared-content boundary when building falls behind, and never skip viewing time (the daemon's rule; shown here through the supervisor's own rig)
  * never run two builders (a second supervisor is refused; a builder held by someone else is busy, not a failure) and resume safely after a kill
  * check disk space and inodes before it builds and stop safely, without a failure and without deleting anything, when there is not enough
  * report builder failure, stalled publication, low capacity and a holding broadcast as machine-readable health, and delete nothing, ever
"""
import ast
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'tests'))
import broadcast_supervisor as SV                       # noqa: E402
import rootlock as RL                                   # noqa: E402
import test_multiseason as TM                           # noqa: E402
from dflsite import broadcast as B, season as S         # noqa: E402

PY = sys.executable
FAKE = str(ROOT / 'tests' / 'fake_builder.py')
SUP = str(ROOT / 'tools' / 'broadcast_supervisor.py')
T0 = 1000.0
GB = 1024 ** 3
PLENTY = lambda path: {'free_bytes': 500 * GB, 'free_inodes': 10 ** 8, 'total_inodes': 10 ** 8}      # noqa: E731


def small(free_gb, inodes=10 ** 8):
    return lambda path: {'free_bytes': int(free_gb * GB), 'free_inodes': inodes, 'total_inodes': 10 ** 8}


class Harness:
    def __init__(self, tmp, built=4):
        self.tmp = tmp
        self.root, self.public, self.ctl = (os.path.join(tmp, n) for n in ('root', 'public', 'ctl'))
        self.store = os.path.join(self.root, 'store')
        os.makedirs(self.ctl)
        os.makedirs(self.public)
        self.y1 = TM.Year(self.store, 1, 0, rooted=True)
        self.y1.build(built)
        open(os.path.join(self.store, S.CONTINUOUS), 'w').close()
        self.health_path = os.path.join(self.root, 'supervisor', 'health.json')

    def set(self, name, value='1'):
        Path(self.ctl, name).write_text(str(value))

    def clear(self, name):
        p = Path(self.ctl, name)
        if p.exists():
            p.unlink()

    def calls(self):
        p = Path(self.ctl, 'calls.log')
        return [l.split() for l in p.read_text().splitlines()] if p.exists() else []

    def starts(self):
        return [(int(l[2]), int(l[3])) for l in self.calls() if l[0] == 'start']

    def cfg(self, **kw):
        d = dict(root=self.root, public=self.public, builder_cmd=[PY, FAKE, '--ctl', self.ctl], capacity_fn=PLENTY, floor_gb=1, step_margin_gb=0.1, season_gb=0.5, inode_floor=1000,
                 season_inodes=5000, backoff_base=60, backoff_max=1800, escalate_after=3, busy_warn_s=900, stall_s=300, hold_critical_s=1800)
        d.update(kw)
        return SV.Config(**d)

    def sup(self, **kw):
        return SV.Supervisor(self.cfg(**kw))

    def health(self):
        return json.loads(Path(self.health_path).read_text())

    def builds(self, season):
        d = self.store if season == 1 else os.path.join(self.store, 'seasons', 's%03d' % season)
        p = os.path.join(d, 'builds.json')
        return json.loads(Path(p).read_text()) if os.path.exists(p) else None

    def seasons(self):
        d = os.path.join(self.store, 'seasons')
        return sorted(n for n in os.listdir(d) if len(n) == 4) if os.path.isdir(d) else []       # as the builder selects: sNNN (a `.partial` is not a season)

    def daemon(self, pos=0.0):
        bc = S.SeasonBroadcaster(self.store, self.public, guard_publication=True)
        bc.clock.start(T0, pos, B.LEASE_S, ready_until=pos)
        return bc

    def tree(self, sub=None):
        out = {}
        base = self.public if sub is None else os.path.join(self.public, sub)
        for d, _, names in os.walk(base):
            for n in names:
                p = os.path.join(d, n)
                out[os.path.relpath(p, self.public)] = Path(p).read_bytes()
        return out

    def store_tree(self):
        out = {}
        for d, _, names in os.walk(self.root):
            for n in names:
                p = os.path.join(d, n)
                rel = os.path.relpath(p, self.root)
                if rel.startswith('supervisor' + os.sep) or rel == RL.LOCK:
                    continue
                out[rel] = Path(p).read_bytes()
        return out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmpd = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpd.cleanup)
        B.FSYNC = False
        self.addCleanup(setattr, B, 'FSYNC', True)
        self.h = Harness(self.tmpd.name)

    def run_until(self, sup, state, now=T0, limit=20, step=1.0):
        h = None
        for i in range(limit):
            h = sup.cycle(now + i * step)
            if h['builder']['state'] == state:
                return h, now + i * step
        self.fail('never reached %r: %s' % (state, json.dumps(h['builder'])))


# ---------------------------------------------------------------------------------------------------------------------------------------- the next season, ahead of time
class Rollover(Base):
    def test_a_complete_season_gets_its_successor_prepared_and_built_and_no_more(self):
        h = self.h
        sup = h.sup()
        hl, now = self.run_until(sup, 'ahead')
        self.assertEqual(h.seasons(), ['s002'], 'one season ahead, never two')
        b = h.builds(2)
        self.assertEqual(sorted(int(k) for k in b['events']), [5, 6, 7, 8])
        self.assertEqual(hl['seasons'], {'on_air': 1, 'target': 2, 'prepared_ahead': 1, 'ahead_complete': True, 'interrupted_evidence': [], 'partial_pending': []})
        n = len(h.calls())
        for i in range(4):
            sup.cycle(now + 10 + i)
        self.assertEqual(len(h.calls()), n, 'nothing more is built while the successor waits')
        self.assertEqual(h.seasons(), ['s002'])

    def test_an_unfinished_first_season_is_built_before_anything_is_prepared(self):
        self.h = Harness(self.tmpd.name + '-b', built=2)
        h = self.h
        sup = h.sup()
        h1 = sup.cycle(T0)
        self.assertEqual(h1['builder']['state'], 'building')
        self.assertEqual(h.seasons(), [], 'the season on air is finished first')
        self.run_until(sup, 'ahead', now=T0 + 1)
        self.assertEqual(sorted(int(k) for k in h.builds(1)['events']), [1, 2, 3, 4])
        self.assertEqual(h.seasons(), ['s002'])

    def test_rollover_through_the_daemon_keeps_every_earlier_public_record_and_prepares_the_next(self):
        h = self.h
        bc = h.daemon(pos=h.y1.plan['total_s'] - 60)
        sup = h.sup()
        self.run_until(sup, 'ahead')
        t = 0
        while not os.path.exists(os.path.join(h.store, S.CARRY)) and t < 600:
            bc.tick(T0 + t)
            t += 1
        self.assertEqual(B.get(os.path.join(h.store, S.CARRY))['season'], 2, 'the daemon handed over to the prepared season')
        for k in range(20):
            bc.tick(T0 + t + k)
        before = {k: v for k, v in h.tree().items() if k.startswith(('releases', 'broadcast/live', 'broadcast/honors', 'playback')) and not k.endswith('index.json')}
        self.assertTrue(before)
        store_before = {k: v for k, v in h.store_tree().items() if k.startswith(('store/plan.json', 'store/builds.json', 'store/seasons/s002'))}
        hl, now = self.run_until(sup, 'ahead', now=T0 + t + 100)
        self.assertEqual(hl['seasons']['on_air'], 2)
        self.assertEqual(h.seasons(), ['s002', 's003'])
        self.assertEqual(sorted(int(k) for k in h.builds(3)['events']), [9, 10, 11, 12])
        for k in range(30):
            bc.tick(T0 + t + 200 + k)
        after = h.tree()
        for k, v in before.items():
            self.assertIn(k, after, 'no earlier public record is ever removed: ' + k)
            self.assertEqual(after[k], v, 'nor changed: ' + k)
        now_store = h.store_tree()
        for k in ('store/plan.json', 'store/builds.json'):
            self.assertEqual(now_store[k], store_before[k], 'the supervisor never rewrites a finished season')

    def test_a_crash_between_the_two_files_next_season_writes_leaves_no_half_season(self):
        h = self.h
        h.set('crash_next_season')
        sup = h.sup()
        hl = sup.cycle(T0)
        self.assertEqual(hl['builder']['state'], 'failed')
        self.assertEqual(h.seasons(), [], 'no half-prepared season is visible to the builder or the daemon')
        hl, _ = self.run_until(sup, 'ahead', now=T0 + 1000, limit=20, step=100)
        self.assertEqual(h.seasons(), ['s002'])
        self.assertEqual(sorted(int(k) for k in h.builds(2)['events']), [5, 6, 7, 8])


# ---------------------------------------------------------------------------------------------------------------------------------------- building falls behind
class SlowBuilder(Base):
    def test_the_clock_holds_at_the_prepared_boundary_and_no_viewing_time_is_skipped(self):
        h = self.h
        end1 = h.y1.plan['total_s']
        bc = h.daemon(pos=end1 - 5)
        sup = h.sup()
        built_at = {100: 1, 400: 1, 800: 1, 1200: 1, 1600: 1}          # the builder gets one action at these virtual seconds: slow
        pos, holding, health = [], [], {}
        for t in range(0, 2300):
            r = bc.tick(T0 + t)
            p = bc.clock.position(T0 + t)
            v = bc.view()
            if v.horizon is not None:
                self.assertLessEqual(p, v.horizon + 1.5, 'never past what is prepared (t=%d)' % t)
            pos.append(p)
            holding.append(bool(r['holding']))
            if t in built_at:
                sup.cycle(T0 + t)
            if t in (300, 700):
                health[t] = sup.cycle(T0 + t, act=False)          # a look at the health, no builder action
        for a, b in zip(pos, pos[1:]):
            self.assertGreaterEqual(b, a - 1e-6, 'the clock never goes back')
            self.assertLessEqual(b - a, 1.6, 'the clock never jumps')
        self.assertTrue(any(holding), 'it did hold')
        moved = pos[-1] - pos[0]
        running = len([x for x in holding if not x])
        self.assertAlmostEqual(moved, running, delta=6, msg='all the viewing time that ran was shown: the hold took time away from nothing')
        for t, hl in health.items():
            self.assertTrue(hl['broadcast']['holding'], 'health says the broadcast is holding (t=%d)' % t)
            codes = [p['code'] for p in hl['problems']]
            self.assertIn('broadcast_holding', codes)
            self.assertEqual(hl['status'], 'warning')
        self.assertEqual(len(h.builds(2)['events']), 4)
        self.assertGreater(pos[-1], end1 + 400, 'and it went on once more was built')

    def test_a_fast_builder_never_holds(self):
        h = self.h
        end1 = h.y1.plan['total_s']
        bc = h.daemon(pos=end1 - 5)
        sup = h.sup()
        self.run_until(sup, 'ahead')
        holds = 0
        for t in range(0, 500):
            r = bc.tick(T0 + t)
            holds += bool(r['holding']) and t > 30
        self.assertEqual(holds, 0)
        hl = sup.cycle(T0 + 499)
        self.assertFalse(hl['broadcast']['holding'])
        self.assertGreater(hl['broadcast']['lead_s'], 0)


# ---------------------------------------------------------------------------------------------------------------------------------------- builder failure
class Failure(Base):
    def test_failure_is_reported_retried_with_backoff_and_recovers_without_duplicates(self):
        h = self.h
        h.set('fail_at', 2)
        sup = h.sup()
        now = T0
        failures = 0
        for i in range(40):
            hl = sup.cycle(now)
            if hl['builder']['state'] == 'failed' and hl['builder']['consecutive_failures'] > failures:
                failures = hl['builder']['consecutive_failures']
                self.assertIn('synthetic failure', hl['builder']['last_error'])
                self.assertGreater(hl['builder']['next_attempt_at'], now, 'the retry waits (backoff)')
                codes = {p['code']: p['severity'] for p in hl['problems']}
                self.assertEqual(codes.get('builder_failed'), 'critical' if failures >= 3 else 'warning')
                self.assertEqual(hl['status'], 'critical' if failures >= 3 else 'warning')
                if failures == 1:
                    n = len(h.calls())
                    sup.cycle(now + 5)
                    self.assertEqual(len(h.calls()), n, 'no attempt before the backoff has passed')
                if failures == 3:
                    break
                now = hl['builder']['next_attempt_at'] + 0.1
            else:
                now += 1
        self.assertEqual(failures, 3)
        self.assertEqual(h.starts().count((2, 6)), 3)
        h.clear('fail_at')
        now = hl['builder']['next_attempt_at'] + 0.1
        hl, _ = self.run_until(sup, 'ahead', now=now)
        self.assertEqual(hl['builder']['consecutive_failures'], 0)
        self.assertEqual([p for p in hl['problems'] if p['code'] == 'builder_failed'], [])
        self.assertEqual(sorted(int(k) for k in h.builds(2)['events']), [5, 6, 7, 8], 'every event built exactly once in the store')
        self.assertEqual([s for s in h.starts() if s[1] == 5], [(2, 5)], 'a finished event is never built again')

    def test_a_failure_changes_nothing_that_is_public_or_finished(self):
        h = self.h
        h.set('fail_at', 1)
        before = h.store_tree()
        pub = h.tree()
        sup = h.sup()
        sup.cycle(T0)                                   # next-season
        sup.cycle(T0 + 1)                               # event 1 fails
        after = h.store_tree()
        for k, v in before.items():
            if k.endswith(('plan.json', 'builds.json')) and 'seasons' not in k:
                self.assertEqual(after[k], v)
        self.assertEqual(h.tree(), pub)
        self.assertEqual(h.builds(2)['events'], {}, 'nothing of the failed event was recorded as built')

    def test_a_remembered_backoff_survives_a_restart_and_retry_now_overrides_it(self):
        h = self.h
        h.set('fail_at', 1)
        h.sup().cycle(T0)                                              # next-season
        hl = h.sup().cycle(T0 + 1)                                     # a fresh supervisor object: event 1 fails
        self.assertEqual(hl['builder']['consecutive_failures'], 1)
        n = len(h.calls())
        hl = h.sup().cycle(T0 + 10)                                    # another new process: still inside the backoff
        self.assertEqual(len(h.calls()), n)
        self.assertEqual(hl['builder']['consecutive_failures'], 1)
        hl = h.sup(retry_now=True).cycle(T0 + 11)                      # a person fixed nothing but asked for a retry: it tries and fails again
        self.assertEqual(len(h.calls()), n + 2)
        self.assertEqual(hl['builder']['consecutive_failures'], 2)

    def test_a_failed_next_season_is_reported(self):
        h = self.h
        h.set('fail_next_season')
        hl = h.sup().cycle(T0)
        self.assertEqual(hl['builder']['state'], 'failed')
        self.assertIn('next-season', hl['builder']['last_error'] + hl['builder']['last_action'])
        self.assertEqual(h.seasons(), [])


# ---------------------------------------------------------------------------------------------------------------------------------------- never two builders, safe restart
def cli(h, *args, public=True, extra=()):
    a = [PY, SUP] + list(args) + ['--root', h.root] + (['--public', h.public] if public else [])
    a += ['--builder-cmd', json.dumps([PY, FAKE, '--ctl', h.ctl]), '--floor-gb', '0', '--step-margin-gb', '0', '--season-gb', '0', '--inode-floor', '0', '--season-inodes', '0']
    return a + list(extra)


def reap(p):
    if p.poll() is None:
        p.kill()
        p.wait()
    for f in (p.stdout, p.stderr):
        if f:
            f.close()


def wait_for(fn, what, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError('timed out waiting for ' + what)


class Duplicates(Base):
    def spawn(self, *args, **kw):
        p = subprocess.Popen(cli(self.h, *args, **kw), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(reap, p)
        return p

    def pid_of_health(self):
        try:
            return self.h.health()['pid']
        except (OSError, ValueError, KeyError):
            return None

    def test_a_second_supervisor_is_refused_and_changes_nothing(self):
        h = self.h
        h.set('delay', 1.0)
        a = self.spawn('run', extra=['--interval', '0.2'])
        wait_for(lambda: h.calls(), 'the first supervisor to start a step')
        raw = Path(h.health_path).read_bytes()
        calls = len(h.calls())
        b = subprocess.run(cli(h, 'once'), capture_output=True, text=True)
        self.assertEqual(b.returncode, 75, b.stdout + b.stderr)
        self.assertIn('busy', (b.stdout + b.stderr).lower())
        self.assertEqual(self.pid_of_health(), a.pid, 'the refused one did not take over the health file')
        self.assertLessEqual(len(h.calls()), calls + 2, 'and started no builder of its own')
        pids = {l[1] for l in h.calls()}
        self.assertEqual(len(pids), 1, 'one builder process at a time: %s' % sorted(pids))
        a.send_signal(signal.SIGTERM)
        a.wait(timeout=30)

    def test_a_builder_held_by_someone_else_is_busy_not_a_failure(self):
        h = self.h
        sup = h.sup()
        with RL.hold(h.root, 'a recovery set'):
            hl = sup.cycle(T0)
            self.assertEqual(hl['builder']['state'], 'busy')
            self.assertEqual(hl['builder']['consecutive_failures'], 0)
            self.assertEqual(hl['problems'], [])
            hl = sup.cycle(T0 + 901)
            self.assertIn('builder_busy_long', [p['code'] for p in hl['problems']])
            self.assertEqual(hl['builder']['consecutive_failures'], 0)
        hl, _ = self.run_until(sup, 'ahead', now=T0 + 1000)
        self.assertEqual(hl['problems'], [])


class Restart(Base):
    def spawn(self, *args, **kw):
        p = subprocess.Popen(cli(self.h, *args, **kw), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(reap, p)
        return p

    def test_a_killed_supervisor_is_replaced_and_every_event_is_built_once_and_never_concurrently(self):
        h = self.h
        h.set('delay', 0.6)
        a = self.spawn('run', extra=['--interval', '0.2'])
        wait_for(lambda: [l for l in h.calls() if l[0] == 'start'], 'a step in flight')
        a.kill()
        a.wait()
        b = self.spawn('run', extra=['--interval', '0.2'])
        wait_for(lambda: Path(h.health_path).exists() and h.health()['pid'] == b.pid and h.health()['builder']['state'] == 'ahead', 'the replacement to finish the season', timeout=90)
        self.assertEqual(sorted(int(k) for k in h.builds(2)['events']), [5, 6, 7, 8])
        ev = [(float(l[4]), l[0], l[1]) for l in h.calls()]
        open_ = {}
        for t, verb, pid in sorted(ev):
            if verb == 'start':
                self.assertEqual(open_, {}, 'two builders ran at once')
                open_[pid] = t
            else:
                open_.pop(pid, None)
        b.send_signal(signal.SIGTERM)
        b.wait(timeout=30)

    def test_sigterm_stops_the_supervisor_and_its_builder_and_says_so(self):
        h = self.h
        h.set('delay', 5)
        a = self.spawn('run', extra=['--interval', '0.2'])
        wait_for(lambda: [l for l in h.calls() if l[0] == 'start'], 'a step in flight')
        a.send_signal(signal.SIGTERM)
        self.assertEqual(a.wait(timeout=20), 0)
        hl = h.health()
        self.assertEqual(hl['state'], 'stopped')
        child = [l for l in h.calls() if l[0] == 'start'][0][1]
        time.sleep(0.5)
        stat = Path('/proc/%s/stat' % child)
        self.assertTrue(not stat.exists() or stat.read_text().split(')')[-1].split()[0] == 'Z', 'the builder step was stopped with it')
        done = h.builds(2)
        self.assertEqual((done or {'events': {}})['events'], {}, 'and nothing half-built was recorded')


# ---------------------------------------------------------------------------------------------------------------------------------------- capacity
class Capacity(Base):
    def test_not_enough_disk_builds_nothing_and_changes_nothing(self):
        h = self.h
        before, pub = h.store_tree(), h.tree()
        sup = h.sup(capacity_fn=small(0.2))                          # floor 1 GB in these tests
        for i in range(3):
            hl = sup.cycle(T0 + i)
        self.assertEqual(hl['builder']['state'], 'capacity_wait')
        self.assertEqual(h.calls(), [], 'no builder was started')
        self.assertEqual(h.store_tree(), before)
        self.assertEqual(h.tree(), pub)
        self.assertEqual(hl['builder']['consecutive_failures'], 0, 'not a failure')
        pr = {p['code']: p for p in hl['problems']}
        self.assertEqual(pr['capacity_insufficient']['severity'], 'critical')
        self.assertEqual(hl['status'], 'critical')
        self.assertFalse(hl['capacity']['ok'])
        self.assertIn('disk', pr['capacity_insufficient']['detail'])
        sup2 = h.sup(capacity_fn=PLENTY)
        hl, _ = self.run_until(sup2, 'ahead', now=T0 + 10)
        self.assertEqual([p for p in hl['problems'] if p['code'].startswith('capacity')], [])

    def test_not_enough_inodes_builds_nothing(self):
        h = self.h
        hl = h.sup(capacity_fn=small(100, inodes=500)).cycle(T0)
        self.assertEqual(hl['builder']['state'], 'capacity_wait')
        self.assertEqual(h.calls(), [])
        self.assertIn('inode', {p['code']: p for p in hl['problems']}['capacity_insufficient']['detail'])

    def test_a_whole_season_must_fit_before_the_next_one_is_prepared(self):
        h = self.h
        sup = h.sup(capacity_fn=small(1.3))                          # floor 1 + step 0.1 fits; floor + season 0.5 + step does not
        hl = sup.cycle(T0)
        self.assertEqual(hl['builder']['state'], 'capacity_wait')
        self.assertEqual(h.seasons(), [])
        self.assertIn('season', hl['problems'][0]['detail'])
        hl = h.sup(capacity_fn=small(1.7)).cycle(T0 + 1)
        self.assertEqual(h.seasons(), ['s002'])
        self.assertNotEqual(hl['builder']['state'], 'capacity_wait')

    def test_capacity_that_is_enough_but_tight_warns_and_builds(self):
        h = self.h
        sup = h.sup(capacity_fn=small(1.7))
        hl = sup.cycle(T0)
        self.assertEqual(hl['builder']['state'], 'building' if h.seasons() else hl['builder']['state'])
        hl, _ = self.run_until(sup, 'ahead', now=T0 + 1)
        pr = {p['code']: p for p in hl['problems']}
        self.assertEqual(pr['capacity_low']['severity'], 'warning')
        self.assertEqual(hl['status'], 'warning')

    def test_a_disk_that_fills_during_a_step_is_capacity_not_failure(self):
        h = self.h
        sup = h.sup()
        sup.cycle(T0)                                                  # next-season
        h.set('enospc')
        hl = sup.cycle(T0 + 1)
        self.assertEqual(hl['builder']['state'], 'capacity_wait')
        self.assertEqual(hl['builder']['consecutive_failures'], 0)
        self.assertIn('capacity_insufficient', [p['code'] for p in hl['problems']])
        self.assertEqual(h.builds(2)['events'], {})
        h.clear('enospc')
        hl, _ = self.run_until(sup, 'ahead', now=T0 + 1000)
        self.assertEqual(sorted(int(k) for k in h.builds(2)['events']), [5, 6, 7, 8])

    def test_a_low_public_volume_is_reported_even_when_building_is_fine(self):
        h = self.h
        pub = os.path.realpath(h.public)

        def probe(path):
            return {'free_bytes': 0.2 * GB, 'free_inodes': 10 ** 8, 'total_inodes': 10 ** 8} if os.path.realpath(path) == pub else PLENTY(path)
        sup = h.sup(capacity_fn=probe)
        sup._same_volume = lambda x, y: False                           # as if the public folder were on its own volume
        hl = sup.cycle(T0)
        pr = {p['code']: p for p in hl['problems']}
        self.assertEqual(pr['public_capacity_low']['severity'], 'critical')
        self.assertNotEqual(hl['builder']['state'], 'capacity_wait', 'the builder volume is fine: building goes on')

    def test_the_real_probe_reads_this_machine(self):
        c = SV.capacity(self.tmpd.name)
        self.assertGreater(c['free_bytes'], 0)
        self.assertIsInstance(c['free_inodes'], int)

    def test_the_cli_refuses_to_build_below_an_absurd_floor(self):
        h = self.h
        before = h.store_tree()
        p = subprocess.run([PY, SUP, 'once', '--root', h.root, '--public', h.public, '--builder-cmd', json.dumps([PY, FAKE, '--ctl', h.ctl]), '--floor-gb', '1000000000'], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        hl = json.loads(p.stdout)
        self.assertEqual(hl['builder']['state'], 'capacity_wait')
        self.assertEqual(h.store_tree(), before)


# ---------------------------------------------------------------------------------------------------------------------------------------- health
class Health(Base):
    def clock(self, state, lease_until, pos=100.0):
        B.put(os.path.join(self.h.store, 'clock.json'), {'format': 'dfl-broadcast-clock', 'state': state, 'anchor_wall': T0, 'anchor_pos': pos, 'lease_until': lease_until, 'epoch': 1})

    def status(self, checked_at, ok=True):
        from dflsite import status as ST
        ST.write_atomic(os.path.join(self.h.public, 'status.json'), {'format': ST.FORMAT, 'format_version': 1, 'checked_at': ST.now_iso(checked_at), 'export': {'ok': ok}, 'runner': {'state': 'running'}})

    def test_publication_that_stops_is_reported_critical(self):
        h = self.h
        sup = h.sup()
        self.run_until(sup, 'ahead', now=T0)
        self.clock('running', lease_until=T0 + 90)
        self.status(T0 + 5)
        hl = sup.cycle(T0 + 20)
        self.assertNotIn('publication_stalled', [p['code'] for p in hl['problems']])
        hl = sup.cycle(T0 + 400)                                      # the lease ran out and the public heartbeat is old
        pr = {p['code']: p for p in hl['problems']}
        self.assertEqual(pr['publication_stalled']['severity'], 'critical')
        self.assertEqual(hl['status'], 'critical')
        self.assertEqual(hl['broadcast']['clock_state'], 'running')

    def test_an_old_public_heartbeat_alone_is_a_stall_and_a_pause_is_not(self):
        h = self.h
        sup = h.sup()
        self.run_until(sup, 'ahead', now=T0)
        self.clock('running', lease_until=T0 + 10 ** 6)
        self.status(T0 - 1000)
        self.assertIn('publication_stalled', [p['code'] for p in sup.cycle(T0 + 50)['problems']])
        self.clock('paused', lease_until=T0 - 5)
        codes = [p['code'] for p in sup.cycle(T0 + 60)['problems']]
        self.assertNotIn('publication_stalled', codes)
        self.assertIn('broadcast_paused', codes)

    def test_a_failed_export_is_reported(self):
        h = self.h
        sup = h.sup()
        self.run_until(sup, 'ahead', now=T0)
        self.clock('running', lease_until=T0 + 10 ** 6)
        self.status(T0 + 5, ok=False)
        self.assertIn('publication_error', [p['code'] for p in sup.cycle(T0 + 10)['problems']])

    def test_a_store_that_is_not_continuous_is_flagged(self):
        h = self.h
        os.unlink(os.path.join(h.store, S.CONTINUOUS))
        hl = h.sup().cycle(T0)
        self.assertIn('store_not_continuous', [p['code'] for p in hl['problems']])

    def test_the_health_file_is_valid_atomic_and_complete(self):
        h = self.h
        sup = h.sup()
        hl = sup.cycle(T0)
        doc = h.health()
        self.assertEqual(doc['format'], 'dfl-supervisor-health')
        self.assertEqual(doc['format_version'], 1)
        for k in ('status', 'state', 'checked_at', 'checked_at_iso', 'pid', 'started_at', 'cycle', 'problems', 'builder', 'capacity', 'broadcast', 'seasons'):
            self.assertIn(k, doc)
        for k in ('state', 'season', 'built', 'total', 'consecutive_failures', 'last_success_at', 'last_error', 'next_attempt_at'):
            self.assertIn(k, doc['builder'])
        for k in ('free_bytes', 'free_inodes', 'floor_bytes', 'ok'):
            self.assertIn(k, doc['capacity'])
        self.assertEqual(doc['status'], 'ok')
        leftovers = [n for n in os.listdir(os.path.dirname(h.health_path)) if n.startswith('.tmp')]
        self.assertEqual(leftovers, [])
        self.assertEqual(hl['cycle'], 1)

    def test_the_check_command_maps_health_to_exit_codes(self):
        h = self.h
        sup = h.sup()
        sup.cycle(T0)
        now = T0 + 1
        for status, want in (('ok', 0), ('warning', 1), ('critical', 2)):
            doc = h.health()
            doc['status'], doc['checked_at'] = status, now
            Path(h.health_path).write_text(json.dumps(doc))
            self.assertEqual(SV.check(h.health_path, now + 5, 180)[0], want, status)
        self.assertEqual(SV.check(h.health_path, now + 500, 180)[0], 3, 'a health file that stopped being written is unknown, never ok')
        self.assertEqual(SV.check(os.path.join(h.root, 'nope.json'), now, 180)[0], 3)
        doc = h.health()
        doc['state'] = 'stopped'
        doc['checked_at'] = now
        doc['status'] = 'ok'
        Path(h.health_path).write_text(json.dumps(doc))
        self.assertEqual(SV.check(h.health_path, now + 1, 180)[0], 2, 'a stopped supervisor is not healthy')

    def test_the_check_command_line_exits_with_the_code(self):
        h = self.h
        h.sup().cycle(time.time())
        p = subprocess.run([PY, SUP, 'check', '--root', h.root], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(json.loads(p.stdout)['status'], 'ok')


# ---------------------------------------------------------------------------------------------------------------------------------------- it deletes nothing
class NoDeletion(Base):
    BANNED = {'remove', 'unlink', 'rmtree', 'rmdir', 'removedirs', 'rename', 'renames', 'move', 'truncate', 'ftruncate'}

    def test_the_supervisor_source_calls_nothing_that_deletes_or_moves(self):
        src = Path(SUP).read_text()
        tree = ast.parse(src)
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
                if name in self.BANNED:
                    found.append((name, node.lineno))
        self.assertEqual(found, [], 'the supervisor must not delete, move or truncate anything (os.replace of its own temporary health file is the only rename)')

    def test_a_whole_unattended_stretch_only_adds_files(self):
        h = self.h
        before = h.store_tree()
        pub = h.tree()
        sup = h.sup()
        h.set('fail_at', 3)
        for i in range(8):
            sup.cycle(T0 + i * 200)
        h.clear('fail_at')
        for i in range(12):
            sup.cycle(T0 + 2000 + i * 200)
        after = h.store_tree()
        for k, v in before.items():
            self.assertIn(k, after)
            if not k.endswith(('plan.json', 'builds.json')) or 'seasons' in k:
                self.assertEqual(after[k], v, k)
        self.assertEqual(h.tree(), pub, 'the public folder is never written by the supervisor')


if __name__ == '__main__':
    unittest.main()


# ---------------------------------------------------------------------------------------------------------------------------------------- an interrupted `sNNN.partial`
class Partial(Base):
    """A process killed while `next-season` was creating the season leaves `sNNN.partial` behind (the folder is renamed into place only when both files are complete).
    The restart must (1) not mistake it for a season, (2) keep it as evidence, never delete or overwrite it, and (3) not be blocked by it, however it was cut off."""

    MODES = ('before', 'plan', 'torn', 'builds')

    def leftover(self):
        d = os.path.join(self.h.store, 'seasons')
        return sorted(n for n in os.listdir(d) if '.partial' in n) if os.path.isdir(d) else []

    def snapshot(self, name):
        base = os.path.join(self.h.store, 'seasons', name)
        out = {}
        for d, _, names in os.walk(base):
            for n in names:
                out[os.path.relpath(os.path.join(d, n), base)] = Path(d, n).read_bytes()
        return out

    def crash(self, sup, mode, now=T0):
        self.h.set('crash_next_season', mode)
        hl = sup.cycle(now)
        self.assertEqual(hl['builder']['state'], 'failed', hl['builder'])
        return hl

    def test_every_interruption_point_recovers_and_keeps_the_leftover_as_evidence(self):
        for mode in self.MODES:
            with self.subTest(mode=mode):
                with tempfile.TemporaryDirectory() as tmp:
                    h = Harness(tmp)
                    self.h = h
                    sup = h.sup()
                    self.crash(sup, mode)
                    self.assertEqual(h.seasons(), [], 'the leftover is not a season')
                    self.assertEqual(self.leftover(), ['s002.partial'])
                    evidence = self.snapshot('s002.partial')
                    hl, _ = self.run_until(sup, 'ahead', now=T0 + 1000, limit=30, step=100)
                    self.assertEqual(h.seasons(), ['s002'])
                    self.assertEqual(sorted(int(k) for k in h.builds(2)['events']), [5, 6, 7, 8])
                    kept = [n for n in self.leftover() if '.interrupted-' in n]
                    self.assertEqual(len(kept), 1, self.leftover())
                    self.assertEqual(self.snapshot(kept[0]), evidence, 'the evidence is byte for byte what the crash left')
                    self.assertNotIn('s002.partial', self.leftover(), 'the working name is free again')
                    self.assertEqual(hl['seasons']['interrupted_evidence'], kept)
                    self.assertEqual(hl['status'], 'ok', hl['problems'])

    def test_two_interruptions_keep_two_pieces_of_evidence(self):
        h = self.h
        sup = h.sup()
        self.crash(sup, 'plan')
        first = self.snapshot('s002.partial')
        self.crash(sup, 'torn', now=T0 + 1000)
        second_name = [n for n in self.leftover() if n == 's002.partial']
        self.assertEqual(second_name, ['s002.partial'])
        self.run_until(sup, 'ahead', now=T0 + 5000, limit=30, step=100)
        kept = [n for n in self.leftover() if '.interrupted-' in n]
        self.assertEqual(len(kept), 2, kept)
        self.assertIn(first, [self.snapshot(n) for n in kept])

    def test_a_leftover_that_is_a_file_does_not_block_the_next_season(self):
        h = self.h
        os.makedirs(os.path.join(h.store, 'seasons'), exist_ok=True)
        Path(h.store, 'seasons', 's002.partial').write_bytes(b'not a folder')
        sup = h.sup()
        self.run_until(sup, 'ahead', limit=30)
        self.assertEqual(h.seasons(), ['s002'])
        kept = [n for n in self.leftover() if '.interrupted-' in n]
        self.assertEqual(len(kept), 1)
        self.assertEqual(Path(h.store, 'seasons', kept[0]).read_bytes(), b'not a folder')

    def test_the_daemon_never_starts_a_season_from_a_complete_looking_partial(self):
        h = self.h
        sup = h.sup()
        self.crash(sup, 'builds')                                  # both files written; the folder was never renamed into place
        self.assertEqual(sorted(os.listdir(os.path.join(h.store, 'seasons', 's002.partial'))), ['builds.json', 'plan.json'])
        bc = h.daemon(pos=h.y1.plan['total_s'] - 30)
        for t in range(400):
            bc.tick(T0 + t)
        self.assertFalse(os.path.exists(os.path.join(h.store, S.CARRY)), 'no hand-over to a season that was never completed')
        st = bc.clock.state()
        self.assertEqual(round(bc.clock.position(T0 + 399), 1), float(h.y1.plan['total_s']), 'the clock holds at the end of the season on air')

    def test_the_supervisor_never_writes_into_or_removes_a_leftover_itself(self):
        h = self.h
        sup = h.sup()
        self.crash(sup, 'plan')
        before = self.snapshot('s002.partial')
        for i in range(3):
            sup.cycle(T0 + 1 + i, act=False)                       # observation only: the leftover is untouched
        self.assertEqual(self.snapshot('s002.partial'), before)
