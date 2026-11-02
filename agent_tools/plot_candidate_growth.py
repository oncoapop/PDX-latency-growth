#!/usr/bin/env python3
"""Plot one readable growth-curve page per eligible PDX sample and passage."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.backends.backend_pdf import PdfPages

from rank_candidates import add_key


def safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_") or "candidate"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    return parser.parse_args()


def main() -> int:
    workspace = parse_args().workspace.expanduser().resolve()
    normalized = workspace / "normalized"
    analysis = workspace / "analysis"
    output_dir = analysis / "growth_curves"
    output_dir.mkdir(parents=True, exist_ok=True)

    measurements = add_key(pd.read_csv(normalized / "growth_measurements.csv", dtype=str))
    measurements["surgery_date"] = pd.to_datetime(measurements["surgery_date"], errors="coerce")
    measurements["measurement_date"] = pd.to_datetime(measurements["measurement_date"], errors="coerce")
    measurements["tumour_volume_mm3"] = pd.to_numeric(measurements["tumour_volume_mm3"], errors="coerce")
    measurements = measurements.dropna(
        subset=["sa_id", "passage", "mouse_id", "surgery_date", "measurement_date", "tumour_volume_mm3"]
    )
    measurements["days_since_surgery"] = (
        measurements["measurement_date"] - measurements["surgery_date"]
    ).dt.total_seconds() / 86400

    ranking_path = analysis / "eligible_candidate_ranking.csv"
    if not ranking_path.exists():
        raise SystemExit("Run rank_candidates.py first; eligible_candidate_ranking.csv is missing.")
    eligible = add_key(pd.read_csv(ranking_path, dtype=str, keep_default_na=False))
    candidate_keys = list(eligible[["sa_id", "passage"]].drop_duplicates().itertuples(index=False, name=None))
    if not candidate_keys:
        raise SystemExit("No passage-matched candidates with ATLAS VF material were found.")

    pdf_path = analysis / "eligible_candidate_growth_curves.pdf"
    plotted = 0
    with PdfPages(pdf_path) as pdf:
        for sa_id, passage in candidate_keys:
            candidate = measurements[
                measurements["sa_id"].eq(sa_id) & measurements["passage"].eq(passage)
            ].copy()
            if candidate.empty:
                continue

            figure, axis = plt.subplots(figsize=(11, 7))
            for mouse_id, trace in candidate.groupby("mouse_id", sort=True):
                trace = trace.sort_values("days_since_surgery")
                axis.plot(
                    trace["days_since_surgery"],
                    trace["tumour_volume_mm3"],
                    marker="o",
                    linewidth=1.8,
                    markersize=4.5,
                    label=str(mouse_id),
                )

            axis.axhline(100, color="#B42318", linestyle="--", linewidth=1.2, label="100 mm3 threshold")
            axis.set_title(f"{sa_id} | passage {passage}", fontsize=15, fontweight="bold")
            axis.set_xlabel("Days since surgery")
            axis.set_ylabel("Tumour volume (mm3)")
            axis.set_xlim(left=0)
            axis.set_ylim(bottom=0)
            axis.grid(True, color="#D0D5DD", linewidth=0.7, alpha=0.7)
            axis.legend(loc="center left", bbox_to_anchor=(1.02, 0.5), frameon=False, title="Mouse / tumour")
            figure.subplots_adjust(right=0.76, left=0.1, bottom=0.12, top=0.9)

            stem = safe_filename(f"{sa_id}_passage_{passage}")
            figure.savefig(output_dir / f"{stem}.png", dpi=180, bbox_inches="tight")
            pdf.savefig(figure, bbox_inches="tight")
            plt.close(figure)
            plotted += 1

    if plotted == 0:
        pdf_path.unlink(missing_ok=True)
        raise SystemExit("Eligible candidates had no plottable growth measurements.")
    print(f"Plotted {plotted} eligible SA ID and passage combination(s)")
    print(f"Wrote {pdf_path}")
    print(f"Wrote individual PNG files to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
