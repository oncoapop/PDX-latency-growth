import tempfile
import unittest
from pathlib import Path

from agent_tools.file_cache import get_or_compute, load_cache, save_cache, write_if_changed


class FileCacheTests(unittest.TestCase):
    def test_second_call_is_a_cache_hit_when_file_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.txt"
            path.write_text("hello")
            cache: dict = {}
            calls = []

            def compute():
                calls.append(1)
                return "computed"

            value1, hit1 = get_or_compute(cache, path, compute)
            value2, hit2 = get_or_compute(cache, path, compute)

            self.assertEqual(value1, "computed")
            self.assertEqual(value2, "computed")
            self.assertFalse(hit1)
            self.assertTrue(hit2)
            self.assertEqual(len(calls), 1)

    def test_changed_file_is_recomputed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.txt"
            path.write_text("hello")
            cache: dict = {}
            get_or_compute(cache, path, lambda: "first")

            path.write_text("hello again, longer")  # different size -> different fingerprint
            value, hit = get_or_compute(cache, path, lambda: "second")

            self.assertEqual(value, "second")
            self.assertFalse(hit)

    def test_cache_survives_a_save_and_load_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.txt"
            path.write_text("hello")
            cache_path = Path(tmp) / "cache.json"

            cache = load_cache(cache_path)
            value, hit = get_or_compute(cache, path, lambda: ["some", "rows"])
            self.assertFalse(hit)
            save_cache(cache_path, cache)

            reloaded = load_cache(cache_path)
            value2, hit2 = get_or_compute(reloaded, path, lambda: ["should", "not", "run"])
            self.assertTrue(hit2)
            self.assertEqual(value2, ["some", "rows"])

    def test_missing_cache_file_loads_as_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_cache(Path(tmp) / "does_not_exist.json"), {})

    def test_write_if_changed_leaves_mtime_alone_when_content_is_identical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.csv"
            self.assertTrue(write_if_changed(path, "a,b\n1,2\n"))
            original_mtime = path.stat().st_mtime

            wrote_again = write_if_changed(path, "a,b\n1,2\n")

            self.assertFalse(wrote_again)
            self.assertEqual(path.stat().st_mtime, original_mtime)

    def test_write_if_changed_rewrites_when_content_differs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.csv"
            write_if_changed(path, "a,b\n1,2\n")
            wrote = write_if_changed(path, "a,b\n1,3\n")
            self.assertTrue(wrote)
            self.assertEqual(path.read_text(), "a,b\n1,3\n")


if __name__ == "__main__":
    unittest.main()
