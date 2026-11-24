import io
from pathlib import Path
import struct
import tempfile
import unittest
from tools.zstd_frame_probe import probe
from tools.zstd_frame_bench import spans, upstream_decode_args


class ProbeTests(unittest.TestCase):
    def test_frame_and_seek_table(self):
        # Single-segment frame, content size 3, last raw block of 3 bytes.
        frame = bytes.fromhex("28b52ffd2003190000") + b"abc"
        table = struct.pack("<IIII", 0x184D2A5E, 17, len(frame), 3) + struct.pack("<IBI", 1, 0, 0x8F92EAB1)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.zst"
            path.write_bytes(frame + table)
            report = probe(path)
            self.assertEqual(report["frame_count"], 1)
            self.assertEqual(report["frames"][0]["content_bytes"], 3)
            self.assertEqual(report["frames"][0]["window_bytes"], 3)
            self.assertEqual(report["seek_table"]["entries"], 1)
            path.write_bytes(frame[:-1])
            with self.assertRaises(ValueError):
                probe(path)

    def test_long_window_policy(self):
        # Non-single-segment frame, WD=160 => 1GiB, empty last raw block.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "long.zst"
            path.write_bytes(bytes.fromhex("28b52ffd00a0010000"))
            layout = probe(path)
            self.assertEqual(layout["frames"][0]["window_bytes"], 1 << 30)
            self.assertIn("--long=30", upstream_decode_args(path, Path("zstd"), layout))
            layout["frames"][0]["window_bytes"] = (1 << 30) + 1
            with self.assertRaisesRegex(ValueError, "outside policy"):
                upstream_decode_args(path, Path("zstd"), layout)

    def test_boundaries(self):
        for hybrid in (False, True):
            rows = list(spans(110, 32, [0, 21, 40, 72, 110], hybrid))
            self.assertEqual(rows[0][0], 0)
            self.assertEqual(rows[-1][1], 110)
            self.assertTrue(all(0 < b-a <= 32 for a,b in rows))
            self.assertTrue(all(a[1] == b[0] for a,b in zip(rows, rows[1:])))


if __name__ == "__main__":
    unittest.main()
