"""The public feed (src/dflsite/feed.py): what a viewer may see, and nothing else.

Boundaries: the allowlist schemas and forbidden-key net hold at every stage of a season; scrambling everything hidden in the league (ratings,
attributes, growth, traits, memories, relationships, ballots, honours, the draft order) leaves the feed byte-for-byte unchanged; the league's
seed appears nowhere; results of events that have not been committed are absent; building the feed does not change the league.
"""
import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import feedlab  # noqa: E402
from dfl import persist  # noqa: E402
from dflsite import feed  # noqa: E402


def S():
    return feedlab.stages()


def games_of(f, kind=None):
    return [g for g in f["schedule.json"]["games"] if kind is None or g["kind"] == kind]


class Boundary(unittest.TestCase):
    def test_every_stage_is_entirely_public(self):
        _, out, _ = S()
        for stage, f in out.items():
            self.assertEqual(feed.check_public(f, [str(feedlab.SEED), f"dfl{feedlab.SEED}"]), [], stage)

    def test_nothing_hidden_is_in_any_file_at_any_stage(self):
        _, out, _ = S()
        bad = ["ZZSECRETZZ", str(feedlab.SEED), "true_overall", "\"ovr\"", "\"attrs\"", "\"traits\"", "\"ballot", "\"draft_order\"", "\"rings\"", "\"tiaras\""]
        for stage, f in out.items():
            for name, v in f.items():
                text = feed.canonical(v)
                for b in bad:
                    self.assertNotIn(b, text, (stage, name, b))

    def test_scrambling_everything_hidden_changes_nothing(self):
        u, out, snaps = S()
        for stage in ("w6", "bowl"):
            clone = feedlab.poisoned(snaps[stage])
            again = feedlab.build(clone)
            for name in out[stage]:
                self.assertEqual(feed.canonical(out[stage][name]), feed.canonical(again[name]), (stage, name))

    def test_roster_rows_carry_only_the_public_facts(self):
        _, out, _ = S()
        want = {"id", "name", "pos", "number", "age", "seasons", "height_cm", "weight_kg", "slot", "injury", "contract"}
        n = 0
        for name, v in out["w6"].items():
            if name.startswith("rosters/"):
                for p in v["players"]:
                    self.assertEqual(set(p), want)
                    self.assertEqual(set(p["contract"]), {"cap_hit", "years_left"})
                    n += 1
        self.assertGreater(n, 48 * 53)

    def test_a_schema_or_forbidden_key_violation_is_caught(self):
        _, out, _ = S()
        import copy
        f = copy.deepcopy(out["w6"])
        f["rosters/ALA.json"]["players"][0]["ovr"] = 71
        self.assertTrue(any("ovr" in e for e in feed.check_public(f)))
        f = copy.deepcopy(out["w6"])
        f["teams.json"]["teams"][0]["motivation"] = "x"
        self.assertTrue(feed.check_public(f))
        f = copy.deepcopy(out["w6"])
        f["season.json"]["note"] = f"dfl{feedlab.SEED}"
        self.assertTrue(feed.check_public(f, [f"dfl{feedlab.SEED}"]))
        f = copy.deepcopy(out["w6"])
        f["standings.json"]["divisions"][0]["clubs"][0]["pct"] = "high"
        self.assertTrue(feed.check_public(f))
        f = copy.deepcopy(out["w6"])
        del f["season.json"]["year"]
        self.assertTrue(feed.check_public(f))

    def test_building_the_feed_does_not_change_the_league(self):
        _, out, snaps = S()
        u = snaps["w6"]
        before = persist.journal_digest(u)
        fired = list(u.fired)
        feedlab.build(u)
        feedlab.build(u)
        self.assertEqual(persist.journal_digest(u), before)
        self.assertEqual(u.fired, fired)

    def test_the_same_league_gives_the_same_feed(self):
        u, out, snaps = S()
        a, b = feedlab.build(snaps["w6"]), feedlab.build(snaps["w6"])
        self.assertEqual(feed.canonical(a), feed.canonical(b))
        self.assertEqual(a["manifest.json"]["content_hash"], out["w6"]["manifest.json"]["content_hash"])


class Revealed(unittest.TestCase):
    def test_start_of_the_league_has_a_calendar_and_teams_but_no_results(self):
        _, out, _ = S()
        f = out["start"]
        self.assertEqual(f["season.json"]["events_done"], 0)
        self.assertIsNone(f["season.json"]["last_event"])
        self.assertEqual(len(f["teams.json"]["teams"]), 48)
        self.assertEqual(games_of(f), [])
        self.assertFalse(f["season.json"]["schedule_published"])
        self.assertEqual(f["calendar.json"]["events"][0]["status"], "upcoming")
        self.assertEqual(f["standings.json"]["divisions"], [])
        self.assertEqual(f["history.json"]["seasons"], [])

    def test_schedule_appears_with_camp_and_has_no_scores(self):
        _, out, _ = S()
        f = out["camp"]
        self.assertTrue(f["season.json"]["schedule_published"])
        reg = games_of(f, "regular")
        self.assertEqual(len(reg), 360)
        self.assertTrue(all(g["status"] == "scheduled" and g["away_score"] is None and g["home_score"] is None for g in reg))
        self.assertEqual(len(games_of(f, "tour")), 28)
        self.assertTrue(all(g["venue"] is None for g in games_of(f, "tour")))

    def test_only_played_weeks_have_scores_and_standings_count_only_them(self):
        _, out, _ = S()
        f = out["w6"]
        played = [g for g in games_of(f, "regular") if g["status"] == "final"]
        self.assertEqual({g["week"] for g in played}, set(range(1, 7)))
        self.assertTrue(all(g["week"] > 6 for g in games_of(f, "regular") if g["status"] == "scheduled"))
        self.assertTrue(all(g["away_score"] is not None for g in played))
        gp = sum(c["w"] + c["l"] + c["t"] for d in f["standings.json"]["divisions"] for c in d["clubs"])
        self.assertEqual(gp, 2 * len(played))
        self.assertEqual(f["standings.json"]["through_week"], 6)
        self.assertEqual(f["season.json"]["regular_weeks"], {"total": 19, "played": 6})
        self.assertIsNotNone(f["standings.json"]["designation"])          # decided after Week 4, so visible after Week 6

    def test_week_4_designation_is_not_visible_before_week_4(self):
        u, _, snaps = S()
        # the w6 snapshot is past week 4; the camp feed (no games) has no designation
        self.assertIsNone(S()[1]["camp"]["standings.json"]["designation"])
        d = S()[1]["w6"]["standings.json"]["designation"]
        self.assertEqual(d["through_week"], 4)
        self.assertEqual(set(d), {"through_week", "home_conference", "decided_by", "conferences"})
        for c in d["conferences"].values():
            self.assertEqual(set(c), {"w", "l", "t", "pf", "pa", "pd"})            # the seeded draw value is not published

    def test_tour_venues_appear_only_for_played_rounds(self):
        _, out, _ = S()
        tour = games_of(out["w6"], "tour")
        for g in tour:
            self.assertEqual(g["venue"] is not None, g["status"] == "final")

    def test_playoffs_unseeded_then_seeded_then_played(self):
        _, out, _ = S()
        self.assertEqual(out["w6"]["playoffs.json"]["status"], "not started")
        self.assertEqual(out["w6"]["playoffs.json"]["rounds"], [])
        reg = out["reg_end"]["playoffs.json"]                       # every regular game played; the Wild Card round has not been
        self.assertEqual(reg["status"], "seeded")
        self.assertEqual({k: len(v) for k, v in reg["seeds"].items()}, {"Eastern": 6, "Western": 6})
        wc = [r for r in reg["rounds"] if r["name"] == "wild_card"][0]
        self.assertTrue(all(g["status"] == "scheduled" and g["away_score"] is None for gs in wc["conferences"].values() for g in gs))
        self.assertEqual({k: len(v) for k, v in wc["conferences"].items()}, {"Eastern": 2, "Western": 2})
        self.assertIsNone(reg["bowl"])
        self.assertIsNone(reg["champion"])
        after = out["wild_card"]["playoffs.json"]
        self.assertEqual(after["status"], "in progress")
        w = [r for r in after["rounds"] if r["name"] == "wild_card"][0]
        self.assertTrue(all(g["status"] == "final" for gs in w["conferences"].values() for g in gs))
        d = [r for r in after["rounds"] if r["name"] == "divisional"][0]
        self.assertTrue(all(g["status"] == "scheduled" for gs in d["conferences"].values() for g in gs))
        self.assertFalse(any(r["name"] == "conference" for r in after["rounds"]))          # only the next round shows unplayed games

    def test_bowl_and_coronation_appear_only_after_they_are_played(self):
        _, out, _ = S()
        self.assertIsNone(out["wild_card"]["playoffs.json"]["bowl"])
        b = out["bowl"]["playoffs.json"]
        self.assertEqual(b["bowl"]["status"], "final")
        self.assertEqual({k: len(v) for k, v in b["bowl"]["rosters"].items()}, {"Eastern": 53, "Western": 53})
        self.assertEqual(set(b["bowl"]["rosters"]["Eastern"][0]), {"id", "name", "pos", "club"})
        cor = b["coronation"]
        self.assertEqual(cor["status"], "scheduled")
        self.assertIsNone(cor["away_score"])
        self.assertIsNone(b["champion"])
        self.assertIsNone(out["bowl"]["season.json"]["champion"])
        done = out["coronation"]["playoffs.json"]
        self.assertEqual(done["coronation"]["status"], "final")
        self.assertEqual(done["status"], "complete")
        self.assertEqual(out["coronation"]["season.json"]["champion"], done["champion"])
        self.assertEqual(done["coronation"]["venue"], "Amethyst Aloha Stadium")

    def test_history_gains_the_season_when_the_year_closes_and_keeps_it(self):
        _, out, _ = S()
        self.assertEqual(out["coronation"]["history.json"]["seasons"], [])
        h = out["closed"]["history.json"]["seasons"]
        self.assertEqual(len(h), 1)
        s = h[0]
        self.assertEqual((s["season"], s["year"]), (1, 2000))
        self.assertEqual(s["champion"], out["coronation"]["playoffs.json"]["champion"])
        self.assertNotEqual(s["champion"], s["runner_up"])
        self.assertEqual(len(s["division_winners"]), 8)
        self.assertEqual(len(s["exiled"]), 8)
        self.assertEqual(len(s["playoff_games"]), 10)
        self.assertEqual(len(s["standings"]), 8)
        self.assertIsNotNone(s["score"])
        later = out["next_year"]["history.json"]["seasons"]
        self.assertEqual(later[0]["champion"], s["champion"])
        self.assertEqual(later[0]["score"], s["score"])     # Edition 8.58: the season result keeps the score for good, so it survives the new year
        self.assertEqual(later[0]["score_source"], "recorded")
        self.assertTrue(any("recorded permanently" in n["text"] for n in out["next_year"]["notices.json"]["notices"]))

    def test_the_recorded_score_is_the_score_that_was_played(self):
        u, _, _ = S()
        rep = u.reports[0]
        c = rep["result"]["coronation"]
        self.assertEqual(rep["result"]["result_revision"], 2)
        self.assertEqual({c["home"], c["away"]}, set(rep["result"]["champions"].values()))
        self.assertEqual(c["home"], rep["result"]["coronation_home"])
        self.assertEqual(c["home"] if c["home_score"] > c["away_score"] else c["away"], rep["winner"])
        self.assertEqual(feed.coronation_score(u, rep)[0], f'{max(c["away_score"], c["home_score"])}-{min(c["away_score"], c["home_score"])}')

    def _old_report(self, u):
        """A season report as the code before Edition 8.58 wrote it: no Coronation score and no result revision."""
        import copy
        rep = copy.copy(u.reports[0])
        rep["result"] = {k: v for k, v in u.reports[0]["result"].items() if k not in ("coronation", "result_revision")}
        return rep

    def _fake_archive(self, rep, **over):
        r = rep["result"]
        away = next(c for c in r["champions"].values() if c != r["coronation_home"])
        game = {"type": "game", "kind": "coronation", "season": rep["season"], "home": r["coronation_home"], "away": away,
                "score": [31, 17] if rep["winner"] == r["coronation_home"] else [17, 31], "winner": rep["winner"]}
        game.update(over)

        class A:
            def game(self, gid):
                if gid != f'S{rep["season"]:03d}/coronation':
                    raise KeyError(gid)
                return game
        return A()

    def test_an_old_report_without_a_score_says_unavailable_and_is_never_estimated(self):
        u, _, _ = S()
        score, src = feed.coronation_score(u, self._old_report(u))
        self.assertEqual((score, src), (None, "unavailable"))

    def test_an_old_report_recovers_its_score_only_from_a_matching_archived_game(self):
        u, _, _ = S()
        rep = self._old_report(u)
        self.assertEqual(feed.coronation_score(u, rep, self._fake_archive(rep)), ("31-17", "archive"))
        for bad in ({"kind": "game"}, {"season": 9}, {"home": "Nowhere"}, {"winner": "Nowhere"}, {"score": [20, 20]},
                    {"score": [17, 31] if rep["winner"] == rep["result"]["coronation_home"] else [31, 17]}):
            self.assertEqual(feed.coronation_score(u, rep, self._fake_archive(rep, **bad)), (None, "unavailable"), bad)

    def test_a_missing_archived_game_is_unavailable_not_invented(self):
        u, _, _ = S()

        class Empty:
            def game(self, gid):
                raise KeyError(gid)
        self.assertEqual(feed.coronation_score(u, self._old_report(u), Empty()), (None, "unavailable"))

    def test_new_year_resets_the_season_views_but_not_the_history(self):
        _, out, _ = S()
        f = out["next_year"]
        self.assertEqual(f["season.json"]["year"], 2001)
        self.assertEqual(f["season.json"]["phase"], "offseason")
        self.assertEqual(games_of(f), [])
        self.assertEqual(f["playoffs.json"]["status"], "not started")
        self.assertEqual(f["standings.json"]["divisions"], [])
        self.assertEqual(len(f["history.json"]["seasons"]), 1)

    def test_the_season_file_tracks_progress(self):
        _, out, _ = S()
        a, b = out["camp"]["season.json"], out["w6"]["season.json"]
        self.assertLess(a["events_done"], b["events_done"])
        self.assertEqual(b["last_event"]["key"], "week_6")
        self.assertEqual(b["phase"], "regular season")
        self.assertEqual(b["next_event"]["index"], b["events_done"] + 1)
        self.assertEqual(out["closed"]["season.json"]["phase"], "complete")
        self.assertEqual(out["closed"]["season.json"]["events_done"], out["closed"]["season.json"]["events_total"])
        m = out["w6"]["manifest.json"]
        self.assertEqual((m["season"], m["year"], m["simulation_date"]), (1, 2000, b["simulation_date"]))

    def test_every_club_has_a_roster_file_and_team_ten_is_the_gladiators(self):
        _, out, _ = S()
        teams = out["w6"]["teams.json"]["teams"]
        self.assertEqual(len({t["abbr"] for t in teams}), 48)
        for t in teams:
            self.assertIn(f'rosters/{t["abbr"]}.json', out["w6"])
        self.assertEqual([t["full_name"] for t in teams if t["id"] == 10], ["New York Gladiators"])

    def test_labels_and_notices(self):
        _, out, _ = S()
        for stage, f in out.items():
            self.assertIn("DEVELOPMENT / TEST LEAGUE", f["manifest.json"]["league"]["label"])
            ids = {n["id"] for n in f["notices.json"]["notices"]}
            self.assertTrue({"dev-league", "event-reveal", "no-hidden", "not-built"} <= ids, stage)
            self.assertEqual(f["manifest.json"]["league"]["kind"], "development")


class Rules(unittest.TestCase):
    def test_the_rules_summary_is_checked_against_the_current_spec(self):
        ed, h = feed.spec_edition()
        r = feed.rules_file()
        self.assertEqual((r["spec_edition"], r["spec_hash"]), (ed, h),
                         "the spec changed: re-read docs/public/RULES_SUMMARY.md against it and update its spec_edition and spec_hash")
        self.assertGreaterEqual(len(r["sections"]), 8)

    def test_the_summary_says_nothing_the_spec_has_not_approved(self):
        text = feed.rules_text().lower()
        self.assertIn("still await the owner's rulings", text)          # extensions and midseason deals are named only as awaiting a ruling
        self.assertNotIn("extensions are", text)
        self.assertIn("all 53 selected players dress", text)
        self.assertIn("fully guaranteed", text)
        self.assertIn("amethyst aloha stadium", text)


if __name__ == "__main__":
    unittest.main()
