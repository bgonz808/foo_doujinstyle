import ctypes.util
import hashlib
from pathlib import Path
import tempfile
import unittest

from tools.zstd_cursor_bench import Codec, Reader, PAGE, pack
from tools.zstd_frame_bench import Zstd


class CursorTests(unittest.TestCase):
    def test_retained_forward_and_cached_backward(self):
        library=ctypes.util.find_library("zstd")
        self.assertTrue(library,"libzstd required")
        with tempfile.TemporaryDirectory() as tmp:
            raw=Path(tmp)/"raw"
            packed=Path(tmp)/"frames"
            data=bytes(range(256))*(PAGE*5//256+1)
            raw.write_bytes(data)
            idx=pack(Zstd(library),raw,packed,PAGE*3)
            reader=Reader(Codec(library),packed,idx,2,PAGE*2)
            try:
                for offset,size in [(0,32),(PAGE+2,80),(2,40),(PAGE*3+1,10),
                                    (PAGE*2+1,128),(PAGE*4+2,50),(len(data)-20,20)]:
                    self.assertEqual(reader.read(offset,size),data[offset:offset+size])
                self.assertEqual(reader.stats["session_opens"],2)
                self.assertEqual(reader.stats["restarts"],0)
                self.assertGreater(reader.stats["cursor_reuses"],0)
                self.assertGreater(reader.stats["page_hits"],0)
                self.assertLessEqual(reader.stats["peak_cache_bytes"],PAGE*2)
                self.assertLessEqual(reader.stats["peak_sessions"],2)
            finally:
                reader.close()

    def test_backward_miss_requires_restart(self):
        library=ctypes.util.find_library("zstd")
        self.assertTrue(library)
        with tempfile.TemporaryDirectory() as tmp:
            raw=Path(tmp)/"raw"
            path=Path(tmp)/"frames"
            raw.write_bytes(b"a"*(PAGE*3))
            idx=pack(Zstd(library),raw,path,PAGE*3)
            reader=Reader(Codec(library),path,idx,1,PAGE)
            try:
                self.assertEqual(reader.read(PAGE*2,5),b"a"*5)
                self.assertEqual(reader.read(0,5),b"a"*5)
                self.assertEqual(reader.stats["restarts"],1)
            finally:
                reader.close()


if __name__=="__main__":
    unittest.main()
