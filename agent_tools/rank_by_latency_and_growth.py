#!/usr/bin/env python3
"""Rank mice by a single score combining latency-to-100mm3 (L) and growth
rate (G): score = G / L. Lower latency and higher growth rate both push the
score up, so sorting descending by score puts "fast to take, fast to grow"
mice first. Rows missing either L or G, or with L <= 0 (not a meaningful
denominator), get a NaN score and sort to the bottom.

Reads analysis/mouse_latency_and_growth_report.csv (from
agent_tools/build_latency_and_growth_report.py) and writes
analysis/mouse_ranked_by_latency_and_growth.csv, same columns plus `score`,
sorted descending by score.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def write_csv_if_changed(df: pd.DataFrame, path: Path) -> None:
    content = df.to_csv(index=False)
    if path.exists() and path.read_text() == content:
        return
    path.write_text(content)


def rank_by_score(report: pd.DataFrame) -> pd.DataFrame:
    result = report.copy()
    latency = result["latency_days"]
    growth = result["growth_rate_per_day"]
    result["score"] = np.where(latency > 0, growth / latency, np.nan)
    return result.sort_values("score", ascending=False, na_position="last")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument(
        "--threshold",
        type=float,
        default=100.0,
        help="Volume threshold in mm3 whose latency+growth report to read (default: 100)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    workspace = args.workspace.expanduser().resolve()
    threshold = args.threshold
    analysis = workspace / "analysis"
    source_dir = analysis if threshold == 100.0 else analysis / f"threshold_{threshold:g}"
    report_filename = "mouse_latency_and_growth_report.csv" if threshold == 100.0 else f"mouse_latency_and_growth_report_{threshold:g}.csv"
    out_filename = "mouse_ranked_by_latency_and_growth.csv" if threshold == 100.0 else f"mouse_ranked_by_latency_and_growth_{threshold:g}.csv"

    report = pd.read_csv(source_dir / report_filename, dtype={"passage": str})
    ranked = rank_by_score(report)
    out_path = source_dir / out_filename
    write_csv_if_changed(ranked, out_path)

    scored = ranked["score"].notna().sum()
    print(f"Wrote {len(ranked)} mouse row(s) to {out_path} ({scored} with a computed score)")
    if scored:
        top = ranked.iloc[0]
        passage = "" if pd.isna(top["passage"]) else str(top["passage"])
        passage_label = f" passage {passage}" if passage else ""
        print(f"Top: {top['sa_id']}{passage_label} {top['mouse_id']} — latency {top['latency_days']:.1f}d, growth rate {top['growth_rate_per_day']:.4f}/d, score {top['score']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
