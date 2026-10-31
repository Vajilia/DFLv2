"""Step 7 (roster, contract and transaction verification), part 1: the ledger's numbers are the Design Document's numbers.

The document is read as text. Each number is taken from the sentence or table row that states it, with a pattern narrow enough to fail
loudly if the sentence is reworded, and compared with the ledger's constant AND with the independent model's (`tests/ledger_oracle.py`),
which was typed from the document, not from the ledger. If the document and the code disagree, the document wins (it is authoritative);
if a pattern stops matching, the document changed and the rule must be re-verified by a person.
"""
import glob
import os
import re
import sys
import unittest
from fractions import Fraction as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import ledger_oracle as O  # noqa: E402
from dfl.ledger import numbers as N, rules as R  # noqa: E402
from dfl.ledger.positions import MINIMUMS  # noqa: E402


def spec_text():
    paths = sorted(glob.glob(os.path.join(ROOT, "docs", "spec", "DFL_Design_Document_Ed*.md")),
                   key=lambda p: [int(x) for x in re.findall(r"\d+", os.path.basename(p))])
    with open(paths[-1], encoding="utf-8") as f:
        return f.read()


def section(text, start, end):
    a = text.index(start)
    return text[a:text.index(end, a)]


TEXT = spec_text()
S0 = section(TEXT, "## §0 Current values", "## §1")
S11 = section(TEXT, "## §11 Positions, rosters, contracts, cap", "## §12")
S12 = section(TEXT, "## §12 Draft, trades, waivers", "## §13")


def row(label):
    """The value cell of the §0 row whose label cell starts with `label`, split on the middle dot."""
    for line in S0.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 2 and cells[0].startswith(label):
            return [x.strip() for x in cells[1].split("·")]
    raise AssertionError(f"§0 has no row starting {label!r}: the document changed, re-verify")


def money(s):
    m = re.fullmatch(r"\$([\d,]+(?:\.\d+)?)", s)
    assert m, s
    return F(m.group(1).replace(",", ""))


class Section0(unittest.TestCase):
    def test_cap_reserve_minimum_comp_threshold(self):
        cap, reserve, minimum, comp = [money(x) for x in row("Cap · reserve")]
        self.assertEqual((cap, reserve, minimum), (R.CAP, R.RESERVE, R.MIN_SALARY))
        self.assertEqual((cap, reserve, minimum), (O.CAP, O.RESERVE, O.MIN_SALARY))
        self.assertEqual(comp, 75)

    def test_rookie_scale_and_option(self):
        scale, option = row("Rookie scale")
        got = [money(x) for x in scale.split("/")]
        self.assertEqual(len(got), 7)
        self.assertEqual(tuple(got), R.ROOKIE_SCALE)
        self.assertEqual(tuple(got), tuple(F(x) for x in O.ROOKIE_SCALE))
        self.assertEqual(money(option), R.R1_OPTION)

    def test_roster_squad_ceiling_required_places_bowl(self):
        roster, squad, ceiling, required, bowl = [int(x) for x in row("Roster · practice squad")]
        self.assertEqual((roster, squad, ceiling, required), (R.ROSTER_MAX, R.PS_MAX, R.PRESEASON_CEILING, R.REQUIRED_PLACES))
        self.assertEqual((roster, squad, ceiling, required), (O.ROSTER, O.SQUAD, O.CEILING, O.REQUIRED_PLACES))
        self.assertEqual(bowl, 53)                                                  # built by the Bowl step, which is marked INCOMPLETE in §7

    def test_contracts_counted_before_the_cut_and_vested_seasons(self):
        counted, vested = row("Contracts counted against the cap before the final cut")
        self.assertEqual(int(re.match(r"\d+", counted).group()), R.CAP_COUNTED_BEFORE_CUT)
        self.assertEqual(int(re.match(r"\d+", counted).group()), O.COUNTED_BEFORE_CUT)
        self.assertEqual(int(vested), R.VESTED_SEASONS)

    def test_draft_shape(self):
        text = row("Draft")[0]
        m = re.fullmatch(r"(\d+) rounds × (\d+) = (\d+) picks", text)
        self.assertTrue(m, text)
        rounds, per, total = map(int, m.groups())
        self.assertEqual(rounds * per, total)
        self.assertEqual(len(R.ROOKIE_SCALE), rounds)

    def test_paid_weeks_and_the_other_rule_numbers_in_section_11(self):
        self.assertEqual(R.PAID_WEEKS, int(re.search(r"over the (\d+) paid regular-season weeks", S11).group(1)))
        self.assertEqual(R.PAID_WEEKS, O.PAID_WEEKS)
        m = re.search(r"at most (\d+) of whom may be veterans with more than (\d+) credited seasons", S11)
        self.assertEqual((R.PS_VETERANS_MAX, R.PS_VETERAN_AFTER), (int(m.group(1)), int(m.group(2))))
        m = re.search(r"Game-day active roster (\d+), or (\d+) with fewer than (\d+) offensive linemen", S11)
        self.assertEqual((R.ACTIVE_LIMIT, R.ACTIVE_LIMIT_FEW_OL, R.OL_FOR_FULL), tuple(map(int, m.groups())))
        m = re.search(r"\(out at least (four) games; at most (eight) players per club per season may be designated to return\)", S11)
        words = {"four": 4, "eight": 8}
        self.assertEqual((R.IR_MIN_GAMES, R.IR_RETURNS_MAX), (words[m.group(1)], words[m.group(2)]))
        m = re.search(r"elevate up to (two) practice-squad players for a game \(each player at most (three) times", S11)
        self.assertEqual((R.ELEVATIONS_PER_GAME, R.ELEVATIONS_PER_PLAYER), (words2(m.group(1)), words2(m.group(2))))
        m = re.search(r"At most (five) covered years and (five) bonus proration years", S11)
        self.assertEqual((R.MAX_COVERED_YEARS, R.MAX_PRORATION_YEARS), (words2(m.group(1)), words2(m.group(2))))
        self.assertIn("dead money ≤ $735", S11)
        self.assertEqual(R.VIABILITY_MAX_COMMITTED, 735)
        self.assertIn("flag below $50", S11)

    def test_waiver_priority_switches_to_the_standings_after_four_league_weeks(self):
        m = re.search(r"Priority is last season's draft order until (four) league weeks finish, then weekly from current standings", S11)
        self.assertTrue(m)
        self.assertEqual(R.WAIVER_STANDINGS_AFTER_WEEKS, 4)

    def test_midseason_deals_expire_at_year_close(self):
        self.assertIn("Midseason deals pay remaining weeks only and expire at year close", S11)

    def test_trade_window_last_paid_week(self):
        # "ending Friday 23:59 before the first game of numbered week 9": the last paid week before it is 9 on the ledger's clock (calendar week 10 holds numbered week 9)
        self.assertIn("before the first game of numbered week 9", S12)
        self.assertEqual(R.TRADE_LAST_PAID_WEEK, 9)


def words2(w):
    return {"two": 2, "three": 3, "five": 5}[w]


class PositionsAndNumbers(unittest.TestCase):
    def test_positional_minimums(self):
        text = re.search(r"Positional minimums \(the 48 required places\): ([^.]*)\.", S11).group(1)
        want, hb_rule = {}, None
        for part in text.split(";"):
            part = part.strip()
            if part.startswith("HB"):
                hb_rule = part
                continue
            for item in part.split(","):
                pos, n = item.split()
                want[pos] = int(n)
        self.assertEqual(hb_rule, "HB 3, or HB 2 and FB 1")
        self.assertEqual(want, MINIMUMS)
        self.assertEqual(sum(want.values()) + 3, R.REQUIRED_PLACES)

    def test_jersey_blocks(self):
        block_text = re.search(r"Primary blocks: ([^.]*)\.", S11).group(1)
        want = {}
        for item in block_text.split("·"):
            rng, poss = item.strip().split(" ", 1)
            lo, hi = map(int, rng.split("-"))
            for pos in poss.split(","):
                want[pos.strip()] = (lo, hi)
        self.assertEqual(want, dict(N.PRIMARY))
        self.assertEqual(want, O.PRIMARY)

    def test_overflow_blocks(self):
        m = re.search(r"WR may use open numbers (\d+)-(\d+), TE open (\d+)-(\d+), LB open (\d+)-(\d+), DL open (\d+)-(\d+)", S11)
        wr, te, lb, dl = [(int(m.group(i)), int(m.group(i + 1))) for i in (1, 3, 5, 7)]
        want = {"WR": wr, "TE": te, "ROLB": lb, "MLB": lb, "LOLB": lb, "LE": dl, "RE": dl, "DT": dl}
        self.assertEqual(want, dict(N.OVERFLOW))
        self.assertEqual(want, O.OVERFLOW)


S3 = section(TEXT, "## §3 League structure", "## §4")


class Edition855(unittest.TestCase):
    """Edition 8.55 (owner's rulings of 2026-10-30): the playoff, Coronation and cascade statements in the document are the code's."""

    def test_the_postseason_row_matches_the_standings_constants(self):
        from dfl import standings
        (cell,) = row("Postseason")
        self.assertTrue(cell.startswith("12 clubs (6 per conference), 11 games"), cell)
        self.assertEqual(standings.PLAYOFF_CLUBS_PER_CONFERENCE * 2, 12)
        self.assertEqual(standings.PLAYOFF_CLUBS_PER_CONFERENCE, 4 + standings.WILD_CARDS)           # four division winners and the wild cards
        per_conf = len(standings.wild_card_pairs()) + 2 + 1                                         # wild card games, two divisional games, the championship
        self.assertEqual(per_conf * 2 + 1, 11)                                                      # and the Coronation
        self.assertEqual(2 ** 11, 2048)
        self.assertIn("2,048", " ".join(row("Bracket winner paths")))

    def test_the_bracket_text(self):
        from dfl import standings
        self.assertIn("seed 3 hosts seed 6 and seed 4 hosts seed 5", S3)
        self.assertEqual(standings.wild_card_pairs(), [(3, 6), (4, 5)])
        self.assertIn("seed 1 hosts the lowest surviving seed and seed 2 hosts the other survivor", S3)
        self.assertEqual(standings.divisional_pairs([1, 2, 4, 5]), [(1, 5), (2, 4)])
        self.assertIn("Seeds 1 and 2 have a bye", S3)

    def test_the_coronation_venue_is_permanent_and_neutral(self):
        from dfl import league
        m = re.search(r"always played at \*\*([^*]+)\*\*", S3)
        self.assertEqual(m.group(1), league.CORONATION_VENUE)
        self.assertIn(league.CORONATION_VENUE, " ".join(row("Diamond Coronation venue")))
        self.assertIn("always neutral", S3)
        self.assertNotIn("fan vote held in June", S3)

    def test_the_cascade_order(self):
        from dfl import standings
        m = re.search(r"\(1\) overall wins; (.*?)\(10\) the seeded draw", S3)
        steps = [re.sub(r"^\(\d+\) ", "", x.strip().rstrip(";").rstrip(",")) for x in ("(1) overall wins; " + m.group(1)).split("; ") if x.strip()]
        names = ["overall wins", "overall win %", "division wins", "division win %", "conference wins", "conference win %", "point differential",
                 "strength of schedule", "strength of victory"]
        self.assertEqual(steps, names)
        self.assertEqual(standings.CASCADE[:-1], tuple(n.replace("overall ", "").replace(" ", "_").replace("%", "pct").replace("win_pct", "win_pct") for n in names))
        self.assertEqual(standings.CASCADE[-1], "seeded_draw")

    def test_the_designation_is_about_weeks_1_to_4_and_80_games(self):
        from dfl import standings
        self.assertEqual(standings.CORONATION_AGGREGATE_WEEKS, 4)
        self.assertIn("numbered weeks 1–4 (80 games)", S3)

    def test_the_year_has_47_events_and_no_venue_events(self):
        from dfl import calendar as C
        self.assertIn("47 dated events a year", TEXT)
        keys = [k for _, k, *_ in C.year_events(2000)]
        self.assertEqual(len(keys), 47)
        self.assertFalse([k for k in keys if "venue" in k])
        self.assertNotIn("| Coronation venue vote |", TEXT)

    def test_the_draft_returner_block_matches_twelve_playoff_clubs(self):
        self.assertIn("slots 37 minus the block size through 36", S12)             # 8 returning + 28 non-playoff clubs = 36

    def test_the_first_round_option_text(self):
        self.assertIn("guaranteed in full", S11)
        self.assertIn("Draft Round 1", S11)
        self.assertEqual((R.R1_OPTION, R.R1_OPTION_GUARANTEED), (F(35), True))


if __name__ == "__main__":
    unittest.main()
