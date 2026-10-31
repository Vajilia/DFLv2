"""cutover_production.sh and rollback_cutover.sh against a scratch tree, with recording stand-ins for caddy / systemctl / curl.

Proved: nothing is changed unless the new site, the feed pointer and a rollback copy are in place; the old production block (and only that block) leaves the main
Caddyfile and the production hostnames get the same template the preview uses; Caddy is reloaded only after `caddy validate` passes, and a failed validation or
reload or local check puts the old configuration back byte for byte and leaves the old services alone; the old services are stopped and disabled only after the
new site answers; the rollback copy is private and checksummed; the rollback restores the old block and services, and its dry run changes nothing; a second
cutover is refused. What this cannot prove: that the real Caddy accepts the generated block (that is the first thing the script's validate step does on the server).
"""
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPLOY = os.path.join(ROOT, "deploy", "website")
HOSTS = "diamondfootballleague.com, www.diamondfootballleague.com, 143-198-130-68.sslip.io"
OLD_BLOCK = """diamondfootballleague.com, www.diamondfootballleague.com, 143-198-130-68.sslip.io {
\tencode gzip
\thandle /api/* {
\t\tbasic_auth {
\t\t\tcontrol $2a$14$notarealhashnotarealhashnotarealhashnotarealhashnotareal
\t\t}
\t\treverse_proxy 127.0.0.1:8081
\t}
\thandle {
\t\troot * /var/www/dfl
\t\tfile_server
\t}
}
"""
UNITS = ("dfl-live", "dfl-control", "dfl-chronicle")


def write(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    if mode:
        os.chmod(path, mode)


def read(path):
    with open(path) as f:
        return f.read()


class Cutover(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="dflcut")
        self.log = os.path.join(self.d, "calls.log")
        self.bin = os.path.join(self.d, "bin")
        os.makedirs(self.bin)
        write(os.path.join(self.bin, "caddy"), '#!/bin/sh\necho "caddy $*" >> "%s"\n[ -f "%s/caddy_fails" ] && { echo "bad config" >&2; exit 1; }\nexit 0\n' % (self.log, self.d), 0o755)
        write(os.path.join(self.bin, "systemctl"),
              '#!/bin/sh\necho "systemctl $*" >> "%s"\n[ "$1" = reload ] && [ -f "%s/reload_fails" ] && exit 1\n'
              '[ "$1" = is-enabled ] && { echo enabled; exit 0; }\n[ "$1" = is-active ] && { echo active; exit 0; }\nexit 0\n' % (self.log, self.d), 0o755)
        write(os.path.join(self.bin, "curl"), '#!/bin/sh\nexit 22\n', 0o755)
        self.base = os.path.join(self.d, "srv")
        self.cd = os.path.join(self.d, "etc_caddy")
        self.conf_d = os.path.join(self.cd, "conf.d")
        self.units = os.path.join(self.d, "units")
        self.snaps = os.path.join(self.d, "rollback")
        self.opt = os.path.join(self.d, "opt_dfl")
        self.etc = os.path.join(self.d, "etc_dfl")
        # the deployed new site
        write(os.path.join(self.base, "repo", "deploy", "website", "Caddyfile.template"), read(os.path.join(DEPLOY, "Caddyfile.template")))
        write(os.path.join(self.base, "DEPLOYED_REVISION.json"), json.dumps({"commit": "a" * 40}))
        write(os.path.join(self.base, "site", "current", "index.html"), "<html>Development league</html>")
        write(os.path.join(self.base, "feed", "latest.json"), json.dumps({"format": "dfl-public-feed", "release": "e00012-0123456789"}))
        # the old production set-up
        self.caddyfile = os.path.join(self.cd, "Caddyfile")
        write(self.caddyfile, "{\n\temail ops@example.com\n}\n\n" + OLD_BLOCK + "\nimport %s/*.caddy\n" % self.conf_d)
        write(os.path.join(self.conf_d, "dflv2-preview.caddy"), "preview.example.com {\n\tfile_server\n}\n")
        for u in UNITS:
            write(os.path.join(self.units, u + ".service"), "[Service]\nExecStart=/usr/bin/true %s\n" % u)
        write(os.path.join(self.opt, "engine", "live.py"), "print('old')\n")
        write(os.path.join(self.etc, "chronicle.env"), "SECRET_TOKEN=do-not-print\n", 0o600)
        self.original = read(self.caddyfile)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def env(self, **extra):
        e = dict(os.environ, PATH=self.bin + os.pathsep + os.environ["PATH"], BASE=self.base, CADDY_DIR=self.cd, SNAP_ROOT=self.snaps, UNIT_DIR=self.units,
                 OLD_DIRS="%s %s" % (self.opt, self.etc), WEB_ROOT=os.path.join(self.d, "no_web_root"), ALLOW_NON_ROOT="1", SKIP_LOCAL_CHECK="1")
        e.update(extra)
        return e

    def cutover(self, **extra):
        return subprocess.run(["sh", os.path.join(DEPLOY, "cutover_production.sh")], capture_output=True, text=True, env=self.env(**extra))

    def rollback(self, snap, *flags, **extra):
        return subprocess.run(["sh", os.path.join(DEPLOY, "rollback_cutover.sh"), snap, *flags], capture_output=True, text=True, env=self.env(**extra))

    def calls(self):
        return read(self.log).splitlines() if os.path.exists(self.log) else []

    def snap(self):
        (n,) = os.listdir(self.snaps)
        return os.path.join(self.snaps, n)

    def test_a_good_cutover_replaces_only_the_old_block_and_serves_the_new_site_on_the_production_names(self):
        r = self.cutover()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        main = read(self.caddyfile)
        self.assertNotIn("basic_auth", main)
        self.assertNotIn("reverse_proxy", main)
        self.assertNotIn("/var/www/dfl", main)
        self.assertIn("email ops@example.com", main)
        self.assertEqual(main.count("import %s/*.caddy" % self.conf_d), 1)
        snippet = read(os.path.join(self.conf_d, "dflv2-production.caddy"))
        self.assertIn(HOSTS, snippet)
        self.assertNotIn("__HOST__", snippet)
        self.assertIn("/srv/dflv2", snippet)
        self.assertNotIn("reverse_proxy", snippet)
        self.assertEqual(read(os.path.join(self.conf_d, "dflv2-preview.caddy")), "preview.example.com {\n\tfile_server\n}\n", "the preview site is untouched")
        calls = self.calls()
        v = [i for i, c in enumerate(calls) if c.startswith("caddy validate")]
        rl = [i for i, c in enumerate(calls) if c == "systemctl reload caddy"]
        self.assertTrue(v and rl and v[-1] < rl[0], "Caddy is validated before it is reloaded")
        for u in UNITS:
            self.assertIn("systemctl disable --now " + u, calls)
            self.assertGreater(calls.index("systemctl disable --now " + u), rl[0], "the old services are stopped only after the new site is live")
        rec = json.loads(read(os.path.join(self.base, "PRODUCTION_CUTOVER.json")))
        self.assertEqual(rec["deployed_commit"], "a" * 40)
        self.assertEqual(rec["feed_release_at_cutover"], "e00012-0123456789")

    def test_the_rollback_copy_is_private_checksummed_and_holds_the_old_application_and_configuration(self):
        self.assertEqual(self.cutover().returncode, 0)
        s = self.snap()
        self.assertEqual(stat.S_IMODE(os.stat(s).st_mode), 0o700)
        arc = os.path.join(s, "old_application.tgz")
        self.assertEqual(stat.S_IMODE(os.stat(arc).st_mode), 0o600)
        names = subprocess.run(["tar", "-tzf", arc], capture_output=True, text=True).stdout
        self.assertIn("engine/live.py", names)
        self.assertIn("chronicle.env", names)
        self.assertEqual(read(os.path.join(s, "caddy", "Caddyfile")), self.original)
        for u in UNITS:
            self.assertTrue(os.path.exists(os.path.join(s, "systemd", u + ".service")))
        self.assertIn("dfl-live enabled=enabled", read(os.path.join(s, "cutover_state.txt")))
        self.assertEqual(subprocess.run(["sha256sum", "-c", "SHA256SUMS", "--quiet"], cwd=s).returncode, 0)

    def test_no_secret_is_printed(self):
        r = self.cutover()
        self.assertNotIn("do-not-print", r.stdout + r.stderr + "".join(self.calls()))

    def test_a_configuration_caddy_rejects_leaves_everything_as_it_was(self):
        write(os.path.join(self.d, "caddy_fails"), "")
        r = self.cutover()
        self.assertEqual(r.returncode, 5, r.stdout + r.stderr)
        self.assertEqual(read(self.caddyfile), self.original)
        self.assertFalse(os.path.exists(os.path.join(self.conf_d, "dflv2-production.caddy")))
        self.assertNotIn("systemctl reload caddy", self.calls())
        self.assertFalse([c for c in self.calls() if "disable" in c], "the old services were not touched")

    def test_a_reload_that_fails_puts_the_old_configuration_back(self):
        write(os.path.join(self.d, "reload_fails"), "")
        r = self.cutover()
        self.assertEqual(r.returncode, 6, r.stdout + r.stderr)
        self.assertEqual(read(self.caddyfile), self.original)
        self.assertFalse(os.path.exists(os.path.join(self.conf_d, "dflv2-production.caddy")))
        self.assertFalse([c for c in self.calls() if "disable" in c])

    def test_a_new_site_that_does_not_answer_is_undone_and_the_old_services_keep_running(self):
        r = self.cutover(SKIP_LOCAL_CHECK="", CHECK_WAIT="0", PATH=self.bin + os.pathsep + os.environ["PATH"])
        self.assertEqual(r.returncode, 8, r.stdout + r.stderr)
        self.assertEqual(read(self.caddyfile), self.original)
        self.assertFalse(os.path.exists(os.path.join(self.conf_d, "dflv2-production.caddy")))
        self.assertFalse([c for c in self.calls() if "disable" in c])

    def test_nothing_is_changed_when_the_new_site_or_feed_is_not_in_place(self):
        for victim in (os.path.join(self.base, "feed", "latest.json"), os.path.join(self.base, "site", "current", "index.html"),
                       os.path.join(self.base, "DEPLOYED_REVISION.json")):
            keep = read(victim)
            os.remove(victim)
            r = self.cutover()
            self.assertEqual(r.returncode, 2, victim + r.stderr)
            self.assertEqual(read(self.caddyfile), self.original)
            self.assertFalse(os.path.exists(self.snaps))
            write(victim, keep)

    def test_a_main_caddyfile_without_the_old_block_is_refused_untouched(self):
        write(self.caddyfile, "{\n\temail ops@example.com\n}\nimport %s/*.caddy\n" % self.conf_d)
        before = read(self.caddyfile)
        r = self.cutover()
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertEqual(read(self.caddyfile), before)
        self.assertFalse(os.path.exists(os.path.join(self.conf_d, "dflv2-production.caddy")))
        self.assertFalse([c for c in self.calls() if "disable" in c])

    def test_a_second_cutover_is_refused(self):
        self.assertEqual(self.cutover().returncode, 0)
        self.assertEqual(self.cutover().returncode, 7)

    def test_the_rollback_dry_run_validates_and_changes_nothing(self):
        self.assertEqual(self.cutover().returncode, 0)
        s = self.snap()
        main, snippet = read(self.caddyfile), read(os.path.join(self.conf_d, "dflv2-production.caddy"))
        n = len(self.calls())
        r = self.rollback(s, "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((read(self.caddyfile), read(os.path.join(self.conf_d, "dflv2-production.caddy"))), (main, snippet))
        self.assertFalse([c for c in self.calls()[n:] if c.startswith("systemctl")], "a dry run starts and stops nothing")
        write(os.path.join(self.d, "caddy_fails"), "")
        self.assertEqual(self.rollback(s, "--dry-run").returncode, 5, "a saved configuration that no longer validates is reported before it is needed")

    def test_the_rollback_puts_the_old_block_and_services_back_and_keeps_the_preview(self):
        self.assertEqual(self.cutover().returncode, 0)
        s = self.snap()
        r = self.rollback(s)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(read(self.caddyfile), self.original)
        self.assertFalse(os.path.exists(os.path.join(self.conf_d, "dflv2-production.caddy")))
        self.assertTrue(os.path.exists(os.path.join(self.conf_d, "dflv2-preview.caddy")))
        for u in UNITS:
            self.assertIn("systemctl enable --now " + u, self.calls())

    def test_a_damaged_rollback_copy_is_refused(self):
        self.assertEqual(self.cutover().returncode, 0)
        s = self.snap()
        with open(os.path.join(s, "systemd", "dfl-live.service"), "a") as f:
            f.write("tampered\n")
        self.assertEqual(self.rollback(s).returncode, 3)

    def test_a_rollback_whose_restored_configuration_will_not_validate_changes_nothing(self):
        self.assertEqual(self.cutover().returncode, 0)
        s = self.snap()
        main = read(self.caddyfile)
        write(os.path.join(self.d, "caddy_fails"), "")
        r = self.rollback(s)
        self.assertEqual(r.returncode, 5, r.stdout + r.stderr)
        self.assertEqual(read(self.caddyfile), main)
        self.assertTrue(os.path.exists(os.path.join(self.conf_d, "dflv2-production.caddy")))


if __name__ == "__main__":
    unittest.main()
