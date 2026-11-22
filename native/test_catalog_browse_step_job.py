"""Real IPC and SQLite integration tests for the native browse state machine."""
import os
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from catalog_cache import CatalogCache
from native.reference_publisher import ReferencePublisher

PUBLISHER = os.getenv('DOUJINSTYLE_TEST_PUBLISHER')
HOME = 'https://doujinstyle.com/?p=home&page=0'
OLD = 'https://doujinstyle.com/?p=home&page=1'
OLDER = 'https://doujinstyle.com/?p=home&page=2'


def page(ids, *linked):
    rows = ''.join('<div class="gridBox"><div class="gridDetails">'
                   '<a href="/?p=page&amp;type=1&amp;id=%d">Release %d</a></div></div>' % (id, id)
                   for id in ids)
    nav = ''.join('<a href="/?p=home&amp;page=%d">%d</a>' % (id, id) for id in linked)
    return ('<mainbar>Total Results Found: 3</mainbar>' + rows +
            '<div class="pageScroll">' + nav + '</div>').encode().hex()


@unittest.skipUnless(PUBLISHER, 'Native C++ helper required')
class NativeBrowseStepTests(unittest.TestCase):
    def test_oldest_first_commit_resume_and_no_refetch_after_complete(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'db.sqlite'
            with closing(ReferencePublisher(PUBLISHER)) as publisher:
                with closing(CatalogCache(path, publisher=publisher)) as cache:
                    first = publisher.request(path, 'catalog_browse_step',
                                              fixture_pages={HOME: page([3], 1)})
                    self.assertEqual(first['state']['target'], OLD)
                    self.assertEqual(cache.db.execute('SELECT count(*) FROM pages').fetchone()[0], 0)
                    second = publisher.request(path, 'catalog_browse_step', state=first['state'],
                                               fixture_pages={OLD: page([2], 0)})
                    self.assertEqual(second['saved'], 1)
                    self.assertFalse(second['done'])
                    self.assertEqual(second['state']['target'], HOME)
                    self.assertEqual(cache.listing(2)['album'], 'Release 2')
                    self.assertEqual(cache.backfill_checkpoint()['url'], OLD)
                    third = publisher.request(path, 'catalog_browse_step', state=second['state'],
                                              fixture_pages={HOME: page([3], 1)})
                    self.assertEqual(third['saved'], 0)
                    self.assertTrue(third['done'])
                    self.assertTrue(cache.backfill_checkpoint()['complete'])
                    self.assertEqual(cache.listing(3)['album'], 'Release 3')
                    terminal = publisher.request(path, 'catalog_browse_step', fixture_pages={})
                    self.assertTrue(terminal['done'])

    def test_resume_relocation_must_overlap_without_partial_commit(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'db.sqlite'
            with closing(ReferencePublisher(PUBLISHER)) as publisher:
                with closing(CatalogCache(path, publisher=publisher)) as cache:
                    cache.put_page(OLD, {'releases': [], 'rows_on_page': 0}, checkpoint={
                        'url': OLD, 'referrer': HOME, 'referrer_policy': '',
                        'ids': [100,99], 'complete': False})
                    moved = publisher.request(path, 'catalog_browse_step',
                                              fixture_pages={OLD: page([300, 299], 2)})
                    self.assertEqual(moved['state']['target'], OLDER)
                    self.assertEqual(moved['state']['relocations'], 1)
                    self.assertEqual(cache.backfill_checkpoint()['url'], OLD)
                    settled = publisher.request(path, 'catalog_browse_step', state=moved['state'],
                                                fixture_pages={OLDER: page([100,99], 1)})
                    self.assertEqual(settled['saved'], 2)
                    self.assertEqual(cache.listing(100)['album'], 'Release 100')
                    self.assertEqual(cache.backfill_checkpoint()['url'], OLDER)

    def test_off_origin_and_bad_checkpoint_fail_before_writing(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'db.sqlite'
            with closing(ReferencePublisher(PUBLISHER)) as publisher:
                with closing(CatalogCache(path, publisher=publisher)) as cache:
                    for target in ('https://doujinstyle.com.evil.test/?p=home&page=1',
                                   'https://doujinstyle.com/?p=home&page=1000000'):
                        with self.assertRaises(OSError):
                            publisher.request(path, 'catalog_browse_step', state={
                                'target': target, 'referrer': HOME, 'policy': '',
                                'anchor': None, 'relocations': 0}, fixture_pages={})
                    with self.assertRaises(OSError):
                        publisher.request(path, 'catalog_browse_step',
                                          fixture_pages={HOME: page([3]) + 'broken'})
                    self.assertEqual(cache.db.execute('SELECT count(*) FROM pages').fetchone()[0], 0)


if __name__ == '__main__': unittest.main()
