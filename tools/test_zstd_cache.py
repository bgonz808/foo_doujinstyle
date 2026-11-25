import hashlib
from collections import OrderedDict
from pathlib import Path
import tempfile
import unittest

from tools.zstd_frame_bench import replay


class IdentityDecoder:
    def __init__(self):
        self.calls = 0

    def decompress(self, data, size):
        self.calls += 1
        if len(data) != size:
            raise ValueError("length")
        return data


class CacheTests(unittest.TestCase):
    def test_oversize_does_not_evict_hot_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/"frames"
            p.write_bytes(b"abcd" + b"0123456789")
            index = [(0,4,0,4),(4,10,4,10)]
            trace = [(0,4,hashlib.sha256(b"abcd").digest()),
                     (4,2,hashlib.sha256(b"01").digest()),
                     (0,4,hashlib.sha256(b"abcd").digest())]
            codec = IdentityDecoder()
            cache = OrderedDict()
            cold = replay(codec,p,index,trace,4,cache)
            self.assertEqual((cold["misses"],cold["hits"],cold["evictions"],cold["oversize_bypasses"]), (2,1,0,1))
            self.assertEqual(cold["peak_cached_bytes"],4)
            repeat = replay(codec,p,index,trace,4,cache)
            self.assertEqual((repeat["misses"],repeat["hits"]), (1,2))
            self.assertEqual(repeat["cache_hit_useful_bytes"],8)
            self.assertEqual(codec.calls,3)

    def test_zero_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/"frames"
            p.write_bytes(b"abcd")
            trace = [(0,4,hashlib.sha256(b"abcd").digest())]*2
            result = replay(IdentityDecoder(),p,[(0,4,0,4)],trace,0)
            self.assertEqual((result["misses"],result["peak_cached_bytes"]), (2,0))


if __name__ == "__main__":
    unittest.main()
