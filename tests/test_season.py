"""Season mode (src/dflsite/season.py): the daemon over a small synthetic year built from the public playback fixture, in virtual time.

The fixture is the checked-in public replay, copied under fresh season-901 identities (as tools/build_watch_preview.py does): nothing here touches a league,
a production directory or the site. A real league's whole year is exercised by tools/broadcast_season_check.py.
"""
import copy
import json
import math
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from dflsite import broadcast as B, playback as PB, season as S, status as ST

FIX = ROOT / 'tests/fixtures/playback_v1/playback'
T0 = 1000.0


def fixture_game(gid):
    idx = json.loads((FIX / 'index.json').read_text())
    g = json.loads((FIX / idx['games'][0]['file']).read_text())
    g.update(gid=gid, id=gid.split('/', 1)[1], season=901)
    return g


class Rig:
    """A store with a three-block, two-window year. `slot` is the nominal window length (a short one forces an overrun)."""

    def __init__(self, tmp, slot=960, built=3, games=None):
        self.store, self.public = os.path.join(tmp, 'store'), os.path.join(tmp, 'public')
        self.slot = slot
        self.gids = games or ['S901/reg:w01:g01', 'S901/reg:w01:g02', 'S901/reg:w02:g01']
        self.entries, self.lengths = {}, {}
        for gid in self.gids:
            g = fixture_game(gid)
            rel, size, sha = PB.write_game(os.path.join(self.store, 'playback'), g)
            rec = B.store_game(self.store, g, slot, replay_name=os.path.basename(rel))
            self.entries[gid] = (PB.index_entry(g, rel, size, sha), rec, g)
            self.lengths[gid] = rec['length_s']
        self.D = math.ceil(max(self.lengths.values()) + B.DELAY_S)
        w0 = 200
        w0_end = w0 + slot
        self.t = {'w0': w0, 'r1': w0_end, 'w1': w0_end + 600 + 300, 'r2': w0_end + 600 + 300 + slot}
        self.plan = {'format': S.PLAN_FORMAT, 'format_version': 1, 'season': 901, 'total_s': self.t['r2'] + 600, 'params': {'slot_s': slot}, 'rounds': {}, 'round_sizes': {}, 'single': {},
                     'games': {'reg:w01:g01': 'w000', 'reg:w01:g02': 'w000', 'reg:w02:g01': 'w001'}, 'events': ['offseason_open', 'week_1', 'week_2'],
                     'items': [
                         {'kind': 'block', 'role': 'offseason', 'label': 'opening', 'start_s': 0, 'end_s': 100, 'events': ['offseason_open'], 'events_at': 100, 'parts': []},
                         {'kind': 'window', 'key': 'w000', 'label': 'week 1 window', 'start_s': w0, 'slot_s': slot, 'expected': 2, 'gated': False, 'event': None},
                         {'kind': 'block', 'role': 'recap', 'label': 'week 1 results', 'start_s': self.t['r1'], 'end_s': self.t['r1'] + 600, 'events': ['week_1'], 'events_at': 0, 'parts': []},
                         {'kind': 'window', 'key': 'w001', 'label': 'week 2 window', 'start_s': self.t['w1'], 'slot_s': slot, 'expected': 1, 'gated': False, 'event': None},
                         {'kind': 'block', 'role': 'recap', 'label': 'week 2 results', 'start_s': self.t['r2'], 'end_s': self.t['r2'] + 600, 'events': ['week_2'], 'events_at': 0, 'parts': []}]}
        B.put(os.path.join(self.store, S.PLAN), self.plan)
        self.builds = {'format': S.BUILDS_FORMAT, 'format_version': 1, 'events': {}, 'games': {}, 'honors': {}, 'league_id': 'test'}
        self.key = {'S901/reg:w01:g01': ('week_1', 2), 'S901/reg:w01:g02': ('week_1', 2), 'S901/reg:w02:g01': ('week_2', 3)}
        self.built = 0
        self.build(built)

    def build(self, upto):
        """Make events up to `upto` available to the daemon (the builder's last act is to rewrite builds.json)."""
        names = {1: 'offseason_open', 2: 'week_1', 3: 'week_2'}
        while self.built < upto:
            self.built += 1
            n = self.built
            rel = 'e%05d-%010d' % (n, n)
            os.makedirs(os.path.join(self.store, 'feed/releases', rel), exist_ok=True)
            B.put(os.path.join(self.store, 'feed/releases', rel, 'manifest.json'), {'release': rel, 'secret_marker': 'RESULT-OF-EVENT-%d' % n})
            B.put(os.path.join(self.store, 'feed/pointers/e%05d.json' % n), {'release': rel})
            self.builds['events'][str(n)] = {'key': names[n], 'release': rel, 'events': n, 'pointer': 'feed/pointers/e%05d.json' % n}
            for gid, (key, seq) in self.key.items():
                if seq == n:
                    e, rec, g = self.entries[gid]
                    self.builds['games'][gid] = {'seq': n, 'key': key, 'away': g['teams']['away']['abbr'], 'home': g['teams']['home']['abbr'], 'segments': rec['segments'],
                                                 'length_s': rec['length_s'], 'delay_s': B.DELAY_S, 'replay': rec['replay'], 'entry': e}
        B.put(os.path.join(self.store, S.BUILDS), self.builds)

    def daemon(self, **kw):
        return S.SeasonBroadcaster(self.store, self.public, **kw)

    def files(self):
        out = set()
        for d, _, names in os.walk(self.public):
            for n in names:
                out.add(os.path.relpath(os.path.join(d, n), self.public))
        return out

    def pub(self, *p):
        return os.path.join(self.public, *p)


def run(bc, a, b, step=1):
    """Tick virtual seconds a..b (inclusive)."""
    r = None
    t = a
    while t <= b:
        r = bc.tick(t)
        t += step
    return r


class SeasonMode(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        B.FSYNC = False
        self.addCleanup(setattr, B, 'FSYNC', True)

    def rig(self, **kw):
        r = Rig(self.tmp.name, **kw)
        bc = r.daemon()
        bc.clock.start(T0, 0.0, B.LEASE_S, ready_until=0.0)
        return r, bc

    def test_nothing_is_released_before_its_time_and_the_clock_waits_for_the_builder(self):
        r, bc = self.rig(built=0)
        res = run(bc, T0, T0 + 60)
        self.assertEqual(res['pos'], 0)
        self.assertTrue(res['holding'])
        self.assertEqual(sorted(r.files()), ['broadcast/clock.json', 'broadcast/replays/index.json', 'broadcast/schedule.json', 'status.json'])
        log = [json.loads(x) for x in open(os.path.join(r.store, 'clock_log.jsonl'))]
        self.assertLessEqual(len(log), 2, 'a stand-still is not a log line per second')
        r.build(1)
        res = run(bc, T0 + 61, T0 + 70)
        self.assertEqual([x['why'] for x in map(json.loads, open(os.path.join(r.store, 'clock_log.jsonl')))][-1], 'coverage_wait')
        self.assertGreater(res['pos'], 0)

    def test_a_release_appears_with_its_block_and_the_pointer_is_last(self):
        r, bc = self.rig()
        run(bc, T0, T0 + 99)
        self.assertFalse(os.path.exists(r.pub('latest.json')))
        run(bc, T0 + 100, T0 + 101)
        self.assertEqual(B.get(r.pub('latest.json')), {'release': 'e00001-0000000001'})
        self.assertTrue(os.path.isdir(r.pub('releases/e00001-0000000001')))
        self.assertFalse(os.path.exists(r.pub('releases/e00002-0000000002')))
        # nothing about later events appears anywhere in the public tree
        for rel in r.files():
            if rel.endswith('.json'):
                self.assertNotIn('RESULT-OF-EVENT-2', open(r.pub(rel)).read())

    def test_week_results_wait_for_the_whole_window_and_are_revealed_with_the_replays(self):
        r, bc = self.rig()
        run(bc, T0, T0 + r.t['r1'] - 5)
        self.assertEqual(B.get(r.pub('latest.json'))['release'], 'e00001-0000000001')
        self.assertFalse(os.path.exists(r.pub('playback/index.json')))
        run(bc, T0 + r.t['r1'] - 4, T0 + r.t['r1'] + 2)
        self.assertEqual(B.get(r.pub('latest.json'))['release'], 'e00002-0000000002')
        idx = B.get(r.pub('playback/index.json'))
        self.assertEqual(sorted(e['gid'] for e in idx['games']), ['S901/reg:w01:g01', 'S901/reg:w01:g02'])
        for e in idx['games']:
            self.assertTrue(os.path.exists(r.pub('playback', e['file'])))
        self.assertFalse(any('w02' in f for f in r.files() if f.startswith('playback/')))
        self.assertEqual(ST.read(r.pub('status.json'))['league_events'], 2)

    def test_status_follows_the_broadcast_not_the_builder_or_the_league(self):
        r, bc = self.rig()
        run(bc, T0, T0 + 20)
        doc = ST.read(r.pub('status.json'))
        self.assertEqual((doc['league_events'], doc['published_events'], doc['release']), (0, None, None))
        self.assertEqual(doc['runner']['state'], 'running')
        self.assertIsNone(doc['runner']['until_events'])
        self.assertTrue(doc['export']['ok'])
        raw = open(r.pub('status.json')).read()
        for secret in ('horizon', 'e00003', 'built'):
            self.assertNotIn(secret, raw)
        run(bc, T0 + 21, T0 + 150)
        self.assertEqual(ST.read(r.pub('status.json'))['league_events'], 1)

    def test_overrun_is_private_until_the_slot_expires_then_shifts_everything_after_it(self):
        r, bc = self.rig(slot=500)
        over = max(0, r.D - 500)
        self.assertGreater(over, 0, 'the fixture game must outlast a 500 s slot')
        w0, w1 = r.t['w0'], r.t['w1']
        view = bc.view()
        priv = {w['key']: w for w in view.windows}
        self.assertEqual(priv['w000']['overrun_s'], over)
        self.assertEqual(priv['w001']['start_s'], w1 + over)
        self.assertEqual(priv['w001']['nominal_s'], w1)
        # the recap block moves with the overrun: later breaks keep their length
        run(bc, T0, T0 + w0 + 499)
        pub = B.get(r.pub('broadcast/schedule.json'))
        self.assertEqual([w['start_s'] for w in pub['windows']], [w0, w1], 'the overrun is not public while the slot is still running')
        self.assertNotIn(str(over), json.dumps(pub))
        run(bc, T0 + w0 + 500, T0 + w0 + 502)
        pub = B.get(r.pub('broadcast/schedule.json'))
        self.assertEqual([w['start_s'] for w in pub['windows']], [w0, w1 + over], 'it becomes visible when the slot expires with the game still live')
        # results follow the real end of the window, not the nominal one
        recap = r.t['r1'] - 500 + 500   # nominal recap start in this rig
        run(bc, T0 + w0 + 503, T0 + recap + over - 3)
        self.assertEqual(B.get(r.pub('latest.json'))['release'], 'e00001-0000000001')
        run(bc, T0 + recap + over - 2, T0 + recap + over + 2)
        self.assertEqual(B.get(r.pub('latest.json'))['release'], 'e00002-0000000002')
        # the later window starts exactly where the public schedule said
        run(bc, T0 + recap + over + 3, T0 + w1 + over + 5)
        man = B.get(r.pub('broadcast/live/S901-reg-w02-g01/manifest.json'))
        self.assertEqual(man['start_s'], w1 + over)

    def test_total_length_grows_by_exactly_the_overrun(self):
        r, bc = self.rig(slot=500)
        over = max(0, r.D - 500)
        last = [w for w in bc.view().windows][-1]
        self.assertEqual(last['start_s'] - last['nominal_s'], over)

    def test_pointer_group_is_applied_once_even_when_many_are_due(self):
        r, bc = self.rig()
        bc.clock.pause(T0)
        st = bc.clock.state()
        B.put(bc.clock.path, dict(st, anchor_pos=r.plan['total_s'], ready_until=r.plan['total_s']))
        bc.clock.resume(T0 + 1)
        res = bc.tick(T0 + 2)
        self.assertEqual(B.get(r.pub('latest.json'))['release'], 'e00003-0000000003')
        res2 = bc.tick(T0 + 3)
        self.assertEqual(res2['placed'], 0, 'a second pass changes nothing')
        self.assertEqual(len([f for f in r.files() if f.startswith('playback/games/')]), 3)

    def test_a_pause_file_stops_and_restarts_the_clock(self):
        r, bc = self.rig()
        run(bc, T0, T0 + 150)
        pos = bc.clock.position(T0 + 150)
        open(os.path.join(r.store, S.PAUSE), 'w').close()
        run(bc, T0 + 151, T0 + 400)
        self.assertEqual(bc.clock.position(T0 + 400), bc.clock.position(T0 + 152))
        self.assertAlmostEqual(bc.clock.position(T0 + 400), pos + 1, delta=1.5)
        self.assertEqual(ST.read(r.pub('status.json'))['runner']['state'], 'paused')
        os.unlink(os.path.join(r.store, S.PAUSE))
        run(bc, T0 + 401, T0 + 410)
        self.assertGreater(bc.clock.position(T0 + 410), bc.clock.position(T0 + 400) + 5)
        self.assertEqual(ST.read(r.pub('status.json'))['runner']['state'], 'running')

    def test_pause_and_resume_reach_the_status_file_on_the_tick_that_notices_them(self):
        r2, bc2 = self.rig()                                   # not up to STATUS_EVERY_S later
        run(bc2, T0, T0 + 150)
        self.assertEqual(ST.read(r2.pub('status.json'))['runner']['state'], 'running')
        open(os.path.join(r2.store, S.PAUSE), 'w').close()
        bc2.tick(T0 + 151)
        self.assertEqual(ST.read(r2.pub('status.json'))['runner']['state'], 'paused')
        os.unlink(os.path.join(r2.store, S.PAUSE))
        bc2.tick(T0 + 152)
        self.assertEqual(ST.read(r2.pub('status.json'))['runner']['state'], 'running')

    def test_publisher_failure_freezes_inside_published_coverage_and_recovers(self):
        r, bc = self.rig()
        run(bc, T0, T0 + 50)
        before = bc.clock.state()['ready_until']
        with patch.object(B.shutil, 'copytree', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                run(bc, T0 + 51, T0 + 200)          # the first release is due at 100
            for k in range(60):                      # a daemon keeps trying every second
                with self.assertRaises(OSError):
                    bc.tick(T0 + 201 + k)
        failed_at = bc.clock.state()['ready_until']
        self.assertLessEqual(failed_at, 100 + B.DELAY_S + 1)
        self.assertEqual(ST.read(r.pub('status.json'))['export']['ok'], False)
        self.assertFalse(os.path.exists(r.pub('latest.json')), 'a failed copy publishes no pointer')
        self.assertFalse(any(f.startswith('releases/') for f in r.files()))
        # the clock cannot outrun what was published, however long the outage lasts
        self.assertLessEqual(bc.clock.position(T0 + 5000), failed_at)
        res = bc.tick(T0 + 5000)
        self.assertEqual(B.get(r.pub('latest.json'))['release'], 'e00001-0000000001')
        self.assertEqual(ST.read(r.pub('status.json'))['export']['ok'], True)
        self.assertGreater(res['down_s'], 4000)
        log = [json.loads(x) for x in open(os.path.join(r.store, 'clock_log.jsonl'))]
        self.assertEqual([x['why'] for x in log].count('publish_failure'), 1, 'one outage is one log line, not one per failed second')
        self.assertLess(len(log), 8)
        self.assertLessEqual(res['pos'], failed_at + 1)

    def test_a_restarted_daemon_repairs_the_tree_and_records_the_gap(self):
        r, bc = self.rig()
        run(bc, T0, T0 + r.t['w0'] + 100)
        game = 'S901-reg-w01-g01'
        n = len(os.listdir(r.pub('broadcast/live', game)))
        shutil.rmtree(r.pub('broadcast/live', game))                    # a crash that lost files
        os.makedirs(r.pub('releases/.tmp-e00002-0000000002'))                  # debris of a copy that was cut short
        open(r.pub('broadcast/.tmp-1234-clock.json'), 'w').write('{}')
        bc2 = r.daemon()                                                # a new process
        res = bc2.tick(T0 + r.t['w0'] + 700)
        self.assertFalse(os.path.exists(r.pub('releases/.tmp-e00002-0000000002')))
        self.assertFalse(os.path.exists(r.pub('broadcast/.tmp-1234-clock.json')))
        self.assertGreater(res['down_s'], 400)
        self.assertLess(res['pos'], r.t['w0'] + 110, 'no catch-up: the clock resumes where the viewers stopped')
        self.assertTrue(os.path.isdir(r.pub('broadcast/live', game)))
        self.assertGreaterEqual(len(os.listdir(r.pub('broadcast/live', game))), n)

    def test_postseason_participants_are_announced_with_the_previous_results(self):
        r, bc = self.rig()
        for it in bc.plan['items']:
            if it['key' if 'key' in it else 'kind'] == 'w001':
                it['gated'] = True
        view = S.materialize(bc.plan, B.get(os.path.join(r.store, S.BUILDS)))
        w1 = [w for w in view.windows if w['key'] == 'w001'][0]
        self.assertEqual(w1['announce_s'], r.t['r1'])

    def test_the_whole_public_tree_is_a_function_of_the_position(self):
        r, bc = self.rig()
        seen = {}
        t = 0
        while t <= r.plan['total_s'] + 5:
            bc.tick(T0 + t)
            if t % 97 == 0 or t in (r.t['w0'], r.t['w0'] + 1, r.t['r1'], r.t['r1'] + 1):
                seen[t] = r.files()
            t += 1
        for t, files in seen.items():
            for f in files:
                if f.startswith('releases/e00002') :
                    self.assertGreaterEqual(t, r.t['r1'])
                if f.startswith('releases/e00003') or 'w02' in f:
                    self.assertGreaterEqual(t, r.t['r2'] if f.startswith('releases/e00003') else r.t['w1'])
                if f.startswith('broadcast/replays/S901'):
                    self.assertGreaterEqual(t, r.t['w0'] + r.D - 20)
        self.assertEqual(B.get(r.pub('latest.json'))['release'], 'e00003-0000000003')


if __name__ == '__main__':
    unittest.main()
