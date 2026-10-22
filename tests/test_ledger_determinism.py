"""Determinism: the same seed gives the same ledger digest in separate processes with different hash seeds."""
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE = (
    "import sys; sys.path.insert(0, %r)\n"
    "from dfl.ledger.world import World\n"
    "w = World([f'C{i:02d}' for i in range(1, 9)], seed=7, reckless=['C01'])\n"
    "for y in range(3): w.step_year(first=(y == 0))\n"
    "print(w.L.digest())\n" % os.path.join(ROOT, "src")
)


def digest(hashseed):
    env = dict(os.environ, PYTHONHASHSEED=str(hashseed))
    return subprocess.run([sys.executable, "-I", "-c", CODE], env=env, capture_output=True, text=True, check=True).stdout.strip()


class Determinism(unittest.TestCase):
    def test_same_seed_same_digest_across_processes(self):
        a, b, c = digest(0), digest(12345), digest(999)
        self.assertEqual(len(a), 64)
        self.assertEqual(a, b)
        self.assertEqual(a, c)

    def test_different_seed_different_digest(self):
        env = dict(os.environ, PYTHONHASHSEED="0")
        other = subprocess.run([sys.executable, "-I", "-c", CODE.replace("seed=7", "seed=8")], env=env, capture_output=True, text=True, check=True).stdout.strip()
        self.assertNotEqual(other, digest(0))


if __name__ == "__main__":
    unittest.main()
