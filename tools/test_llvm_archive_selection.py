import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from tools.llvm_archive_selection import select, verify, materialize, TOOLS


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

    def test_stream_extract(self):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w") as archive:
            for name in self.inventory()[1:]:
                member = tarfile.TarInfo(name)
                payload = b"test"
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
        data.seek(0)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chosen = materialize(data, root)
            verify(root, chosen)
            self.assertFalse((root / "llvm/bin/unused.exe").exists())

    def test_selected_link_rejected(self):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w") as archive:
            member = tarfile.TarInfo("llvm/bin/clang-cl.exe")
            member.type = tarfile.SYMTYPE
            member.linkname = "../outside"
            archive.addfile(member)
        data.seek(0)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "not a regular file"):
                materialize(data, Path(tmp))

    def test_reject_bad_inventory(self):
        for extra in ["llvm/../bad", "/absolute", "llvm/bin/CLANG-CL.EXE", "llvm/bin/evil:stream", "other/a"]:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                select(self.inventory() + [extra])
        with self.assertRaises(ValueError):
            select([x for x in self.inventory() if not x.endswith("clang-tidy.exe")])


if __name__ == "__main__":
    unittest.main()
