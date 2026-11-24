import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
from tools.seekable_verify import verify_candidate, read_table


class RawDecoder:
    """Tiny independent test oracle for synthetic fixtures only."""
    def decode(self,data,size):
        if len(data)!=size+1 or data[:1]!=b"F":
            raise ValueError("Bad synthetic frame")
        return data[1:]


def fixture(data):
    frame=b"F"+data
    table=struct.pack("<IIII",0x184D2A5E,17,len(frame),len(data))
    table+=struct.pack("<IBI",1,0,0x8F92EAB1)
    return frame+table


class CustodyTests(unittest.TestCase):
    def test_equivalence_and_tamper(self):
        original=b"abc"
        expected=(hashlib.sha256(original).hexdigest(),len(original))
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"candidate"
            good=fixture(original)
            p.write_bytes(good)
            result=verify_candidate(p,RawDecoder(),expected)
            self.assertEqual(result["candidate_sha256"],hashlib.sha256(good).hexdigest())
            self.assertEqual(result["frames"][0]["logical_sha256"],expected[0])
            for bad in (fixture(b"abd"),fixture(b"ab"),fixture(b"abcd"),good+b"x",
                        good[:-1],b"x"+good,good[:6]+bytes([good[6]^1])+good[7:]):
                with self.subTest(bad=bad),self.assertRaises(ValueError):
                    p.write_bytes(bad)
                    verify_candidate(p,RawDecoder(),expected)


if __name__=="__main__":
    unittest.main()
