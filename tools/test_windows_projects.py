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
        self.assertEqual(len(refs), 3)
        self.assertTrue(all(ref.find(NS + "Project") is not None and
                            (ref.find(NS + "Project").text or "").startswith("{")
                            for ref in refs))


if __name__ == "__main__":
    unittest.main()
