import tempfile
from pathlib import Path
import unittest
from zipfile import ZipFile
from e2e.host.prepare_guest import install_copy
from e2e.host.run_readiness import MEMBER
from e2e.host.runner import sha256,template_digest,QualificationError

class PreparationContracts(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.base=self.root/"base"
        self.base.mkdir()
        (self.base/"portable_mode_enabled").touch()
        (self.base/"foobar2000.exe").write_bytes(b"synthetic host")
        self.tree=template_digest(self.base)[0]
        self.host=sha256(self.base/"foobar2000.exe")
        self.package=self.root/"test-e2e-trace.fb2k-component"
        with ZipFile(self.package,"w") as z:
            z.writestr(MEMBER,b"synthetic trace DLL")
        self.dest=self.root/"prepared"
        self.path="user-components/foo_doujinstyle/foo_doujinstyle.dll"

    def install(self,**kw):
        args=dict(base=self.base,dest=self.dest,package=self.package,package_sha=sha256(self.package),
                  component_path=self.path,expected_tree=self.tree,expected_host=self.host)
        args.update(kw)
        return install_copy(**args)

    def test_clean_copy_and_exact_dll(self):
        result=self.install()
        self.assertEqual(result,sha256(self.dest/self.path))
        self.assertEqual((self.dest/self.path).read_bytes(),b"synthetic trace DLL")
        self.assertEqual(template_digest(self.base)[0],self.tree)
        self.assertFalse((self.base/self.path).exists())
        with self.assertRaises(QualificationError):
            self.install()

    def test_reject_mismatch_or_path(self):
        for kw in ({"package_sha":"a"*64},{"expected_tree":"b"*64},{"expected_host":"c"*64},
                   {"component_path":"../foo_doujinstyle.dll"},{"component_path":"foo_doujinstyle.dll"},
                   {"dest":self.base/"nested"}):
            with self.subTest(kw=kw),self.assertRaises(QualificationError):
                self.install(**kw)
        self.assertFalse(self.dest.exists())

    def test_reject_existing_component_in_base(self):
        target=self.base/self.path
        target.parent.mkdir(parents=True)
        target.write_bytes(b"old")
        with self.assertRaises(QualificationError):
            self.install(expected_tree=template_digest(self.base)[0])

    def test_reject_extra_or_duplicate_dll(self):
        for name in ("evil.dll",MEMBER.upper()):
            with ZipFile(self.package,"w") as z:
                z.writestr(MEMBER,b"one")
                z.writestr(name,b"two")
            with self.assertRaises(QualificationError):
                self.install()
        self.assertFalse(self.dest.exists())
