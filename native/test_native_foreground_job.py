"""Offline integration of actual native --foreground-search, not mocked events.

Seeds committed catalog data through the real native writer and makes the
frontend read the warm snapshot, avoiding external network and live providers.
"""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
import unittest

from native.reference_publisher import ReferencePublisher

NATIVE = os.getenv("DOUJINSTYLE_TEST_PUBLISHER")


def page_markup():
    return (b"<mainbar>Total Results Found: 1</mainbar>"
            b'<div class="gridBox"><div class="gridDetails">'
            b'<a href="?p=page&amp;type=1&amp;id=52">Fixture Catalog</a>'
            b'</div></div>')


@unittest.skipUnless(NATIVE, "A compiled native publisher is required")
class NativeForegroundCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "catalog.db"
        self.output = Path(self.tmp.name) / "tracks"
        self.output.mkdir()
        self.publisher = ReferencePublisher(executable=NATIVE)
        self.addCleanup(self.publisher.close)
        self.publisher.request(self.db, "initialize", wal=True, legacy=[])
        url = "https://doujinstyle.com/?p=search&source=1&type=blanket&result=Fixture&page=0"
        self.publisher.request(self.db, "site_search_job", query="Fixture", pages=1,
                               fixture_pages={url: page_markup().hex()})
        with closing(sqlite3.connect(self.db)) as db:
            # No live remote offer or archive fetch should occur in this
            # deterministic warm fixture run.
            db.execute("UPDATE releases SET offer_checked=?, offer_error=NULL WHERE id=52",
                       (int(time.time()),))
            db.commit()

    def run_frontend(self, *extra):
        return subprocess.run(
            [NATIVE, "--foreground-search", "--cache", str(self.db),
             "--output", str(self.output), "--query=Fixture",
             "--pages", "1", "--result-rows", *extra],
            capture_output=True, timeout=15, check=False)

    def test_cached_search_is_read_and_presented_without_python_runtime(self):
        response = self.run_frontend()
        self.assertEqual(response.returncode, 0, response.stdout + response.stderr)
        text = response.stdout.decode("utf-8", "strict")
        self.assertIn("Page 1  •  1 results of 1 matches  •  cached by native catalog", text)
        self.assertIn("\x1e52\tFixture Catalog\t", text)
        self.assertIn("Done. 1 listings, 0/0 archives inspected, 0 references written.", text)
        self.assertIn("Coverage: 0/1 releases have published tracks.", text)
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM releases").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM copies").fetchone()[0], 0)

    def test_rejects_invalid_and_ambiguous_options(self):
        for arguments in (
            ("--pages", "4"),
            ("--refresh", "--refresh"),
            ("--query=other",),
            ("--unknown", "1"),
        ):
            with self.subTest(arguments=arguments):
                result = self.run_frontend(*arguments)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn(b"Done.", result.stdout)

    def test_error_does_not_leak_sensitive_query_in_stdout(self):
        result = subprocess.run(
            [NATIVE, "--foreground-search", "--cache", str(self.db),
             "--output", str(self.output), "--query=secret-canary",
             "--pages", "0"],
            capture_output=True, timeout=15, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(b"secret-canary", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
