"""Replay tools: the exporter, the independent verifier and the completeness checker, without a browser and without the site (docs/REPLAY_TOOLS.md).

Data: tests/fixtures/replay_tools_v1 holds one real archived game from a 12-event development league (archive_excerpt.sqlite) and that league's published
feed. Extra archive rows are made from it in a temporary copy (the same game under other ids, seasons, kinds and event numbers) so that the rules that
decide which games get a replay can be checked without a full league. What is checked:
  * export, then the independent verifier, then the completeness check, all pass on a real game; running the export again changes nothing
  * a game committed after the published feed's event count is not exported (an unrevealed game never reaches the public); a game that is not from the
    spatial engine is skipped; both are named by the completeness check, and only `missing` is a problem
  * a failed export leaves the previous index in place; an index of another format is refused
  * the verifier fails if a recorded position in a published file is changed, even when the file is renamed and its hash is repaired to match
  * the completeness check finds a missing index entry, a missing file, a leftover file, a duplicate id, an orphan entry
  * the id mapping: regular, tour, bowl and coronation ids are the archive id without its season; playoff ids are numbered 1..n per conference and round
  * a player not on the public roster is shown as "player N" and carries no name
  * the replay tools are outside the code fingerprint, which is the deployed release's"""
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from dflsite import playback as PB  # noqa: E402
import export_playback as EXP  # noqa: E402
import replay_coverage as COV  # noqa: E402
import verify_playback_against_archive as VER  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures", "replay_tools_v1")
ARCHIVE = os.path.join(FIX, "archive_excerpt.sqlite")
FEED = os.path.join(FIX, "feed")
GID = "S001/reg:w01:g02"
DEPLOYED_FINGERPRINT = "efaa7167832713d771942b9504f72f25edb0793af6fa6d2fa2593a8792909973"      # engine release 7e59cbb


def clone_rows(src, extra):
    """Copy the one-game archive and add copies of that game: extra = [(new game id, season, kind, seq, engine or None)]."""
    dst = os.path.join(os.path.dirname(src), "archive.sqlite")
    shutil.copy(ARCHIVE, dst)
    db = sqlite3.connect(dst)
    base = db.execute("SELECT game_id, season, seq, kind, home, away, engine, rule_version, n_traces, sha256, size, payload FROM games").fetchone()
    traces = db.execute("SELECT play_i, sha256, size, payload FROM traces WHERE game_id = ?", (base[0],)).fetchall()
    for gid, season, kind, seq, engine in extra:
        rec = json.loads(zlib.decompress(base[11]))
        rec.update(gid=gid, season=season, kind=kind)
        if engine:
            rec["engine"] = engine
        z = zlib.compress(json.dumps(rec).encode())
        db.execute("INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (gid, season, seq, kind, base[4], base[5], engine or base[6], base[7], base[8], base[9], len(z), z))
        for p in traces:
            db.execute("INSERT INTO traces VALUES (?,?,?,?,?)", (gid,) + tuple(p))
    db.commit()
    db.close()
    return dst


def run(tool, *args):
    """Run a tool the way the operator does, in its own process; -> (exit code, stdout, stderr)."""
    p = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "tools", tool), *args], capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def load(path):
    with open(path, "rb") as f:
        return json.loads(f.read().decode("utf-8"))


def read(path):
    with open(path, "rb") as f:
        return f.read()


def write_index(play, fn):
    p = os.path.join(play, "index.json")
    idx = load(p)
    fn(idx)
    with open(p, "wb") as f:
        f.write(PB.canonical(idx))


def feed_listing(dst, ids, **change):
    """A copy of the fixture feed whose schedule lists exactly these finished games, each a copy of the archived game with its own id (and any field in
    `change`), so the feed and the one-game archive can agree. -> the feed's path."""
    shutil.copytree(FEED, dst)
    rel = os.path.join(dst, "releases", load(os.path.join(dst, "latest.json"))["release"])
    sched = load(os.path.join(rel, "schedule.json"))
    base = next(g for g in sched["games"] if g["id"] == "reg:w01:g02")
    sched["games"] = [dict(base, id=i, **change) for i in ids]
    with open(os.path.join(rel, "schedule.json"), "wb") as f:
        f.write(PB.canonical(sched))
    return dst


class Tools(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.play = os.path.join(self.tmp, "playback")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.feed = feed_listing(os.path.join(self.tmp, "feed"), ["reg:w01:g02"])

    def export(self, archive=ARCHIVE, *extra):
        return run("export_playback.py", "--archive", archive, "--feed", self.feed, "--out", self.play, *extra)

    def coverage(self, archive=ARCHIVE, *extra, feed=None):
        return run("replay_coverage.py", "--archive", archive, "--playback", self.play, "--feed", feed or self.feed, "--json", *extra)

    # ------------------------------------------------------------------------------------------------ the whole chain on a real game
    def test_export_verify_coverage_and_a_repeat_run(self):
        code, out, err = self.export()
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["games_written"], 1)
        idx = load(os.path.join(self.play, "index.json"))
        self.assertEqual((idx["format"], idx["format_version"], idx["kind"]), ("dfl-public-playback", 1, "index"))
        self.assertEqual([e["gid"] for e in idx["games"]], [GID])
        self.assertEqual(idx["games"][0]["id"], "reg:w01:g02")
        code, out, err = run("verify_playback_against_archive.py", "--archive", ARCHIVE, "--playback", self.play)
        self.assertEqual(code, 0, out + err)
        self.assertIn("ALL CHECKS PASSED", out)
        code, out, err = self.coverage(ARCHIVE, "--expect-complete")
        self.assertEqual(code, 0, out + err)
        rep = json.loads(out)
        self.assertEqual((rep["archive_games"], rep["index_games"], rep["classes"], rep["problems"]), (1, 1, {"exported": 1}, []))
        before = {"index.json": read(os.path.join(self.play, "index.json")), "game": read(os.path.join(self.play, idx["games"][0]["file"]))}
        code, out, err = self.export()
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["games_written"], 0, "a published game is not written twice")
        self.assertEqual(read(os.path.join(self.play, "index.json")), before["index.json"], "the index is byte-identical after a repeat run")
        self.assertEqual(read(os.path.join(self.play, idx["games"][0]["file"])), before["game"])

    # ------------------------------------------------------------------------------------------------ which games get a replay
    def test_unrevealed_and_non_spatial_games_are_not_exported_and_are_named(self):
        arch = clone_rows(os.path.join(self.tmp, "x"), [("S001/reg:w01:g03", 1, "regular", 13, None),          # committed after the feed's event 12
                                                         ("S001/reg:w01:g04", 1, "regular", 11, "legacy")])    # not the spatial engine
        code, out, err = self.export(arch)
        self.assertEqual(code, 0, err)
        self.assertEqual([e["gid"] for e in load(os.path.join(self.play, "index.json"))["games"]], [GID])
        code, out, err = self.coverage(arch)
        self.assertEqual(code, 0, out + err)
        rep = json.loads(out)
        self.assertEqual(rep["classes"], {"exported": 1, "unrevealed": 1, "not_spatial": 1})
        self.assertEqual((rep["exceptions"]["unrevealed"], rep["exceptions"]["not_spatial"], rep["problems"]), (["S001/reg:w01:g03"], ["S001/reg:w01:g04"], []))
        code, out, err = self.coverage(arch, "--expect-complete")
        self.assertEqual(code, 1, "with --expect-complete any game outside `exported` fails the check")

    def test_a_failed_export_leaves_the_previous_index_in_place(self):
        self.assertEqual(self.export()[0], 0)
        good = read(os.path.join(self.play, "index.json"))
        arch = clone_rows(os.path.join(self.tmp, "x"), [("S001/reg:w01:g05", 1, "regular", 12, None)])
        db = sqlite3.connect(arch)                                           # a game whose clubs have no public record: the export must stop, not guess
        row = db.execute("SELECT payload FROM games WHERE game_id = ?", ("S001/reg:w01:g05",)).fetchone()[0]
        rec = json.loads(zlib.decompress(row))
        rec["home"] = "Nowhere Nobodies"
        db.execute("UPDATE games SET payload = ? WHERE game_id = ?", (zlib.compress(json.dumps(rec).encode()), "S001/reg:w01:g05"))
        db.commit()
        db.close()
        code, out, err = self.export(arch)
        self.assertNotEqual(code, 0)
        self.assertIn("no public team record", err)
        self.assertEqual(read(os.path.join(self.play, "index.json")), good, "the previous index is untouched")

    def test_an_index_of_another_format_is_refused(self):
        self.assertEqual(self.export()[0], 0)
        write_index(self.play, lambda i: i.update(format_version=2))
        code, out, err = self.export()
        self.assertNotEqual(code, 0)
        self.assertIn("refusing to mix formats", err)

    # ------------------------------------------------------------------------------------------------ the verifier is independent of the exporter
    def test_the_verifier_fails_when_a_recorded_position_is_changed_even_with_a_repaired_hash(self):
        self.assertEqual(self.export()[0], 0)
        idx = load(os.path.join(self.play, "index.json"))
        e = idx["games"][0]
        g = load(os.path.join(self.play, e["file"]))
        play = next(p for p in g["plays"] if p["trace"])
        raw = bytearray(PB.decode_b64(play["trace"]["pos"]))
        raw[0] ^= 0x01                                                         # one hundredth of a yard on the first sample of the first position column
        import base64
        play["trace"]["pos"] = base64.b64encode(bytes(raw)).decode()
        body = PB.canonical(g)
        h = hashlib.sha256(body).hexdigest()
        rel = f"games/{PB.slug(g['gid'])}.{h[:10]}.json"
        with open(os.path.join(self.play, rel), "wb") as f:
            f.write(body)
        os.remove(os.path.join(self.play, e["file"]))
        e.update(file=rel, sha256=h, bytes=len(body))
        write_index(self.play, lambda i: i.update(games=[e]))
        code, out, err = run("verify_playback_against_archive.py", "--archive", ARCHIVE, "--playback", self.play)
        self.assertEqual(code, 1, out)
        self.assertIn("PROBLEM", out)

    # ------------------------------------------------------------------------------------------------ the completeness check
    def test_coverage_finds_what_is_wrong_with_an_index(self):
        arch = clone_rows(os.path.join(self.tmp, "x"), [("S001/reg:w01:g06", 1, "regular", 12, None), ("S001/bowl", 1, "bowl", 12, None)])
        self.feed = feed_listing(os.path.join(self.tmp, "feed2"), ["reg:w01:g02", "reg:w01:g06"])
        self.assertEqual(self.export(arch)[0], 0)
        idx = load(os.path.join(self.play, "index.json"))
        self.assertEqual(sorted(e["id"] for e in idx["games"]), ["bowl", "reg:w01:g02", "reg:w01:g06"])
        self.assertEqual(json.loads(self.coverage(arch)[1])["problems"], [])
        by = {e["gid"]: e for e in idx["games"]}

        def problems(index_fn=None, files_fn=None):
            d = os.path.join(self.tmp, "mut")
            shutil.rmtree(d, ignore_errors=True)
            shutil.copytree(self.play, d)
            if index_fn:
                write_index(d, index_fn)
            if files_fn:
                files_fn(d)
            code, out, err = run("replay_coverage.py", "--archive", arch, "--playback", d, "--feed", self.feed, "--json")
            return code, json.loads(out)["problems"]
        code, p = problems(lambda i: i.update(games=[e for e in i["games"] if e["gid"] != "S001/reg:w01:g06"]))
        self.assertEqual(code, 1)
        self.assertTrue(any("S001/reg:w01:g06" in x and "not in the index" in x for x in p), p)
        self.assertTrue(any("left over" in x for x in p), "the unlisted game's file is a leftover: " + str(p))
        code, p = problems(files_fn=lambda d: os.remove(os.path.join(d, by["S001/bowl"]["file"])))
        self.assertTrue(any("file is missing" in x for x in p), p)
        code, p = problems(lambda i: i["games"].append(dict(by[GID], gid="S001/reg:w09:g09")))
        self.assertTrue(any("orphan" in x for x in p), p)
        code, p = problems(lambda i: [e.update(id="reg:w01:g02") for e in i["games"] if e["gid"] == "S001/reg:w01:g06"])
        self.assertTrue(any("given to both" in x for x in p), p)
        code, p = problems(lambda i: [e.update(id="reg:w05:g01") for e in i["games"] if e["gid"] == "S001/reg:w01:g06"])
        self.assertTrue(any("not the archive id" in x for x in p), p)

    def test_every_game_the_feed_lists_must_find_its_replay_under_the_same_id_clubs_and_score(self):
        arch = clone_rows(os.path.join(self.tmp, "x"), [("S001/reg:w01:g06", 1, "regular", 12, None)])
        self.assertEqual(self.export(arch)[0], 0)
        ok = feed_listing(os.path.join(self.tmp, "f_ok"), ["reg:w01:g02", "reg:w01:g06"])
        self.assertEqual(json.loads(self.coverage(arch, feed=ok)[1])["feed_games_without_replay"], [])
        # the feed calls the second game something else: it has no replay under that id, and the report says where the same game is
        renamed = feed_listing(os.path.join(self.tmp, "f_renamed"), ["reg:w01:g02", "reg:w01:g07"])
        code, out, err = self.coverage(arch, feed=renamed)
        rep = json.loads(out)
        self.assertEqual(code, 1)
        self.assertEqual([(x["id"], x["same_game_under"]) for x in rep["feed_games_without_replay"]], [("reg:w01:g07", ["S001/reg:w01:g02", "S001/reg:w01:g06"])])
        # the same id with another result: the site would not link it, and neither does the check pass
        other = feed_listing(os.path.join(self.tmp, "f_score"), ["reg:w01:g02", "reg:w01:g06"])
        rel = os.path.join(other, "releases", load(os.path.join(other, "latest.json"))["release"], "schedule.json")
        sched = load(rel)
        sched["games"][1]["home_score"] += 1
        with open(rel, "wb") as f:
            f.write(PB.canonical(sched))
        code, out, err = self.coverage(arch, feed=other)
        self.assertEqual(code, 1)
        self.assertTrue(json.loads(out)["feed_games_without_replay"][0]["same_id_other_game"])

    # ------------------------------------------------------------------------------------------------ public-data scan of a whole folder
    def test_public_scan_passes_a_real_export_and_fails_on_anything_private(self):
        self.assertEqual(self.export()[0], 0)
        code, out, err = run("replay_public_scan.py", "--playback", self.play)
        self.assertEqual(code, 0, out + err)
        self.assertIn("NOTHING OUTSIDE THE PUBLIC RULES", out)
        e = load(os.path.join(self.play, "index.json"))["games"][0]

        def tampered(change, rename_to_hash=True):
            d = os.path.join(self.tmp, "scan")
            shutil.rmtree(d, ignore_errors=True)
            shutil.copytree(self.play, d)
            g = load(os.path.join(d, e["file"]))
            change(g)
            body = PB.canonical(g)
            h = hashlib.sha256(body).hexdigest()
            rel = f"games/{PB.slug(g['gid'])}.{h[:10]}.json"
            os.remove(os.path.join(d, e["file"]))
            with open(os.path.join(d, rel), "wb") as f:
                f.write(body)
            write_index(d, lambda i: i["games"][0].update(file=rel, sha256=h, bytes=len(body)))   # the hash is repaired: only the content is wrong
            code, out, err = run("replay_public_scan.py", "--playback", d, "--json")
            return code, json.loads(out)["problems"]
        code, p = tampered(lambda g: g["plays"][0].update(rating=80))
        self.assertEqual(code, 1)
        self.assertTrue(any("rating" in x for x in p), p)
        code, p = tampered(lambda g: next(iter(g["roster"].values())).update(overall=71))
        self.assertTrue(any("roster" in x for x in p), p)
        code, p = tampered(lambda g: next(x for x in g["plays"] if x["trace"])["trace"].update(pos="AAAA this is not base64 " * 4))
        self.assertTrue(any("not base64" in x for x in p), p)
        code, p = tampered(lambda g: g["plays"][1].update(text="Run for 4 yards; his coverage rating was low."))
        self.assertTrue(any("mentions something private" in x for x in p), p)
        code, p = tampered(lambda g: g["plays"][1].update(text="A player is injured and leaves the game."))
        self.assertTrue(any("not an injury" in x for x in p), p)
        code, p = tampered(lambda g: g["roster"].update({"999": {"side": "home", "name": "Nova Novrup", "number": 7, "pos": "QB"}}))
        self.assertEqual(code, 0, "a surname that merely contains 'ovr' is not a rating: " + str(p))
        os.makedirs(os.path.join(self.play, "extra"), exist_ok=True)
        with open(os.path.join(self.play, "extra", "notes.txt"), "w") as f:
            f.write("x")
        code, out, err = run("replay_public_scan.py", "--playback", self.play, "--json")
        self.assertEqual(code, 1)
        self.assertTrue(any("index does not name" in x for x in json.loads(out)["problems"]))

    # ------------------------------------------------------------------------------------------------ the game-id mapping
    def test_id_mapping(self):
        """Real ids from the production league's season 1 (archive id on the left, the id the published feed gives the same game on the right): the number
        after `s` is the away club's seed, the number after `v` the home club's; a round's games are numbered by the HOME seed, lowest first."""
        real = {
            "S001/po:Eastern:wild_card:s6v3": "po:Eastern:wild_card:1", "S001/po:Eastern:wild_card:s5v4": "po:Eastern:wild_card:2",
            "S001/po:Eastern:divisional:s4v1": "po:Eastern:divisional:1", "S001/po:Eastern:divisional:s3v2": "po:Eastern:divisional:2",
            "S001/po:Eastern:conference:s2v1": "po:Eastern:conference:1", "S001/po:Western:wild_card:s6v3": "po:Western:wild_card:1",
            "S001/po:Western:wild_card:s5v4": "po:Western:wild_card:2", "S001/po:Western:divisional:s6v1": "po:Western:divisional:1",
            "S001/po:Western:divisional:s5v2": "po:Western:divisional:2", "S001/po:Western:conference:s2v1": "po:Western:conference:1"}
        others = ["S001/reg:w01:g01", "S001/tour:r1:g01", "S001/bowl", "S001/coronation"]
        siblings = list(real) + others
        for gid, want in real.items():
            self.assertEqual(PB.feed_id_for(gid, siblings), want, gid)
        self.assertEqual([PB.feed_id_for(g, siblings) for g in others], ["reg:w01:g01", "tour:r1:g01", "bowl", "coronation"])
        self.assertEqual(len({PB.feed_id_for(g, siblings) for g in siblings}), len(siblings), "no two games share an id")
        # the same archive id in another season is its own game: numbering never mixes seasons
        more = siblings + ["S002/po:Eastern:wild_card:s5v4", "S002/po:Eastern:wild_card:s6v3"]
        self.assertEqual(PB.feed_id_for("S002/po:Eastern:wild_card:s5v4", more), "po:Eastern:wild_card:2")
        self.assertEqual(PB.feed_id_for("S001/po:Eastern:wild_card:s5v4", more), "po:Eastern:wild_card:2")

    def test_a_player_who_is_not_on_a_public_roster_is_numbered_and_unnamed(self):
        db = sqlite3.connect(f"file:{ARCHIVE}?mode=ro", uri=True)
        rec = json.loads(zlib.decompress(db.execute("SELECT payload FROM games").fetchone()[0]))
        traces = {i: zlib.decompress(p) for i, p in db.execute("SELECT play_i, payload FROM traces WHERE game_id = ?", (GID,))}
        db.close()
        events, teams, roster, _ = EXP.feed_public(FEED)
        full = PB.build_game(rec, traces, teams, roster, "reg:w01:g02")
        empty = PB.build_game(rec, traces, teams, {}, "reg:w01:g02")
        self.assertTrue(all("name" in v for v in full["roster"].values()))
        self.assertTrue(all(set(v) == {"side"} for v in empty["roster"].values()), "with no public roster entry the file carries a side and nothing else")
        self.assertTrue(any("player " in p["text"] for p in empty["plays"]))
        self.assertEqual(len(full["plays"]), len(empty["plays"]))
        self.assertEqual(full["traced_plays"], empty["traced_plays"])

    # ------------------------------------------------------------------------------------------------ nothing about the engine moves
    def test_the_replay_tools_are_outside_the_code_fingerprint(self):
        from dfl import persist
        man = persist.code_manifest()
        self.assertTrue(all(k.startswith(("src/dfl/", "data/")) for k in man), "the fingerprint covers the engine and its data only")
        self.assertFalse([k for k in man if "dflsite" in k or "playback" in k or "replay" in k])
        self.assertEqual(persist.fingerprint(), DEPLOYED_FINGERPRINT, "the engine code and data are those of the deployed release")


if __name__ == "__main__":
    unittest.main()
