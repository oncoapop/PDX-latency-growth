#!/usr/bin/env python3
"""Compute a per-mouse exponential growth rate from normalized/growth_measurements.csv.

Growth rate is the slope of an ordinary-least-squares fit of ln(tumour_volume_mm3)
against days-since-first-positive-measurement, using every positive-volume
measurement available for that mouse (not just the points bracketing the
100 mm3 threshold used for latency) - so it is reportable even for mice that
never reached 100 mm3, and reflects the whole observed growth curve rather
than just the two points nearest the threshold crossing.

This is independent of agent_tools/rank_candidates.py's latency calculation;
run both and join on (sa_id, passage, mouse_id) to see latency and growth
rate side by side (see plot_latency_and_growth_rate_summary.py).
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def write_csv_if_changed(df: pd.DataFrame, path: Path) -> None:
    """Skip the write when content is unchanged, so this output's mtime
    doesn't bump on every run — a downstream Make-style staleness check
    (run_latency_pipeline.py) depends on that to skip stages correctly."""
    content = df.to_csv(index=False)
    if path.exists() and path.read_text() == content:
        return
    path.write_text(content)


def growth_rate_for_trace(trace: pd.DataFrame) -> dict[str, Any]:
    positive = trace[trace["tumour_volume_mm3"] > 0].sort_values("measurement_date")
    result: dict[str, Any] = {
        "n_points_used": len(positive),
        "first_measurement_date": pd.NaT,
        "last_measurement_date": pd.NaT,
        "first_volume_mm3": np.nan,
        "last_volume_mm3": np.nan,
        "growth_rate_per_day": np.nan,
        "doubling_time_days": np.nan,
        "fit_r_squared": np.nan,
        "status": "insufficient data (fewer than 2 positive-volume measurements)",
    }
    if len(positive) < 2:
        return result

    result["first_measurement_date"] = positive["measurement_date"].iloc[0]
    result["last_measurement_date"] = positive["measurement_date"].iloc[-1]
    result["first_volume_mm3"] = float(positive["tumour_volume_mm3"].iloc[0])
    result["last_volume_mm3"] = float(positive["tumour_volume_mm3"].iloc[-1])

    start = positive["measurement_date"].iloc[0]
    x = (positive["measurement_date"] - start).dt.total_seconds().to_numpy() / 86400.0
    y = np.log(positive["tumour_volume_mm3"].to_numpy(dtype=float))
    if np.ptp(x) == 0:
        result["status"] = "all measurements on the same date; growth rate not estimable"
        return result

    slope, intercept = np.polyfit(x, y, 1)
    fitted = intercept + slope * x
    residual = float(np.sum((y - fitted) ** 2))
    total = float(np.sum((y - y.mean()) ** 2))
    r_squared = 1 - residual / total if total > 0 else np.nan

    result["growth_rate_per_day"] = float(slope)
    result["fit_r_squared"] = r_squared
    result["doubling_time_days"] = math.log(2) / slope if slope > 0 else np.nan
    result["status"] = "fitted" if slope > 0 else "fitted (non-growing or shrinking fit)"
    return result


def build_mouse_growth_rate(measurements: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for keys, trace in measurements.groupby(["sa_id", "passage", "mouse_id"], dropna=False):
        sa_id, passage, mouse_id = keys
        result = growth_rate_for_trace(trace)
        records.append({"sa_id": sa_id, "passage": passage, "mouse_id": mouse_id, **result})
    return pd.DataFrame(records)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    return parser.parse_args()


def main() -> int:
    workspace = parse_args().workspace.expanduser().resolve()
    normalized = workspace / "normalized"
    output = workspace / "analysis"
    output.mkdir(parents=True, exist_ok=True)

    measurements = pd.read_csv(normalized / "growth_measurements.csv", dtype=str)
    measurements["measurement_date"] = pd.to_datetime(measurements["measurement_date"], errors="coerce")
    measurements["tumour_volume_mm3"] = pd.to_numeric(measurements["tumour_volume_mm3"], errors="coerce")
    measurements = measurements.dropna(subset=["measurement_date", "tumour_volume_mm3"])

    growth_rate = build_mouse_growth_rate(measurements)
    write_csv_if_changed(growth_rate, output / "mouse_growth_rate.csv")
    fitted = growth_rate[growth_rate["status"] == "fitted"]
    print(f"Computed growth rate for {len(growth_rate)} mouse/trace record(s); {len(fitted)} with a positive (growing) fit")
    print(f"Wrote {output / 'mouse_growth_rate.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
