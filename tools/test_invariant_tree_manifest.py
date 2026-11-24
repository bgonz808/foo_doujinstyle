import tempfile
from pathlib import Path
import unittest
from tools.invariant_tree_manifest import inventory

class InvariantTreeManifestTests(unittest.TestCase):
    def test_content_and_name_sensitive_not_timestamp_sensitive(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / "a.lib"
            p.write_bytes(b"library")
            baseline = inventory(root)
            os.utime(p, (1, 1))
            self.assertEqual(baseline, inventory(root))
            p.write_bytes(b"changed")
            self.assertNotEqual(baseline, inventory(root))
            p.write_bytes(b"library")
            p.rename(root / "b.lib")
            self.assertNotEqual(baseline, inventory(root))

    def test_empty_tree_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                inventory(Path(tmp))
