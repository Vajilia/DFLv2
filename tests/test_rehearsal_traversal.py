"""Path traversal and the private canary (tools/rehearsal_check.py `http --canary`). The stand-in servers mirror the deployment's layout:
    R/feed/  (served at /feed/)    R/site/  (served at /)    R/private/store/CANARY.txt  (never served)
The SAFE stand-in cleans and confines paths the way a correct file server does; the CAREFREE one decodes twice and joins naively, as a bad configuration would.
This proves the probe catches a leak. That the real Caddy block behaves like the SAFE one is proven by the installer's own check on the server."""
import functools
import http.server
import os
import posixpath
import socketserver
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import unquote
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))
import rehearsal_check as RC

TOKEN = 'DFL-REHEARSAL-CANARY-0123456789abcdef'


class StandIn(http.server.BaseHTTPRequestHandler):
    careless = False
    base = None

    def log_message(self, *a):
        pass

    def resolve(self):
        raw = self.path.split('?')[0]
        path = unquote(unquote(raw)) if self.careless else unquote(raw)
        if self.careless:
            rel = path[len('/feed/'):] if path.startswith('/feed/') else path.lstrip('/')
            root = os.path.join(self.base, 'feed' if path.startswith('/feed/') else 'site')
            return os.path.normpath(root + '/' + rel)                                    # no confinement: '..' climbs out
        clean = posixpath.normpath(path)
        if clean.startswith('/feed/'):
            root, rel = os.path.join(self.base, 'feed'), clean[len('/feed/'):]
        else:
            root, rel = os.path.join(self.base, 'site'), clean.lstrip('/')
        full = os.path.realpath(os.path.join(root, rel))
        return full if full == os.path.realpath(root) or full.startswith(os.path.realpath(root) + os.sep) else None

    def do_GET(self):
        try:
            full = self.resolve()
        except Exception:
            full = None
        if full and os.path.isfile(full):
            body = open(full, 'rb').read()
            self.send_response(200)
        else:
            body = b'not found'
            self.send_response(404)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.send_error(405)


def serve(base, careless=False):
    h = type('H', (StandIn,), {'careless': careless, 'base': base})
    srv = socketserver.ThreadingTCPServer(('127.0.0.1', 0), h)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class Traversal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__('shutil').rmtree(self.tmp, ignore_errors=True))
        for d in ('feed/broadcast', 'site', 'private/store', 'monitor'):
            os.makedirs(os.path.join(self.tmp, d))
        open(os.path.join(self.tmp, 'feed', 'status.json'), 'w').write('{}')
        open(os.path.join(self.tmp, 'site', 'index.html'), 'w').write('<script src="a.js"></script>')
        self.canary = os.path.join(self.tmp, 'private', 'store', 'CANARY.txt')
        open(self.canary, 'w').write(TOKEN + '\n')
        open(os.path.join(self.tmp, 'private', 'store', 'plan.json'), 'w').write('{"format": "dfl-broadcast-plan"}')

    def probe(self, careless):
        srv = serve(self.tmp, careless)
        try:
            return RC.traversal_probe('http://127.0.0.1:%d' % srv.server_address[1], self.canary)
        finally:
            srv.shutdown()

    def test_the_paths_cover_the_encodings_that_matter(self):
        ps = RC.traversal_paths()
        for needle in ('%2e%2e/', '..%2f', '%252e%252e%252f', '..%5c', '%c0%ae%c0%ae/', '..%00/', '..;/', '....//', '/feed/../', '//private/store/CANARY.txt'):
            self.assertTrue(any(needle in p for p in ps), needle)
        self.assertGreater(len(ps), 200)

    def test_a_confining_server_leaks_nothing(self):
        self.assertEqual(self.probe(False), [])

    def test_a_careless_server_is_caught_with_the_paths_that_worked(self):
        bad = self.probe(True)
        self.assertTrue(bad)
        self.assertTrue(any('%252e' in p or '..' in p for p, _ in bad), bad[:3])

    def test_http_rules_fail_on_a_leak_and_on_a_missing_canary(self):
        for careless, want_fail in ((False, False), (True, True)):
            srv = serve(self.tmp, careless)
            try:
                res = RC.http_rules('http://127.0.0.1:%d' % srv.server_address[1], canary=self.canary)
            finally:
                srv.shutdown()
            lines = [(ok, w) for ok, w in res if 'traversal' in w or 'canary' in w]
            self.assertTrue(lines)
            self.assertEqual(any(not ok for ok, _ in lines), want_fail, lines)
        srv = serve(self.tmp)
        try:
            res = RC.http_rules('http://127.0.0.1:%d' % srv.server_address[1], canary=os.path.join(self.tmp, 'nope.txt'))
        finally:
            srv.shutdown()
        self.assertTrue(any((not ok) and 'proves nothing' in w for ok, w in res))


if __name__ == '__main__':
    unittest.main()
