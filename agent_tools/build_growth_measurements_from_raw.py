#!/usr/bin/env python3
"""Build normalized growth_measurements.csv (+ surgeries/atlas placeholders)
from raw cage-based "Tumor Volumes(YYYY.MM.DD)*.xlsx" workbooks.

This reuses the raw-workbook extraction already proven in
plot_tumour_growth_by_cage.py (header detection, mouse/cage carry-forward,
transplant-date parsing) rather than re-implementing it, and adds the two
pieces of structure the agent_tools/rank_candidates.py pipeline needs on top:
SA ID (split out of the extracted tumour model) and passage number (parsed
from "passage N" note rows). Only tumour volume is read; the raw workbooks'
Weight (g) / % Tumor Weight columns are never touched, because
plot_tumour_growth_by_cage.py's extract_sheet() does not extract them.

Only year-named (YYYY) subfolders of the source root are scanned. Other
sub-collections (Drug Studies, Metastasis cohort, Pilot Study, etc.) use
different filename/sheet conventions and are intentionally out of scope for
this converter; see AGENT_HANDOFF.md for the full workflow.

Surgery date resolution: the cage-ID YYMMDD/YYYYMMDD heuristic
(parse_surgery_date_from_cage) is cross-checked against Atlas (the lab's
Transplants table, agent_tools/atlas_client.py) whenever a token is
available at ~/.atlas. Atlas wins when it has one unambiguous surgery_date
for a cage; a disagreement or an Atlas-side conflict is recorded in the
surgery_date_source column rather than silently resolved. Pass --no-atlas to
use the heuristic only (e.g. when offline).

SA ID / passage resolution: Atlas's `ep` field (ear position: R/L/B/N)
matches our own per-animal site label, so Atlas's Transplants table is keyed
at exactly the same (cage, site) granularity as our mouse_id. Atlas's own
`passage` field is used directly when available (it is present far more
often than a "passage N" note in the workbook Comments). Atlas's `patient_id`
is a different naming scheme from the lab's informal "SA###" labels (no
textual overlap - confirmed separately). sa_id is resolved in three tiers:
(1) a single SA### named in this animal's own Comments cells; (2) failing
that, an empirical patient_id -> SA### crosswalk built from other animals
where both are independently known, used only when unambiguous across every
animal sharing that patient_id; (3) failing that too (an Atlas-matched
animal whose patient_id was never seen paired with an SA### anywhere in the
dataset), Atlas's own patient_id is used directly as sa_id - not the lab's
usual naming, but still the authoritative, unique sample identity, so a
report isn't left with a blank sample ID when Atlas already knows exactly
which animal this is. See sa_id_source / passage_source columns for
provenance on every row.

Checkpointing: a per-workspace cache under <workspace>/.cache/ makes a
routine re-run fast — raw_rows_cache_v2.json skips re-parsing any workbook
whose (mtime, size) hasn't changed since last time, and
atlas_crosswalk_cache.json reuses the last Atlas pull for up to
--atlas-max-age-hours (default 24h) instead of re-fetching. Pass --no-cache
or --refresh-atlas to bypass either cache, or delete <workspace>/.cache/ to
reset both. See also agent_tools/run_latency_pipeline.py, which chains this
script with the rest of the pipeline and skips whole downstream stages that
are already up to date.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plot_tumour_growth_by_cage import (  # noqa: E402
    extract_sheet,
    parse_date_from_filename,
)
from agent_tools.atlas_client import (  # noqa: E402
    AnimalAtlasRecord,
    CageSurgeryDate,
    fetch_animal_crosswalk_cached,
    fetch_cage_crosswalk_cached,
)
from agent_tools.file_cache import get_or_compute, load_cache, save_cache, write_if_changed  # noqa: E402

YEAR_DIR_RE = re.compile(r"^\d{4}$")
PASSAGE_NUM_RE = re.compile(r"passage\s*(\d+)", re.IGNORECASE)
SA_ID_RE = re.compile(r"^(SA\d+[A-Z]?)", re.IGNORECASE)

# Cage IDs encode the surgery date as a standalone digit run, either 8-digit
# YYYYMMDD (e.g. "DY20230102") or 6-digit YYMMDD (e.g. "260715(1)" ->
# 2026-07-15, "O251010" -> 2025-10-10). Lookaround asserts the run is not
# part of a longer digit sequence, so an 8-digit run is never mistaken for a
# 6-digit one (and vice versa) the way a plain r"(\d{6})" search would do
# (plot_tumour_growth_by_cage.py's parse_transplant_date() uses exactly that
# looser pattern; it is not reused here because it mis-parses 8-digit IDs).
FULL_DATE_RE = re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)")
SHORT_DATE_RE = re.compile(r"(?<!\d)(\d{2})(\d{2})(\d{2})(?!\d)")


def parse_surgery_date_from_cage(cage: Any) -> pd.Timestamp | None:
    """Infer the surgery date from a cage ID, preferring a full YYYYMMDD
    digit run over a 6-digit YYMMDD one when both could apply, and
    validating month/day so an implausible match (e.g. month 23) is
    rejected rather than silently kept."""
    text = str(cage).strip()
    full_match = FULL_DATE_RE.search(text)
    if full_match:
        year, month, day = (int(part) for part in full_match.groups())
        try:
            return pd.Timestamp(year, month, day)
        except ValueError:
            pass
    short_match = SHORT_DATE_RE.search(text)
    if short_match:
        yy, month, day = (int(part) for part in short_match.groups())
        try:
            return pd.Timestamp(2000 + yy, month, day)
        except ValueError:
            pass
    return None

GROWTH_COLUMNS = [
    "sa_id",
    "passage",
    "mouse_id",
    "surgery_date",
    "measurement_date",
    "tumour_volume_mm3",
    "source_file",
    "source_location",
    "surgery_date_source",
    "sa_id_source",
    "passage_source",
    "atlas_patient_id",
    "atlas_transplant_id",
    "atlas_barcode",
]


def resolve_surgery_date(
    cage: str, atlas_crosswalk: dict[str, CageSurgeryDate]
) -> tuple[pd.Timestamp | None, str]:
    """Atlas (the Transplants table) is authoritative when it has a single,
    unambiguous surgery_date for this cage; otherwise fall back to the
    cage-ID YYMMDD heuristic. Returns (date_or_None, source_label) — the
    label always says which one was used, and calls out an Atlas/heuristic
    disagreement rather than silently picking a side."""
    heuristic_date = parse_surgery_date_from_cage(cage)
    entry = atlas_crosswalk.get(cage)

    if entry is not None and entry.surgery_date:
        atlas_date = pd.Timestamp(entry.surgery_date)
        if heuristic_date is not None and heuristic_date != atlas_date:
            return atlas_date, f"atlas (heuristic disagreed: {heuristic_date.date().isoformat()})"
        return atlas_date, "atlas"

    if entry is not None and entry.status != "matched":
        # Atlas has this cage but can't give one clean date for it (either no
        # surgery_date on any record, or conflicting dates across records).
        if heuristic_date is not None:
            return heuristic_date, f"cage_id_heuristic (atlas: {entry.status})"
        return None, f"unresolved (atlas: {entry.status})"

    if heuristic_date is not None:
        return heuristic_date, "cage_id_heuristic"
    return None, "unresolved"


def split_sa_id(tumour_model: str) -> str:
    match = SA_ID_RE.match(tumour_model.strip())
    return match.group(1).upper() if match else ""


def extract_passage(row_note: str) -> str:
    match = PASSAGE_NUM_RE.search(row_note)
    return match.group(1) if match else ""


def find_year_dirs(source_root: Path) -> list[Path]:
    return sorted(p for p in source_root.iterdir() if p.is_dir() and YEAR_DIR_RE.match(p.name))


def read_workbook_rows(path: Path, include_zero: bool = True) -> list[dict[str, Any]]:
    """include_zero=True keeps explicit "0 mm3" rows (a tumour checked and
    found not yet palpable/measurable), not just a filtering nicety: a 0
    immediately before the first >=100mm3 reading is what lets
    days_to_threshold() in rank_candidates.py report a tight
    [last_known_below_100_date, first_ge_100_date] bracket instead of an
    open-ended "left-censored" upper bound back to surgery."""
    date = parse_date_from_filename(path)
    if date is None:
        print(f"Skipping {path.name}: no YYYY.MM.DD date in filename")
        return []
    rows: list[dict[str, Any]] = []
    for sheet_name in pd.ExcelFile(path).sheet_names:
        rows.extend(extract_sheet(path, sheet_name, date, include_zero))
    return rows


def read_all_raw_rows(source_root: Path, cache_path: Path | None = None) -> list[dict[str, Any]]:
    """Extract every row from every Tumor Volumes(*.xlsx workbook under the
    year folders. When cache_path is given, a workbook already in the cache
    with an unchanged (mtime, size) is reused instead of re-parsed — this is
    what makes a routine re-run (a handful of new weekly files added since
    last time) fast instead of re-reading a decade of workbooks every time."""
    cache = load_cache(cache_path) if cache_path else {}
    raw_rows: list[dict[str, Any]] = []
    hits = misses = 0
    for year_dir in find_year_dirs(source_root):
        for path in sorted(year_dir.rglob("Tumor Volumes(*.xlsx")):
            if path.name.startswith("~$"):
                continue
            if cache_path:
                rows, hit = get_or_compute(cache, path, lambda p=path: read_workbook_rows(p))
                hits += int(hit)
                misses += int(not hit)
            else:
                rows = read_workbook_rows(path)
            raw_rows.extend(rows)
    if cache_path:
        save_cache(cache_path, cache)
        print(f"Workbook parse cache: {hits} file(s) reused, {misses} file(s) (re)parsed ({cache_path})")
    return raw_rows


def animal_id(row: dict[str, Any]) -> str:
    """The per-animal identity for the normalized schema: cage plus
    ear-notch/site label, e.g. "251220(1) R" (AGENT_HANDOFF.md convention).
    extract_sheet()'s own "mouse_id" field is the cage-level id shared by
    every ear-notched sub-mouse in that cage, so site must be appended here."""
    return f"{row['cage']} {row['tumour_site']}".strip()


def aggregate_mouse_attributes(raw_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """For each per-animal id, collect every distinct non-blank SA ID and
    passage observed across all of that animal's rows (across all weekly
    files), since the Comments/passage note is often written only once and
    not repeated on later measurement dates. Conflicting values are
    reported, not guessed."""
    by_animal: dict[str, dict[str, set[str]]] = {}
    for row in raw_rows:
        attrs = by_animal.setdefault(animal_id(row), {"sa_id": set(), "passage": set()})
        sa_id = split_sa_id(row["tumour_model"])
        if sa_id:
            attrs["sa_id"].add(sa_id)
        passage = extract_passage(row["row_note"])
        if passage:
            attrs["passage"].add(passage)

    resolved: dict[str, dict[str, Any]] = {}
    for animal, attrs in by_animal.items():
        resolved[animal] = {
            "sa_id": sorted(attrs["sa_id"]),
            "passage": sorted(attrs["passage"]),
        }
    return resolved


def build_patient_id_to_sa_id_crosswalk(
    mouse_attrs: dict[str, dict[str, Any]],
    animal_crosswalk: dict[str, AnimalAtlasRecord],
) -> dict[str, str]:
    """Empirically derive an Atlas patient_id -> SA ID mapping from animals
    where both are independently known (a single scraped SA ID from this
    run's own Comments text, and a matched Atlas record for that same
    cage+site). A patient_id observed with more than one distinct SA ID is
    excluded entirely rather than guessed."""
    observed: dict[str, set[str]] = {}
    for animal, attrs in mouse_attrs.items():
        sa_ids = attrs["sa_id"]
        if len(sa_ids) != 1:
            continue
        entry = animal_crosswalk.get(animal)
        if entry is None or entry.status != "matched" or not entry.patient_id:
            continue
        observed.setdefault(entry.patient_id, set()).add(sa_ids[0])
    return {patient_id: next(iter(sa_ids)) for patient_id, sa_ids in observed.items() if len(sa_ids) == 1}


def build_normalized_rows(
    raw_rows: list[dict[str, Any]],
    atlas_crosswalk: dict[str, CageSurgeryDate] | None = None,
    animal_crosswalk: dict[str, AnimalAtlasRecord] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    atlas_crosswalk = atlas_crosswalk or {}
    animal_crosswalk = animal_crosswalk or {}
    mouse_attrs = aggregate_mouse_attributes(raw_rows)
    patient_id_to_sa_id = build_patient_id_to_sa_id_crosswalk(mouse_attrs, animal_crosswalk)
    surgery_date_cache: dict[str, tuple[pd.Timestamp | None, str]] = {}
    normalized: list[dict[str, Any]] = []
    unmapped: list[dict[str, Any]] = []

    for row in raw_rows:
        mouse_id = animal_id(row)
        attrs = mouse_attrs[mouse_id]
        sa_ids = attrs["sa_id"]
        passages = attrs["passage"]
        cage = row["cage"]
        if cage not in surgery_date_cache:
            surgery_date_cache[cage] = resolve_surgery_date(cage, atlas_crosswalk)
        surgery_date, surgery_date_source = surgery_date_cache[cage]
        source_location = f"{row['sheet']}!row{row['excel_row']}"

        atlas_entry = animal_crosswalk.get(mouse_id)
        atlas_matched = atlas_entry is not None and atlas_entry.status == "matched"
        atlas_patient_id = atlas_entry.patient_id if atlas_matched else ""
        atlas_transplant_id = atlas_entry.transplant_id if atlas_matched else ""
        atlas_barcode = atlas_entry.barcode if atlas_matched else ""

        if len(sa_ids) == 1:
            sa_id, sa_id_source = sa_ids[0], "comments"
        elif atlas_matched and atlas_entry.patient_id in patient_id_to_sa_id:
            sa_id, sa_id_source = patient_id_to_sa_id[atlas_entry.patient_id], "atlas_patient_id_crosswalk"
        elif atlas_matched and atlas_entry.patient_id:
            # No workbook ever labelled this patient with an "SA###" name we
            # could borrow (the empirical crosswalk above has nothing for
            # it) - fall back to Atlas's own patient_id directly. It isn't
            # in the lab's informal "SA###" convention, but it is still the
            # authoritative, unique sample identity for this animal, so this
            # is reportable identity, not a guess.
            sa_id, sa_id_source = atlas_entry.patient_id, "atlas_patient_id_direct"
        else:
            sa_id, sa_id_source = "", ""

        passage_conflict = False
        if atlas_matched and atlas_entry.passage:
            passage, passage_source = atlas_entry.passage, "atlas"
        elif len(passages) == 1:
            passage, passage_source = passages[0], "comments"
        else:
            passage, passage_source = "", ""
            passage_conflict = len(passages) > 1

        base_row = {
            "mouse_id": mouse_id,
            "surgery_date": surgery_date.date().isoformat() if surgery_date is not None else "",
            "measurement_date": row["date"],
            "tumour_volume_mm3": row["tumour_volume_mm3"],
            "source_file": row["source_file"],
            "source_location": source_location,
            "surgery_date_source": surgery_date_source,
            "sa_id_source": sa_id_source,
            "passage_source": passage_source,
            "atlas_patient_id": atlas_patient_id,
            "atlas_transplant_id": atlas_transplant_id,
            "atlas_barcode": atlas_barcode,
        }

        reasons = []
        if not sa_id:
            reasons.append("no SA ID found for this mouse" if not sa_ids else f"conflicting SA IDs: {sa_ids}")
        if passage_conflict:
            reasons.append(f"conflicting passage notes: {passages}")
        if surgery_date is None:
            reasons.append(f"unresolvable surgery_date ({surgery_date_source})")

        if reasons:
            unmapped.append(
                {
                    **base_row,
                    "sa_id": sa_id or ";".join(sa_ids),
                    "passage": passage or ";".join(passages),
                    "reason": "; ".join(reasons),
                }
            )
            continue

        normalized.append({"sa_id": sa_id, "passage": passage, **base_row})

    return normalized, unmapped


def write_surgeries_placeholder(normalized: list[dict[str, Any]], path: Path) -> None:
    seen: set[tuple[str, str, str]] = set()
    rows = []
    for row in normalized:
        key = (row["sa_id"], row["passage"], row["mouse_id"])
        if key in seen:
            continue
        seen.add(key)
        rows.append({"sa_id": row["sa_id"], "passage": row["passage"], "surgery_id": row["mouse_id"], "tumour_taken": ""})
    write_if_changed(path, pd.DataFrame(rows, columns=["sa_id", "passage", "surgery_id", "tumour_taken"]).to_csv(index=False))


def write_atlas_placeholder(path: Path) -> None:
    write_if_changed(path, pd.DataFrame(columns=["sa_id", "passage", "atlas_location", "material_label"]).to_csv(index=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path, help="Output workspace directory (created if missing)")
    parser.add_argument("source_root", type=Path, help="Root folder containing year-named (YYYY) subfolders")
    parser.add_argument(
        "--no-atlas",
        action="store_true",
        help="Skip the Atlas Transplants cross-check and use the cage-ID heuristic only",
    )
    parser.add_argument(
        "--refresh-atlas",
        action="store_true",
        help="Ignore the cached Atlas cross-check and fetch fresh, regardless of its age",
    )
    parser.add_argument(
        "--atlas-max-age-hours",
        type=float,
        default=24.0,
        help="Reuse the cached Atlas cross-check if it is younger than this (default: 24)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable the per-workbook parse cache and re-read every file from scratch",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    workspace = args.workspace.expanduser().resolve()
    source_root = args.source_root.expanduser().resolve()
    normalized_dir = workspace / "normalized"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = workspace / ".cache"

    year_dirs = find_year_dirs(source_root)
    if not year_dirs:
        raise SystemExit(f"No year-named (YYYY) subfolders found under {source_root}")
    print(f"Scanning {len(year_dirs)} year folder(s) under {source_root}: {', '.join(p.name for p in year_dirs)}")

    atlas_crosswalk: dict[str, CageSurgeryDate] = {}
    animal_crosswalk: dict[str, AnimalAtlasRecord] = {}
    if args.no_atlas:
        print("Atlas cross-check skipped (--no-atlas); using the cage-ID heuristic only.")
    else:
        atlas_crosswalk, atlas_status = fetch_cage_crosswalk_cached(
            cache_dir / "atlas_crosswalk_cache.json",
            max_age_hours=args.atlas_max_age_hours,
            force_refresh=args.refresh_atlas,
        )
        print(atlas_status)
        animal_crosswalk, animal_status = fetch_animal_crosswalk_cached(
            cache_dir / "atlas_animal_crosswalk_cache.json",
            max_age_hours=args.atlas_max_age_hours,
            force_refresh=args.refresh_atlas,
        )
        print(animal_status)

    # v2: now includes 0 mm3 rows (see read_workbook_rows docstring) - a
    # distinct filename from the old raw_rows_cache.json so a pre-fix cache
    # never gets silently reused with the old (zero-filtered) extraction.
    raw_rows_cache_path = None if args.no_cache else cache_dir / "raw_rows_cache_v2.json"
    raw_rows = read_all_raw_rows(source_root, raw_rows_cache_path)
    print(f"Extracted {len(raw_rows)} raw measurement row(s) from Tumor Volumes(*.xlsx workbooks")

    normalized, unmapped = build_normalized_rows(raw_rows, atlas_crosswalk, animal_crosswalk)
    write_if_changed(normalized_dir / "growth_measurements.csv", pd.DataFrame(normalized, columns=GROWTH_COLUMNS).to_csv(index=False))
    write_if_changed(normalized_dir / "unmapped_rows.csv", pd.DataFrame(unmapped).to_csv(index=False))
    write_surgeries_placeholder(normalized, normalized_dir / "surgeries.csv")
    write_atlas_placeholder(normalized_dir / "atlas_inventory.csv")

    print(f"Wrote {len(normalized)} normalized growth measurement row(s) to {normalized_dir / 'growth_measurements.csv'}")
    print(f"Wrote {len(unmapped)} unmapped/ambiguous row(s) to {normalized_dir / 'unmapped_rows.csv'}")
    print("Wrote placeholder surgeries.csv (tumour_taken left blank/unknown) and empty atlas_inventory.csv")
    source_counts = pd.Series([row["surgery_date_source"] for row in normalized]).value_counts()
    print(f"surgery_date_source breakdown:\n{source_counts.to_string()}")
    sa_id_source_counts = pd.Series([row["sa_id_source"] for row in normalized]).value_counts()
    print(f"sa_id_source breakdown:\n{sa_id_source_counts.to_string()}")
    passage_source_counts = pd.Series([row["passage_source"] or "(none)" for row in normalized]).value_counts()
    print(f"passage_source breakdown:\n{passage_source_counts.to_string()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
