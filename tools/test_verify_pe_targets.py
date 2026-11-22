import tempfile
from pathlib import Path
import struct
import unittest
from tools.verify_pe_targets import EXPECTED, machine

class TestPEMachines(unittest.TestCase):
    def pe(self, arch):
        body = bytearray(512)
        body[:2] = b"MZ"
        struct.pack_into("<I", body, 0x3C, 0x80)
        body[0x80:0x84] = b"PE" + bytes(2)
        struct.pack_into("<H", body, 0x84, arch)
        return bytes(body)

    def test_all_known_machines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "part.dll"
            for arch, expected in EXPECTED.items():
                path.write_bytes(self.pe(expected))
                with self.subTest(arch=arch):
                    self.assertEqual(machine(path), expected)

    def test_corrupt_pe_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "part.dll"
            for bad in (b"", b"MZ", bytes(512), b"X" * 512):
                path.write_bytes(bad)
                with self.assertRaises(ValueError):
                    machine(path)

if __name__ == "__main__":
    unittest.main()
