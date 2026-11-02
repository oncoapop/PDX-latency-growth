import unittest

import pandas as pd

from agent_tools.rank_by_latency_and_growth import rank_by_score


class RankByLatencyAndGrowthTests(unittest.TestCase):
    def test_lower_latency_and_higher_growth_rate_both_increase_score(self) -> None:
        report = pd.DataFrame(
            [
                {"mouse_id": "fast_and_slow_growth", "latency_days": 10.0, "growth_rate_per_day": 0.02},
                {"mouse_id": "fast_and_fast_growth", "latency_days": 10.0, "growth_rate_per_day": 0.08},
                {"mouse_id": "slow_and_fast_growth", "latency_days": 80.0, "growth_rate_per_day": 0.08},
            ]
        )
        ranked = rank_by_score(report)
        self.assertEqual(
            list(ranked["mouse_id"]),
            ["fast_and_fast_growth", "fast_and_slow_growth", "slow_and_fast_growth"],
        )

    def test_missing_values_sort_to_the_bottom_with_nan_score(self) -> None:
        report = pd.DataFrame(
            [
                {"mouse_id": "complete", "latency_days": 10.0, "growth_rate_per_day": 0.05},
                {"mouse_id": "no_latency", "latency_days": float("nan"), "growth_rate_per_day": 0.05},
                {"mouse_id": "no_growth", "latency_days": 10.0, "growth_rate_per_day": float("nan")},
            ]
        )
        ranked = rank_by_score(report)
        self.assertEqual(ranked.iloc[0]["mouse_id"], "complete")
        self.assertTrue(ranked["score"].iloc[1:].isna().all())

    def test_zero_or_negative_latency_does_not_produce_a_score(self) -> None:
        report = pd.DataFrame([{"mouse_id": "zero_latency", "latency_days": 0.0, "growth_rate_per_day": 0.05}])
        ranked = rank_by_score(report)
        self.assertTrue(pd.isna(ranked.iloc[0]["score"]))


if __name__ == "__main__":
    unittest.main()
