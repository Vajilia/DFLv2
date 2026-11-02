"""deploy/website/deploy_site_only.sh: a frontend-only update changes the site files and nothing else, and is undone in one step.

A throwaway git repository stands in for the project (its site/ and deploy/website/install_site.sh are the real ones); the target is a throwaway
BASE with a "league" folder, a "feed" folder and an engine-code folder. After the update every file outside BASE/site/ is byte-identical, the live site
equals the commit's site/ folder, and rollback.sh --site restores the previous release."""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "deploy", "website", "deploy_site_only.sh")


def run(*a, cwd=None, env=None):
    return subprocess.run(a, capture_output=True, text=True, cwd=cwd, env=env)


def digest(root, skip=()):
    h = hashlib.sha256()
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if os.path.join(d, x) not in skip)
        for n in sorted(files):
            p = os.path.join(d, n)
            if p in skip:
                continue
            h.update(os.path.relpath(p, root).encode())
            if os.path.islink(p):
                h.update(os.readlink(p).encode())
            else:
                with open(p, "rb") as f:
                    h.update(f.read())
    return h.hexdigest()


class SiteOnly(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dflsiteonly")
        self.repo = os.path.join(self.tmp, "repo")
        shutil.copytree(os.path.join(ROOT, "site"), os.path.join(self.repo, "site"))
        os.makedirs(os.path.join(self.repo, "deploy", "website"))
        for n in ("install_site.sh", "rollback.sh", "record_site_revision.py"):
            shutil.copy(os.path.join(ROOT, "deploy", "website", n), os.path.join(self.repo, "deploy", "website", n))
        self.git = lambda *a: run("git", "-c", "user.email=t@t", "-c", "user.name=t", *a, cwd=self.repo)
        self.git("init", "-q")
        self.git("config", "uploadpack.allowAnySHA1InWant", "true")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "one")
        self.c1 = self.git("rev-parse", "HEAD").stdout.strip()
        with open(os.path.join(self.repo, "site", "assets", "app.js"), "a") as f:
            f.write("\n/* a frontend-only change */\n")
        self.git("commit", "-q", "-am", "two")
        self.c2 = self.git("rev-parse", "HEAD").stdout.strip()
        self.base = os.path.join(self.tmp, "srv")
        for d, name, body in (("league", "league.db", b"saved state"), ("league", "PAUSED", b""), ("feed", "latest.json", b"{}"), ("repo/src/dfl", "engine.py", b"engine code")):
            os.makedirs(os.path.join(self.base, d), exist_ok=True)
            with open(os.path.join(self.base, d, name), "wb") as f:
                f.write(body)
        self.env = dict(os.environ, BASE=self.base, SRC=os.path.join(self.tmp, "src"), REPO_URL=self.repo)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def deploy(self, commit):
        return run("sh", SCRIPT, commit, env=self.env)

    def test_first_site_must_come_from_the_full_deploy(self):
        r = self.deploy(self.c1)
        self.assertEqual(r.returncode, 2)
        self.assertIn("no site installed", r.stderr)

    def test_update_changes_only_the_site_and_rolls_back(self):
        os.makedirs(os.path.join(self.base, "site"))
        old = os.path.join(self.tmp, "first")
        os.makedirs(old)
        subprocess.run(f"git -C {self.repo} archive {self.c1} site | tar -x -C {old}", shell=True, check=True)
        first = run("sh", os.path.join(self.repo, "deploy", "website", "install_site.sh"), os.path.join(old, "site"), env=self.env)
        self.assertEqual(first.returncode, 0, first.stderr)
        outside = lambda: digest(self.base, skip=(os.path.join(self.base, "site"), os.path.join(self.base, "site-revisions"), os.path.join(self.base, "SITE_REVISION.json")))
        before_outside, before_site = outside(), os.readlink(os.path.join(self.base, "site", "current"))
        import time
        time.sleep(1.1)                                                      # release ids are timestamps to the second
        r = self.deploy(self.c2)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("byte-identical", r.stdout)
        rec = json.load(open(os.path.join(self.base, "SITE_REVISION.json")))
        self.assertEqual((rec["site_commit"], rec["site_release"]), (self.c2, os.readlink(os.path.join(self.base, "site", "current"))[len("releases/"):]))
        self.assertTrue(os.path.exists(os.path.join(self.base, "site-revisions", rec["site_release"] + ".json")))
        self.assertEqual(outside(), before_outside, "nothing outside the site folder changed: not the league, the feed or the engine code")
        now = os.readlink(os.path.join(self.base, "site", "current"))
        self.assertNotEqual(now, before_site)
        with open(os.path.join(self.base, "site", "current", "assets", "app.js")) as f:
            self.assertIn("a frontend-only change", f.read())
        rb = run("sh", os.path.join(self.repo, "deploy", "website", "rollback.sh"), "--site", env=self.env)
        self.assertEqual(rb.returncode, 0, rb.stderr)
        self.assertEqual(os.readlink(os.path.join(self.base, "site", "current")), before_site)
        with open(os.path.join(self.base, "site", "current", "assets", "app.js")) as f:
            self.assertNotIn("a frontend-only change", f.read())
        self.assertEqual(outside(), before_outside)
        back = json.load(open(os.path.join(self.base, "SITE_REVISION.json")))
        self.assertEqual(back["site_release"], before_site[len("releases/"):])      # the first release was installed without a record: said so, not guessed
        self.assertIsNone(back["site_commit"])

    def test_refuses_a_branch_name_and_a_dirty_checkout(self):
        os.makedirs(os.path.join(self.base, "site"))
        r = self.deploy("main")
        self.assertEqual(r.returncode, 2)
        self.assertIn("40-character", r.stderr)


if __name__ == "__main__":
    unittest.main()
