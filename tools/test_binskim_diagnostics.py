"""Negative/redaction tests for BA2024 diagnostics."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.binskim_diagnostics import inspect_reports


class DiagnosticTests(unittest.TestCase):
    def test_category_only_and_no_raw_paths_or_secrets(self):
        with TemporaryDirectory() as dirname:
            folder = Path(dirname)
            payload = {"runs": [{"results": [{"ruleId": "BA2024", "kind": "fail",
                "message": {"text": "C:\\SECRET_456_PASSWORD\\private\\MSVCRT.lib source=/bad", "arguments": [
                    "pfc.lib; hardening failure; component.obj",
                    "SECRET_456_PASSWORD: own_obscure_module.obj"]}}]}]}
            (folder / "Win32-foo_doujinstyle.dll.sarif").write_text(json.dumps(payload))
            result = inspect_reports(folder)
            self.assertEqual(result["complete_reports"], 1)
            item = result["targets"]["Win32/foo_doujinstyle.dll"]
            self.assertEqual(item["finding_count"], 1)
            self.assertEqual(item["categories"]["msvc_runtime"], 1)
            self.assertEqual(item["categories"]["foobar_sdk"], 1)
            self.assertEqual(item["categories"]["first_party"], 1)
            rendered = json.dumps(result)
            self.assertNotIn("SECRET_456_PASSWORD", rendered)
            self.assertNotIn("MSVCRT.lib", rendered)
            self.assertNotIn("own_obscure_module", rendered)

    def test_unavailable_reports_are_not_claimed_clean(self):
        with TemporaryDirectory() as dirname:
            report = inspect_reports(Path(dirname))
            self.assertEqual(report["complete_reports"], 0)
            self.assertFalse(all(x["present"] for x in report["targets"].values()))


if __name__ == "__main__":
    unittest.main()
