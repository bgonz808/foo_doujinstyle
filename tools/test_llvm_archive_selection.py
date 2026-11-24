import tempfile
import unittest
from pathlib import Path
from tools.llvm_archive_selection import select, verify, TOOLS


class LLVMArchiveSelectionTests(unittest.TestCase):
    def inventory(self):
        return ["llvm/"] + ["llvm/bin/" + name for name in sorted(TOOLS)] + [
            "llvm/bin/runtime.dll", "llvm/bin/unused.exe", "llvm/lib/clang/23/include/stddef.h",
            "llvm/lib/clang/23/include/arm/foo.h", "llvm/lib/clang/23/lib/large.lib",
            "llvm/share/doc/readme.txt"]

    def test_select_and_verify_exact_inventory(self):
        chosen = select(self.inventory())
        self.assertEqual(len(chosen), 7)
        self.assertNotIn("llvm/bin/unused.exe", chosen)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in chosen:
                p = root / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"test")
            verify(root, chosen)
            extra = root / "extra"
            extra.write_bytes(b"unexpected")
            with self.assertRaisesRegex(ValueError, "inventory mismatch"):
                verify(root, chosen)

    def test_reject_bad_inventory(self):
        for extra in ["llvm/../bad", "/absolute", "llvm/bin/CLANG-CL.EXE", "llvm/bin/evil:stream", "other/a"]:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                select(self.inventory() + [extra])
        with self.assertRaises(ValueError):
            select([x for x in self.inventory() if not x.endswith("clang-tidy.exe")])


if __name__ == "__main__":
    unittest.main()
