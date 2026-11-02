#!/usr/bin/env python3
"""Merge mouse_latency_to_100.csv and mouse_growth_rate.csv into one per-mouse
report.

latency_days is the best point estimate (log-linear interpolated when a
prior positive measurement exists to interpolate from; otherwise the day the
tumour was first recorded >=100 mm3). Alongside it, min_latency_days /
last_known_below_100_date give the tightest available lower bound: the most
recent earlier measurement that was still below 100 mm3 (this can be an
explicit 0, i.e. "checked, not yet palpable" - agent_tools/rank_candidates.py
now receives those rows rather than having them filtered out upstream). Only
when no earlier measurement exists at all (last_known_below_100_date is
blank) is the latency genuinely open-ended back to the surgery date - that
is the only case still labelled "left-censored" in latency_note.

Run agent_tools/rank_candidates.py and agent_tools/compute_growth_metrics.py
first; this script only reads their analysis/ outputs.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def write_csv_if_changed(df: pd.DataFrame, path: Path) -> None:
    """Skip the write when content is unchanged, so this output's mtime
    doesn't bump on every run — a downstream Make-style staleness check
    (run_latency_pipeline.py) depends on that to skip stages correctly."""
    content = df.to_csv(index=False)
    if path.exists() and path.read_text() == content:
        return
    path.write_text(content)


def load_sample_identity(measurements: pd.DataFrame) -> pd.DataFrame:
    """One row per (sa_id, passage, mouse_id) carrying the Atlas sample
    identifiers (patient_id, transplant_id, barcode) alongside sa_id/passage
    - these are constant per animal, so the first non-blank value across
    that animal's measurement rows is representative."""
    columns = ["atlas_patient_id", "atlas_transplant_id", "atlas_barcode"]
    available = [c for c in columns if c in measurements.columns]
    if not available:
        return measurements[["sa_id", "passage", "mouse_id"]].drop_duplicates()

    def first_non_blank(series: pd.Series) -> str:
        non_blank = series[series.astype(str).str.strip() != ""]
        return non_blank.iloc[0] if len(non_blank) else ""

    return (
        measurements.groupby(["sa_id", "passage", "mouse_id"], dropna=False)[available]
        .agg(first_non_blank)
        .reset_index()
    )


def build_report(
    latency: pd.DataFrame,
    growth_rate: pd.DataFrame,
    threshold: float = 100.0,
    sample_identity: pd.DataFrame | None = None,
) -> pd.DataFrame:
    latency = latency.copy()
    latency["latency_days"] = latency["latency_metric_days"]
    latency["latency_note"] = latency["status"]
    latency["threshold_mm3"] = threshold
    keep = latency[
        [
            "sa_id",
            "passage",
            "mouse_id",
            "threshold_mm3",
            "surgery_date",
            "last_known_below_100_date",
            "min_latency_days",
            "first_ge_100_date",
            "latency_days",
            "latency_note",
        ]
    ]
    report = keep.merge(growth_rate, on=["sa_id", "passage", "mouse_id"], how="outer", suffixes=("", "_growth"))
    if sample_identity is not None and not sample_identity.empty:
        report = report.merge(sample_identity, on=["sa_id", "passage", "mouse_id"], how="left")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument(
        "--threshold",
        type=float,
        default=100.0,
        help="Volume threshold in mm3 whose rank_candidates.py output to read (default: 100)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    workspace = args.workspace.expanduser().resolve()
    threshold = args.threshold
    analysis = workspace / "analysis"
    source_dir = analysis if threshold == 100.0 else analysis / f"threshold_{threshold:g}"
    report_filename = "mouse_latency_and_growth_report.csv" if threshold == 100.0 else f"mouse_latency_and_growth_report_{threshold:g}.csv"

    latency = pd.read_csv(source_dir / f"mouse_latency_to_{threshold:g}.csv", dtype={"passage": str})
    growth_rate = pd.read_csv(analysis / "mouse_growth_rate.csv", dtype={"passage": str})
    measurements = pd.read_csv(workspace / "normalized" / "growth_measurements.csv", dtype=str, keep_default_na=False)
    sample_identity = load_sample_identity(measurements)
    report = build_report(latency, growth_rate, threshold, sample_identity)
    write_csv_if_changed(report, source_dir / report_filename)

    with_latency = report["latency_days"].notna().sum()
    with_growth_rate = report["growth_rate_per_day"].notna().sum()
    print(f"Wrote {len(report)} mouse row(s) to {source_dir / report_filename}")
    print(f"{with_latency} have a latency-to-{threshold:g}mm3 value; {with_growth_rate} have a fitted growth rate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
