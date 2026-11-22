"""Compiled worker IPC tests for native site resources, provider validation and idle gating."""
import os
from pathlib import Path
import unittest

from native.reference_publisher import ReferencePublisher


@unittest.skipUnless(os.getenv('DOUJINSTYLE_TEST_PUBLISHER'), 'requires compiled C++ catalog worker')
class NativeResourceAndIdleIPC(unittest.TestCase):
    def setUp(self):
        self.publisher = ReferencePublisher(os.environ['DOUJINSTYLE_TEST_PUBLISHER'])
        self.addCleanup(self.publisher.close)

    def test_site_resource_only_accepts_site_and_bounded_bytes(self):
        request = self.publisher.request
        result = request(None, 'site_resource', kind='art',
                         url='https://doujinstyle.com/thumbs/123.jpg', cap=4096,
                         fixture_body='ffd8ff00ff')
        self.assertEqual(result, {'status': 200, 'body': 'ffd8ff00ff'})
        for url in ('http://doujinstyle.com/thumbs/123.jpg',
                    'https://doujinstyle.com.evil.com/thumbs/123.jpg',
                    'https://127.0.0.1/thumbs/123.jpg',
                    'https://user@doujinstyle.com/thumbs/123.jpg',
                    'https://doujinstyle.com/guides'):
            with self.subTest(url=url), self.assertRaises(OSError):
                request(None, 'site_resource', kind='art', url=url, cap=4096, fixture_body='ffd8ff')
        with self.assertRaises(OSError):
            request(None, 'site_resource', kind='art', url='https://doujinstyle.com/thumbs/123.jpg',
                    cap=4096, fixture_body='ee' * 4097)
        with self.assertRaises(OSError):
            request(None, 'site_resource', kind='art', url='https://doujinstyle.com/thumbs/123.jpg',
                    cap=1000000, fixture_body='ff')

    def test_robots_transport_is_bounded(self):
        result = self.publisher.request(None, 'site_resource', kind='robots',
               url='https://doujinstyle.com/robots.txt', cap=65536,
               fixture_body=b'User-agent: *\nDisallow: /'.hex())
        self.assertEqual(bytes.fromhex(result['body']), b'User-agent: *\nDisallow: /')
        for url in ('https://doujinstyle.com/robots-old.txt', 'https://evil.test/robots.txt'):
            with self.assertRaises(OSError):
                self.publisher.request(None, 'site_resource', kind='robots', url=url,
                                       cap=65536, fixture_body='')

    def test_idle_gate_is_stateful_per_worker(self):
        def step(idle, now):
            return self.publisher.request(None, 'idle_wait', fixture_idle_ms=idle, fixture_now_ms=now)['ready']
        self.assertFalse(step(29999, 100000))
        self.assertTrue(step(30000, 100000))
        self.assertFalse(step(30001, 109000))
        self.assertFalse(step(0, 110000))
        self.assertTrue(step(60000, 110000))
        self.assertFalse(step(60000, 119999))
        self.assertTrue(step(30000, 120000))

    def test_provider_policy_reuses_native_validator(self):
        def supported(url, host, offer=None):
            reply = self.publisher.request(None, 'provider_capability', url=url, host=host,
                                           offer=offer or {'host':host,'filename':'x.zip'})
            return reply['supported']
        self.assertTrue(supported('https://www.mediafire.com/file/abc/Release.zip/file', 'mediafire.com'))
        self.assertTrue(supported('https://mega.nz/file/ABCDEFGH#' + 'A' * 43, 'mega.nz'))
        self.assertFalse(supported('https://mega.nz/folder/ABCDEFGH#' + 'A' * 43, 'mega.nz'))
        self.assertFalse(supported('https://mega.nz/file/ABCDEFGH#' + 'A' * 43, 'mediafire.com'))
        self.assertFalse(supported('https://www.mediafire.com.evil.test/file/abc/a.zip', 'mediafire.com'))
        self.assertFalse(supported('http://www.mediafire.com/file/abc/a.zip', 'mediafire.com'))
        self.assertFalse(supported('https://localhost/file/abc/a.zip', 'mediafire.com'))
        with self.assertRaises(OSError):
            self.publisher.request(None, 'provider_capability', url='https://mega.nz/file/ABCDEFGH',
                                   host='mega.nz', offer=[1,2])


if __name__ == '__main__': unittest.main()
