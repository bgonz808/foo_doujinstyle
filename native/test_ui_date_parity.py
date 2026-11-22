"""Differential tests for native foobar2000 date display vs source-only Python."""
import os
import unittest
from native.search_worker import choose_release_date, result_record
from native.reference_publisher import ReferencePublisher


@unittest.skipUnless(os.getenv('DOUJINSTYLE_TEST_PUBLISHER'), 'requires compiled C++ catalog worker')
class NativeUiDateParity(unittest.TestCase):
    def setUp(self):
        self.worker = ReferencePublisher(os.environ['DOUJINSTYLE_TEST_PUBLISHER'])
        self.addCleanup(self.worker.close)

    def test_date_selection_parity(self):
        fixtures = [
            {}, {'release_date':'2026-11-21'}, {'release_date':'2026/2/3'},
            {'release_date':'2025-02-29', 'listed_at':'2025-03-01'},
            {'release_date':'2024-02-29'}, {'release_date':'2024.2.9'},
            {'release_date':'0000-01-01'}, {'release_date':'2030-13-01'},
            {'external_lookup':{'bangumi':{'fields':{'release_date':'2020-10-31'}}}},
            {'tags':['M3-58']}, {'tags':['M3-58', 'electronic']},
            {'tags':['Ｍ３－５８']}, {'tags':['RTS23']},
            {'tags':['C108'], 'listed_at':'2026-08-20'},
            {'tags':['C109'], 'listed_at':'2026-12-30'},
            {'tags':['M3-57','M3-58'], 'listed_at':'2026-11-01'},
            {'events':[{'id':'M3-58'}]},
            {'events':[{'id':'M3-58'}], 'tags':['Ｍ３－５８']},
            {'events':[{'id':'C109'}], 'listed_at':'2026-12-24'},
            {'events':[{'id':'M3-57'}, {'id':'M3-58'}]},
            {'external_lookup':{'bangumi':{'fields':{'events':[{'id':'M3-54'}]}}}},
            {'external_lookup':{'bangumi':{'fields':{'release_date':'2024-01-01'}}},
             'release_date':'2025-01-01'},
            {'listed_at':'2026-12-01'},
        ]
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                expected_date, expected_source = choose_release_date(fixture)
                got = self.worker.request(None, 'ui_date', release=fixture)
                self.assertEqual(got, {'date':expected_date or '', 'source':expected_source or ''})

    def test_record_uses_compiled_date_field(self):
        row = {'id':100, 'album':'Alb', 'artist':'Circle', 'tags':['M3-58'],
               'technical':{'advertised_format':'FLAC'}}
        native = result_record(row, self.worker)
        source = result_record(row)
        self.assertEqual(native, source)
        self.assertIn('2026-10-25', native)

    def test_invalid_release_rejected(self):
        for invalid in ([], None, 'bad'):
            with self.assertRaises(OSError):
                self.worker.request(None, 'ui_date', release=invalid)

if __name__=='__main__': unittest.main()
