import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_tools import atlas_client
from agent_tools.atlas_client import CageSurgeryDate, fetch_cage_crosswalk_cached


class AtlasCrosswalkCacheTests(unittest.TestCase):
    def test_fresh_fetch_is_cached_and_then_reused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "atlas_crosswalk_cache.json"
            fetch = mock.Mock(
                return_value=(
                    {"260715(1)": CageSurgeryDate(surgery_date="2026-07-15", status="matched")},
                    "Fetched 1 Atlas transplant record(s) for cross-check.",
                )
            )
            with mock.patch.object(atlas_client, "fetch_cage_crosswalk", fetch):
                crosswalk1, status1 = fetch_cage_crosswalk_cached(cache_path, max_age_hours=24)
                crosswalk2, status2 = fetch_cage_crosswalk_cached(cache_path, max_age_hours=24)

            self.assertEqual(fetch.call_count, 1)  # second call was served from cache
            self.assertIn("260715(1)", crosswalk1)
            self.assertEqual(crosswalk1, crosswalk2)
            self.assertIn("Cached for reuse", status1)
            self.assertIn("Using cached Atlas cross-check", status2)

    def test_force_refresh_bypasses_a_fresh_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "atlas_crosswalk_cache.json"
            fetch = mock.Mock(
                return_value=({"c": CageSurgeryDate(surgery_date="2026-01-01", status="matched")}, "ok")
            )
            with mock.patch.object(atlas_client, "fetch_cage_crosswalk", fetch):
                fetch_cage_crosswalk_cached(cache_path, max_age_hours=24)
                fetch_cage_crosswalk_cached(cache_path, max_age_hours=24, force_refresh=True)

            self.assertEqual(fetch.call_count, 2)

    def test_expired_cache_triggers_a_fresh_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "atlas_crosswalk_cache.json"
            fetch = mock.Mock(
                return_value=({"c": CageSurgeryDate(surgery_date="2026-01-01", status="matched")}, "ok")
            )
            with mock.patch.object(atlas_client, "fetch_cage_crosswalk", fetch):
                fetch_cage_crosswalk_cached(cache_path, max_age_hours=0.0)
                fetch_cage_crosswalk_cached(cache_path, max_age_hours=0.0)

            self.assertEqual(fetch.call_count, 2)

    def test_failed_fetch_falls_back_to_stale_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "atlas_crosswalk_cache.json"
            good_fetch = mock.Mock(
                return_value=({"c": CageSurgeryDate(surgery_date="2026-01-01", status="matched")}, "ok")
            )
            with mock.patch.object(atlas_client, "fetch_cage_crosswalk", good_fetch):
                fetch_cage_crosswalk_cached(cache_path, max_age_hours=0.0)

            failing_fetch = mock.Mock(return_value=({}, "Atlas unavailable; skipping Atlas cross-check."))
            with mock.patch.object(atlas_client, "fetch_cage_crosswalk", failing_fetch):
                crosswalk, status = fetch_cage_crosswalk_cached(cache_path, max_age_hours=0.0)

            self.assertIn("c", crosswalk)
            self.assertIn("stale cached Atlas cross-check", status)


if __name__ == "__main__":
    unittest.main()
