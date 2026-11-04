"""Real browser checks against a fresh isolated public tree, never the production league."""
import copy
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))
from dflsite import broadcast as B
import broadcast_demo as D
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipIf(sync_playwright is None, 'Playwright is required')
class BroadcastPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store, self.public = [os.path.join(self.tmp.name, x) for x in ('private', 'public')]
        fixture = ROOT/'tests/fixtures/playback_v1/playback'
        idx = json.loads((fixture/'index.json').read_text())
        original = json.loads((fixture/idx['games'][0]['file']).read_text())
        # New identities and narrative markers: these games/results have never been published.
        self.game = copy.deepcopy(original)
        self.game.update(gid='S901/reg:w01:g01', id='reg:w01:g01', season=901)
        self.game['plays'] = self.game['plays'][:10]
        for i,p in enumerate(self.game['plays']):
            p['text'] = 'FRESH-UNRELEASED-PLAY-%d' % i
            p['score'] = {'away':i, 'home':i+1}
        self.game['final'] = dict(self.game['plays'][-1]['score'])
        self.game['winner'] = 'home'
        self.game['tie'] = False
        self.game['traced_plays'] = sum(bool(p['trace']) for p in self.game['plays'])
        other = copy.deepcopy(self.game)
        other['gid'] = 'S901/reg:w01:g02'
        other['id'] = 'reg:w01:g02'
        path = os.path.join(self.tmp.name, 'first.json')
        path2 = os.path.join(self.tmp.name, 'second.json')
        Path(path).write_text(json.dumps(self.game))
        Path(path2).write_text(json.dumps(other))
        D.prepare(self.store, self.public, ['0:'+path+','+path2], 960)
        self.bc = B.Broadcaster(self.store, self.public)
        self.bc.clock.start(time.time())
        self.move(0, 'paused')
        outer = self
        class Handler(D.Handler):
            public = outer.public
            def _file(self):
                path = self.path.split('?',1)[0]
                if path == '/':
                    return str(ROOT/'site/index.html')
                if path == '/assets/config.js':
                    return str(Path(outer.tmp.name)/'config.js')
                if path.startswith('/assets/'):
                    name = path[len('/assets/'):]
                    if '/' not in name and (ROOT/'site/assets'/name).is_file():
                        return str(ROOT/'site/assets'/name)
                    return None
                return super()._file()
            def do_GET(self, head=False):
                # The protocol demo's original server needs a CSS content type for site assets.
                full = self._file()
                if full and full.endswith('.css'):
                    raw=Path(full).read_bytes()
                    self.send_response(200)
                    self.send_header('Content-Type','text/css')
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    if not head:
                        self.wfile.write(raw)
                else:
                    super().do_GET(head)
        Path(self.tmp.name, 'config.js').write_text('window.DFL_CONFIG={broadcastOnly:true,broadcast:"/broadcast/"};')
        self.server = D.Server(('127.0.0.1',0),Handler)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.url='http://127.0.0.1:%d/' % self.server.server_address[1]
        self.pages=[]
        self.errors=[]
        self.requests=[]

    def tearDown(self):
        for p in self.pages:
            p.close()
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def move(self, pos, state='paused', lease=90):
        now=time.time()
        self.bc.lease=lease
        st=self.bc.clock.state()
        st.update(state=state,anchor_pos=pos,anchor_wall=now,lease_until=now+lease,epoch=st['epoch']+1)
        self.bc.clock._write(st,'test-position',now)
        self.bc.tick(now)

    def page(self, width=1000, skew=0):
        p=self.browser.new_page(viewport={'width':width,'height':900})
        if skew:
            p.add_init_script('Date.now = () => %d;' % skew)
        p.on('pageerror',lambda e:self.errors.append(str(e)))
        p.on('request',lambda r:self.requests.append(r.url))
        self.pages.append(p)
        p.goto(self.url+'#/watch')
        p.wait_for_selector('.broadcast')
        p.wait_for_function('DFL_VIEWER.broadcast().state().position !== null')
        return p

    def test_fresh_public_tree_and_frontend_do_not_read_computed_results(self):
        self.move(-20)
        p=self.page()
        p.wait_for_function("document.querySelector('.bc-stage').textContent === ''")
        public_bytes=b''.join(f.read_bytes() for f in Path(self.public).rglob('*') if f.is_file())
        self.assertNotIn(b'FRESH-UNRELEASED',public_bytes)
        self.assertFalse(p.locator('.pb-canvas').count())
        slug=B.safe(self.game['gid'])
        for path in ('broadcast/live/'+slug+'/head.json','broadcast/live/'+slug+'/end.json','broadcast/replays/'+slug+'.json','private/clock.json','feed/latest.json','feed/status.json','feed/playback/index.json'):
            with self.assertRaises(HTTPError) as err:
                urlopen(self.url+path)
            self.assertEqual(err.exception.code,404)
        self.assertFalse(any('/feed/' in r for r in self.requests))
        self.assertEqual(self.errors,[])

    def test_late_join_refresh_and_clock_skew_share_the_same_play(self):
        plays,_,_=B.timeline(self.game,960)
        traced=next(p for p in plays if p['trace'] and p['trace']['t_end']>=3)
        pos=traced['t0']+1+B.DELAY_S
        self.move(pos)
        a=self.page(skew=1000)
        b=self.page(width=390,skew=9999999999999)
        for p in (a,b):
            p.wait_for_selector('.pb-live .pb-canvas')
            p.wait_for_function("document.querySelector('.broadcast').dataset.play === '%s'" % traced['i'])
            self.assertEqual(p.locator('.bc-stage .pb-text').inner_text(),'Play in progress.')
            self.assertFalse(p.locator('.bc-stage .pb-text').inner_text().startswith('FRESH-UNRELEASED'))
            self.assertLessEqual(p.evaluate('document.documentElement.scrollWidth'),p.evaluate('innerWidth'))
        self.assertEqual(a.locator('.broadcast').get_attribute('data-play'),b.locator('.broadcast').get_attribute('data-play'))
        b.reload()
        b.wait_for_selector('.pb-live .pb-canvas')
        b.wait_for_function("document.querySelector('.broadcast').dataset.play === '%s'" % traced['i'])
        self.assertEqual(self.errors,[])

    def test_live_canvas_moves_then_freezes_on_pause_and_seek_cannot_fast_forward(self):
        plays,_,_=B.timeline(self.game,960)
        traced=next(p for p in plays if p['trace'] and p['trace']['t_end']>=3)
        pos=traced['t0']+.2+B.DELAY_S
        self.move(pos,'running')
        p=self.page()
        p.wait_for_selector('.pb-live .pb-canvas')
        p.wait_for_function("document.querySelector('.broadcast').dataset.play === '%s'" % traced['i'])
        snap=lambda:p.locator('.pb-canvas').evaluate('c=>c.toDataURL()')
        first=snap()
        p.wait_for_timeout(350)
        self.assertNotEqual(first,snap())
        self.move(pos+.7,'paused')
        p.wait_for_function('DFL_VIEWER.broadcast().state().paused')
        p.wait_for_timeout(150)
        frozen=snap()
        p.wait_for_timeout(300)
        self.assertEqual(frozen,snap())
        slider=p.locator('input[aria-label="Broadcast position within this game"]')
        bound=float(slider.get_attribute('max'))
        slider.evaluate("e=>{e.value='999999';e.dispatchEvent(new Event('input'));}")
        self.assertLessEqual(float(slider.input_value()),bound)
        slider.evaluate("e=>{e.value='0';e.dispatchEvent(new Event('input'));}")
        self.assertIn('behind live',p.locator('.bc-controls').inner_text())
        p.get_by_role('button',name='Jump to live',exact=True).click()
        self.assertIn('At live',p.locator('.bc-controls').inner_text())
        self.assertEqual(self.errors,[])

    def test_switching_simultaneous_games_and_final_replay(self):
        self.move(10)
        p=self.page(width=390)
        p.wait_for_selector('.pb-live .pb-canvas')
        p.locator('.bc-game[data-gid="S901/reg:w01:g02"]').click()
        p.wait_for_function("DFL_VIEWER.broadcast().state().selected === 'S901/reg:w01:g02'")
        p.wait_for_function("document.querySelector('.broadcast').dataset.gid === 'S901/reg:w01:g02'")
        _,_,length=B.timeline(self.game,960)
        self.move(length+B.DELAY_S+2)
        p.wait_for_selector('.bc-stage .pb:not(.pb-live)',timeout=20000)
        self.assertIn('final',p.locator('.pb-head').inner_text().lower())
        self.assertEqual(self.errors,[])

    def test_expired_lease_freezes_the_viewer_without_a_local_clock_fallback(self):
        self.move(12,'running',lease=.5)
        p=self.page()
        p.wait_for_function('DFL_VIEWER.broadcast().state().paused')
        before=p.evaluate('DFL_VIEWER.broadcast().state().position')
        p.wait_for_timeout(300)
        self.assertEqual(before,p.evaluate('DFL_VIEWER.broadcast().state().position'))
        self.assertIn('paused',p.locator('.broadcast > .notice').first.inner_text().lower())
        self.assertEqual(self.errors,[])

    def test_missing_server_time_fails_closed(self):
        p=self.browser.new_page()
        self.pages.append(p)
        clock=B.get(os.path.join(self.public,'broadcast','clock.json'))
        p.route('**/broadcast/clock.json',lambda route:route.fulfill(status=200,content_type='application/json',body=json.dumps(clock)))
        p.goto(self.url+'#/watch')
        p.wait_for_function("document.querySelector('.broadcast > .notice').textContent.includes('incompatible')")
        self.assertIsNone(p.evaluate('DFL_VIEWER.broadcast().state().position'))
        self.assertFalse(p.locator('.pb-canvas').count())
