"""Python-to-native boundary tests: no Python POST replay and commit-before-notify."""
import unittest
from unittest.mock import patch
from native.search_worker import schedule_index, run_idle_layers, IDLE_LINKS


class Publisher:
    def __init__(self, fail=False):
        self.calls = []
        self.committed = set()
        self.fail = fail

    def request(self, path, op, **kwargs):
        self.calls.append((op, kwargs))
        if op == 'catalog_index_plan':
            return {'offers': [1, 2] if kwargs.get('offers', True) else [], 'archives': []}
        if op == 'catalog_idle_plan':
            return {'details': [], 'links': [{'id': 1, 'listing': {'id': 1}}], 'archives': []}
        if op == 'catalog_index_dispatch':
            if self.fail:
                raise OSError('lost native reply after uncertain POST')
            events = []
            for release_id in kwargs['ids']:
                self.committed.add(release_id)
                entry = {'id': release_id, 'had_observation': True, 'offers': [
                    ['doujinstyle', {'host': 'www.mediafire.com'},
                     'https://www.mediafire.com/file/ABC/file.zip']],
                    'error': None, 'copies': [release_id], 'statuses': ['Found www.mediafire.com offer']}
                events.append(entry)
                kwargs['on_event'](entry)
            return {'cancelled': False, 'rate_limited': False,
                    'offers_count': len(kwargs['ids']), 'initial_archives_count': 0,
                    'events': events, 'archives': []}
        if op == 'catalog_offer_batch':
            if self.fail:
                raise OSError('lost native reply after uncertain POST')
            self.committed.update(kwargs['ids'])
            return {'cancelled': False, 'results': [
                {'id': id, 'had_observation': True, 'offers': [
                    ['doujinstyle', {'host': 'www.mediafire.com'},
                     'https://www.mediafire.com/file/ABC/file.zip']],
                 'error': None, 'copies': [id], 'statuses': ['Found www.mediafire.com offer']} for id in kwargs['ids']]}
        if op == 'provider_capability':
            return {'supported': True}
        raise AssertionError(op)


class Cache:
    native = True
    path = 'catalog.sqlite'

    def __init__(self, publisher):
        self.publisher = publisher

    def listing(self, id):
        assert id in self.publisher.committed, 'Notification before SQLite commit'
        return {'id': id}


class NativeOfferBatchBoundaryTests(unittest.TestCase):
    def test_foreground_uses_native_pool_and_commit_before_notify(self):
        publisher = Publisher()
        events, rows = [], []
        with patch('native.search_worker.os.name', 'nt'), \
                patch('native.search_worker.fetch_offer', side_effect=AssertionError('Python POST called')):
            schedule_index(None, Cache(publisher), {1: {'id': 1}, 2: {'id': 2}},
                           events.append, rows.append, False, lambda _: None, workers=3)
        self.assertEqual([r['id'] for r in rows], [1, 2])
        self.assertEqual([op for op, _ in publisher.calls],
                         ['catalog_index_dispatch'])
        self.assertEqual(publisher.calls[0][1]['ids'], [1, 2])
        self.assertEqual(publisher.calls[0][1]['workers'], 3)

    def test_uncertain_post_is_not_replayed(self):
        publisher = Publisher(fail=True)
        with patch('native.search_worker.os.name', 'nt'), \
                patch('native.search_worker.fetch_offer', side_effect=AssertionError('Python fallback replay')):
            with self.assertRaisesRegex(OSError, 'uncertain POST'):
                schedule_index(None, Cache(publisher), {1: {'id': 1}},
                               lambda _: None, None, False, lambda _: None)
        self.assertEqual([op for op, _ in publisher.calls].count('catalog_index_dispatch'), 1)

    def test_http_429_reports_committed_subset_and_stops(self):
        class RatePublisher(Publisher):
            def request(self, path, op, **kwargs):
                if op != 'catalog_index_dispatch':
                    return super().request(path, op, **kwargs)
                self.calls.append((op, kwargs))
                self.committed.add(kwargs['ids'][0])
                event = {'id': kwargs['ids'][0], 'had_observation': True,
                         'offers': [], 'error': 'HTTP 429', 'copies': [], 'statuses': []}
                kwargs['on_event'](event)
                return {'cancelled': False, 'rate_limited': True, 'offers_count': len(kwargs['ids']),
                        'initial_archives_count': 0, 'events': [event], 'archives': []}
        publisher = RatePublisher()
        seen = []
        with patch('native.search_worker.os.name', 'nt'), \
                patch('native.search_worker.fetch_offer', side_effect=AssertionError('Python POST replay')):
            with self.assertRaisesRegex(OSError, '429'):
                schedule_index(None, Cache(publisher), {1: {'id': 1}, 2: {'id': 2}},
                               lambda *_: None, lambda row: seen.append(row['id']),
                               False, lambda _: None, workers=1)
        self.assertEqual(seen, [1])
        self.assertEqual([op for op, _ in publisher.calls].count('catalog_index_dispatch'), 1)

    def test_idle_uses_committed_native_offer_without_python_writer(self):
        publisher = Publisher()
        messages = []
        with patch('native.search_worker.os.name', 'nt'), \
                patch('native.search_worker.fetch_offer', side_effect=AssertionError('Python POST called')):
            run_idle_layers(None, Cache(publisher), IDLE_LINKS, None, None,
                            messages.append, slot=lambda previous: previous)
        self.assertEqual([op for op, _ in publisher.calls],
                         ['catalog_idle_plan', 'catalog_offer_batch'])
        self.assertTrue(any('Download link saved' in msg for msg in messages))


if __name__ == '__main__':
    unittest.main()
