import unittest
from pathlib import Path
from tools.toolchain_matrix import compile_flags, library_args, link_args, summarize

class ToolchainMatrixTests(unittest.TestCase):
    def test_frozen_relative_archive_control(self):
        self.assertEqual(library_args("native-relative",Path("a")), library_args("native-relative",Path("b")))
        self.assertNotEqual(library_args("native-absolute",Path("a")), library_args("native-absolute",Path("b")))
        self.assertIn("rcsD",library_args("llvm-ar",Path("a")))

    def test_ordinary_coff_and_full_symbols(self):
        for c in ("msvc","clang"):
            flags=compile_flags(c,Path("a"),"entry.c")
            self.assertIn("/Z7",flags)
            self.assertIn("/GS",flags)
            self.assertNotIn("/GL",flags)
        for l in ("msvc","lld"):
            self.assertIn("/debug:full",link_args(l))
            self.assertIn("/pdbaltpath:fixture.pdb",link_args(l))

    def test_incomplete_samples_not_qualified(self):
        self.assertEqual(len(summarize([])),16)
        self.assertFalse(summarize([])["msvc/native-relative/msvc"]["complete"])
