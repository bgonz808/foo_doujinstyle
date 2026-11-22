"""Protocol audit: unresolved POST intents survive process boundaries."""
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from native.reference_publisher import ReferencePublisher


@unittest.skipUnless(os.getenv('DOUJINSTYLE_TEST_PUBLISHER'), 'Portable native publisher not compiled')
class PostAttemptIPC(unittest.TestCase):
    def test_audit_exposes_only_reviewable_metadata_and_never_implicitly_clears(self):
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder)/'catalog.sqlite'
            db = sqlite3.connect(db_path)
            db.execute('CREATE TABLE releases(id INTEGER PRIMARY KEY)')
            db.commit()
            peer = ReferencePublisher(executable=Path(os.environ['DOUJINSTYLE_TEST_PUBLISHER']))
            try:
                self.assertEqual(peer.request(db_path,'catalog_post_attempts'),[])
                with self.assertRaisesRegex(OSError, 'Unjournaled single-release POST disabled'):
                    peer.request(None, 'site_release_offers', id=7,
                                 url='https://doujinstyle.com/?p=page&type=1&id=7')
                db.execute('''CREATE TABLE catalog_post_attempts(
                    release_id INTEGER PRIMARY KEY,owner TEXT NOT NULL,
                    started_at INTEGER NOT NULL,post_count INTEGER NOT NULL)
                ''')
                db.execute('INSERT INTO catalog_post_attempts VALUES(7,?,?,?)',('secret-owner',1234,2))
                db.commit()
                self.assertEqual(peer.request(db_path,'catalog_post_attempts'),
                                 [{'id':7,'started_at':1234,'post_count':2}])
                # Re-opening a native peer preserves the marker: no automatic retry.
                peer.close()
                peer = ReferencePublisher(executable=Path(os.environ['DOUJINSTYLE_TEST_PUBLISHER']))
                self.assertEqual(peer.request(db_path,'catalog_post_attempts'),
                                 [{'id':7,'started_at':1234,'post_count':2}])
            finally:
                peer.close()
                db.close()
