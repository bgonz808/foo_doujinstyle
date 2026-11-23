"""Guard AST inventory against accidental counting of development-only Python."""
import unittest
from pathlib import Path
from native.pack_search_worker import SOURCES, worker_sources
from tools.quality.runtime_python_graph import inspect

ROOT = Path(__file__).resolve().parents[1]


class TestRuntimePythonAst(unittest.TestCase):
    def test_matches_packaged_bytes(self):
        data = inspect(ROOT)
        _, original = worker_sources(ROOT)
        self.assertEqual(data["packaged_python_lines"], original["python_lines"])
        self.assertEqual(data["packaged_python_files"], 5)
        self.assertEqual(sum(data["category_lines"].values()) +
                         data["entry_python_lines"], data["packaged_python_lines"])
        self.assertEqual(len(data["modules"]), len(SOURCES))

    def test_excludes_source_only_functions(self):
        data = inspect(ROOT)
        blocks = data["modules"]["native.search_worker"]["blocks"]
        names = {block["name"] for block in blocks}
        self.assertNotIn("compatibility_schedule_index", names)
        self.assertNotIn("compatibility_run_search", names)
        self.assertNotIn("compatibility_run_idle_layers", names)
        self.assertIn("run_search", names)
        self.assertIn("schedule_index", names)

    def test_is_deterministic(self):
        self.assertEqual(inspect(ROOT), inspect(ROOT))


if __name__ == "__main__":
    unittest.main()
