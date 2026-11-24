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
            self.assertEqual(item["known_runtime_libraries"], {"msvcrt.lib": 1})
            self.assertEqual(item["categories"]["foobar_sdk"], 1)
            self.assertEqual(item["categories"]["first_party"], 1)
            rendered = json.dumps(result)
            self.assertNotIn("SECRET_456_PASSWORD", rendered)
            self.assertNotIn("MSVCRT.lib", rendered)
            self.assertNotIn("own_obscure_module", rendered)

    def test_omitted_sarif_kind_defaults_to_fail(self):
        with TemporaryDirectory() as dirname:
            folder = Path(dirname)
            payload = {"runs": [{"results": [{"ruleId": "BA2024",
                "message": {"id": "Warning", "arguments": [
                    "The following modules were not compiled with /Qspectre: MSVCRT.lib,pfc.lib,component.obj"]}}]}]}
            (folder / "x64-publish_references.exe.sarif").write_text(json.dumps(payload))
            row = inspect_reports(folder)["targets"]["x64/publish_references.exe"]
            self.assertEqual(row["finding_count"], 1)
            self.assertEqual(row["categories"].get("msvc_runtime"), 1)
            self.assertEqual(row["categories"].get("foobar_sdk"), 1)
            self.assertEqual(row["categories"].get("first_party"), 1)

    def test_provenance_and_mitigation_are_distinct(self):
        from tools.binskim_diagnostics import cause_categories
        self.assertEqual(cause_categories('RawCommandLine value is missing from SECRET.lib'),
                         ['missing_compiler_command_line'])
        self.assertEqual(cause_categories('switch was not enabled on the command-line'),
                         ['spectre_switch_not_enabled'])
        self.assertEqual(cause_categories('Spectre mitigations explicitly disabled'),
                         ['spectre_explicitly_disabled'])
        self.assertEqual(cause_categories('compiled with optimizations disabled (/Od)'),
                         ['optimization_disabled'])
        self.assertEqual(cause_categories('unexpected SECRET.lib'), ['unclassified'])

    def test_cause_classification_does_not_leak_paths(self):
        with TemporaryDirectory() as dirname:
            p = Path(dirname)
            secret = 'C:/PRIVATE_LOCATION/secret-library.lib'
            payload = {'runs':[{'results':[{'ruleId':'BA2024',
                'message':{'id':'Warning','arguments':[
                    'The following modules were compiled with a toolset that supports /Qspectre but the switch was not enabled on the command-line: '+secret]}}]}]}
            (p/'ARM64EC-foo_doujinstyle.dll.sarif').write_text(json.dumps(payload))
            result = inspect_reports(p)
            self.assertEqual(result['targets']['ARM64EC/foo_doujinstyle.dll']['causes'],
                             {'spectre_switch_not_enabled':1})
            self.assertNotIn('PRIVATE_LOCATION', json.dumps(result))

    def test_unavailable_reports_are_not_claimed_clean(self):
        with TemporaryDirectory() as dirname:
            report = inspect_reports(Path(dirname))
            self.assertEqual(report["complete_reports"], 0)
            self.assertFalse(all(x["present"] for x in report["targets"].values()))


if __name__ == "__main__":
    unittest.main()
