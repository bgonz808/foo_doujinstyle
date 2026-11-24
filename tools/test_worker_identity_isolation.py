"""Keep volatile identity in its own compiled unit without losing attribution."""
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent

class WorkerIdentityIsolationTests(unittest.TestCase):
    def test_publisher_does_not_depend_on_generated_identity(self):
        source = (ROOT / "native/publish_references.cpp").read_text(encoding="utf-8")
        self.assertNotIn("doujinstyle_build_version.hpp", source)
        self.assertIn("doujin::print_worker_build_identity();", source)
        identity = (ROOT / "native/worker_build_identity.cpp").read_text(encoding="utf-8")
        for token in ("doujinstyle_build_version.hpp", "DOUJIN_FULL_VERSION", "DOUJIN_SOURCE_SHA256", "DOUJIN_GIT_COMMIT"):
            self.assertIn(token, identity)

    def test_all_existing_build_paths_compile_identity_unit(self):
        project = ET.parse(ROOT / "native/publish_references.vcxproj")
        self.assertEqual(sum(n.get("Include") == "worker_build_identity.cpp"
                             for n in project.findall(".//{*}ClCompile")), 1)
        for path in ("tools/test_portable.sh", "native/build_cross.py", "native/analyze_native.py"):
            self.assertIn("worker_build_identity.cpp", (ROOT / path).read_text(encoding="utf-8"))
