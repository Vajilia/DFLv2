"""The monitor's memory rule (tools/rehearsal_check.py: read_cgroup_memory, MemoryJudge, Watch.memory_check).

Why it exists. The first rehearsal was stopped at 55,743 s by `memory_high`: "daemon memory 578 MB of a 629 MB limit". The old rule tripped when memory.current (which counts the kernel's
file cache) passed 90 % of memory.max, on ONE sample. The kernel never reclaimed under pressure (memory.events: high 0, max 0, oom 0, oom_kill 0; memory.pressure total 0): the group
was full of cache that the kernel gives back when it needs it. A long run that writes tens of thousands of files fills the cache to the limit, so the old rule would stop any long run.

Required here:
  * cache growth that the kernel can reclaim never trips, however close to MemoryMax memory.current gets
  * one high sample, or a short excursion, never trips; the SAME high number held for the sustain window does
  * memory the kernel cannot give back counts in full: anonymous, dirty and writeback pages, mapped files, shared memory, unreclaimable kernel memory. Only CLEAN unmapped file
    cache and RECLAIMABLE slab are discounted; the discount is never "all file cache"
  * the kernel's own OOM verdict (memory.events oom / oom_kill) trips at once; `max` and `high` events alone (the kernel reclaiming) do not
  * every trip's evidence carries the breakdown, memory.events, the pressure files and the recent samples, so the next unknown cannot happen
  * the parser reads the real files of the Droplet (tests/fixtures/cgroup_v2_droplet_2026-11-08/)
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'tests'))
import rehearsal_check as RC                        # noqa: E402

FIXTURE = ROOT / 'tests' / 'fixtures' / 'cgroup_v2_droplet_2026-11-08'
MAX = 629145600                                     # the rehearsal daemon's MemoryMax (600M)
MB = 1_000_000


def sample(anon=20 * MB, file=0, dirty=0, wb=0, mapped=0, shmem=0, kernel=0, slab_r=0, slab_u=0, events=None, mx=MAX, pressure=None):
    """A cgroup-v2 style sample. memory.current is what the kernel reports: anon + file + kernel. `file` includes dirty, writeback, mapped and shmem (as in memory.stat)."""
    s = dict(version=2, max=mx, stat_ok=True, anon=anon, file=file + dirty + wb + mapped + shmem, file_dirty=dirty, file_writeback=wb, file_mapped=mapped, shmem=shmem,
             kernel=kernel + slab_r + slab_u, slab_reclaimable=slab_r, slab_unreclaimable=slab_u, unevictable=0,
             events=dict(low=0, high=0, max=0, oom=0, oom_kill=0), pressure=pressure)
    s['current'] = s['anon'] + s['file'] + s['kernel']
    if events:
        s['events'].update(events)
    return s


def feed(judge, samples, every=10.0, t0=1000.0):
    """Run samples through the judge one per `every` seconds; -> (index of the first trip or None, its verdict)."""
    for i, s in enumerate(samples):
        v = judge.observe(s, t0 + i * every)
        if v:
            return i, v
    return None, None


class Reader(unittest.TestCase):
    def test_it_reads_the_real_files_of_the_droplet(self):
        s = RC.read_cgroup_memory(str(FIXTURE))
        self.assertEqual((s['version'], s['current'], s['max']), (2, 269832192, 629145600))
        self.assertEqual((s['anon'], s['file'], s['kernel']), (22396928, 202477568, 44957696))
        self.assertEqual((s['file_dirty'], s['file_writeback'], s['file_mapped'], s['shmem']), (24576, 0, 0, 0))
        self.assertEqual((s['slab_reclaimable'], s['slab_unreclaimable']), (44766456, 55104))
        self.assertEqual(s['events'], dict(low=0, high=0, max=0, oom=0, oom_kill=0))
        self.assertEqual(s['pressure']['source'], 'cgroup')
        self.assertEqual((s['pressure']['some']['total'], s['pressure']['full']['avg10']), (0, 0.0))
        self.assertEqual(s['anon'] + s['file'] + s['kernel'], s['current'], 'the kernel\'s own identity holds in the real files')

    def test_the_real_droplet_state_is_about_one_twelfth_pinned(self):
        """270 MB of 600 MiB "used", of which almost all is clean cache and reclaimable slab."""
        s = RC.read_cgroup_memory(str(FIXTURE))
        est = RC.memory_estimates(s)
        self.assertGreater(est['reclaimable'], 240 * MB)
        self.assertLess(est['pinned'], 24 * MB)
        self.assertGreaterEqual(est['pinned'], s['anon'])

    def test_missing_files_do_not_crash_and_are_said_so(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        self.assertIsNone(RC.read_cgroup_memory(d))                                   # no memory.current: not a cgroup (not systemd, or not started yet)
        open(os.path.join(d, 'memory.current'), 'w').write('300000000\n')
        open(os.path.join(d, 'memory.max'), 'w').write('600000000\n')
        s = RC.read_cgroup_memory(d)
        self.assertFalse(s['stat_ok'])
        self.assertEqual(RC.memory_estimates(s)['pinned'], 300000000, 'without memory.stat nothing is discounted: the conservative reading')

    def test_an_unlimited_group_has_no_max(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        shutil.copytree(FIXTURE, os.path.join(d, 'c'))
        open(os.path.join(d, 'c', 'memory.max'), 'w').write('max\n')
        self.assertIsNone(RC.read_cgroup_memory(os.path.join(d, 'c'))['max'])


class Rule(unittest.TestCase):
    def judge(self, **kw):
        return RC.MemoryJudge(**kw)

    # ---- healthy: the kernel can take it back -------------------------------------------------------------------------------------------------------------------------
    def test_cache_filling_the_group_is_healthy_however_close_it_gets(self):
        """The run that was stopped: current climbs to 92 % and beyond because of clean cache and reclaimable slab; nothing the kernel cannot give back grows."""
        series = [sample(anon=25 * MB, file=f * MB, slab_r=45 * MB) for f in range(150, 560, 10)]
        self.assertGreater(series[-1]['current'], 0.9 * MAX, 'the series really does pass the old rule\'s line')
        self.assertTrue(any(s['current'] > 0.9 * s['max'] for s in series))
        self.assertEqual(feed(self.judge(), series), (None, None))
        self.assertEqual(feed(self.judge(), series * 20), (None, None), 'and it stays healthy for as long as it is held')

    def test_the_kernel_reclaiming_at_the_limit_is_not_a_trip_by_itself(self):
        """`max` and `high` events are the kernel doing its job (the supervisor's own measurement saw 62,532 of them with no kill): they are recorded, they do not trip."""
        series = [sample(anon=25 * MB, file=(520 + i) * MB, slab_r=45 * MB, events=dict(high=i * 100, max=i * 500)) for i in range(30)]
        self.assertEqual(feed(self.judge(), series), (None, None))

    # ---- one sample or a short excursion --------------------------------------------------------------------------------------------------------------------------------
    def test_one_high_sample_is_not_a_trip_but_the_same_number_held_is(self):
        low, high = sample(anon=100 * MB), sample(anon=590 * MB)                      # 590 of 629 = 94 % pinned
        self.assertEqual(feed(self.judge(), [low, low, high, low, low, low]), (None, None))
        self.assertEqual(feed(self.judge(), [low, high, high, low, low, low]), (None, None), 'a 10-20 s excursion is not a trip')
        i, v = feed(self.judge(), [low] + [high] * 12)
        self.assertIsNotNone(v, 'held for two minutes it is')
        self.assertEqual(v['reason'], 'sustained_unreclaimable')

    def test_the_sustain_window_is_in_seconds_and_in_samples(self):
        high = sample(anon=590 * MB)
        j = self.judge(sustain_s=60.0, min_samples=3)
        self.assertIsNone(j.observe(high, 0.0))
        self.assertIsNone(j.observe(high, 59.0))
        v = j.observe(high, 60.0)
        self.assertIsNotNone(v, 'three samples spanning 60 s')
        j = self.judge(sustain_s=60.0, min_samples=3)
        self.assertIsNone(j.observe(high, 0.0))
        self.assertIsNone(j.observe(high, 100.0), 'two samples are not enough however far apart: a stalled monitor is not a sustained condition')
        self.assertIsNotNone(j.observe(high, 110.0))

    def test_dropping_below_the_line_resets_the_clock(self):
        high, low = sample(anon=590 * MB), sample(anon=300 * MB)
        j = self.judge()
        t = 0.0
        for _ in range(5):
            self.assertIsNone(j.observe(high, t)); t += 10
        self.assertIsNone(j.observe(low, t)); t += 10
        for _ in range(5):
            self.assertIsNone(j.observe(high, t), 'the count starts again'); t += 10

    # ---- genuinely dangerous: what the kernel cannot give back ---------------------------------------------------------------------------------------------------------------
    def test_anonymous_memory_held_near_the_limit_trips_with_the_breakdown(self):
        i, v = feed(self.judge(), [sample(anon=(580 + i) * MB, file=20 * MB) for i in range(10)])
        self.assertIsNotNone(v)
        self.assertEqual(v['reason'], 'sustained_unreclaimable')
        ev = v['evidence']
        for k in ('current', 'max', 'anon', 'file', 'file_dirty', 'file_writeback', 'file_mapped', 'shmem', 'kernel', 'slab_reclaimable', 'slab_unreclaimable', 'events', 'pressure',
                  'reclaimable_estimate', 'pinned_estimate', 'pinned_fraction', 'rule', 'recent'):
            self.assertIn(k, ev, k)
        self.assertGreater(ev['pinned_fraction'], 0.9)
        self.assertEqual(ev['rule']['sustain_s'], 60.0)
        self.assertGreaterEqual(len(ev['recent']), 3)
        self.assertIn('anon', v['detail'])
        json.dumps(v)                                                                  # it goes into the evidence file as it is

    def test_dirty_and_writeback_pages_count_but_the_same_pages_clean_do_not(self):
        base = dict(anon=300 * MB, kernel=20 * MB)
        clean = sample(file=280 * MB, **base)                                           # 600 MB of 629 used: all of it cache the kernel can drop
        self.assertEqual(feed(self.judge(), [clean] * 20), (None, None))
        dirty = sample(file=0, dirty=200 * MB, wb=80 * MB, **base)                      # the same 280 MB of file pages, not yet written: 600 MB that cannot be dropped yet
        i, v = feed(self.judge(), [dirty] * 20)
        self.assertIsNotNone(v)
        self.assertEqual((v['evidence']['file_dirty'], v['evidence']['file_writeback']), (200 * MB, 80 * MB))

    def test_mapped_files_and_shared_memory_are_not_discounted(self):
        for kw in (dict(mapped=380 * MB), dict(shmem=380 * MB)):
            i, v = feed(self.judge(), [sample(anon=200 * MB, kernel=10 * MB, **kw)] * 20)
            self.assertIsNotNone(v, kw)

    def test_slab_that_can_be_reclaimed_is_discounted_and_slab_that_cannot_is_not(self):
        self.assertEqual(feed(self.judge(), [sample(anon=100 * MB, slab_r=480 * MB)] * 20), (None, None))
        i, v = feed(self.judge(), [sample(anon=100 * MB, slab_u=480 * MB)] * 20)
        self.assertIsNotNone(v)

    def test_only_clean_unmapped_cache_is_discounted_never_all_file_cache(self):
        s = sample(anon=100 * MB, file=100 * MB, dirty=50 * MB, wb=30 * MB, mapped=20 * MB, shmem=10 * MB, slab_r=40 * MB, slab_u=10 * MB, kernel=5 * MB)
        est = RC.memory_estimates(s)
        self.assertEqual(est['reclaimable'], 100 * MB + 40 * MB, 'clean cache (100) + reclaimable slab (40); not dirty 50, writeback 30, mapped 20, shmem 10')
        self.assertEqual(est['pinned'], s['current'] - est['reclaimable'])

    def test_the_hard_boundary_is_untouched(self):
        """The rule reads memory.max as the limit and never changes it, raises it or reads anything but files."""
        src = (ROOT / 'tools' / 'rehearsal_check.py').read_text()
        self.assertNotIn('memory.max", "w"', src)
        self.assertEqual(sample(anon=590 * MB)['max'], MAX)

    # ---- the kernel's own verdict --------------------------------------------------------------------------------------------------------------------------------------
    def test_an_oom_event_trips_at_once(self):
        j = self.judge()
        self.assertIsNone(j.observe(sample(events=dict(oom=0, oom_kill=0)), 0.0))
        v = j.observe(sample(events=dict(oom=1, oom_kill=1)), 10.0)
        self.assertIsNotNone(v)
        self.assertEqual(v['reason'], 'oom_event')
        self.assertEqual(v['evidence']['events']['oom_kill'], 1)

    def test_old_oom_counts_at_monitor_start_are_not_new_events(self):
        j = self.judge()
        self.assertIsNone(j.observe(sample(events=dict(oom=3, oom_kill=3)), 0.0))
        self.assertIsNone(j.observe(sample(events=dict(oom=3, oom_kill=3)), 10.0))
        self.assertIsNotNone(j.observe(sample(events=dict(oom=3, oom_kill=4)), 20.0))

    # ---- no limit, no stat -----------------------------------------------------------------------------------------------------------------------------------------------
    def test_without_a_limit_there_is_nothing_to_compare_with(self):
        self.assertEqual(feed(self.judge(), [sample(anon=900 * MB, mx=None)] * 20), (None, None))

    def test_pressure_is_recorded_and_does_not_trip_by_itself(self):
        p = dict(source='cgroup', some=dict(avg10=40.0, avg60=30.0, avg300=10.0, total=9000000), full=dict(avg10=20.0, avg60=15.0, avg300=5.0, total=4000000))
        s = sample(anon=50 * MB, file=400 * MB, pressure=p)
        self.assertEqual(feed(self.judge(), [s] * 30), (None, None))
        j = self.judge()
        j.observe(s, 0.0)
        self.assertEqual(j.recent()[-1]['some_avg10'], 40.0)


class WatchIntegration(unittest.TestCase):
    """The monitor itself, on a cgroup folder of files: the trip, its evidence and the latch."""
    def setUp(self):
        from test_rehearsal_guard import MonitorTrips
        self.t = MonitorTrips('test_a_correct_tree_trips_nothing_and_logs_heartbeats')
        self.t.setUp()
        self.addCleanup(self.t.doCleanups)
        self.cg = os.path.join(self.t.tmp, 'cg')
        shutil.copytree(FIXTURE, self.cg)

    def put(self, **kw):
        s = sample(**kw)
        def w(n, v):
            with open(os.path.join(self.cg, n), 'w') as f:
                f.write(v)
        w('memory.current', '%d\n' % s['current']); w('memory.max', '%d\n' % s['max'])
        w('memory.stat', ''.join('%s %d\n' % kv for kv in dict(
            anon=s['anon'], file=s['file'], kernel=s['kernel'], shmem=s['shmem'], file_mapped=s['file_mapped'], file_dirty=s['file_dirty'], file_writeback=s['file_writeback'],
            slab_reclaimable=s['slab_reclaimable'], slab_unreclaimable=s['slab_unreclaimable'], unevictable=0).items()))
        w('memory.events', ''.join('%s %d\n' % kv for kv in s['events'].items()))

    def test_a_cache_full_group_trips_nothing_and_a_pinned_group_trips_after_the_window(self):
        w = self.t.watch(150, cgroup=self.cg)
        self.put(anon=25 * MB, file=540 * MB, slab_r=45 * MB)                           # 610 MB of 629: the state the first rehearsal tripped in
        for at in range(150, 400, 10):
            w.memory_check(T0 + at)
        self.assertIsNone(self.t.latch(), 'cache is not a trip')
        self.assertEqual(w.found, 0)
        self.put(anon=585 * MB, file=20 * MB, kernel=5 * MB)
        for at in range(400, 440, 10):
            w.memory_check(T0 + at)
        self.assertIsNone(self.t.latch(), '40 s is inside the window')
        for at in range(440, 480, 10):
            w.memory_check(T0 + at)
        trip = self.t.latch()
        self.assertEqual(trip['kind'], 'memory_high')
        self.assertIn('sustained', trip['detail'])
        ev = json.load(open(os.path.join(self.t.tmp, 'evidence', [d for d in os.listdir(os.path.join(self.t.tmp, 'evidence')) if d.startswith('trip-')][0], 'evidence.json')))
        self.assertGreater(ev['evidence']['anon'], 580 * MB)
        self.assertIn('recent', ev['evidence'])

    def test_the_heartbeat_line_carries_the_memory_breakdown(self):
        w = self.t.watch(150, cgroup=self.cg)
        self.put(anon=25 * MB, file=300 * MB, dirty=10 * MB, slab_r=45 * MB)
        self.t.check(w, 150)
        beat = [l for l in self.t.lines() if l.get('heartbeat')][-1]
        m = beat['mem']
        for k in ('current', 'max', 'anon', 'file', 'dirty', 'writeback', 'kernel', 'pinned'):
            self.assertIn(k, m, k)
        self.assertEqual(m['anon'], 25 * MB)

    def test_an_oom_kill_event_trips_the_broadcast(self):
        w = self.t.watch(150, cgroup=self.cg)
        self.put(anon=25 * MB)
        w.memory_check(T0 + 150)
        self.assertIsNone(self.t.latch())
        self.put(anon=25 * MB, events=dict(oom=1, oom_kill=1))
        w.memory_check(T0 + 160)
        self.assertEqual(self.t.latch()['kind'], 'memory_high')
        self.assertIn('oom', self.t.latch()['detail'])


T0 = 1_700_000_000.0
try:
    from test_season import T0                       # the rig's clock origin
except Exception:                                    # pragma: no cover
    pass

if __name__ == '__main__':
    unittest.main()
