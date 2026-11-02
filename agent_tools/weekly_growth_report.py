#!/usr/bin/env python3
"""Weekly living-mouse tumour growth report.

Plots tumour growth, grouped by cage, for every mouse still in rotation
(a measurement within the last LIVING_WINDOW_DAYS), and flags any living
mouse that has never reached NO_TAKE_THRESHOLD_MM3 more than
NO_TAKE_FLAG_DAYS after surgery.
"""

from __future__ import annotations

import argparse
import math
import os
import tempfile
from pathlib import Path

plot_cache_dir = Path(tempfile.gettempdir()) / f"pdx-growth-cache-{os.getuid()}"
os.environ.setdefault("MPLCONFIGDIR", str(plot_cache_dir / "matplotlib"))
os.environ.setdefault("XDG_CACHE_HOME", str(plot_cache_dir))

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

LIVING_WINDOW_DAYS = 10
NO_TAKE_THRESHOLD_MM3 = 100.0
NO_TAKE_FLAG_DAYS = 90


def load_measurements(workspace: Path) -> pd.DataFrame:
    path = workspace / "normalized" / "growth_measurements.csv"
    df = pd.read_csv(path, parse_dates=["measurement_date", "surgery_date"])
    df["cage"] = df["mouse_id"].str.rsplit(" ", n=1).str[0]
    return df


def compute_status(df: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """One row per mouse: living/flag status as of the latest measurement date."""
    last_seen = df.groupby("mouse_id")["measurement_date"].max().rename("last_measurement_date")
    max_volume = df.groupby("mouse_id")["tumour_volume_mm3"].max().rename("max_volume_mm3")
    first = df.sort_values("measurement_date").groupby("mouse_id").first()
    status = pd.concat(
        [last_seen, max_volume, first[["cage", "sa_id", "passage", "surgery_date"]]], axis=1
    )
    status["is_living"] = status["last_measurement_date"] >= as_of - pd.Timedelta(days=LIVING_WINDOW_DAYS)
    status["days_since_surgery"] = (as_of - status["surgery_date"]).dt.days
    # "No take" is only meaningful once a mouse has had enough time to grow a
    # tumour; flagging it any earlier would just be normal latency, not a
    # concern worth a human look.
    status["no_take_flag"] = (
        status["is_living"]
        & (status["max_volume_mm3"] < NO_TAKE_THRESHOLD_MM3)
        & (status["days_since_surgery"] > NO_TAKE_FLAG_DAYS)
    )
    return status.reset_index()


def plot_living_mice(df: pd.DataFrame, status: pd.DataFrame, output_png: Path, as_of: pd.Timestamp) -> None:
    living_ids = status.loc[status["is_living"], "mouse_id"]
    flagged_ids = set(status.loc[status["no_take_flag"], "mouse_id"])
    data = df[df["mouse_id"].isin(living_ids)].sort_values(["cage", "mouse_id", "measurement_date"])
    cages = sorted(data["cage"].unique())
    n_cols = min(3, max(1, len(cages)))
    n_rows = math.ceil(len(cages) / n_cols)
    fig, axes = plt.subplots(n_rows, n_cols, figsize=(5.4 * n_cols, 4.0 * n_rows), squeeze=False)

    for ax, cage in zip(axes.flat, cages):
        cage_data = data[data["cage"] == cage]
        for mouse_id, trace in cage_data.groupby("mouse_id"):
            flagged = mouse_id in flagged_ids
            ax.plot(
                trace["measurement_date"],
                trace["tumour_volume_mm3"],
                marker="o",
                linewidth=2.2 if flagged else 1.6,
                color="crimson" if flagged else None,
                linestyle="--" if flagged else "-",
                label=f"{mouse_id}{' ⚠ no take >90d' if flagged else ''}",
            )
        ax.axhline(NO_TAKE_THRESHOLD_MM3, color="#999999", linestyle=":", linewidth=1)
        ax.set_title(f"Cage {cage}", fontsize=11)
        ax.set_ylabel("Tumour volume (mm3)")
        ax.grid(True, alpha=0.25)
        ax.legend(fontsize=7, frameon=False)
        locator = mdates.AutoDateLocator(minticks=3, maxticks=6)
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
        ax.tick_params(axis="x", labelrotation=30, labelsize=8)

    for ax in axes.flat[len(cages):]:
        ax.axis("off")

    fig.suptitle(f"Living mice — tumour growth by cage (as of {as_of:%Y-%m-%d})", fontsize=15, y=0.995)
    fig.text(
        0.5, 0.975,
        f"{len(living_ids)} living mice across {len(cages)} cages | "
        f"dashed red = no tumour ≥{NO_TAKE_THRESHOLD_MM3:g}mm3 after {NO_TAKE_FLAG_DAYS}+ days",
        ha="center", va="top", fontsize=10,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.955))
    fig.savefig(output_png, dpi=200, bbox_inches="tight")
    plt.close(fig)


def write_if_changed(path: Path, content: str) -> bool:
    """Skip the write when content is unchanged, so an unchanged file's mtime
    doesn't look stale to any later staleness check."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text() == content:
        return False
    path.write_text(content)
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace", type=Path,
        default=Path("outputs/room134-aparicio-sam-latency"),
        help="Pipeline workspace containing normalized/growth_measurements.csv",
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    workspace = args.workspace
    output_dir = args.output_dir or (workspace / "weekly_report")
    output_dir.mkdir(parents=True, exist_ok=True)

    df = load_measurements(workspace)
    as_of = df["measurement_date"].max()
    status = compute_status(df, as_of)

    png_path = output_dir / f"living_mice_growth_{as_of:%Y-%m-%d}.png"
    plot_living_mice(df, status, png_path, as_of)

    flagged = status[status["no_take_flag"]].sort_values("days_since_surgery", ascending=False)
    flagged_csv = output_dir / "flagged_no_take_latest.csv"
    write_if_changed(flagged_csv, flagged.to_csv(index=False))

    living = status[status["is_living"]]
    print(f"As of {as_of:%Y-%m-%d}: {len(living)} living mice across {living['cage'].nunique()} cages")
    print(
        f"Flagged (no tumour ≥{NO_TAKE_THRESHOLD_MM3:g}mm3, "
        f">{NO_TAKE_FLAG_DAYS} days since surgery): {len(flagged)}"
    )
    print(f"Wrote {png_path}")
    print(f"Wrote {flagged_csv}")


if __name__ == "__main__":
    main()
