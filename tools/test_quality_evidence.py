"""Synthetic contract tests for reusable evidence and coverage summarization."""
import json
import os
from unittest.mock import patch
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
import xml.etree.ElementTree as ET

from tools.quality.coverage import reduce_export
from tools.quality.report import create, assess_patterns, parse_junit


class QualityEvidenceTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.junit = self.root / "junit"
        self.junit.mkdir()
        self.version = self.root / "VERSION"
        self.version.write_text("0.0.96\n")
        self.coverage = self.root / "coverage.json"
        self.inventory = self.root / "inventory.json"
        self.inventory.write_text(json.dumps({
            "python_lines": 1375, "fallback_python_lines": 0,
            "deprecated_active_lines": 287}))
        self.features = self.root / "features.json"
        self.features.write_text(json.dumps({
            "schema_version": 1,
            "migration_baseline": {"version": "0.0.64", "packaged_python_lines": 1400},
            "features": [{
                "id": "search", "issue": 1, "migration_state": "python-adapter-active",
                "contract_tests": ["pkg.SearchTests.test_search"],
                "host_e2e_tests": ["doujinstyle.search.*"]
            }]}))
        self.coverage.write_text(json.dumps({
            "schema_version": 1, "measured": True, "tool": "llvm-cov",
            "scope": "portable-publisher", "source_file_count": 2,
            "profile_count": 4, "summary": {
                "lines": {"covered": 9, "count": 10, "percent": 90},
                "branches": {"covered": 2, "count": 4, "percent": 50},
                "functions": {"covered": 1, "count": 2, "percent": 50},
                "regions": {"covered": 10, "count": 12, "percent": 83.3333}
            }}))
        self.junit_file = self.junit / "unit.xml"
        self.write_junit()

    def write_junit(self, failed=False, skipped=False):
        root = ET.Element("testsuite")
        c = ET.SubElement(root, "testcase", classname="pkg.SearchTests", name="test_search")
        if failed:
            ET.SubElement(c, "failure", message="regression")
        if skipped:
            ET.SubElement(c, "skipped", message="no Windows host")
        ET.ElementTree(root).write(self.junit_file)

    def report(self, **kwargs):
        with patch.dict(os.environ, {"GITHUB_SHA": ""}):
            return create(coverage=kwargs.get("coverage", self.coverage),
                      junit_dir=self.junit, inventory=self.inventory,
                      features=self.features, version_file=self.version,
                      output=self.root / "out", require_coverage=kwargs.get("require_coverage", True))

    def test_real_counts_and_feature_link_are_not_equated_with_e2e(self):
        result = self.report()
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["tests"]["passed"], 1)
        self.assertEqual(result["native_coverage"]["summary"]["lines"]["percent"], 90)
        self.assertIsNone(result["native_coverage"]["host_e2e_coverage"])
        self.assertEqual(result["features"][0]["contracts"]["status"], "passed")
        self.assertEqual(result["features"][0]["host_e2e"]["status"], "not_run")
        self.assertEqual(result["migration"]["packaged_python_lines"], 1375)
        self.assertEqual(result["migration"]["delta_python_lines"], -25)
        self.assertEqual(result["features"][0]["migration_state"], "python-adapter-active")
        self.assertEqual(result["tests"]["unique_test_ids"], 1)
        self.assertEqual(result["tests"]["duplicate_executions"], 0)
        self.assertEqual(result["evidence"]["coverage_summary_sha256"],
                         __import__("hashlib").sha256(self.coverage.read_bytes()).hexdigest())
        self.assertTrue((self.root / "out" / "summary.md").is_file())

    def test_missing_coverage_is_not_zero_or_success(self):
        result = self.report(coverage=self.root / "absent.json", require_coverage=True)
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["native_coverage"])

    def test_failing_junit_fails_checkpoint(self):
        self.write_junit(failed=True)
        result = self.report()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["features"][0]["contracts"]["status"], "failed")

    def test_skipped_is_partial_not_passed(self):
        self.write_junit(skipped=True)
        result = self.report()
        self.assertEqual(result["features"][0]["contracts"]["status"], "partial")
        self.assertEqual(result["tests"]["skipped"], 1)

    def test_no_junit_is_incomplete(self):
        self.junit_file.unlink()
        result = self.report()
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["tests"]["total"], 0)

    def test_invalid_features_raise(self):
        self.features.write_text('{"schema_version":1,"features":[{"id":"search"},{"id":"search"}]}')
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.report()

    def test_llvm_summary_only_instrumented_native_scope(self):
        root = self.root / "native"
        root.mkdir()
        item = root / "pub.cpp"
        item.write_text("int main(){return 0;}")
        generated = self.root / "build" / "third_party.cpp"
        generated.parent.mkdir()
        generated.write_text("int other(){return 0;}")
        def hit(path, n):
            return {"filename": str(path), "summary": {
                metric: {"count": n, "covered": n - 1} for metric
                in ("lines", "functions", "branches", "regions")}}
        raw = {"type": "llvm.coverage.json.export", "data": [{
            "files": [hit(item, 5), hit(generated, 100)]}]}
        coverage = reduce_export(raw, root=root, binaries=[], scope="portable")
        self.assertEqual(coverage["summary"]["lines"]["count"], 5)
        self.assertEqual(coverage["summary"]["lines"]["covered"], 4)
        self.assertEqual(coverage["source_file_count"], 1)
        self.assertIsNone(coverage["whole_repository_coverage"])

    def test_unittest_runner_emits_junit_even_when_failing(self):
        tests = self.root / "sample"
        tests.mkdir()
        (tests / "test_sample.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n"
            " def test_good(self): self.assertTrue(True)\n"
            " def test_bad(self): self.assertTrue(False)\n"
            " @unittest.skip('fixture')\n def test_skip(self): pass\n")
        target = self.root / "observed.xml"
        cmd = [sys.executable, "-m", "tools.quality.unittest_runner", "--start", str(tests),
               "--output", str(target), "--suite-name", "synthetic"]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        tree = ET.parse(target)
        self.assertEqual(len(tree.findall(".//testcase")), 3)
        self.assertEqual(len(tree.findall(".//failure")), 1)
        self.assertEqual(len(tree.findall(".//skipped")), 1)

    def test_subtest_failure_not_reported_as_pass(self):
        from tools.quality.unittest_runner import RecordedResult, to_xml
        class Fixture(unittest.TestCase):
            def runTest(self):
                with self.subTest(value=1):
                    self.fail("private diagnostic")
        result = unittest.TextTestRunner(resultclass=RecordedResult).run(Fixture())
        xml = to_xml(result.records, "subtest")
        self.assertFalse(result.wasSuccessful())
        self.assertEqual(xml.get("failures"), "1")
        self.assertNotIn("private diagnostic", ET.tostring(xml, encoding="unicode"))

    def test_junit_parser_is_fail_closed_on_wrong_root(self):
        self.junit_file.write_text("<not-junit><testcase name='x'/></not-junit>")
        with self.assertRaisesRegex(ValueError, "Not JUnit"):
            parse_junit(self.junit)

    def test_feature_pattern_unknown_does_not_claim_coverage(self):
        result = assess_patterns(["absent.*"], [{"id": "pkg.ok", "status": "passed"}])
        self.assertEqual(result["status"], "not_run")
        self.assertEqual(result["matched_cases"], 0)


if __name__ == "__main__":
    unittest.main()
