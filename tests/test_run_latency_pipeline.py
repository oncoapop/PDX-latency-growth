import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_tools.run_latency_pipeline import is_stale, run_threshold_stages


class IsStaleTests(unittest.TestCase):
    def test_missing_output_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "out.csv"
            self.assertTrue(is_stale([output], []))

    def test_output_newer_than_input_is_not_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "in.csv"
            output_path = Path(tmp) / "out.csv"
            input_path.write_text("x")
            output_path.write_text("y")
            future = os.path.getmtime(output_path) + 10
            os.utime(output_path, (future, future))
            self.assertFalse(is_stale([output_path], [input_path]))

    def test_output_older_than_input_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "in.csv"
            output_path = Path(tmp) / "out.csv"
            output_path.write_text("y")
            input_path.write_text("x")
            future = os.path.getmtime(input_path) + 10
            os.utime(input_path, (future, future))
            self.assertTrue(is_stale([output_path], [input_path]))

    def test_no_inputs_means_only_output_existence_matters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "out.csv"
            output_path.write_text("y")
            self.assertFalse(is_stale([output_path], []))

    def test_any_missing_output_is_stale_even_if_others_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            existing = Path(tmp) / "out1.csv"
            missing = Path(tmp) / "out2.csv"
            existing.write_text("y")
            self.assertTrue(is_stale([existing, missing], []))


class RunThresholdStagesTests(unittest.TestCase):
    def test_second_call_skips_all_three_stages_when_nothing_changed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "normalized").mkdir()
            (workspace / "analysis").mkdir()
            (workspace / "normalized" / "growth_measurements.csv").write_text("x")
            (workspace / "normalized" / "surgeries.csv").write_text("x")
            (workspace / "normalized" / "atlas_inventory.csv").write_text("x")
            (workspace / "analysis" / "mouse_growth_rate.csv").write_text("x")

            def fake_run(cmd, check):
                # Simulate each script by creating its declared output file.
                out_dir = workspace / "analysis" / "threshold_200"
                out_dir.mkdir(parents=True, exist_ok=True)
                if "rank_candidates.py" in cmd[1]:
                    (out_dir / "mouse_latency_to_200.csv").write_text("x")
                elif "build_latency_and_growth_report.py" in cmd[1]:
                    (out_dir / "mouse_latency_and_growth_report_200.csv").write_text("x")
                elif "rank_by_latency_and_growth.py" in cmd[1]:
                    (out_dir / "mouse_ranked_by_latency_and_growth_200.csv").write_text("x")

            with mock.patch("agent_tools.run_latency_pipeline.subprocess.run", side_effect=fake_run) as mocked:
                run_threshold_stages(workspace, 200.0, force=False)
                self.assertEqual(mocked.call_count, 3)

            with mock.patch("agent_tools.run_latency_pipeline.subprocess.run", side_effect=fake_run) as mocked:
                run_threshold_stages(workspace, 200.0, force=False)
                self.assertEqual(mocked.call_count, 0)


if __name__ == "__main__":
    unittest.main()
