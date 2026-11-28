import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from tools.catalog_census import freeze, report, sample_order, cohort

class CensusTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/"seed.sqlite"
        with sqlite3.connect(self.db) as db:
            db.executescript("""PRAGMA user_version=1;
                CREATE TABLE releases(id INTEGER PRIMARY KEY,offer_checked INTEGER,offer_error TEXT);
                CREATE TABLE copies(id INTEGER PRIMARY KEY,host TEXT,archive TEXT,archive_checked INTEGER,archive_error TEXT);
                CREATE TABLE release_copies(release_id INTEGER,copy_id INTEGER,source TEXT,current INTEGER);
                CREATE TABLE pages(url TEXT);
                INSERT INTO releases VALUES(1,1,NULL),(2,1,NULL),(3,NULL,NULL),(4,1,'private error');
                INSERT INTO release_copies VALUES(1,10,'a',1),(1,10,'b',1),(2,10,'a',1),(1,11,'a',1),(2,12,'a',1),(4,13,'a',0);
                """)
            arc={"format":"zip","members":2,"inventory":[
                {"path":"PRIVATE.flac","kind":"audio_candidate","zip_header_offset":100,"zip_method":0,"encrypted":False},
                {"path":"PRIVATE.flac","kind":"audio_candidate","zip_header_offset":200,"zip_method":14,"encrypted":False}]}
            db.executemany("INSERT INTO copies VALUES(?,?,?,?,?)",[
                (10,"mediafire.com",json.dumps(arc),1,None),
                (11,"example.org",json.dumps({"format":"7z"}),1,None),
                (12,"mega.nz",None,None,None),
                (13,"https://private.invalid/secret",None,None,None)])
    def test_reproducible_prefix(self):
        m=freeze(self.db,"test",2,4)
        self.assertEqual(m,freeze(self.db,"test",2,4))
        self.assertEqual(cohort(m,2),sample_order([1,2,3,4],"test")[:2])
        m["ordered_release_ids"][1]=m["ordered_release_ids"][0]
        with self.assertRaises(ValueError): cohort(m,2)
    def test_cardinality_and_alternates(self):
        x=report(self.db,batch=1)
        self.assertEqual(x["current_distinct_copies"],3)
        self.assertEqual(x["current_release_copy_pairs"],4)
        self.assertEqual(x["historical_release_copy_pairs"],5)
        self.assertEqual(x["release_status"]["with_structural_candidate"],2)
        self.assertEqual(sum(c["members"] for c in x["member_interactions"]),2)
        g={r["gap"]:r for r in x["observed_gap_rankings"]}
        self.assertEqual(g["zip-method:14"]["affected_releases"],2)
        self.assertEqual(g["archive:7z"]["releases_without_known_candidate"],0)
        self.assertEqual(x["private_next_batch"]["offer_release_ids"],[3])
        self.assertEqual(x["private_next_batch"]["archive_copy_jobs"],[{"id":12,"release_id":2}])
        self.assertNotIn("PRIVATE",json.dumps(x))
        self.assertNotIn("secret",json.dumps(x))
    def test_resume_existing_manifest_against_new_snapshot(self):
        m=freeze(self.db,"seed",2,4)
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE releases SET offer_checked=2 WHERE id=3")
        x=report(self.db,m,4)
        self.assertEqual(x["cohort_size"],4)
        self.assertNotEqual(x["snapshot_sha256"],m["snapshot_sha256"])
        self.assertEqual(x["original_seed_snapshot_sha256"],m["snapshot_sha256"])
        self.assertEqual(x["private_next_batch"]["offer_release_ids"],[])
    def test_refuse_wal_sidecar_and_new_schema(self):
        wal=Path(str(self.db)+"-wal")
        wal.touch()
        with self.assertRaisesRegex(ValueError,"closed SQLite backup"): report(self.db)
        wal.unlink()
        with sqlite3.connect(self.db) as db: db.execute("PRAGMA user_version=3")
        with self.assertRaisesRegex(ValueError,"Unqualified catalog schema"): report(self.db)
