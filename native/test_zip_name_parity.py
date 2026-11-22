"""Differential regression for the native archive-wide ZIP name decoder."""
import json
import os
from pathlib import Path
import subprocess
import unittest

from native.zip_names import resolve_names
from native.archive_zip import decode_members
from native.pack_search_worker import worker_sources


class ZipNameParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.publisher = os.getenv('DOUJIN_NATIVE_WORKER')
        if not cls.publisher:
            raise unittest.SkipTest('native worker path is required for parity')

    @classmethod
    def native(cls, records):
        request = {"operation": "zip_name_resolve", "db": "None", "records": [
            [raw.hex(), flags, extra.hex(), bool(bad)] for raw, flags, extra, bad in records]}
        result = subprocess.run([cls.publisher, '--serve'], input=(json.dumps(request) + '\n').encode(),
                                capture_output=True, check=True, timeout=10)
        reply = result.stdout.decode().splitlines()
        assert reply[0] == 'DoujinStyle catalog 1', reply[:1]
        value = json.loads(reply[1])
        if 'error' in value:
            raise AssertionError(value['error'])
        return value['result']

    def test_archive_wide_encodings(self):
        datasets = [
            [(b'cover.jpg', 0, b'', False), (b'No. 1.mp3', 0, b'', False)],
            [(b'\x82\xa0\x82\xa2\x82\xa4.mp3', 0, b'', False),
             (b'\x82\xa0.flac', 0, b'', False), (b'one.mp3', 0, b'', False)],
            [(b'\x82\xa0.mp3', 0, b'', False)],
            [(b'hello.mp3', 0, '日本語.mp3'.encode(), False)],
            [('日本語.flac'.encode(), 0x800, '別曲.flac'.encode(), False)],
            [(b'\xff.mp3', 0x800, b'', True), (b'\xff.mp3', 0x800, '日本.flac'.encode(), False)],
            [(b'\x82.mp3', 0, b'\xff', True), (b'\xff.flac', 0, b'', False)],
            [(b'\x81\\\x81\\.mp3', 0, b'', False), (b'\x82\xa0.mp3', 0, b'', False)],
        ]
        for records in datasets:
            with self.subTest(records=records):
                expected = list(resolve_names(records))
                actual = self.native(records)
                self.assertEqual([list(row) for row in expected], actual)

    def test_untrusted_length_and_type_limits(self):
        for records in [[[b'overlong' * 600, 0, b'', False]],
                        [[b'abc', -1, b'', False]]]:
            with self.subTest(records=records):
                command = {"operation": "zip_name_resolve", "db": "None", "records": [
                    [raw.hex(), flags, extra.hex(), bad] for raw, flags, extra, bad in records]}
                result = subprocess.run([self.publisher, '--serve'], input=(json.dumps(command) + '\n').encode(),
                                        capture_output=True, check=True, timeout=10)
                line = json.loads(result.stdout.decode().splitlines()[1])
                self.assertIn('error', line)

    def test_packaged_path_no_python_decoder(self):
        source = Path(__file__).resolve().parent.parent
        members, inventory = worker_sources(source)
        self.assertNotIn('native/zip_names.py', members)
        self.assertNotIn('from native.zip_names import', members['native/archive_zip.py'].decode())
        runtime_search = members['native/search_worker.py'].decode()
        for retired in ('def chart_refresh_lock(', 'def refresh_charts(',
                        'def compatibility_run_popular(', 'def compatibility_prefetch_charts('):
            self.assertNotIn(retired, runtime_search)
        self.assertIn('def run_popular(', runtime_search)
        self.assertIn('def prefetch_charts(', runtime_search)
        self.assertEqual(inventory['python_lines'], sum(len(data.splitlines())
                         for key, data in members.items() if key.endswith('.py')))
        names = decode_members([[b'abc'.hex(), 3, 3, 0, 0, 0, 0, '', False, 'abc', 'cp437', False, None]])
        self.assertEqual(next(names).filename, 'abc')
        with self.assertRaises(Exception):
            next(decode_members([[b'abc'.hex(), 3, 3, 0, 0, 0, 0, '', False]]))


if __name__ == '__main__':
    unittest.main()
