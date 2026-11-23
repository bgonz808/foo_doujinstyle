"""Compiled native index eligibility, parity, and source-only Python retirement."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from native.pack_search_worker import worker_sources
from native.reference_publisher import ReferencePublisher

HERE = Path(__file__).resolve().parent.parent
MF = "https://www.mediafire.com/file/abc/Test/file"
MEGA = "https://mega.nz/file/PublicID#AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"


class NativeIndexPlanTests(unittest.TestCase):
    def test_python_selection_is_source_only(self):
        members, report = worker_sources(HERE)
        cache = members['catalog_cache.py'].decode()
        worker = members['native/search_worker.py'].decode()
        self.assertNotIn('def offer_jobs(', cache)
        self.assertNotIn('def index_jobs(', cache)
        self.assertNotIn('def compatibility_schedule_index(', worker)
        self.assertNotIn('catalog_index_plan', worker)
        self.assertNotIn('def schedule_index(', worker)
        self.assertIn('schedule_index', report['modules']['native/search_worker.py']['standalone'])
        # The actual native selection endpoint remains tested below against
        # SQLite. Its absence from Python must not remove native coverage.
        publisher = (HERE / 'native/publish_references.cpp').read_text(encoding='utf-8')
        self.assertIn('if (operation == "catalog_index_plan")', publisher)

    @unittest.skipUnless(os.getenv('DOUJINSTYLE_TEST_PUBLISHER'), 'Native worker not built')
    def test_idle_eligibility_and_layer_precedence(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'idle.sqlite'
            db = sqlite3.connect(path)
            db.executescript("""
                CREATE TABLE releases(id INTEGER PRIMARY KEY,listing TEXT,offer_checked INTEGER);
                CREATE TABLE copies(id INTEGER PRIMARY KEY,share_url TEXT,host TEXT,offer TEXT,
                                    archive TEXT,archive_checked INTEGER,archive_error TEXT);
                CREATE TABLE release_copies(release_id INTEGER,copy_id INTEGER,current INTEGER);
            """)
            db.executemany('INSERT INTO releases VALUES(?,?,?)', [
                (1,json.dumps({'id':1,'album':'old'}),None),
                (2,json.dumps({'id':2,'album':'offer','download_offer':1,
                               'download_forms':['form']}),None),
                (3,json.dumps({'id':3,'album':'listed','listed_at':123}),None)])
            db.executemany('INSERT INTO copies VALUES(?,?,?,?,?,?,?)', [
                (21,MF,'mediafire.com','{}',None,None,None),
                (22,MEGA,'mega.nz','{}',json.dumps({'format':'zip', 'audio_probes':{},
                    'inventory':[], 'header_probe_pass_complete':True}),5555555555,None)])
            db.executemany('INSERT INTO release_copies VALUES(?,?,?)', [(1,21,1),(3,22,1)])
            db.commit()
            peer = ReferencePublisher(executable=Path(os.environ['DOUJINSTYLE_TEST_PUBLISHER']))
            try:
                result = peer.request(path,'catalog_idle_plan',layers=2|4|8|16|32)
                self.assertEqual(result['details'],[1])
                self.assertEqual([x['id'] for x in result['links']],[2])
                self.assertEqual([x['id'] for x in result['archives']],[21,22])
                self.assertEqual(result['archives'][0]['ids'],[1])
                self.assertTrue(result['archives'][0]['headers'])
                self.assertTrue(result['archives'][0]['cover'])
                self.assertFalse(result['archives'][1]['headers'])
                self.assertTrue(result['archives'][1]['cover'])
                self.assertEqual(peer.request(path,'catalog_idle_plan',layers=2)['archives'],[])
                self.assertEqual(peer.request(path,'catalog_idle_plan',layers=1)['details'],[])
                with self.assertRaisesRegex(OSError,'Invalid idle indexing layer'):
                    peer.request(path,'catalog_idle_plan',layers=64)
            finally:
                peer.close()
                db.close()

    @unittest.skipUnless(os.getenv('DOUJINSTYLE_TEST_PUBLISHER'), 'Native worker not built')
    def test_native_sqlite_offer_retry_archive_selection_and_replan(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'jobs.sqlite'
            db = sqlite3.connect(path)
            db.executescript('''
                CREATE TABLE releases(id INTEGER PRIMARY KEY, offer_checked INTEGER, offer_error TEXT);
                CREATE TABLE copies(id INTEGER PRIMARY KEY,share_url TEXT,host TEXT,offer TEXT,
                                    archive_checked INTEGER,archive_error TEXT,archive TEXT);
                CREATE TABLE release_copies(release_id INTEGER,copy_id INTEGER,current INTEGER);
                INSERT INTO releases VALUES(1,NULL,NULL),(2,9999999999,NULL),(3,0,'timeout');
                INSERT INTO copies VALUES
                  (10,'https://www.mediafire.com/file/abc/Test/file','mediafire.com','{}',NULL,NULL,NULL),
                  (11,'https://mediafire.com.bad.org/file/abc','mediafire.com','{}',NULL,NULL,NULL);
                INSERT INTO release_copies VALUES(1,10,1),(1,11,1);
            ''')
            db.commit()
            peer = ReferencePublisher(executable=Path(os.environ['DOUJINSTYLE_TEST_PUBLISHER']))
            try:
                initial = peer.request(path, 'catalog_index_plan', ids=[1,2,3])
                self.assertEqual(initial['offers'], [1,3])
                self.assertEqual([x['id'] for x in initial['archives']], [10])
                self.assertEqual(peer.request(path, 'catalog_index_plan', ids=[1], offers=False)['offers'], [])
                with self.assertRaisesRegex(OSError, 'Duplicate or invalid'):
                    peer.request(path, 'catalog_index_plan', ids=[1,1])
                db.execute('INSERT INTO copies VALUES(12,?,?,?,NULL,NULL,NULL)',(MEGA,'mega.nz','{}'))
                db.execute('INSERT INTO release_copies VALUES(1,12,1)')
                db.commit()
                found = peer.request(path, 'catalog_index_plan', ids=[1], offers=False)
                self.assertEqual([x['id'] for x in found['archives']], [12,10])
                # A completed ZIP checkpoint with the recognized fields does not get queued anew.
                db.execute('UPDATE copies SET archive_checked=9999999999,archive=? WHERE id=12',
                           (json.dumps({'format':'zip','header_probe_pass_complete':True,
                                        'audio_probes':{},'inventory':[]}),))
                db.commit()
                self.assertEqual([x['id'] for x in peer.request(path,'catalog_index_plan',
                                                                 ids=[1], offers=False)['archives']], [10])
            finally:
                peer.close()
                db.close()


class CoordinatorBoundaryTests(unittest.TestCase):
    def test_idle_replans_after_committed_details_and_offers(self):
        from unittest.mock import patch
        from native.search_worker import run_idle_layers, IDLE_DETAILS, IDLE_LINKS, IDLE_ZIP
        stages = []
        class Publisher:
            def request(self, path, operation, **kwargs):
                self_path = path
                assert self_path == 'catalog'
                if operation == 'catalog_idle_detail_job':
                    cache.saved_details = True  # fixture models the native SQLite commit
                    event = {'kind': 'idle_detail', 'id': 1}
                    kwargs['on_event'](event)
                    return {'events': [event]}
                assert operation == 'catalog_idle_plan'
                layer = kwargs['layers']
                stages.append(layer)
                if layer == IDLE_DETAILS: return {'details':[1], 'links':[], 'archives':[]}
                if layer == IDLE_LINKS:
                    assert cache.saved_details
                    return {'details':[], 'links':[{'id':1,'listing':{'id':1, 'url':'https://doujinstyle.com/?p=page&type=1&id=1'}}], 'archives':[]}
                if layer == IDLE_ZIP:
                    assert cache.saved_offers
                    return {'details':[], 'links':[], 'archives':[]}
                raise AssertionError('Unexpected eligibility phase')
        class Cache:
            native=True
            path='catalog'
            publisher=Publisher()
            saved_details=False
            saved_offers=False
            def put_observation(self, row): self.saved_details=True
            def put_offers(self, id, offers, err, *, observation): self.saved_offers=True
        cache=Cache()
        events=[]
        with patch('native.search_worker.release_details',return_value=({'id':1},[])), \
             patch('native.search_worker.fetch_offer',return_value=(1,{},[('source',{},'https://www.mediafire.com/file/abc')],None)):
            run_idle_layers(object(),cache,IDLE_DETAILS|IDLE_LINKS|IDLE_ZIP,
                            Path('.'),None,events.append,slot=lambda _:0)
        self.assertEqual(stages,[IDLE_LINKS,IDLE_ZIP])
        self.assertTrue(cache.saved_offers)
        self.assertIn('Selected idle indexing pass finished.', events)

    @unittest.skipUnless(os.getenv('DOUJINSTYLE_TEST_PUBLISHER'), 'Native worker not built')
    @unittest.skipIf(os.name == 'nt', 'Portable fail-closed test; Windows WinHTTP requires host fixtures')
    def test_index_dispatch_never_calls_source_only_selectors(self):
        from unittest.mock import patch
        from native.search_worker import schedule_index
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'index.sqlite'
            db = sqlite3.connect(path)
            db.executescript('''
                CREATE TABLE releases(id INTEGER PRIMARY KEY,offer_checked INTEGER,offer_error TEXT);
                CREATE TABLE copies(id INTEGER PRIMARY KEY,share_url TEXT,host TEXT,offer TEXT,
                                    archive_checked INTEGER,archive_error TEXT,archive TEXT);
                CREATE TABLE release_copies(release_id INTEGER,copy_id INTEGER,current INTEGER);
                INSERT INTO releases VALUES (1,9999999999,NULL);
            ''')
            db.execute('INSERT INTO copies VALUES(10,?,?,?,NULL,NULL,NULL)',
                       (MF,'mediafire.com','{}'))
            db.execute('INSERT INTO release_copies VALUES(1,10,1)')
            db.commit()
            class NativeOnlyCache:
                native = True
                publisher = ReferencePublisher(executable=Path(os.environ['DOUJINSTYLE_TEST_PUBLISHER']))
                def __init__(self): self.path = path
                def offer_jobs(self,*args,**kwargs): raise AssertionError('Retired Python eligibility called')
                def index_jobs(self,*args,**kwargs): raise AssertionError('Retired Python eligibility called')
                def releases_for_copy(self,copy_id): return [1]
            cache = NativeOnlyCache()
            completed = []
            try:
                # The portable worker MUST fail closed rather than invoking
                # Python ZIP inspection. Real WinHTTP archive probing is
                # separately qualified on a Windows foobar2000 host.
                with patch('native.search_worker.probe_archive', side_effect=AssertionError('Retired Python probe entered')):
                    with self.assertRaisesRegex(OSError, 'WinHTTP archive probing requires Windows'):
                        schedule_index(object(), cache, {1:{'id':1}}, lambda *_:None, None,
                                       False, completed.append, workers=2)
                self.assertFalse(completed)
                self.assertIsNone(db.execute('SELECT archive_checked FROM copies WHERE id=10').fetchone()[0])
            finally:
                cache.publisher.close()
                db.close()

if __name__ == "__main__": unittest.main()
