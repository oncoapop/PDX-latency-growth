import unittest

import pandas as pd

from agent_tools.build_latency_and_growth_report import build_report, load_sample_identity


def _latency_row(**overrides) -> dict:
    row = {
        "sa_id": "SA501",
        "passage": "3",
        "mouse_id": "m1",
        "status": "log-linear interpolated crossing",
        "surgery_date": "2026-01-01",
        "last_known_below_100_date": "",
        "min_latency_days": float("nan"),
        "first_ge_100_date": "2026-01-20",
        "observed_latency_days": 19.0,
        "latency_left_censored": False,
        "interpolated_latency_days": 17.5,
        "latency_metric_days": 17.5,
    }
    row.update(overrides)
    return row


class BuildLatencyAndGrowthReportTests(unittest.TestCase):
    def test_bracketed_crossing_carries_the_lower_bound_through(self) -> None:
        latency = pd.DataFrame(
            [
                _latency_row(
                    status="observed crossing; bracketed by a prior sub-threshold measurement",
                    last_known_below_100_date="2026-01-17",
                    min_latency_days=16.0,
                    latency_metric_days=19.0,
                )
            ]
        )
        growth_rate = pd.DataFrame(columns=["sa_id", "passage", "mouse_id", "growth_rate_per_day", "status"])
        report = build_report(latency, growth_rate)
        row = report.iloc[0]
        self.assertEqual(row["latency_days"], 19.0)
        self.assertEqual(row["min_latency_days"], 16.0)
        self.assertEqual(row["last_known_below_100_date"], "2026-01-17")
        self.assertIn("bracketed by a prior sub-threshold measurement", row["latency_note"])

    def test_true_left_censored_case_has_no_lower_bound(self) -> None:
        latency = pd.DataFrame(
            [
                _latency_row(
                    status="left-censored: no earlier measurement available",
                    latency_left_censored=True,
                    latency_metric_days=19.0,
                )
            ]
        )
        growth_rate = pd.DataFrame(columns=["sa_id", "passage", "mouse_id", "growth_rate_per_day", "status"])
        report = build_report(latency, growth_rate)
        row = report.iloc[0]
        self.assertEqual(row["latency_days"], 19.0)
        self.assertTrue(pd.isna(row["min_latency_days"]))
        self.assertIn("left-censored", row["latency_note"])

    def test_non_censored_status_passes_through_unchanged(self) -> None:
        latency = pd.DataFrame([_latency_row()])
        growth_rate = pd.DataFrame(columns=["sa_id", "passage", "mouse_id", "growth_rate_per_day", "status"])
        report = build_report(latency, growth_rate)
        row = report.iloc[0]
        self.assertEqual(row["latency_days"], 17.5)
        self.assertEqual(row["latency_note"], "log-linear interpolated crossing")


    def test_sample_identity_is_merged_into_the_report(self) -> None:
        latency = pd.DataFrame([_latency_row()])
        growth_rate = pd.DataFrame(columns=["sa_id", "passage", "mouse_id", "growth_rate_per_day", "status"])
        measurements = pd.DataFrame(
            [
                {
                    "sa_id": "SA501", "passage": "3", "mouse_id": "m1",
                    "atlas_patient_id": "ZZTEST001", "atlas_transplant_id": "X9999-TEST0001", "atlas_barcode": "90001",
                },
                {
                    "sa_id": "SA501", "passage": "3", "mouse_id": "m1",
                    "atlas_patient_id": "", "atlas_transplant_id": "", "atlas_barcode": "",
                },
            ]
        )
        sample_identity = load_sample_identity(measurements)
        report = build_report(latency, growth_rate, sample_identity=sample_identity)
        row = report.iloc[0]
        self.assertEqual(row["atlas_patient_id"], "ZZTEST001")
        self.assertEqual(row["atlas_transplant_id"], "X9999-TEST0001")
        self.assertEqual(row["atlas_barcode"], "90001")


if __name__ == "__main__":
    unittest.main()
