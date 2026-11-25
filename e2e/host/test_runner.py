"""Portable tests of the host E2E manifest and template safety contracts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from e2e.host.runner import QualificationError, check_template, read_manifest, relative_path, sha256, template_digest


def manifest(host: Path, component: Path, digest: str) -> dict:
    return {
        "schema_version": 1,
        "project": "foo_doujinstyle",
        "source_commit": "a" * 40,
        "foobar_version": "2.0",
        "template_sha256": digest,
        "host_path": "foobar2000.exe",
        "host_sha256": sha256(host),
        "component_path": "user-components/foo_doujinstyle.dll",
        "component_sha256": sha256(component),
        "robotframework_version": "7.3.1",
        "flaui_version": "5.0.2",
    }


class HostManifestContracts(unittest.TestCase):
    def test_package_binds_installed_trace_dll(self):
        from zipfile import ZipFile
        from e2e.host.run_readiness import verify_package, MEMBER
        with tempfile.TemporaryDirectory() as td:
            package=Path(td)/"test-e2e-trace.fb2k-component"
            raw=b"test DLL"
            with ZipFile(package,"w") as z:
                z.writestr(MEMBER,raw)
            spec={"component_sha256":hashlib.sha256(raw).hexdigest()}
            verify_package(package,sha256(package),spec)
            with self.assertRaises(QualificationError):
                verify_package(package,"a"*64,spec)
            with self.assertRaises(QualificationError):
                verify_package(package,sha256(package),{"component_sha256":"b"*64})

    def test_relative_path_guard(self):
        for value in ("../secret", "/absolute", "C:/evil", "foo\\bar", "foo//bar", "", "foo/./bar"):
            with self.subTest(value=value), self.assertRaises(QualificationError):
                relative_path(value)
        self.assertEqual(relative_path("user-components/component.dll"), Path("user-components/component.dll"))

    def test_template_content_and_component_identity(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "portable_mode_enabled").touch()
            host = root / "foobar2000.exe"
            host.write_bytes(b"synthetic-test-host")
            component = root / "user-components" / "foo_doujinstyle.dll"
            component.parent.mkdir()
            component.write_bytes(b"synthetic-test-component")
            digest, count = template_digest(root)
            self.assertEqual(count, 3)
            spec = manifest(host, component, digest)
            self.assertEqual(check_template(root, spec)["file_count"], 3)
            component.write_bytes(b"synthetic-test-component-mutation")
            with self.assertRaises(QualificationError):
                check_template(root, spec)

    def test_manifest_validation_and_missing_hash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            host = root / "foobar2000.exe"
            comp = root / "component.dll"
            host.write_bytes(b"h")
            comp.write_bytes(b"c")
            doc = manifest(host, comp, "b" * 64)
            file = root / "manifest.json"
            file.write_text(json.dumps(doc), encoding="utf-8")
            self.assertEqual(read_manifest(file)["project"], "foo_doujinstyle")
            doc["component_sha256"] = "0" * 64
            file.write_text(json.dumps(doc), encoding="utf-8")
            with self.assertRaises(QualificationError):
                read_manifest(file)

    def test_template_modification_changes_digest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            file = root / "foobar2000.exe"
            file.write_bytes(b"one")
            old, _ = template_digest(root)
            file.write_bytes(b"two")
            new, _ = template_digest(root)
            self.assertNotEqual(old, new)

    def test_no_symlinks(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "target").write_bytes(b"a")
            link = root / "link"
            try:
                link.symlink_to(root / "target")
            except (OSError, NotImplementedError):
                self.skipTest("Symlink not supported by host")
            with self.assertRaises(QualificationError):
                template_digest(root)


if __name__ == "__main__":
    unittest.main()

class NativeProcessContracts(unittest.TestCase):
    def test_process_filter(self):
        from unittest.mock import patch
        from e2e.host.windows_process import process_ids
        with patch("e2e.host.windows_process._snapshot",return_value=[("foobar2000.exe",123),("python.exe",456)]):
            self.assertEqual(process_ids("FOOBAR2000.EXE"),[123])
    def test_no_host_fails_closed(self):
        from unittest.mock import patch
        from e2e.host.run_readiness import no_host
        with patch("e2e.host.windows_process.process_ids",return_value=[]):
            no_host()
        with patch("e2e.host.windows_process.process_ids",return_value=[123]), self.assertRaises(QualificationError):
            no_host()
        with patch("e2e.host.windows_process.process_ids",side_effect=OSError("inspection failed")), self.assertRaises(OSError):
            no_host()
