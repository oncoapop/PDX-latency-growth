#!/usr/bin/env python3
"""Plot one growth-curve page per SA ID, overlaying every mouse that shares
that SA ID regardless of passage (passage distinguishes line style/color).

Unlike plot_candidate_growth.py this does not require ATLAS VF eligibility -
it plots every SA ID present in normalized/growth_measurements.csv, so it
works even when atlas_inventory.csv is empty (as it is for a latency-only
run; see AGENT_HANDOFF.md for the full candidate-selection workflow, which
groups by SA ID *and* passage and gates on ATLAS VF material instead).
"""

from __future__ import annotations

import argparse
import os
import re
import tempfile
from pathlib import Path

plot_cache_dir = Path(tempfile.gettempdir()) / f"pdx-growth-cache-{os.getuid()}"
os.environ.setdefault("MPLCONFIGDIR", str(plot_cache_dir / "matplotlib"))
os.environ.setdefault("XDG_CACHE_HOME", str(plot_cache_dir))

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.backends.backend_pdf import PdfPages


def safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_") or "sa_id"


def load_measurements(normalized_dir: Path) -> pd.DataFrame:
    measurements = pd.read_csv(normalized_dir / "growth_measurements.csv", dtype=str, keep_default_na=False)
    measurements["surgery_date"] = pd.to_datetime(measurements["surgery_date"], errors="coerce")
    measurements["measurement_date"] = pd.to_datetime(measurements["measurement_date"], errors="coerce")
    measurements["tumour_volume_mm3"] = pd.to_numeric(measurements["tumour_volume_mm3"], errors="coerce")
    measurements = measurements.dropna(
        subset=["sa_id", "mouse_id", "surgery_date", "measurement_date", "tumour_volume_mm3"]
    )
    measurements = measurements[measurements["sa_id"].astype(str).str.strip() != ""]
    measurements["days_since_surgery"] = (
        measurements["measurement_date"] - measurements["surgery_date"]
    ).dt.total_seconds() / 86400
    return measurements


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    return parser.parse_args()


def main() -> int:
    workspace = parse_args().workspace.expanduser().resolve()
    normalized = workspace / "normalized"
    analysis = workspace / "analysis"
    output_dir = analysis / "growth_curves_by_sa_id"
    output_dir.mkdir(parents=True, exist_ok=True)

    measurements = load_measurements(normalized)
    if measurements.empty:
        raise SystemExit("No rows with a resolved sa_id/surgery_date/measurement_date/volume to plot.")

    sa_ids = sorted(measurements["sa_id"].unique())
    passage_values = sorted(measurements["passage"].astype(str).unique())
    line_styles = ["-", "--", "-.", ":"]
    passage_style = {passage: line_styles[i % len(line_styles)] for i, passage in enumerate(passage_values)}

    pdf_path = analysis / "growth_curves_by_sa_id.pdf"
    plotted = 0
    with PdfPages(pdf_path) as pdf:
        for sa_id in sa_ids:
            candidate = measurements[measurements["sa_id"] == sa_id].copy()
            if candidate.empty:
                continue

            figure, axis = plt.subplots(figsize=(11, 7))
            color_cycle = plt.rcParams["axes.prop_cycle"].by_key()["color"]
            for index, (mouse_id, trace) in enumerate(sorted(candidate.groupby("mouse_id", sort=True))):
                trace = trace.sort_values("days_since_surgery")
                passage = str(trace["passage"].iloc[0])
                label = f"{mouse_id} (passage {passage})" if passage else str(mouse_id)
                axis.plot(
                    trace["days_since_surgery"],
                    trace["tumour_volume_mm3"],
                    marker="o",
                    linewidth=1.6,
                    markersize=4,
                    linestyle=passage_style.get(passage, "-"),
                    color=color_cycle[index % len(color_cycle)],
                    label=label,
                )

            axis.axhline(100, color="#B42318", linestyle="--", linewidth=1.2, label="100 mm3 threshold")
            axis.set_title(f"{sa_id} — all passages/mice", fontsize=15, fontweight="bold")
            axis.set_xlabel("Days since surgery")
            axis.set_ylabel("Tumour volume (mm3)")
            axis.set_xlim(left=0)
            axis.set_ylim(bottom=0)
            axis.grid(True, color="#D0D5DD", linewidth=0.7, alpha=0.7)
            axis.legend(loc="center left", bbox_to_anchor=(1.02, 0.5), frameon=False, fontsize=7, title="Mouse (passage)")
            figure.subplots_adjust(right=0.74, left=0.1, bottom=0.12, top=0.9)

            figure.savefig(output_dir / f"{safe_filename(sa_id)}.png", dpi=180, bbox_inches="tight")
            pdf.savefig(figure, bbox_inches="tight")
            plt.close(figure)
            plotted += 1

    print(f"Plotted {plotted} SA ID(s)")
    print(f"Wrote {pdf_path}")
    print(f"Wrote individual PNG files to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
