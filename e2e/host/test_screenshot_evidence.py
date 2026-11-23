"""Synthetic files test screenshot collection without pretending to capture GUI."""
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest

from e2e.host.screenshot_evidence import collect, MAX_IMAGES


def png() -> bytes:
    # Structural IHDR+payload sentinel; collector checks evidence identity,
    # not PNG decoding or semantic UI contents.
    return (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" +
            struct.pack(">II", 1280, 800) + b"\x08\x02\x00\x00\x00" +
            b"\x00" * 64)


class ScreenshotContracts(unittest.TestCase):
    def test_no_image_is_never_qualified(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(collect(Path(temp))["status"], "missing")

    def test_hashes_image_in_robot_output_dir(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "screenshot1.png"
            file.write_bytes(png())
            out = collect(Path(temp))
            self.assertEqual(out["status"], "present")
            self.assertEqual(out["count"], 1)
            self.assertEqual(out["files"][0]["sha256"], hashlib.sha256(png()).hexdigest())
            self.assertEqual(out["files"][0]["path"], "screenshot1.png")

    def test_rejects_truncated_or_malicious_files(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "broken.png"
            file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"not an image")
            self.assertEqual(collect(Path(temp))["status"], "invalid")
            file.write_bytes(png())
            target = Path(temp) / "outside.png"
            try:
                target.symlink_to(file)
            except (OSError, NotImplementedError):
                return
            self.assertEqual(collect(Path(temp))["status"], "invalid")


if __name__ == "__main__":
    unittest.main()
