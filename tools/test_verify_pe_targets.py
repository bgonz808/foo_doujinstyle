import tempfile
from pathlib import Path
import struct
import unittest
from unittest.mock import patch, SimpleNamespace
from tools.verify_pe_targets import EXPECTED, machine, verify

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

    def test_arm64ec_requires_linked_arm64x_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "part.dll"
            linker = Path(tmp) / "link.exe"
            linker.write_bytes(b"stub")
            path.write_bytes(self.pe(0x8664))
            with patch("tools.verify_pe_targets.subprocess.run") as run:
                run.return_value = SimpleNamespace(stdout="  8664 machine (x64) (ARM64X)\n")
                verify(path, "ARM64EC", link_exe=linker)
                run.assert_called_once()
                run.return_value = SimpleNamespace(stdout="  8664 machine (x64)\n")
                with self.assertRaisesRegex(RuntimeError, "lacks ARM64X"):
                    verify(path, "ARM64EC", link_exe=linker)
            with self.assertRaisesRegex(RuntimeError, "requires a real"):
                verify(path, "ARM64EC")

    def test_arm64ec_object_machine_is_not_valid_linked_pe(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "part.dll"
            linker = Path(tmp) / "link.exe"
            linker.write_bytes(b"stub")
            path.write_bytes(self.pe(0xA641))
            with self.assertRaisesRegex(RuntimeError, "expected final PE"):
                verify(path, "ARM64EC", link_exe=linker)

if __name__ == "__main__":
    unittest.main()
