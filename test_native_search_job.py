"""Native multi-page search tests; requires compiled C++ helper."""
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import unittest
from urllib.parse import quote

from catalog_cache import CatalogCache
from native.reference_publisher import ReferencePublisher
from doujin import ds_url

PUBLISHER = os.getenv("DOUJINSTYLE_TEST_PUBLISHER")


def page(rows, total=55):
    return ("<mainbar>Total Results Found: %d</mainbar>" % total + "".join(
        '<div class="gridBox"><div class="gridDetails">'
        '<a href="?p=page&amp;type=1&amp;id=%d">Release %d</a></div></div>' % (rid, rid)
        for rid in rows)).encode()


@unittest.skipUnless(PUBLISHER, "Native helper not available")
class NativeSearchJobTests(unittest.TestCase):
    def test_committed_pages_duplicate_suppression_and_late_failure(self):
        with TemporaryDirectory() as directory:
            db = Path(directory) / "catalog.sqlite"
            url = lambda idx: ds_url(p="search", source=1, type="blanket", result="Fixture", page=idx)
            pages = {url(0): page(range(55, 35, -1)).hex(),
                     url(1): page([55, *range(35, 16, -1)]).hex()}
            with closing(ReferencePublisher(PUBLISHER)) as publisher:
                with closing(CatalogCache(db, publisher=publisher)) as catalog:
                    events = []
                    def committed_page(event):
                        # The event must be visible to a different SQLite
                        # connection AFTER the native worker commits the page.
                        self.assertEqual(event['kind'], 'search_page')
                        for row in event['batch']['releases']:
                            self.assertEqual(catalog.listing(row['id'])['album'], row['album'])
                            self.assertIn(str(row['id']), event['ui_dates'])
                        events.append(event)
                    result = publisher.request(db, "site_search_job", query="Fixture", pages=3,
                                               fixture_pages=pages, on_event=committed_page)
                    self.assertEqual([len(step["releases"]) for step in result["pages"]], [20, 19])
                    self.assertEqual([event['page'] for event in events], [1, 2])
                    self.assertEqual([event['batch'] for event in events], result['pages'])
                    self.assertIn("Fixture page unavailable", result["late_error"])
                    self.assertEqual(catalog.listing(55)["album"], "Release 55")
                    self.assertEqual(catalog.listing(17)["album"], "Release 17")
                    self.assertEqual(catalog.page(url(0))["rows_on_page"], 20)
                    warm = publisher.request(db, "site_search_job", query="Fixture", pages=2,
                                             fixture_pages={})
                    self.assertEqual([len(step["releases"]) for step in warm["pages"]], [20, 19])
                    self.assertEqual(warm["late_error"], "")
                    self.assertTrue(all(step["cache_hit"] for step in warm["pages"]))

    def test_validation_and_first_page_failure_no_partial_commit(self):
        with TemporaryDirectory() as directory:
            db = Path(directory) / "catalog.sqlite"
            with closing(ReferencePublisher(PUBLISHER)) as publisher:
                with closing(CatalogCache(db, publisher=publisher)) as catalog:
                    for payload in ({"query": "tag: ", "pages": 1},
                                    {"query": "X", "pages": 4},
                                    {"query": "X", "pages": 1}):
                        with self.subTest(payload=payload), self.assertRaises(OSError):
                            publisher.request(db, "site_search_job", fixture_pages={}, **payload)
                    self.assertEqual(catalog.db.execute("SELECT count(*) FROM releases").fetchone()[0], 0)
    def test_warm_frontend_dispatch_uses_one_native_batch_without_python_html(self):
        from native.search_worker import SearchHttp, run_search
        from unittest.mock import patch
        with TemporaryDirectory() as directory:
            db = Path(directory) / "catalog.sqlite"
            url = lambda idx: ds_url(p="search", source=1, type="blanket", result="Fixture", page=idx)
            fixture = {url(0): page(range(55, 35, -1)).hex(),
                       url(1): page([55, *range(35, 16, -1)]).hex()}
            with closing(ReferencePublisher(PUBLISHER)) as publisher:
                with closing(CatalogCache(db, publisher=publisher)) as catalog:
                    publisher.request(db, "site_search_job", query="Fixture", pages=2,
                                      fixture_pages=fixture)
                    with patch("native.search_worker.index_results", return_value={}) as results, \
                         patch("native.search_worker.compatibility_search_page", side_effect=AssertionError("Python HTML reached")), \
                         patch.object(publisher, "request", wraps=publisher.request) as requests:
                        messages = []
                        run_search(SearchHttp(), catalog, "Fixture", 2, Path(directory), messages.append)
                        self.assertEqual([call.args[1] for call in requests.call_args_list], ["site_search_job"])
                        self.assertEqual(sum(message.startswith("Page ") for message in messages), 2)
                        results.assert_called_once()
    def test_idle_listing_and_navigation_use_native_parser(self):
        from native.idle_backfill import idle_backfill
        from unittest.mock import patch
        class OfflineHTTP:
            def get(self, url, **kwargs):
                if url.endswith("robots.txt"):
                    return {}, b"User-agent: *\nAllow: /\n", url
                html_body = (b'<div class="gridBox"><div class="gridDetails">'
                             b'<a href="?p=page&amp;type=1&amp;id=25">Native idle release</a>'
                             b'</div></div><div class="pageScroll">'
                             b'<a href="?p=home&amp;page=0">1</a></div>')
                return {}, html_body, url
        with TemporaryDirectory() as directory:
            db = Path(directory) / "catalog.sqlite"
            with closing(ReferencePublisher(PUBLISHER)) as publisher:
                with closing(CatalogCache(db, publisher=publisher)) as catalog:
                    with patch("doujin.parse_listing", side_effect=AssertionError("Python listing used")), \
                         patch("native.idle_backfill.compatibility_page_links", side_effect=AssertionError("Python navigation used")):
                        messages = []
                        idle_backfill(OfflineHTTP(), catalog, messages.append, slot=lambda _: 0)
                    self.assertEqual(catalog.listing(25)["album"], "Native idle release")
                    self.assertTrue(catalog.backfill_checkpoint()["complete"])
                    self.assertTrue(any("Idle catalog page 1" in msg for msg in messages))
