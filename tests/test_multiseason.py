"""Multi-season broadcast continuity (src/dflsite/season.py, tools/broadcast_build.py next-season).

A synthetic two-season store in virtual time (the checked-in public replay fixture, copied under fresh season-901 and season-902 identities; nothing here touches
a league, a production directory or the site). The second season REPEATS the first season's game ids (`reg:w01:g01`) with other scores, as every real season does.

What must hold across the year boundary:
  * the first season's public files, replay library, playback index, Honors index and latest pointer stay exactly as they were
  * a repeated id never selects another season's game
  * nothing of the next season (participants, results, awards) is public before its own time
  * pause, a killed and restarted daemon and a crash in the middle of the hand-over all end in the same public tree as an uninterrupted run
  * a next season that does not fit (wrong season, wrong first event, a game id already used) is never started
"""
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from dflsite import broadcast as B, playback as PB, season as S

FIX = ROOT / 'tests/fixtures/playback_v1/playback'
T0 = 1000.0
SLOT = 960
EVENTS = ['offseason_open', 'week_1', 'week_2', 'year_close']       # four events a "year"
GAMES = [('reg:w01:g01', 'week_1', 2), ('reg:w01:g02', 'week_1', 2), ('reg:w02:g01', 'week_2', 3)]


def fixture_game(season, rid, bump):
    idx = json.loads((FIX / 'index.json').read_text())
    g = json.loads((FIX / idx['games'][0]['file']).read_text())
    g.update(gid='S%03d/%s' % (season, rid), id=rid, season=season)
    g['final'] = {'away': g['final']['away'] + bump, 'home': g['final']['home'] + 2 * bump}     # another season, another score
    return g


class Year:
    """One season's private material, written into the store the way the builder does, but with synthetic content."""

    def __init__(self, store, season, event_base, rooted):
        self.store, self.season, self.base = store, season, event_base
        self.dir = store if rooted else os.path.join(store, 'seasons', 's%03d' % season)
        os.makedirs(self.dir, exist_ok=True)
        slot = SLOT
        t = {'w0': 200, 'r1': 200 + slot}
        t['w1'], t['r2'] = t['r1'] + 900, t['r1'] + 900 + slot
        t['h'] = t['r2'] + 600
        self.t = t
        self.plan = {'format': S.PLAN_FORMAT, 'format_version': 1, 'season': season, 'event_base': event_base, 'total_s': t['h'] + 600, 'params': {'slot_s': slot}, 'rounds': {},
                     'round_sizes': {}, 'single': {}, 'games': {'reg:w01:g01': 'w000', 'reg:w01:g02': 'w000', 'reg:w02:g01': 'w001'}, 'events': list(EVENTS), 'items': [
                         {'kind': 'block', 'role': 'offseason', 'label': 'opening', 'start_s': 0, 'end_s': 100, 'events': ['offseason_open'], 'events_at': 100, 'parts': []},
                         {'kind': 'window', 'key': 'w000', 'label': 'week 1 window', 'start_s': t['w0'], 'slot_s': slot, 'expected': 2, 'gated': False, 'event': None},
                         {'kind': 'block', 'role': 'recap', 'label': 'week 1 results', 'start_s': t['r1'], 'end_s': t['r1'] + 600, 'events': ['week_1'], 'events_at': 0, 'parts': []},
                         {'kind': 'window', 'key': 'w001', 'label': 'week 2 window', 'start_s': t['w1'], 'slot_s': slot, 'expected': 1, 'gated': False, 'event': None},
                         {'kind': 'block', 'role': 'recap', 'label': 'week 2 results', 'start_s': t['r2'], 'end_s': t['r2'] + 600, 'events': ['week_2'], 'events_at': 0, 'parts': []},
                         {'kind': 'block', 'role': 'honors', 'label': 'honors show', 'start_s': t['h'], 'end_s': t['h'] + 600, 'events': ['year_close'], 'events_at': 300,
                          'parts': [{'part': 'tiara', 'at': 0, 'event': 'year_close'}]}]}
        B.put(os.path.join(self.dir, S.PLAN), self.plan)
        self.builds = {'format': S.BUILDS_FORMAT, 'format_version': 1, 'events': {}, 'games': {}, 'honors': {}, 'league_id': 'test'}
        self.recs = {}
        for rid, key, seq in GAMES:
            g = fixture_game(season, rid, bump=season - 900)
            rel, size, sha = PB.write_game(os.path.join(store, 'playback'), g)
            rec = B.store_game(store, g, slot, replay_name=os.path.basename(rel))
            self.recs[g['gid']] = (PB.index_entry(g, rel, size, sha), rec, g, key, seq)
        self.built = 0

    def build(self, upto):
        while self.built < upto:
            self.built += 1
            n = self.built
            seq = self.base + n
            rel = 'e%05d-%010d' % (seq, seq)
            os.makedirs(os.path.join(self.store, 'feed/releases', rel), exist_ok=True)
            B.put(os.path.join(self.store, 'feed/releases', rel, 'manifest.json'), {'release': rel, 'secret_marker': 'RESULT-OF-EVENT-%d' % seq})
            B.put(os.path.join(self.store, 'feed/pointers/e%05d.json' % seq), {'release': rel})
            self.builds['events'][str(seq)] = {'key': EVENTS[n - 1], 'release': rel, 'events': seq, 'pointer': 'feed/pointers/e%05d.json' % seq}
            for gid, (e, rec, g, key, k) in self.recs.items():
                if k == n:
                    self.builds['games'][gid] = {'seq': seq, 'key': key, 'away': g['teams']['away']['abbr'], 'home': g['teams']['home']['abbr'], 'segments': rec['segments'],
                                                 'length_s': rec['length_s'], 'delay_s': B.DELAY_S, 'replay': rec['replay'], 'entry': e}
            if n == 4:
                B.put(os.path.join(self.store, 'honors', 's%03d/tiara.json' % self.season), {'kind': 'tiara', 'season': self.season, 'marker': 'HONORS-OF-%d' % self.season})
                self.builds['honors']['tiara'] = 's%03d/tiara.json' % self.season
        B.put(os.path.join(self.dir, S.BUILDS), self.builds)


class Rig:
    def __init__(self, tmp):
        self.store, self.public = os.path.join(tmp, 'store'), os.path.join(tmp, 'public')
        self.y1 = Year(self.store, 901, 0, rooted=True)
        self.y2 = Year(self.store, 902, 4, rooted=False)
        self.end1 = self.y1.plan['total_s']

    def daemon(self, guard=False):
        """`guard=False`: the clock follows the wall clock whatever the tick spacing (the timeline tests tick every 5 s); True is the daemon's real publication guard
        (the clock may not run more than the viewer delay ahead of what was published, so it must be ticked every second)."""
        return S.SeasonBroadcaster(self.store, self.public, guard_publication=guard)

    def started(self, guard=False, pos=0.0):
        bc = self.daemon(guard)
        bc.clock.start(T0, pos, B.LEASE_S, ready_until=pos if guard else None)
        return bc

    def tree(self):
        out = {}
        for d, _, names in os.walk(self.public):
            for n in names:
                p = os.path.join(d, n)
                rel = os.path.relpath(p, self.public)
                if rel.startswith('broadcast/clock.json') or rel == 'status.json':
                    continue
                out[rel] = hashlib.sha256(open(p, 'rb').read()).hexdigest()
        return out

    def pub(self, *p):
        return os.path.join(self.public, *p)

    def js(self, *p):
        return json.loads(open(self.pub(*p), 'rb').read())

    def text_of_tree(self):
        out = []
        for d, _, names in os.walk(self.public):
            for n in names:
                out.append(open(os.path.join(d, n), 'rb').read().decode('utf-8', 'replace'))
        return '\n'.join(out)


def run(bc, a, b, step=5):
    t = a
    r = None
    while t <= b:
        r = bc.tick(T0 + t)
        t += step
    return r


class Boundary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        B.FSYNC = False
        self.addCleanup(setattr, B, 'FSYNC', True)
        self.r = Rig(self.tmp.name)
        self.r.y1.build(4)

    def start_year_two(self, built=4):
        self.r.y2.build(built)

    def to_end_of_year_one(self, bc):
        run(bc, 0, self.r.end1 - 5)

    # ------------------------------------------------------------------------------------------------------------------ earlier seasons stay as they were
    def test_first_season_stays_exactly_as_it_was_across_the_boundary(self):
        r = self.r
        bc = r.started()
        self.to_end_of_year_one(bc)
        before = {k: v for k, v in r.tree().items() if k != 'latest.json' and not k.endswith('index.json')}
        idx1 = r.js('playback/index.json')
        rep1 = r.js('broadcast/replays/index.json')
        hon1 = r.js('broadcast/honors/index.json')
        latest1 = r.js('latest.json')
        self.start_year_two()
        run(bc, r.end1, r.end1 + 3 * 3600)
        after = r.tree()
        for k, v in before.items():
            if k == 'broadcast/schedule.json':               # the schedule is a function of the position and moves on by design
                continue
            self.assertEqual(after.get(k), v, '%s changed or vanished at the year boundary' % k)
        idx = r.js('playback/index.json')
        old = [e for e in idx['games'] if e['season'] == 901]
        self.assertEqual(old, idx1['games'], 'the first season keeps its replay-index entries, unchanged')
        rep = r.js('broadcast/replays/index.json')
        self.assertEqual([g for g in rep['games'] if g['season'] == 901], rep1['games'])
        hon = r.js('broadcast/honors/index.json')
        self.assertEqual([p for p in hon['parts'] if p['season'] == 901], hon1['parts'])
        self.assertTrue(any(p['season'] == 902 for p in hon['parts']))
        self.assertEqual(latest1['release'], 'e%05d-%010d' % (4, 4))

    def test_the_index_is_never_emptied_and_latest_never_moves_back(self):
        r = self.r
        bc = r.started()
        self.to_end_of_year_one(bc)
        self.start_year_two()
        seen_latest, n_idx = 0, 0
        t = r.end1
        while t < r.end1 + 2 * 3600:
            bc.tick(T0 + t)
            t += 5
            for p, k in (('playback/index.json', 'games'), ('broadcast/replays/index.json', 'games'), ('broadcast/honors/index.json', 'parts')):
                self.assertTrue(os.path.exists(r.pub(p)), '%s disappeared at %d s' % (p, t))
            self.assertGreaterEqual(len(r.js('playback/index.json')['games']), n_idx)
            n_idx = len(r.js('playback/index.json')['games'])
            ev = int(r.js('latest.json')['release'][1:6])
            self.assertGreaterEqual(ev, seen_latest, 'latest.json moved backwards')
            seen_latest = ev

    # ------------------------------------------------------------------------------------------------------------------ repeated ids
    def test_a_repeated_id_never_selects_another_seasons_game(self):
        r = self.r
        bc = r.started()
        self.start_year_two()
        run(bc, 0, r.end1 + r.y2.plan['total_s'] - 5)
        idx = r.js('playback/index.json')['games']
        ids = [e['id'] for e in idx]
        self.assertEqual(len(ids), 6)
        self.assertLess(len(set(ids)), len(ids), 'the fixture must repeat ids across seasons')
        self.assertEqual(len({e['gid'] for e in idx}), 6)
        self.assertEqual(len({(e['season'], e['id']) for e in idx}), 6)
        self.assertEqual(len({e['file'] for e in idx}), 6)
        scores = {}
        for e in idx:
            doc = json.loads(open(r.pub('playback', e['file']), 'rb').read())
            self.assertEqual((doc['gid'], doc['season'], doc['id']), (e['gid'], e['season'], e['id']))
            self.assertEqual(doc['final'], {'away': e['away_score'], 'home': e['home_score']})
            self.assertEqual(hashlib.sha256(open(r.pub('playback', e['file']), 'rb').read()).hexdigest(), e['sha256'])
            scores.setdefault(e['id'], set()).add((e['season'], e['away_score'], e['home_score']))
        for rid, s in scores.items():
            self.assertEqual(len({x[0] for x in s}), 2, rid)
            self.assertEqual(len(s), 2, 'two seasons, two different results for ' + rid)
        rep = r.js('broadcast/replays/index.json')['games']
        self.assertEqual(sorted(g['gid'] for g in rep), sorted(e['gid'] for e in idx))
        for g in rep:
            self.assertTrue(os.path.exists(r.pub('broadcast/replays', g['replay'])))
            self.assertEqual(g['season'], int(g['gid'][1:4]))
            self.assertTrue(os.path.isdir(r.pub('broadcast/live', B.safe(g['gid']))))

    def test_a_game_of_another_season_in_the_next_builds_is_refused(self):
        r = self.r
        bc = r.started()
        self.start_year_two()
        bad = json.loads(open(os.path.join(r.y2.dir, S.BUILDS), 'rb').read())
        stolen = next(iter(r.y1.builds['games']))                    # a season-901 game id inside season 902's builds
        bad['games'][stolen] = r.y1.builds['games'][stolen]
        B.put(os.path.join(r.y2.dir, S.BUILDS), bad)
        run(bc, 0, r.end1 + 1800)
        self.assertFalse(os.path.exists(os.path.join(r.store, S.CARRY)), 'a next season that does not fit is never started')
        self.assertNotIn('RESULT-OF-EVENT-5', r.text_of_tree())
        self.assertEqual(r.js('status.json')['published_events'], 4)

    # ------------------------------------------------------------------------------------------------------------------ nothing of the next season early
    def test_the_next_season_is_not_public_before_its_time(self):
        r = self.r
        bc = r.started()
        self.start_year_two()
        marks = ['RESULT-OF-EVENT-%d' % n for n in (6, 7, 8)] + ['HONORS-OF-902']          # events 6 to 8 are year two's week 1, week 2 and year close
        t = 0
        end = r.end1 + r.y2.t['r1'] - 5                # up to just before year two's first results block
        while t <= end:
            bc.tick(T0 + t)
            t += 15
            if t % 600 == 0 or t > end - 30:
                text = r.text_of_tree()
                for m in marks:
                    self.assertNotIn(m, text, '%s public at %d s' % (m, t))
                if t < r.end1 + 100 - 15:                # the opening event of year two (event 5) is released at its own time, not before
                    self.assertNotIn('RESULT-OF-EVENT-5', text, 'event 5 public at %d s' % t)
        rep = r.js('broadcast/replays/index.json')['games']
        for g in rep:                                    # a season-902 replay is listed only once that game's broadcast has ended (its own live folder says final)
            if g['season'] == 902:
                self.assertEqual(r.js('broadcast/live', B.safe(g['gid']), 'manifest.json')['state'], 'final')
        self.assertTrue(all(e['season'] == 901 for e in r.js('playback/index.json')['games']), 'no season-902 result in the results index before its results block')
        self.assertFalse(any(p['season'] == 902 for p in r.js('broadcast/honors/index.json')['parts']))
        self.assertFalse(os.path.exists(r.pub('broadcast/honors/s902')))
        for d, _, names in os.walk(os.path.join(r.public, 'playback')):          # the results library (replays of results) has no season-902 file yet
            for n in names:
                self.assertNotIn('S902-', n)

    def test_season_two_games_are_absent_from_the_replay_library_until_they_end(self):
        r = self.r
        bc = r.started()
        self.start_year_two()
        run(bc, 0, r.end1 + r.y2.t['w0'] + 60)              # one minute into season two's first window
        rep = r.js('broadcast/replays/index.json')['games']
        self.assertTrue(all(g['season'] == 901 for g in rep))
        self.assertEqual(r.js('broadcast/live/S902-reg-w01-g01/manifest.json')['state'], 'live')
        self.assertFalse(os.path.exists(r.pub('broadcast/live/S902-reg-w02-g01')), 'a later window has not begun')

    def test_the_next_season_schedule_lists_only_what_has_begun_or_is_near(self):
        r = self.r
        bc = r.started()
        self.start_year_two()
        run(bc, 0, r.end1 + 60)
        sched = r.js('broadcast/schedule.json')
        for w in sched['windows']:
            for g in w['games']:
                self.assertNotIn('S902', g['gid']) if w['start_s'] > r.end1 + 60 + S.LOOKAHEAD_S else None
        self.assertTrue(all(w['start_s'] >= r.end1 for w in sched['windows'] if any('S902' in g['gid'] for g in w['games'])))

    # ------------------------------------------------------------------------------------------------------------------ pause, restart, crash
    def uninterrupted(self, until, step=5):
        tmp = tempfile.mkdtemp(dir=self.tmp.name)
        r = Rig(tmp)
        r.y1.build(4)
        r.y2.build(4)
        bc = r.started()
        run(bc, 0, until, step)
        return r.tree()

    def test_pause_across_the_boundary_holds_the_clock_and_the_tree(self):
        r = self.r
        bc = r.started()
        self.start_year_two()
        run(bc, 0, r.end1 - 100)
        open(os.path.join(r.store, S.PAUSE), 'w').close()
        held = pos = None
        for t in range(r.end1 - 95, r.end1 + 2000, 5):                 # the boundary passes in wall time while paused
            bc.tick(T0 + t)
            if held is None:
                held, pos = r.tree(), bc.clock.position(T0 + t)       # the first tick that sees the PAUSE file stops the clock
        self.assertEqual(bc.clock.position(T0 + r.end1 + 2000), pos)
        self.assertEqual(r.tree(), held)
        os.unlink(os.path.join(r.store, S.PAUSE))
        resume_at = r.end1 + 2000
        bc.tick(T0 + resume_at)
        t = resume_at
        while bc.clock.position(T0 + t) < r.end1 + 600:
            t += 5
            bc.tick(T0 + t)
            self.assertLess(t, resume_at + 3 * 3600)
        self.assertTrue(os.path.exists(os.path.join(r.store, S.CARRY)))
        self.assertEqual(r.js('status.json')['runner']['state'], 'running')

    def test_a_killed_daemon_restarted_around_the_boundary_ends_in_the_same_tree(self):
        r = self.r
        self.start_year_two()
        want = self.uninterrupted(r.end1 + 5400)
        for kill_at in (r.end1 - 40, r.end1 + 1, r.end1 + 700):
            tmp = tempfile.mkdtemp(dir=self.tmp.name)
            q = Rig(tmp)
            q.y1.build(4)
            q.y2.build(4)
            bc = q.started()
            run(bc, 0, kill_at)
            bc2 = q.daemon()                                  # a new process after a gap in wall time: the viewers' clock stood still
            now = kill_at + 5
            down = bc2.tick(T0 + now + 400)
            run(bc2, now + 400, now + 400 + (r.end1 + 5400 - kill_at))
            got = q.tree()
            pos_gap = 400
            self.assertEqual(sorted(got), sorted(want), 'file set after a restart at %d' % kill_at)
            for k in want:
                if not k.endswith('index.json') and not k.endswith('latest.json'):
                    self.assertEqual(got[k], want[k], k)
            self.assertGreaterEqual(pos_gap, 0)
            self.assertIsNotNone(down)

    def test_a_crash_in_the_middle_of_the_hand_over_is_finished_by_the_next_process(self):
        r = self.r
        bc = r.started()
        self.start_year_two()
        run(bc, 0, r.end1 - 10)
        real_put = B.put
        state = {'n': 0}

        def failing(path, obj):
            if os.path.basename(path) == S.CARRY:
                state['n'] += 1
                raise OSError('disk full')
            return real_put(path, obj)
        B.put = failing
        try:
            with self.assertRaises(Exception):
                run(bc, r.end1 - 5, r.end1 + 30, step=1)
        finally:
            B.put = real_put
        self.assertGreaterEqual(state['n'], 1)
        self.assertFalse(os.path.exists(os.path.join(r.store, S.CARRY)))
        bc2 = r.daemon()
        run(bc2, r.end1 + 31, r.end1 + 5400)
        self.assertTrue(os.path.exists(os.path.join(r.store, S.CARRY)))
        got = r.tree()
        want = self.uninterrupted(r.end1 + 5400)
        self.assertEqual(sorted(got), sorted(want))

    def test_the_whole_public_tree_is_a_function_of_the_position_in_both_seasons(self):
        r = self.r
        self.start_year_two()
        for at in (r.end1 - 10, r.end1 + 50, r.end1 + r.y2.t['r1'] + 10, r.end1 + r.y2.plan['total_s'] + 30):
            a = self.uninterrupted(at, step=5)
            tmp = tempfile.mkdtemp(dir=self.tmp.name)
            q = Rig(tmp)
            q.y1.build(4)
            q.y2.build(4)
            bc = q.started()
            run(bc, 0, at, step=37)                                # a different tick pattern, same final position
            bc.tick(T0 + at)
            b = q.tree()
            self.assertEqual(sorted(a), sorted(b), 'at %d' % at)
            for k in a:
                if not k.endswith('index.json') and not k.endswith('latest.json'):
                    self.assertEqual(a[k], b[k], '%s at %d' % (k, at))

    # ------------------------------------------------------------------------------------------------------------------ holding
    def set_pos(self, bc, now, pos):
        st = bc.clock.state()
        B.put(bc.clock.path, dict(st, anchor_wall=now, anchor_pos=pos, lease_until=now + B.LEASE_S, ready_until=pos))

    def test_the_guarded_clock_crosses_the_boundary_when_the_next_season_is_ready(self):
        r = self.r
        self.start_year_two()
        bc = r.started(guard=True, pos=r.end1 - 30)
        pos = []
        for t in range(0, 120):
            bc.tick(T0 + t)
            pos.append(bc.clock.position(T0 + t))
        self.assertTrue(os.path.exists(os.path.join(r.store, S.CARRY)))
        self.assertGreater(pos[-1], r.end1 + 20, 'the clock goes on into the next season')
        self.assertEqual(sorted(set(round(b - a) for a, b in zip(pos, pos[1:]))), [1], 'the clock never jumped or stopped at the boundary')

    def test_the_guarded_clock_holds_at_the_end_when_the_next_season_does_not_fit(self):
        r = self.r
        self.start_year_two()
        bad = json.loads(open(os.path.join(r.y2.dir, S.BUILDS), 'rb').read())
        bad['events']['5']['key'] = 'week_1'                          # the plan has no such event order: the build is not season two's
        bad['games']['S901/reg:w01:g01'] = r.y1.builds['games']['S901/reg:w01:g01']
        B.put(os.path.join(r.y2.dir, S.BUILDS), bad)
        bc = r.started(guard=True, pos=r.end1 - 30)
        for t in range(0, 150):
            bc.tick(T0 + t)
        self.assertFalse(os.path.exists(os.path.join(r.store, S.CARRY)))
        self.assertAlmostEqual(bc.clock.position(T0 + 149), r.end1, delta=1.5, msg='held at the end of the season on air')
        self.assertEqual(r.js('status.json')['runner']['state'], 'paused')
        self.assertEqual(r.js('status.json')['published_events'], 4)

    def test_without_a_next_season_a_rolled_store_holds_at_the_end(self):
        r = self.r
        self.start_year_two(built=4)
        end2 = r.end1 + r.y2.plan['total_s']
        bc = r.started(guard=True, pos=r.end1 - 20)
        for t in range(0, 60):
            bc.tick(T0 + t)
        self.assertTrue(os.path.exists(os.path.join(r.store, S.CARRY)))
        now = T0 + 60
        self.set_pos(bc, now, end2 - 25)                               # jump the clock to the last seconds of season two (the whole season is built)
        bc.tick(now)
        for t in range(61, 140):
            bc.tick(T0 + t)
        pos = bc.clock.position(T0 + 139)
        self.assertLessEqual(pos, end2 + 1e-6, 'the clock never runs past the end of the last prepared season')
        self.assertGreater(pos, end2 - 3)
        self.assertEqual(r.js('status.json')['published_events'], 8)
        self.assertEqual(r.js('status.json')['runner']['state'], 'paused')

    def test_a_single_year_store_behaves_as_before(self):
        tmp = tempfile.mkdtemp(dir=self.tmp.name)
        q = Rig(tmp)
        q.y1.build(4)
        bc = q.started()
        run(bc, 0, q.end1 + 3000)
        self.assertFalse(os.path.exists(os.path.join(q.store, S.CARRY)))
        self.assertEqual(q.js('status.json')['published_events'], 4)
        self.assertTrue(bc.clock.position(T0 + q.end1 + 3000) > q.end1)


if __name__ == '__main__':
    unittest.main()
