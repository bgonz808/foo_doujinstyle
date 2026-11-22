"""The native event stream must never be mistaken for a terminal response.

These are client-side IPC contract tests. Native C++ executor tests separately
prove events happen after SQLite commits; this checks the wire/adapter side.
"""
from io import BytesIO
import json
from pathlib import Path
import unittest
from native.reference_publisher import ReferencePublisher, NativeCatalogWorkerUnavailable


class FakeProcess:
    def __init__(self, frames):
        self.stdin = BytesIO()
        self.stdout = BytesIO(b''.join(json.dumps(frame).encode() + b'\n' for frame in frames))
        self.killed = False
        self.returncode = 0

    def wait(self, timeout=0):
        return self.returncode

    def kill(self):
        self.killed = True


class StreamedCatalogEventsTests(unittest.TestCase):
    @staticmethod
    def client(frames):
        publisher = ReferencePublisher(executable=Path('/unused'))
        publisher.process = FakeProcess(frames)
        return publisher

    def test_two_committed_events_before_terminal_result(self):
        events = []
        client = self.client([{'event': {'id': 5}}, {'event': {'id': 7}},
                              {'result': {'events': [{'id': 5}, {'id': 7}], 'cancelled': False}}])
        result = client.request('/fixture/catalog', 'catalog_index_dispatch', ids=[5, 7],
                                on_event=events.append)
        self.assertEqual(events, [{'id': 5}, {'id': 7}])
        self.assertEqual(result['events'], events)
        self.assertIn(b'catalog_index_dispatch', client.process.stdin.getvalue())
        self.assertTrue(json.loads(client.process.stdin.getvalue())['stream_events'])
        client.close()

    def test_uncalled_events_are_rejected_and_channel_closed(self):
        client = self.client([{'event': {'id': 5}}, {'result': 1}])
        with self.assertRaisesRegex(OSError, 'Unsolicited'):
            client.request(None, 'site_search_job')
        self.assertIsNone(client.process)

    def test_event_stream_is_opt_in_and_field_reserved(self):
        client = self.client([{'result': {'ok': True}}])
        self.assertEqual(client.request(None, 'catalog_index_dispatch'), {'ok': True})
        self.assertNotIn('stream_events', json.loads(client.process.stdin.getvalue()))
        with self.assertRaisesRegex(ValueError, 'reserved'):
            client.request(None, 'catalog_index_dispatch', stream_events=True)
        client.close()

    def test_callback_failure_does_not_desynchronize_pipe(self):
        client = self.client([{'event': {'id': 5}}, {'result': 1}])
        child = client.process
        def reject(_):
            raise ValueError('wrong committed ID')
        with self.assertRaisesRegex(ValueError, 'wrong committed ID'):
            client.request(None, 'catalog_index_dispatch', on_event=reject)
        self.assertIsNone(client.process)
        self.assertTrue(child.killed, 'protocol rejection must promptly kill an active writer')

    def test_nested_request_fails_fast_without_deadlocking_worker(self):
        client = self.client([{'event': {'id': 5}}, {'result': True}])
        def nested(_):
            client.request(None, 'ui_date', release={'id': 5})
        with self.assertRaisesRegex(NativeCatalogWorkerUnavailable, 'reentrant'):
            client.request(None, 'catalog_index_dispatch', on_event=nested)
        self.assertIsNone(client.process)

    def test_valid_remote_error_keeps_worker_synchronized(self):
        client = self.client([{'error': 'Release not found'}, {'result': {'ok': True}}])
        with self.assertRaisesRegex(OSError, 'Release not found'):
            client.request(None, 'site_release')
        self.assertIsNotNone(client.process)
        self.assertEqual(client.request(None, 'site_charts'), {'ok': True})
        client.close()

    def test_terminal_error_after_committed_event_is_failure(self):
        events = []
        client = self.client([{'event': {'id': 5}}, {'error': 'SQLite writer unavailable'}])
        with self.assertRaisesRegex(OSError, 'SQLite writer unavailable'):
            client.request(None, 'catalog_index_dispatch', on_event=events.append)
        self.assertEqual(events, [{'id': 5}])
        client.close()

    def test_no_terminal_frame_is_interrupted_not_success(self):
        events = []
        client = self.client([{'event': {'id': 5}}])
        with self.assertRaises(NativeCatalogWorkerUnavailable):
            client.request(None, 'catalog_index_dispatch', on_event=events.append)
        self.assertEqual(events, [{'id': 5}])
        self.assertIsNone(client.process)

    def test_ambiguous_terminal_frame_closes_pipe(self):
        client = self.client([{'result': {}, 'error': 'protocol-confusion'}])
        with self.assertRaisesRegex(OSError, 'ambiguous'):
            client.request(None, 'site_search_job')
        self.assertIsNone(client.process)

    def test_malformed_terminal_error_closes_pipe(self):
        client = self.client([{'error': {'message': 'not a string'}}])
        with self.assertRaisesRegex(OSError, 'malformed error'):
            client.request(None, 'site_search_job')
        self.assertIsNone(client.process)

    def test_frame_length_is_bounded(self):
        from unittest.mock import patch
        client = self.client([{'result': 'x' * 80}])
        with patch('native.reference_publisher.MAX_NATIVE_FRAME_BYTES', 32):
            with self.assertRaisesRegex(OSError, 'byte limit'):
                client.request(None, 'site_search_job')
        self.assertIsNone(client.process)

    def test_malformed_event_is_rejected(self):
        client = self.client([{'event': [5, 7]}, {'result': True}])
        with self.assertRaisesRegex(OSError, 'malformed'):
            client.request(None, 'catalog_index_dispatch', on_event=lambda _: None)
        self.assertIsNone(client.process)


if __name__ == '__main__':
    unittest.main()
