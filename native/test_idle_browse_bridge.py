"""Installed Windows browse driver must use the C++ checkpoint state machine."""
from pathlib import Path
import unittest
from unittest.mock import patch
from native import idle_backfill as module
from native.pack_search_worker import worker_sources


class BrowseBoundaryTests(unittest.TestCase):
    def test_installed_worker_excludes_source_only_browse_and_offer_runners(self):
        members, inventory = worker_sources(Path(__file__).resolve().parent.parent)
        self.assertNotIn('native/idle_backfill.py', members)
        self.assertNotIn(b'def compatibility_native_plan_index(', members['native/search_worker.py'])
        self.assertNotIn(b'def fetch_offer(', members['native/search_worker.py'])
        self.assertNotIn(b'def save_offer(', members['native/search_worker.py'])
        self.assertNotIn(b'from lxml import html', members['native/search_worker.py'])
        self.assertNotIn(b'def run_idle_layers(', members['native/search_worker.py'])
        self.assertNotIn(b'catalog_browse_step', members['native/search_worker.py'])
        self.assertNotIn('native/idle_backfill.py', inventory['modules'])

    def test_windows_worker_drives_native_page_steps_and_progress(self):
        class Publisher:
            calls = []
            def request(self, _db, operation, **params):
                assert operation == 'catalog_browse_step'
                self.calls.append(params['state'])
                if len(self.calls) == 1:
                    return {'done': False, 'state': {'target': 'oldest'},
                            'message': 'Located oldest observed browse page.'}
                if len(self.calls) == 2:
                    return {'done': False, 'state': {'target': 'newer'},
                            'saved': 1, 'rows': 3, 'message': 'Idle catalog page saved.'}
                return {'done': True, 'state': None, 'saved': 0,
                        'rows': 2, 'message': 'Idle backfill reached the newest page.'}
        class Cache:
            native = True
            path = 'fixture.db'
            publisher = Publisher()
            def put_page(self, *args, **kwargs):
                raise AssertionError('Python must not commit a native browse page')
        messages, idle_ticks = [], []
        def idle(previous):
            idle_ticks.append(previous)
            return previous + 10
        with patch.object(module.sys, 'platform', 'win32'), \
                patch.object(module, 'report_robots_advisory') as robots, \
                patch.object(module, 'compatibility_idle_backfill', side_effect=AssertionError('Python browse reached')):
            module.idle_backfill(None, Cache(), messages.append, slot=idle)
        self.assertEqual(len(Cache.publisher.calls), 3)
        self.assertEqual(len(idle_ticks), 3)
        self.assertEqual(robots.call_count, 1)
        self.assertTrue(any('Idle catalog page 2' in msg for msg in messages))
        self.assertTrue(any('newest page' in msg for msg in messages))


if __name__ == '__main__': unittest.main()
