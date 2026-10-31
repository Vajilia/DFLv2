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

    def test_the_seed_is_installed_by_an_unprivileged_user_when_the_package_sits_in_a_private_directory(self):
        """Found at the first real deployment: the package is unpacked under /root (mode 700); the service user must not need to enter it."""
        import pwd
        if os.geteuid() != 0:
            self.skipTest("needs root to switch user")
        user = "dflseedtest"
        made = subprocess.run(["useradd", "--system", "--shell", "/usr/sbin/nologin", user], capture_output=True).returncode == 0
        if not made:
            self.skipTest("cannot create a throwaway user")
        self.addCleanup(lambda: subprocess.run(["userdel", user], capture_output=True))
        private = tempfile.mkdtemp(prefix="dflprivate")                      # mkdtemp makes it mode 700, as /root is
        try:
            pkg = os.path.join(private, "pkg")
            shutil.copytree(self.P.pkg, pkg)
            os.chmod(self.d, 0o755)                                          # the scratch install tree itself must be enterable; the PACKAGE directory is not
            r = self.run_install(pkg=pkg, SERVICE_USER=user)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            latest = os.path.join(self.base, "feed", "latest.json")
            self.assertTrue(os.path.exists(latest))
            self.assertEqual(pwd.getpwuid(os.stat(latest).st_uid).pw_name, user)
            self.assertFalse(os.path.exists(os.path.join(self.base, ".seed-incoming")), "the staged seed copy is removed")
            self.assertEqual(stat.S_IMODE(os.stat(private).st_mode), 0o700, "the private directory was not loosened")
        finally:
            shutil.rmtree(private, ignore_errors=True)

    def _head(self):
        sha = subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        for f in ("tools/verify_revision.py", "deploy/website/deploy_pinned.sh"):
            if subprocess.run(["git", "-C", ROOT, "cat-file", "-e", f"HEAD:{f}"], capture_output=True).returncode != 0:
                self.skipTest("HEAD does not contain the pinned-deploy tools yet (commit them first)")
        return sha

    def _pinned(self, arg, **env):
        e = dict(os.environ, PATH=self.bin + os.pathsep + os.environ["PATH"], BASE=self.base, CADDY_DIR=self.caddy_dir, ROLLBACK_ROOT=self.roll,
                 SERVICE_USER="root", ALLOW_NON_ROOT="1", REPO_URL=ROOT, SRC=os.path.join(self.d, "src"), PKGS=os.path.join(self.d, "pkgs"))
        e.update(env)
        return subprocess.run(["sh", os.path.join(DEPLOY, "deploy_pinned.sh"), arg], capture_output=True, text=True, env=e)

    def test_a_pinned_deploy_installs_exactly_that_commit_from_git_alone_and_the_check_notices_any_change(self):
        sha = self._head()
        r = self._pinned(sha)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ALL MATCH", r.stdout)
        rec = json.load(open(os.path.join(self.base, "DEPLOYED_REVISION.json")))
        self.assertEqual(rec["commit"], sha)
        verify = [sys.executable, "-I", os.path.join(ROOT, "tools", "verify_revision.py"), "--commit", sha, "--repo-url", ROOT, "--base", self.base,
                  "--snippet", os.path.join(self.caddy_dir, "conf.d", "dflv2-preview.caddy")]
        self.assertEqual(subprocess.run(verify, capture_output=True, text=True).returncode, 0)
        with open(os.path.join(self.base, "repo", "tools", "validate.py"), "a") as f:                  # any edit on the server is noticed
            f.write("# tampered\n")
        r2 = subprocess.run(verify, capture_output=True, text=True)
        self.assertEqual(r2.returncode, 1, r2.stdout)
        self.assertIn("FAIL the installed code is the commit's code", r2.stdout)
        with open(os.path.join(self.caddy_dir, "conf.d", "dflv2-preview.caddy"), "a") as f:
            f.write("# hand edit\n")
        self.assertIn("FAIL the Caddy block", subprocess.run(verify, capture_output=True, text=True).stdout)

    def test_a_pinned_deploy_refuses_a_branch_name_or_an_abbreviation(self):
        self._head()
        for bad in ("main", "audit/completion-plan", "5a6bf27"):
            r = self._pinned(bad)
            self.assertEqual(r.returncode, 2, bad)
            self.assertIn("40-character", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.base, "repo")), "nothing was installed")

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


class FoundAtDeployment(unittest.TestCase):
    """Regressions for what the first real deployment (2026-10-31) found."""

    def test_the_caddy_block_serves_feed_and_site_with_one_root_each(self):
        t = read(os.path.join(DEPLOY, "Caddyfile.template"))
        self.assertNotIn("root @feed", t, "a second `root` with a matcher served 404 for every feed file on Caddy 2.11")
        self.assertIn("file_server @feed {", t)
        self.assertIn("root /srv/dflv2\n", t)
        self.assertIn("root /srv/dflv2/site/current\n", t)

    def test_only_a_release_file_that_exists_gets_the_immutable_header(self):
        """2026-10-31: the first live release answered 403 (private folder) with `immutable`, and a browser kept replaying the 403 for a year."""
        t = read(os.path.join(DEPLOY, "Caddyfile.template"))
        i = t.index("@release {")
        block = t[i:t.index("}\n\t\t}", i)]
        self.assertIn("path /feed/releases/*", block)
        self.assertIn("file {", block, "the long-cache header applies only where Caddy can stat the file (an error answer keeps no-cache)")
        self.assertIn('header @release Cache-Control "public, max-age=31536000, immutable"', t)
        self.assertNotIn("@release path", t, "the one-line form matched error answers too")

    def test_the_status_heartbeat_is_never_cached_and_the_units_match_the_design(self):
        t = read(os.path.join(DEPLOY, "Caddyfile.template"))
        self.assertIn("@latest path /feed/latest.json /feed/status.json", t)
        svc = read(os.path.join(DEPLOY, "dflv2-league.service"))
        for need in ("User=dflv2", "ReadWritePaths=/srv/dflv2/league", "run_league.sh", "RestartPreventExitStatus=75", "TimeoutStopSec="):
            self.assertIn(need, svc)
        self.assertNotIn("/srv/dflv2/feed", svc, "the runner cannot write the public feed")
        exp = read(os.path.join(DEPLOY, "dfl-feed-export.service"))
        self.assertIn("EXPORT_ARGS=--skip-if-current", exp)
        self.assertIn("OnUnitInactiveSec=1min", read(os.path.join(DEPLOY, "dfl-feed-export.timer")))
        inst = read(os.path.join(DEPLOY, "install_league.sh"))
        self.assertIn("pause", inst, "the league is installed paused")
        self.assertIn('[ -e "$LG" ]', inst, "an existing league is never overwritten")
        self.assertNotIn("--snapshot-every", inst, "the default (a snapshot after every event) keeps the exporter from replaying game weeks")

    def test_run_league_reads_pace_and_limit_from_the_env_file(self):
        d = tempfile.mkdtemp(prefix="dflrl")
        try:
            os.makedirs(os.path.join(d, "repo", "tools"))
            write(os.path.join(d, "repo", "tools", "league_runner.py"), "")
            fake = os.path.join(d, "py")
            write(fake, '#!/bin/sh\nprintf "%s\\n" "$@"\n', 0o755)
            env_file = os.path.join(d, "league.env")

            def run():
                r = subprocess.run(["sh", os.path.join(DEPLOY, "run_league.sh")], capture_output=True, text=True,
                                   env=dict(os.environ, BASE=d, PYTHON=fake, LEAGUE_ENV=env_file))
                self.assertEqual(r.returncode, 0, r.stderr)
                return r.stdout.split()
            r = subprocess.run(["sh", os.path.join(DEPLOY, "run_league.sh")], capture_output=True, text=True, env=dict(os.environ, BASE=d, PYTHON=fake, LEAGUE_ENV=env_file))
            self.assertEqual(r.returncode, 2, "no settings: refuses to start rather than run without a limit")
            self.assertEqual(r.stdout, "")
            write(env_file, "DFL_PACE=900\nDFL_UNTIL=abc\n")
            r = subprocess.run(["sh", os.path.join(DEPLOY, "run_league.sh")], capture_output=True, text=True, env=dict(os.environ, BASE=d, PYTHON=fake, LEAGUE_ENV=env_file))
            self.assertEqual(r.returncode, 2)
            write(env_file, "DFL_UNTIL=none\n")
            self.assertNotIn("--until-events", run())
            write(env_file, "DFL_PACE=900\nDFL_UNTIL=141\n")
            a = run()
            self.assertEqual(a[a.index("--pace") + 1], "900")
            self.assertEqual(a[a.index("--until-events") + 1], "141")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def _snapshot(self, tar_rc):
        d = tempfile.mkdtemp(prefix="dflsnap")
        try:
            web = os.path.join(d, "www", "dfl")
            os.makedirs(web)
            write(os.path.join(web, "index.html"), "x")
            bindir = os.path.join(d, "bin")
            os.makedirs(bindir)
            real = shutil.which("tar")
            # a tar that behaves as the real one but exits `tar_rc` when it creates the archive (1 = "file changed as we read it")
            write(os.path.join(bindir, "tar"), '#!/bin/sh\ncase "$*" in *-czf*) %s "$@"; rc=$?; [ "$rc" = 0 ] && exit %d; exit $rc;; esac\nexec %s "$@"\n' % (real, tar_rc, real), 0o755)
            etc = os.path.join(d, "etc_caddy")
            os.makedirs(etc)
            env = dict(os.environ, PATH=bindir + os.pathsep + os.environ["PATH"], SNAP_ROOT=os.path.join(d, "snap"), WEB_ROOT=web)
            r = subprocess.run(["sh", os.path.join(DEPLOY, "snapshot_config.sh")], capture_output=True, text=True, env=env)
            made = sorted(os.listdir(os.path.join(d, "snap"))) if os.path.isdir(os.path.join(d, "snap")) else []
            sums = os.path.exists(os.path.join(d, "snap", made[0], "SHA256SUMS")) if made else False
            return r, sums
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_a_file_changing_while_the_web_root_is_archived_does_not_stop_the_rollback_copy(self):
        r, sums = self._snapshot(1)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(sums, "the checksum list is written even when tar warned")

    def test_a_real_tar_failure_still_stops_the_rollback_copy(self):
        r, sums = self._snapshot(2)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(sums, "no checksum list means the installer will not accept this copy")


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

    def test_a_good_site_passes_and_the_official_map_is_served(self):
        r = self.verify(self.url, "--profile", "local", "--require-map")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PASS  the official map file is served", r.stdout)
        self.assertIn("feed files match", r.stdout)

    def test_a_site_without_a_map_only_warns_unless_the_map_is_required(self):
        import preview_site
        d = os.path.join(self.tmp, "nomap")
        shutil.copytree(preview_site.SITE, d)
        write(os.path.join(d, "assets", "map", "map.json"), json.dumps({"file": None}))
        preview_site.Handler.site_root = d
        try:
            r = self.verify(self.url, "--profile", "local")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("WARN  the official map is not installed yet", r.stdout)
            self.assertEqual(self.verify(self.url, "--profile", "local", "--require-map").returncode, 1)
        finally:
            preview_site.Handler.site_root = preview_site.SITE

    def test_a_pointer_that_is_not_json_is_a_failure_not_a_crash(self):
        import preview_site
        empty = tempfile.mkdtemp(prefix="dflempty")
        old = preview_site.Handler.feed_root
        preview_site.Handler.feed_root = empty
        try:
            r = self.verify(self.url, "--profile", "caddy")
            self.assertNotIn("Traceback", r.stdout + r.stderr)
            self.assertEqual(r.returncode, 1)
            self.assertIn("FAIL", r.stdout)
        finally:
            preview_site.Handler.feed_root = old
            shutil.rmtree(empty, ignore_errors=True)

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
