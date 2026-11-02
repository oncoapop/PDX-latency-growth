import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_tools import atlas_client
from agent_tools.atlas_client import AnimalAtlasRecord, build_animal_crosswalk, fetch_animal_crosswalk_cached


def _record(cage_id: str, ep: str, barcode: int, patient_id: str = "ZZTEST001", passage=6) -> dict:
    return {
        "cage_id": cage_id,
        "ep": ep,
        "barcode": barcode,
        "transplant_id": f"X9999-{barcode}",
        "patient_id": patient_id,
        "patient_cid": "CIDTEST0001",
        "passage": passage,
        "surgery_date": "2026-08-22",
        "grown": None,
        "necropsy": None,
    }


class BuildAnimalCrosswalkTests(unittest.TestCase):
    def test_keys_by_cage_and_ep_matching_our_site_label(self) -> None:
        records = [_record("990101", "R", 90001), _record("990101", "L", 90002), _record("990101", "B", 90003)]
        crosswalk = build_animal_crosswalk(records)
        self.assertEqual(set(crosswalk), {"990101 R", "990101 L", "990101 B"})
        self.assertEqual(crosswalk["990101 R"].patient_id, "ZZTEST001")
        self.assertEqual(crosswalk["990101 R"].passage, "6")
        self.assertEqual(crosswalk["990101 R"].status, "matched")

    def test_duplicate_cage_ep_pair_is_reported_conflicting_not_guessed(self) -> None:
        records = [_record("990101", "R", 90001), _record("990101", "R", 90099)]
        crosswalk = build_animal_crosswalk(records)
        self.assertNotEqual(crosswalk["990101 R"].status, "matched")
        self.assertIn("conflicting", crosswalk["990101 R"].status)

    def test_records_missing_cage_or_ep_are_skipped(self) -> None:
        records = [{"cage_id": "", "ep": "R", "barcode": 1}, {"cage_id": "990101", "ep": "", "barcode": 2}]
        crosswalk = build_animal_crosswalk(records)
        self.assertEqual(crosswalk, {})


class FetchAnimalCrosswalkCachedTests(unittest.TestCase):
    def test_fresh_fetch_is_cached_and_then_reused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "atlas_animal_crosswalk_cache.json"
            fetch = mock.Mock(
                return_value=(
                    {"990101 R": AnimalAtlasRecord("ZZTEST001", "CIDTEST0001", "6", "X9999-90001", "90001", "2026-08-22", None, None, "matched")},
                    "Fetched 1 Atlas transplant record(s) for the per-animal cross-check.",
                )
            )
            with mock.patch.object(atlas_client, "fetch_animal_crosswalk", fetch):
                crosswalk1, status1 = fetch_animal_crosswalk_cached(cache_path, max_age_hours=24)
                crosswalk2, status2 = fetch_animal_crosswalk_cached(cache_path, max_age_hours=24)

            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(crosswalk1, crosswalk2)
            self.assertIn("Cached for reuse", status1)
            self.assertIn("Using cached Atlas animal cross-check", status2)


if __name__ == "__main__":
    unittest.main()
