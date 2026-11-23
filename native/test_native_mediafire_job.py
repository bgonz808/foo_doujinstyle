"""Native provider dispatch tests: no external requests, no Python streaming."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from native.search_worker import download_archive
from native.pack_search_worker import worker_sources

SHARE = 'https://www.mediafire.com/file/abcdef/fixture.zip/file'

class NativeMediaFireJobTest(unittest.TestCase):
    def test_native_mediafire_uses_compiled_download_and_never_python_http(self):
        class Publisher:
            calls = []
            def request(self, db, operation, **fields):
                self.calls.append(operation)
                assert operation == 'archive_download_job'
                assert fields['id'] == 14 and fields['output']
                assert fields['referrer'].startswith('https://doujinstyle.com/')
                return {'status': 'saved', 'archive': {'path': str(Path(fields['output']) / 'Album.zip'),
                        'size': 1024, 'authenticated': False,
                        'integrity': 'transport_and_length_only'}}
        class Cache:
            native = True
            path = Path('/tmp/catalog')
            publisher = Publisher()
            def listing(self, release_id):
                assert release_id == 14
                return {'url': 'https://doujinstyle.com/?p=page&id=14'}
        class ForbiddenHttp:
            def open_archive(self, *args): raise AssertionError('Python archive download must not run')
            def get(self, *args): raise AssertionError('Python MediaFire resolver must not run')
        with tempfile.TemporaryDirectory() as folder:
            lines = []
            saved = download_archive(ForbiddenHttp(), Cache(), 14, Path(folder), lines.append)
            self.assertEqual(saved.name, 'Album.zip')
            self.assertIn('no whole-file MAC', '\n'.join(lines))
            self.assertEqual(Cache.publisher.calls, ['archive_download_job'])

    def test_native_result_must_expose_transport_only_integrity(self):
        class Cache:
            native = True
            path = Path('/tmp/catalog')
            def listing(self, _): return {'url':'https://doujinstyle.com/a'}
            class Publisher:
                def request(self, *_a, **_kw):
                    return {'status': 'saved', 'archive': {'path': '/tmp/success.zip', 'size': 1,
                            'authenticated': True, 'integrity': 'transport_and_length_only'}}
            publisher = Publisher()
        with self.assertRaisesRegex(OSError, 'contradictory'):
            download_archive(None, Cache(), 12, Path('/tmp'), lambda _: None)

    def test_missing_offer_is_committed_by_native_then_download(self):
        class Publisher:
            calls = []
            def request(self, db, operation, **fields):
                self.calls.append((operation, fields))
                if operation == 'catalog_offer_batch':
                    assert fields['ids'] == [9] and fields['all_forms'] is True
                    return {'cancelled': False, 'results': [{'id':9, 'had_observation':True,
                            'offers':[['doujinstyle', {'host':'www.mediafire.com'}, SHARE]],
                            'error':None, 'copies':[1]}]}
                if operation == 'archive_download_job':
                    if len(self.calls) == 1:
                        return {'status':'no_offer'}
                    return {'status':'saved', 'archive': {'path':'/tmp/album.zip', 'size':33,
                            'authenticated': False, 'integrity':'transport_and_length_only'}}
                raise AssertionError(operation)
        class Cache:
            native = True
            path = Path('/tmp/catalog')
            publisher = Publisher()
            def listing(self, _): return {'id': 9, 'url': 'https://doujinstyle.com/?p=page&id=9'}
            def put_offers(self, *a, **kw):
                raise AssertionError('Native must own catalog commit')
        cache = Cache()
        with patch('native.search_worker.fetch_offer', side_effect=AssertionError('Python POST replay')):
            self.assertEqual(download_archive(object(), cache, 9, Path('/tmp'), lambda _:None), Path('/tmp/album.zip'))
        self.assertEqual([op for op, _ in cache.publisher.calls],
                         ['archive_download_job', 'catalog_offer_batch', 'archive_download_job'])

    def test_uncertain_post_failure_is_not_replayed(self):
        class Publisher:
            calls = []
            def request(self, db, operation, **fields):
                self.calls.append(operation)
                if operation == 'archive_download_job': return {'status': 'no_offer'}
                if operation == 'catalog_offer_batch':
                    return {'cancelled': False, 'results': [{'id':9, 'had_observation':False,
                            'offers':[], 'error':'POST response lost', 'copies':[]}]}
                raise AssertionError(operation)
        class Cache:
            native = True
            path = Path('/tmp/catalog')
            publisher = Publisher()
            def listing(self, _): return {'id':9, 'url':'https://doujinstyle.com/?p=page&type=1&id=9'}
            def put_offers(self, *a, **kw): raise AssertionError('Native owns commit')
        cache = Cache()
        with patch('native.search_worker.fetch_offer', side_effect=AssertionError('Python POST replay')):
            with self.assertRaisesRegex(ValueError, 'POST response lost'):
                download_archive(None, cache, 9, Path('/tmp'), lambda _: None)
        self.assertEqual(cache.publisher.calls, ['archive_download_job', 'catalog_offer_batch'])

    def test_legacy_streaming_is_absent_from_installed_payload(self):
        members, _ = worker_sources(Path(__file__).resolve().parent.parent)
        worker = members['native/search_worker.py'].decode()
        self.assertNotIn('def compatibility_download_archive(', worker)
        self.assertNotIn('def save_archive(', worker)
        self.assertNotIn('archive_download_job', worker)
        # Native operation must remain reachable even though its Python
        # coordinator is now source-only and absent from the zipapp.
        implementation = (Path(__file__).resolve().parent / 'publish_references.cpp').read_text()
        self.assertIn('if (operation == "archive_download_job")', implementation)
        publisher = members['native/reference_publisher.py'].decode()
        self.assertNotIn('def mediafire_download_archive(', publisher)
        self.assertNotIn('def mega_download_archive(', publisher)

if __name__ == '__main__': unittest.main()
