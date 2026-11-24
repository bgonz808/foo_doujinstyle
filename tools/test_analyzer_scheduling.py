"""Bounded independent analysis retains strict coverage and failure semantics."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "native"))
from analyze_native import NATIVE, run_analysis, run_analysis_group, write_database


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

    def test_global_scheduler_preserves_shared_source_context_and_bound(self):
        import threading
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared = NATIVE / "descriptor.cpp"
            dll = write_database(root / "dll", [(shared, ["clang-cl", "/D_DLL", shared]),
                                                 (NATIVE / "search.cpp", ["clang-cl", "/D_DLL"])])
            worker = write_database(root / "worker", [(shared, ["clang-cl", "/D_WORKER", shared]),
                                                      (NATIVE / "publish_references.cpp", ["clang-cl", "/D_WORKER"])])
            lock = threading.Lock()
            active = 0
            peak = 0
            def invoke(argv, **kwargs):
                nonlocal active, peak
                with lock:
                    active += 1
                    peak = max(peak, active)
                try:
                    import time
                    time.sleep(0.01)
                    return subprocess.CompletedProcess(argv, 0, stdout="")
                finally:
                    with lock:
                        active -= 1
            with patch("analyze_native.shutil.which", return_value="clang-tidy"), \
                 patch("analyze_native.os.cpu_count", return_value=4), \
                 patch("analyze_native.subprocess.run", side_effect=invoke) as call:
                result = run_analysis_group([("dll", dll), ("worker", worker)], "clang-tidy",
                                            strict=True, jobs=3)
                self.assertEqual(call.call_count, 4)
                self.assertEqual(result["workers"], 3)
                self.assertLessEqual(peak, 3)
                shared_calls = [c.args[0] for c in call.call_args_list if c.args[0][-1] == str(shared)]
                self.assertEqual(len(shared_calls), 2)
                self.assertEqual({argv[argv.index("-p")+1] for argv in shared_calls},
                                 {str(dll.parent), str(worker.parent)})
                self.assertTrue(all("--warnings-as-errors=*" in c.args[0] for c in call.call_args_list))

    def test_global_scheduler_failure_in_one_context_is_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared = NATIVE / "descriptor.cpp"
            dll = write_database(root / "dll", [(shared, ["clang-cl", "/D_DLL", shared])])
            worker = write_database(root / "worker", [(shared, ["clang-cl", "/D_WORKER", shared])])
            def invoke(argv, **kwargs):
                return subprocess.CompletedProcess(argv, int(str(worker.parent) in argv), stdout="")
            with patch("analyze_native.shutil.which", return_value="clang-tidy"), \
                 patch("analyze_native.subprocess.run", side_effect=invoke) as call:
                with self.assertRaisesRegex(RuntimeError, "worker/descriptor.cpp"):
                    run_analysis_group([("dll", dll), ("worker", worker)], "clang-tidy", strict=True, jobs=2)
                self.assertEqual(call.call_count, 2)

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
