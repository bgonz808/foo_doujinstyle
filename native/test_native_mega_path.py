"""Installed/native MEGA routing tests without making external network calls."""
from contextlib import closing
from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZIP_STORED

from doujin import inspect_zip
from native.archive_providers import is_public_mega_file
from native.archive_zip import MediaFireZip
from native.pack_search_worker import worker_sources
from native.reference_publisher import ReferencePublisher
from native.search_worker import download_archive

PUBLIC = "https://mega.nz/file/PublicID#AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"


class NativeMegaPath(unittest.TestCase):
    def test_link_validation(self):
        self.assertTrue(is_public_mega_file(PUBLIC))
        self.assertTrue(is_public_mega_file("https://mega.nz/#!PublicID!" + PUBLIC.rsplit('#', 1)[-1]))
        for bad in ("http://mega.nz/file/PublicID#" + PUBLIC.rsplit('#',1)[-1],
                    "https://mega.nz.evil.test/file/PublicID#" + PUBLIC.rsplit('#',1)[-1],
                    "https://mega.nz@evil.test/file/PublicID#" + PUBLIC.rsplit('#',1)[-1],
                    "https://mega.nz/folder/PublicID#" + PUBLIC.rsplit('#',1)[-1],
                    "https://mega.nz/file/PublicID#short", PUBLIC + "?x=1"):
            self.assertFalse(is_public_mega_file(bad), bad)

    def test_installed_payload_has_no_mega_python_crypto(self):
        members, report = worker_sources(Path(__file__).resolve().parent.parent)
        self.assertNotIn("native/mega_download.py", members)
        self.assertLess(report["python_lines"], 2800)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with ZipFile(root / "worker.pyz", 'w') as zipapp:
                for name, value in members.items():
                    zipapp.writestr(name, value)
            # Verify the installed worker has no provider Python module or MEGA crypto:
            # C++ revalidates every destination and selects the downloader.
            (root / "mega_download.py").write_text('raise RuntimeError("injected")')
            probe = '''import sys
sys.path.insert(0, sys.argv[1]); from native.runtime_contract import ARCHIVE_PROVIDERS
assert not ARCHIVE_PROVIDERS
assert "native.mega_download" not in sys.modules
assert "native.archive_providers" not in sys.modules
'''
            result = subprocess.run([sys.executable, '-c', probe, str(root / "worker.pyz")],
                                    cwd=root, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))

    def test_native_archive_dispatch_preserves_catalog_report(self):
        raw = BytesIO()
        with ZipFile(raw, 'w', compression=ZIP_STORED) as archive:
            archive.writestr('Album/01.flac', b'prefix' * 20)
            archive.writestr('Album/cover.jpg', b'\xff\xd8\xff' + b'j' * 30)
        size = len(raw.getvalue())
        class FakeWorker:
            host_zlib = Path('/fake/zlib1.dll')
            operations = []
            def mega_zip_open(self, url, budget, requests, suffix):
                self.operations.append('mega_zip_open')
                assert url == PUBLIC and budget > 0 and requests > 0
                with ZipFile(BytesIO(raw.getvalue())) as archive:
                    rows = []
                    for info in archive.infolist():
                        rows.append([info.filename.encode().hex(), info.compress_size, info.file_size, info.CRC,
                                     info.header_offset, info.compress_type, info.flag_bits, '', False,
                                     info.filename, 'utf-8', False, ''])
                return {"format": "zip", "size": size, "etag": "", "modified": "",
                        "disposition": "", "filename": "Album.zip", "members": rows,
                        "fetched": 300, "requests": 1}
            def zip_inventory(self, members):
                from native.zip_inventory_reference import project
                return project(members)
            def request(self, db, operation, **params):
                self.operations.append(operation)
                assert operation == 'mega_zip_probes'
                return {"observations": [{"prefix_status": "fixture"}] * len(params['members']),
                        "fetched": 500, "requests": 2}
        worker = FakeWorker()
        class ForbiddenHttp:
            def get(self, *args, **kwargs):
                raise AssertionError('Python MEGA network operation must not execute')
        # The worker capability is compiled only on Windows. Simulate platform
        # selection, but never spawn WinHTTP or access the network in this fixture.
        with patch('doujin.os.name', 'nt'):
            report = inspect_zip(ForbiddenHttp(), PUBLIC, 1_000_000,
                                 directory_only=True, header_probes=True, zip_worker=worker)
        self.assertEqual(report['format'], 'zip')
        self.assertEqual(report['audio_members'], 1)
        self.assertEqual(report['response_filename'], 'Album.zip')
        self.assertEqual(report['archive_http']['body_bytes'], 500)
        self.assertEqual(report['archive_http']['mega_mac'], 'not_verified_for_range_probes')
        self.assertEqual(worker.operations, ['mega_zip_open', 'mega_zip_probes'])

    def test_native_download_is_selected_and_requires_authenticated_result(self):
        class Cache:
            native = True
            path = Path('/tmp/catalog')
            class Publisher:
                def request(self, db, operation, **fields):
                    assert operation == 'archive_download_job' and fields['id'] == 102
                    return {'status': 'saved', 'archive': {'authenticated': True,
                            'path': str(Path(fields['output']) / 'Album.zip'), 'size': 99}}
            publisher = Publisher()
            def listing(self, release_id): return {"url": 'https://doujinstyle.com/?p=page&id=102'}
            def copies_for(self, release_id):
                return [{"copy_id": 1, "host": "mega.nz", "offer": {},
                         "sources": [{"current": 1}]}]
            class DB:
                def execute(self, *args):
                    class Cursor:
                        def fetchone(self): return (PUBLIC,)
                    return Cursor()
            db = DB()
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            self.assertEqual(download_archive(None, Cache(), 102, output, lambda _: None), output / 'Album.zip')
            class Bad(Cache):
                class Publisher(Cache.Publisher):
                    def request(self, db, operation, **fields):
                        return {'status': 'saved', 'archive': {'authenticated': False,
                                'path': '/bad', 'size': 1}}
                publisher = Publisher()
            with self.assertRaises(OSError):
                download_archive(None, Bad(), 102, output, lambda _: None)


if __name__ == '__main__':
    unittest.main()
