"""Team identity pins from the approved 2026-10-30 workbook: stable team ids, and the names the owner approved."""
import csv
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Identity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "data", "season1", "TEAMS.csv"), encoding="utf-8", newline="") as f:
            cls.teams = {int(r["team_id"]): r for r in csv.DictReader(f)}

    def test_48_clubs_with_stable_ids(self):
        self.assertEqual(sorted(self.teams), list(range(1, 49)))

    def test_team_10_is_the_new_york_gladiators_not_the_goliaths(self):
        t = self.teams[10]
        self.assertEqual((t["abbr"], t["team_city"], t["team_name"]), ("NYG", "New York", "Gladiators"))
        self.assertNotIn("Goliaths", {r["team_name"] for r in self.teams.values()})

    def test_team_37_is_the_havana_mariposas(self):
        t = self.teams[37]
        self.assertEqual((t["abbr"], t["team_city"], t["team_name"]), ("HAV", "Havana", "Mariposas"))


if __name__ == "__main__":
    unittest.main()
