import tempfile
from pathlib import Path
import unittest
from zipfile import ZipFile
from tools.build_identity import identify, header, source_digest, write_if_changed
from tools.package_component import pack


class IdentityContract(unittest.TestCase):
    def test_git_free_fallback_is_deterministic_and_sensitive_to_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'VERSION').write_text('0.0.65\n')
            (root / 'a.cpp').write_text('hello\n')
            first = identify(root)
            self.assertEqual(first['source_kind'], 'snapshot')
            self.assertRegex(first['version'], r'^0\.0\.65-dev\.0\+src\.[0-9a-f]{12}$')
            self.assertEqual(first, identify(root))
            (root / 'a.cpp').write_text('goodbye\n')
            self.assertNotEqual(first['source_sha256'], identify(root)['source_sha256'])
            self.assertEqual(header(first), header(first))

    def test_generated_identity_does_not_touch_identical_header(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'generated' / 'identity.hpp'
            self.assertTrue(write_if_changed(path, b'first\n'))
            os.utime(path, ns=(1000000000, 1000000000))
            before = path.stat().st_mtime_ns
            self.assertFalse(write_if_changed(path, b'first\n'))
            self.assertEqual(path.stat().st_mtime_ns, before)
            self.assertTrue(write_if_changed(path, b'second\n'))
            self.assertEqual(path.read_bytes(), b'second\n')

    def test_ignored_build_outputs_and_deleted_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'VERSION').write_text('0.0.65\n')
            base = source_digest(root, False)[0]
            (root / 'build-linux').mkdir()
            (root / 'build-linux' / 'junk.obj').write_bytes(b'junk')
            (root / 'junk.plist').write_bytes(b'junk')
            self.assertEqual(base, source_digest(root, False)[0])

    def test_content_framing_and_case_collisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'VERSION').write_text('0.0.65\n')
            (root / 'Case.cpp').write_bytes(b'a')
            (root / 'case.cpp').write_bytes(b'b')
            with self.assertRaises(ValueError):
                source_digest(root, False)

    def test_package_is_byte_reproducible_and_safely_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            content = root / 'x'; content.write_bytes(b'abc' * 100)
            first = pack(root / 'first.zip', [('x64/foo.dll', content)])
            second = pack(root / 'second.zip', [('x64/foo.dll', content)])
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with ZipFile(first) as archive:
                self.assertEqual(archive.namelist(), ['x64/foo.dll'])
                self.assertEqual(archive.infolist()[0].date_time, (1980, 1, 1, 0, 0, 0))
            with self.assertRaises(ValueError):
                pack(root / 'bad.zip', [('../secrets', content)])


if __name__ == '__main__':
    unittest.main()

class GitIdentityContract(unittest.TestCase):
    def test_clean_and_dirty_git_identity(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(['git', 'init', '-q', '-b', 'main', str(root)], check=True)
            subprocess.run(['git', '-C', str(root), 'config', 'user.name', 'Test'], check=True)
            subprocess.run(['git', '-C', str(root), 'config', 'user.email', 'test@example.invalid'], check=True)
            (root / 'VERSION').write_text('0.0.65\n')
            (root / 'foo.cpp').write_text('hello\n')
            subprocess.run(['git','-C',str(root),'add','.'],check=True)
            subprocess.run(['git','-C',str(root),'commit','-qm','baseline'],check=True)
            clean = identify(root)
            self.assertFalse(clean['dirty'])
            self.assertIn('-dev.1+g', clean['version'])
            self.assertEqual(len(clean['git_commit']),40)
            (root / 'foo.cpp').write_text('changed\n')
            dirty = identify(root)
            self.assertTrue(dirty['dirty'])
            self.assertTrue(dirty['version'].endswith('.dirty'))
            self.assertNotEqual(clean['source_sha256'], dirty['source_sha256'])
            (root / 'other.cpp').write_text('new\n')
            newer = identify(root)
            self.assertNotEqual(dirty['source_sha256'], newer['source_sha256'])
            (root / 'build-linux').mkdir()
            (root / 'build-linux' / 'foo.o').write_text('binary')
            self.assertEqual(newer['source_sha256'], identify(root)['source_sha256'])
