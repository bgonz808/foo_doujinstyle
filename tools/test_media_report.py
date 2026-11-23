"""Tests for offline, schema-checked media fixture HTML presentation."""
import json
from pathlib import Path
import unittest

from tools.media_report import render


ROOT = Path(__file__).resolve().parent.parent


class MediaReportTests(unittest.TestCase):
    def setUp(self):
        self.matrix = json.loads((ROOT / "quality/media-support.json").read_text(encoding="utf-8"))
        self.manifest = {
            "schema_version": 1, "kind": "media-fixture-manifest",
            "source_commit": "a" * 40, "component_version": "0.0.105",
            "toolchain": {"ffmpeg": "sample"},
            "cases": [{"id": "pcm-wav", "container": "wav", "codec": "pcm_s16le",
                       "status": "generated", "classification": "audio_candidate"}]
        }
        self.result = {
            "schema_version": 1, "kind": "media-transport-observation",
            "qualification": "fixture-only-not-native-or-host",
            "case_count": 1,
            "pairs": [{"case": "pcm-wav", "archive": "zip-stored",
                       "relative_to_direct_ms": 0.12, "response_byte_ratio": 1.2},
                      {"case": "pcm-wav", "archive": "zip-deflate",
                       "relative_to_direct_ms": 0.19, "response_byte_ratio": 0.72}]
        }

    def test_report_is_offline_and_labels_limitations(self):
        html = render(self.matrix, self.manifest, self.result)
        self.assertIn("Offline media compatibility fixtures", html)
        self.assertIn("no foobar2000", html.lower())
        self.assertIn("0.12 ms", html)
        self.assertIn("1.20× bytes", html)
        self.assertIn("pcm-wav", html)
        self.assertIn("href='manifest.json'", html)
        self.assertIn("Content-Security-Policy", html)
        self.assertNotIn("<script", html)
        self.assertNotIn("<iframe", html)

    def test_html_escapes_untrusted_metadata(self):
        attack = '<img src=x onerror="alert(1)">'
        self.manifest["cases"][0]["id"] = attack
        self.result["pairs"][0]["case"] = attack
        html = render(self.matrix, self.manifest, self.result)
        self.assertNotIn(attack, html)
        self.assertIn("&lt;img", html)

    def test_incompatible_schema_rejected(self):
        self.result["qualification"] = "host-qualified"
        with self.assertRaisesRegex(ValueError, "Unsupported media evidence"):
            render(self.matrix, self.manifest, self.result)


if __name__ == "__main__":
    unittest.main()
