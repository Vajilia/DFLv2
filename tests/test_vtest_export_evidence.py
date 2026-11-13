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

    list_mode = 'deny'          # what the service-level listing answers to an authenticated key: deny | all | only_ours | error
    public_list = False         # an anonymous bucket listing succeeds
    corrupt_get = False

    def do_GET(self):
        self._record()
        authed = 'authorization' in self.headers
        bucket_root = self.path.rstrip('/') in ('/dfl-vtest-evidence-1', '')
        if self.path == '/':                                    # service-level listing of buckets
            if not authed or Fake.list_mode == 'deny':
                self.send_response(403)
                self.end_headers()
            elif Fake.list_mode == 'error':
                self.send_response(500)
                self.end_headers()
            else:
                names = ['dfl-vtest-evidence-1'] + (['production-backups-zzz', 'other-bucket'] if Fake.list_mode == 'all' else [])
                body = ('<ListAllMyBucketsResult><Buckets>' + ''.join('<Bucket><Name>%s</Name></Bucket>' % n for n in names) + '</Buckets></ListAllMyBucketsResult>').encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            return
        if bucket_root:
            if authed or Fake.public_list or Fake.public:
                self.send_response(200)
            else:
                self.send_response(403)
            self.end_headers()
            return
        if not authed and not Fake.public:
            self.send_response(403)
            self.end_headers()
            return
        item = Fake.store.get(self.path)
        if not item:
            self.send_response(404)
            self.end_headers()
            return
        body = item[0][:-1] + bytes([item[0][-1] ^ 1]) if Fake.corrupt_get else item[0]
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Upload(unittest.TestCase):
    def setUp(self):
        Fake.store, Fake.public, Fake.seen = {}, False, []
        Fake.list_mode, Fake.public_list, Fake.corrupt_get = 'deny', False, False
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


class Prove(Upload):
    """The first gate of the evidence transfer: the whole round trip, with a harmless archive, before any validation machine exists."""
    def prove(self, bucket='dfl-vtest-evidence-1', creds=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = X.prove(bucket, 'nyc3', workdir=self.tmp.name, endpoint='http://127.0.0.1:%d' % self.port, creds=creds or (lambda: (ACCESS, SECRET)))
        return rc, out.getvalue() + err.getvalue()

    def stored_archive(self):
        keys = [k for k in Fake.store if k.endswith('.tar.gz')]
        self.assertEqual(len(keys), 1, keys)
        return keys[0], Fake.store[keys[0]][0]

    def test_a_private_bucket_and_a_scoped_key_pass_every_check_and_each_is_reported(self):
        rc, text = self.prove()
        self.assertEqual(rc, 0, text)
        key, body = self.stored_archive()
        self.assertIn(hashlib.sha256(body).hexdigest(), text)
        for name in ('upload', 'read-back', 'authenticated download', 'anonymous object', 'anonymous listing', 'key scope'):
            self.assertIn(name, text.lower(), name)
        self.assertNotIn('FAIL', text)
        self.assertIn(os.path.basename(key), text, 'the owner is told which object to download')

    def test_the_archive_is_harmless_a_note_and_filler_and_nothing_from_the_machine(self):
        import tarfile
        self.prove()
        _, body = self.stored_archive()
        with tarfile.open(fileobj=io.BytesIO(body), mode='r:gz') as t:
            names = sorted(t.getnames())
        self.assertEqual(names, ['PROOF.txt', 'filler.bin'])
        self.assertLess(len(body), 2 * 1024 * 1024)

    def test_download_bytes_that_differ_are_a_failure(self):
        Fake.corrupt_get = True
        rc, text = self.prove()
        self.assertNotEqual(rc, 0)
        self.assertIn('FAIL', text)
        self.assertIn('authenticated download', text.lower())

    def test_a_publicly_readable_object_is_a_failure(self):
        Fake.public = True
        rc, text = self.prove()
        self.assertNotEqual(rc, 0)

    def test_a_publicly_listable_bucket_is_a_failure_even_if_the_object_is_private(self):
        Fake.public_list = True
        rc, text = self.prove()
        self.assertNotEqual(rc, 0)
        self.assertIn('listing', text.lower())

    def test_a_key_that_can_see_other_buckets_is_not_scoped_and_is_a_failure(self):
        Fake.list_mode = 'all'
        rc, text = self.prove()
        self.assertNotEqual(rc, 0)
        self.assertIn('not limited', text.lower())
        self.assertNotIn('production-backups-zzz', text, 'other bucket names are counted, never printed')
        self.assertNotIn('other-bucket', text)

    def test_a_listing_that_shows_only_the_evidence_bucket_passes(self):
        Fake.list_mode = 'only_ours'
        rc, text = self.prove()
        self.assertEqual(rc, 0, text)

    def test_an_inconclusive_scope_check_is_not_a_pass(self):
        Fake.list_mode = 'error'
        rc, text = self.prove()
        self.assertNotEqual(rc, 0)
        self.assertIn('not confirmed', text.lower())

    def test_a_refused_bucket_sends_nothing(self):
        rc, text = self.prove(bucket='dflv2-backups')
        self.assertNotEqual(rc, 0)
        self.assertEqual(Fake.seen, [])

    def test_no_credential_is_printed_or_written_or_sent_in_the_clear(self):
        before = set(os.listdir(self.tmp.name))
        rc, text = self.prove()
        self.assertNotIn(SECRET, text)
        self.assertNotIn(ACCESS, text)
        after = set(os.listdir(self.tmp.name))
        self.assertFalse([n for n in after - before if not n.startswith('transfer-proof-')], 'only the harmless test archive may appear in the work folder')
        for r in Fake.seen:
            self.assertNotIn(SECRET, repr(r))

    def test_it_never_deletes_and_sets_no_acl_and_only_prove_reads_the_service_listing(self):
        self.prove()
        self.assertFalse([r for r in Fake.seen if r['method'] not in ('PUT', 'HEAD', 'GET')])
        self.assertFalse([r for r in Fake.seen if 'x-amz-acl' in r['headers']])
        self.assertTrue([r for r in Fake.seen if r['path'] == '/'], 'prove checks the key scope')

    def test_a_plain_upload_never_reads_the_service_listing(self):
        self.go()
        self.assertFalse([r for r in Fake.seen if r['path'] == '/'])

    def test_missing_credentials_send_nothing(self):
        rc, text = self.prove(creds=lambda: ('', ''))
        self.assertNotEqual(rc, 0)
        self.assertEqual(Fake.seen, [])


class PastedCredentials(unittest.TestCase):
    """A terminal may add bracketed-paste marker codes around pasted text. They must never become part of a key."""
    def test_marker_codes_and_stray_control_characters_are_removed(self):
        self.assertEqual(X.clean_credential('\x1b[200~DO00ABCDEF\x1b[201~'), ('DO00ABCDEF', 2))
        self.assertEqual(X.clean_credential('  abc+/=\r\n'), ('abc+/=', 0))
        self.assertEqual(X.clean_credential('ab\x07c\x00d'), ('abcd', 2))
        self.assertEqual(X.clean_credential(''), ('', 0))

    def test_prove_uses_the_cleaned_credentials_and_reports_lengths_only(self):
        out = io.StringIO()
        asked = []

        class Quiet(Upload):
            pass
        srv = http.server.HTTPServer(('127.0.0.1', 0), Fake)
        Fake.store, Fake.public, Fake.seen = {}, False, []
        Fake.list_mode, Fake.public_list, Fake.corrupt_get = 'deny', False, False
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        with tempfile.TemporaryDirectory() as d, redirect_stdout(out):
            rc = X.prove('dfl-vtest-evidence-1', 'nyc3', workdir=d, endpoint='http://127.0.0.1:%d' % srv.server_address[1],
                         creds=lambda: ('\x1b[200~' + ACCESS + '\x1b[201~', '\x1b[200~' + SECRET + '\x1b[201~'))
        text = out.getvalue()
        self.assertEqual(rc, 0, text)
        self.assertIn('id %d characters' % len(ACCESS), text)
        self.assertIn('secret %d characters' % len(SECRET), text)
        self.assertIn('removed 4 stray', text)
        self.assertNotIn(SECRET, text)
        self.assertNotIn(ACCESS, text)
        for r in Fake.seen:
            self.assertNotIn('200~', repr(r))
            self.assertIn('Credential=' + ACCESS + '/', r['headers'].get('authorization', 'Credential=' + ACCESS + '/'))

    def test_both_commands_clean_whatever_they_are_given_and_the_prompt_stays_hidden_input(self):
        src = (ROOT / 'tools' / 'vtest' / 'export_evidence.py').read_text()
        for fn in ('def prove', 'def upload_many'):
            body = src.split(fn, 1)[1].split('\ndef ', 1)[0]
            self.assertIn('_clean_pair', body, fn)
        self.assertIn('getpass', src.split('def prompt_credentials', 1)[1].split('\ndef ', 1)[0])


class Standalone(unittest.TestCase):
    def test_the_tool_runs_from_a_folder_that_holds_only_itself(self):
        """The first gate runs on the owner's Chromebook, where the rest of the harness does not exist."""
        import shutil
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            shutil.copy(ROOT / 'tools' / 'vtest' / 'export_evidence.py', d)
            r = subprocess.run([sys.executable, '-I', str(Path(d, 'export_evidence.py')), 'prove', '--help'], capture_output=True, text=True, cwd=d, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('--bucket', r.stdout)
            r2 = subprocess.run([sys.executable, '-I', str(Path(d, 'export_evidence.py')), 'prove', '--bucket', 'dflv2-backups', '--region', 'nyc3'], capture_output=True, text=True, cwd=d, timeout=30, input='')
            self.assertNotEqual(r2.returncode, 0)
            self.assertIn('REFUSED', r2.stdout + r2.stderr)

    def test_the_default_archive_folder_is_the_harness_folder_when_the_harness_exists(self):
        self.assertEqual(os.path.realpath(X.default_export_dir()), os.path.realpath(C.EXPORT))


class Classification(unittest.TestCase):
    def test_the_exporter_measures_nothing_so_changing_it_repeats_no_stage(self):
        self.assertEqual(C.classify_why('tools/vtest/export_evidence.py')[:2], ('none', set()))


if __name__ == '__main__':
    unittest.main()
