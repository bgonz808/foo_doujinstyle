"""Portable evidence contracts, not actual Windows build qualification."""
from copy import deepcopy
import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID
from tools.toolchain_inventory import tree_digest
from tools.symbol_identity import MSF7, pdb_identity, verify_pair
from tools.reproducibility_detail import archive_detail, pdb_detail

from tools.reproducibility import compare, pe_structure, FILES, verify_source_receipt, verify_embedded_identity


class ReproducibilityTests(unittest.TestCase):
    def setUp(self):
        self.ctx = {"GITHUB_SHA": "a"*40, "GITHUB_RUN_ID": "42", "GITHUB_RUN_ATTEMPT": "1"}
        self.a = {"schema_version": 1, "context": self.ctx, "replica": "a", "arch": "x64",
                  "source": {"git_commit": "a"*40, "dirty": False, "source_sha256": "b"*64},
                  "git_tree": "e"*40, "clean_before_after": True, "embedded_identity_verified": True,
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

    def test_source_receipt_must_match_before_and_after(self):
        before = {"source": {"git_commit": "a"*40, "dirty": False}, "git_tree": "b"*40}
        self.assertEqual(verify_source_receipt(before, deepcopy(before)), before["source"])
        for after in ({**before, "git_tree": "c"*40}, {**before, "source": {"dirty": True}}):
            with self.assertRaises(ValueError):
                verify_source_receipt(before, after)

    def test_worker_cannot_satisfy_dll_identity(self):
        source = {"git_commit": "a"*40, "source_sha256": "b"*64, "version": "0.2.0-dev.307+g" + "a"*12}
        markers = b"|".join(value.encode("ascii") for value in source.values())
        worker = b"MZworker|" + markers
        dll = b"MZdll|" + markers + b"|" + worker
        verify_embedded_identity(dll, worker, source)
        for bad in (b"MZdll|" + worker, dll + worker):
            with self.assertRaises(ValueError):
                verify_embedded_identity(bad, worker, source)

    def test_tree_inventory_independent_of_root_and_mtime(self):
        with TemporaryDirectory() as a, TemporaryDirectory() as b:
            for root in (Path(a), Path(b)):
                (root / "tool.dll").write_bytes(b"compiler")
                (root / "include").mkdir()
                (root / "include" / "a.h").write_bytes(b"header")
            self.assertEqual(tree_digest(a), tree_digest(b))
            (Path(b) / "tool.dll").write_bytes(b"modified")
            self.assertNotEqual(tree_digest(a), tree_digest(b))

    def test_symbol_identity_and_mismatch(self):
        guid = UUID("12345678-1234-5678-1234-567812345678")
        pdb = bytearray(512*5)
        pdb[:32] = MSF7
        struct.pack_into("<IIIIII", pdb,32,512,1,5,16,0,2)
        struct.pack_into("<I",pdb,1024,3)
        struct.pack_into("<IIII",pdb,1536,2,0,28,4)
        struct.pack_into("<III",pdb,2048,20000404,0,1)
        pdb[2060:2076]=guid.bytes_le
        self.assertEqual(pdb_identity(pdb), {"guid":str(guid),"age":1})
        pe=bytearray(1024)
        pe[:2]=b"MZ"
        struct.pack_into("<I",pe,60,64)
        pe[64:68]=b"PE\0\0"
        struct.pack_into("<HHIIIHH",pe,68,0x8664,1,0,0,0,240,0)
        struct.pack_into("<H",pe,88,0x20b)
        struct.pack_into("<I",pe,88+108,16)
        struct.pack_into("<II",pe,88+112+48,0x1000,28)
        struct.pack_into("<IIII",pe,88+240+8,512,0x1000,512,512)
        cv=b"RSDS"+guid.bytes_le+struct.pack("<I",1)+b"test.pdb\0"
        struct.pack_into("<IIII",pe,512+12,2,len(cv),0x1040,576)
        pe[576:576+len(cv)]=cv
        self.assertTrue(verify_pair(pe,pdb,"test.pdb")["basename_verified"])
        with self.assertRaises(ValueError):
            verify_pair(pe,pdb,"other.pdb")
        struct.pack_into("<I",pdb,2056,2)
        with self.assertRaises(ValueError):
            verify_pair(pe,pdb,"test.pdb")

    def test_archive_header_and_payload_are_separate(self):
        def archive(stamp, payload):
            header = (b"/".ljust(16) + str(stamp).encode().ljust(12) +
                      b"0".ljust(6) + b"0".ljust(6) + b"0".ljust(8) +
                      str(len(payload)).encode().ljust(10) + b"`\n")
            return b"!<arch>\n" + header + payload + (b"\n" if len(payload)%2 else b"")
        a=archive_detail(archive(1,b"abc"))[0]
        b=archive_detail(archive(2,b"abc"))[0]
        self.assertEqual(a["payload_sha256"],b["payload_sha256"])
        self.assertNotEqual(a["header_sha256"],b["header_sha256"])
        with self.assertRaises(ValueError):
            archive_detail(archive(1,b"abc")[:-1])

    def test_msf_logical_stream_and_physical_map(self):
        pdb=bytearray(512*6)
        pdb[:32]=MSF7
        struct.pack_into("<IIIIII",pdb,32,512,1,6,16,0,2)
        struct.pack_into("<I",pdb,1024,3)
        struct.pack_into("<IIII",pdb,1536,2,0,28,4)
        struct.pack_into("<III",pdb,2048,20000404,123,1)
        a=pdb_detail(pdb)
        pdb[2560:2560+28]=pdb[2048:2048+28]
        struct.pack_into("<I",pdb,1548,5)
        b=pdb_detail(pdb)
        self.assertEqual(a["streams"],b["streams"])
        self.assertNotEqual(a["directory_sha256"],b["directory_sha256"])
        struct.pack_into("<I",pdb,2564,456)
        self.assertNotEqual(pdb_detail(pdb)["streams"],b["streams"])
