import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from e2e.host.archive_readout import inspect_archive,readout

def member(path,offset,method=8,encrypted=False):
    return dict(path=path,suffix=".flac",kind="audio_candidate",compressed_bytes=10,
                uncompressed_bytes=20,zip_method=method,zip_header_offset=offset,
                crc32="12345678",encrypted=encrypted)

class ArchiveReadoutContracts(unittest.TestCase):
    def test_directory_not_content_proof(self):
        rows=[member("disc/a.flac",0),member("disc/a.flac",50,12),member("disc/b.flac",100,0,True)]
        result=inspect_archive(json.dumps(dict(format="zip",members=3,inventory=rows)))
        self.assertEqual(result["duplicate_paths"],["disc/a.flac"])
        self.assertEqual([x["remote_playback_candidate"] for x in result["members"]],[True,False,False])
        self.assertTrue(all(x["member_sha256"] is None for x in result["members"]))
        self.assertFalse(result["member_bytes_verified"])

    def test_reject_duplicate_locator_and_count(self):
        a=member("a.flac",0)
        for rows,count in (([a,a],2),([a],2)):
            with self.assertRaises(ValueError):
                inspect_archive(json.dumps(dict(format="zip",members=count,inventory=rows)))

    def test_committed_per_copy_not_release_page(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"catalog.db"
            schema=Path(__file__).resolve().parents[2]/"native/catalog_schema.sql"
            with sqlite3.connect(path) as db:
                db.executescript(schema.read_text())
                db.execute("INSERT INTO releases(id,listing,seen) VALUES(1,'{}',0)")
                archive=json.dumps(dict(format="zip",members=1,inventory=[member("a.flac",0)]))
                for ident,host,obs in ((1,"mediafire.com",archive),(2,"mega.nz",None)):
                    db.execute("INSERT INTO copies(id,destination,share_url,host,offer,archive) VALUES(?,?,?,?,?,?)",
                               (ident,str(ident),"https://private.invalid/secret",host,"{}",obs))
                    db.execute("INSERT INTO release_copies VALUES(?,?,?,?,?,?)",(1,ident,"fixture",0,0,1))
            out=readout(path,1)
            self.assertEqual([c["archive"]["status"] for c in out["copies"]],
                             ["directory_inventory_observed","not_inspected"])
            self.assertNotIn("private.invalid",json.dumps(out))
            self.assertFalse(out["live_verified"])
