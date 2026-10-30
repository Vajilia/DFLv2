#!/usr/bin/env python3
"""Regenerate tests/fixtures/feed_v1: a frozen, published feed in format dfl-public-feed version 1, from a finished placeholder season.

Run this ONLY when cutting a new format version, or deliberately adding a minor field (then the old fixture must still read; keep it and add a
second fixture instead). The compatibility tests exist to prove that feeds written by earlier code still read: do not regenerate to make them pass.
Three rosters are kept to keep the fixture small; the manifest is re-sealed over what is kept.
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import feedlab  # noqa: E402
from dflsite import feed, publish  # noqa: E402

KEEP = ("ALA", "NYG", "CLE")
OUT = os.path.join(ROOT, "tests", "fixtures", "feed_v1")


def main():
    _, out, _ = feedlab.stages()
    f = {k: v for k, v in out["closed"].items() if k != "manifest.json" and (not k.startswith("rosters/") or k[8:-5] in KEEP)}
    f = feed.seal(f, feedlab.LEAGUE, "Frozen v1 fixture", "fixture", 47, feedlab.STAMP)
    shutil.rmtree(OUT, ignore_errors=True)
    publish.publish(f, OUT)
    print(f["manifest.json"]["release"], sum(v["bytes"] for v in f["manifest.json"]["files"].values()))


if __name__ == "__main__":
    main()
