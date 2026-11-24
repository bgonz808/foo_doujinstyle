"""Prevent orphaned E2E documents and inflated execution claims on extraction.

This is a *planning and source-contract* test; it cannot qualify Windows host
playback, PCM output, ETW capture or actual per-asset latency.
"""
from pathlib import Path
import json
import unittest

from e2e.host.decoder_io_matrix import generate
from e2e.host.cache_matrix import validate_manifest
from e2e.host.trace import EVENTS, PAIRS

ROOT = Path(__file__).resolve().parents[1]
DOCS = ("LATENCY_MODEL.md", "STREAMING_IO_MATRIX.md", "CACHE_MATRIX.md",
        "ARCHIVE_TOUCH.md", "TRACE.md", "HOST_E2E.md",
        "WORKSTREAM_LEDGER.md")


class ExtractedE2EContracts(unittest.TestCase):
    def test_preserved_documents_are_navigable(self):
        for name in DOCS:
            with self.subTest(doc=name):
                path = ROOT / "docs" / name
                self.assertTrue(path.is_file(), f"Missing critical extracted doc: {name}")
                self.assertGreater(path.stat().st_size, 150)
        model = (ROOT / "docs/LATENCY_MODEL.md").read_text(encoding="utf-8")
        for token in ("STREAMING_IO_MATRIX.md", "CACHE_MATRIX.md",
                      "ARCHIVE_TOUCH.md", "output PCM",
                      "not_measured", "ZIP STORE", "ZIP DEFLATE",
                      "Wavebar", "Minibar", "#19732"):
            self.assertIn(token, model)

    def test_streaming_matrix_is_only_a_plan(self):
        doc = json.loads((ROOT / "quality/decoder-io-profiles.json").read_text())
        result = generate(doc)
        self.assertEqual(result["asset_combinations"], 27)
        self.assertEqual(result["planned_step_count"], 648)
        self.assertEqual(result["host_e2e_cases_executed"], 0)
        self.assertEqual(result["status"], "not_run")
        self.assertIsNone(result["first_party_dll_coverage"])
        self.assertIsNone(result["first_party_worker_coverage"])
        self.assertTrue(all(case["host_capability"] == "not_qualified"
                            for case in result["cases"]))
        self.assertEqual({mode["id"] for mode in doc["archive_modes"]},
                         {"direct", "zip-store", "zip-deflate"})
        self.assertEqual(len(doc["audio_profiles"]), 9)

    def test_cache_state_vectors_remain_declared(self):
        plan = json.loads((ROOT / "quality/cache-scenarios.json").read_text())
        validate_manifest(plan)
        self.assertEqual(len(plan["dimensions"]), 10)
        self.assertIn("select", plan["dimensions"]["trigger"])
        self.assertIn("server-closed", plan["dimensions"]["connection"])
        self.assertIn("restarted", plan["dimensions"]["runtime"])
        self.assertIn("partial", plan["dimensions"]["checkpoints"])
        self.assertIn("mediafire-stub", plan["dimensions"]["provider"])

    def test_qpc_has_separate_decode_and_output_stages(self):
        for event in ("robot.play.intent", "component.track.open.begin",
                      "component.decoder.first_pcm", "output.pcm.first",
                      "output.wasapi.rendered",
                      "component.decoder.seek.first_pcm", "robot.stop.intent"):
            self.assertIn(event, EVENTS)
        names = {name for _, _, name in PAIRS}
        self.assertTrue({"play.intent_to_first_pcm",
                         "play.intent_to_output_pcm",
                         "seek.intent_to_first_pcm"}.issubset(names))

    def test_ledger_remembers_source_prs_and_unmerged_quality(self):
        ledger = (ROOT / "docs/WORKSTREAM_LEDGER.md").read_text()
        for ref in ("#19", "#21", "#23", "#24", "#27", "#28",
                    "#29", "#31", "#40", "#41", "#42", "#33"):
            self.assertIn(ref, ledger)
        self.assertIn("unported", ledger.lower())
        self.assertIn("quality", ledger.lower())


if __name__ == "__main__":
    unittest.main()
