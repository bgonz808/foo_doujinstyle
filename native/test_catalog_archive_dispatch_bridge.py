"""Installed-worker boundary: Python may present, never select/fetch/commit archives."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from native.search_worker import (dispatch_archives, present_archive_event,
    repair_release, run_idle_layers, schedule_index, IDLE_TAGS)
from native.reference_publisher import NativeCatalogWorkerUnavailable


class NativePeer:
    host_zlib = Path('/host/zlib1.dll')
    def __init__(self, *, malformed=False, fail_403=False):
        self.calls = []
        self.saved = set()
        self.malformed = malformed
        self.fail_403 = fail_403
        self.copy = 10

    def request(self, path, op, **params):
        self.calls.append(op)
        if op == 'catalog_index_dispatch':
            entry = {'id': 1, 'had_observation': False, 'offers': [], 'error': None,
                     'statuses': [], 'copies': [], 'rate_limited': False}
            params['on_event'](entry)
            return {'offers_count': 1, 'initial_archives_count': 1, 'cancelled': False,
                    'rate_limited': False, 'events': [entry],
                    'archives': [{'id': 10, 'share_url': 'https://www.mediafire.com/file/abc/x.zip/file'}]}
        if op == 'catalog_idle_plan':
            return {'details': [], 'links': [], 'archives': [
                {'id': 10, 'share_url': 'https://www.mediafire.com/file/abc/x.zip/file',
                 'ids': [1], 'headers': True, 'cover': False}]}
        if op == 'archive_download_plan':
            return {'status': 'ready', 'copy': {'id': self.copy,
                'url': 'https://www.mediafire.com/file/abc/x.zip/file' if self.copy == 10
                else 'https://mega.nz/file/public#key'}}
        if op == 'catalog_archive_dispatch':
            self.assert_native_arguments(params)
            copy = params['jobs'][0]['id']
            failure = self.fail_403 and copy == 10
            full = {'id': copy, 'ids': [1], 'ok': not failure,
                    'error': 'HTTP 403' if failure else None,
                    'format': None if failure else 'zip',
                    'audio_members': None if failure else 2,
                    'archive_http': None if failure else {'body_bytes': 123},
                    'cover_payload': None}
            self.saved.add(copy)  # Native SQLite commit BEFORE notification.
            params['on_event'](full)
            result = {key: value for key, value in full.items() if key != 'cover_payload'}
            if self.malformed:
                result['id'] = 99
            return {'events': [result], 'attempted': 1, 'selected': 1,
                    'cancelled': False, 'rate_limited': False}
        if op == 'catalog_offer_batch':
            self.copy = 11  # fresh known offer selected by native code
            return {'results': [{'id': 1, 'offers': [], 'error': None}]}
        raise AssertionError(f'Unexpected Python-side operation {op}')

    @staticmethod
    def assert_native_arguments(params):
        assert params['workers'] in (1, 2, 3)
        assert params['host_zlib'] == '/host/zlib1.dll'
        assert 'on_event' in params


class NativeCache:
    native = True
    path = 'not-a-real-database'
    def __init__(self, publisher):
        self.publisher = publisher
    def listing(self, id):
        assert id == 1
        return {'id': 1}
    def put_archive(self, *args, **kwargs):
        raise AssertionError('Python SQLite archive writer entered')
    def releases_for_copy(self, *args, **kwargs):
        raise AssertionError('Python copy resolver entered')


class ArchiveAdapterTests(unittest.TestCase):
    def test_foreground_has_no_python_network_archive_or_writer(self):
        peer = NativePeer()
        cache = NativeCache(peer)
        emitted = []
        with patch('native.search_worker.os.name', 'nt'), \
             patch('native.search_worker.probe_archive', side_effect=AssertionError('Python probe entered')):
            schedule_index(None, cache, {1: {'id': 1}}, emitted.append, None, False,
                           lambda event: self.assertIn(event['id'], peer.saved), workers=2)
        self.assertEqual(peer.calls, ['catalog_index_dispatch', 'catalog_archive_dispatch'])
        self.assertTrue(any('Offer response committed' in line for line in emitted))

    def test_malformed_terminal_does_not_replay_native_archive(self):
        peer = NativePeer(malformed=True)
        cache = NativeCache(peer)
        with self.assertRaisesRegex(NativeCatalogWorkerUnavailable, 'Incomplete'):
            dispatch_archives(cache, [{'id': 10}], [1], lambda *_:None,
                              lambda event: self.assertIn(event['id'], peer.saved))
        self.assertEqual(peer.calls, ['catalog_archive_dispatch'])

    def test_idle_archive_is_selected_and_paced_natively(self):
        peer = NativePeer()
        cache = NativeCache(peer)
        updated = []
        with patch('native.search_worker.export', side_effect=lambda *a, **k: updated.append(a[2]) or {'written': 1}), \
             patch('native.search_worker.inspect_archives', side_effect=AssertionError('Legacy indexer entered')):
            run_idle_layers(None, cache, IDLE_TAGS, Path('/irrelevant'), None,
                            lambda *_:None, slot=lambda t: t)
        self.assertEqual(peer.calls, ['catalog_idle_plan', 'catalog_archive_dispatch'])
        self.assertEqual(updated, [[1]])

    def test_repair_preserves_selected_copy_and_403_fallback(self):
        peer = NativePeer(fail_403=True)
        cache = NativeCache(peer)
        names = []
        def fake_export(_cache, _path, _ids, **kwargs):
            self.assertEqual(kwargs['copy_id'], 11)
            kwargs['on_reference'](1, 'ref-1.djt')
            return {'written': 1}
        with patch('native.search_worker.export', side_effect=fake_export), \
             patch('native.search_worker.probe_archive', side_effect=AssertionError('Python probe entered')):
            repair_release(None, cache, 1, Path('/irrelevant'), names.append)
        self.assertEqual(peer.calls, ['archive_download_plan', 'catalog_archive_dispatch',
                                      'catalog_offer_batch', 'archive_download_plan', 'catalog_archive_dispatch'])
        self.assertIn('\x1c1\tref-1.djt', names)

    def test_artwork_written_only_from_committed_event(self):
        with tempfile.TemporaryDirectory() as folder:
            totals = dict.fromkeys(('archives_attempted','archives_indexed','archive_body_bytes','references_written'),0)
            present_archive_event({'id': 10,'ids':[1,2],'ok':True,'error':None,
                'format':'zip','audio_members':3,'archive_http':{'body_bytes':5},
                'cover_payload':'ffd8ff'},totals,lambda *_:None,Path(folder))
            self.assertEqual((Path(folder)/'1-archive.img').read_bytes(), bytes.fromhex('ffd8ff'))
            self.assertEqual((Path(folder)/'2-archive.img').read_bytes(), bytes.fromhex('ffd8ff'))
            self.assertEqual(totals['archives_indexed'],1)


if __name__=='__main__':
    unittest.main()
