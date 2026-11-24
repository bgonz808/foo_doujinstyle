"""Bounded independent analysis retains strict coverage and failure semantics."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "native"))
from analyze_native import NATIVE, run_analysis, write_database


class AnalyzerSchedulingTests(unittest.TestCase):
    def test_each_unit_analyzed_once_and_any_failure_is_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = [NATIVE / "component.cpp", NATIVE / "search.cpp", NATIVE / "descriptor.cpp"]
            database = write_database(Path(tmp), [(p, ["clang-cl", "/c", p]) for p in files + files[:1]])
            def invoke(argv, **kwargs):
                return subprocess.CompletedProcess(argv, int(argv[-1].endswith("search.cpp")), stdout="diagnostic\n")
            with patch("analyze_native.shutil.which", return_value="clang-tidy"), \
                 patch("analyze_native.os.cpu_count", return_value=4), \
                 patch("analyze_native.subprocess.run", side_effect=invoke) as call:
                with self.assertRaisesRegex(RuntimeError, "search.cpp"):
                    run_analysis(database, "clang-tidy", strict=True, jobs=2)
                self.assertEqual(call.call_count, 3)
                self.assertEqual({x.args[0][-1] for x in call.call_args_list}, {str(p) for p in files})
                for c in call.call_args_list:
                    self.assertIn("--warnings-as-errors=*", c.args[0])
                    self.assertIn(str(database.parent), c.args[0])

    def test_empty_and_invalid_jobs_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = write_database(Path(tmp), [])
            with patch("analyze_native.shutil.which", return_value="clang-tidy"):
                with self.assertRaisesRegex(RuntimeError, "No first-party"):
                    run_analysis(database, "clang-tidy")
            database = write_database(Path(tmp), [(NATIVE / "descriptor.cpp", ["clang++", "-c"])])
            with patch("analyze_native.shutil.which", return_value="clang-tidy"), \
                 patch("analyze_native.subprocess.run") as run:
                with self.assertRaisesRegex(ValueError, "positive"):
                    run_analysis(database, "clang-tidy", jobs=0)
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
