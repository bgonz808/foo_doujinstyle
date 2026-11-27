import unittest
from tools.artifact_forensics import stream_roles

class ArtifactForensicsTests(unittest.TestCase):
    def test_allowlisted_stream_roles(self):
        rows = stream_roles(b"Stream  3 ( 123 bytes): [DBI Stream]\nStream 7 (8 bytes): [C:\\private\\x.obj]\n")
        self.assertEqual([r["role"] for r in rows], ["dbi", "unclassified"])
        self.assertEqual([r["stream"] for r in rows], [3, 7])
        self.assertNotIn("private", str(rows))

    def test_unknown_format_is_not_guessed(self):
        self.assertEqual(stream_roles(b"Stream 7: secret"), [])
