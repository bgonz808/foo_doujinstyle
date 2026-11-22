"""Native ZIP inventory parity and installed-worker boundary fixtures."""
import os
import random
from pathlib import Path
import unittest
from zipfile import ZipInfo

from native.archive_zip import Member
from native.reference_publisher import ReferencePublisher
from native.zip_inventory_reference import project


def fixture_member(name, *, raw=None, warning=False, alternate=None):
    data = name.encode('utf-8') if raw is None else raw
    return Member(name, data, 150, 300, 0xdeadbeef, 91, 8, 0,
                  'utf-8' if raw is None else 'cp932-inferred', warning, alternate)


@unittest.skipUnless(os.getenv('DOUJINSTYLE_TEST_PUBLISHER'), 'Native publisher required')
class NativeZipInventory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.worker = ReferencePublisher(os.environ['DOUJINSTYLE_TEST_PUBLISHER'])

    @classmethod
    def tearDownClass(cls):
        cls.worker.close()

    def test_fixture_parity(self):
        members = [fixture_member(name) for name in (
            'Album/', 'Album/01.FLAC', 'Album/cover.JPG', '__MACOSX/._01.flac',
            'Album/README.txt', 'Album/.hidden', 'Album/sub/track.MP3',
            'Album\\notes.nfo', 'Album/hello.aac', 'Album/音楽.ogg',
            'Album/② - other.wav', 'Album/._cover.png', 'Artwork/表紙.png',
            'Album/weird.dummy-never')]
        members.append(fixture_member('Album/変な名前.flac', raw=b'not-utf8',
                                      warning=True, alternate='Album/別名.flac'))
        observed = self.worker.zip_inventory(members)
        self.assertEqual(observed, project(members))
        self.assertEqual(observed['audio_members'], 6)
        self.assertIn('Album/cover.JPG', [row['path'] for row in observed['artwork']])
        self.assertIn('zip_name', observed['inventory'][-1])
        self.assertEqual(observed['unknown_suffixes']['.dummy-never'], 1)

    def test_seeded_cross_member_parity(self):
        randomizer = random.Random(0x55DD)
        stems = ['1', 'readme', 'cover', 'tracklist', '表紙', '⓪', 'album', '._dummy', '__MACOSX']
        endings = ['.FLAC', '.mp3', '.nfo', '.txt', '.unknown', '.jpg', '.PDF', '.m3u8', '', '.']
        for _ in range(35):
            members = []
            for i in range(randomizer.randrange(1, 55)):
                prefix = randomizer.choice(['', 'album/', 'album/sub/', '__MACOSX/', 'artist\\album\\'])
                name = prefix + randomizer.choice(stems) + randomizer.choice(endings)
                if randomizer.randrange(15) == 0:
                    name += '/'
                members.append(fixture_member(name))
            with self.subTest(len=len(members)):
                self.assertEqual(self.worker.zip_inventory(members), project(members))

    def test_standard_zipinfo_adapter(self):
        first = ZipInfo('Comp/01 - audio.mp3')
        first.file_size = 500
        first.compress_size = 480
        first.header_offset = 90
        first.CRC = 0x01020304
        second = ZipInfo('Comp/notes.cue')
        second.header_offset = 501
        second.CRC = 0
        second.compress_size = 0
        self.assertEqual(self.worker.zip_inventory([first, second]), project([first, second]))

    def test_reject_malformed_or_oversized_inventory(self):
        with self.assertRaises(OSError):
            self.worker.request(None, 'zip_inventory', members={})
        with self.assertRaises(OSError):
            self.worker.request(None, 'zip_inventory', members=[{'filename': 'a.flac'}])
        with self.assertRaises(OSError):
            self.worker.zip_inventory([fixture_member('a' * 4097 + '.mp3')])
        with self.assertRaises(OSError):
            self.worker.request(None, 'zip_inventory', members=[{
                'filename': 'negative.flac', 'compress_size': -1, 'file_size': 1,
                'CRC': 0, 'header_offset': 0, 'compress_type': 8, 'flag_bits': 0}])

    def test_standalone_reference_not_embedded(self):
        from native.pack_search_worker import worker_sources
        modules, _ = worker_sources(Path(__file__).resolve().parent.parent)
        self.assertNotIn('native/zip_inventory_reference.py', modules)
        self.assertNotIn('doujin.py', modules)
        self.assertNotIn(b'def zip_inventory(', modules['native/reference_publisher.py'])


if __name__ == '__main__':
    unittest.main()
