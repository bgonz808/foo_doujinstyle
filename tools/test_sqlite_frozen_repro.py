from collections import Counter
import unittest
from tools.sqlite_frozen_repro import input_args, sequence, summary

class FrozenSQLiteTests(unittest.TestCase):
    def test_replace_only_source(self):
        flags=["/c","/Z7","/Qspectre","/Brepro","/DVALUE=1","/Iinclude"]
        for original in (["sqlite.c"],["/Tcsqlite.c"],["/Tc","sqlite.c"]):
            result=input_args(flags+original,"C:/work/sqlite.c","C:/temp/frozen.i","C:/work")
            self.assertEqual(result[:len(flags)],flags)
            self.assertIn("C:/temp/frozen.i"," ".join(result))
            self.assertNotIn("sqlite.c"," ".join(result))

    def test_reject_ambiguous_or_wrong_language(self):
        for args in ([],["sqlite.c","sqlite.c"],["/Tpsqlite.c"],["/Tcother.c"]):
            with self.assertRaises(ValueError):
                input_args(args,"C:/work/sqlite.c","C:/temp/frozen.i","C:/work")

    def test_counterbalance(self):
        for replica in ("a","b"):
            self.assertEqual(Counter(sequence(replica)),{"source":4,"frozen":4})
        self.assertNotEqual(sequence("a"),sequence("b"))
        self.assertFalse(summary([])["frozen"]["complete"])
