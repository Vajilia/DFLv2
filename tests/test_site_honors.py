"""Honors browser integration with the backend's actual document builders, isolated files."""
import copy
import json
import os
from pathlib import Path
import shutil
import socketserver
import sys
import tempfile
import threading
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tools'),str(ROOT/'tests')]
from dflsite import honors as H, season as S
from test_honors import universe, TEAMS
import preview_site
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright=None


@unittest.skipIf(sync_playwright is None,'Playwright required')
class HonorsPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw=sync_playwright().start()
        cls.browser=cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.pw.stop()

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)/'feed'
        shutil.copytree(ROOT/'tests/fixtures/feed_v1',self.root)
        self.honors=self.root/'broadcast/honors'
        self.honors.mkdir(parents=True)
        self.isolated=False
        self.config=Path(self.tmp.name)/'config.js'
        outer=self
        class Handler(preview_site.Handler):
            feed_root=str(outer.root)
            site_root=str(ROOT/'site')
            def translate_path(self,path):
                if path.split('?',1)[0]=='/assets/config.js' and outer.isolated:
                    return str(outer.config)
                return super().translate_path(path)
        self.server=socketserver.ThreadingTCPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads=True
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.url='http://127.0.0.1:%d/'%self.server.server_address[1]
        self.requests=[];self.errors=[];self.pages=[]

    def tearDown(self):
        for p in self.pages:p.close()
        self.server.shutdown();self.server.server_close();self.tmp.cleanup()

    def publish(self,parts=(),docs=None,unavailable=None):
        docs=docs or {}
        entries=[]
        for part in parts:
            doc=docs.get(part) or H.build_part(universe(),part,1,TEAMS,{'hall_min_seasons':5})
            H.check_public(doc)
            path=self.honors/'s001'/(part+'.json');path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps(doc))
            entries.append({'part':part,'season':1,'file':'s001/'+part+'.json'})
        idx={'format':H.FORMAT,'format_version':1,'kind':'index','parts':entries,'unavailable':S.honors_unavailable() if unavailable is None else unavailable}
        (self.honors/'index.json').write_text(json.dumps(idx))
        return idx

    def page(self,width=1000):
        p=self.browser.new_page(viewport={'width':width,'height':900})
        self.pages.append(p)
        p.on('request',lambda r:self.requests.append(r.url))
        p.on('pageerror',lambda e:self.errors.append(str(e)))
        p.goto(self.url+'#/honors')
        p.wait_for_selector('.honors')
        p.wait_for_function("!document.querySelector('.honors > .notice').textContent.startsWith('Loading')")
        return p

    def refresh(self,p):
        p.evaluate('DFL_VIEWER.state.honors.refresh()')

    def test_before_first_part_404_is_expected_without_guessing_files(self):
        p=self.page()
        self.assertIn('not been revealed',p.locator('.honors > .notice').inner_text())
        self.assertFalse(p.locator('.honors-part').count())
        self.assertFalse(any('/s001/' in u for u in self.requests))
        self.assertEqual(self.errors,[])

    def test_parts_arrive_progressively_and_polling_does_not_require_a_new_feed_release(self):
        self.publish(['rings'])
        p=self.page()
        p.wait_for_selector('[data-part="rings"]')
        self.assertFalse(p.locator('[data-part="tiara"]').count())
        self.assertFalse(any('/tiara.json' in u for u in self.requests))
        p.locator('[data-part="rings"] summary').click()
        p.wait_for_selector('[data-part="rings"] details[open]')
        self.publish(['rings','tiara'])
        self.refresh(p)
        p.wait_for_selector('[data-part="tiara"]')
        self.assertTrue(p.locator('[data-part="rings"] details').evaluate('e=>e.open'))
        self.assertIn('Cee Oh',p.locator('[data-part="tiara"]').text_content())
        self.assertIn('Head coach',p.locator('[data-part="tiara"]').text_content())
        self.assertEqual(self.errors,[])

    def test_empty_halls_missing_records_and_no_fan_entrant_are_not_invented(self):
        docs={k:H.build_part(universe(tiara_year=None,rings_year=None),k,1,TEAMS,{'hall_min_seasons':5}) for k in H.PARTS}
        self.publish(H.PARTS,docs)
        p=self.page(width=390)
        p.wait_for_selector('[data-part="fan_entrant"]')
        for k,doc in docs.items():
            self.assertIn(doc['note'],p.locator('[data-part="'+k+'"]').text_content())
        self.assertFalse(p.locator('.honors-part details').count())
        self.assertIn('5 credited seasons',p.locator('[data-part="halls"]').text_content())
        for u in S.honors_unavailable():
            self.assertIn(u['award'],p.locator('.honors').text_content())
            self.assertIn(u['reason'],p.locator('.honors').text_content())
        self.assertLessEqual(p.evaluate('document.documentElement.scrollWidth'),390)
        self.assertEqual(self.errors,[])

    def test_recorded_halls_scope_registers_and_fan_entrant_are_shown(self):
        docs={k:H.build_part(universe(eligible=True),k,1,TEAMS) for k in ('halls','fan_entrant')}
        self.publish(docs.keys(),docs)
        p=self.page()
        p.wait_for_selector('[data-part="halls"]')
        p.locator('[data-part="halls"] summary').click()
        self.assertIn('League hall',p.locator('[data-part="halls"]').text_content())
        self.assertIn('Retired Star',p.locator('[data-part="halls"]').inner_text())
        self.assertIn('Retired Star',p.locator('[data-part="fan_entrant"]').text_content())
        self.assertEqual(self.errors,[])

    def test_unavailable_text_is_literal_and_part_paths_cannot_escape(self):
        idx=self.publish(['rings'])
        idx['parts'][0]['file']='../../private.json'
        (self.honors/'index.json').write_text(json.dumps(idx))
        p=self.page()
        self.assertIn('Invalid Honors part reference',p.locator('.honors > .notice').inner_text())
        self.assertFalse(any('/private.json' in u for u in self.requests))
        doc=H.build_part(universe(),'rings',1,TEAMS)
        doc['note']='<img src="bad" onerror="window.BAD=1">'
        self.publish(['rings'],{'rings':doc})
        self.refresh(p)
        self.assertIn(doc['note'],p.locator('.honors-part').text_content())
        self.assertEqual(p.locator('.honors img').count(),0)
        self.assertIsNone(p.evaluate('window.BAD'))
        self.assertEqual(self.errors,[])

    def test_failed_part_is_retried_and_foreign_formats_do_not_render(self):
        idx=self.publish(['tiara'])
        part=self.honors/'s001/tiara.json';saved=part.read_text();part.unlink()
        p=self.page()
        self.assertIn('temporarily unavailable',p.locator('.honors').inner_text())
        part.write_text(saved)
        self.refresh(p)
        self.assertTrue(p.locator('[data-part="tiara"]').count())
        idx['format_version']=99
        (self.honors/'index.json').write_text(json.dumps(idx))
        self.refresh(p)
        self.assertIn('Unsupported Honors format',p.locator('.honors > .notice').inner_text())
        self.assertEqual(self.errors,[])

    def test_isolated_mode_honors_has_no_simulation_feed_requests(self):
        self.isolated=True
        self.config.write_text('window.DFL_CONFIG={broadcastOnly:true,broadcast:"/feed/broadcast/"};')
        self.publish(['rings'])
        p=self.page(width=390)
        p.wait_for_selector('[data-part="rings"]')
        self.assertFalse(any('/feed/latest.json' in u or '/feed/status.json' in u or '/feed/playback/' in u for u in self.requests))
        visible=p.locator('#tabs a:visible').all_text_contents()
        self.assertEqual([s.lower() for s in visible],['watch now','honors'])
        self.assertEqual(self.errors,[])
