# PDX Candidate Selection Agent Handoff

## Repository and file references

This is the single orientation file to give to another agent. The agent must be able to access either the local checkout on the work Mac or the public GitHub repository; this file references the scripts rather than embedding them.

- Public repository: <https://github.com/oncoapop/PDX-latency-growth>
- Workflow branch: `feat/latency`
- Work-Mac repository root: wherever this checkout lives locally (see `$REPO_ROOT` below)
- This handoff: `AGENT_HANDOFF.md`
- Python dependencies: `requirements.txt`
- Source-copy tool: `agent_tools/prepare_workspace.py`
- Excel/CSV/Word parser: `agent_tools/parse_inputs.py`
- Candidate-ranking tool: `agent_tools/rank_candidates.py`
- Growth-curve tool: `agent_tools/plot_candidate_growth.py`
- Normalized CSV templates: `agent_tools/templates/`
- Automated tests: `tests/test_agent_workflow.py` and `tests/test_candidate_ranking.py`

The source Excel, Word, CSV, and TSV files are confidential inputs supplied separately. They are not stored in the public repository. Never substitute the synthetic test data for the real source files.

### Locate or obtain the repository

If you already have a local checkout:

```zsh
cd /path/to/your/checkout
REPO_ROOT="$PWD"
```

On another computer or in a clean agent workspace:

```zsh
git clone --branch feat/latency https://github.com/oncoapop/PDX-latency-growth.git
cd PDX-latency-growth
REPO_ROOT="$PWD"
```

All remaining repository paths in this document are relative to `$REPO_ROOT`.

### Prepare Python

From `$REPO_ROOT`:

```zsh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Do not copy credentials, `.my.cnf`, `config.local.yml`, source workbooks, normalized work data, or generated work-data outputs into the public repository.

## Objective

Identify which PDX sample and passage should be expanded next. Treat every `SA ID + passage` combination as a separate biological candidate. Do not pool passages, even when they share an SA ID.

## Safety and provenance

- Work on copied files in a new task directory. Never edit the supplied Excel, CSV, TSV, or Word files.
- Keep work data local. Do not upload source files or normalized data to public repositories or external services.
- Record the source filename and cell, row, paragraph, or table location for every normalized record.
- Do not infer a mouse-to-transplant mapping when no explicit key exists. Report the ambiguity.
- Keep missing, unknown, zero, and not-applicable values distinct.

## Prepare a working directory

From `$REPO_ROOT`, run:

```zsh
.venv/bin/python agent_tools/prepare_workspace.py /path/to/agent-workspace /path/to/source-folder
.venv/bin/python agent_tools/parse_inputs.py /path/to/agent-workspace
```

The first command copies supported files into `input/` and writes a SHA-256 source manifest. The second command extracts populated Excel cells, CSV/TSV cells, Word paragraphs, and Word table cells into `parsed/source_elements.csv` with source locations.

Create `normalized/` in the workspace. Copy the three header templates from `agent_tools/templates/` and populate them from the extracted evidence:

- `growth_measurements.csv`
- `surgeries.csv`
- `atlas_inventory.csv`

Do not change the required column names.

## Normalized data rules

### Growth measurements

One row represents one tumour measurement from one mouse or independently tracked tumour.

Required fields:

- `sa_id`
- `passage`
- `mouse_id`
- `surgery_date`
- `measurement_date`
- `tumour_volume_mm3`

If L, N, R, B, or another label identifies the independently tracked mouse/tumour, include it in `mouse_id`, for example `251220(1) R`. Keep parenthesized cages distinct.

### Surgeries

One row represents one independent implant attempt. Use the source system's stable surgery/transplant ID. If one surgical event contains multiple independently assessed implant sites, record each attempt separately with a stable site suffix so the denominator is the number of attempts, not merely the number of operating-room events.

Required fields:

- `sa_id`
- `passage`
- `surgery_id`
- `tumour_taken`

Normalize confirmed outcomes to `1` or `0`. Leave genuinely unknown outcomes blank. Do not convert unknown outcomes to failure.

### ATLAS inventory

One row represents one inventory item at a recorded ATLAS location.

Required fields:

- `sa_id`
- `passage`
- `atlas_location`
- `material_label`

The transplantable-material gate passes only when the passage-matched material label is exactly `VF` and a location is present. Do not treat another material type as equivalent to VF.

## Calculations

Run:

```zsh
.venv/bin/python agent_tools/rank_candidates.py /path/to/agent-workspace
.venv/bin/python agent_tools/plot_candidate_growth.py /path/to/agent-workspace
```

The script evaluates each `SA ID + passage` separately.

1. **Latency to 100 mm3**: days from surgery to the first 100 mm3 crossing. When two positive measurements bracket 100 mm3, estimate the crossing with log-linear interpolation. When the measurement immediately before the first >=100 mm3 reading is 0 or otherwise non-interpolable (e.g. a recorded "not yet palpable" check), report that prior date as `last_known_below_100_date` and the first >=100 mm3 date as the bracket's two ends, rather than treating the later date as a free-standing point estimate. Only when no earlier measurement exists at all is latency genuinely open-ended back to surgery - mark that case left-censored.
2. **Take rate**: confirmed tumour takes divided by all surgeries. Also report the rate among surgeries with known outcomes and the number of unknown outcomes.
3. **ATLAS feasibility**: require a passage-matched inventory item labelled exactly `VF` with a recorded location.
4. **Growth tightness**: fastest latency to 100 mm3 subtracted from slowest latency to 100 mm3 among successful tumours. Report unavailable when fewer than two tumours have interpretable latency.
5. **Passage identity**: never aggregate metrics across passages.

## Selection logic

ATLAS VF availability is a hard eligibility gate. Among eligible candidates, order by:

1. complete decision data before incomplete data;
2. shortest median latency to 100 mm3;
3. highest take rate across all surgeries;
4. smallest latency spread.

This is an ordered decision rule, not a weighted score. Report the individual ranks and raw metrics. If changing the priority order changes the recommendation, show the alternative rather than implying the choice is certain.

## Required outputs

Review all files under `analysis/`:

- `eligible_candidate_ranking.csv`: passage-specific shortlist and recommendation.
- `candidate_metrics.csv`: all candidates, including ineligible ones.
- `mouse_latency_to_100.csv`: mouse-level threshold calculations and censoring status.
- `data_quality_issues.csv`: missing or ambiguous evidence that limits interpretation.
- `eligible_candidate_growth_curves.pdf`: one independently scaled page per eligible `SA ID + passage`.
- `growth_curves/*.png`: the same candidate plots as individual image files.

Review the generated growth curves for the eligible candidates. Each page shows individual mice, marks 100 mm3, and includes mice that failed to reach 100 mm3. Do not infer consistency from the summary table without checking these traces.

In the written conclusion, state:

- the recommended SA ID and passage;
- its median latency, take rate, ATLAS VF location, and growth spread;
- the number of surgeries and successful tumours supporting the result;
- censoring, missing outcomes, ambiguous joins, and small sample sizes;
- which candidate would be preferred if take rate were prioritized above latency.
