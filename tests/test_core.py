import importlib.util
import unittest
from pathlib import Path

MAIN = Path(__file__).resolve().parents[1] / "main.py"
spec = importlib.util.spec_from_file_location("mkary_main", MAIN)
mkary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mkary)


class CoreTests(unittest.TestCase):
    def test_completed_markers(self):
        self.assertTrue(mkary.is_completed({"completed_at": "2026-01-01"}))
        self.assertTrue(mkary.is_completed({"status": "completed"}))
        self.assertFalse(mkary.is_completed({"status": "in_progress"}))

    def test_list_extraction(self):
        expected = [{"id": 1}]
        self.assertEqual(mkary.as_list({"chapters": expected}, "chapters"), expected)
        self.assertEqual(mkary.as_list({"data": {"lectures": expected}}, "lectures"), expected)

    def test_id_extraction(self):
        self.assertEqual(mkary.id_of({"lecture_id": 90001010}, "id", "lecture_id"), "90001010")
        self.assertIsNone(mkary.id_of({}, "id", "lecture_id"))

    def test_chapter_progress_is_not_assumed_completed(self):
        self.assertFalse(mkary.is_completed({"best_correct_count": 6}))


if __name__ == "__main__":
    unittest.main()
