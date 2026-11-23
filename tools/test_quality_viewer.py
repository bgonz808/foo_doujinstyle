"""Contract and adversarial tests for self-contained quality HTML viewer."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import xml.etree.ElementTree as ET

from tools.quality.viewer import cases_from_junit, render


class QualityHtmlTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.junit = self.root / "junit"
        self.junit.mkdir()
        root = ET.Element("testsuite")
        ET.SubElement(root, "testcase", classname="pkg.Tests", name="test_success")
        skip = ET.SubElement(root, "testcase", classname="pkg.Tests", name="test_skipped")
        ET.SubElement(skip, "skipped", message="unsupported platform")
        ET.ElementTree(root).write(self.junit / "portable.xml", encoding="utf-8")

        self.checkpoint = {
            "schema_version": 1, "kind": "native-quality-checkpoint",
            "version": "0.0.100", "status": "passed",
            "qualification": "source-tested-not-host-qualified",
            "source_commit": "a" * 40, "run_id": "123",
            "tests": {"total": 2, "passed": 1, "skipped": 1, "failed": 0,
                      "unique_test_ids": 2, "duplicate_executions": 0},
            "native_coverage": {
                "scope": "portable-native-publisher",
                "summary": {
                    "lines": {"count": 10, "covered": 9, "percent": 90.0},
                    "branches": {"count": 8, "covered": 6, "percent": 75.0},
                    "functions": {"count": 4, "covered": 3, "percent": 75.0},
                    "regions": {"count": 12, "covered": 9, "percent": 75.0}
                }, "instrumented_source_files": 1, "profile_count": 2,
                "host_e2e_coverage": None
            },
            "migration": {"packaged_python_lines": 1375, "delta_python_lines": 0},
            "features": [{
                "id": "search", "issue": 1, "migration_state": "python-adapter-active",
                "contracts": {"status": "passed", "matched_cases": 1},
                "host_e2e": {"status": "not_run", "matched_cases": 0}
            }],
            "evidence": {"junit": [{"path": "portable.xml", "sha256": "a" * 64}],
                         "coverage_summary_sha256": "b" * 64}
        }
        self.coverage = {
            "schema_version": 1, "measured": True,
            "files": [{"path": "search.cpp", "coverage": {
                "lines": {"count": 10, "covered": 9, "percent": 90},
                "branches": {"count": 8, "covered": 6, "percent": 75}
            }}]
        }

    def test_self_contained_html_reports_real_scope_and_test_outcomes(self):
        page = render(self.checkpoint, self.coverage, cases_from_junit(self.junit))
        self.assertIn("0.0.100", page)
        self.assertIn("90.0%", page)
        self.assertIn("search.cpp", page)
        self.assertIn("test_skipped", page)
        self.assertIn("not_run", page)
        self.assertIn("source-tested-not-host-qualified", page)
        self.assertIn("Whole-plugin", page)
        self.assertIn("Content-Security-Policy", page)
        self.assertNotIn("<script", page)
        self.assertNotIn("https://", page)

    def test_xss_from_checkpoint_and_junit_is_escaped(self):
        evil = '<img src=x onerror="alert(1)">'
        self.checkpoint["features"][0]["id"] = evil
        root = ET.Element("testsuite")
        ET.SubElement(root, "testcase", classname="pkg", name=evil)
        ET.ElementTree(root).write(self.junit / "portable.xml")
        page = render(self.checkpoint, self.coverage, cases_from_junit(self.junit))
        self.assertNotIn(evil, page)
        self.assertIn("&lt;img", page)
        self.assertNotIn("<img src=x", page)

    def test_missing_coverage_does_not_masquerade_as_zero(self):
        self.checkpoint["native_coverage"] = None
        page = render(self.checkpoint, None, [])
        self.assertIn("No instrumented native coverage was collected", page)
        self.assertIn("Not measured", page)

    def test_invalid_junit_fails_closed(self):
        (self.junit / "portable.xml").write_text("<notjunit><testcase name='x'/></notjunit>")
        with self.assertRaisesRegex(ValueError, "Not a JUnit"):
            cases_from_junit(self.junit)

    def test_unknown_schema_rejected(self):
        self.checkpoint["schema_version"] = 1000
        with self.assertRaisesRegex(ValueError, "Unsupported quality checkpoint"):
            render(self.checkpoint, self.coverage, [])


if __name__ == "__main__":
    unittest.main()
