"""The private evidence-transfer method (tools/vtest/export_evidence.py). Written before the module existed.

Why: console transcription of archives consumed whole sessions (the earlier sessions) and could not be done twice. The method here moves one verified archive from the disposable machine to a PRIVATE,
evidence-only bucket the owner created, with a key that can reach that bucket only; the owner downloads it with the provider's own web page and attaches it in chat, and the receiver checks its SHA-256
against the hash the machine printed. What the tool must never do: write a credential anywhere, print one, upload anywhere but an evidence bucket, make anything public, list or delete anything.
Nothing in these tests contacts a real service: a local fake server stands in for the object store.
"""
import hashlib
import http.server
import io
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools' / 'vtest'))
import common as C            # noqa: E402
import export_evidence as X   # noqa: E402

ACCESS, SECRET = 'AKIDEXAMPLEACCESS12345', 'wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY'


class SigV4(unittest.TestCase):
    def test_the_published_get_vanilla_vector(self):
        """AWS's own signature test suite, case get-vanilla: this signature is what a correct implementation must produce."""
        h = X.sign('GET', 'example.amazonaws.com', '/', '', {}, hashlib.sha256(b'').hexdigest(), 'AKIDEXAMPLE', 'wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY', 'us-east-1', 'service', '20150830T123600Z', include_content_sha=False)
        self.assertIn('Signature=5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31', h['Authorization'])
        self.assertIn('Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request', h['Authorization'])
        self.assertIn('SignedHeaders=host;x-amz-date', h['Authorization'])

    def test_the_signature_depends_on_every_part(self):
        base = ('PUT', 'b.nyc3.digitaloceanspaces.com', '/evidence/x.tar.gz', '', {}, hashlib.sha256(b'a').hexdigest(), ACCESS, SECRET, 'nyc3', 's3', '20261112T120000Z')
        sig = lambda args: X.sign(*args)['Authorization'].split('Signature=')[1]
        a = sig(base)
        for i, alt in ((0, 'GET'), (1, 'c.nyc3.digitaloceanspaces.com'), (2, '/evidence/y.tar.gz'), (5, hashlib.sha256(b'b').hexdigest()), (7, SECRET + 'x'), (8, 'ams3'), (10, '20261112T120001Z')):
            args = list(base)
            args[i] = alt
            self.assertNotEqual(sig(tuple(args)), a, i)


class Guards(unittest.TestCase):
    def test_only_an_evidence_bucket_is_accepted(self):
        for ok in ('dfl-vtest-evidence-2026', 'dfl-vtest-evidence-a'):
            X.check_bucket(ok)
        for bad in ('dfl-broadcast', 'dflv2-backups', 'dfl-vtest-evidence', 'dfl-vtest-evidence-', 'other-dfl-vtest-evidence-1', '', 'dfl-vtest-evidence-1/../x', 'dfl-vtest-evidence-A_B'):
            with self.assertRaises(X.Refused, msg=bad):
                X.check_bucket(bad)

    def test_only_a_region_name_is_accepted(self):
        X.check_region('nyc3')
        for bad in ('', 'nyc3.evil.example', 'nyc3/', 'a b'):
            with self.assertRaises(X.Refused, msg=bad):
                X.check_region(bad)

    def test_only_files_in_the_harness_export_folder_and_of_sane_size_are_uploaded(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d, 'evidence-x.tar.gz')
            f.write_bytes(b'x')
            with self.assertRaises(X.Refused):
                X.check_archive(str(f), export_dir=str(Path(d, 'elsewhere')))
            X.check_archive(str(f), export_dir=d)
            with self.assertRaises(X.Refused):
                X.check_archive(str(Path(d, 'missing.tar.gz')), export_dir=d)
            big = Path(d, 'big.tar.gz')
            with open(big, 'wb') as fh:
                fh.truncate(X.MAX_BYTES + 1)
            with self.assertRaises(X.Refused):
                X.check_archive(str(big), export_dir=d)
            with self.assertRaises(X.Refused):
                X.check_archive(str(Path(d, 'notes.txt')), export_dir=d)

    def test_the_object_key_is_under_a_fixed_prefix_and_has_no_path_tricks(self):
        self.assertEqual(X.object_key('evidence-run-1.tar.gz'), 'evidence/evidence-run-1.tar.gz')
        with self.assertRaises(X.Refused):
            X.object_key('../x.tar.gz')
        with self.assertRaises(X.Refused):
            X.object_key('a/b.tar.gz')


class Fake(http.server.BaseHTTPRequestHandler):
    store = {}
    public = False
    seen = []

    def log_message(self, *a):
        pass

    def _record(self, body=b''):
        Fake.seen.append({'method': self.command, 'path': self.path, 'headers': {k.lower(): v for k, v in self.headers.items()}, 'body_len': len(body)})

    def do_PUT(self):
        n = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(n)
        self._record(body)
        if hashlib.sha256(body).hexdigest() != self.headers.get('x-amz-content-sha256'):
            self.send_response(400)
            self.end_headers()
            return
        Fake.store[self.path] = (body, {k.lower(): v for k, v in self.headers.items() if k.lower().startswith('x-amz-meta-')})
        self.send_response(200)
        self.send_header('ETag', '"%s"' % hashlib.md5(body).hexdigest())
        self.end_headers()

    def do_HEAD(self):
        self._record()
        item = Fake.store.get(self.path)
        if item is None or 'authorization' not in self.headers:
            self.send_response(404 if item is None else 403)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header('Content-Length', str(len(item[0])))
        for k, v in item[1].items():
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self):
        self._record()
        if 'authorization' not in self.headers and not Fake.public:
            self.send_response(403)
            self.end_headers()
            return
        item = Fake.store.get(self.path)
        self.send_response(200 if item else 404)
        self.end_headers()


class Upload(unittest.TestCase):
    def setUp(self):
        Fake.store, Fake.public, Fake.seen = {}, False, []
        self.srv = http.server.HTTPServer(('127.0.0.1', 0), Fake)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.archive = Path(self.tmp.name, 'evidence-run-1.tar.gz')
        self.archive.write_bytes(os.urandom(5000))
        self.sha = hashlib.sha256(self.archive.read_bytes()).hexdigest()

    def go(self, access=ACCESS, secret=SECRET, bucket='dfl-vtest-evidence-1'):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = X.upload(str(self.archive), bucket, 'nyc3', export_dir=self.tmp.name, endpoint='http://127.0.0.1:%d' % self.port, creds=lambda: (access, secret))
        return rc, out.getvalue() + err.getvalue()

    def test_a_private_upload_is_verified_and_prints_the_hash(self):
        rc, text = self.go()
        self.assertEqual(rc, 0, text)
        self.assertIn(self.sha, text)
        path = '/dfl-vtest-evidence-1/evidence/evidence-run-1.tar.gz'
        body, meta = Fake.store[path]
        self.assertEqual(hashlib.sha256(body).hexdigest(), self.sha)
        self.assertEqual(meta.get('x-amz-meta-sha256'), self.sha)
        self.assertIn('/dfl-vtest-evidence-1/evidence/evidence-run-1.tar.gz.sha256', Fake.store)

    def test_it_only_ever_puts_and_reads_back_never_lists_or_deletes_and_sets_no_public_acl(self):
        self.go()
        self.assertTrue({r['method'] for r in Fake.seen} <= {'PUT', 'HEAD', 'GET'})
        self.assertFalse([r for r in Fake.seen if r['method'] not in ('PUT', 'HEAD', 'GET')])
        self.assertFalse([r for r in Fake.seen if 'x-amz-acl' in r['headers']], 'no ACL header: the bucket default (private) applies')
        self.assertFalse([r for r in Fake.seen if r['method'] == 'GET' and 'authorization' in r['headers']], 'the only GET is the unauthenticated privacy check')

    def test_an_object_that_anyone_can_read_is_reported_as_a_failure(self):
        Fake.public = True
        rc, text = self.go()
        self.assertNotEqual(rc, 0)
        self.assertIn('publicly readable', text)

    def test_no_credential_is_printed_or_written(self):
        before = set(os.listdir(self.tmp.name))
        rc, text = self.go()
        self.assertNotIn(SECRET, text)
        self.assertNotIn(ACCESS, text)
        self.assertEqual(set(os.listdir(self.tmp.name)), before, 'nothing is written beside the archive')
        for r in Fake.seen:
            self.assertNotIn(SECRET, repr(r))

    def test_a_refused_bucket_sends_nothing(self):
        rc, text = self.go(bucket='dfl-broadcast')
        self.assertNotEqual(rc, 0)
        self.assertEqual(Fake.seen, [])

    def test_missing_credentials_send_nothing_and_say_what_to_do(self):
        rc, text = self.go(access='', secret='')
        self.assertNotEqual(rc, 0)
        self.assertEqual(Fake.seen, [])

    def test_a_server_that_rejects_the_upload_is_a_failure_not_a_pass(self):
        orig = Fake.do_PUT
        Fake.do_PUT = lambda self: (self.send_response(403), self.end_headers())
        self.addCleanup(setattr, Fake, 'do_PUT', orig)
        rc, text = self.go()
        self.assertNotEqual(rc, 0)

    def test_the_default_credential_prompt_never_reads_the_environment_or_a_file(self):
        src = (ROOT / 'tools' / 'vtest' / 'export_evidence.py').read_text()
        self.assertIn('getpass', src)
        self.assertNotIn('os.environ', src.split('def prompt_credentials', 1)[1].split('\ndef ', 1)[0])
        self.assertNotIn("open(", src.split('def prompt_credentials', 1)[1].split('\ndef ', 1)[0])


class SeveralArchives(Upload):
    def test_several_archives_in_one_run_ask_for_the_credentials_once(self):
        second = Path(self.tmp.name, 'evidence-run-2.tar.gz')
        second.write_bytes(os.urandom(3000))
        asked = []
        out = io.StringIO()
        with redirect_stdout(out):
            rc = X.upload_many([str(self.archive), str(second)], 'dfl-vtest-evidence-1', 'nyc3', export_dir=self.tmp.name,
                               endpoint='http://127.0.0.1:%d' % self.port, creds=lambda: asked.append(1) or (ACCESS, SECRET))
        self.assertEqual(rc, 0, out.getvalue())
        self.assertEqual(len(asked), 1)
        self.assertIn('/dfl-vtest-evidence-1/evidence/evidence-run-2.tar.gz', Fake.store)
        self.assertIn(hashlib.sha256(second.read_bytes()).hexdigest(), out.getvalue())

    def test_a_bad_file_in_the_list_sends_nothing_at_all(self):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = X.upload_many([str(self.archive), str(Path(self.tmp.name, 'nope.tar.gz'))], 'dfl-vtest-evidence-1', 'nyc3', export_dir=self.tmp.name,
                               endpoint='http://127.0.0.1:%d' % self.port, creds=lambda: (ACCESS, SECRET))
        self.assertNotEqual(rc, 0)
        self.assertEqual(Fake.seen, [])

    def test_one_failure_does_not_hide_behind_a_later_success(self):
        second = Path(self.tmp.name, 'evidence-run-2.tar.gz')
        second.write_bytes(b'y' * 100)
        Fake.public = True
        out = io.StringIO()
        with redirect_stdout(out):
            rc = X.upload_many([str(self.archive), str(second)], 'dfl-vtest-evidence-1', 'nyc3', export_dir=self.tmp.name,
                               endpoint='http://127.0.0.1:%d' % self.port, creds=lambda: (ACCESS, SECRET))
        self.assertNotEqual(rc, 0)


class Classification(unittest.TestCase):
    def test_the_exporter_measures_nothing_so_changing_it_repeats_no_stage(self):
        self.assertEqual(C.classify_why('tools/vtest/export_evidence.py')[:2], ('none', set()))


if __name__ == '__main__':
    unittest.main()
