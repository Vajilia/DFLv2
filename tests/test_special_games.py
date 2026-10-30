"""Regression tests for four defects found in the audit (see PROJECT_COMPLETION_PLAN.md, section 8):
1. special games ignored the venue rules (the Eastern finalist was always the Coronation's home side, whatever the venue; neutral
   venues gave their nominal home side the home-field edge). Edition 8.55 replaced the rotating, voted Coronation venue with one
   permanent neutral venue; the nominal home finalist is now designated after Week 4 (see CoronationVenue);
2. Tiaras went to injured-reserve players as well as the final 53;
3. awarding Tiaras twice inflated the summary counter;
4. CI hid crashes of the spatial smoke run behind `|| true`."""
import copy
import os
import random
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
from dfl import league  # noqa: E402
from dfl.engine.params import P  # noqa: E402
from dfl.ledger import honours  # noqa: E402
from dfl.ledger.world import World  # noqa: E402
from dfl.resolver import PlaceholderResolver  # noqa: E402

TABLES = os.path.join(ROOT, "data", "season1")


class Recorder(PlaceholderResolver):
    """The placeholder resolver, remembering each game dict it was handed."""
    def __init__(self):
        super().__init__()
        self.seen = []

    def resolve(self, game, rng, decisive):
        self.seen.append(dict(game, decisive=decisive))
        return super().resolve(game, rng, decisive)


def run_with_recorder(seed="v", season=1, rng_seed=1):
    tpl = league.Template(TABLES)
    rec = Recorder()
    run = league.SeasonRun(tpl, tpl.initial_state(), season, rec, random.Random(rng_seed), seed=seed)
    for n in sorted(run.week_rows):
        run.play_week(n)
    for i in range(1, 8):
        run.play_tour_round(i)
    run.finish_regular()
    for name in ("wild_card", "divisional", "conference"):
        run.play_round(name)
    run.play_bowl()
    run.play_coronation()
    return tpl, run, rec


class CoronationVenue(unittest.TestCase):
    """Edition 8.55: the Diamond Coronation is always played at Amethyst Aloha Stadium and is always neutral. The finalist from the
    conference designated after Week 4 is the nominal home side, which controls kickoff and uniform rules but gives no home-field edge."""

    @classmethod
    def setUpClass(cls):
        cls.tpl, cls.srun, cls.rec = run_with_recorder()

    def test_the_venue_is_the_permanent_one_and_the_game_is_neutral(self):
        cor = self.srun.coronation
        self.assertEqual(league.CORONATION_VENUE, "Amethyst Aloha Stadium")
        self.assertEqual((cor["venue"], cor["neutral"]), (league.CORONATION_VENUE, True))
        self.assertNotIn("home_club", cor)                      # nobody hosts: there is no home ground

    def test_the_engine_is_told_the_coronation_is_neutral(self):
        cor = self.srun.coronation
        game = self.rec.seen[-1]                                # the Coronation is the last game played
        self.assertEqual((game["away"], game["home"], game["id"]), (cor["away"], cor["home"], "coronation"))
        self.assertIs(game["neutral"], True)

    def test_the_finalist_of_the_conference_designated_after_week_4_is_the_nominal_home_side(self):
        run, cor = self.srun, self.srun.coronation
        d = run.coronation_designation
        self.assertEqual(cor["home"], run.champs[d["home_conference"]])
        self.assertEqual(cor["away"], next(c for conf, c in run.champs.items() if conf != d["home_conference"]))
        self.assertEqual(cor["home_conference"], d["home_conference"])

    def test_the_designation_is_made_from_weeks_1_to_4_alone_and_then_frozen(self):
        from dfl import standings
        tpl = league.Template(TABLES)
        run = league.SeasonRun(tpl, tpl.initial_state(), 1, PlaceholderResolver(), random.Random(7), seed="frozen")
        for n in (1, 2, 3):
            run.play_week(n)
            self.assertIsNone(run.coronation_designation)       # nothing is decided before Week 4 is complete
        run.play_week(4)
        saved = run.coronation_designation
        self.assertIsNotNone(saved)
        self.assertEqual(saved["through_week"], 4)
        self.assertEqual(saved, standings.conference_designation(run.active, run.games, "frozen|season1"))
        self.assertEqual(saved["interconference_games"], 80)
        for n in sorted(run.week_rows)[4:]:
            run.play_week(n)
        self.assertIs(run.coronation_designation, saved)        # the rest of the season cannot reopen it
        self.assertEqual(run.designate_coronation_home(), saved)
        self.assertEqual(run.coronation_designation, standings.conference_designation(run.active, run.games, "frozen|season1"))

    def test_the_saved_decision_is_in_the_season_result(self):
        result, _ = self.srun.handoff()
        self.assertEqual(result["coronation_designation"], self.srun.coronation_designation)
        self.assertEqual((result["coronation_venue"], result["coronation_home"]), (league.CORONATION_VENUE, self.srun.coronation["home"]))

    def test_over_many_seasons_neither_conference_is_always_the_nominal_home_and_every_game_is_neutral(self):
        confs = set()
        for s in range(1, 25):
            _, run, rec = run_with_recorder(seed=f"m{s}", season=1, rng_seed=s)
            confs.add(run.coronation_designation["home_conference"])
            self.assertTrue(run.coronation["neutral"] and rec.seen[-1]["neutral"])
            self.assertEqual(run.coronation["venue"], league.CORONATION_VENUE)
        self.assertEqual(confs, {"Western", "Eastern"})


class OtherSpecialGames(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tpl, cls.srun, cls.rec = run_with_recorder()

    def test_every_tour_game_is_neutral_and_regular_and_playoff_games_are_not(self):
        n_tour = n_reg = 0
        seen = self.rec.seen
        tour_pairs = {(g["away"], g["home"]) for g in self.srun.tour_games}
        for g in seen:
            if (g["away"], g["home"]) in tour_pairs and g.get("neutral"):
                n_tour += 1
            elif not g.get("neutral"):
                n_reg += 1
        self.assertEqual(n_tour, 28)
        self.assertEqual(sum(1 for g in seen if g.get("neutral")), 28 + 1 + 1)       # the Tour, the Bowl and the Coronation
        self.assertGreater(n_reg, 256)

    def test_the_bowl_is_neutral_and_neither_conference_is_always_the_nominal_home(self):
        self.assertTrue(self.srun.bowl["neutral"])
        homes = set()
        for s in range(1, 25):
            _, run, _ = run_with_recorder(seed=f"b{s}")
            homes.add(run.bowl["home"])
        self.assertEqual(homes, {"Western All-Stars", "Eastern All-Stars"})

    def test_playoff_games_stay_at_the_higher_seeds_venue_and_are_not_neutral(self):
        decisive = [g for g in self.rec.seen if g["decisive"]]
        self.assertEqual(len(decisive), 10 + 1 + 1)             # ten playoff games, the Bowl and the Coronation
        self.assertFalse(any(g.get("neutral") for g in decisive[:10]))
        self.assertEqual(len(self.srun.playoff_log), 10)
        for conf, away, home, a, h in self.srun.playoff_log:
            seed = {c: s for s, c in self.srun.by_seed[conf].items()}
            self.assertLess(seed[home], seed[away])


class EngineNeutral(unittest.TestCase):
    def test_a_neutral_game_carries_no_home_edge(self):
        import test_engine as TE
        from dfl.engine.game import Game
        h, a = TE.squad("C00"), TE.squad("C01")
        self.assertEqual(Game(h, a, random.Random(1)).home_edge, P["home_edge"])
        self.assertEqual(Game(h, a, random.Random(1), neutral=True).home_edge, 0.0)

    def test_the_home_edge_only_helps_the_home_side_when_the_venue_is_not_neutral(self):
        import test_engine as TE
        from dfl.engine.game import play_game
        old = P["home_edge"]
        P["home_edge"] = 3.0                                    # exaggerated so that 30 games separate the cases
        try:
            wins = {}
            for neutral in (False, True):
                w = 0
                for s in range(30):
                    r = play_game(TE.squad("C00"), TE.squad("C01"), random.Random(s), True, neutral=neutral)
                    w += r.score[0] > r.score[1]
                wins[neutral] = w
        finally:
            P["home_edge"] = old
        self.assertGreater(wins[False], wins[True] + 5)

    def test_non_neutral_games_are_unchanged(self):
        import test_engine as TE
        from dfl.engine.game import play_game
        a = play_game(TE.squad("C00"), TE.squad("C01"), random.Random(5), False)
        b = play_game(TE.squad("C00"), TE.squad("C01"), random.Random(5), False, neutral=False)
        self.assertEqual((a.score, a.stats), (b.score, b.stats))


class OpeningPossession(unittest.TestCase):
    """Section 10: "Home kicks to start the first half, away the second. No coin toss. In overtime the away side kicks first." The
    DFL has no coin toss, so opening possession is fixed by which side is the (nominal) home side, and a neutral venue changes nothing."""

    def kickoffs(self, neutral, seed):
        """[(period, kicker index)] of every kickoff, taken from the engine itself (the play log's team column is the previous possession)."""
        import test_engine as TE
        from dfl.engine.game import Game
        seen, real = [], Game.kickoff

        def spy(game, kicker, free_kick=False):
            seen.append((game.period, kicker, free_kick))
            return real(game, kicker, free_kick)
        Game.kickoff = spy
        try:
            Game(TE.squad("C00"), TE.squad("C01"), random.Random(seed), False, neutral=neutral).run()
        finally:
            Game.kickoff = real
        return seen

    def test_the_nominal_home_kicks_the_first_half_and_away_the_second_at_home_and_at_neutral_sites(self):
        for neutral in (False, True):
            for seed in range(1, 6):
                ko = self.kickoffs(neutral, seed)
                self.assertEqual(ko[0][:2], (1, 0))              # period 1: the home side (index 0) kicks
                half2 = [k for k in ko if k[0] == 3][0]
                self.assertEqual(half2[:2], (3, 1))              # period 3: the away side kicks

    def test_neutral_changes_only_the_edge_not_the_kicking_order(self):
        self.assertEqual(self.kickoffs(False, 7)[0], self.kickoffs(True, 7)[0])

    def test_there_is_no_coin_toss_in_the_engine(self):
        with open(os.path.join(ROOT, "src", "dfl", "engine", "game.py"), encoding="utf-8") as f:
            text = f.read().lower()
        self.assertNotIn("coin", text)
        self.assertNotIn("toss", text)


class Tiaras(unittest.TestCase):
    def setUp(self):
        self.w = World([f"C{i:02d}" for i in range(1, 9)], seed=73)
        self.w.open_year(first=True)

    def leaders(self, club):
        w = self.w
        return {w.leaders[club][r].identity.id for r in ("ceo", "gm", "hc", "oc", "dc", "stc")}

    def test_the_tiara_set_is_exactly_the_final_53_and_six_leaders_without_injured_reserve(self):
        w = self.w
        club = w.L.clubs["C02"]
        moved = club.roster[0]                                  # an injured player of the winning club on injured reserve
        club.roster.remove(moved)
        club.ir.append(moved)
        self.assertTrue(club.ir and club.ps)
        got = honours.award_tiara(w, 2005, "C02")
        self.assertEqual(got, set(club.roster) | self.leaders("C02"))      # defect: IR players used to be added
        self.assertNotIn(moved, got)
        self.assertFalse(set(club.ir) & got)
        self.assertFalse(set(club.ps) & got)
        self.assertLessEqual(len(got) - len(self.leaders("C02")), 53)
        self.assertEqual(len(self.leaders("C02")), 6)
        self.assertNotIn(moved, w.tiaras)

    def test_nobody_outside_the_winner_gets_one(self):
        w = self.w
        honours.award_tiara(w, 2005, "C02")
        other = set(w.L.clubs["C03"].roster)
        self.assertFalse(other & set(w.tiaras))

    def test_awarding_twice_is_idempotent_including_the_summary_counter(self):
        w = self.w
        first = honours.award_tiara(w, 2005, "C02")
        count = w.stats["tiaras"]
        snapshot = copy.deepcopy(w.tiaras)
        self.assertEqual(count, len(first))
        second = honours.award_tiara(w, 2005, "C02")            # defect: the counter doubled
        self.assertEqual(second, first)
        self.assertEqual(w.stats["tiaras"], count)
        self.assertEqual(w.tiaras, snapshot)

    def test_a_new_year_counts_again_and_a_person_has_at_most_one_per_season(self):
        w = self.w
        n = len(honours.award_tiara(w, 2005, "C02"))
        honours.award_tiara(w, 2006, "C02")
        self.assertEqual(w.stats["tiaras"], 2 * n)
        pid = next(iter(w.L.clubs["C02"].roster))
        self.assertEqual(w.tiaras[pid], {2005, 2006})


class CiSmoke(unittest.TestCase):
    CAL = [sys.executable, "-I", os.path.join(ROOT, "tools", "calibrate_engine.py")]

    def test_the_workflow_no_longer_swallows_failures(self):
        with open(os.path.join(ROOT, ".github", "workflows", "ci.yml"), encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("|| true", text)
        self.assertNotIn("continue-on-error", text)
        self.assertIn("calibrate_engine.py --games 20 --seed 1 --spatial --procs 2 --informational", text)

    def test_a_statistical_miss_is_informational_and_a_crash_is_not(self):
        miss = subprocess.run(self.CAL + ["--games", "2", "--seed", "1", "--informational"], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(miss.returncode, 0, miss.stderr)
        self.assertIn("FAIL", miss.stdout)                      # two games miss bands, and say so
        strict = subprocess.run(self.CAL + ["--games", "2", "--seed", "1"], capture_output=True, text=True, cwd=ROOT)
        self.assertGreater(strict.returncode, 0)                # without the flag a miss still fails, as before
        # an execution failure (no game can be played, so the statistics are undefined) fails even in informational mode
        crash = subprocess.run(self.CAL + ["--games", "0", "--informational"], capture_output=True, text=True, cwd=ROOT)
        self.assertNotEqual(crash.returncode, 0)
        self.assertNotIn("informational:", crash.stdout)


if __name__ == "__main__":
    unittest.main()
