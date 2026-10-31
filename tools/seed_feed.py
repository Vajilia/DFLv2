#!/usr/bin/env python3
"""Install a shipped (seed) feed into a feed root, safely.

  seed_feed.py SRC_FEED_ROOT DEST_FEED_ROOT [--allow-older]

Reads and verifies the release SRC_FEED_ROOT/latest.json names (every file's size and SHA-256 against its manifest), copies it into DEST under a
temporary name, verifies it again, renames it into place and only then replaces DEST/latest.json, all while holding DEST's publisher lock. It
never deletes anything, never moves the feed backwards unless told to, and a failure leaves the previous live release exactly as it was.
Uses only the standard library and src/dflsite (no league, no engine, no numpy): this is what a deployment package uses on the server.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("src")
    ap.add_argument("dest")
    ap.add_argument("--allow-older", action="store_true")
    a = ap.parse_args(argv)
    from dflsite import publish
    try:
        print(json.dumps(publish.install_release(a.src, a.dest, allow_older=a.allow_older), sort_keys=True))
        return 0
    except publish.PublishBusy as e:
        print(json.dumps({"busy": True, "detail": str(e)}), file=sys.stderr)
        return 75
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
