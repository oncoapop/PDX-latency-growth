#!/usr/bin/env python3
"""Static version of the interactive "latency by SA ID" chart, for Confluence
embedding (Confluence can't run the interactive JS version — see
agent_tools/post_weekly_report_to_confluence.py docs for why).

X-axis: SA ID, sorted left to right by earliest (minimum) observed latency.
Y-axis: latency to 100mm3, capped at Y_MAX; mice above the cap are drawn as a
red triangle pinned at the cap with a count label, not silently dropped.
Color: growth rate per day, clamped to the 5th-95th percentile so a few
outliers don't wash out the color differences across the bulk of the data.

Also writes two CSVs: the full per-mouse table (same rows as the chart) and
a per-SA-ID summary (mouse count, min/mean/max latency, passage range) sized
for embedding directly as a Confluence table.
"""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

plot_cache_dir = Path(tempfile.gettempdir()) / f"pdx-growth-cache-{os.getuid()}"
os.environ.setdefault("MPLCONFIGDIR", str(plot_cache_dir / "matplotlib"))
os.environ.setdefault("XDG_CACHE_HOME", str(plot_cache_dir))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

Y_MAX = 400.0


def load_data(report_csv: Path) -> pd.DataFrame:
    df = pd.read_csv(report_csv, parse_dates=["surgery_date"])
    return df.dropna(subset=["latency_days", "growth_rate_per_day", "surgery_date", "sa_id"])


def write_per_mouse_csv(df: pd.DataFrame, output_csv: Path) -> pd.DataFrame:
    table = df[["sa_id", "mouse_id", "passage", "surgery_date", "latency_days", "growth_rate_per_day"]].copy()
    table = table.rename(columns={"mouse_id": "sample_id"})
    table = table.sort_values(["sa_id", "latency_days"])
    table.to_csv(output_csv, index=False)
    return table


def write_summary_csv(df: pd.DataFrame, output_csv: Path) -> pd.DataFrame:
    summary = df.groupby("sa_id").agg(
        n_mice=("mouse_id", "count"),
        min_latency_days=("latency_days", "min"),
        mean_latency_days=("latency_days", "mean"),
        max_latency_days=("latency_days", "max"),
        min_passage=("passage", "min"),
        max_passage=("passage", "max"),
    ).reset_index()
    summary["mean_latency_days"] = summary["mean_latency_days"].round(1)
    summary = summary.sort_values("min_latency_days")
    summary.to_csv(output_csv, index=False)
    return summary


def plot_latency_by_sa_id(df: pd.DataFrame, output_png: Path) -> None:
    categories = df.groupby("sa_id")["latency_days"].min().sort_values().index.tolist()
    cat_index = {sa_id: i for i, sa_id in enumerate(categories)}

    g_sorted = np.sort(df["growth_rate_per_day"].to_numpy())
    g_min = g_sorted[int(0.05 * (len(g_sorted) - 1))]
    g_max = g_sorted[int(0.95 * (len(g_sorted) - 1))]

    cmap = matplotlib.colors.LinearSegmentedColormap.from_list("growth", ["#2a78d6", "#e34948"])
    norm = matplotlib.colors.Normalize(vmin=g_min, vmax=g_max, clip=True)

    within = df[df["latency_days"] <= Y_MAX]
    censored = df[df["latency_days"] > Y_MAX]

    fig, ax = plt.subplots(figsize=(max(14, len(categories) * 0.22), 7))
    x = within["sa_id"].map(cat_index)
    ax.scatter(
        x, within["latency_days"],
        c=within["growth_rate_per_day"], cmap=cmap, norm=norm,
        s=22, edgecolors="black", linewidths=0.3, alpha=0.9,
    )

    if not censored.empty:
        counts = censored.groupby("sa_id").size()
        for sa_id, count in counts.items():
            cx = cat_index[sa_id]
            ax.scatter([cx], [Y_MAX], marker="^", s=90, color="#e34948", edgecolors="black", linewidths=0.4, zorder=5)
            ax.annotate(
                f"▲{count}", (cx, Y_MAX), xytext=(0, 6), textcoords="offset points",
                ha="center", fontsize=8, fontweight="bold", color="#e34948",
            )

    ax.set_xlim(-1, len(categories))
    ax.set_ylim(0, Y_MAX)
    ax.set_xticks(range(len(categories)))
    ax.set_xticklabels(categories, rotation=90, fontsize=7)
    ax.set_ylabel(f"Latency to 100mm³ (days, capped at {Y_MAX:g})")
    ax.set_title("Latency to 100mm³ by SA ID, sorted by earliest observed latency", fontsize=13)
    ax.grid(True, alpha=0.2)

    sm = matplotlib.cm.ScalarMappable(cmap=cmap, norm=norm)
    cbar = fig.colorbar(sm, ax=ax, pad=0.01, fraction=0.03)
    cbar.set_label("Growth rate (per day)", fontsize=9)

    fig.text(
        0.01, 0.01,
        f"{len(df)} mice, {len(categories)} SA IDs · ▲N = N mice censored above {Y_MAX:g}d at that SA ID",
        fontsize=8, color="#555555",
    )
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(output_png, dpi=200, bbox_inches="tight")
    plt.close(fig)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report-csv", type=Path,
        default=Path("outputs/room134-aparicio-sam-latency/analysis/mouse_latency_and_growth_report.csv"),
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("outputs/room134-aparicio-sam-latency/weekly_report"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    df = load_data(args.report_csv)
    png_path = args.output_dir / "latency_by_sa_id.png"
    per_mouse_csv = args.output_dir / "latency_by_sa_id_per_mouse.csv"
    summary_csv = args.output_dir / "latency_by_sa_id_summary.csv"

    plot_latency_by_sa_id(df, png_path)
    per_mouse = write_per_mouse_csv(df, per_mouse_csv)
    summary = write_summary_csv(df, summary_csv)

    print(f"{len(df)} mice across {df['sa_id'].nunique()} SA IDs")
    print(f"Wrote {png_path}")
    print(f"Wrote {per_mouse_csv} ({len(per_mouse)} rows)")
    print(f"Wrote {summary_csv} ({len(summary)} rows)")


if __name__ == "__main__":
    main()
