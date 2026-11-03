import unittest

import pandas as pd

from agent_tools.plot_latency_by_sa_id import write_per_mouse_csv, write_summary_csv


def _frame():
    return pd.DataFrame([
        {"sa_id": "SA1", "mouse_id": "m1", "passage": 3, "surgery_date": pd.Timestamp("2020-01-01"),
         "latency_days": 20.0, "growth_rate_per_day": 0.1},
        {"sa_id": "SA1", "mouse_id": "m2", "passage": 5, "surgery_date": pd.Timestamp("2020-01-02"),
         "latency_days": 40.0, "growth_rate_per_day": 0.2},
        {"sa_id": "SA2", "mouse_id": "m3", "passage": 2, "surgery_date": pd.Timestamp("2020-02-01"),
         "latency_days": 10.0, "growth_rate_per_day": 0.05},
    ])


class WritePerMouseCsvTests(unittest.TestCase):
    def test_renames_mouse_id_to_sample_id_and_sorts_by_sa_id_then_latency(self) -> None:
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "per_mouse.csv"
            table = write_per_mouse_csv(_frame(), out)
            self.assertIn("sample_id", table.columns)
            self.assertNotIn("mouse_id", table.columns)
            self.assertEqual(list(table["sample_id"]), ["m1", "m2", "m3"])
            self.assertTrue(out.exists())


class WriteSummaryCsvTests(unittest.TestCase):
    def test_one_row_per_sa_id_with_min_mean_max_latency(self) -> None:
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "summary.csv"
            summary = write_summary_csv(_frame(), out)
            self.assertEqual(len(summary), 2)
            sa1 = summary[summary["sa_id"] == "SA1"].iloc[0]
            self.assertEqual(sa1["n_mice"], 2)
            self.assertEqual(sa1["min_latency_days"], 20.0)
            self.assertEqual(sa1["max_latency_days"], 40.0)
            self.assertEqual(sa1["mean_latency_days"], 30.0)

    def test_sorted_by_min_latency_ascending(self) -> None:
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "summary.csv"
            summary = write_summary_csv(_frame(), out)
            self.assertEqual(list(summary["sa_id"]), ["SA2", "SA1"])


if __name__ == "__main__":
    unittest.main()
