# PDX Latency & Growth Tracking

Repository: <https://github.com/oncoapop/PDX-latency-growth>

Reusable analysis scripts for PDX mouse tumour-volume Excel workbooks: cage-based growth plotting, a passage-specific candidate-selection workflow, and a latency-to-threshold + growth-rate pipeline that cross-checks sample identity and surgery dates against Atlas.

No real source data (workbooks, normalized CSVs, Atlas exports, credentials) is ever committed to this repository — see `.gitignore` and the Safety and provenance section of [AGENT_HANDOFF.md](AGENT_HANDOFF.md). Everything here is code, tests (with synthetic fixtures), and documentation.

The script reads dated `Tumor Volumes*.xlsx` files, extracts tumour measurements by worksheet, mouse/cage ID, tumour model, and tumour site, then generates:

- tumour-growth plots by cage and worksheet
- free-scale, same-linear-scale, and log-scale plots
- audit CSVs for extracted rows and plotted values
- current PDX projections for active tumours above a configurable volume threshold

An example workbook is included in `examples/`.

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python plot_tumour_growth_by_cage.py --config config.yml
```

Outputs are written to the directory configured in `config.yml`.

## Configuration

The analysis is not tied to a hard-coded data directory. Edit `config.yml`:

```yaml
input_dir: examples
output_dir: outputs
pattern: "Tumor Volumes*.xlsx"
aggregate: sum
include_zero: false
scale: all
min_latest_volume_for_projection: 200
projection_thresholds_mm3: [500, 1000]
projection_fit_windows: [5, 4, 3]
min_projection_r2: 0.5
```

Command-line arguments can override the config:

```bash
python plot_tumour_growth_by_cage.py --config config.yml /path/to/excel/files --output-dir /path/to/output
```

## Workbook Extraction

The script scans each workbook for sheets with `Mouse ID` and `Volume (mm3)` headers. It extracts:

- date from the workbook filename, for example `2026.06.22`
- worksheet name, for example `Xenograft` or `Organoid Study`
- mouse ID and cage ID
- tumour model from the comments column, for example `SA501 Breast`
- mouse identification (ear notch) for example `L`, `R`, `B`, or `N`
- tumour volume in mm3
- source workbook and Excel row number for auditability

Rows with blank mouse IDs inherit the most recent valid mouse ID, matching the template structure used in the source workbooks. Rows marked as passage notes are kept as notes but do not overwrite the mouse ID.

Parenthesised identifiers are part of the cage ID. For example, `251220(1)` and `251220(3)` are plotted as separate cages. A trailing mouse suffix remains grouped within its parent cage, so `260409(2)-1` is assigned to cage `260409(2)`.

## Growth Plots

The main growth plots group traces by the complete cage ID, tumour model, and worksheet. Trace labels include mouse ID and tumour site.

Generated plot variants:

- free y-axis
- same linear y-axis
- log y-axis

Date axes are formatted with limited, readable ticks. Plot filenames include both the generation date and the latest measurement date, for example:

```text
tumour_growth_by_cage_sum_generated_2026-06-23_through_2026-06-22.png
```

## PDX Projection Logic

Current PDX projection includes only traces that:

- have a tumour model beginning with `SA`
- were measured on the latest measurement date in the dataset
- have latest volume greater than `min_latest_volume_for_projection`

For each trace, the script fits recent exponential growth using log-linear regression. It tests recent windows from `projection_fit_windows`, such as 5, 4, and 3 points. It selects the best projectable fit with `R2 >= min_projection_r2`; if none pass, it keeps the best projectable fit and labels it as weak in the CSV.

The projection CSV includes:

- latest measured volume and date
- selected fit window
- daily log growth rate
- doubling time
- R2
- projected or observed dates for each threshold

The projection plot shows observed data as solid lines and projected data as dashed lines. It labels only the final threshold date per trace, in bold, to reduce overlap.

## Audit Outputs

The extracted-row CSV is the source-of-truth audit file. It records the source workbook, worksheet, row number, mouse ID, cage, tumour model, tumour site, and extracted volume for each point.

Use this file to verify any plotted point against the original Excel source.

## Portable Agent Workflow

For analysis by another agent, use [AGENT_HANDOFF.md](AGENT_HANDOFF.md). The `agent_tools/` scripts copy Excel, CSV, TSV, and DOCX inputs into an isolated working directory, preserve a source hash manifest, extract content with source locations, and rank unique `SA ID + passage` candidates.

The candidate workflow uses four separate decision signals:

- latency from surgery to 100 mm3
- tumour take rate per surgery
- passage-matched ATLAS inventory labelled `VF`
- spread between fastest and slowest latency to 100 mm3

It does not combine passages or use an unexplained weighted score.

## Latency + growth-rate pipeline for raw cage-based workbooks

For a decade-spanning real data room of year-named (`YYYY`) subfolders of `Tumor Volumes(YYYY.MM.DD)*.xlsx` workbooks, `agent_tools/run_latency_pipeline.py` chains the raw-workbook extraction (`build_growth_measurements_from_raw.py`, which cross-checks surgery dates against Atlas when `~/.atlas` is configured), `rank_candidates.py`, `compute_growth_metrics.py` (per-mouse growth rate and doubling time), `build_latency_and_growth_report.py`, and the two plotting scripts (`plot_latency_and_growth_rate_summary.py`, `plot_growth_curves_by_sa_id.py`) into one command:

```zsh
.venv/bin/python agent_tools/run_latency_pipeline.py /path/to/workspace /path/to/data-room-folder
```

Each stage is skipped when its output is already newer than its inputs, and the workbook/Atlas pulls are themselves cached under `<workspace>/.cache/`, so a routine re-run (a few new weekly files added since last time) only redoes what actually changed. Pass `--force` to rerun everything, or `--from-stage <stage>` to force one stage and everything after it.

### Sample identity and passage

`build_growth_measurements_from_raw.py` resolves `sa_id` and `passage` in three tiers, in order: (1) a model name in the workbook's own Comments column; (2) failing that, an empirical crosswalk from Atlas's `patient_id` to a known `SA###` label, built from other animals where both are independently known, used only when unambiguous; (3) failing that too, Atlas's own `patient_id` directly — not the lab's usual naming, but still the authoritative, unique sample identity when Atlas already knows exactly which animal this is. Every row in `growth_measurements.csv` also carries `atlas_patient_id`, `atlas_transplant_id`, and `atlas_barcode` for the exact per-animal sample/tumour ID, plus `sa_id_source`/`passage_source` so you can see which tier resolved it.

### Comparing latency thresholds

`rank_candidates.py`, `build_latency_and_growth_report.py`, and `rank_by_latency_and_growth.py` all accept `--threshold <mm3>` (default 100). A non-default threshold writes to its own `analysis/threshold_<N>/` subdirectory rather than overwriting the default output, so two cutoffs can be compared side by side:

```zsh
.venv/bin/python agent_tools/rank_candidates.py /path/to/workspace --threshold 200
.venv/bin/python agent_tools/build_latency_and_growth_report.py /path/to/workspace --threshold 200
.venv/bin/python agent_tools/rank_by_latency_and_growth.py /path/to/workspace --threshold 200
```

Or pass `--also-threshold 200` (repeatable) to `run_latency_pipeline.py` to run and checkpoint both thresholds together in one command, so they can't drift out of sync with each other.

### Latency brackets, not bare upper bounds

A mouse's latency-to-threshold is reported as a bracket, not a single number, whenever an earlier measurement exists: `last_known_below_100_date`/`min_latency_days` (the most recent prior reading still below threshold — this can be an explicit 0 mm3, i.e. "checked, not yet palpable") alongside `first_ge_100_date`/the point-estimate `latency_days` (log-linear interpolated when possible). Only an animal with no earlier measurement at all is reported as `left-censored`.
