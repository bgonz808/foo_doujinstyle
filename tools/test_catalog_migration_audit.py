import os
from pathlib import Path
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
import unittest
from tools.catalog_migration_audit import audit,digest,initialize,logical

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.getenv("DOUJINSTYLE_TEST_PUBLISHER"),"Native publisher not compiled")
class NativeCatalogMigrationAuditTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.source=self.root/"seed.sqlite"
        self.worker=Path(os.environ["DOUJINSTYLE_TEST_PUBLISHER"]).resolve()
        schema=(ROOT/"native/catalog_schema.sql").read_text().split("CREATE TABLE IF NOT EXISTS catalog_lookup_claims")[0]
        with sqlite3.connect(self.source) as db:
            db.executescript(schema)
            db.execute("PRAGMA user_version=1")
            db.execute("INSERT INTO releases(id,listing,seen) VALUES(1,?,1)",('{"id":1,"title":"private title"}',))
            db.execute("INSERT INTO copies(id,destination,share_url,host,offer) VALUES(1,?,?,?,?)",
                       ("fixture","https://private.invalid/key","example.org","{}"))
            db.execute("INSERT INTO release_copies VALUES(1,1,'fixture',1,1,1)")
            db.execute("INSERT INTO backfill_checkpoint VALUES(1,'{}')")
            db.execute("INSERT INTO pages VALUES('private page','{}',1)")
        self.identity=subprocess.run([str(self.worker),"--version"],capture_output=True,check=True).stdout.decode().splitlines()

    def test_actual_worker_migration_and_reopen(self):
        before=digest(self.source)
        r=audit(self.source,self.root/"working.sqlite",self.worker,digest(self.worker),self.identity[2].split("=",1)[1])
        self.assertEqual(r["before_version"],1)
        self.assertEqual(r["after_version"],2)
        self.assertEqual(r["original_counts"]["releases"],1)
        self.assertEqual(len(r["added_tables"]),3)
        self.assertEqual(digest(self.source),before)
        self.assertNotIn("private title",str(r))

    def test_reject_newer_schema_without_mutation(self):
        with sqlite3.connect(self.source) as db: db.execute("PRAGMA user_version=3")
        before=digest(self.source)
        self.assertFalse(initialize(self.worker,self.source))
        self.assertEqual(digest(self.source),before)

    def test_ddl_failure_rolls_back(self):
        with sqlite3.connect(self.source) as db:
            db.execute("DROP INDEX release_copies_by_copy")
            db.execute("CREATE TABLE release_copies_by_copy (test INTEGER)")
        before=digest(self.source)
        self.assertFalse(initialize(self.worker,self.source))
        self.assertEqual(digest(self.source),before)
        self.assertEqual(logical(self.source)["version"],1)

    def test_no_overwrite_or_wrong_worker(self):
        with self.assertRaisesRegex(ValueError,"New disposable destination"):
            audit(self.source,self.source,self.worker,digest(self.worker),"x")
        with self.assertRaisesRegex(ValueError,"Worker digest mismatch"):
            audit(self.source,self.root/"working.sqlite",self.worker,"0"*64,"x")
        self.assertFalse((self.root/"working.sqlite").exists())
