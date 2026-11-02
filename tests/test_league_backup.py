"""tools/league_backup.py: a backup folder is checked against its own manifest, and the archive is found by that manifest, not by the name the league file
records for the live archive. Valid, missing, damaged and mismatched components are each reported; a valid backup restores to the same league."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, TOOLS)
import league_backup as LB  # noqa: E402


def py(*args):
    return subprocess.run([sys.executable, "-I", *args], capture_output=True, text=True)


def new_league(d, name, seed, events, placeholder=True):
    path = os.path.join(d, name)
    r = py(os.path.join(TOOLS, "league_store.py"), "init", path, "--seed", str(seed), "--placeholder" if placeholder else "--play-level", "--snapshot-every", "4")
    assert r.returncode == 0, r.stderr
    advance(path, events)
    return path


def advance(path, events):
    r = py(os.path.join(TOOLS, "league_runner.py"), path, "--until-events", str(events))
    assert r.returncode == 0, r.stderr


def rewrite_manifest(folder):
    """Make the manifest agree with the files as they are now, as someone repairing the manifest (not the data) would."""
    with open(os.path.join(folder, LB.MANIFEST)) as f:
        m = json.load(f)
    for c in m["components"]:
        p = os.path.join(folder, c["file"])
        c["size"], c["sha256"] = os.path.getsize(p), LB.sha256_file(p)
    LB.write_manifest(folder, m)


class Backup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp(prefix="dflbak")
        cls.cfg = os.path.join(cls.d, "league.env")
        with open(cls.cfg, "w") as f:
            f.write("DFL_UNTIL=3\n")
        cls.live = new_league(cls.d, "live.db", 7, 11, placeholder=False)                 # the real engine: week 1 (event 12) is the first event that records games
        cls.early = os.path.join(cls.d, "early")
        LB.create(cls.live, cls.early)                                                      # the archive of this league one event before its first games
        advance(cls.live, 12)
        cls.other = new_league(cls.d, "other.db", 99, 3)                                    # another league entirely (placeholder: no games)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def make(self, name, league=None, include=True):
        out = os.path.join(self.d, name)
        shutil.rmtree(out, ignore_errors=True)
        LB.create(league or self.live, out, include=[self.cfg] if include else [])
        return out

    def problems(self, folder):
        return LB.verify(folder)["problems"]

    def test_the_league_records_its_archive_under_the_live_name_and_the_backup_still_verifies(self):
        self.assertTrue(os.path.exists(self.live + ".archive"), "the league has an archive beside it, named after the league file")
        b = self.make("ok")
        self.assertEqual(sorted(os.listdir(b)), ["BACKUP_MANIFEST.json", "archive.db", "config", "league.db"])
        self.assertFalse(os.path.exists(os.path.join(b, "league.db.archive")), "the backup folder has no file under the live archive's name")
        res = LB.verify(b)
        self.assertEqual(res["problems"], [])
        m = res["manifest"]
        self.assertEqual((m["format"], m["journal_head"]["seq"]), ("dfl-backup", 12))
        self.assertEqual([c["role"] for c in m["components"]], ["league", "archive", "config"])

    def test_a_valid_backup_restores_to_the_same_league(self):
        b = self.make("ok2")
        dest = os.path.join(self.d, "restored")
        shutil.rmtree(dest, ignore_errors=True)
        res = LB.restore(b, dest)
        self.assertTrue(res["restored"], res)
        self.assertEqual(res["events"], 12)
        self.assertEqual(LB._identity(os.path.join(dest, "league.db"))["journal_head"], LB.verify(b)["manifest"]["journal_head"])
        r = py(os.path.join(TOOLS, "league_backup.py"), "restore", b, dest)
        self.assertNotEqual(r.returncode, 0)

    def test_the_command_line_exits_zero_for_a_good_backup_and_nonzero_for_a_bad_one(self):
        b = self.make("cli")
        self.assertEqual(py(os.path.join(TOOLS, "league_backup.py"), "verify", b).returncode, 0)
        os.remove(os.path.join(b, "archive.db"))
        r = py(os.path.join(TOOLS, "league_backup.py"), "verify", b)
        self.assertEqual(r.returncode, 1)
        self.assertIn("archive file archive.db is missing", r.stdout)

    def test_a_missing_component_is_named(self):
        for victim, text in (("archive.db", "archive file archive.db is missing"), ("league.db", "league file league.db is missing"),
                             ("config/league.env", "config file config/league.env is missing")):
            b = self.make("missing")
            os.remove(os.path.join(b, victim))
            self.assertTrue(any(text in x for x in self.problems(b)), (victim, self.problems(b)))
        b = self.make("nomanifest")
        os.remove(os.path.join(b, LB.MANIFEST))
        self.assertTrue(any("BACKUP_MANIFEST.json is missing" in x for x in self.problems(b)))

    def test_a_damaged_component_is_found(self):
        for victim in ("archive.db", "league.db", "config/league.env"):
            b = self.make("damaged")
            p = os.path.join(b, victim)
            with open(p, "r+b") as f:
                f.seek(os.path.getsize(p) // 2)
                byte = f.read(1)
                f.seek(os.path.getsize(p) // 2)
                f.write(bytes([byte[0] ^ 0xFF]))
            self.assertTrue(any("SHA-256" in x for x in self.problems(b)), (victim, self.problems(b)))
        b = self.make("truncated")
        p = os.path.join(b, "archive.db")
        os.truncate(p, os.path.getsize(p) - 4096)
        self.assertTrue(any("bytes, the manifest says" in x for x in self.problems(b)))
        b = self.make("badmanifest")
        with open(os.path.join(b, LB.MANIFEST), "w") as f:
            f.write("{not json")
        self.assertTrue(any("cannot be read" in x for x in self.problems(b)))

    def test_damage_is_found_even_when_the_manifest_is_repaired_to_match(self):
        b = self.make("repaired")
        p = os.path.join(b, "archive.db")
        with open(p, "r+b") as f:                                                       # damage the data, then make the manifest agree with the damage
            f.seek(os.path.getsize(p) // 2)
            f.write(b"\xff" * 64)
        rewrite_manifest(b)
        ps = self.problems(b)
        self.assertTrue(ps and not any("SHA-256" in x for x in ps), ps)
        self.assertTrue(any(x.startswith("archive file:") or "cannot be" in x for x in ps), ps)

    def test_an_archive_from_another_league_is_reported_as_mismatched(self):
        b = self.make("foreign")
        o = self.make("foreign_src", league=self.other)
        shutil.copyfile(os.path.join(o, "archive.db"), os.path.join(b, "archive.db"))
        self.assertTrue(any("SHA-256" in x or "bytes, the manifest says" in x for x in self.problems(b)), "swapped in without touching the manifest")
        rewrite_manifest(b)                                                             # swapped in AND the manifest repaired: the league and archive still disagree
        ps = self.problems(b)
        self.assertTrue(any("different league" in x or "differs" in x or "lacks" in x for x in ps), ps)

    def test_an_archive_that_is_behind_the_league_is_reported(self):
        self.assertEqual(LB.verify(self.early)["manifest"]["journal_head"]["seq"], 11)
        b = self.make("behind")
        self.assertEqual(LB.verify(b)["manifest"]["journal_head"]["seq"], 12)
        shutil.copyfile(os.path.join(self.early, "archive.db"), os.path.join(b, "archive.db"))
        rewrite_manifest(b)
        ps = self.problems(b)
        self.assertTrue(any("lacks records for events" in x for x in ps), ps)

    def test_a_manifest_that_does_not_describe_the_files_is_reported(self):
        b = self.make("liar")
        with open(os.path.join(b, LB.MANIFEST)) as f:
            m = json.load(f)
        m["journal_head"]["seq"] = 99
        LB.write_manifest(b, m)
        self.assertTrue(any("journal_head" in x for x in self.problems(b)))
        m["journal_head"]["seq"] = 12
        m["components"][0]["file"] = "../league.db"
        LB.write_manifest(b, m)
        self.assertTrue(any("outside the backup folder" in x for x in self.problems(b)))

    def test_the_early_backup_is_itself_valid_and_verify_leaves_the_folder_as_it_found_it(self):
        before = sorted(os.listdir(self.early))
        self.assertEqual(self.problems(self.early), [])
        self.assertEqual(sorted(os.listdir(self.early)), before)

    def test_a_backup_never_goes_into_a_folder_that_is_in_use(self):
        b = self.make("inuse")
        with self.assertRaises(SystemExit):
            LB.create(self.live, b)


if __name__ == "__main__":
    unittest.main()
