"""Portable evidence contracts, not actual Windows build qualification."""
from copy import deepcopy
import struct
import unittest

from tools.reproducibility import compare, pe_structure, FILES


class ReproducibilityTests(unittest.TestCase):
    def setUp(self):
        self.ctx = {"GITHUB_SHA": "a"*40, "GITHUB_RUN_ID": "42", "GITHUB_RUN_ATTEMPT": "1"}
        self.a = {"schema_version": 1, "context": self.ctx, "replica": "a", "arch": "x64",
                  "source": {"git_commit": "a"*40, "dirty": False, "source_sha256": "b"*64},
                  "known_inputs": {"tool": "1"},
                  "files": {name: {"sha256": "c"*64, "size": 100} for name in FILES}}
        self.b = deepcopy(self.a)
        self.b["replica"] = "b"

    def test_identical_output_observation(self):
        self.assertEqual(compare(self.a, self.b, self.ctx)["status"], "identical_observed_outputs")

    def test_binary_and_pdb_differences_are_not_conflated(self):
        self.b["files"]["publish_references.pdb"]["sha256"] = "d"*64
        result = compare(self.a, self.b, self.ctx)
        self.assertEqual(result["status"], "different_observed_outputs")
        self.assertTrue(result["artifacts"]["publish_references.exe"]["equal"])
        self.assertFalse(result["artifacts"]["publish_references.pdb"]["equal"])

    def test_different_toolchain_reported_separately(self):
        self.b["known_inputs"]["tool"] = "2"
        self.assertEqual(compare(self.a, self.b, self.ctx)["status"], "different_known_inputs")

    def test_replayed_or_incomplete_evidence_rejected(self):
        for mutate in (lambda b: b["context"].update(GITHUB_RUN_ATTEMPT="2"),
                       lambda b: b.update(replica="a"),
                       lambda b: b["files"].pop("publish_references.pdb"),
                       lambda b: b["source"].update(dirty=True)):
            b = deepcopy(self.b)
            mutate(b)
            with self.assertRaises(ValueError):
                compare(self.a, b, self.ctx)

    def test_pe_structure_and_bounds(self):
        data = bytearray(512)
        data[:2] = b"MZ"
        struct.pack_into("<I", data, 60, 64)
        data[64:68] = b"PE\0\0"
        struct.pack_into("<HHIIIHH", data, 68, 0x8664, 1, 123, 0, 0, 112, 0)
        struct.pack_into("<H", data, 88, 0x20b)
        data[200:205] = b".text"
        struct.pack_into("<II", data, 216, 16, 300)
        self.assertEqual(pe_structure(data)["coff_timestamp"], 123)
        self.assertEqual(pe_structure(data)["sections"][0]["size"], 16)
        struct.pack_into("<II", data, 216, 16, 510)
        with self.assertRaises(ValueError):
            pe_structure(data)
