"""Play one game between two founding-league clubs and print its box score and play-by-play.
Usage: show_game.py [--seed N] [--home C00] [--away C01] [--pbp] [--spatial]"""
import argparse
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl.characters import Creator  # noqa: E402
from dfl.engine.boxscore import box_score, format_box, play_by_play  # noqa: E402
from dfl.engine.game import play_game  # noqa: E402
from dfl.engine.squad import Squad  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--seed", type=int, default=1)
ap.add_argument("--home", default="C00")
ap.add_argument("--away", default="C01")
ap.add_argument("--pbp", action="store_true")
a = ap.parse_args()
clubs = [f"C{i:02d}" for i in range(48)]
pop = Creator(a.seed).founding(2001, clubs)
sq = {c: Squad(c, pop["clubs"][c]["roster"] + pop["clubs"][c]["ps"]) for c in (a.home, a.away)}
r = play_game(sq[a.home], sq[a.away], random.Random(a.seed), False, True)
print(format_box(box_score(r, sq[a.home], sq[a.away])))
if a.pbp:
    print()
    print("\n".join(play_by_play(r, sq[a.home], sq[a.away])))
