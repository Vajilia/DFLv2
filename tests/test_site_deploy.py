"""The staging deployment package and its scripts (deploy/website/, tools/build_staging_package.py, tools/verify_deployment.py).

Nothing here touches a real server. The install script is run against a scratch directory tree with stand-ins for `caddy`, `systemctl` and the
network lookups, which record what they were asked to do. Proved: the package is verified before use; nothing changes without a recent rollback
copy and a DNS record that points at the machine; the production site block is never edited (the Caddyfile only gains an import line); Caddy is
reloaded only after `caddy validate` passes and the previous configuration is restored byte for byte when it does not; installing twice is safe;
the outside-in verifier passes a good site, fails a damaged one, and notices a changed production site.
What this cannot prove: that the real Caddy accepts the generated block and that Let's Encrypt issues the certificate. Those are the first steps on the server.
"""
import hashlib
import json
import os
import shutil
import socketserver
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import feedlab  # noqa: E402
from dflsite import publish  # noqa: E402

BUILD = os.path.join(ROOT, "tools", "build_staging_package.py")
VERIFY = os.path.join(ROOT, "tools", "verify_deployment.py")
DEPLOY = os.path.join(ROOT, "deploy", "website")

PRODUCTION_BLOCK = "diamondfootballleague.com {\n\troot * /var/www/dfl\n\tfile_server\n}\n"


def write(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    if mode:
        os.chmod(path, mode)


def read(path):
    with open(path) as f:
        return f.read()


class Package(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _, out, _ = feedlab.stages()
        cls.tmp = tempfile.mkdtemp(prefix="dflpkgtest")
        cls.seed = os.path.join(cls.tmp, "seed")
        publish.publish(out["bowl"], cls.seed)
        r = subprocess.run([sys.executable, "-I", BUILD, "--seed-feed", cls.seed, "--out", os.path.join(cls.tmp, "out"), "--from-worktree"],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        cls.info = json.loads(r.stdout)
        cls.unpacked = os.path.join(cls.tmp, "unpacked")
        os.makedirs(cls.unpacked)
        with tarfile.open(cls.info["package"]) as t:
            t.extractall(cls.unpacked, filter="data")
        (cls.pkg,) = [os.path.join(cls.unpacked, n) for n in os.listdir(cls.unpacked)]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_package_holds_the_code_a_verified_seed_feed_and_checksums_and_nothing_else(self):
        top = sorted(os.listdir(self.pkg))
        self.assertEqual(top, ["PACKAGE.json", "RUNBOOK.md", "SHA256SUMS", "repo", "seed-feed"])
        meta = json.loads(read(os.path.join(self.pkg, "PACKAGE.json")))
        self.assertEqual(meta["seed_release"], publish.read_release(self.seed)["manifest.json"]["release"])
        self.assertEqual(meta["commit"], subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip())
        r = subprocess.run(["sha256sum", "-c", "SHA256SUMS", "--quiet"], cwd=self.pkg, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(publish.read_release(os.path.join(self.pkg, "seed-feed"))["manifest.json"]["release"], meta["seed_release"])
        names = [os.path.join(d, n) for d, _, ns in os.walk(self.pkg) for n in ns]
        self.assertFalse([n for n in names if n.endswith((".db", ".archive", ".pem", ".key", ".env"))], "no league file or secret belongs in the package")
        self.assertFalse(any("/.git/" in n for n in names))
        for needed in ("repo/deploy/website/install_staging.sh", "repo/tools/seed_feed.py", "repo/tools/verify_deployment.py", "repo/site/index.html",
                       "repo/src/dflsite/publish.py"):
            self.assertTrue(os.path.exists(os.path.join(self.pkg, needed)), needed)

    def test_a_committed_tree_is_required_unless_it_is_said_otherwise(self):
        if not subprocess.run(["git", "-C", ROOT, "status", "--porcelain"], capture_output=True, text=True).stdout.strip():
            self.skipTest("the working tree is clean here, so there is nothing to refuse")
        r = subprocess.run([sys.executable, "-I", BUILD, "--seed-feed", self.seed, "--out", os.path.join(self.tmp, "o2")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("uncommitted", r.stderr)

    def test_a_damaged_seed_feed_cannot_be_packaged(self):
        bad = os.path.join(self.tmp, "badseed")
        shutil.copytree(self.seed, bad)
        rel = publish.read_release(bad)["manifest.json"]["release"]
        with open(os.path.join(bad, "releases", rel, "teams.json"), "ab") as f:
            f.write(b" ")
        r = subprocess.run([sys.executable, "-I", BUILD, "--seed-feed", bad, "--out", os.path.join(self.tmp, "o3"), "--from-worktree"], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)


class Install(unittest.TestCase):
    """install_staging.sh against a scratch tree, with recording stand-ins for caddy / systemctl and the address lookups."""

    @classmethod
    def setUpClass(cls):
        Package.setUpClass.__func__(Package)
        cls.P = Package

    @classmethod
    def tearDownClass(cls):
        Package.tearDownClass.__func__(Package)

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="dflinst")
        self.log = os.path.join(self.d, "calls.log")
        self.bin = os.path.join(self.d, "bin")
        os.makedirs(self.bin)
        # stand-ins
        write(os.path.join(self.bin, "caddy"), '#!/bin/sh\necho "caddy $*" >> "%s"\n[ -f "%s/caddy_fails" ] && { echo "bad config" >&2; exit 1; }\nexit 0\n' % (self.log, self.d), 0o755)
        write(os.path.join(self.bin, "systemctl"), '#!/bin/sh\necho "systemctl $*" >> "%s"\n[ -f "%s/reload_fails" ] && exit 1\nexit 0\n' % (self.log, self.d), 0o755)
        write(os.path.join(self.bin, "getent"), '#!/bin/sh\n[ -f "%s/no_dns" ] && exit 2\n[ -f "%s/wrong_dns" ] && { echo "198.51.100.7 STREAM $2"; exit 0; }\necho "203.0.113.9 STREAM $2"\n' % (self.d, self.d), 0o755)
        write(os.path.join(self.bin, "ip"), '#!/bin/sh\necho "2: eth0    inet 203.0.113.9/24 brd 203.0.113.255 scope global eth0"\n', 0o755)
        write(os.path.join(self.bin, "curl"), '#!/bin/sh\nexit 22\n', 0o755)
        self.base = os.path.join(self.d, "srv")
        self.caddy_dir = os.path.join(self.d, "etc_caddy")
        self.roll = os.path.join(self.d, "rollback")
        os.makedirs(self.caddy_dir)
        os.makedirs(os.path.join(self.roll, "20261030T000000Z"))
        self.caddyfile = os.path.join(self.caddy_dir, "Caddyfile")
        write(self.caddyfile, "{\n\temail ops@example.com\n}\n" + PRODUCTION_BLOCK)
        self.original = read(self.caddyfile)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def run_install(self, pkg=None, **env):
        e = dict(os.environ, PATH=self.bin + os.pathsep + os.environ["PATH"], BASE=self.base, CADDY_DIR=self.caddy_dir, ROLLBACK_ROOT=self.roll,
                 SERVICE_USER="root", ALLOW_NON_ROOT="1")
        e.update(env)
        return subprocess.run(["sh", os.path.join(DEPLOY, "install_staging.sh"), pkg or self.P.pkg], capture_output=True, text=True, env=e)

    def calls(self):
        return read(self.log).splitlines() if os.path.exists(self.log) else []

    def test_a_good_install_adds_one_block_and_one_import_and_nothing_else_to_caddy(self):
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        snippet = read(os.path.join(self.caddy_dir, "conf.d", "dflv2-preview.caddy"))
        self.assertIn("preview.diamondfootballleague.com {", snippet)
        self.assertNotIn("__HOST__", snippet)
        self.assertNotIn("diamondfootballleague.com {\n\troot * /var/www", snippet)
        now = read(self.caddyfile)
        self.assertTrue(now.startswith(self.original), "the existing Caddyfile text must be untouched; only a line is appended")
        self.assertEqual(now[len(self.original):].count("import "), 1)
        calls = self.calls()
        v = [i for i, c in enumerate(calls) if c.startswith("caddy validate")]
        rl = [i for i, c in enumerate(calls) if c == "systemctl reload caddy"]
        self.assertTrue(v and rl and v[-1] < rl[0], calls)                                  # validated, then reloaded
        self.assertFalse([c for c in calls if "restart" in c or "stop" in c], calls)       # never restarted or stopped
        self.assertTrue(os.path.islink(os.path.join(self.base, "repo")))
        self.assertTrue(os.path.islink(os.path.join(self.base, "site", "current")))
        got = publish.read_release(os.path.join(self.base, "feed"))
        self.assertEqual(got["manifest.json"]["release"], json.loads(read(os.path.join(self.P.pkg, "PACKAGE.json")))["seed_release"])
        self.assertTrue(os.path.exists(os.path.join(self.base, "site", "current", "index.html")))
        self.assertTrue([n for n in os.listdir(self.caddy_dir) if ".dfl-staging." in n and n.endswith(".bak")], "a backup of the Caddyfile is kept")

    def test_installing_twice_is_safe(self):
        self.assertEqual(self.run_install().returncode, 0)
        after_first = read(self.caddyfile)
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(read(self.caddyfile), after_first, "the import line is added once")
        self.assertEqual(publish.read_release(os.path.join(self.base, "feed"))["manifest.json"]["release"],
                         json.loads(read(os.path.join(self.P.pkg, "PACKAGE.json")))["seed_release"])

    def test_a_configuration_caddy_rejects_is_undone_and_never_reloaded(self):
        open(os.path.join(self.d, "caddy_fails"), "w").close()
        r = self.run_install()
        self.assertEqual(r.returncode, 5, r.stdout + r.stderr)
        self.assertEqual(read(self.caddyfile), self.original, "the Caddyfile must be restored byte for byte")
        self.assertFalse(os.path.exists(os.path.join(self.caddy_dir, "conf.d", "dflv2-preview.caddy")))
        self.assertNotIn("systemctl reload caddy", self.calls())

    def test_a_failed_reload_restores_the_previous_configuration(self):
        open(os.path.join(self.d, "reload_fails"), "w").close()
        r = self.run_install()
        self.assertEqual(r.returncode, 6, r.stdout + r.stderr)
        self.assertEqual(read(self.caddyfile), self.original)

    def test_nothing_changes_without_a_recent_rollback_copy(self):
        shutil.rmtree(self.roll)
        r = self.run_install()
        self.assertEqual(r.returncode, 3)
        self.assertEqual(read(self.caddyfile), self.original)
        self.assertFalse(os.path.exists(self.base))

    def test_nothing_changes_while_the_dns_does_not_point_here(self):
        open(os.path.join(self.d, "wrong_dns"), "w").close()
        r = self.run_install()
        self.assertEqual(r.returncode, 4)
        self.assertIn("resolves to", r.stderr)
        self.assertFalse(os.path.exists(self.base))
        os.remove(os.path.join(self.d, "wrong_dns"))
        open(os.path.join(self.d, "no_dns"), "w").close()
        self.assertEqual(self.run_install().returncode, 4)
        self.assertEqual(read(self.caddyfile), self.original)

    def test_a_tampered_package_is_refused(self):
        bad = os.path.join(self.d, "pkg")
        shutil.copytree(self.P.pkg, bad)
        with open(os.path.join(bad, "repo", "site", "index.html"), "a") as f:
            f.write("<!-- tampered -->")
        r = self.run_install(bad)
        self.assertEqual(r.returncode, 2)
        self.assertFalse(os.path.exists(self.base))

    def test_uninstall_removes_only_the_staging_block(self):
        self.assertEqual(self.run_install().returncode, 0)
        env = dict(os.environ, PATH=self.bin + os.pathsep + os.environ["PATH"], CADDY_DIR=self.caddy_dir, ROLLBACK_ROOT=self.roll)
        r = subprocess.run(["sh", os.path.join(DEPLOY, "uninstall_staging.sh")], capture_output=True, text=True, env=env, cwd=self.d)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.caddy_dir, "conf.d", "dflv2-preview.caddy")))
        self.assertTrue(read(self.caddyfile).startswith(self.original))
        self.assertTrue(os.listdir(os.path.join(self.roll, "removed")), "the removed block is kept")
        open(os.path.join(self.d, "caddy_fails"), "w").close()                                  # a new install that Caddy rejects is undone as well
        self.assertEqual(self.run_install().returncode, 5)

    def test_every_script_parses(self):
        for n in sorted(os.listdir(DEPLOY)):
            if n.endswith(".sh"):
                r = subprocess.run(["sh", "-n", os.path.join(DEPLOY, n)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, f"{n}: {r.stderr}")


class Verifier(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import preview_site
        _, out, _ = feedlab.stages()
        cls.out = out
        cls.tmp = tempfile.mkdtemp(prefix="dflver")
        cls.feed = os.path.join(cls.tmp, "feed")
        publish.publish(out["bowl"], cls.feed)
        preview_site.Handler.feed_root = cls.feed
        preview_site.Handler.site_root = preview_site.SITE
        socketserver.TCPServer.allow_reuse_address = True
        cls.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), preview_site.Handler)
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def verify(self, *args):
        return subprocess.run([sys.executable, "-I", VERIFY, *args], capture_output=True, text=True)

    def test_a_good_site_passes_and_the_missing_map_is_only_a_warning(self):
        r = self.verify(self.url, "--profile", "local")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("WARN  the official map is not installed yet", r.stdout)
        self.assertIn("all 59 feed files match" if "59" in r.stdout else "feed files match", r.stdout)

    def test_requiring_the_map_fails_without_it(self):
        self.assertEqual(self.verify(self.url, "--profile", "local", "--require-map").returncode, 1)

    def test_a_damaged_feed_file_fails(self):
        rel = publish.read_release(self.feed)["manifest.json"]["release"]
        target = os.path.join(self.feed, "releases", rel, "standings.json")
        with open(target, "rb") as f:
            good = f.read()
        try:
            with open(target, "wb") as f:
                f.write(good.replace(b"{", b"[", 1))
            r = self.verify(self.url, "--profile", "local")
        finally:
            with open(target, "wb") as f:
                f.write(good)
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL  all", r.stdout)

    def test_a_feed_with_a_hidden_field_fails_the_public_check(self):
        rel = publish.read_release(self.feed)["manifest.json"]["release"]
        base = os.path.join(self.feed, "releases", rel)
        names = ("manifest.json", "rosters/ALA.json")
        saved = {n: open(os.path.join(base, n), "rb").read() for n in names}
        try:
            roster = json.loads(saved["rosters/ALA.json"])
            roster["players"][0]["ovr"] = 99
            raw = json.dumps(roster, sort_keys=True, separators=(",", ":")).encode()
            m = json.loads(saved["manifest.json"])
            m["files"]["rosters/ALA.json"].update(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
            mraw = json.dumps(m, sort_keys=True, separators=(",", ":")).encode()
            with open(os.path.join(base, "rosters/ALA.json"), "wb") as f:
                f.write(raw)
            with open(os.path.join(base, "manifest.json"), "wb") as f:
                f.write(mraw)
            ptr_path = os.path.join(self.feed, "latest.json")
            ptr_saved = open(ptr_path, "rb").read()
            ptr = json.loads(ptr_saved)
            ptr["manifest_sha256"] = hashlib.sha256(mraw).hexdigest()
            with open(ptr_path, "w") as f:
                json.dump(ptr, f)
            r = self.verify(self.url, "--profile", "local")
        finally:
            for n, b in saved.items():
                with open(os.path.join(base, n), "wb") as f:
                    f.write(b)
            with open(os.path.join(self.feed, "latest.json"), "wb") as f:
                f.write(ptr_saved)
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL  the served feed passes the public-data check", r.stdout)

    def test_production_baseline_proves_the_live_site_is_unchanged_until_it_changes(self):
        import preview_site
        prod_dir = os.path.join(self.tmp, "prod")
        write(os.path.join(prod_dir, "index.html"), "<html>the old site</html>")
        import http.server
        handler = type("H", (http.server.SimpleHTTPRequestHandler,), {"log_message": lambda *a: None,
                                                                    "__init__": lambda s, *a, **k: http.server.SimpleHTTPRequestHandler.__init__(s, *a, directory=prod_dir, **k)})
        srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        prod = f"http://127.0.0.1:{srv.server_address[1]}/"
        base = os.path.join(self.tmp, "baseline.json")
        try:
            self.assertEqual(self.verify("--production", prod, "--save-baseline", base).returncode, 0)
            r = self.verify(self.url, "--profile", "local", "--production", prod, "--baseline", base)
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertIn("the production site is unchanged", r.stdout)
            write(os.path.join(prod_dir, "index.html"), "<html>REPLACED</html>")
            r = self.verify(self.url, "--profile", "local", "--production", prod, "--baseline", base)
            self.assertEqual(r.returncode, 1)
            self.assertIn("FAIL  the production site is unchanged", r.stdout)
        finally:
            srv.shutdown()
            srv.server_close()
