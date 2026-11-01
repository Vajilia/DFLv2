"""tools/verify_playback_against_archive.py must pass an honest export and name every kind of departure from the archive.

The archive excerpt (tests/fixtures/playback_v1/archive_excerpt.db) holds one real archived game, S001/reg:w01:g02, with its 119 spatial traces; the
public file in tests/fixtures/playback_v1/playback is its export. Each case below changes the public file the way a faulty exporter or a hand edit
could (and fixes up the index so the only thing wrong is the content) and expects the check to fail and to say what."""
import base64
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "playback_v1")
TOOL = os.path.join(ROOT, "tools", "verify_playback_against_archive.py")
ARCH = os.path.join(FIX, "archive_excerpt.db")
GID = "S001/reg:w01:g02"
sys.path.insert(0, os.path.join(ROOT, "src"))
from dflsite import playback as PB  # noqa: E402


class Verifier(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dflpbv")
        self.dir = os.path.join(self.tmp, "pb")
        shutil.copytree(os.path.join(FIX, "playback"), self.dir)
        with open(os.path.join(self.dir, "index.json")) as f:
            self.idx = json.load(f)
        self.entry = next(e for e in self.idx["games"] if e["gid"] == GID)
        with open(os.path.join(self.dir, self.entry["file"])) as f:
            self.game = json.load(f)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_tool(self):
        p = subprocess.run([sys.executable, "-I", TOOL, "--archive", ARCH, "--playback", self.dir, "--gid", GID], capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr

    def publish_changed(self):
        raw = PB.canonical(self.game)
        sha = hashlib.sha256(raw).hexdigest()
        rel = f"games/S001-reg-w01-g02.{sha[:10]}.json"
        with open(os.path.join(self.dir, rel), "wb") as f:
            f.write(raw)
        self.entry.update(file=rel, sha256=sha, bytes=len(raw))
        with open(os.path.join(self.dir, "index.json"), "w") as f:
            json.dump(self.idx, f)

    def first(self, pred):
        return next(p for p in self.game["plays"] if pred(p))

    def test_an_honest_export_passes_and_counts_what_it_compared(self):
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("166 plays, 119 traced, 3 scoring plays", out)
        self.assertIn("ALL CHECKS PASSED", out)

    def test_a_moved_player_is_found(self):
        p = self.first(lambda p: p["trace"] and p["trace"]["n"] > 4)
        t = p["trace"]
        raw = bytearray(base64.b64decode(t["pos"]))
        struct.pack_into("<h", raw, 2 * 3, struct.unpack_from("<h", raw, 2 * 3)[0] + 7)       # column 0, sample 3 (a change of 0.07 yd that carries forward)
        t["pos"] = base64.b64encode(bytes(raw)).decode()
        self.publish_changed()
        rc, out = self.run_tool()
        self.assertEqual(rc, 1)
        self.assertIn(f"play {p['i']}: position column 0 differs from the archive", out)

    def test_a_changed_final_score_and_changed_scoring_play_are_found(self):
        self.game["final"]["home"] += 1
        self.publish_changed()
        rc, out = self.run_tool()
        self.assertEqual(rc, 1)
        self.assertIn("final score", out)
        self.setUp()
        p = self.first(lambda p: p.get("pts"))
        p["pts"] += 1
        self.publish_changed()
        rc, out = self.run_tool()
        self.assertEqual(rc, 1)
        self.assertIn(f"play {p['i']}: scoring play has", out)

    def test_a_removed_trace_and_an_invented_trace_are_found(self):
        p = self.first(lambda p: p["trace"])
        saved = p["trace"]
        p["trace"] = None
        self.game["traced_plays"] -= 1
        self.publish_changed()
        rc, out = self.run_tool()
        self.assertEqual(rc, 1)
        self.assertIn(f"play {p['i']}: the archive has trace and the file has none", out)
        self.setUp()
        q = self.first(lambda p: p["trace"] is None)
        q["trace"] = json.loads(json.dumps(saved))
        self.game["traced_plays"] += 1
        self.publish_changed()
        rc, out = self.run_tool()
        self.assertEqual(rc, 1)
        self.assertIn(f"play {q['i']}: the archive has no trace and the file has one", out)

    def test_a_wrong_participant_or_side_is_found(self):
        pid = next(iter(self.game["roster"]))
        self.game["roster"][pid]["side"] = "home" if self.game["roster"][pid]["side"] == "away" else "away"
        self.publish_changed()
        rc, out = self.run_tool()
        self.assertEqual(rc, 1)
        self.assertIn(f"player {pid}: side", out)
        self.setUp()
        self.game["roster"]["999999"] = {"side": "home", "name": "Nobody"}
        self.publish_changed()
        rc, out = self.run_tool()
        self.assertEqual(rc, 1)
        self.assertIn("players:", out)
        self.setUp()
        self.game["teams"]["home"]["name"] = "Somewhere Else Wolves"
        self.publish_changed()
        rc, out = self.run_tool()
        self.assertEqual(rc, 1)
        self.assertIn("home club", out)

    def test_a_private_field_a_missing_play_and_a_stale_index_are_found(self):
        self.game["plays"][0]["rating"] = 80
        self.publish_changed()
        rc, out = self.run_tool()
        self.assertEqual(rc, 1)
        self.assertIn("keys outside the public schema: ['rating']", out)
        self.setUp()
        del self.game["plays"][5]
        self.publish_changed()
        rc, out = self.run_tool()
        self.assertEqual(rc, 1)
        self.assertIn("play list", out)
        self.setUp()
        with open(os.path.join(self.dir, self.entry["file"]), "ab") as f:
            f.write(b" ")
        rc, out = self.run_tool()
        self.assertEqual(rc, 1)
        self.assertIn("does not match its index entry", out)

    def test_it_never_writes(self):
        def digest(root):
            h = hashlib.sha256()
            for d, _, fs in sorted(os.walk(root)):
                for n in sorted(fs):
                    with open(os.path.join(d, n), "rb") as f:
                        h.update(f.read())
            return h.hexdigest()
        a, b = digest(self.dir), hashlib.sha256(Path(ARCH).read_bytes()).hexdigest()
        self.run_tool()
        self.assertEqual((digest(self.dir), hashlib.sha256(Path(ARCH).read_bytes()).hexdigest()), (a, b))


if __name__ == "__main__":
    unittest.main()
