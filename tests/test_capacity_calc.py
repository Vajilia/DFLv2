"""The capacity calculation (tools/capacity_calc.py): arithmetic only, no host access, and no ceiling is chosen.

Required here:
  * the components add up and grow linearly with the seasons, from the documented defaults
  * the free-disk reserve is the supervisor's own rule (floor + step margin + season allowance) when that exceeds the monitor's floor, and it cannot drift from the supervisor's code
  * with no disk size given it says nothing about fit; with one it finds the last season that fits
  * unknown inputs are refused, and nothing is ever recommended or written
"""
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))
import broadcast_supervisor as SUP      # noqa: E402
import capacity_calc as CC              # noqa: E402

GB = 1e9


def inputs(**kw):
    d = dict(CC.DEFAULTS)
    d.update(kw)
    return d


class Model(unittest.TestCase):
    def test_the_components_add_up_and_grow_linearly(self):
        i = inputs(other_used_gb=10.0)
        a, b, c = CC.need(i, 1), CC.need(i, 2), CC.need(i, 3)
        for k in ('public_permanent', 'private_stores', 'backup_workspace', 'retained_evidence'):
            self.assertAlmostEqual(c[k] - b[k], b[k] - a[k], places=9, msg=k)
            self.assertGreaterEqual(b[k], a[k])
        self.assertAlmostEqual(a['total'], sum(v for k, v in a.items() if k != 'total'))
        self.assertAlmostEqual(a['public_permanent'], 0.25 + 0.955)
        self.assertAlmostEqual(a['private_stores'], 0.011 + 1.073)

    def test_backup_workspace_counts_the_kept_set_the_restore_destination_and_the_scratch(self):
        i = inputs(backup_sets_kept=2, restore_test_on_volume=True, files_per_season=100_000, backup_scratch_kb_per_file=1.0)
        n = 3
        one = n * (0.955 + 1.073)
        self.assertAlmostEqual(CC.need(i, n)['backup_workspace'], 2 * one + one + n * 100_000 * 1e3 / GB)
        j = inputs(backup_sets_kept=0, restore_test_on_volume=False, files_per_season=0)
        self.assertAlmostEqual(CC.need(j, n)['backup_workspace'], 0.0)

    def test_quarantined_partials_are_counted_at_one_set_each(self):
        i = inputs(quarantined_partials=2, evidence_allowance_gb=0.1)
        self.assertAlmostEqual(CC.need(i, 2)['retained_evidence'], 0.1 + 2 * 2 * (0.955 + 1.073))

    def test_the_reserve_is_the_supervisors_rule_and_cannot_drift_from_its_code(self):
        i = inputs()
        self.assertAlmostEqual(CC.reserve_gb(i), 10.5 * 2 ** 30 / GB)          # 10.5 GiB: the supervisor's numbers are binary gigabytes, the monitor's floor is decimal
        c = type('C', (), dict(floor_gb=i['build_floor_gb'], step_margin_gb=i['step_margin_gb'], season_gb=i['season_gb'], inode_floor=0, step_inodes=0, season_inodes=0))()
        got, _, _ = SUP.Supervisor.capacity_need(type('S', (), {'cfg': c})(), 'next-season')
        self.assertAlmostEqual(got / GB, CC.reserve_gb(i))
        self.assertEqual(SUP.GB, 2 ** 30)
        self.assertEqual(CC.reserve_gb(inputs(monitor_floor_gb=20.0)), 20.0)


class Fit(unittest.TestCase):
    def test_nothing_is_said_about_fit_without_a_disk_size(self):
        self.assertIsNone(CC.max_seasons(inputs()))
        self.assertTrue(all(r['fits'] is None for r in CC.table(inputs(), [1, 2])))

    def test_the_last_season_that_fits_is_found(self):
        i = inputs(disk_gb=47.0, other_used_gb=10.0)
        n = CC.max_seasons(i)
        self.assertGreaterEqual(n, 1)
        self.assertLessEqual(CC.need(i, n)['total'], 47.0)
        self.assertGreater(CC.need(i, n + 1)['total'], 47.0)
        self.assertEqual(CC.max_seasons(inputs(disk_gb=5.0, other_used_gb=10.0)), 0)

    def test_the_ceiling_is_what_the_public_folder_will_hold_at_the_horizon_plus_a_margin_and_is_never_chosen_here(self):
        i = inputs()
        self.assertAlmostEqual(CC.ceiling_gb(i, 4), 0.25 + 4 * 0.955)
        self.assertAlmostEqual(CC.ceiling_gb(i, 4, margin_gb=1.0), 0.25 + 4 * 0.955 + 1.0)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            CC.main(['--disk-gb', '47', '--other-used-gb', '10'])
        self.assertNotIn('ceiling', buf.getvalue().lower().replace('seasons that fit', ''), 'the table does not recommend a ceiling')


class Command(unittest.TestCase):
    def run_main(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = CC.main(list(argv))
        return code, buf.getvalue()

    def test_json_output_has_the_inputs_the_reserve_and_the_rows(self):
        code, out = self.run_main('--disk-gb', '60', '--other-used-gb', '12', '--seasons', '3', '--json')
        d = json.loads(out)
        self.assertEqual((code, len(d['rows']), round(d['reserve_gb'], 3)), (0, 3, 11.274))
        self.assertEqual(d['inputs']['disk_gb'], 60.0)

    def test_an_override_changes_the_result_and_an_unknown_input_is_refused(self):
        _, a = self.run_main('--seasons', '2', '--json')
        _, b = self.run_main('--seasons', '2', '--json', '--set', '{"public_per_season_gb": 2.0}')
        self.assertGreater(json.loads(b)['rows'][1]['public_permanent'], json.loads(a)['rows'][1]['public_permanent'])
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            CC.main(['--set', '{"nonsense": 1}'])

    def test_without_the_hosts_other_usage_it_says_the_totals_are_understated(self):
        _, out = self.run_main('--disk-gb', '60')
        self.assertIn('understates', out)


if __name__ == '__main__':
    unittest.main()
