"""Manifest derivation from disposable synthetic portable templates."""
import tempfile
from pathlib import Path
import unittest

from e2e.host.manifest_generator import build_spec
from e2e.host.runner import QualificationError, check_template


class ManifestMakerContracts(unittest.TestCase):
    def test_build_hash_pinned_spec(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "portable_mode_enabled").touch()
            (root / "foobar2000.exe").write_bytes(b"fake-host")
            (root / "user-components").mkdir()
            (root / "user-components" / "component.dll").write_bytes(b"fake-component")
            spec = build_spec(root, "user-components/component.dll", "2.25",
                              "a" * 40, "null",
                              {"robotframework":"7.3.1", "robotframework-flaui":"5.0.2"})
            self.assertEqual(spec["output_backend"], "null")
            self.assertEqual(spec["source_commit"], "a"*40)
            self.assertEqual(check_template(root, spec)["tree_sha256"], spec["template_sha256"])
            (root / "foobar2000.exe").write_bytes(b"tampered")
            with self.assertRaises(QualificationError):
                check_template(root, spec)

    def test_rejects_unsafe_paths_or_missing_portable(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with self.assertRaises(QualificationError):
                build_spec(root, "../outside.dll", "2.25", "a"*40,"null",
                           {"robotframework":"7.3.1","robotframework-flaui":"5.0.2"})
            (root / "portable_mode_enabled").touch()
            (root / "foobar2000.exe").write_bytes(b"h")
            with self.assertRaises(QualificationError):
                build_spec(root, "../../outside.dll", "2.25", "a"*40,"null",
                           {"robotframework":"7.3.1","robotframework-flaui":"5.0.2"})


if __name__ == "__main__":
    unittest.main()
