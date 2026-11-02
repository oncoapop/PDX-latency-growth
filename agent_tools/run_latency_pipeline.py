#!/usr/bin/env python3
"""Run the full tumour latency + growth-rate pipeline end to end, skipping
any stage whose output is already newer than its inputs — a classic
Make-style staleness check — so a routine re-run (e.g. after a few new
weekly workbooks appear) only redoes what actually needs it, and a run
interrupted partway through (network drop, Ctrl-C) resumes from the first
stage that wasn't finished rather than starting over.

Stages, in order: extract -> rank -> growth_rate -> report -> ranked_score ->
plots_summary -> plots_by_sa_id.

The "extract" stage (build_growth_measurements_from_raw.py) is always
invoked rather than mtime-checked against the source tree, because checking
whether a 10-year source tree changed costs almost as much as walking it —
instead it stays fast on its own, via its internal per-workbook parse cache
and the Atlas cross-check cache (see that script's docstring). Every stage
after it is skipped when its output is already newer than its inputs.

Pass --also-threshold 200 (repeatable) to additionally run rank/report/
ranked_score at another volume threshold, writing to
analysis/threshold_<N>/ (see rank_candidates.py --threshold). These extra
stages are checkpointed the same Make-style way as the primary ones, so
they don't silently go stale relative to the 100mm3 output when only the
primary pipeline is re-run.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_TOOLS = REPO_ROOT / "agent_tools"
PYTHON = sys.executable

STAGE_ORDER = ["extract", "rank", "growth_rate", "report", "ranked_score", "plots_summary", "plots_by_sa_id"]


def _mtime(path: Path) -> float | None:
    return path.stat().st_mtime if path.exists() else None


def is_stale(outputs: list[Path], inputs: list[Path]) -> bool:
    """True when any output is missing, or any input is newer than the
    oldest existing output."""
    output_times = [_mtime(p) for p in outputs]
    if any(t is None for t in output_times):
        return True
    oldest_output = min(output_times)
    input_times = [t for t in (_mtime(p) for p in inputs) if t is not None]
    if not input_times:
        return False
    return max(input_times) > oldest_output


def run_stage(name: str, cmd: list[str], outputs: list[Path], inputs: list[Path], force: bool) -> None:
    if not force and not is_stale(outputs, inputs):
        print(f"[{name}] up to date, skipping")
        return
    print(f"[{name}] running: {' '.join(str(part) for part in cmd)}")
    subprocess.run(cmd, check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("source_root", type=Path, help="Root folder containing year-named (YYYY) subfolders")
    parser.add_argument("--no-atlas", action="store_true")
    parser.add_argument("--refresh-atlas", action="store_true")
    parser.add_argument("--atlas-max-age-hours", type=float, default=24.0)
    parser.add_argument("--no-cache", action="store_true", help="Disable the per-workbook parse cache")
    parser.add_argument("--force", action="store_true", help="Rerun every stage regardless of staleness")
    parser.add_argument(
        "--from-stage",
        choices=STAGE_ORDER,
        help="Force this stage and every stage after it to rerun, even if they look up to date",
    )
    parser.add_argument(
        "--also-threshold",
        type=float,
        action="append",
        default=[],
        metavar="MM3",
        help="Additionally run rank/report/ranked_score at this volume threshold (repeatable), e.g. --also-threshold 200",
    )
    return parser.parse_args()


def run_threshold_stages(workspace: Path, threshold: float, force: bool) -> None:
    normalized = workspace / "normalized"
    analysis = workspace / "analysis"
    source_dir = analysis / f"threshold_{threshold:g}"
    label = f"{threshold:g}"

    run_stage(
        f"rank_{label}",
        [PYTHON, str(AGENT_TOOLS / "rank_candidates.py"), str(workspace), "--threshold", str(threshold)],
        outputs=[source_dir / f"mouse_latency_to_{label}.csv"],
        inputs=[normalized / "growth_measurements.csv", normalized / "surgeries.csv", normalized / "atlas_inventory.csv"],
        force=force,
    )
    run_stage(
        f"report_{label}",
        [PYTHON, str(AGENT_TOOLS / "build_latency_and_growth_report.py"), str(workspace), "--threshold", str(threshold)],
        outputs=[source_dir / f"mouse_latency_and_growth_report_{label}.csv"],
        inputs=[
            source_dir / f"mouse_latency_to_{label}.csv",
            analysis / "mouse_growth_rate.csv",
            normalized / "growth_measurements.csv",
        ],
        force=force,
    )
    run_stage(
        f"ranked_score_{label}",
        [PYTHON, str(AGENT_TOOLS / "rank_by_latency_and_growth.py"), str(workspace), "--threshold", str(threshold)],
        outputs=[source_dir / f"mouse_ranked_by_latency_and_growth_{label}.csv"],
        inputs=[source_dir / f"mouse_latency_and_growth_report_{label}.csv"],
        force=force,
    )


def main() -> int:
    args = parse_args()
    workspace = args.workspace.expanduser().resolve()
    source_root = args.source_root.expanduser().resolve()
    normalized = workspace / "normalized"
    analysis = workspace / "analysis"

    force_from_index = STAGE_ORDER.index(args.from_stage) if args.from_stage else len(STAGE_ORDER)

    def forced(stage: str) -> bool:
        return args.force or STAGE_ORDER.index(stage) >= force_from_index

    extract_cmd = [PYTHON, str(AGENT_TOOLS / "build_growth_measurements_from_raw.py"), str(workspace), str(source_root)]
    if args.no_atlas:
        extract_cmd.append("--no-atlas")
    if args.refresh_atlas:
        extract_cmd.append("--refresh-atlas")
    if args.no_cache:
        extract_cmd.append("--no-cache")
    extract_cmd += ["--atlas-max-age-hours", str(args.atlas_max_age_hours)]

    run_stage(
        "extract",
        extract_cmd,
        outputs=[normalized / "growth_measurements.csv"],
        inputs=[],  # always invoked; its own caches keep repeats cheap
        force=True,
    )
    run_stage(
        "rank",
        [PYTHON, str(AGENT_TOOLS / "rank_candidates.py"), str(workspace)],
        outputs=[analysis / "mouse_latency_to_100.csv"],
        inputs=[normalized / "growth_measurements.csv", normalized / "surgeries.csv", normalized / "atlas_inventory.csv"],
        force=forced("rank"),
    )
    run_stage(
        "growth_rate",
        [PYTHON, str(AGENT_TOOLS / "compute_growth_metrics.py"), str(workspace)],
        outputs=[analysis / "mouse_growth_rate.csv"],
        inputs=[normalized / "growth_measurements.csv"],
        force=forced("growth_rate"),
    )
    run_stage(
        "report",
        [PYTHON, str(AGENT_TOOLS / "build_latency_and_growth_report.py"), str(workspace)],
        outputs=[analysis / "mouse_latency_and_growth_report.csv"],
        inputs=[analysis / "mouse_latency_to_100.csv", analysis / "mouse_growth_rate.csv", normalized / "growth_measurements.csv"],
        force=forced("report"),
    )
    run_stage(
        "ranked_score",
        [PYTHON, str(AGENT_TOOLS / "rank_by_latency_and_growth.py"), str(workspace)],
        outputs=[analysis / "mouse_ranked_by_latency_and_growth.csv"],
        inputs=[analysis / "mouse_latency_and_growth_report.csv"],
        force=forced("ranked_score"),
    )
    run_stage(
        "plots_summary",
        [PYTHON, str(AGENT_TOOLS / "plot_latency_and_growth_rate_summary.py"), str(workspace)],
        outputs=[analysis / "summary_plots" / "latency_by_sa_id.png"],
        inputs=[analysis / "mouse_latency_and_growth_report.csv"],
        force=forced("plots_summary"),
    )
    run_stage(
        "plots_by_sa_id",
        [PYTHON, str(AGENT_TOOLS / "plot_growth_curves_by_sa_id.py"), str(workspace)],
        outputs=[analysis / "growth_curves_by_sa_id.pdf"],
        inputs=[normalized / "growth_measurements.csv"],
        force=forced("plots_by_sa_id"),
    )

    for threshold in args.also_threshold:
        run_threshold_stages(workspace, threshold, force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
