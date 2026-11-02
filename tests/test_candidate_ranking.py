import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent_tools"))

from rank_candidates import add_key, atlas_flags, build_mouse_latency, candidate_metrics


class CandidateRankingTests(unittest.TestCase):
    def test_passages_remain_separate_and_metrics_are_calculated(self) -> None:
        measurements = pd.DataFrame(
            [
                ["SA501", "P1", "m1", "2026-01-01", "2026-01-11", 50],
                ["SA501", "P1", "m1", "2026-01-01", "2026-01-21", 200],
                ["SA501", "P1", "m2", "2026-01-01", "2026-01-16", 50],
                ["SA501", "P1", "m2", "2026-01-01", "2026-01-26", 200],
                ["SA501", "P2", "m3", "2026-02-01", "2026-02-21", 100],
            ],
            columns=["sa_id", "passage", "mouse_id", "surgery_date", "measurement_date", "tumour_volume_mm3"],
        )
        measurements = add_key(measurements)
        measurements["surgery_date"] = pd.to_datetime(measurements["surgery_date"])
        measurements["measurement_date"] = pd.to_datetime(measurements["measurement_date"])
        latency = build_mouse_latency(measurements)

        surgeries = add_key(pd.DataFrame([
            ["SA501", "1", "s1", "1"], ["SA501", "1", "s2", "1"],
            ["SA501", "1", "s3", "0"], ["SA501", "2", "s4", "1"],
        ], columns=["sa_id", "passage", "surgery_id", "tumour_taken"]))
        atlas = add_key(pd.DataFrame([
            ["SA501", "1", "A1", "VF"], ["SA501", "2", "A2", "FF"],
        ], columns=["sa_id", "passage", "atlas_location", "material_label"]))

        metrics = candidate_metrics(latency, surgeries, atlas).set_index(["sa_id", "passage"])
        self.assertEqual(set(metrics.index), {("SA501", "1"), ("SA501", "2")})
        self.assertAlmostEqual(metrics.loc[("SA501", "1"), "take_rate_all_surgeries"], 2 / 3)
        self.assertTrue(metrics.loc[("SA501", "1"), "has_atlas_vf"])
        self.assertFalse(metrics.loc[("SA501", "2"), "has_atlas_vf"])
        self.assertGreater(metrics.loc[("SA501", "1"), "growth_spread_days"], 0)

    def test_first_measurement_above_threshold_is_left_censored(self) -> None:
        measurements = add_key(pd.DataFrame([
            ["SA1", "3", "m1", "2026-01-01", "2026-01-10", 125],
        ], columns=["sa_id", "passage", "mouse_id", "surgery_date", "measurement_date", "tumour_volume_mm3"]))
        measurements["surgery_date"] = pd.to_datetime(measurements["surgery_date"])
        measurements["measurement_date"] = pd.to_datetime(measurements["measurement_date"])
        result = build_mouse_latency(measurements).iloc[0]
        self.assertTrue(result["latency_left_censored"])
        self.assertEqual(result["observed_latency_days"], 9)

    def test_zero_volume_before_crossing_gives_a_tight_bracket_not_left_censored(self) -> None:
        measurements = add_key(pd.DataFrame([
            ["SA1", "3", "m1", "2026-01-01", "2026-01-17", 0],
            ["SA1", "3", "m1", "2026-01-01", "2026-01-20", 125],
        ], columns=["sa_id", "passage", "mouse_id", "surgery_date", "measurement_date", "tumour_volume_mm3"]))
        measurements["surgery_date"] = pd.to_datetime(measurements["surgery_date"])
        measurements["measurement_date"] = pd.to_datetime(measurements["measurement_date"])
        result = build_mouse_latency(measurements).iloc[0]
        self.assertFalse(result["latency_left_censored"])
        self.assertEqual(result["status"], "observed crossing; bracketed by a prior sub-threshold measurement")
        self.assertEqual(result["last_known_below_100_date"], pd.Timestamp("2026-01-17"))
        self.assertEqual(result["min_latency_days"], 16)
        self.assertEqual(result["observed_latency_days"], 19)
        self.assertEqual(result["latency_metric_days"], 19.0)

    def test_interpolated_crossing_also_reports_its_lower_bound(self) -> None:
        measurements = add_key(pd.DataFrame([
            ["SA1", "3", "m1", "2026-01-01", "2026-01-10", 50],
            ["SA1", "3", "m1", "2026-01-01", "2026-01-20", 200],
        ], columns=["sa_id", "passage", "mouse_id", "surgery_date", "measurement_date", "tumour_volume_mm3"]))
        measurements["surgery_date"] = pd.to_datetime(measurements["surgery_date"])
        measurements["measurement_date"] = pd.to_datetime(measurements["measurement_date"])
        result = build_mouse_latency(measurements).iloc[0]
        self.assertEqual(result["status"], "log-linear interpolated crossing")
        self.assertEqual(result["last_known_below_100_date"], pd.Timestamp("2026-01-10"))
        self.assertEqual(result["min_latency_days"], 9)
        self.assertTrue(result["min_latency_days"] < result["latency_metric_days"] < result["observed_latency_days"])

    def test_candidate_that_never_reaches_threshold_remains_reportable(self) -> None:
        measurements = add_key(pd.DataFrame([
            ["SA2", "1", "m1", "2026-01-01", "2026-01-10", 80],
        ], columns=["sa_id", "passage", "mouse_id", "surgery_date", "measurement_date", "tumour_volume_mm3"]))
        measurements["surgery_date"] = pd.to_datetime(measurements["surgery_date"])
        measurements["measurement_date"] = pd.to_datetime(measurements["measurement_date"])
        latency = build_mouse_latency(measurements)
        surgeries = add_key(pd.DataFrame([
            ["SA2", "1", "s1", "0"],
        ], columns=["sa_id", "passage", "surgery_id", "tumour_taken"]))
        atlas = add_key(pd.DataFrame([
            ["SA2", "1", "B1", "VF"],
        ], columns=["sa_id", "passage", "atlas_location", "material_label"]))

        metrics = candidate_metrics(latency, surgeries, atlas).iloc[0]
        self.assertEqual(latency.iloc[0]["status"], "did not reach threshold")
        self.assertTrue(pd.isna(metrics["median_latency_to_100_days"]))
        self.assertFalse(metrics["complete_decision_data"])

    def test_single_tumour_does_not_claim_growth_tightness(self) -> None:
        measurements = add_key(pd.DataFrame([
            ["SA3", "1", "m1", "2026-01-01", "2026-01-11", 100],
        ], columns=["sa_id", "passage", "mouse_id", "surgery_date", "measurement_date", "tumour_volume_mm3"]))
        measurements["surgery_date"] = pd.to_datetime(measurements["surgery_date"])
        measurements["measurement_date"] = pd.to_datetime(measurements["measurement_date"])
        surgeries = add_key(pd.DataFrame([
            ["SA3", "1", "s1", "1"],
        ], columns=["sa_id", "passage", "surgery_id", "tumour_taken"]))
        atlas = add_key(pd.DataFrame([
            ["SA3", "1", "C1", "VF"],
        ], columns=["sa_id", "passage", "atlas_location", "material_label"]))

        metrics = candidate_metrics(build_mouse_latency(measurements), surgeries, atlas).iloc[0]
        self.assertTrue(pd.isna(metrics["growth_spread_days"]))
        self.assertFalse(metrics["complete_decision_data"])

    def test_only_vf_locations_are_reported(self) -> None:
        atlas = add_key(pd.DataFrame([
            ["SA4", "1", "VF-FREEZER", "VF"],
            ["SA4", "1", "FF-FREEZER", "FF"],
        ], columns=["sa_id", "passage", "atlas_location", "material_label"]))

        flags = atlas_flags(atlas).iloc[0]
        self.assertTrue(flags["has_atlas_vf"])
        self.assertEqual(flags["atlas_vf_locations"], "VF-FREEZER")

    def test_missing_candidate_identity_cannot_be_recommended(self) -> None:
        measurements = add_key(pd.DataFrame([
            ["SA5", "", "m1", "2026-01-01", "2026-01-11", 100],
            ["SA5", "", "m2", "2026-01-01", "2026-01-12", 100],
        ], columns=["sa_id", "passage", "mouse_id", "surgery_date", "measurement_date", "tumour_volume_mm3"]))
        measurements["surgery_date"] = pd.to_datetime(measurements["surgery_date"])
        measurements["measurement_date"] = pd.to_datetime(measurements["measurement_date"])
        surgeries = add_key(pd.DataFrame([
            ["SA5", "", "s1", "1"], ["SA5", "", "s2", "1"],
        ], columns=["sa_id", "passage", "surgery_id", "tumour_taken"]))
        atlas = add_key(pd.DataFrame([
            ["SA5", "", "D1", "VF"],
        ], columns=["sa_id", "passage", "atlas_location", "material_label"]))

        metrics = candidate_metrics(build_mouse_latency(measurements), surgeries, atlas).iloc[0]
        self.assertFalse(metrics["complete_decision_data"])


if __name__ == "__main__":
    unittest.main()
