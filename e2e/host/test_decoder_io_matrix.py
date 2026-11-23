"""No hardware/network test: validate streaming format × cursor plan semantics."""
from __future__ import annotations
from copy import deepcopy
import json
from pathlib import Path
import unittest

from e2e.host.decoder_io_matrix import ProfileError, generate

SPEC = Path(__file__).resolve().parents[2] / "quality" / "decoder-io-profiles.json"

class StreamingMatrixContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = json.loads(SPEC.read_text(encoding="utf-8"))

    def test_complete_cross_product_without_runtime_claim(self):
        plan = generate(self.spec)
        self.assertEqual(plan["asset_combinations"], 27)
        self.assertEqual(plan["planned_step_count"], 648)
        self.assertEqual(plan["status"], "not_run")
        self.assertEqual(plan["host_e2e_cases_executed"], 0)
        self.assertIsNone(plan["first_party_dll_coverage"])
        self.assertEqual(len(plan["host_process_schedule"]), 2)
        self.assertEqual(sum(s["restart_count"] for s in plan["host_process_schedule"]), 1)
        for case in plan["cases"]:
            self.assertIsNone(case["source_sha256"])
            self.assertEqual(case["host_capability"], "not_qualified")
            self.assertTrue(all(x["verdict"] == "not_run" for x in case["steps"]))
            self.assertTrue(all(x["actual_decoder"] is None for x in case["steps"]))

    def test_wav_matrix_and_archive_boundaries(self):
        cases = {x["id"]: x for x in generate(self.spec)["cases"]}
        for method in ("direct", "zip-store", "zip-deflate"):
            self.assertIn(method + "--wav-pcm16", cases)
            self.assertIn(method + "--wav-pcm24", cases)
            self.assertIn(method + "--wav-float32", cases)
            self.assertIn(method + "--wav-extra-chunks", cases)
        self.assertEqual(cases["zip-deflate--wav-pcm16"]["cursor"], "DeflateCursor")
        self.assertEqual(cases["zip-store--wav-pcm16"]["cursor"], "RangeCursor")
        self.assertEqual(cases["direct--wav-pcm16"]["cursor"], "foobar-file")
        unsupported = {x["container"] for x in generate(self.spec)["unsupported_archive_cases"]}
        self.assertEqual(unsupported, {"rar", "7z", "zip"})

    def test_determinism_and_rejects_bad_profiles(self):
        self.assertEqual(generate(self.spec), generate(self.spec))
        bad = deepcopy(self.spec)
        bad["audio_profiles"].append(deepcopy(bad["audio_profiles"][0]))
        with self.assertRaises(ProfileError):
            generate(bad)
        bad = deepcopy(self.spec)
        bad["required_observables"].remove("cursor_seek_count")
        with self.assertRaises(ProfileError):
            generate(bad)
        bad = deepcopy(self.spec)
        bad["lifecycle_phases"].remove("host-restarted-persistent-state")
        with self.assertRaises(ProfileError):
            generate(bad)

if __name__ == "__main__":
    unittest.main()
