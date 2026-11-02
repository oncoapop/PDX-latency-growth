import tempfile
import unittest
from pathlib import Path

import pandas as pd
from openpyxl import Workbook

from agent_tools.atlas_client import AnimalAtlasRecord, CageSurgeryDate
from agent_tools.build_growth_measurements_from_raw import (
    build_normalized_rows,
    build_patient_id_to_sa_id_crosswalk,
    find_workbooks,
    find_year_dirs,
    read_all_raw_rows,
    resolve_surgery_date,
)


def _atlas_record(patient_id: str = "VBA0001", passage: str | None = "6", status: str = "matched") -> AnimalAtlasRecord:
    return AnimalAtlasRecord(
        patient_id=patient_id,
        patient_cid="CID0000001",
        passage=passage,
        transplant_id="X0001-11111",
        barcode="1234",
        surgery_date=None,
        grown=None,
        necropsy=None,
        status=status,
    )


def _write_week(path: Path, rows: list[list]) -> None:
    """rows: list of [mouse_id_or_None, site, length, width, volume, weight, pct, comments]"""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Xenograft"
    sheet.append(["Tumor Volumes"])
    sheet.append([])
    sheet.append(["Mouse ID", None, "Measurements", None, "Volume (mm3)", "Weight (g)", "% Tumor Weight", "Comments"])
    sheet.append([None, None, "length (mm)", "width (mm)"])
    for row in rows:
        sheet.append(row)
    workbook.save(path)


class BuildGrowthMeasurementsTests(unittest.TestCase):
    def test_find_year_dirs_only_matches_four_digit_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "2024").mkdir()
            (root / "2025").mkdir()
            (root / "Drug Studies").mkdir()
            (root / "Pilot Study 2026").mkdir()
            names = {p.name for p in find_year_dirs(root)}
            self.assertEqual(names, {"2024", "2025"})

    def test_find_workbooks_matches_both_filename_conventions(self) -> None:
        # Confirmed on the real data: 2017-2023 (and part of 2024) used
        # "Tumor Volumes (...)" with a space before the parenthesis; 2024
        # onward dropped it. Matching only one pattern silently skipped
        # every workbook using the other convention.
        with tempfile.TemporaryDirectory() as tmp:
            year_dir = Path(tmp) / "2017"
            (year_dir / "1. Jan").mkdir(parents=True)
            (year_dir / "1. Jan" / "Tumor Volumes (2017.01.19)-SL.xlsx").write_text("x")
            (year_dir / "1. Jan" / "Tumor Volumes(2017.01.23)SL.xlsx").write_text("x")
            (year_dir / "1. Jan" / "~$Tumor Volumes (2017.01.19)-SL.xlsx").write_text("x")
            found = {p.name for p in find_workbooks(year_dir)}
            # The glob patterns anchor at the start of the filename, so an
            # Excel lock file (~$...) is naturally excluded without needing
            # a separate filter.
            self.assertEqual(
                found,
                {"Tumor Volumes (2017.01.19)-SL.xlsx", "Tumor Volumes(2017.01.23)SL.xlsx"},
            )

    def test_converter_resolves_sa_id_and_passage_across_weeks_and_flags_unmapped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            year_dir = root / "2026" / "7. July"
            year_dir.mkdir(parents=True)

            # Week 1 (day 5 post-surgery, cage 260715(1) -> surgery 2026-07-15):
            # SA ID noted on the cage-ID row; the passage is noted on a later
            # row within the same cage block whose Mouse ID column reads
            # "passage 3" instead of repeating the cage ID (the real-data
            # pattern: that row still carries its own site + volume).
            _write_week(
                year_dir / "Tumor Volumes(2026.07.20)TA.xlsx",
                [
                    ["260715(1)", "R", 5, 4, 50, 20.0, 1.0, "SA501 Breast"],
                    ["260715(1)", "N", 6, 5, 40, 19.5, 1.0, ""],
                    ["passage 3", "R", 7, 6, 52, 20.0, 1.0, ""],
                ],
            )
            # Week 2 (day 12 post-surgery): Comments blank this week; SA ID/
            # passage must still resolve from week 1's note. Volume crosses
            # the 100 mm3 threshold.
            _write_week(
                year_dir / "Tumor Volumes(2026.07.27)TA.xlsx",
                [
                    ["260715(1)", "R", 9, 8, 150, 21.0, 1.1, ""],
                    ["260715(1)", "N", 8, 7, 60, 20.0, 1.0, ""],
                ],
            )
            # An unrelated mouse with no SA model ever mentioned -> unmapped.
            _write_week(
                year_dir / "Tumor Volumes(2026.07.13)TA.xlsx",
                [["260706", "B", 3, 2, 30, 18.0, 1.0, ""]],
            )

            raw_rows = read_all_raw_rows(root)
            normalized, unmapped = build_normalized_rows(raw_rows)

            self.assertIn("260715(1) R", {row["mouse_id"] for row in normalized})
            row = next(r for r in normalized if r["mouse_id"] == "260715(1) R" and r["measurement_date"] == "2026-07-27")
            self.assertEqual(row["sa_id"], "SA501")
            self.assertEqual(row["passage"], "3")
            self.assertEqual(row["surgery_date"], "2026-07-15")
            self.assertEqual(row["tumour_volume_mm3"], 150.0)

            # Weight / % Tumor Weight columns must never appear in the
            # normalized schema.
            self.assertNotIn("weight", {key.lower() for key in row})

            unmapped_mice = {r["mouse_id"] for r in unmapped}
            self.assertIn("260706 B", unmapped_mice)

    def test_resolve_surgery_date_prefers_unambiguous_atlas_match(self) -> None:
        crosswalk = {"260715(1)": CageSurgeryDate(surgery_date="2026-07-16", status="matched")}
        date, source = resolve_surgery_date("260715(1)", crosswalk)
        self.assertEqual(date, pd.Timestamp("2026-07-16"))
        self.assertEqual(source, "atlas (heuristic disagreed: 2026-07-15)")

    def test_resolve_surgery_date_falls_back_to_heuristic_on_atlas_conflict(self) -> None:
        crosswalk = {
            "260715(1)": CageSurgeryDate(
                surgery_date=None, status="conflicting dates in Atlas: 2026-07-15; 2026-07-16"
            )
        }
        date, source = resolve_surgery_date("260715(1)", crosswalk)
        self.assertEqual(date, pd.Timestamp("2026-07-15"))
        self.assertIn("atlas: conflicting dates", source)

    def test_resolve_surgery_date_uses_heuristic_when_atlas_has_no_entry(self) -> None:
        date, source = resolve_surgery_date("260715(1)", {})
        self.assertEqual(date, pd.Timestamp("2026-07-15"))
        self.assertEqual(source, "cage_id_heuristic")

    def test_resolve_surgery_date_handles_full_yyyymmdd_cage_ids(self) -> None:
        date, source = resolve_surgery_date("DY20230102", {})
        self.assertEqual(date, pd.Timestamp("2023-01-02"))
        self.assertEqual(source, "cage_id_heuristic")

    def test_resolve_surgery_date_unresolved_when_nothing_matches(self) -> None:
        date, source = resolve_surgery_date("Ciara", {})
        self.assertIsNone(date)
        self.assertEqual(source, "unresolved")

    def test_days_to_threshold_from_normalized_output_matches_rank_candidates(self) -> None:
        from agent_tools.rank_candidates import add_key, build_mouse_latency

        rows = [
            {
                "sa_id": "SA501",
                "passage": "3",
                "mouse_id": "260715(1) R",
                "surgery_date": "2026-07-15",
                "measurement_date": "2026-07-20",
                "tumour_volume_mm3": 50.0,
            },
            {
                "sa_id": "SA501",
                "passage": "3",
                "mouse_id": "260715(1) R",
                "surgery_date": "2026-07-15",
                "measurement_date": "2026-07-27",
                "tumour_volume_mm3": 150.0,
            },
        ]
        measurements = add_key(pd.DataFrame(rows))
        measurements["surgery_date"] = pd.to_datetime(measurements["surgery_date"])
        measurements["measurement_date"] = pd.to_datetime(measurements["measurement_date"])
        latency = build_mouse_latency(measurements)
        self.assertEqual(latency.loc[0, "status"], "log-linear interpolated crossing")
        self.assertFalse(latency.loc[0, "latency_left_censored"])
        self.assertTrue(0 < latency.loc[0, "interpolated_latency_days"] < 12)


def _raw_row(mouse_id: str, tumour_model: str = "", row_note: str = "", volume: float = 50.0) -> dict:
    cage, _, site = mouse_id.partition(" ")
    return {
        "date": "2026-07-20",
        "source_file": "Tumor Volumes(2026.07.20)TA.xlsx",
        "sheet": "Xenograft",
        "excel_row": 10,
        "mouse_id": cage,
        "cage": cage,
        "tumour_model": tumour_model,
        "tumour_site": site,
        "tumour_volume_mm3": volume,
        "row_note": row_note,
    }


class AtlasEnrichmentTests(unittest.TestCase):
    def test_patient_id_crosswalk_recovers_sa_id_when_unambiguous(self) -> None:
        mouse_attrs = {
            "260715(1) R": {"sa_id": ["SA501"], "passage": []},
            "260715(1) L": {"sa_id": [], "passage": []},  # Comments never named a model
        }
        animal_crosswalk = {
            "260715(1) R": _atlas_record(patient_id="ZZTEST001"),
            "260715(1) L": _atlas_record(patient_id="ZZTEST001"),
        }
        crosswalk = build_patient_id_to_sa_id_crosswalk(mouse_attrs, animal_crosswalk)
        self.assertEqual(crosswalk, {"ZZTEST001": "SA501"})

    def test_patient_id_crosswalk_excludes_ambiguous_mappings(self) -> None:
        mouse_attrs = {
            "a R": {"sa_id": ["SA501"], "passage": []},
            "b R": {"sa_id": ["SA609"], "passage": []},  # same patient_id, different scraped SA ID
        }
        animal_crosswalk = {
            "a R": _atlas_record(patient_id="ZZTEST001"),
            "b R": _atlas_record(patient_id="ZZTEST001"),
        }
        crosswalk = build_patient_id_to_sa_id_crosswalk(mouse_attrs, animal_crosswalk)
        self.assertEqual(crosswalk, {})

    def test_build_normalized_rows_recovers_sa_id_via_atlas_and_prefers_atlas_passage(self) -> None:
        raw_rows = [
            _raw_row("260715(1) R", tumour_model="SA501 Breast"),
            _raw_row("260715(1) L"),  # no Comments text at all for this animal
        ]
        animal_crosswalk = {
            "260715(1) R": _atlas_record(patient_id="ZZTEST001", passage="11"),
            "260715(1) L": _atlas_record(patient_id="ZZTEST001", passage="11"),
        }
        normalized, unmapped = build_normalized_rows(raw_rows, animal_crosswalk=animal_crosswalk)

        self.assertEqual(len(unmapped), 0)
        by_mouse = {r["mouse_id"]: r for r in normalized}
        self.assertEqual(by_mouse["260715(1) R"]["sa_id"], "SA501")
        self.assertEqual(by_mouse["260715(1) R"]["sa_id_source"], "comments")
        self.assertEqual(by_mouse["260715(1) R"]["passage"], "11")
        self.assertEqual(by_mouse["260715(1) R"]["passage_source"], "atlas")
        # L had no Comments SA ID at all - recovered via the empirical crosswalk
        self.assertEqual(by_mouse["260715(1) L"]["sa_id"], "SA501")
        self.assertEqual(by_mouse["260715(1) L"]["sa_id_source"], "atlas_patient_id_crosswalk")
        self.assertEqual(by_mouse["260715(1) L"]["atlas_patient_id"], "ZZTEST001")

    def test_build_normalized_rows_falls_back_to_atlas_patient_id_when_never_linked_to_an_sa_id(self) -> None:
        # This patient_id is never seen paired with a scraped SA ID anywhere
        # in the dataset, so the empirical crosswalk has nothing for it -
        # Atlas's own patient_id should still be used rather than leaving
        # sa_id blank/unmapped.
        raw_rows = [_raw_row("260715(1) R")]
        animal_crosswalk = {"260715(1) R": _atlas_record(patient_id="VBA9999", passage="3")}
        normalized, unmapped = build_normalized_rows(raw_rows, animal_crosswalk=animal_crosswalk)
        self.assertEqual(len(unmapped), 0)
        row = normalized[0]
        self.assertEqual(row["sa_id"], "VBA9999")
        self.assertEqual(row["sa_id_source"], "atlas_patient_id_direct")
        self.assertEqual(row["passage"], "3")

    def test_build_normalized_rows_still_unmapped_without_atlas_or_comments(self) -> None:
        raw_rows = [_raw_row("999999 R")]
        normalized, unmapped = build_normalized_rows(raw_rows)
        self.assertEqual(len(normalized), 0)
        self.assertEqual(len(unmapped), 1)
        self.assertIn("no SA ID found", unmapped[0]["reason"])


if __name__ == "__main__":
    unittest.main()
