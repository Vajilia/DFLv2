"""The Ambassador Bowl's ranked-ballot count (src/dfl/stv.py, spec section 7).

Hand-worked counts, an independent second implementation compared on thousands of random ballot sets, and the properties the Droop quota is
chosen for. The second implementation is written from the rules in the spec text (not from stv.py) and differs in structure on purpose.
"""
import os
import random
import subprocess
import sys
import unittest
from fractions import Fraction

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl import stv  # noqa: E402


def oracle(ballots, cands, places, seed):
    """Spec section 7 count, written a second way: ballots as (weight, ranking) pairs rebuilt after every step."""
    voters = len(ballots)
    quota = voters // (places + 1) + 1
    live = {c: True for c in cands}
    book = [[Fraction(1), list(b)] for b in ballots]
    won, out = [], []
    n = 0

    def pick(group):
        group = sorted(group)
        if len(group) == 1:
            return group[0]
        r = random.Random(f"{seed}|step{n}|" + ",".join(str(x) for x in group))
        return group[int(r.random() * len(group))]

    def totals():
        t = {c: Fraction(0) for c, v in live.items() if v}
        for w, b in book:
            for c in b:
                if live.get(c):
                    t[c] += w
                    break
        return t

    while len(won) < places:
        n += 1
        t = totals()
        if len(t) <= places - len(won):
            while t:
                top = max(t.values())
                c = pick([x for x in t if t[x] == top])
                won.append(c)
                del t[c]
                live[c] = False
            break
        top = max(t.values())
        if top >= quota:
            c = pick([x for x in t if t[x] == top])
            for entry in book:
                first = next(x for x in entry[1] if live.get(x))
                if first == c:
                    entry[0] = entry[0] * (top - quota) / top
            won.append(c)
            live[c] = False
        else:
            low = min(t.values())
            c = pick([x for x in t if t[x] == low])
            out.append(c)
            live[c] = False
    while any(live.values()):
        n += 1
        t = totals()
        low = min(t.values())
        c = pick([x for x in t if t[x] == low])
        out.append(c)
        live[c] = False
    return won, won + out[::-1]


class Quota(unittest.TestCase):
    def test_droop_for_eight_voters(self):
        # floor(8 / (places + 1)) + 1, as the spec writes it
        self.assertEqual([stv.droop(8, p) for p in (1, 2, 3, 4, 5)], [5, 3, 3, 2, 2])

    def test_droop_means_no_more_than_places_candidates_can_reach_it(self):
        for voters in range(1, 30):
            for places in range(1, 8):
                q = stv.droop(voters, places)
                self.assertGreater((places + 1) * q, voters)            # places + 1 candidates cannot all have a quota
                self.assertLessEqual(places * q, voters + places)       # and it is the smallest such whole number
                self.assertLessEqual((places + 1) * (q - 1), voters)


class Worked(unittest.TestCase):
    def test_one_place_with_a_transfer(self):
        # 5 voters, 1 place: quota 3. A 2, B 1, C 2: B goes out, her ballot passes to A, who reaches 3.
        ballots = [list("ABC")] * 2 + [list("BAC")] + [list("CBA")] * 2
        r = stv.count(ballots, "ABC", 1, "s")
        self.assertEqual((r["quota"], r["elected"], r["order"]), (3, ["A"], ["A", "C", "B"]))
        self.assertEqual([(x["kind"], x["who"]) for x in r["log"]], [("eliminated", "B"), ("elected", "A"), ("ordered-out", "C")])
        self.assertEqual(r["log"][1]["tally"], "3")

    def test_a_surplus_is_passed_on_in_proportion(self):
        # 6 voters, 2 places: quota 3. A has 4: elected; a quarter of each of her four ballots passes to B (B = 1 + 1 = 2, C = 1); C goes out and her ballot passes to B, who ends on 3.
        ballots = [list("ABC")] * 4 + [list("BAC")] + [list("CAB")]
        r = stv.count(ballots, "ABC", 2, "s")
        self.assertEqual(r["quota"], 3)
        self.assertEqual(r["elected"], ["A", "B"])
        self.assertEqual([(x["kind"], x["who"], x["tally"]) for x in r["log"]],
                         [("elected", "A", "4"), ("eliminated", "C", "1"), ("elected-without-quota", "B", "3")])

    def test_a_quota_of_first_preferences_is_always_elected(self):
        # 8 voters, 3 places, quota 3: A has 3 first preferences at the start and is in, whatever else happens.
        rng = random.Random(5)
        for _ in range(300):
            ballots = [["A"] + rng.sample("BCDEFG", 6) for _ in range(3)]
            for _ in range(5):
                ballots.append(rng.sample("ABCDEFG", 7))
            res = stv.count(ballots, "ABCDEFG", 3, "q")
            self.assertIn("A", res["elected"])

    def test_the_order_lists_everyone_once_elected_first(self):
        rng = random.Random(9)
        cands = list(range(10, 25))
        ballots = [rng.sample(cands, len(cands)) for _ in range(8)]
        r = stv.count(ballots, cands, 4, "x")
        self.assertEqual(sorted(r["order"]), cands)
        self.assertEqual(r["order"][:4], r["elected"])

    def test_bad_input_is_refused(self):
        with self.assertRaises(ValueError):
            stv.count([list("AB"), list("A")], "AB", 1, "s")            # a ballot that does not rank everyone
        with self.assertRaises(ValueError):
            stv.count([list("AB")], "AB", 3, "s")                        # more places than candidates


class Ties(unittest.TestCase):
    def test_a_tie_is_drawn_among_the_tied_alone_and_repeatably(self):
        ballots = [list("ABC"), list("BCA"), list("CAB")] * 2          # 2, 2, 2: the first elimination is drawn
        a = stv.count(ballots, "ABC", 1, "seed-1")
        self.assertEqual(a, stv.count(ballots, "ABC", 1, "seed-1"))
        seen = {stv.count(ballots, "ABC", 1, f"seed-{i}")["elected"][0] for i in range(40)}
        self.assertTrue(seen <= {"A", "B", "C"} and len(seen) >= 2)       # different seeds can draw different winners

    def test_the_order_of_the_ballots_does_not_matter(self):
        rng = random.Random(3)
        cands = list("ABCDEFGH")
        for _ in range(100):
            ballots = [rng.sample(cands, 8) for _ in range(8)]
            places = rng.choice((1, 2, 3, 5))
            r1 = stv.count(ballots, cands, places, "s")
            shuffled = ballots[:]
            rng.shuffle(shuffled)
            self.assertEqual(r1, stv.count(shuffled, cands, places, "s"))
            self.assertEqual(stv.count(shuffled, list(reversed(cands)), places, "s")["order"], r1["order"])      # nor the order of the candidate list

    def test_same_result_under_different_hash_seeds(self):
        code = ("import sys\nsys.path.insert(0, %r)\nfrom dfl import stv\nimport random\nrng = random.Random(1)\nc = list(range(30))\n"
                "b = [rng.sample(c, 30) for _ in range(8)]\n"
                "print([stv.count(b, c, p, 's%%d' %% p)['order'] for p in (1, 3, 5)])\n") % os.path.join(ROOT, "src")
        outs = set()
        for h in (0, 1, 2, 77):
            env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
            env["PYTHONHASHSEED"] = str(h)
            p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
            self.assertEqual(p.returncode, 0, p.stderr[-800:])
            outs.add(p.stdout)
        self.assertEqual(len(outs), 1)


class AgainstTheSecondImplementation(unittest.TestCase):
    def test_random_ballots_8_voters(self):
        rng = random.Random(2024)
        for trial in range(3000):
            n = rng.choice((3, 4, 5, 8, 12, 20))
            cands = rng.sample(range(100, 400), n)
            places = rng.randint(1, min(5, n))
            skew = rng.random() < 0.5
            base = rng.sample(cands, n)
            ballots = []
            for _ in range(8):
                b = rng.sample(cands, n)
                if skew:                                                  # many voters nearly agree: surpluses and ties both occur
                    b = base[:]
                    i = rng.randrange(n - 1)
                    b[i], b[i + 1] = b[i + 1], b[i]
                ballots.append(b)
            r = stv.count(ballots, cands, places, f"t{trial}")
            won, order = oracle(ballots, sorted(cands), places, f"t{trial}")
            self.assertEqual(r["elected"], won, (trial, ballots, places))
            self.assertEqual(r["order"], order, (trial,))

    def test_other_electorates(self):
        rng = random.Random(77)
        for trial in range(600):
            voters = rng.randint(1, 15)
            n = rng.randint(2, 9)
            cands = list(range(n))
            places = rng.randint(1, n)
            ballots = [rng.sample(cands, n) for _ in range(voters)]
            r = stv.count(ballots, cands, places, "z")
            self.assertEqual((r["elected"], r["order"]), oracle(ballots, cands, places, "z"))


if __name__ == "__main__":
    unittest.main()
