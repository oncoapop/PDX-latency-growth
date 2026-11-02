import math
import unittest

import pandas as pd

from agent_tools.compute_growth_metrics import build_mouse_growth_rate, growth_rate_for_trace


def _trace(rows: list[tuple[str, float]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=["measurement_date", "tumour_volume_mm3"])
    frame["measurement_date"] = pd.to_datetime(frame["measurement_date"])
    return frame


class GrowthRateTests(unittest.TestCase):
    def test_exact_doubling_every_ten_days_gives_known_rate(self) -> None:
        trace = _trace(
            [
                ("2026-01-01", 50.0),
                ("2026-01-11", 100.0),
                ("2026-01-21", 200.0),
                ("2026-01-31", 400.0),
            ]
        )
        result = growth_rate_for_trace(trace)
        self.assertEqual(result["status"], "fitted")
        self.assertAlmostEqual(result["doubling_time_days"], 10.0, places=6)
        self.assertAlmostEqual(result["growth_rate_per_day"], math.log(2) / 10, places=6)
        self.assertAlmostEqual(result["fit_r_squared"], 1.0, places=6)
        self.assertEqual(result["n_points_used"], 4)

    def test_fewer_than_two_positive_points_is_insufficient(self) -> None:
        result = growth_rate_for_trace(_trace([("2026-01-01", 0.0), ("2026-01-11", 50.0)]))
        self.assertIn("insufficient data", result["status"])
        self.assertTrue(pd.isna(result["growth_rate_per_day"]))

    def test_shrinking_trace_is_reported_but_not_a_positive_doubling_time(self) -> None:
        result = growth_rate_for_trace(_trace([("2026-01-01", 200.0), ("2026-01-11", 100.0)]))
        self.assertEqual(result["status"], "fitted (non-growing or shrinking fit)")
        self.assertLess(result["growth_rate_per_day"], 0)
        self.assertTrue(pd.isna(result["doubling_time_days"]))

    def test_build_mouse_growth_rate_groups_by_sa_passage_mouse(self) -> None:
        measurements = pd.DataFrame(
            [
                {"sa_id": "SA501", "passage": "3", "mouse_id": "m1", "measurement_date": "2026-01-01", "tumour_volume_mm3": 50.0},
                {"sa_id": "SA501", "passage": "3", "mouse_id": "m1", "measurement_date": "2026-01-11", "tumour_volume_mm3": 100.0},
                {"sa_id": "SA501", "passage": "4", "mouse_id": "m2", "measurement_date": "2026-01-01", "tumour_volume_mm3": 50.0},
            ]
        )
        measurements["measurement_date"] = pd.to_datetime(measurements["measurement_date"])
        result = build_mouse_growth_rate(measurements)
        self.assertEqual(len(result), 2)
        m1 = result[result["mouse_id"] == "m1"].iloc[0]
        self.assertEqual(m1["status"], "fitted")
        m2 = result[result["mouse_id"] == "m2"].iloc[0]
        self.assertIn("insufficient data", m2["status"])


if __name__ == "__main__":
    unittest.main()
