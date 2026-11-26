import copy
import json
from pathlib import Path
import unittest
from tools.toolchain_lock import evaluate


class ToolchainLockTests(unittest.TestCase):
    def setUp(self):
        self.lock = json.loads(Path(__file__).with_name("windows-toolchain-lock.json").read_text())
        self.actual = copy.deepcopy(self.lock["roots"])

    def test_matching_hosted_runner_reused(self):
        self.assertTrue(evaluate(self.lock, self.actual)["allowed"])

    def test_candidate_not_release_approved(self):
        self.assertFalse(evaluate(self.lock, self.actual, release=True)["allowed"])
        approved = {**self.lock, "qualification": "approved"}
        self.assertTrue(evaluate(approved, self.actual, release=True)["allowed"])

    def test_each_root_content_change_blocks(self):
        for root in self.actual:
            with self.subTest(root=root):
                drift = copy.deepcopy(self.actual)
                drift[root]["sha256"] = "0"*64
                result = evaluate(self.lock, drift)
                self.assertFalse(result["allowed"])
                self.assertEqual(result["differences"], [root])

    def test_missing_extra_and_malformed_roots_fail_closed(self):
        for bad in ({}, {**self.actual, "unexpected": self.actual["sdk-bin"]},
                    {**self.actual, "sdk-bin": {"sha256": "0"*64, "files": True, "bytes": 1}}):
            with self.subTest(bad=list(bad)), self.assertRaises(ValueError):
                evaluate(self.lock, bad)
