"""Construct a miniature v1 database and prove only aggregate facts escape."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tools.catalog_corpus import inspect


class CorpusAuditTests(unittest.TestCase):
    def test_fixture_counts_without_leaking_urls_or_filenames(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "catalog.sqlite"
            with sqlite3.connect(path) as db:
                db.execute("PRAGMA user_version=1")
                db.execute("CREATE TABLE releases (id INTEGER PRIMARY KEY, listing TEXT)")
                db.execute("CREATE TABLE copies (id INTEGER PRIMARY KEY, host TEXT, archive TEXT)")
                db.execute("CREATE TABLE release_copies (release_id INTEGER, copy_id INTEGER)")
                db.execute("CREATE TABLE pages (url TEXT, snapshot TEXT)")
                db.execute("INSERT INTO releases VALUES (?,?)",
                           (19732, json.dumps({"technical":{"advertised_format":"WAV"}, "album":"SECRET"})))
                db.execute("INSERT INTO copies VALUES (?,?,?)",
                           (1,"mediafire.com",json.dumps({"format":"zip", "archive_bytes":5555,
                             "audio":[{"path":"PRIVATE SECRET.wav", "zip_method":0}],
                             "share_url":"https://secret.example/token=secret"})))
            data = inspect(path)
            self.assertEqual(data["sqlite_user_version"], 1)
            self.assertEqual(data["catalog_releases"], 1)
            self.assertEqual(data["audio_inventory_members"], 1)
            self.assertEqual(data["audio_member_suffixes_only"], {".wav": 1})
            self.assertTrue(data["reported_release_present"]["19732"])
            self.assertNotIn("SECRET", json.dumps(data))
            self.assertNotIn("secret.example", json.dumps(data))
            self.assertNotIn("PRIVATE", json.dumps(data))

    def test_missing_schema_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "empty.sqlite"
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE nothing (id)")
            with self.assertRaises(ValueError):
                inspect(path)


if __name__ == "__main__":
    unittest.main()
