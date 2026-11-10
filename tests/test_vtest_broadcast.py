"""The broadcast-performance judges of the validation harness (tools/vtest/broadcast_probe.py, docs/MEMORY_MONITOR_VALIDATION_CRITERIA.md section 5).

They are tested against the REAL daemon code (`SeasonBroadcaster`) in virtual time on the fixture the harness installs (tests/test_season.py `Rig`, slot 7200): a healthy daemon must pass, and each way a daemon can fail
viewers (a hung tick, an outage longer than the lease, a late or missing segment, a probe that missed the start, a paused clock) must be caught by the check that is meant to catch it.
NOT tested here: how the real machine behaves under load. That is what the run is for."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tools' / 'vtest'), str(ROOT / 'tools'), str(ROOT / 'src'), str(ROOT / 'tests')]
import broadcast_probe as BP            # noqa: E402
import common as C                      # noqa: E402
import stage_v1                         # noqa: E402
from dflsite import broadcast as B      # noqa: E402
from test_season import Rig             # noqa: E402

T0 = 1_000_000.0


def stamp_new_files(public, t, seen):
    """Real file times are wall time; the daemon here runs in virtual time. Give every file the virtual second it appeared in."""
    for d, _, names in os.walk(os.path.join(public, 'broadcast', 'live')):
        for n in names:
            p = os.path.join(d, n)
            if p not in seen:
                seen.add(p)
                os.utime(p, (t, t))


class Day:
    """A fixture store, the real daemon, and a once-a-second probe, all in virtual time."""

    def __init__(self, tmp):
        B.FSYNC = False
        self.rig = Rig(tmp, slot=7200)
        self.bc = self.rig.daemon()
        self.bc.clock.start(T0, 0.0, B.LEASE_S, ready_until=0.0)
        self.samples, self.seen, self.last_tick = [], set(), T0
        self.window = BP.first_window(json.load(open(os.path.join(self.rig.store, 'plan.json'))), json.load(open(os.path.join(self.rig.store, 'builds.json'))), B.DELAY_S, B.SEG_S)

    def second(self, t, tick=True, paused=False):
        if tick:
            self.bc.tick(t)
            self.last_tick = t
            stamp_new_files(self.rig.public, t, self.seen)
        self.samples.append({'t': t, 'state': json.loads(json.dumps(self.bc.clock.state())), 'mtime': self.last_tick, 'paused_file': paused})

    def run(self, until_pos=None, dead=(), start=T0):
        until_pos = until_pos or self.window['end_pos']
        t = start
        while B.position(self.bc.clock.state(), t) < until_pos or t < T0 + 30:
            self.second(t, tick=not (dead and dead[0] <= t < dead[1]))
            t += 1
            if t > T0 + 4000:
                raise AssertionError('the clock never reached the end of the window')
        return t

    def log(self):
        return C.jsonl_read(os.path.join(self.rig.store, 'clock_log.jsonl'))

    def live(self):
        return BP.scan_live(self.rig.public)

    def rows(self, samples=None, live=None, log=None, t1=None, journal=''):
        t1 = t1 or self.samples[-1]['t']
        r = stage_v1.judge_publish_phase(B, samples or self.samples, log if log is not None else self.log(), live or self.live(), self.window, T0, t1, True, None, [], journal)
        return {x[0]: x for x in r}


class Healthy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(setattr, B, 'FSYNC', True)

    def day(self):
        return Day(self.tmp.name)

    def test_the_first_window_is_found_from_the_installed_plan(self):
        w = self.day().window
        self.assertEqual((w['start_s'], w['next_start_s'], sorted(w['segments'].values())), (200, 8300, [134, 134]))
        self.assertAlmostEqual(w['end_pos'], 200 + 670.45 + B.DELAY_S + B.SEG_S, places=1)

    def test_a_healthy_daemon_passes_every_broadcast_check(self):
        d = self.day()
        d.run()
        rows = d.rows()
        self.assertEqual({k: v[1] for k, v in rows.items()}, {'V1.publish.window_reached': 'pass', 'V1.publish.no_trip': 'pass', 'V1.publish.clock_stall': 'info', 'V1.publish.lease': 'pass',
                                                              'V1.publish.deadline': 'pass', 'V1.publish.errors': 'pass'})
        data = rows['V1.publish.deadline'][3]
        self.assertEqual((data['segments_checked'], data['late_count'], data['missing_count']), (268, 0, 0), 'every segment of both games was judged against its own deadline')
        self.assertLessEqual(rows['V1.publish.clock_stall'][3]['last_tick_age_s']['max'], 1.0)

    def test_a_hung_daemon_stalls_the_clock_but_the_guard_keeps_the_viewers_covered(self):
        d = self.day()
        d.run(dead=(T0 + 400, T0 + 412))
        rows = d.rows()
        self.assertEqual(rows['V1.publish.clock_stall'][1], 'info', 'a stand-still is reported, it does not decide the stage')
        self.assertFalse(rows['V1.publish.clock_stall'][4])
        self.assertGreater(rows['V1.publish.clock_stall'][3]['stalled_samples'], 0)
        self.assertEqual(rows['V1.publish.lease'][1], 'pass', '12 s is far inside the lease')
        self.assertGreater(rows['V1.publish.lease'][3]['logged_standstills'], 0, 'the daemon logged the stand-still; it is reported, not a lease lapse')
        self.assertEqual(rows['V1.publish.deadline'][1], 'pass', 'the clock stood still instead of outrunning the files: no viewer underrun')

    def test_a_logged_publish_failure_fails_the_lease_check_even_if_the_lease_held(self):
        d = self.day()
        d.run()
        log = d.log() + [{'at': T0 + 100, 'why': 'publish_failure', 'down_s': 4.0}]
        self.assertEqual(d.rows(log=log)['V1.publish.lease'][1], 'fail')

    def test_an_outage_longer_than_the_lease_is_seen_in_the_samples_and_fails_the_lease_check(self):
        d = self.day()
        d.run(dead=(T0 + 300, T0 + 300 + B.LEASE_S + 20))
        rows = d.rows()
        self.assertEqual(rows['V1.publish.lease'][1], 'fail')
        self.assertTrue(any(e.get('why') == 'downtime' for e in d.log()), 'the daemon recorded the gap itself')
        self.assertEqual(rows['V1.publish.clock_stall'][1], 'info')

    def test_a_segment_published_after_viewers_need_it_fails_and_so_does_a_missing_one(self):
        d = self.day()
        d.run()
        live = d.live()
        name = sorted(live)[0]
        late = json.loads(json.dumps({k: {'manifest': v['manifest'], 'segments': {str(n): t for n, t in v['segments'].items()}} for k, v in live.items()}))
        for v in late.values():
            v['segments'] = {int(n): t for n, t in v['segments'].items()}
        late[name]['segments'][40] += B.DELAY_S + 2.0                      # released at its position, but 5 s late in wall time
        r = d.rows(live=late)
        self.assertEqual(r['V1.publish.deadline'][1], 'fail')
        self.assertEqual(r['V1.publish.deadline'][3]['late_count'], 1)
        del late[name]['segments'][41]
        r = d.rows(live=late)
        self.assertEqual((r['V1.publish.deadline'][1], r['V1.publish.deadline'][3]['missing_count']), ('fail', 1))

    def test_a_game_that_never_reached_its_end_is_not_a_pass(self):
        d = self.day()
        d.run(until_pos=500)
        r = d.rows()
        self.assertIn(r['V1.publish.deadline'][1], ('fail',), r['V1.publish.deadline'][2])      # segments past the clock's position were never due, but the window is not final

    def test_a_probe_that_started_after_the_window_began_is_invalid_not_a_pass(self):
        d = self.day()
        d.run()
        late = [s for s in d.samples if B.position(s['state'], s['t']) > 250]
        self.assertEqual(d.rows(samples=late)['V1.publish.deadline'][1], 'invalid')

    def test_a_paused_clock_is_not_a_performance_test(self):
        d = self.day()
        d.run()
        paused = [dict(s, paused_file=True) for s in d.samples]
        self.assertEqual(d.rows(samples=paused)['V1.publish.clock_stall'][1], 'invalid')

    def test_unreadable_clock_samples_are_invalid(self):
        d = self.day()
        d.run()
        blind = [dict(s, state=None) for s in d.samples]
        self.assertEqual(d.rows(samples=blind)['V1.publish.clock_stall'][1], 'invalid')

    def test_healthy_tick_gaps_up_to_the_publication_horizon_are_not_even_a_stand_still(self):
        d = self.day()
        t, gap = T0, 0
        while B.position(d.bc.clock.state(), t) < d.window['end_pos'] or t < T0 + 30:
            gap = (gap + 1) % 4                                     # two ticks, then two skipped seconds: a 3 s gap between ticks, the whole of DELAY_S
            d.second(t, tick=gap not in (2, 3))
            t += 1
        rows = d.rows()
        self.assertEqual(rows['V1.publish.clock_stall'][3]['stalled_samples'], 0)
        self.assertEqual({k: v[1] for k, v in rows.items() if k != 'V1.publish.clock_stall'}, {k: 'pass' for k in rows if k != 'V1.publish.clock_stall'})

    def test_error_lines_from_the_daemon_fail_and_plain_log_lines_do_not(self):
        ok = BP.error_row('V1.x', 'Nov 09 daemon started\n{"recovered": true, "at": 1}\n')
        bad = BP.error_row('V1.x', '{"error": "OSError", "detail": "No space left", "at": 5}\n')
        self.assertEqual((ok[1], bad[1]), ('pass', 'fail'))
        self.assertEqual(bad[3]['errors'][0]['error'], 'OSError')

    def test_the_wall_time_a_position_was_reached_is_found_from_the_clocks_own_state(self):
        d = self.day()
        d.run(until_pos=300)
        w = BP.wall_at(B, d.samples, 250.0)
        self.assertAlmostEqual(w - T0, 250.0, delta=1.0)
        self.assertIsNone(BP.wall_at(B, d.samples, 10 ** 6))


class StressPhase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(setattr, B, 'FSYNC', True)

    def test_cache_stress_is_judged_only_if_nothing_was_being_published_and_the_deadline_row_says_not_run(self):
        d = Day(self.tmp.name)
        end = d.run()
        t0, t1 = end + 5, end + 105
        for t in range(int(end), int(t1)):
            d.second(float(t))
        rows = {r[0]: r for r in stage_v1.judge_stress_phase(B, d.samples, d.log(), d.live(), d.window, t0, t1, 1000.0, '')}
        self.assertEqual({k: v[1] for k, v in rows.items()}, {'V1.cache.clock_stall': 'info', 'V1.cache.lease': 'pass', 'V1.separation': 'pass', 'V1.cache.deadline': 'not_run', 'V1.cache.errors': 'pass'})
        self.assertFalse(rows['V1.cache.deadline'][4], 'a not_run row is explicit and never counted as a pass')
        overlapped = stage_v1.judge_stress_phase(B, d.samples, d.log(), d.live(), d.window, T0, t1, 1000.0, '')
        self.assertEqual({r[0]: r[1] for r in overlapped}['V1.separation'], 'invalid', 'segments were written inside this window: it no longer isolates the cache effect')
        past = stage_v1.judge_stress_phase(B, d.samples, d.log(), d.live(), d.window, t0, t1, 9000.0, '')
        self.assertEqual({r[0]: r[1] for r in past}['V1.separation'], 'invalid', 'the clock reached the next window')

    def test_only_the_stand_still_diagnostic_is_not_required_in_the_publication_phase(self):
        d = Day(self.tmp.name)
        d.run()
        rows = stage_v1.judge_publish_phase(B, d.samples, d.log(), d.live(), d.window, T0, d.samples[-1]['t'], True, None, [], '')
        self.assertEqual([r[0] for r in rows if len(r) == 5 and not r[4]], ['V1.publish.clock_stall'])


if __name__ == '__main__':
    unittest.main()
