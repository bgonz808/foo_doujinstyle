"""Ensure Visual Studio project references and custom targets remain well-formed."""
from pathlib import Path
import xml.etree.ElementTree as ET
import unittest

ROOT = Path(__file__).resolve().parent.parent / "native"
NS = "{http://schemas.microsoft.com/developer/msbuild/2003}"


class WindowsProjectLayoutTests(unittest.TestCase):
    def test_projects(self):
        for filename in ("foo_doujinstyle.vcxproj", "publish_references.vcxproj"):
            with self.subTest(project=filename):
                root = ET.parse(ROOT / filename).getroot()
                self.assertEqual(root.tag, NS + "Project")
                link_targets = [node for node in root.findall(NS + "Target")
                                if node.attrib.get("Name") == "EmitSourceLink"]
                self.assertEqual(len(link_targets), 1)
                self.assertEqual(len(link_targets[0].findall(NS + "Exec")), 1)
                self.assertEqual(len(root.findall(".//" + NS + "ProjectReference/" + NS + "Target")), 0)
        component = ET.parse(ROOT / "foo_doujinstyle.vcxproj").getroot()
        refs = component.findall(".//" + NS + "ProjectReference")
        self.assertEqual(len(refs), 4)
        self.assertTrue(all(ref.find(NS + "Project") is not None and
                            (ref.find(NS + "Project").text or "").startswith("{")
                            for ref in refs))

    def test_sqlite_has_one_shared_compile_owner(self):
        shared = ET.parse(ROOT / "sqlite_shared.vcxproj").getroot()
        self.assertEqual([n.get("Include") for n in shared.findall(".//" + NS + "ClCompile") if n.get("Include")],
                         ["sqlite_amalgamation.c"])
        self.assertEqual(shared.find(".//" + NS + "ConfigurationType").text, "StaticLibrary")
        self.assertEqual(shared.find(".//" + NS + "RuntimeLibrary").text, "MultiThreadedDLL")
        self.assertEqual(shared.find(".//" + NS + "ControlFlowGuard").text, "Guard")
        self.assertEqual(shared.find(".//" + NS + "SDLCheck").text, "true")
        self.assertEqual(shared.find(".//" + NS + "BufferSecurityCheck").text, "true")
        self.assertTrue(any("/Qspectre" in (n.text or "") for n in shared.findall(".//" + NS + "AdditionalOptions")))
        self.assertTrue(any("-mspeculative-load-hardening" in (n.text or "") for n in shared.findall(".//" + NS + "AdditionalOptions")))
        self.assertTrue(any(n.get("Name") == "PrepareSharedSQLite" for n in shared.findall(NS + "Target")))
        guid = shared.find(".//" + NS + "ProjectGuid").text
        for name in ("foo_doujinstyle.vcxproj", "publish_references.vcxproj"):
            root = ET.parse(ROOT / name).getroot()
            self.assertFalse(any(n.get("Include") == "sqlite_amalgamation.c"
                                 for n in root.findall(".//" + NS + "ClCompile")))
            refs = [n for n in root.findall(".//" + NS + "ProjectReference")
                    if n.get("Include") == "sqlite_shared.vcxproj"]
            self.assertEqual(len(refs), 1)
            self.assertEqual(refs[0].find(NS + "Project").text, guid)
            self.assertEqual(refs[0].find(NS + "LinkLibraryDependencies").text, "true")

    def test_msvc_tu_concurrency_retains_debug_info_and_mitigations(self):
        for name in ("foo_doujinstyle.vcxproj", "publish_references.vcxproj"):
            root = ET.parse(ROOT / name).getroot()
            options = root.findall(".//" + NS + "ItemDefinitionGroup/" + NS + "ClCompile/" + NS + "AdditionalOptions")
            self.assertTrue(any("/MP$(DoujinCompilerJobs) /FS" in (e.text or "") and
                                "!= 'ClangCL'" in e.get("Condition", "") for e in options))
            self.assertEqual(root.find(".//" + NS + "DebugInformationFormat").text, "ProgramDatabase")
            self.assertTrue(any("/Qspectre" in (e.text or "") for e in options))
            self.assertEqual(root.find(".//" + NS + "DoujinCompilerJobs").text, "2")
            self.assertTrue(any(e.get("Name") == "ValidateCompilerJobs" for e in root.findall(NS + "Target")))
        root = ET.parse(ROOT / "foo_doujinstyle.vcxproj").getroot()
        self.assertTrue(any("/LTCG" in (e.text or "") and "!= 'ClangCL'" in e.get("Condition", "")
                            for e in root.findall(".//" + NS + "Link/" + NS + "AdditionalOptions")))

    def test_portable_sqlite_is_compiled_once(self):
        script = (ROOT.parent / "tools/test_portable.sh").read_text(encoding="utf-8")
        self.assertEqual(script.count("native/sqlite_amalgamation.c"), 1)
        self.assertIn('"$CC" -shared build-linux/sqlite3.o -o build-linux/libsqlite3.so', script)

if __name__ == "__main__":
    unittest.main()
