#!/usr/bin/env python3
"""Weekly living-mouse tumour growth report.

Plots tumour growth, grouped by cage, for every mouse still in rotation
(a measurement within the last LIVING_WINDOW_DAYS), flags any living mouse
that has never reached NO_TAKE_THRESHOLD_MM3 more than NO_TAKE_FLAG_DAYS
after surgery, and plots the current harvest-date projection (500/1000 mm3)
for every actively-growing mouse already above the projection volume floor.

The harvest-date projection reuses plot_tumour_growth_by_cage.py's own
build_pdx_projections/plot_pdx_projections, fed from the already-extracted
normalized/growth_measurements.csv rather than a fresh scan of the raw
workbooks — orders of magnitude faster for a 1,000+ file archive.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from plot_tumour_growth_by_cage import (  # noqa: E402
    build_pdx_projections, classify_project, draw_project_legend_panel, plot_pdx_projections,
)

LIVING_WINDOW_DAYS = 10
NO_TAKE_THRESHOLD_MM3 = 100.0
NO_TAKE_FLAG_DAYS = 90

# All text on these plots is sized relative to the cage-ID title, which must
# stay readable at a glance — nothing else should be noticeably smaller.
CAGE_TITLE_FONTSIZE = 18
AXIS_LABEL_FONTSIZE = 14
TICK_LABEL_FONTSIZE = 13
MOUSE_LEGEND_FONTSIZE = 13


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
    # Reserve one extra grid slot, sized the same as a cage subplot, for the
    # project-color legend — a thin text strip above the plots is unreadable
    # at the sizes requested here.
    n_slots = len(cages) + 1
    n_cols = min(3, max(1, n_slots))
    n_rows = math.ceil(n_slots / n_cols)
    fig, axes = plt.subplots(n_rows, n_cols, figsize=(6.2 * n_cols, 4.8 * n_rows), squeeze=False)

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
                label=f"{mouse_id}{' ⚠ no take' if flagged else ''}",
            )
        ax.axhline(NO_TAKE_THRESHOLD_MM3, color="#999999", linestyle=":", linewidth=1)
        _, project_color = classify_project(cage)
        ax.set_title(f"Cage {cage}", fontsize=CAGE_TITLE_FONTSIZE, fontweight="bold", color=project_color)
        ax.set_ylabel("Tumour volume (mm3)", fontsize=AXIS_LABEL_FONTSIZE)
        ax.set_xlabel("Date", fontsize=AXIS_LABEL_FONTSIZE)
        ax.grid(True, alpha=0.25)
        ax.legend(fontsize=MOUSE_LEGEND_FONTSIZE, frameon=False)
        locator = mdates.AutoDateLocator(minticks=3, maxticks=6)
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
        ax.tick_params(axis="x", labelrotation=30, labelsize=TICK_LABEL_FONTSIZE)
        ax.tick_params(axis="y", labelsize=TICK_LABEL_FONTSIZE)

    draw_project_legend_panel(axes.flat[len(cages)], fontsize=CAGE_TITLE_FONTSIZE)
    for ax in axes.flat[len(cages) + 1:]:
        ax.axis("off")

    fig.suptitle(f"Living mice — tumour growth by cage (as of {as_of:%Y-%m-%d})", fontsize=20, y=0.998)
    fig.text(
        0.5, 0.975,
        f"{len(living_ids)} living mice across {len(cages)} cages | "
        f"dashed red = no tumour ≥{NO_TAKE_THRESHOLD_MM3:g}mm3 after {NO_TAKE_FLAG_DAYS}+ days",
        ha="center", va="top", fontsize=14,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(output_png, dpi=200, bbox_inches="tight")
    plt.close(fig)


def load_as_raw_schema(df: pd.DataFrame) -> pd.DataFrame:
    """Reshape the normalized schema into the columns
    build_pdx_projections/plot_pdx_projections expect."""
    raw = df.copy()
    raw["tumour_site"] = raw["mouse_id"].str.rsplit(" ", n=1).str[1].fillna("")
    raw["tumour_model"] = raw["sa_id"].fillna("")
    raw["sheet"] = "normalized"
    raw["date"] = raw["measurement_date"]
    return raw[["date", "cage", "sheet", "mouse_id", "tumour_site", "tumour_model", "tumour_volume_mm3"]]


def write_harvest_projection(
    df: pd.DataFrame, output_dir: Path, as_of: pd.Timestamp, generated_date: str,
) -> tuple[Path | None, Path | None, int]:
    raw = load_as_raw_schema(df)
    first_record_date = raw["date"].min()
    observed, summary = build_pdx_projections(raw)
    if summary.empty:
        return None, None, 0

    summary["analysis_generated_date"] = generated_date
    summary["data_through_date"] = as_of.date().isoformat()
    csv_path = output_dir / "pdx_growth_projection_latest.csv"
    png_path = output_dir / "pdx_growth_projection_latest.png"
    pdf_path = output_dir / "pdx_growth_projection_latest.pdf"
    summary.to_csv(csv_path, index=False)
    plot_pdx_projections(
        observed, summary, png_path, pdf_path,
        generated_date, as_of.date().isoformat(), first_record_date,
    )
    return png_path, csv_path, len(summary)


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
    generated_date = pd.Timestamp.today().date().isoformat()
    status = compute_status(df, as_of)

    png_path = output_dir / "living_mice_growth_latest.png"
    plot_living_mice(df, status, png_path, as_of)

    flagged = status[status["no_take_flag"]].sort_values("days_since_surgery", ascending=False)
    flagged_csv = output_dir / "flagged_no_take_latest.csv"
    write_if_changed(flagged_csv, flagged.to_csv(index=False))

    projection_png, projection_csv, n_projected = write_harvest_projection(
        df, output_dir, as_of, generated_date
    )

    living = status[status["is_living"]]
    print(f"As of {as_of:%Y-%m-%d}: {len(living)} living mice across {living['cage'].nunique()} cages")
    print(
        f"Flagged (no tumour ≥{NO_TAKE_THRESHOLD_MM3:g}mm3, "
        f">{NO_TAKE_FLAG_DAYS} days since surgery): {len(flagged)}"
    )
    print(f"Wrote {png_path}")
    print(f"Wrote {flagged_csv}")
    if projection_png is not None:
        print(f"Projected {n_projected} actively-growing mouse/site traces above 200 mm3")
        print(f"Wrote {projection_png}")
        print(f"Wrote {projection_csv}")
    else:
        print("No currently-growing SA* mouse above the projection volume threshold found.")


if __name__ == "__main__":
    main()
