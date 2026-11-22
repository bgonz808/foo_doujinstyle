"""The real native worker selects persisted offers via SQLite and pipe IPC."""
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from native.reference_publisher import ReferencePublisher
from native.pack_search_worker import worker_sources

MEGA = 'https://mega.nz/file/PublicID#AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8'
MF = 'https://www.mediafire.com/file/abc/Test/file'

class NativeArchivePlanTest(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('DOUJINSTYLE_TEST_PUBLISHER'), 'Native executable not compiled')
    def test_ipc_planning_and_provider_order(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'catalog.sqlite'
            db = sqlite3.connect(path)
            db.executescript('''
                CREATE TABLE releases(id INTEGER PRIMARY KEY);
                CREATE TABLE copies(id INTEGER PRIMARY KEY, share_url TEXT, host TEXT, offer TEXT);
                CREATE TABLE release_copies(release_id INTEGER, copy_id INTEGER, current INTEGER);
                INSERT INTO releases VALUES(1),(2);
            ''')
            db.executemany('INSERT INTO copies VALUES(?,?,?,?)', [
                (1, MEGA, 'mega.nz', '{"filename":"FLAC.zip"}'),
                (2, MF, 'mediafire.com', '{"filename":"Album.zip"}'),
                (3, 'https://www.mediafire.com/file/old', 'mediafire.com', '{}')])
            db.executemany('INSERT INTO release_copies VALUES(?,?,?)',
                           [(1, 1, 1), (1, 2, 1), (1, 3, 0)])
            db.commit(); db.close()
            peer = ReferencePublisher(executable=Path(os.environ['DOUJINSTYLE_TEST_PUBLISHER']))
            try:
                first = peer.request(path, 'archive_download_plan', id=1)
                self.assertEqual(first['status'], 'ready')
                self.assertEqual(first['copy']['url'], MF)
                self.assertEqual(first['copy']['filename'], 'Album.zip')
                backup = peer.request(path, 'archive_download_plan', id=1, exclude=MF)
                self.assertEqual(backup['copy']['url'], MEGA)
                self.assertEqual(peer.request(path, 'archive_download_plan', id=2)['status'], 'no_offer')
                self.assertEqual(peer.request(path, 'archive_download_plan', id=3)['status'], 'missing_release')
                with self.assertRaisesRegex(OSError, 'Invalid archive job release ID'):
                    peer.request(path, 'archive_download_plan', id=-1)
            finally:
                peer.close()

    def test_no_source_only_downloader_in_worker(self):
        members, report = worker_sources(Path(__file__).resolve().parent.parent)
        self.assertNotIn('native/mega_download.py', members)
        self.assertNotIn('def compatibility_download_archive(', members['native/search_worker.py'].decode())
        self.assertLess(report['python_lines'], 2668)

if __name__ == '__main__': unittest.main()
