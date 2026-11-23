"""Source-level native foreground cutover guard; not a substitute for host GUI."""
import unittest
from pathlib import Path
from native.pack_search_worker import worker_sources

ROOT = Path(__file__).resolve().parents[1]


class NativeForegroundCutover(unittest.TestCase):
    def test_foobar_query_launches_native_helper(self):
        src = (ROOT / "native/search.cpp").read_text(encoding="utf-8")
        self.assertIn('const bool native_foreground = arguments.find(L" --query=")', src)
        self.assertIn('L" --foreground-search --cache "', src)
        self.assertIn('const bool native = native_idle || native_foreground;', src)
        self.assertIn('const auto launcher = native ? worker : python_launcher();', src)

    def test_bundled_python_cannot_run_foreground_queries(self):
        members, inventory = worker_sources(ROOT)
        payload = members["native/search_worker.py"].decode("utf-8")
        self.assertNotIn("def run_search(", payload)
        self.assertIn("Foreground search is native-only in installed worker", payload)
        self.assertEqual(inventory["fallback_python_lines"], 0)
        self.assertLess(inventory["python_lines"], 1375)


if __name__ == "__main__":
    unittest.main()
