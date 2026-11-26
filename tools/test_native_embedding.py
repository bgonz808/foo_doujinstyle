"""Production native embedding is distinct from historical Python fixtures."""
import struct
import tempfile
import unittest
from pathlib import Path
from native.embed_worker import embed, verify, source_inventory, render


class NativeEmbeddingTests(unittest.TestCase):
    def test_production_source_no_runtime_python(self):
        root = Path(__file__).resolve().parents[1]
        row = source_inventory(root)
        self.assertEqual(row["python_lines"], 0)
        self.assertEqual(row["python_files"], 0)
        self.assertFalse(row["binary_embedding_verified"])

    def test_exact_binary_embedding_and_negative_cases(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            worker, header, dll = (root / name for name in ("worker.exe", "payload.inc", "component.dll"))
            data = bytearray(512)
            data[:2] = b"MZ"
            struct.pack_into("<I", data, 0x3C, 128)
            data[128:132] = b"PE\x00\x00"
            worker.write_bytes(data)
            embed(header, worker)
            self.assertEqual(header.read_text(encoding="ascii"), render(data))
            dll.write_bytes(b"fixture-prefix" + data + b"fixture-suffix")
            self.assertTrue(verify(header, worker, dll)["binary_embedding_verified"])
            dll.write_bytes(dll.read_bytes() + b"native/search_worker.py")
            with self.assertRaises(ValueError):
                verify(header, worker, dll)
            dll.write_bytes(b"missing worker")
            with self.assertRaises(ValueError):
                verify(header, worker, dll)
            header.write_text("stale", encoding="ascii")
            with self.assertRaises(ValueError):
                verify(header, worker, dll)
            worker.write_bytes(b"not a PE")
            with self.assertRaises(ValueError):
                embed(header, worker)
