"""Play one game between two founding-league clubs and print its box score and play-by-play.
Usage: show_game.py [--seed N] [--home C00] [--away C01] [--pbp] [--spatial] [--traces DIR]
--spatial plays every scrimmage snap with 22 players (Edition 8.48); --traces writes each snap's 2D-view trace as JSON into DIR (implies --spatial)."""
import argparse
import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl.characters import Creator  # noqa: E402
from dfl.engine.boxscore import box_score, format_box, play_by_play  # noqa: E402
from dfl.engine.game import play_game  # noqa: E402
from dfl.engine.spatial import trace as TR  # noqa: E402
from dfl.engine.squad import Squad  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--seed", type=int, default=1)
ap.add_argument("--home", default="C00")
ap.add_argument("--away", default="C01")
ap.add_argument("--pbp", action="store_true")
ap.add_argument("--spatial", action="store_true")
ap.add_argument("--traces", default=None)
a = ap.parse_args()
clubs = [f"C{i:02d}" for i in range(48)]
pop = Creator(a.seed).founding(2001, clubs)
sq = {c: Squad(c, pop["clubs"][c]["roster"] + pop["clubs"][c]["ps"]) for c in (a.home, a.away)}
spatial = a.spatial or a.traces is not None
r = play_game(sq[a.home], sq[a.away], random.Random(a.seed), False, True, spatial=spatial, trace=a.traces is not None)
print(format_box(box_score(r, sq[a.home], sq[a.away])))
if a.pbp:
    print()
    print("\n".join(play_by_play(r, sq[a.home], sq[a.away])))
if a.traces:
    os.makedirs(a.traces, exist_ok=True)
    n = 0
    for row in r.table:
        if row.get("trace"):
            with open(os.path.join(a.traces, f"play_{row['i']:04d}.json"), "w") as f:
                json.dump({"play": {k: v for k, v in row.items() if k != "trace"}, "trace": TR.to_json(row["trace"])}, f)
            n += 1
    print(f"\n{n} play traces written to {a.traces}")
