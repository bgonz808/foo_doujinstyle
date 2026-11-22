"""Compiled C++ archive-inspection parity against the source-only Python ZIP reference."""
import io
import json
import os
from pathlib import Path
import random
import subprocess
import tempfile
import unittest
from zipfile import ZipFile, ZIP_STORED, ZIP_DEFLATED

from native.zip_inventory_reference import project


class ArchiveInspectionNativeParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.getenv('DOUJINSTYLE_TEST_ARCHIVE_INSPECTION')
        if not path or not Path(path).is_file():
            raise unittest.SkipTest('Compile native/test_archive_inspection.cpp with -lz first')
        cls.executable = path

    def native(self, contents):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'archive.zip'
            path.write_bytes(contents)
            completed = subprocess.run([self.executable, str(path)], capture_output=True, timeout=10)
            self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors='replace'))
            return json.loads(completed.stdout)

    def test_native_projection_matches_reference(self):
        rnd = random.Random(86013)
        for trial in range(45):
            data = io.BytesIO()
            with ZipFile(data, 'w') as archive:
                entries = rnd.randrange(1, 22)
                for index in range(entries):
                    extension = rnd.choice(('.flac', '.wav', '.ogg', '.mp3', '.jpg', '.nfo', '.txt', '.bin'))
                    folder = 'Album/' if rnd.random() > 0.3 else ''
                    name = f'{folder}{index:02}{extension}'
                    archive.writestr(name, rnd.randbytes(rnd.randrange(1, 7000)),
                                     compress_type=rnd.choice((ZIP_STORED, ZIP_DEFLATED)))
            with self.subTest(trial=trial):
                native = self.native(data.getvalue())
                with ZipFile(io.BytesIO(data.getvalue())) as archive:
                    expected = project(archive.infolist())
                for key in ('members', 'audio_members', 'audio', 'inventory', 'member_types',
                            'unknown_suffixes', 'sidecar_candidates', 'artwork'):
                    if key == 'sidecar_candidates':
                        names = [item.filename for item in ZipFile(io.BytesIO(data.getvalue())).infolist()]
                        expected_value = [names[i] for i in expected[key]]
                    else:
                        expected_value = expected[key]
                    self.assertEqual(native[key], expected_value, key)
                self.assertGreaterEqual(native['archive_http']['requests'], 1)

    def test_cover_choice_and_audio_prefix(self):
        raw = io.BytesIO()
        with ZipFile(raw, 'w') as archive:
            archive.writestr('cover.jpg', b'\xff\xd8\xff' + bytes(10))
            archive.writestr('Disc/03.wav', b'RIFF' + bytes(200))
        result = self.native(raw.getvalue())
        self.assertEqual(result['cached_front_cover'], 'cover.jpg')
        self.assertEqual(bytes.fromhex(result['cover_payload']), b'\xff\xd8\xff' + bytes(10))
        self.assertIn('prefix_status', result['audio_probes']['Disc/03.wav'])

    def test_rejects_corrupt_central_directory(self):
        raw = io.BytesIO()
        with ZipFile(raw, 'w') as archive:
            archive.writestr('a.flac', b'abc' * 100, compress_type=ZIP_DEFLATED)
        original = raw.getvalue()
        self.assertEqual(self.native(original)['audio_members'], 1)
        self.assertIn(self.native(original[:-10])['format'], ('zip_invalid_or_incomplete', 'unknown'))
        self.assertIn(self.native(original.replace(b'PK\x01\x02', b'PK\xff\xff'))['format'],
                      ('zip_invalid_or_incomplete', 'unknown'))


if __name__ == '__main__': unittest.main()
