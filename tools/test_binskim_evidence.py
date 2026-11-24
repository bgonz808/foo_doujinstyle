"""Offline negative and redaction tests for failure-resilient BinSkim evidence."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from tools.binskim_evidence import collect
from tools.binskim_policy import ARCHES, BINARIES


class BinSkimEvidenceTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.build, self.reports, self.output = (root / part for part in ("build", "reports", "evidence"))
        self.build.mkdir()
        self.reports.mkdir()
        self.baseline = root / "baseline.json"
        self.policy = root / "policy.json"
        self.version = root / "VERSION"
        self.baseline.write_text('{"schema_version":1,"accepted_findings":[]}', encoding="utf-8")
        self.policy.write_text(json.dumps({
            "schema_version": 1, "default_enforcement": "blocking",
            "rules": {"BA6006": {"enforcement": "advisory"},
                      "BA2024": {"enforcement": "blocking"}}}), encoding="utf-8")
        self.version.write_text("0.0.94\n", encoding="utf-8")
        self.secret = "SECRET_456_PASSWORD"
        self.worker = b"MZ" + bytes(range(256)) * 2
        self.dll = b"MZ" + b"A" * 400 + self.worker + b"B" * 400
        with ZipFile(self.build / "foo-v0.0.94.fb2k-component", "w") as package:
            for arch in ARCHES:
                folder = self.build / arch
                folder.mkdir()
                (folder / BINARIES[0]).write_bytes(self.dll)
                (folder / BINARIES[1]).write_bytes(self.worker)
                name = ("" if arch == "Win32" else
                        ("x64/" if arch == "x64" else "arm64ec/")) + BINARIES[0]
                package.writestr(name, self.dll)
                for binary in BINARIES:
                    target = f"{arch}/{binary}"
                    self.make_report(target)

    def make_report(self, target, *, failure=None, scanner_ok=True):
        data = {
            "version": "2.1.0", "runs": [{
                "tool": {"driver": {"name": "BinSkim"}},
                "artifacts": [{"location": {"uri": "file:///C:/private/" + self.secret + "/" + target}}],
                "invocations": [{"executionSuccessful": scanner_ok,
                                 "commandLine": "binskim --token " + self.secret,
                                 "environmentVariables": {"TOKEN": self.secret}}],
                "results": [{"ruleId": "BA2016", "kind": "pass", "level": "none",
                             "message": {"text": self.secret}}],
            }]
        }
        if failure is not None:
            data["runs"][0]["results"].append({
                "ruleId": failure, "kind": "fail", "level": "warning",
                "message": {"text": self.secret}})
        arch, name = target.split("/")
        (self.reports / f"{arch}-{name}.sarif").write_text(
            json.dumps(data), encoding="utf-8")

    def gather(self):
        return collect(self.build, self.reports, self.baseline, self.policy,
                       self.output, version_file=self.version,
                       source_commit="a" * 40, run_id="123456789")

    def test_clean_bundle_is_complete_digest_bound_and_sanitized(self):
        verdict = self.gather()
        self.assertEqual(verdict["status"], "pass")
        self.assertEqual(verdict["complete_scans"], 6)
        self.assertEqual(verdict["findings"]["blocking"], 0)
        self.assertEqual(len(list((self.output / "sarif").glob("*.sarif"))), 6)
        manifest = json.loads((self.output / "binary-manifest.json").read_text())
        self.assertEqual(manifest["source_commit"], "a" * 40)
        self.assertEqual(manifest["ci_run_id"], "123456789")
        self.assertTrue(manifest["package"]["content_verified"])
        self.assertTrue(all(manifest["targets"][f"{arch}/foo_doujinstyle.dll"]["embedded_worker_match"]
                            for arch in ARCHES))
        self.assertTrue(all(item["sarif"]["raw_sha256"] and item["sarif"]["sanitized_sha256"]
                            for item in manifest["targets"].values()))
        evidence_text = "".join(path.read_text(encoding="utf-8")
                                for path in self.output.rglob("*") if path.is_file())
        self.assertNotIn(self.secret, evidence_text)
        self.assertNotIn("C:/private", evidence_text)
        self.assertNotIn("commandLine", evidence_text)

    def test_missing_sarif_keeps_bundle_and_fails(self):
        (self.reports / "ARM64EC-publish_references.exe.sarif").unlink()
        verdict = self.gather()
        self.assertEqual(verdict["status"], "fail")
        self.assertEqual(verdict["complete_scans"], 5)
        self.assertIn("missing_sarif", verdict["reasons"])
        self.assertTrue((self.output / "binskim-verdict.json").is_file())
        self.assertTrue((self.output / "failure-summary.md").is_file())
        self.assertEqual(len(list((self.output / "sarif").glob("*.sarif"))), 5)

    def test_scanner_failed_is_not_treated_as_clean(self):
        self.make_report("x64/foo_doujinstyle.dll", scanner_ok=False)
        verdict = self.gather()
        self.assertEqual(verdict["status"], "fail")
        self.assertEqual(verdict["complete_scans"], 5)
        self.assertIn("invalid_sarif", verdict["reasons"])

    def test_blocking_spectre_warning_fails(self):
        self.make_report("x64/foo_doujinstyle.dll", failure="BA2024")
        verdict = self.gather()
        self.assertEqual(verdict["status"], "fail")
        self.assertEqual(verdict["findings"]["blocking"], 1)
        self.assertEqual(verdict["findings"]["by_rule"], {"BA2024": 1})

    def test_ltcg_is_advisory_not_risk_acceptance(self):
        self.make_report("ARM64EC/publish_references.exe", failure="BA6006")
        verdict = self.gather()
        self.assertEqual(verdict["status"], "pass")
        self.assertEqual(verdict["findings"]["advisory"], 1)
        self.assertEqual(verdict["findings"]["accepted_exceptions"], 0)
        self.assertEqual(verdict["findings"]["blocking"], 0)

    def test_invalid_policy_fails_closed(self):
        self.policy.write_text("{}")
        verdict = self.gather()
        self.assertEqual(verdict["status"], "fail")
        self.assertIn("invalid_policy", verdict["reasons"])

    def test_mismatched_package_fails(self):
        (self.build / "x64/foo_doujinstyle.dll").write_bytes(b"MZ" + b"A" * 300)
        verdict = self.gather()
        self.assertEqual(verdict["status"], "fail")
        self.assertIn("package_mismatch", verdict["reasons"])

    def test_missing_binary_fails_without_breaking_collection(self):
        (self.build / "Win32/publish_references.exe").unlink()
        verdict = self.gather()
        self.assertEqual(verdict["status"], "fail")
        self.assertIn("missing_binary", verdict["reasons"])
        self.assertEqual(verdict["complete_scans"], 6)

    def test_invalid_report_fails_with_no_unredacted_material(self):
        (self.reports / "Win32-foo_doujinstyle.dll.sarif").write_text(
            '{"token":"'+self.secret+'"}', encoding="utf-8")
        verdict = self.gather()
        self.assertEqual(verdict["status"], "fail")
        self.assertIn("invalid_sarif", verdict["reasons"])
        emitted = "".join(path.read_text()
                          for path in self.output.rglob("*") if path.is_file())
        self.assertNotIn(self.secret, emitted)


if __name__ == "__main__":
    unittest.main()
