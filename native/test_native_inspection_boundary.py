"""Native archive and asset boundary tests; Python reference remains source-only."""
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from native.pack_search_worker import worker_sources
from native.search_worker import SearchHttp, probe_archive, cache_cover, cache_brand
from native.idle_backfill import report_robots_advisory


class NativeZipBoundary(unittest.TestCase):
    class Peer:
        host_zlib = Path('/some/zlib1.dll')
        def __init__(self, error=False):
            self.error = error
            self.calls = []
        def request(self, db, op, **kwargs):
            self.calls.append((op, kwargs))
            if self.error: raise OSError('simulated native ZIP failure')
            assert op == 'archive_inspect_job'
            return {'format': 'zip', 'audio_members': 1, 'inventory': [], 'artwork': [],
                    'audio_probes': {'x.wav': {'metadata': {'codec': 'PCM'}}},
                    'cover_payload': 'ffd8ffabcd', 'archive_http': {'body_bytes': 3}}

    def test_packaged_worker_has_no_legacy_archive_reader(self):
        root = Path(__file__).resolve().parent.parent
        members, report = worker_sources(root)
        self.assertNotIn('native/archive_zip.py', members)
        self.assertNotIn('doujin.py', members)
        self.assertNotIn('class MediaFireZip', b''.join(members.values()).decode(errors='replace'))
        self.assertLess(report['python_lines'], 2000)
        with tempfile.TemporaryDirectory() as folder:
            app = Path(folder) / 'worker.pyz'
            with ZipFile(app, 'w') as archive:
                for name, data in members.items(): archive.writestr(name, data)
            (Path(folder) / 'archive_zip.py').write_text('raise RuntimeError("injected archive adapter")')
            result = subprocess.run([os.sys.executable, '-I', '-c',
                'import sys;sys.path.insert(0,sys.argv[1]);import native.search_worker;'
                'assert "doujin" not in sys.modules;'
                'assert not hasattr(native.search_worker.SearchHttp, "get")',
                str(app)], cwd=folder, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_windows_path_single_native_archive_job_and_cover(self):
        peer = self.Peer()
        job = {'id': 50, 'share_url': 'https://mega.nz/file/opaque#key'}
        with patch('native.search_worker.os.name', 'nt'):
            result = probe_archive(SearchHttp(), job, [101], {101: {'url': 'https://doujinstyle.com/?p=page&type=1&id=101'}},
                                   True, True, zip_worker=peer)
        self.assertEqual(result[0:2], (50, [101]))
        self.assertEqual(result[-1], None)
        self.assertEqual(result[3], [bytes.fromhex('ffd8ffabcd')])
        self.assertNotIn('cover_payload', result[2])
        self.assertEqual([x[0] for x in peer.calls], ['archive_inspect_job'])
        self.assertGreater(peer.calls[0][1]['budget'], 0)

    def test_uncertain_native_failure_is_not_replayed_in_python(self):
        peer = self.Peer(error=True)
        with patch('native.search_worker.os.name', 'nt'):
            result = probe_archive(SearchHttp(), {'id': 7, 'share_url': 'https://www.mediafire.com/file/X/a.zip'},
                                   [9], {9: {'url': 'https://doujinstyle.com/?p=page&type=1&id=9'}},
                                   True, False, zip_worker=peer)
        self.assertIsInstance(result[-1], OSError)
        self.assertEqual([op for op,_ in peer.calls], ['archive_inspect_job'])


class NativeAssetBoundary(unittest.TestCase):
    class Peer:
        def __init__(self, status=200, body=b'\xff\xd8\xffABC'):
            self.status, self.body, self.calls = status, body, []
        def request(self, db, op, **kwargs):
            self.calls.append((op, kwargs))
            assert op == 'site_resource'
            return {'status': self.status, 'body': self.body.hex()}

    def test_cover_reads_native_only_and_reuses_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            peer = self.Peer()
            root = Path(folder)
            self.assertTrue(cache_cover(root, 123, peer))
            self.assertEqual((root/'123.jpg').read_bytes(), peer.body)
            self.assertTrue(cache_cover(root, 123, peer))
            self.assertEqual(len(peer.calls), 1)

    def test_site_icon_magic_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            peer = self.Peer(body=b'\x89PNG\r\n\x1a\n' + b'C')
            self.assertTrue(cache_brand(Path(folder), 'https://doujinstyle.com/favicon.png', peer))
            self.assertEqual(len(peer.calls), 1)

    def test_robots_native_result_and_advisory(self):
        peer = self.Peer(body=b'User-agent: *\nDisallow: /\n')
        text = []
        report_robots_advisory(None, 'https://doujinstyle.com/?p=home&page=0', text.append, peer)
        self.assertTrue(any('excludes' in line for line in text))
        self.assertEqual([op for op,_ in peer.calls], ['site_resource'])

if __name__ == '__main__': unittest.main()
