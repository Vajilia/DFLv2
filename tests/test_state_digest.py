"""The journal's state check (save schema 4) sees all of the league's state, and sees it the same way every time.

`persist.journal_digest` is recorded after every event and checked when a league is recovered by replay. Before schema 4 it covered the
ledger, the cursors, two of the six random generators and a few books; a change to the other four generators or to most of the world was
invisible to it. These tests pin what the digest must do now:

  * advancing any one of the six generators changes the digest, and only that generator's part of it;
  * every attribute of the universe, the world, the ledger, the resolver and the season run is in the digest, except the cache-like names
    `neutral.EXCLUDED`, each of which is shown to be harmless to leave out;
  * states that are the same by value have the same digest however they were built (decoded again, unpickled, rebuilt from the seed,
    built in another process under another hash seed), and states that differ by one value do not;
  * recovery by replay refuses a snapshot whose generator, in any of the four the old digest could not see, is not where the journal says.
"""
import os
import pickle
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl import neutral, persist  # noqa: E402
from dfl.year import Universe  # noqa: E402

TABLES = os.path.join(ROOT, "data", "season1")

GENERATORS = {                                   # the league's six random generators, by the path a diagnostic reports
    "u.rng": lambda u: u.rng,
    "u.world.rng": lambda u: u.world.rng,
    "u.world.dev_rng": lambda u: u.world.dev_rng,
    "u.world.soul_rng": lambda u: u.world.soul_rng,
    "u.world.coach_rng": lambda u: u.world.coach_rng,
    "u.world.gov_rng": lambda u: u.world.gov_rng,
}


class Probe:
    """A value no league holds: replacing an attribute with it must change the digest unless that attribute is excluded."""
    marker = "probe"


def league(events=14, seed=1):
    u = Universe(TABLES, seed=seed, engine=False)
    for _ in range(events):
        u.step_event()
    return u


class Generators(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.u = league()

    def test_the_league_has_exactly_these_six_generators(self):
        found = neutral.rng_states(self.u)
        self.assertEqual(sorted(found), sorted(GENERATORS), "a generator was added or removed: say so in GENERATORS and in PERSISTENCE.md")
        self.assertEqual(len({id(g(self.u)) for g in GENERATORS.values()}), 6, "the six are six distinct generators")

    def test_advancing_each_generator_changes_the_digest_and_its_own_part_only(self):
        u = self.u
        base, parts = persist.journal_digest(u), neutral.digest_parts(u)
        for path, get in GENERATORS.items():
            with self.subTest(generator=path):
                g = get(u)
                saved = g.getstate()
                try:
                    g.random()
                    self.assertNotEqual(persist.journal_digest(u), base, "one draw from this generator is invisible to the journal digest")
                    now = neutral.digest_parts(u)
                    changed = {k for k in parts if parts[k] != now.get(k)}
                    own = {k for k in changed if k == path}
                    other_generators = {k for k in changed if k in GENERATORS and k != path}
                    self.assertEqual(own, {path})
                    self.assertEqual(other_generators, set(), "advancing one generator changed another generator's part")
                finally:
                    g.setstate(saved)
                self.assertEqual(persist.journal_digest(u), base, "restoring the generator's state must restore the digest")

    def test_every_kind_of_draw_is_seen_including_the_cached_gaussian(self):
        u = self.u
        for path, get in GENERATORS.items():
            g = get(u)
            saved = g.getstate()
            seen = {persist.journal_digest(u)}
            try:
                for draw in (lambda: g.gauss(0, 1), lambda: g.gauss(0, 1), lambda: g.randint(1, 6), lambda: g.shuffle([1, 2, 3, 4]),
                             lambda: g.choice("abc"), lambda: g.getrandbits(5)):
                    draw()
                    d = persist.journal_digest(u)
                    self.assertNotIn(d, seen, f"{path}: a draw left the digest unchanged")
                    seen.add(d)
            finally:
                g.setstate(saved)

    def test_a_generator_is_distinguished_by_where_it_sits_not_only_by_its_state(self):
        u = self.u
        base = persist.journal_digest(u)
        a, b = u.world.dev_rng, u.world.soul_rng
        sa, sb = a.getstate(), b.getstate()
        try:
            a.setstate(sb)
            b.setstate(sa)
            self.assertNotEqual(persist.journal_digest(u), base, "exchanging two generators' states went unnoticed")
        finally:
            a.setstate(sa)
            b.setstate(sb)
        self.assertEqual(persist.journal_digest(u), base)


class Coverage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.u = league(events=14)                    # past the venue vote, so a season run exists
        assert cls.u.run is not None

    def targets(self):
        u = self.u
        return {"Universe": u, "World": u.world, "Ledger": u.world.L, "resolver": u.resolver, "SeasonRun": u.run}

    def test_every_attribute_is_in_the_digest_or_excluded_with_a_reason(self):
        base = persist.journal_digest(self.u)
        checked = 0
        for label, obj in self.targets().items():
            for name in sorted(vars(obj)):
                old = vars(obj)[name]
                setattr(obj, name, Probe())
                try:
                    changed = persist.journal_digest(self.u) != base
                finally:
                    setattr(obj, name, old)
                checked += 1
                if name in neutral.EXCLUDED:
                    self.assertFalse(changed, f"{label}.{name} is listed as excluded but the digest sees it")
                    self.assertTrue(neutral.EXCLUDED[name].strip(), f"{name} is excluded without a stated reason")
                else:
                    self.assertTrue(changed, f"{label}.{name} is not covered by the journal digest and is not in neutral.EXCLUDED")
        self.assertGreater(checked, 80)
        self.assertEqual(persist.journal_digest(self.u), base)

    def test_what_is_excluded_is_a_cache_or_a_hand_off_and_nothing_else(self):
        self.assertEqual(set(neutral.EXCLUDED), set(neutral.DIGEST_IGNORE))
        self.assertEqual(sorted(neutral.EXCLUDED), ["_memo", "_units", "archive", "archive_buffer", "dir"],
                         "an exclusion was added or removed: justify it in neutral.EXCLUDED and PERSISTENCE.md and update this list")

    def test_nested_values_are_covered_too(self):
        u = self.u
        base = persist.journal_digest(u)
        some = next(iter(u.world.L.players.values()))
        for attr, value in (("ovr", some.ovr + 1), ("age", some.age + 1), ("where", "elsewhere"), ("last_number", 99), ("pool_years", 7)):
            old = getattr(some, attr)
            setattr(some, attr, value)
            try:
                self.assertNotEqual(persist.journal_digest(u), base, f"player.{attr} is not covered")
            finally:
                setattr(some, attr, old)
        card = u.world.cards[some.pid]
        old = dict(card.attrs)
        key = sorted(card.attrs)[0]
        card.attrs[key] = card.attrs[key] + 1
        try:
            self.assertNotEqual(persist.journal_digest(u), base, "a character card's attribute is not covered")
        finally:
            card.attrs.clear()
            card.attrs.update(old)
        self.assertEqual(persist.journal_digest(u), base)

    def test_caches_do_not_change_it(self):
        u = self.u
        base = persist.journal_digest(u)
        filled = [p.contract for p in u.world.L.players.values() if p.contract is not None and p.contract._memo]
        self.assertTrue(filled, "no contract has a filled cache in this state, so this test would prove nothing")
        for k in filled:
            k._memo.clear()
        self.assertEqual(persist.journal_digest(u), base)
        u.world.L.contracts_of(next(iter(u.world.L.clubs)))
        for sq in getattr(u.resolver, "squads", {}).values():
            sq._units = None
        self.assertEqual(persist.journal_digest(u), base)

    def test_dict_order_counts_and_set_order_does_not(self):
        a = {"x": 1, "y": [1, 2], "z": {"k": 1}}
        b = {"z": {"k": 1}, "y": [1, 2], "x": 1}
        self.assertNotEqual(neutral.deep_digest(a), neutral.deep_digest(b), "code iterates dicts in order, so the order is state")
        s1, s2 = set(), set()
        for v in (1, 9, 17, 25, 33):
            s1.add(v)
        for v in (33, 25, 17, 9, 1):
            s2.add(v)
        self.assertEqual(neutral.deep_digest({"s": s1}), neutral.deep_digest({"s": s2}))
        self.assertNotEqual(neutral.deep_digest({"s": s1}), neutral.deep_digest({"s": s1 | {2}}))
        self.assertNotEqual(neutral.deep_digest([1, 2]), neutral.deep_digest((1, 2)))
        self.assertNotEqual(neutral.deep_digest(1), neutral.deep_digest(1.0))
        self.assertNotEqual(neutral.deep_digest(True), neutral.deep_digest(1))


class SameStateSameDigest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.u = league(events=20)
        cls.want = persist.journal_digest(cls.u)

    def test_independently_decoded_copies_agree_with_each_other_and_the_original(self):
        doc = neutral.encode(self.u)
        first, second = neutral.decode(doc), neutral.decode(doc)
        self.assertIsNot(first.world, second.world)
        self.assertEqual(persist.journal_digest(first), persist.journal_digest(second))
        self.assertEqual(persist.journal_digest(first), self.want)

    def test_a_document_round_trip_through_json_agrees(self):
        raw = neutral.dumps_doc(self.u, {})
        back = neutral.decode(neutral.loads_doc(raw)["state"])
        self.assertEqual(persist.journal_digest(back), self.want)

    def test_an_unpickled_copy_agrees(self):
        raw, _ = persist.dumps(self.u)
        a, b = persist.loads(raw), persist.loads(raw)
        self.assertEqual(persist.journal_digest(a), persist.journal_digest(b))
        self.assertEqual(persist.journal_digest(a), self.want)

    def test_a_league_rebuilt_from_its_seed_agrees_and_another_seed_does_not(self):
        self.assertEqual(persist.journal_digest(league(events=20)), self.want)
        self.assertNotEqual(persist.journal_digest(league(events=20, seed=2)), self.want)
        self.assertNotEqual(persist.journal_digest(league(events=19)), self.want)

    def test_one_changed_value_anywhere_makes_a_different_state(self):
        doc = neutral.encode(self.u)
        other = neutral.decode(doc)
        other.world.gov_rng.random()
        self.assertNotEqual(persist.journal_digest(other), self.want)
        other = neutral.decode(doc)
        next(iter(other.world.L.players.values())).ovr += 1
        self.assertNotEqual(persist.journal_digest(other), self.want)

    def test_another_process_under_another_hash_seed_agrees(self):
        code = ("import sys; sys.path.insert(0, %r); from dfl.year import Universe; from dfl import persist\n"
                "u = Universe(%r, seed=1, engine=False)\n"
                "for _ in range(20): u.step_event()\n"
                "print(persist.journal_digest(u))" % (os.path.join(ROOT, "src"), TABLES))
        outs = []
        for hashseed in ("1", "424242"):
            p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=dict(os.environ, PYTHONHASHSEED=hashseed))
            self.assertEqual(p.returncode, 0, p.stderr[-800:])
            outs.append(p.stdout.strip())
        self.assertEqual(outs, [self.want, self.want])


class TheJournalUsesIt(unittest.TestCase):
    """Recovery by replay compares the journal's digest with the replayed state, so a divergence anywhere in the state is refused."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="dfldigest")
        cls.path = os.path.join(cls.tmp, "league.db")
        r = persist.Runner.create(cls.path, TABLES, seed=1, snapshot_every=1000, engine=False, archive=False)
        r.run_events(3)
        cls.rows = r.db.execute("SELECT seq, digest FROM journal ORDER BY seq").fetchall()
        cls.live = persist.journal_digest(r.u)
        r.close()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_file_records_the_algorithm_and_the_journal_holds_the_full_digest(self):
        self.assertEqual(persist.FORMAT_DIGESTS[persist.FORMAT_VERSION], neutral.DIGEST_VERSION)
        c = persist.compatibility(self.path)
        self.assertEqual((c["format"], c["schema"], c["code"]), (persist.FORMAT_VERSION, "ok", "exact"))
        db = sqlite3.connect(self.path)
        try:
            meta = persist.read_meta(db)
        finally:
            db.close()
        self.assertEqual(meta["digest_algorithm"], neutral.DIGEST_VERSION)
        self.assertEqual(self.rows[-1][1], self.live)                                # the journal's last digest is the digest of the live state

    def test_recovery_reproduces_it(self):
        r = persist.Runner.open(self.path, heal=False)
        self.assertEqual((r.seq, persist.journal_digest(r.u)), (3, self.live))
        r.close()

    def test_a_snapshot_whose_generator_is_off_is_refused_for_each_of_the_four_the_old_digest_missed(self):
        for name in ("dev_rng", "soul_rng", "coach_rng", "gov_rng"):
            with self.subTest(generator=name):
                copy = os.path.join(self.tmp, f"tampered_{name}.db")
                shutil.copy(self.path, copy)
                db = sqlite3.connect(copy)
                seq, raw = db.execute("SELECT seq, payload FROM snapshots ORDER BY seq LIMIT 1").fetchone()
                u = persist.loads(raw)
                getattr(u.world, name).random()                                      # one draw off, nothing else changed
                new, sha = persist.dumps(u)
                db.execute("UPDATE snapshots SET payload = ?, sha256 = ?, size = ? WHERE seq = ?", (new, sha, len(new), seq))
                db.commit()
                db.close()
                with self.assertRaises(persist.ReplayDivergence):
                    persist.Runner.open(copy, heal=False)

    def test_an_untampered_copy_still_recovers(self):
        copy = os.path.join(self.tmp, "control.db")
        shutil.copy(self.path, copy)
        r = persist.Runner.open(copy, heal=False)
        self.assertEqual(persist.journal_digest(r.u), self.live)
        r.close()


if __name__ == "__main__":
    unittest.main()
