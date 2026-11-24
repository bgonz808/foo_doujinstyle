"""Portable contracts for Windows compiler-log analysis; no fake Windows pass."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "native"))
from analyze_windows import normalize_command, tracking_records, project_units, NATIVE


class WindowsAnalysisTests(unittest.TestCase):
    def test_tracking_group_and_missing_command(self):
        self.assertEqual(list(tracking_records("^A.cpp|B.cpp\n/c /DWIN32\n^C.cpp\n/c /DWIN32\n")),
                         [(["A.cpp", "B.cpp"], "/c /DWIN32"), (["C.cpp"], "/c /DWIN32")])
        for text in ("^A.cpp\n", "/c\n", "^A.cpp\n^B.cpp\n/c\n"):
            with self.assertRaises(ValueError):
                list(tracking_records(text))

    def test_frontend_flags_preserved(self):
        argv = ["/c", "/std:c++20", "/D_WINDOWS", "/D_WINDLL", "/FIfoo.h",
                "/external:IC:\\SDK Path", "/external:W0", "/Qspectre", "/Foout.obj",
                "/Fd", "out.pdb", "/MD", "/EHsc", "/WX", "C:\\a.cpp", "C:\\b.cpp"]
        result, removed = normalize_command(argv, ["C:\\a.cpp", "C:\\b.cpp"], "C:\\a.cpp",
                                            "clang-cl.exe", "x86_64-pc-windows-msvc", ["C:\\MSVC"])
        for flag in ("/D_WINDOWS", "/D_WINDLL", "/FIfoo.h", "/MD", "/EHsc", "/WX",
                     "/imsvcC:\\SDK Path", "/imsvc", "C:\\MSVC"):
            self.assertIn(flag, result)
        self.assertNotIn("C:\\b.cpp", result)
        self.assertNotIn("out.pdb", result)
        self.assertIn("/Qspectre", removed)
        self.assertEqual(result[-1], "C:\\a.cpp")

    def test_response_file_and_missing_defines_rejected(self):
        for argv in (["/std:c++20", "/DWIN32", "@args.rsp"], ["/std:c++20"]):
            with self.assertRaises(ValueError):
                normalize_command(argv, ["a.cpp"], "a.cpp", "clang-cl.exe", "target", [])

    def test_production_inventory(self):
        self.assertEqual({p.name for p in project_units(NATIVE / "foo_doujinstyle.vcxproj")},
                         {"component.cpp", "descriptor.cpp", "search.cpp"})
        self.assertEqual({p.name for p in project_units(NATIVE / "publish_references.vcxproj")},
                         {"publish_references.cpp", "descriptor.cpp"})


if __name__ == "__main__":
    unittest.main()
