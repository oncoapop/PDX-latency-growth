import unittest

import pandas as pd

from agent_tools.weekly_growth_report import compute_status


def _row(mouse_id, surgery_date, measurement_date, volume, cage=None, sa_id="SA1", passage="1"):
    return {
        "mouse_id": mouse_id,
        "cage": cage or mouse_id.rsplit(" ", 1)[0],
        "sa_id": sa_id,
        "passage": passage,
        "surgery_date": pd.Timestamp(surgery_date),
        "measurement_date": pd.Timestamp(measurement_date),
        "tumour_volume_mm3": volume,
    }


class ComputeStatusTests(unittest.TestCase):
    def test_recently_measured_mouse_is_living(self) -> None:
        as_of = pd.Timestamp("2026-11-02")
        df = pd.DataFrame([_row("100101 R", "2026-01-01", "2026-11-01", 50.0)])
        status = compute_status(df, as_of).set_index("mouse_id")
        self.assertTrue(status.loc["100101 R", "is_living"])

    def test_mouse_not_measured_recently_is_not_living(self) -> None:
        as_of = pd.Timestamp("2026-11-02")
        df = pd.DataFrame([_row("100101 R", "2026-01-01", "2026-06-01", 50.0)])
        status = compute_status(df, as_of).set_index("mouse_id")
        self.assertFalse(status.loc["100101 R", "is_living"])

    def test_living_mouse_never_reaching_threshold_after_90_days_is_flagged(self) -> None:
        as_of = pd.Timestamp("2026-11-02")
        df = pd.DataFrame([
            _row("100101 R", "2026-01-01", "2026-02-01", 20.0),
            _row("100101 R", "2026-01-01", "2026-11-01", 40.0),
        ])
        status = compute_status(df, as_of).set_index("mouse_id")
        self.assertTrue(status.loc["100101 R", "no_take_flag"])

    def test_living_mouse_within_90_days_is_not_flagged_yet(self) -> None:
        as_of = pd.Timestamp("2026-11-02")
        df = pd.DataFrame([_row("100101 R", "2026-10-01", "2026-11-01", 20.0)])
        status = compute_status(df, as_of).set_index("mouse_id")
        self.assertFalse(status.loc["100101 R", "no_take_flag"])

    def test_living_mouse_that_took_is_not_flagged(self) -> None:
        as_of = pd.Timestamp("2026-11-02")
        df = pd.DataFrame([
            _row("100101 R", "2026-01-01", "2026-02-01", 20.0),
            _row("100101 R", "2026-01-01", "2026-11-01", 150.0),
        ])
        status = compute_status(df, as_of).set_index("mouse_id")
        self.assertFalse(status.loc["100101 R", "no_take_flag"])

    def test_dead_mouse_never_reaching_threshold_is_not_flagged(self) -> None:
        as_of = pd.Timestamp("2026-11-02")
        df = pd.DataFrame([_row("100101 R", "2026-01-01", "2026-06-01", 20.0)])
        status = compute_status(df, as_of).set_index("mouse_id")
        self.assertFalse(status.loc["100101 R", "is_living"])
        self.assertFalse(status.loc["100101 R", "no_take_flag"])


if __name__ == "__main__":
    unittest.main()
