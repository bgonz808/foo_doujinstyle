import tempfile
from pathlib import Path
import unittest
from tools.sqlite_cache_pilot import ROOTS, snapshot, restore, allowed
from tools.binskim_evidence import digest

class SqliteCachePilotTests(unittest.TestCase):
    def test_restore_and_reject_wrong_context_or_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            producer, consumer = root / "producer", root / "consumer"
            for prefix in ROOTS:
                folder = producer / prefix
                folder.mkdir(parents=True)
                (folder / "test.obj").write_bytes(b"test")
            (producer / ROOTS[0] / "doujinstyle_sqlite.lib").write_bytes(b"lib")
            archive = root / "cache.zip"
            ctx = {"arch": "x64", "flags": "/Qspectre"}
            sha = snapshot(producer, archive, ctx)
            with self.assertRaisesRegex(ValueError, "Archive digest"):
                restore(consumer, archive, "0"*64, ctx)
            with self.assertRaisesRegex(ValueError, "context mismatch"):
                restore(consumer, archive, sha, {"arch": "x64", "flags": "/Qspectre-"})
            restore(consumer, archive, sha, ctx)
            self.assertEqual(digest(consumer / ROOTS[0] / "doujinstyle_sqlite.lib"),
                             digest(producer / ROOTS[0] / "doujinstyle_sqlite.lib"))
            with self.assertRaisesRegex(ValueError, "empty destination"):
                restore(consumer, archive, sha, ctx)

    def test_forbidden_paths(self):
        for path in ("../evil.lib", "native/build/shared-lib/x64/../evil.lib",
                     "native/build/shared-lib/x64/evil.exe", "foobar-sdk/a.lib",
                     "native/build/shared-lib/x64/a.lib:stream"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                allowed(path)
