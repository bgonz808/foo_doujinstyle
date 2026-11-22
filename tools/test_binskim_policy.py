"""Negative tests for BinSkim SARIF and shipped-binary coverage."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from tools.binskim_policy import ARCHES, BINARIES, check


class BinSkimPolicyTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.build = root / "build"
        self.build.mkdir()
        self.reports = root / "reports"
        self.reports.mkdir()
        with ZipFile(self.build / "foo.fb2k-component", "w") as package:
            for arch in ARCHES:
                folder = self.build / arch
                folder.mkdir()
                for name in BINARIES:
                    (folder / name).write_bytes(b"MZ" + bytes(300))
                member = ("foo_doujinstyle.dll" if arch == "Win32" else
                          ("x64" if arch == "x64" else "arm64ec") + "/foo_doujinstyle.dll")
                package.writestr(member, (folder / "foo_doujinstyle.dll").read_bytes())
                for name in BINARIES:
                    target = f"{arch}/{name}"
                    self.report(target, self.clean(target))
        self.baseline = root / "baseline.json"
        self.baseline.write_text(json.dumps({"schema_version": 1, "accepted_findings": []}))

    def clean(self, target):
        return {"version": "2.1.0", "runs": [{
            "tool": {"driver": {"name": "BinSkim"}},
            "invocations": [{"executionSuccessful": True}],
            "artifacts": [{"location": {"uri": "file:///C:/work/native/build/" + target}}],
            "results": [{"ruleId": "BA2016", "kind": "pass", "level": "none"}]}]}

    def report(self, target, data):
        arch, name = target.split("/")
        (self.reports / f"{arch}-{name}.sarif").write_text(json.dumps(data))

    def test_clean(self):
        self.assertEqual(check(self.build, self.reports, self.baseline), [])

    def test_missing_executable(self):
        (self.build / "x64/publish_references.exe").unlink()
        with self.assertRaisesRegex(ValueError, "Missing/invalid"):
            check(self.build, self.reports, self.baseline)

    def test_package_mismatch(self):
        (self.build / "Win32/foo_doujinstyle.dll").write_bytes(b"MZ" + bytes(301))
        with self.assertRaisesRegex(ValueError, "Packaged DLL differs"):
            check(self.build, self.reports, self.baseline)

    def test_missing_sarif(self):
        (self.reports / "x64-publish_references.exe.sarif").unlink()
        with self.assertRaisesRegex(ValueError, "Missing BinSkim SARIF"):
            check(self.build, self.reports, self.baseline)

    def test_wrong_target(self):
        self.report("x64/foo_doujinstyle.dll", self.clean("Win32/foo_doujinstyle.dll"))
        with self.assertRaisesRegex(ValueError, "target coverage"):
            check(self.build, self.reports, self.baseline)

    def test_scanner_failure(self):
        target = "Win32/foo_doujinstyle.dll"
        data = self.clean(target)
        data["runs"][0]["invocations"][0]["executionSuccessful"] = False
        self.report(target, data)
        with self.assertRaisesRegex(ValueError, "did not complete"):
            check(self.build, self.reports, self.baseline)

    def test_finding_fails(self):
        target = "Win32/foo_doujinstyle.dll"
        data = self.clean(target)
        data["runs"][0]["results"].append({"ruleId": "BA2016", "kind": "fail", "level": "error"})
        self.report(target, data)
        with self.assertRaisesRegex(ValueError, "BA2016"):
            check(self.build, self.reports, self.baseline)

    def test_exact_accepted_finding(self):
        target = "Win32/foo_doujinstyle.dll"
        data = self.clean(target)
        data["runs"][0]["results"].append(
            {"ruleId": "BA2022", "kind": "fail", "level": "warning", "message": {"id": "Unsigned"}})
        self.report(target, data)
        self.baseline.write_text(json.dumps({"schema_version": 1, "accepted_findings": [{
            "target": target, "rule": "BA2022", "kind": "fail", "level": "warning",
            "message_id": "Unsigned", "reason": "Unsigned experimental build"}]}))
        self.assertEqual(len(check(self.build, self.reports, self.baseline)), 1)

    def test_stale_baseline_fails(self):
        self.baseline.write_text(json.dumps({"schema_version": 1, "accepted_findings": [{
            "target": "Win32/foo_doujinstyle.dll", "rule": "BA9999", "kind": "fail",
            "level": "warning", "message_id": "", "reason": "obsolete"}]}))
        with self.assertRaisesRegex(ValueError, "unobserved"):
            check(self.build, self.reports, self.baseline)

    def test_not_applicable_only(self):
        target = "Win32/foo_doujinstyle.dll"
        data = self.clean(target)
        data["runs"][0]["results"] = [{"ruleId": "BA2018", "kind": "notApplicable"}]
        self.report(target, data)
        with self.assertRaisesRegex(ValueError, "only not-applicable"):
            check(self.build, self.reports, self.baseline)


if __name__ == "__main__":
    unittest.main()
