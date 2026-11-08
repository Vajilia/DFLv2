#!/usr/bin/env python3
"""Loopback stand-in for the production web server: the same rules the repository's own tests give it (tests/test_rehearsal_check.py RuleServer), over the generated public folder.

  site_server.py --tree PINNED_TREE --dir WEB --port N

The monitor's URL checks need something to ask; this is it. It listens on 127.0.0.1 only.
"""
import argparse
import functools
import os
import socketserver
import sys


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", required=True)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--port", type=int, required=True)
    a = ap.parse_args(argv)
    sys.path[:0] = [os.path.join(a.tree, "tests"), os.path.join(a.tree, "tools"), os.path.join(a.tree, "src")]
    from test_rehearsal_check import RuleServer
    handler = type("H", (RuleServer,), {"broken": set()})

    class Srv(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True
    Srv(("127.0.0.1", a.port), functools.partial(handler, directory=a.dir)).serve_forever()


if __name__ == "__main__":
    sys.exit(main())
