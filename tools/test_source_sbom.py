"""Offline SPDX source-input version and checksum tests."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from tools.source_sbom import COMPONENTS, generate

class SourceSbomTests(unittest.TestCase):
    def setUp(self):
        t=TemporaryDirectory();self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        for _,_,relative,patterns in COMPONENTS:
            file=self.root/relative;file.parent.mkdir(parents=True,exist_ok=True)
            file.write_text("\n".join(p.replace(r"\.",".") for p in patterns) + "\n" if patterns else "zip-archive-placeholder",encoding="utf-8")

    def test_deterministic_and_bounded(self):
        from tools.source_sbom import sha
        a=generate(self.root,"a"*40,12345)
        self.assertEqual(a,generate(self.root,"a"*40,12345))
        self.assertEqual(len(a["packages"]),4)
        self.assertIn("PARTIAL",a["documentComment"])
        self.assertTrue(all(f["checksums"][0]["checksumValue"]==sha(self.root/f["fileName"][2:]) for f in a["files"]))
        self.assertNotIn(str(self.root),json.dumps(a))

    def test_version_drift_rejected(self):
        f=self.root/"native/third_party/utf8proc/utf8proc.h"
        f.write_text("UTF8PROC_VERSION_MAJOR 999")
        with self.assertRaisesRegex(ValueError,"drift"):
            generate(self.root,"a"*40,1)

    def test_missing_file_rejected(self):
        (self.root/"native/third_party/json.hpp").unlink()
        with self.assertRaisesRegex(ValueError,"absent"):
            generate(self.root,"a"*40,1)

    def test_bad_commit_rejected(self):
        with self.assertRaisesRegex(ValueError,"Full lowercase"):
            generate(self.root,"bad",1)

if __name__=="__main__":
    unittest.main()
