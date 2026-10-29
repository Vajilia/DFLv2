#!/usr/bin/env python3
"""Worked example for docs/SCHEDULE_ROTATION_EXAMPLE.md: one club's opponents over eight seasons with its seed fixed, then changing. Read-only."""
import sys, copy, collections
sys.path.insert(0, __import__('os').path.join(__import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))), 'src'))
from dfl import league
tpl = league.Template(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))), 'data', 'season1'))
base = tpl.initial_state()
dv_of = tpl.division_of
ME='Montana'; MYDIV=dv_of[ME]
def opponents(state, season):
    teams, sched, _ = league.season_tables(tpl, state, season)
    mine=[g for g in sched if ME in (g['away'],g['home'])]
    opp=collections.OrderedDict()
    homes={}
    for g in sorted(mine,key=lambda g:(int(g['week']),int(g['game']))):
        o=g['home'] if g['away']==ME else g['away']
        opp.setdefault(o,[]).append('H' if g['home']==ME else 'A')
    bed=next(t['bed_id'] for t in teams if t['team_city']==ME)
    return bed,opp
def kinds(opp):
    d=collections.defaultdict(list)
    for o,h in opp.items():
        k='division' if dv_of[o]==MYDIV else ('conference' if dv_of[o][0]==MYDIV[0] else 'interconference')
        d[k].append(o if len(h)==1 else o)
    return d
print("Montana's division:",MYDIV, "| division members:", [c for c in dv_of if dv_of[c]==MYDIV])
print("\n=== A: seed fixed at 1 (state never changes) ===")
prev=None
for n in range(1,9):
    bed,opp=opponents(base,n)
    k=kinds(opp)
    sig=(tuple(sorted(opp)))
    print(f"S{n} bed {bed} | conference: {sorted(k['conference'])} | inter: {sorted(k['interconference'])} | same 14 as S1: {sig==prev or prev is None} | home vs inter: {''.join(opp[o][0] for o in sorted(k['interconference']))}")
    prev = sig if prev is None else prev
print("\n=== B: Montana's seed changes (NW division finishes: S1 seed1, then 3,2,5,1,4,2,3), all other clubs fixed ===")
seq=[1,3,2,5,1,4,2,3]
nw=base[MYDIV]
members=[nw['seeds'][s] for s in sorted(nw['seeds'])]   # fixed order of the five clubs
for n,s in enumerate(seq,1):
    st=copy.deepcopy(base)
    others=[c for c in members if c!=ME]
    seeds={}
    seeds[s]=ME
    free=[x for x in range(1,6) if x!=s]
    for x,c in zip(free,others): seeds[x]=c
    st[MYDIV]['seeds']=seeds
    bed,opp=opponents(st,n)
    k=kinds(opp)
    print(f"S{n} seed {s} bed {bed} | conference: {sorted(k['conference'])} | inter: {sorted(k['interconference'])}")
