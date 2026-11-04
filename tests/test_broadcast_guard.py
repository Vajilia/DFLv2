"""Publication failures must freeze within released coverage, not consume a 90s lease."""
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from dflsite import broadcast as B


class PublicationGuard(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store,self.public=[os.path.join(self.tmp.name,x)for x in ('private','public')]
        fixture=ROOT/'tests/fixtures/playback_v1/playback'
        index=json.loads((fixture/'index.json').read_text())
        self.game=json.loads((fixture/index['games'][0]['file']).read_text())
        self.game.update(gid='S902/reg:w01:g01',season=902)
        rec=B.store_game(self.store,self.game,960)
        self.windows=[dict(label='Opening window',start_s=0,slot_s=960,games=[dict(rec,away='OCO',home='GBP')])]
        public,private=B.make_schedule(self.windows)
        B.put(os.path.join(self.store,'schedule.json'),private)
        B.put(os.path.join(self.public,'broadcast','schedule.json'),public)
        self.bc=B.Broadcaster(self.store,self.public)
        self.bc.clock.start(1000)

    def test_dead_publisher_does_not_skip_unpublished_viewing(self):
        self.bc.tick(1000)
        self.bc.tick(1001)
        self.assertEqual(self.bc.clock.position(1050),4)
        self.assertEqual(B.viewer_state(self.public,self.game['gid'],4)['idx'],0)
        resumed=self.bc.tick(1050)
        self.assertEqual(resumed['pos'],4)
        self.assertEqual(resumed['down_s'],46)
        self.assertEqual(self.bc.tick(1051)['pos'],5)

    def test_failed_publication_never_extends_coverage_horizon(self):
        self.bc.tick(1000)
        before=self.bc.clock.state()['ready_until']
        with patch.object(B,'reconcile',side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                self.bc.tick(1002)
        self.assertEqual(self.bc.clock.state()['ready_until'],before)
        self.assertEqual(self.bc.clock.position(1100),before)

    def test_future_playoff_participants_are_not_announced_by_default(self):
        private=copy.deepcopy(self.windows)
        private[0]['start_s']=100
        private[0]['games'][0]['gid']='S902/po:Eastern:conference:s2v1'
        early,_=B.make_schedule(private,99)
        due,_=B.make_schedule(private,100)
        self.assertEqual(early['windows'][0]['games'],[])
        self.assertEqual(due['windows'][0]['games'][0]['home'],'GBP')
        private[0]['announce_s']=50
        known,_=B.make_schedule(private,50)
        self.assertTrue(known['windows'][0]['games'])

    def test_nested_private_fields_are_refused_before_publication(self):
        path=os.path.join(self.store,'games',B.safe(self.game['gid']),'head.json')
        head=B.get(path)
        head['roster'][next(iter(head['roster']))]['private_rating']=99
        B.put(path,head)
        with self.assertRaises(B.BroadcastError):
            self.bc.tick(1000)
        self.assertFalse(os.path.exists(os.path.join(self.public,'broadcast','live',B.safe(self.game['gid']),'head.json')))
