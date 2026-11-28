"""Tests for commit-addressed native SourceLink maps."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.source_link import mapping


class SourceLinkTests(unittest.TestCase):
    def test_commit_addressed_source(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            sha = "0123456789abcdef" * 2 + "01234567"
            self.assertEqual(mapping(root, sha), {"documents": {
                root.resolve().as_posix() + "/*":
                    f"https://raw.githubusercontent.com/bgonz808/foo_doujinstyle/{sha}/*"
            }})

    def test_canonical_native_mapping(self):
        sha = "a" * 40
        self.assertEqual(mapping(Path("different-root"), sha, True), {"documents": {
            "X:/doujin-repro/native/*": f"https://raw.githubusercontent.com/bgonz808/foo_doujinstyle/{sha}/native/*"
        }})
        self.assertEqual(mapping(Path("different-root"), sha, True), mapping(Path("other"), sha, True))

    def test_non_commit_identity_is_rejected(self):
        with TemporaryDirectory() as temp:
            for sha in ("dev", "a" * 39, "g" * 40, "a" * 41, "a" * 40 + "?"):
                with self.subTest(sha=sha), self.assertRaises(ValueError):
                    mapping(Path(temp), sha)


if __name__ == "__main__":
    unittest.main()
