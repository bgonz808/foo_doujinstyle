"""Guard real-world pathology coverage targets without claiming they ran."""
from __future__ import annotations

import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ReportedPathologies(unittest.TestCase):
    def test_named_case_tracking(self):
        features = json.loads((ROOT / "quality/features.json").read_text(encoding="utf-8"))
        decoder = json.loads((ROOT / "quality/decoder-io-profiles.json").read_text(encoding="utf-8"))
        mapped = {x["id"]: x for x in features["features"]}
        jp = mapped["japanese-metadata-encoding-regression"]
        startup = mapped["delayed-archive-audio-startup-regression"]
        self.assertEqual(jp["issue"], 20)
        self.assertEqual(startup["issue"], 26)
        for case in (jp, startup):
            self.assertEqual(case["migration_state"], "regression-not-qualified")
            self.assertEqual(case["contract_tests"], [])
            self.assertTrue(case["host_e2e_tests"])
        cases = {x["id"]: x for x in decoder["named_regressions"]}
        actual = cases["release-19732-delayed-startup"]
        self.assertEqual(actual["issue"], 26)
        self.assertEqual(actual["archive_mode"], "zip-confirmed-member-method-unverified")
        self.assertEqual(actual["audio_profile"], "wav-confirmed-subtype-unverified")
        self.assertEqual(actual["status"], "user-confirmed-wav-zip-method-unverified")

    def test_zip_encoding_contract_is_not_replaced_by_extension_guess(self):
        issue = next(x for x in json.loads((ROOT / "quality/features.json").read_text(
            encoding="utf-8"))["features"]
            if x["id"] == "japanese-metadata-encoding-regression")
        self.assertIn("encoding.28590", " ".join(issue["host_e2e_tests"]))
        # Real source-derived and synthetic CP932 sample evidence remains needed.
        # In particular, CP932 byte 0x5C can be part of a multibyte character,
        # so it cannot be treated as a ZIP path separator before decoding.

if __name__ == "__main__":
    unittest.main()
