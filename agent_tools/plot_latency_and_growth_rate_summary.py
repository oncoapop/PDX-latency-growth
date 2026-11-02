#!/usr/bin/env python3
"""Plot latency-to-100mm3 and growth rate, grouped by SA ID and by passage.

Reads analysis/mouse_latency_and_growth_report.csv (from
agent_tools/build_latency_and_growth_report.py) and writes four PNGs to
analysis/summary_plots/: latency and growth rate, each grouped by sa_id and
by passage, as one strip+box plot per group.
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


def plot_metric_by_group(data: pd.DataFrame, metric: str, group_col: str, ylabel: str, title: str, out_path: Path) -> None:
    subset = data[[group_col, metric]].dropna()
    if subset.empty:
        print(f"Skipping {out_path.name}: no data for {metric} by {group_col}")
        return

    groups = sorted(subset[group_col].unique(), key=lambda value: (len(str(value)), str(value)))
    values_by_group = [subset.loc[subset[group_col] == group, metric].to_numpy() for group in groups]

    fig, ax = plt.subplots(figsize=(max(6, 0.6 * len(groups) + 2), 5))
    positions = np.arange(1, len(groups) + 1)
    ax.boxplot(values_by_group, positions=positions, widths=0.5, showfliers=False)
    rng = np.random.default_rng(0)
    for position, values in zip(positions, values_by_group):
        jitter = rng.uniform(-0.12, 0.12, size=len(values))
        ax.scatter(np.full(len(values), position) + jitter, values, alpha=0.6, s=18, color="tab:blue", zorder=3)
        ax.annotate(f"n={len(values)}", (position, ax.get_ylim()[1]), ha="center", va="bottom", fontsize=8, annotation_clip=False)

    ax.set_xticks(positions)
    ax.set_xticklabels([str(group) for group in groups], rotation=45, ha="right")
    ax.set_xlabel(group_col)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"Wrote {out_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    return parser.parse_args()


def main() -> int:
    workspace = parse_args().workspace.expanduser().resolve()
    analysis = workspace / "analysis"
    out_dir = analysis / "summary_plots"
    out_dir.mkdir(parents=True, exist_ok=True)

    report = pd.read_csv(analysis / "mouse_latency_and_growth_report.csv", dtype={"passage": str})
    report["passage"] = report["passage"].apply(lambda value: "" if pd.isna(value) else str(value))
    report.loc[report["passage"] == "", "passage"] = "(no passage recorded)"
    report["sa_id"] = report["sa_id"].fillna("(unknown SA ID)")

    plot_metric_by_group(report, "latency_days", "sa_id", "latency to 100 mm3 (days)", "Latency to 100 mm3 by SA ID", out_dir / "latency_by_sa_id.png")
    plot_metric_by_group(report, "latency_days", "passage", "latency to 100 mm3 (days)", "Latency to 100 mm3 by passage", out_dir / "latency_by_passage.png")
    plot_metric_by_group(report, "growth_rate_per_day", "sa_id", "growth rate (ln(mm3)/day)", "Growth rate by SA ID", out_dir / "growth_rate_by_sa_id.png")
    plot_metric_by_group(report, "growth_rate_per_day", "passage", "growth rate (ln(mm3)/day)", "Growth rate by passage", out_dir / "growth_rate_by_passage.png")
    plot_metric_by_group(report, "doubling_time_days", "sa_id", "doubling time (days)", "Tumour doubling time by SA ID", out_dir / "doubling_time_by_sa_id.png")
    plot_metric_by_group(report, "doubling_time_days", "passage", "doubling time (days)", "Tumour doubling time by passage", out_dir / "doubling_time_by_passage.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
