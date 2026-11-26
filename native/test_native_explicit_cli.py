"""Compiled archive-action CLI smoke tests: offline fail-closed behaviors.

C++ unit contracts cover successful transfers, repair publication, 403 and
unknown POST outcomes. Real provider success needs Windows host qualification.
"""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from native.reference_publisher import ReferencePublisher
from native.test_native_foreground_job import page_markup

NATIVE = os.getenv("DOUJINSTYLE_TEST_PUBLISHER")


@unittest.skipUnless(NATIVE, "Compiled native helper is required")
class NativeExplicitCLI(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = Path(temporary.name) / "catalog.sqlite"
        self.out = Path(temporary.name) / "out"
        self.out.mkdir()
        with closing(ReferencePublisher(executable=NATIVE)) as worker:
            worker.request(self.db, "initialize", wal=True, legacy=[])

    def command(self, *args):
        return subprocess.run([NATIVE, "--explicit-archive", "--cache", str(self.db),
                               "--output", str(self.out), *args],
                              capture_output=True, timeout=15, check=False)

    def test_missing_release_no_publication(self):
        for action in ("--download-archive", "--repair-release"):
            with self.subTest(action=action):
                response = self.command(action, "52")
                self.assertNotEqual(response.returncode, 0)
                self.assertIn(b"Archive action stopped:", response.stdout)
                self.assertFalse(list(self.out.rglob("*.djt")))
                with closing(sqlite3.connect(self.db)) as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM releases").fetchone()[0], 0)
                    self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_id_and_mode_fail_closed_without_secret_echo(self):
        for args in (("--download-archive", "52", "--repair-release", "52"),
                     ("--download-archive", "0"),
                     ("--download-archive", "18446744073709551616"),
                     ("--repair-release", "-1"),
                     ("--repair-release", "52-canary"),
                     ("--download-archive", "52", "--download-archive", "53"),
                     ("--download-archive", "52", "--extra", "canary-secret")):
            with self.subTest(args=args):
                result = self.command(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn(b"canary", result.stdout + result.stderr)
                self.assertFalse(list(self.out.rglob("*.djt")))

    def test_existing_release_without_offer_fails_without_python_recovery(self):
        url = "https://doujinstyle.com/?p=search&source=1&type=blanket&result=Fixture&page=0"
        with closing(ReferencePublisher(executable=NATIVE)) as worker:
            worker.request(self.db, "site_search_job", query="Fixture", pages=1,
                           fixture_pages={url: page_markup().hex()})
        for action in ("--download-archive", "--repair-release"):
            response = self.command(action, "52")
            self.assertNotEqual(response.returncode, 0)
            self.assertIn(b"Archive action stopped:", response.stdout)
            self.assertFalse(list(self.out.rglob("*.djt")))
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM releases").fetchone()[0], 1)
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")


if __name__ == "__main__":
    unittest.main()
