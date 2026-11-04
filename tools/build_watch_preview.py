#!/usr/bin/env python3
"""Build an isolated Watch now preview from a public test fixture. Never reads a league.

  python tools/build_watch_preview.py --out /absolute/new/folder [--run]

Private material goes in a sibling directory, not under the served site. Use sites-preview
or your development static server for the output folder; --run updates public broadcast files.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import broadcast_demo as D
from dflsite import broadcast as B


def build(out):
    out=Path(out).resolve()
    private=out.with_name(out.name+'-private')
    if out.exists() or private.exists():
        raise SystemExit('Use a new output directory; existing files are never overwritten.')
    shutil.copytree(ROOT/'site',out)
    private.mkdir()
    fixture=ROOT/'tests/fixtures/playback_v1/playback'
    idx=json.loads((fixture/'index.json').read_text())
    game=json.loads((fixture/idx['games'][0]['file']).read_text())
    paths=[]
    for i in range(2):
        g=copy.deepcopy(game)
        g.update(gid='S901/reg:w01:g%02d'%(i+1),id='reg:w01:g%02d'%(i+1),season=901)
        path=private/('game%d.json'%i)
        path.write_text(json.dumps(g))
        paths.append(str(path))
    D.prepare(str(private/'store'),str(out),['5:'+','.join(paths)],960)
    (out/'assets/config.js').write_text('window.DFL_CONFIG={broadcastOnly:true,broadcast:"/broadcast/"};')
    bc=B.Broadcaster(str(private/'store'),str(out))
    bc.clock.start(time.time())
    bc.tick()
    return bc


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out',required=True)
    ap.add_argument('--run',action='store_true')
    args=ap.parse_args()
    bc=build(args.out)
    print('Watch preview prepared. Open #/watch; private source is outside this folder.',flush=True)
    if args.run:
        try:
            while True:
                bc.tick()
                time.sleep(.5)
        except KeyboardInterrupt:
            bc.clock.pause(time.time())
    return 0


if __name__=='__main__':
    sys.exit(main())
