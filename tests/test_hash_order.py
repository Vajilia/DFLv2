"""Nothing the simulation does may depend on the order Python happens to iterate a set or a dict of strings in: that order changes from one
process to the next (hash randomisation), and a league that resumes in another process would then differ from one that did not stop.

Found by the 22-player restore soak of 2026-10-30: trade proposals listed a club's picks with `sorted(picks, key=(-round, -year))`, a key that
ties for two picks of the same round and year owned by different original clubs, so the tie was broken by the set's own order. It surfaced
only once a club held two such picks, which depends on earlier trades, so the earlier hash-seed checks and soaks did not meet it.
"""
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CODE = r'''
import sys
sys.path.insert(0, %(src)r)
from types import SimpleNamespace as NS
from dfl.ledger import trading
picks = {(2002, 1, c) for c in ("Dallas", "Cincinnati", "Boston", "Avon", "Zed")} | {(2002, 2, "Dallas"), (2003, 1, "Boston"), (2001, 1, "Avon")}
club = NS(picks=picks, roster=[])
world = NS(L=NS(clubs={"X": club}, draft_completed=2000, players={}), _have=None)
trading._have = lambda w, c: None
print(trading._assets(world, "X", set(), set(), set()))
'''


def run(hashseed):
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["PYTHONHASHSEED"] = str(hashseed)
    p = subprocess.run([sys.executable, "-c", CODE % {"src": os.path.join(ROOT, "src")}], capture_output=True, text=True, env=env)
    if p.returncode:
        raise AssertionError(p.stderr[-1500:])
    return p.stdout.strip()


class PickOrder(unittest.TestCase):
    def test_a_club_s_tradeable_picks_come_in_one_order_whatever_the_hash_seed(self):
        outs = {run(h) for h in (0, 1, 2, 3, 4, 5, 6, 7)}
        self.assertEqual(len(outs), 1, outs)
        order = eval(next(iter(outs)))
        self.assertEqual(order, sorted(order, key=lambda t: (-t[1][1], -t[1][0], t[1][2])))      # lowest rounds first, latest year first, then original club

    def test_a_year_of_the_placeholder_league_is_the_same_under_different_hash_seeds(self):
        code = r"""
import sys
sys.path.insert(0, %r)
from dfl import persist
from dfl.year import Universe
u = Universe(%r, seed=3, engine=False, archive=False)
out = []
for _ in range(49):
    u.step_event()
    out.append(persist.journal_digest(u)[:12])
print(",".join(out))
""" % (os.path.join(ROOT, "src"), os.path.join(ROOT, "data", "season1"))
        procs = []
        for h in (101, 202):
            env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
            env["PYTHONHASHSEED"] = str(h)
            procs.append(subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env))
        outs = []
        for p in procs:
            o, e = p.communicate()
            self.assertEqual(p.returncode, 0, e[-1500:])
            outs.append(o.strip().split(","))
        self.assertEqual(len(outs[0]), 49)
        self.assertEqual(outs[0], outs[1])


if __name__ == "__main__":
    unittest.main()
