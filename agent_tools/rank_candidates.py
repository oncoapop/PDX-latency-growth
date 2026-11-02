#!/usr/bin/env python3
"""Rank unique PDX sample-passage candidates using transparent biological metrics."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


THRESHOLD_MM3 = 100.0


def write_csv_if_changed(df: pd.DataFrame, path: Path) -> None:
    """Skip the write when content is unchanged, so this output's mtime
    doesn't bump on every run — a downstream Make-style staleness check
    (run_latency_pipeline.py) depends on that to skip stages correctly."""
    content = df.to_csv(index=False)
    if path.exists() and path.read_text() == content:
        return
    path.write_text(content)


def normalize_sa_id(value: Any) -> str:
    text = str(value).strip().upper()
    return "" if text in {"", "NAN", "NONE"} else text


def normalize_passage(value: Any) -> str:
    text = str(value).strip().upper()
    if text in {"", "NAN", "NONE"}:
        return ""
    for prefix in ("PASSAGE", "P"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip(" -_")
            break
    return text


def parse_taken(value: Any) -> bool | None:
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "grown", "take", "taken"}:
        return True
    if text in {"0", "false", "no", "n", "not grown", "no take", "failed"}:
        return False
    return None


def require_columns(frame: pd.DataFrame, required: set[str], name: str) -> None:
    missing = sorted(required - set(frame.columns))
    if missing:
        raise SystemExit(f"{name} is missing required column(s): {', '.join(missing)}")


def days_to_threshold(trace: pd.DataFrame, threshold: float) -> dict[str, Any]:
    """A measurement below threshold (including an explicit 0, i.e. "checked,
    not yet palpable/measurable") immediately before the first >=threshold
    reading tightens the latency from an open-ended upper bound to a real
    bracket: [last_known_below_100_date, first_ge_100_date]. Only when there
    is no earlier measurement at all (position 0 in the trace) is the true
    crossing date genuinely unknown back to surgery — that is the only case
    still called "left-censored"."""
    trace = trace.sort_values("measurement_date").copy()
    result: dict[str, Any] = {
        "status": "not estimable",
        "surgery_date": pd.NaT,
        "last_known_below_100_date": pd.NaT,
        "first_ge_100_date": pd.NaT,
        "min_latency_days": np.nan,
        "observed_latency_days": np.nan,
        "latency_left_censored": False,
        "interpolated_latency_days": np.nan,
        "latency_metric_days": np.nan,
    }
    surgery_dates = trace["surgery_date"].dropna().dt.normalize().unique()
    if len(surgery_dates) != 1:
        result["status"] = "missing or conflicting surgery date"
        return result
    surgery = pd.Timestamp(surgery_dates[0])
    result["surgery_date"] = surgery
    crossing_positions = np.flatnonzero(trace["tumour_volume_mm3"].to_numpy() >= threshold)
    if len(crossing_positions) == 0:
        result["status"] = "did not reach threshold"
        return result

    position = int(crossing_positions[0])
    current = trace.iloc[position]
    observed_days = (current["measurement_date"].normalize() - surgery).days
    result.update(
        {
            "status": "observed crossing",
            "first_ge_100_date": current["measurement_date"].normalize(),
            "observed_latency_days": observed_days,
            "latency_metric_days": float(observed_days),
        }
    )
    if position == 0:
        result["status"] = "left-censored: no earlier measurement available"
        result["latency_left_censored"] = True
        return result

    previous = trace.iloc[position - 1]
    previous_date = previous["measurement_date"].normalize()
    result["last_known_below_100_date"] = previous_date
    result["min_latency_days"] = (previous_date - surgery).days

    previous_volume = float(previous["tumour_volume_mm3"])
    current_volume = float(current["tumour_volume_mm3"])
    interval_days = (current["measurement_date"] - previous["measurement_date"]).total_seconds() / 86400
    if previous_volume > 0 and current_volume > previous_volume and interval_days > 0:
        fraction = math.log(threshold / previous_volume) / math.log(current_volume / previous_volume)
        interpolated_date = previous["measurement_date"] + pd.to_timedelta(fraction * interval_days, unit="D")
        interpolated_days = (interpolated_date - surgery).total_seconds() / 86400
        result["status"] = "log-linear interpolated crossing"
        result["interpolated_latency_days"] = interpolated_days
        result["latency_metric_days"] = interpolated_days
    elif current_volume == threshold:
        result["status"] = "measured exactly at threshold"
    else:
        result["status"] = "observed crossing; bracketed by a prior sub-threshold measurement"
    return result


def add_key(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["sa_id"] = result["sa_id"].map(normalize_sa_id)
    result["passage"] = result["passage"].map(normalize_passage)
    return result


def build_mouse_latency(measurements: pd.DataFrame, threshold: float = THRESHOLD_MM3) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for keys, trace in measurements.groupby(["sa_id", "passage", "mouse_id"], dropna=False):
        sa_id, passage, mouse_id = keys
        result = days_to_threshold(trace, threshold)
        records.append({"sa_id": sa_id, "passage": passage, "mouse_id": mouse_id, **result})
    return pd.DataFrame(records)


def atlas_flags(atlas: pd.DataFrame) -> pd.DataFrame:
    result = atlas.copy()
    result["material_label"] = result["material_label"].fillna("").astype(str).str.strip().str.upper()
    result["atlas_location"] = result["atlas_location"].fillna("").astype(str).str.strip()
    result["eligible_row"] = result["material_label"].eq("VF") & result["atlas_location"].ne("")
    result["vf_location"] = result["atlas_location"].where(result["eligible_row"], "")
    return (
        result.groupby(["sa_id", "passage"], as_index=False)
        .agg(
            has_atlas_vf=("eligible_row", "max"),
            atlas_vf_locations=("vf_location", lambda values: "; ".join(sorted(set(v for v in values if v)))),
        )
    )


def surgery_metrics(surgeries: pd.DataFrame) -> pd.DataFrame:
    work = surgeries.copy()
    work["taken_parsed"] = work["tumour_taken"].map(parse_taken)
    rows: list[dict[str, Any]] = []
    for keys, group in work.groupby(["sa_id", "passage"], dropna=False):
        taken = group["taken_parsed"]
        total = len(group)
        confirmed = int((taken == True).sum())  # noqa: E712
        known = int(taken.notna().sum())
        rows.append(
            {
                "sa_id": keys[0],
                "passage": keys[1],
                "total_surgeries": total,
                "confirmed_tumour_takes": confirmed,
                "unknown_take_outcomes": total - known,
                "take_rate_all_surgeries": confirmed / total if total else np.nan,
                "take_rate_known_outcomes": confirmed / known if known else np.nan,
            }
        )
    return pd.DataFrame(rows)


def candidate_metrics(mouse_latency: pd.DataFrame, surgeries: pd.DataFrame, atlas: pd.DataFrame) -> pd.DataFrame:
    successful = mouse_latency[mouse_latency["latency_metric_days"].notna()].copy()
    latency = (
        successful.groupby(["sa_id", "passage"], as_index=False)
        .agg(
            tumours_reaching_100=("mouse_id", "nunique"),
            median_latency_to_100_days=("latency_metric_days", "median"),
            fastest_latency_to_100_days=("latency_metric_days", "min"),
            slowest_latency_to_100_days=("latency_metric_days", "max"),
            latency_left_censored_count=("latency_left_censored", "sum"),
        )
    )
    latency["growth_spread_days"] = (
        latency["slowest_latency_to_100_days"] - latency["fastest_latency_to_100_days"]
    )
    latency.loc[latency["tumours_reaching_100"] < 2, "growth_spread_days"] = np.nan

    keys = pd.concat(
        [mouse_latency[["sa_id", "passage"]], surgeries[["sa_id", "passage"]], atlas[["sa_id", "passage"]]],
        ignore_index=True,
    ).drop_duplicates()
    metrics = keys.merge(latency, on=["sa_id", "passage"], how="left")
    metrics = metrics.merge(surgery_metrics(surgeries), on=["sa_id", "passage"], how="left")
    metrics = metrics.merge(atlas_flags(atlas), on=["sa_id", "passage"], how="left")
    metrics["has_atlas_vf"] = metrics["has_atlas_vf"].fillna(False).astype(bool)
    metrics["complete_decision_data"] = (
        metrics["sa_id"].ne("")
        & metrics["passage"].ne("")
        & metrics["has_atlas_vf"]
        & metrics["median_latency_to_100_days"].notna()
        & metrics["take_rate_all_surgeries"].notna()
        & metrics["growth_spread_days"].notna()
    )
    metrics["latency_rank"] = metrics["median_latency_to_100_days"].rank(method="min", ascending=True)
    metrics["take_rate_rank"] = metrics["take_rate_all_surgeries"].rank(method="min", ascending=False)
    metrics["growth_tightness_rank"] = metrics["growth_spread_days"].rank(method="min", ascending=True)
    return metrics.sort_values(["sa_id", "passage"])


def quality_issues(metrics: pd.DataFrame) -> pd.DataFrame:
    issues: list[dict[str, str]] = []
    for row in metrics.itertuples(index=False):
        key = {"sa_id": row.sa_id, "passage": row.passage}
        if not row.sa_id:
            issues.append({**key, "issue": "Missing SA ID; candidate identity is incomplete."})
        if not row.passage:
            issues.append({**key, "issue": "Missing passage; sample cannot be compared passage-specifically."})
        if not row.has_atlas_vf:
            issues.append({**key, "issue": "No passage-matched ATLAS record labelled exactly VF with a location."})
        if pd.isna(row.take_rate_all_surgeries):
            issues.append({**key, "issue": "No surgery records available."})
        elif row.unknown_take_outcomes > 0:
            issues.append({**key, "issue": f"{int(row.unknown_take_outcomes)} surgery outcome(s) are unknown."})
        if pd.isna(row.median_latency_to_100_days):
            issues.append({**key, "issue": "No tumour reached 100 mm3 in the supplied measurements."})
        if pd.isna(row.growth_spread_days):
            issues.append({**key, "issue": "Growth spread requires at least two tumours reaching 100 mm3."})
        if getattr(row, "latency_left_censored_count", 0) > 0:
            issues.append({**key, "issue": "At least one latency is left-censored because the first measurement was already >=100 mm3."})
    return pd.DataFrame(issues, columns=["sa_id", "passage", "issue"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument(
        "--threshold",
        type=float,
        default=THRESHOLD_MM3,
        help=f"Volume threshold in mm3 for the latency calculation (default: {THRESHOLD_MM3:g})",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    workspace = args.workspace.expanduser().resolve()
    threshold = args.threshold
    normalized = workspace / "normalized"
    # The default threshold (100mm3) keeps writing to analysis/ with the
    # original filenames, unchanged, so nothing that already depends on
    # those paths (plot_candidate_growth.py, AGENT_HANDOFF.md, existing
    # tests) is affected. Any other threshold gets its own subdirectory so a
    # sensitivity-analysis run (e.g. --threshold 200) never collides with
    # the primary one.
    output = workspace / "analysis" if threshold == THRESHOLD_MM3 else workspace / "analysis" / f"threshold_{threshold:g}"
    output.mkdir(parents=True, exist_ok=True)
    latency_filename = f"mouse_latency_to_{threshold:g}.csv"

    measurements = pd.read_csv(normalized / "growth_measurements.csv", dtype=str)
    surgeries = pd.read_csv(normalized / "surgeries.csv", dtype=str, keep_default_na=False)
    atlas = pd.read_csv(normalized / "atlas_inventory.csv", dtype=str, keep_default_na=False)
    require_columns(
        measurements,
        {"sa_id", "passage", "mouse_id", "surgery_date", "measurement_date", "tumour_volume_mm3"},
        "growth_measurements.csv",
    )
    require_columns(surgeries, {"sa_id", "passage", "surgery_id", "tumour_taken"}, "surgeries.csv")
    require_columns(atlas, {"sa_id", "passage", "atlas_location", "material_label"}, "atlas_inventory.csv")

    measurements = add_key(measurements)
    surgeries = add_key(surgeries)
    atlas = add_key(atlas)
    measurements["surgery_date"] = pd.to_datetime(measurements["surgery_date"], errors="coerce")
    measurements["measurement_date"] = pd.to_datetime(measurements["measurement_date"], errors="coerce")
    measurements["tumour_volume_mm3"] = pd.to_numeric(measurements["tumour_volume_mm3"], errors="coerce")
    measurements = measurements.dropna(subset=["measurement_date", "tumour_volume_mm3"])

    latency = build_mouse_latency(measurements, threshold)
    metrics = candidate_metrics(latency, surgeries, atlas)
    eligible = metrics[metrics["has_atlas_vf"]].copy()
    eligible = eligible.sort_values(
        ["complete_decision_data", "median_latency_to_100_days", "take_rate_all_surgeries", "growth_spread_days"],
        ascending=[False, True, False, True],
        na_position="last",
    )
    eligible.insert(0, "selection_rank", range(1, len(eligible) + 1))
    eligible["recommended_for_growth"] = eligible["selection_rank"].eq(1) & eligible["complete_decision_data"]

    write_csv_if_changed(latency, output / latency_filename)
    write_csv_if_changed(metrics, output / "candidate_metrics.csv")
    write_csv_if_changed(eligible, output / "eligible_candidate_ranking.csv")
    write_csv_if_changed(quality_issues(metrics), output / "data_quality_issues.csv")
    print(f"Evaluated {len(metrics)} unique SA ID and passage combination(s) at the {threshold:g} mm3 threshold")
    print(f"Found {len(eligible)} passage-matched candidate(s) with ATLAS VF material")
    print(f"Wrote analysis outputs to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
