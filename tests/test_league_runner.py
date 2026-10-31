"""The development league's runner, its pause/resume switch, its status file and the exporter's heartbeat, with REAL processes.

Leagues here use the placeholder engine (structure without play) so that an event takes about a second; everything else (the journal, the commit, the recovery
from a process that dies in the middle of a commit) is the real code path. The recovery tests compare the chain of an interrupted-and-restarted league with the
chain of one that was never interrupted: equal chains mean no event fired twice and no result changed.
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
sys.path.insert(0, os.path.join(ROOT, "src"))
from dflsite import status as ST  # noqa: E402


def py(*args, **kw):
    return subprocess.run([sys.executable, "-I", *args], capture_output=True, text=True, **kw)


def spawn(*args):
    return subprocess.Popen([sys.executable, "-I", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def new_league(d, name="league.db", seed=7):
    path = os.path.join(d, name)
    r = py(os.path.join(TOOLS, "league_store.py"), "init", path, "--seed", str(seed), "--placeholder", "--snapshot-every", "2")
    assert r.returncode == 0, r.stderr
    return path


def chain_of(path):
    r = py(os.path.join(TOOLS, "league_store.py"), "status", path)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def wait_for(pred, timeout=60, step=0.1):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(step)
    return None


class Runner(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="dflrun")
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
                p.wait()
        shutil.rmtree(self.d, ignore_errors=True)

    def start(self, league, *extra):
        p = spawn(os.path.join(TOOLS, "league_runner.py"), league, "--poll", "0.2", *extra)
        self.procs.append(p)
        return p

    def rs(self):
        return ST.read(os.path.join(self.d, ST.RUNNER_FILE)) or {}

    def test_it_fires_events_up_to_the_limit_and_says_so(self):
        lg = new_league(self.d)
        r = py(os.path.join(TOOLS, "league_runner.py"), lg, "--until-events", "3")
        self.assertEqual(r.returncode, 0, r.stderr)
        s = self.rs()
        self.assertEqual((s["state"], s["seq"], s["error"]), ("stopped", 3, None))
        self.assertEqual(chain_of(lg)["events"], 3)
        r = py(os.path.join(TOOLS, "league_runner.py"), lg, "--until-events", "3")                  # already there: fires nothing more
        self.assertEqual(chain_of(lg)["events"], 3)

    def test_the_limit_is_on_total_events_and_holds_across_restarts(self):
        lg = new_league(self.d)
        for _ in range(3):                                                                            # restart again and again: nothing more than the limit ever fires
            r = py(os.path.join(TOOLS, "league_runner.py"), lg, "--until-events", "2")
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(chain_of(lg)["events"], 2)
        s = self.rs()
        self.assertEqual((s["state"], s["limit_reached"], s["until_events"], s["seq"]), ("stopped", True, 2, 2))
        pub = ST.public_runner(s)
        self.assertTrue(pub["limit_reached"])
        r = py(os.path.join(TOOLS, "league_runner.py"), lg, "--until-events", "3")                   # raising the limit is the owner's deliberate act
        self.assertEqual(chain_of(lg)["events"], 3)
        self.assertEqual(self.rs()["seq"], 3)

    def test_a_restart_never_fires_early_and_never_catches_up(self):
        lg = new_league(self.d)
        pace = 6
        py(os.path.join(TOOLS, "league_runner.py"), lg, "--until-events", "1", "--pace", str(pace))
        t_restart = time.time()
        py(os.path.join(TOOLS, "league_runner.py"), lg, "--until-events", "2", "--pace", str(pace))   # restarted at once: must still wait out the pace
        log = [json.loads(x) for x in open(os.path.join(self.d, "events.jsonl"))]
        self.assertEqual([e["seq"] for e in log], [1, 2])
        t = [time.mktime(time.strptime(e["started_at"], "%Y-%m-%dT%H:%M:%SZ")) - time.timezone for e in log]
        self.assertGreaterEqual(t[1] - t[0], pace - 1.5, "the second event started a full pace after the first (to the second)")

    def test_every_event_leaves_a_measurement_line_and_nothing_private_is_public(self):
        lg = new_league(self.d)
        py(os.path.join(TOOLS, "league_runner.py"), lg, "--until-events", "2")
        log = [json.loads(x) for x in open(os.path.join(self.d, "events.jsonl"))]
        self.assertEqual(len(log), 2)
        for k in ("seq", "key", "seconds", "vmrss_mb", "maxrss_mb", "mem_available_mb", "league_bytes", "archive_bytes", "started_at"):
            self.assertIn(k, log[0])
        self.assertNotIn("events.jsonl", json.dumps(ST.public_runner(self.rs())))

    def test_pause_stops_the_next_event_and_resume_carries_on(self):
        lg = new_league(self.d)
        p = self.start(lg, "--pace", "0.5")
        self.assertTrue(wait_for(lambda: self.rs().get("seq", 0) >= 2), "the runner is firing events")
        c = py(os.path.join(TOOLS, "league_control.py"), "pause", "--dir", self.d, "--reason", "test")
        self.assertEqual(c.returncode, 0, c.stderr)
        self.assertTrue(wait_for(lambda: self.rs().get("state") == "paused", 10))
        frozen = self.rs()["seq"]
        time.sleep(2.0)
        self.assertEqual(self.rs()["seq"], frozen, "no event fires while paused")
        self.assertEqual(chain_of(lg)["events"], frozen)
        st = py(os.path.join(TOOLS, "league_control.py"), "status", "--dir", self.d, "--json")
        self.assertTrue(json.loads(st.stdout)["paused_marker"])
        py(os.path.join(TOOLS, "league_control.py"), "resume", "--dir", self.d)
        self.assertTrue(wait_for(lambda: self.rs().get("seq", 0) > frozen and self.rs().get("state") == "running", 30), "it carries on after resume")
        p.send_signal(signal.SIGTERM)
        self.assertEqual(p.wait(timeout=60), 0, "SIGTERM ends it cleanly")
        self.assertEqual(self.rs()["state"], "stopped")

    def test_the_pace_is_the_least_time_between_event_starts(self):
        lg = new_league(self.d)
        p = self.start(lg, "--pace", "3")
        self.assertTrue(wait_for(lambda: self.rs().get("seq", 0) >= 1))
        t1 = time.time()
        self.assertTrue(wait_for(lambda: self.rs().get("seq", 0) >= 2, 30))
        gap = time.time() - t1
        self.assertGreater(gap + 1.5, 3.0 - 0.01, "the second event started at least `pace` after the first began (the first event itself took ~1.5 s)")
        self.assertTrue(self.rs().get("next_event_due") is not None or self.rs()["seq"] >= 2)
        p.terminate()
        p.wait(timeout=60)

    def test_only_one_runner_at_a_time(self):
        lg = new_league(self.d)
        py(os.path.join(TOOLS, "league_control.py"), "pause", "--dir", self.d)
        a = self.start(lg)
        self.assertTrue(wait_for(lambda: self.rs().get("state") == "paused", 20))
        b = py(os.path.join(TOOLS, "league_runner.py"), lg)
        self.assertEqual(b.returncode, 75)
        self.assertEqual(chain_of(lg)["events"], 0)
        a.send_signal(signal.SIGKILL)                                                                 # the lock dies with its holder
        a.wait()
        py(os.path.join(TOOLS, "league_control.py"), "resume", "--dir", self.d)
        c = py(os.path.join(TOOLS, "league_runner.py"), lg, "--until-events", "1", timeout=120)
        self.assertEqual(c.returncode, 0, c.stderr)

    def test_a_runner_that_cannot_run_reports_an_error_and_publishes_no_message(self):
        r = py(os.path.join(TOOLS, "league_runner.py"), os.path.join(self.d, "missing.db"), "--control-dir", self.d)
        self.assertEqual(r.returncode, 1)
        s = self.rs()
        self.assertEqual(s["state"], "error")
        self.assertTrue(s["error"]["type"])
        pub = ST.public_runner(s)
        self.assertNotIn("message", json.dumps(pub))
        self.assertNotIn("pid", json.dumps(pub))

    def test_a_process_that_dies_in_the_middle_of_a_commit_leaves_no_duplicate_and_no_changed_result(self):
        n = 6
        ref = new_league(self.d, "ref.db")
        self.assertEqual(py(os.path.join(TOOLS, "league_runner.py"), ref, "--until-events", str(n), "--control-dir", os.path.join(self.d, "refctl")).returncode, 0)
        want = chain_of(ref)
        for k in (1, 3):                                                                              # die inside the 1st and the 3rd commit of the first process
            lg = new_league(self.d, f"t{k}.db")
            ctl = os.path.join(self.d, f"ctl{k}")
            died = py(os.path.join(TOOLS, "league_runner.py"), lg, "--until-events", str(n), "--control-dir", ctl, "--die-inside", str(k))
            self.assertEqual(died.returncode, 9, died.stderr)
            self.assertLess(chain_of(lg)["events"], n)
            done = py(os.path.join(TOOLS, "league_runner.py"), lg, "--until-events", str(n), "--control-dir", ctl)
            self.assertEqual(done.returncode, 0, done.stderr)
            got = chain_of(lg)
            self.assertEqual(got["events"], n)
            self.assertEqual(got["chain"], want["chain"], f"killed inside commit {k}: the chain must equal the never-interrupted league's")

    def test_kill_nine_at_a_random_moment_then_restart_gives_the_same_league(self):
        n = 6
        ref = new_league(self.d, "ref.db")
        py(os.path.join(TOOLS, "league_runner.py"), ref, "--until-events", str(n), "--control-dir", os.path.join(self.d, "refctl"))
        want = chain_of(ref)
        lg = new_league(self.d, "k.db")
        ctl = os.path.join(self.d, "ctlk")
        p = spawn(os.path.join(TOOLS, "league_runner.py"), lg, "--until-events", str(n), "--control-dir", ctl, "--poll", "0.2")
        self.procs.append(p)
        self.assertTrue(wait_for(lambda: (ST.read(os.path.join(ctl, ST.RUNNER_FILE)) or {}).get("seq", 0) >= 2, 60))
        time.sleep(0.6)                                                                                # somewhere inside the next event
        p.send_signal(signal.SIGKILL)
        p.wait()
        done = py(os.path.join(TOOLS, "league_runner.py"), lg, "--until-events", str(n), "--control-dir", ctl)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(chain_of(lg)["chain"], want["chain"])


class Updater(unittest.TestCase):
    """The exporter's heartbeat: status.json says whether the feed is current, and a failure is reported without hiding the last good state."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="dflupd")
        self.feed = os.path.join(self.d, "feed")
        self.lg = new_league(self.d)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def export(self, *extra, league=None):
        return py(os.path.join(TOOLS, "export_public_feed.py"), league or self.lg, "--out", self.feed, *extra)

    def run_to(self, n):
        r = py(os.path.join(TOOLS, "league_runner.py"), self.lg, "--until-events", str(n))
        self.assertEqual(r.returncode, 0, r.stderr)

    def status(self):
        return ST.read(os.path.join(self.feed, ST.FILE))

    def test_publishes_new_events_and_skips_when_nothing_is_new_but_still_reports(self):
        self.run_to(2)
        r = self.export("--skip-if-current")
        self.assertEqual(r.returncode, 0, r.stderr)
        first = json.loads(r.stdout)
        self.assertTrue(first["published"])
        s1 = self.status()
        self.assertEqual((s1["league_events"], s1["published_events"], s1["export"]["ok"]), (2, 2, True))
        self.assertEqual(s1["runner"]["state"], "stopped")
        time.sleep(1.1)
        r2 = self.export("--skip-if-current")
        self.assertEqual(json.loads(r2.stdout)["skipped"], True)
        s2 = self.status()
        self.assertEqual(s2["release"], s1["release"])
        self.assertGreater(s2["checked_at"], s1["checked_at"], "the heartbeat moves even when nothing is published")
        self.assertEqual(s2["export"]["last_publish_at"], s1["export"]["last_publish_at"])
        self.run_to(3)
        r3 = self.export("--skip-if-current")
        self.assertTrue(json.loads(r3.stdout)["published"])
        s3 = self.status()
        self.assertEqual((s3["league_events"], s3["published_events"]), (3, 3))
        self.assertNotEqual(s3["release"], s1["release"])

    def test_a_failed_export_is_reported_and_the_live_release_stays(self):
        self.run_to(2)
        self.export()
        good = self.status()
        live = open(os.path.join(self.feed, "latest.json")).read()
        time.sleep(1.1)
        r = self.export("--skip-if-current", league=os.path.join(self.d, "gone.db"))
        self.assertEqual(r.returncode, 1)
        s = self.status()
        self.assertFalse(s["export"]["ok"])
        self.assertTrue(s["export"]["error"]["type"])
        self.assertNotIn("gone.db", json.dumps(s), "no path or message in the public status")
        self.assertEqual(s["export"]["last_success_at"], good["export"]["last_success_at"], "the last good time survives a failing run")
        self.assertEqual(open(os.path.join(self.feed, "latest.json")).read(), live, "the live release is untouched")

    def test_the_status_file_carries_nothing_private(self):
        self.run_to(1)
        self.export()
        raw = json.dumps(self.status())
        for bad in ("pid", "seed", "/tmp", "message", "traceback"):
            self.assertNotIn(bad, raw)


if __name__ == "__main__":
    unittest.main()
