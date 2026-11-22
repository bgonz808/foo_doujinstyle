"""Native chart snapshot/refresh and installed Python chart bridge tests.

These use the compiled native publisher and a real SQLite database. The C++
helper parses the fixture HTML and commits chart snapshots and listing seeds.
"""
try:
    import fcntl
except ImportError:  # Windows host tests exercise the Win32 lease separately.
    fcntl = None
import os
from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from catalog_cache import CatalogCache
from native.reference_publisher import ReferencePublisher
from native.search_worker import SearchHttp, run_popular, prefetch_charts, GUIDES_URL

FIXTURE = b'''<html><head><link rel="icon" href="/favicon.ico"></head><body>
<h3>This Month</h3><div><a href="/?p=page&amp;type=1&amp;id=25">First</a><b>801 downloads</b></div>
<h3>All-Time</h3><div><a href="/?p=page&amp;type=1&amp;id=25">First</a><b>1,200 downloads</b></div>
<div><a href="/?p=page&amp;type=1&amp;id=26">Second</a><b>1,090 downloads</b></div>
</body></html>'''


@unittest.skipUnless(os.getenv("DOUJINSTYLE_TEST_PUBLISHER"), "Compiled native worker required")
class NativeChartJob(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "charts.sqlite"
        self.publisher = ReferencePublisher(os.environ["DOUJINSTYLE_TEST_PUBLISHER"])
        self.addCleanup(self.publisher.close)
        self.cache = CatalogCache(self.path, publisher=self.publisher)
        self.addCleanup(self.cache.close)
        self.cache.put_listing({"id": 25, "album": "Verified spelling", "tags": ["J-core"]})

    def test_native_chart_snapshot_and_refresh_parity(self):
        fresh = self.publisher.request(self.path, "site_chart_snapshot")
        self.assertIsNone(fresh["snapshot"])
        self.assertEqual(fresh["fetched"], 0)
        update = self.publisher.request(self.path, "site_chart_refresh", previous=0, html=FIXTURE.hex())
        self.assertTrue(update["updated"])
        self.assertFalse(update["busy"])
        self.assertEqual([row["id"] for row in update["snapshot"]["charts"]["month"]], [25])
        self.assertEqual([row["id"] for row in update["snapshot"]["charts"]["all"]], [25, 26])
        self.assertEqual(update["snapshot"]["charts"]["month"][0]["chart_downloads"], 801)
        self.assertEqual(self.cache.listing(25)["album"], "Verified spelling")
        self.assertNotIn("chart_rank", self.cache.listing(26))
        self.assertEqual(self.cache.page_snapshot(GUIDES_URL)[0], update["snapshot"])
        snapshot = self.publisher.request(self.path, "site_chart_snapshot")
        self.assertEqual(snapshot["snapshot"], update["snapshot"])
        reused = self.publisher.request(self.path, "site_chart_refresh", previous=0, html=b"bad".hex())
        self.assertFalse(reused["updated"])
        self.assertEqual(reused["fetched"], update["fetched"])
        with self.assertRaises(OSError):
            self.publisher.request(self.path, "site_chart_refresh", previous=update["fetched"], html=b"bad".hex())
        self.assertEqual(self.publisher.request(self.path, "site_chart_snapshot")["snapshot"], update["snapshot"])

    @unittest.skipIf(fcntl is None, 'POSIX flock used only for portable lock fixture')
    def test_busy_lease_does_not_fetch_or_hold_a_write_transaction(self):
        lock = Path(str(self.path) + ".charts.lock")
        with lock.open("ab") as owner:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            busy = self.publisher.request(self.path, "site_chart_refresh", previous=0, html=b"bad".hex())
            self.assertTrue(busy["busy"])
            self.assertIsNone(busy["snapshot"])
            self.cache.put_listing({"id": 26, "album": "Independent write"})
        refreshed = self.publisher.request(self.path, "site_chart_refresh", previous=0, html=FIXTURE.hex())
        self.assertTrue(refreshed["updated"])

    def test_installed_chart_bridge_uses_cached_native_state(self):
        self.publisher.request(self.path, "site_chart_refresh", previous=0, html=FIXTURE.hex())
        calls, seen, messages = [], [], []
        with patch("native.search_worker.index_results", side_effect=lambda *args, **kwargs:
                   (calls.append((args, kwargs)) or {"listings": 1})):
            run_popular(SearchHttp(), self.cache, "month", Path(self.temp.name), messages.append,
                        emit_result=lambda row: seen.append(row), emit_chart=lambda rows: seen.append(rows))
            prefetch_charts(SearchHttp(), self.cache, Path(self.temp.name), messages.append,
                            emit_result=lambda row: seen.append(row))
        self.assertEqual(len(calls), 2)
        self.assertEqual(list(calls[0][0][2]), [25])
        self.assertEqual(calls[0][0][2][25]["album"], "Verified spelling")
        self.assertEqual(calls[1][1]["workers"], 1)
        self.assertEqual(set(calls[1][0][2]), {25, 26})
        self.assertTrue(any(isinstance(row, dict) and row.get("chart_downloads") == 801 for row in seen))
        self.assertTrue(any("ranked releases" in message for message in messages))

    def test_refresh_failure_retains_displayed_saved_rankings(self):
        self.publisher.request(self.path, "site_chart_refresh", previous=0, html=FIXTURE.hex())
        messages, charts = [], []
        original = self.publisher.request

        def failed(db, operation, **fields):
            if operation == "site_chart_refresh":
                raise OSError("fixture: remote catalog unavailable")
            return original(db, operation, **fields)

        with patch.object(self.publisher, "request", side_effect=failed), \
             patch("native.search_worker.index_results", return_value={"listings": 1}):
            run_popular(SearchHttp(), self.cache, "month", Path(self.temp.name), messages.append,
                        emit_chart=lambda rows: charts.append(list(rows)), refresh=True)
        self.assertEqual(charts, [[25]])
        self.assertTrue(any("Showing saved rankings" in message for message in messages))


if __name__ == '__main__':
    unittest.main()
